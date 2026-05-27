"""
safeguards.py — Polymarket Weather Bot 2026
Захисні механізми: circuit breakers, drawdown monitor, аварійна зупинка.
"""

import logging
import json
import os
import requests as _req
from datetime import datetime, timezone
from typing import Dict, Optional
from dataclasses import dataclass, asdict

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# PostgreSQL persistence (для Render free tier)
# ─────────────────────────────────────────────
_DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL")
_pg_conn = None

def _get_pg_conn():
    global _pg_conn
    if not _DATABASE_URL:
        return None
    if _pg_conn is not None:
        try:
            _pg_conn.cursor().execute("SELECT 1")
            return _pg_conn
        except Exception:
            _pg_conn = None
    try:
        import pg8000
        from urllib.parse import urlparse
        parsed = urlparse(_DATABASE_URL)
        _pg_conn = pg8000.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
        )
        cur = _pg_conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS bot_state ("
            "  id INTEGER PRIMARY KEY,"
            "  state_json TEXT NOT NULL,"
            "  updated_at TIMESTAMP DEFAULT NOW()"
            ")"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS trade_log ("
            "    id SERIAL PRIMARY KEY,"
            "    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),"
            "    cycle INTEGER,"
            "    action TEXT,"
            "    direction TEXT,"
            "    market_question TEXT,"
            "    city TEXT,"
            "    forecast_c REAL,"
            "    threshold_c REAL,"
            "    our_prob REAL,"
            "    market_prob REAL,"
            "    edge REAL,"
            "    size_usd REAL,"
            "    entry_price REAL,"
            "    pnl_usd REAL,"
            "    status TEXT,"
            "    dry_run BOOLEAN DEFAULT TRUE"
            ")"
        )
        _pg_conn.commit()
        cur.close()
        logger.info("🐘 PostgreSQL: таблиці bot_state та trade_log перевірено/створено")
        return _pg_conn
    except Exception as e:
        logger.warning(f"PostgreSQL недоступний: {e}")
        return None

def log_trade_to_pg(trade_data: dict) -> bool:
    conn = _get_pg_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO trade_log (cycle, action, direction, market_question, city, forecast_c, threshold_c, our_prob, market_prob, edge, size_usd, entry_price, pnl_usd, status, dry_run) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                trade_data.get("cycle"),
                trade_data.get("action"),
                trade_data.get("direction"),
                trade_data.get("market_question"),
                trade_data.get("city"),
                trade_data.get("forecast_c"),
                trade_data.get("threshold_c"),
                trade_data.get("our_prob"),
                trade_data.get("market_prob"),
                trade_data.get("edge"),
                trade_data.get("size_usd"),
                trade_data.get("entry_price"),
                trade_data.get("pnl_usd"),
                trade_data.get("status"),
                trade_data.get("dry_run", True)
            )
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logger.debug(f"PostgreSQL log_trade error: {e}")
        return False



def _pg_save(data: dict) -> bool:
    conn = _get_pg_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bot_state (id, state_json, updated_at) "
            "VALUES (1, %s, NOW()) "
            "ON CONFLICT (id) DO UPDATE SET state_json = EXCLUDED.state_json, updated_at = NOW()",
            (json.dumps(data, default=str),)
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logger.debug(f"PostgreSQL save error: {e}")
        return False


def _pg_load() -> Optional[dict]:
    conn = _get_pg_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT state_json FROM bot_state WHERE id = 1")
        row = cur.fetchone()
        cur.close()
        if row:
            return json.loads(row[0])
    except Exception as e:
        logger.debug(f"PostgreSQL load error: {e}")
    return None

STATE_FILE = os.path.join(config.DATA_DIR, "bot_state.json")

_JSONBIN_KEY    = config.JSONBIN_KEY
_JSONBIN_BIN_ID = config.JSONBIN_BIN_ID
_JSONBIN_BASE   = "https://api.jsonbin.io/v3/b"
_JSONBIN_TTL    = 15


def _jsonbin_save(data: dict, _retries: int = 0) -> bool:
    if not _JSONBIN_KEY or not _JSONBIN_BIN_ID:
        return False
    import time as _time
    for attempt in range(_retries + 1):
        try:
            r = _req.put(
                f"{_JSONBIN_BASE}/{_JSONBIN_BIN_ID}",
                json=data,
                headers={
                    "Content-Type": "application/json",
                    "X-Master-Key": _JSONBIN_KEY,
                    "X-Bin-Versioning": "false",
                },
                timeout=_JSONBIN_TTL,
            )
            if r.status_code == 200:
                return True
            if attempt < _retries:
                _time.sleep(2)
        except Exception as e:
            if attempt < _retries:
                _time.sleep(2)
    return False


def _jsonbin_load() -> Optional[dict]:
    if not _JSONBIN_KEY or not _JSONBIN_BIN_ID:
        return None
    try:
        r = _req.get(
            f"{_JSONBIN_BASE}/{_JSONBIN_BIN_ID}/latest",
            headers={"X-Master-Key": _JSONBIN_KEY},
            timeout=_JSONBIN_TTL,
        )
        if r.status_code == 200:
            record = r.json().get("record", {})
            if record:
                return record
        else:
            logger.warning(f"JSONBin load HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"JSONBin load error: {e}")
    return None


@dataclass
class BotState:
    initial_capital: float = config.INITIAL_CAPITAL
    current_capital: float = config.INITIAL_CAPITAL
    peak_capital: float = config.INITIAL_CAPITAL
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    is_halted: bool = False
    halt_reason: str = ""
    start_time: str = datetime.now(timezone.utc).isoformat()
    last_update: str = datetime.now(timezone.utc).isoformat()
    open_positions: Dict = None
    closed_positions: Dict = None  # Сховище для збереження _recently_closed між перезапусками

    def __post_init__(self):
        if self.open_positions is None:
            self.open_positions = {}
        if self.closed_positions is None:
            self.closed_positions = {}

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def drawdown_pct(self) -> float:
        if self.peak_capital == 0:
            return 0.0
        return (self.peak_capital - self.current_capital) / self.peak_capital

    @property
    def roi_pct(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return (self.current_capital - self.initial_capital) / self.initial_capital


class SafeguardManager:

    def __init__(self, config_obj):
        self.config = config_obj
        self.state = self._load_state()
        self._trade_count_hour = 0
        self._hour_mark = datetime.now().hour
        self._jsonbin_fail_count = 0
        self._pg_working = None

    def _load_state(self) -> BotState:
        os.makedirs(config.DATA_DIR, exist_ok=True)

        # RESET_POSITIONS=true → примусово чистий старт (знімає заморожені позиції)
        if os.environ.get("RESET_POSITIONS", "").lower() == "true":
            logger.warning("🔄 RESET_POSITIONS=true → починаємо з чистого аркуша")
            return BotState()

        # PRIMARY: локальний файл (надійний, без мережевих залежностей)
        state = None
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                    valid_keys = BotState.__dataclass_fields__
                    state = BotState(**{k: v for k, v in data.items() if k in valid_keys})
                    logger.info("💾 Стан відновлено з локального файлу")
            except Exception as e:
                logger.warning(f"Локальний стан пошкоджений: {e}")

        # FALLBACK 1: JSONBin (хмара) — якщо локальний файл відсутній
        if state is None:
            cloud_data = _jsonbin_load()
            if cloud_data:
                try:
                    valid_keys = BotState.__dataclass_fields__
                    state = BotState(**{k: v for k, v in cloud_data.items() if k in valid_keys})
                    logger.info("☁️  Стан відновлено з JSONBin")
                except Exception as e:
                    logger.warning(f"JSONBin parse error: {e}")

        # FALLBACK 2: PostgreSQL (Render free tier — персистентний)
        if state is None:
            pg_data = _pg_load()
            if pg_data:
                try:
                    valid_keys = BotState.__dataclass_fields__
                    state = BotState(**{k: v for k, v in pg_data.items() if k in valid_keys})
                    logger.info("🐘 Стан відновлено з PostgreSQL")
                except Exception as e:
                    logger.warning(f"PostgreSQL parse error: {e}")

        # Якщо стан не знайдено — новий запуск
        if state is None:
            logger.info("🆕 Новий стан (перший запуск)")
            return BotState()

        # DRY-RUN: виправляємо капітал, який міг бути з'їдений старим багом
        if config.DRY_RUN and state.current_capital < config.INITIAL_CAPITAL:
            logger.warning(f"🧪 DRY-RUN: виправлено капітал ${state.current_capital:.2f} → ${config.INITIAL_CAPITAL:.2f} (баг old code)")
            state.current_capital = config.INITIAL_CAPITAL
            state.peak_capital = config.INITIAL_CAPITAL
            state.is_halted = False
            state.halt_reason = ""

        return state

    def save_state(self):
        self.state.last_update = datetime.now(timezone.utc).isoformat()
        data = asdict(self.state)

        # 1. PostgreSQL (персистентний на Render)
        pg_ok = _pg_save(data)
        if pg_ok:
            if self._pg_working is None or not self._pg_working:
                logger.info("🐘 PostgreSQL: стан збережено")
            self._pg_working = True
        else:
            self._pg_working = False

        # 2. JSONBin (хмара-backup, тільки 1 спроба)
        _jsonbin_save(data)

        # 3. Локальний файл (епемерний на Render, але працює локально)
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Помилка збереження локального стану: {e}")

    def save_positions(self, active_positions: dict):
        try:
            serialized = {}
            for cid, pos in active_positions.items():
                serialized[cid] = {
                    "question":      pos.question,
                    "direction":     pos.direction,
                    "token_id":      pos.token_id,
                    "entry_price":   pos.entry_price,
                    "size_usd":      pos.size_usd,
                    "shares":        pos.shares,
                    "entry_time":    pos.entry_time.isoformat() if hasattr(pos.entry_time, "isoformat") else str(pos.entry_time),
                    "edge_at_entry": pos.edge_at_entry,
                    "city":          pos.city,
                    "market_type":   pos.market_type,
                }
            self.state.open_positions = serialized

            # Збереження та очищення застарілих closed_positions (>24h)
            now = datetime.now(timezone.utc)
            cleaned_closed = {}
            import trader as _trader
            
            for cid, closed_dt in _trader._recently_closed.items():
                dt_str = closed_dt.isoformat() if hasattr(closed_dt, "isoformat") else str(closed_dt)
                cleaned_closed[cid] = dt_str

            if self.state.closed_positions:
                for cid, dt_str in self.state.closed_positions.items():
                    try:
                        dt = datetime.fromisoformat(dt_str)
                        if (now - dt).total_seconds() < 86400:  # Лишаємо в базі 24 години
                            cleaned_closed[cid] = dt_str
                    except Exception:
                        pass
            
            self.state.closed_positions = cleaned_closed
            self.save_state()
        except Exception as e:
            logger.error(f"Помилка збереження позицій: {e}")

    def restore_positions(self) -> dict:
        import trader as _trader
        restored = {}
        try:
            if self.state.open_positions:
                for cid, d in self.state.open_positions.items():
                    norm_cid = _trader.normalize_condition_id(cid)

                    entry_time = datetime.fromisoformat(d["entry_time"]) if "entry_time" in d else datetime.now(timezone.utc)
                    if entry_time.tzinfo is None:
                        entry_time = entry_time.replace(tzinfo=timezone.utc)
                    
                    pos = _trader.Position(
                        condition_id=norm_cid,
                        question=d.get("question", ""),
                        direction=d.get("direction", "BUY_NO"),
                        token_id=d.get("token_id", ""),
                        entry_price=float(d.get("entry_price", 0)),
                        current_price=float(d.get("entry_price", 0)),
                        size_usd=float(d.get("size_usd", 0)),
                        shares=float(d.get("shares", 0)),
                        entry_time=entry_time,
                        edge_at_entry=float(d.get("edge_at_entry", 0)),
                        city=d.get("city", ""),
                        market_type=d.get("market_type", "temperature"),
                    )
                    restored[norm_cid] = pos

            # Відновлення списку нещодавно закритих угод
            if self.state.closed_positions:
                for cid, dt_str in self.state.closed_positions.items():
                    try:
                        _trader._recently_closed[cid] = datetime.fromisoformat(dt_str)
                    except Exception:
                        pass
            
            if restored:
                logger.info(f"♻️  Відновлено {len(restored)} позицій після рестарту (ID та closed_positions нормалізовані)")
        except Exception as e:
            logger.error(f"Помилка відновлення позицій: {e}")
        return restored

    def check_drawdown(self) -> bool:
        dd = self.state.drawdown_pct
        if dd >= config.MAX_DRAWDOWN_PCT:
            self._halt(f"Просадка {dd:.1%} >= ліміт {config.MAX_DRAWDOWN_PCT:.0%}")
            return False
        return True

    def check_high_edge_warning(self, edge_results: list, threshold: float = 0.70) -> None:
        high_edge = [r for r in edge_results if r.edge > threshold]
        if len(high_edge) > 3:
            logger.warning(f"⚠️ {len(high_edge)} угод з edge > {threshold:.0%} за цикл")

    def check_hourly_trade_limit(self, max_per_hour: int = 50) -> bool:
        current_hour = datetime.now().hour
        if current_hour != self._hour_mark:
            self._trade_count_hour = 0
            self._hour_mark = current_hour
        if self._trade_count_hour >= max_per_hour:
            logger.warning(f"Ліміт угод: {self._trade_count_hour}/{max_per_hour}/год")
            return False
        return True

    def can_trade(self) -> bool:
        if self.state.is_halted:
            logger.error(f"БОТ ЗУПИНЕНО: {self.state.halt_reason}")
            return False
        if not self.check_drawdown():
            return False
        if config.DRY_RUN:
            return True
        return True

    def pre_trade_check(self, size_usd: float, capital: float) -> bool:
        if size_usd < config.MIN_POSITION_USD:
            return False
        if size_usd > capital * config.MAX_POSITION_PCT:
            if config.DRY_RUN:
                return True
            return False
        if not config.DRY_RUN and size_usd > capital * 0.95:
            logger.warning("Недостатньо капіталу")
            return False
        return True

    def _halt(self, reason: str):
        logger.critical(f"АВАРІЙНА ЗУПИНКА: {reason}")
        self.state.is_halted = True
        self.state.halt_reason = reason
        self.save_state()

    def resume(self):
        self.state.is_halted = False
        self.state.halt_reason = ""
        self.save_state()
        logger.info("Бот відновлено")

    def record_trade_open(self, size_usd: float):
        self._trade_count_hour += 1
        self.state.total_trades += 1
        self.state.current_capital -= size_usd
        self.save_state()

    def record_trade_close(self, pnl_usd: float, size_usd: float = 0.0, condition_id: str = None):
        self.state.total_pnl += pnl_usd
        if pnl_usd > 0:
            self.state.winning_trades += 1
        else:
            self.state.losing_trades += 1
        self.state.current_capital += size_usd + pnl_usd
        if self.state.current_capital > self.state.peak_capital:
            self.state.peak_capital = self.state.current_capital
        
        # Миттєвий запис у локальну та хмарну базу закритих позицій
        if condition_id:
            import trader as _trader
            norm_cid = _trader.normalize_condition_id(condition_id)
            now_str = datetime.now(timezone.utc).isoformat()
            if self.state.closed_positions is None:
                self.state.closed_positions = {}
            self.state.closed_positions[norm_cid] = now_str
            _trader._recently_closed[norm_cid] = datetime.now(timezone.utc)

        self.save_state()

    def print_summary(self):
        s = self.state
        logger.info("=" * 50)
        logger.info("📊 ЗВІТ БОТА")
        logger.info(f"   Капітал:    ${s.current_capital:.2f} (старт ${s.initial_capital:.2f})")
        logger.info(f"   ROI:        {s.roi_pct:+.1%}")
        logger.info(f"   Просадка:   {s.drawdown_pct:.1%}")
        logger.info(f"   Угоди:      {s.total_trades} (виграш: {s.winning_trades}, програш: {s.losing_trades})")
        logger.info(f"   Win rate:   {s.win_rate:.1%}")
        logger.info(f"   PnL total:  ${s.total_pnl:+.2f}")
        logger.info(f"   Режим:      {'🧪 DRY-RUN' if config.DRY_RUN else '💰 РЕАЛЬНИЙ'}")
        jb = "✅ активний" if (_JSONBIN_KEY and _JSONBIN_BIN_ID) else "❌ не налаштований"
        logger.info(f"   JSONBin:    {jb}")
        logger.info("=" * 50)