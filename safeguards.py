"""
safeguards.py — Polymarket Weather Bot 2026 (v13 — Neon PostgreSQL)
Захисні механізми: circuit breakers, drawdown monitor, аварійна зупинка.
PostgreSQL = primary (Neon serverless via psycopg2), SQLite = automatic fallback.
"""

import logging
import json
import os
import time as _time
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, Optional
from dataclasses import dataclass, asdict

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# SQLite fallback (безкоштовно, без терміну дії)
# ─────────────────────────────────────────────
_SQLITE_PATH = os.path.join(config.DATA_DIR, "bot_trades.db")
_sqlite_lock = threading.Lock()


def _get_sqlite_conn() -> Optional[sqlite3.Connection]:
    """Повертає SQLite з'єднання для fallback логування угод."""
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(_SQLITE_PATH, timeout=5)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS trade_log ("
            "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "    timestamp TEXT DEFAULT (datetime('now')),"
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
            "    edge_at_entry REAL,"
            "    size_usd REAL,"
            "    entry_price REAL,"
            "    pnl_usd REAL,"
            "    status TEXT,"
            "    strategy TEXT DEFAULT 'UNKNOWN',"
            "    dry_run INTEGER DEFAULT 1,"
            "    time_decay_factor REAL)"
        )
        conn.commit()
        return conn
    except Exception as e:
        logger.debug(f"SQLite init error: {e}")
        return None


def _log_trade_to_sqlite(trade_data: dict) -> bool:
    """Fallback: запис угоди в локальний SQLite файл."""
    try:
        with _sqlite_lock:
            conn = _get_sqlite_conn()
            if not conn:
                return False
            conn.execute(
                "INSERT INTO trade_log (cycle, action, direction, market_question, city, "
                "forecast_c, threshold_c, our_prob, market_prob, edge, edge_at_entry, "
                "size_usd, entry_price, pnl_usd, status, strategy, dry_run, time_decay_factor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    trade_data.get("edge_at_entry", trade_data.get("edge", 0.0)),
                    trade_data.get("size_usd"),
                    trade_data.get("entry_price"),
                    trade_data.get("pnl_usd"),
                    trade_data.get("status"),
                    trade_data.get("strategy", "UNKNOWN"),
                    1 if trade_data.get("dry_run", True) else 0,
                    trade_data.get("time_decay_factor"),
                )
            )
            conn.commit()
            conn.close()
            return True
    except Exception as e:
        logger.debug(f"SQLite log error: {e}")
        return False

# ─────────────────────────────────────────────
# PostgreSQL persistence (Neon / Render / будь-який)
# psycopg2: стандартний драйвер, працює з Neon connection string як є
# ─────────────────────────────────────────────
_DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL")
_pg_pool = None
_pg_reconnect_attempts = 0
_PG_MAX_RECONNECT = 3


def _get_pg_conn():
    global _pg_pool, _pg_reconnect_attempts
    if not _DATABASE_URL:
        return None

    try:
        import psycopg2
        from psycopg2 import pool
    except ImportError:
        logger.debug("psycopg2 не встановлено, спроба pg8000")
        return _get_pg_conn_pg8000()

    if _pg_pool is not None:
        try:
            conn = _pg_pool.getconn()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            conn.commit()
            return conn
        except Exception:
            try:
                _pg_pool.putconn(conn, close=True)
            except Exception:
                pass
            _pg_pool = None

    if _pg_reconnect_attempts >= _PG_MAX_RECONNECT:
        return None

    try:
        conn_url = _DATABASE_URL
        if "sslmode=" not in conn_url:
            conn_url += "?sslmode=require"

        _pg_pool = pool.SimpleConnectionPool(
            1, 5, dsn=conn_url
        )
        conn = _pg_pool.getconn()
        conn.autocommit = False
        cur = conn.cursor()
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
            "    strategy TEXT DEFAULT 'UNKNOWN',"
            "    dry_run BOOLEAN DEFAULT TRUE,"
            "    time_decay_factor REAL"
            ")"
        )
        try:
            cur.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS strategy TEXT DEFAULT 'UNKNOWN'")
        except Exception as migration_err:
            logger.debug(f"Migration column 'strategy' info/skipped: {migration_err}")
        try:
            cur.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS edge_at_entry REAL")
        except Exception as migration_err:
            logger.debug(f"Migration column 'edge_at_entry' info/skipped: {migration_err}")
        try:
            cur.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS time_decay_factor REAL")
        except Exception as migration_err:
            logger.debug(f"Migration column 'time_decay_factor' info/skipped: {migration_err}")
        conn.commit()
        cur.close()
        _pg_reconnect_attempts = 0
        logger.info("🐘 PostgreSQL (psycopg2+Neon): таблиці bot_state та trade_log перевірено/створено")
        return conn
    except Exception as e:
        _pg_reconnect_attempts += 1
        logger.warning(f"PostgreSQL недоступний (спроба {_pg_reconnect_attempts}): {e}")
        logger.info("Спроба fallback через pg8000...")
        return _get_pg_conn_pg8000()


def _pg_release_conn(conn):
    """Повертає з'єднання в пул (psycopg2)."""
    global _pg_pool
    if _pg_pool is not None:
        try:
            _pg_pool.putconn(conn)
        except Exception:
            pass
    else:
        try:
            conn.close()
        except Exception:
            pass


def _get_pg_conn_pg8000():
    """Fallback: підключення через pg8000 (якщо psycopg2 недоступний)."""
    global _pg_reconnect_attempts
    try:
        import ssl
        import pg8000
        from urllib.parse import urlparse
        parsed = urlparse(_DATABASE_URL)
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        conn = pg8000.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
            timeout=15,
            ssl_context=ssl_context,
        )
        conn.autocommit = False
        cur = conn.cursor()
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
            "    strategy TEXT DEFAULT 'UNKNOWN',"
            "    dry_run BOOLEAN DEFAULT TRUE,"
            "    time_decay_factor REAL"
            ")"
        )
        try:
            cur.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS strategy TEXT DEFAULT 'UNKNOWN'")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS edge_at_entry REAL")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS time_decay_factor REAL")
        except Exception:
            pass
        conn.commit()
        cur.close()
        logger.info("🐘 PostgreSQL (pg8000 fallback): таблиці перевірено/створено")
        return conn
    except Exception as e:
        _pg_reconnect_attempts += 1
        logger.warning(f"pg8000 fallback також недоступний: {e}")
        return None


def reset_pg_reconnect_counter():
    """Скидає лічильник переконнектів (викликається на початку кожного циклу)."""
    global _pg_reconnect_attempts
    _pg_reconnect_attempts = 0


def log_trade_to_pg(trade_data: dict) -> bool:
    """Логування угоди: PostgreSQL → SQLite fallback → file."""
    conn = _get_pg_conn()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO trade_log (cycle, action, direction, market_question, city, "
                "forecast_c, threshold_c, our_prob, market_prob, edge, size_usd, entry_price, "
                "pnl_usd, status, strategy, dry_run, edge_at_entry, time_decay_factor) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                    trade_data.get("strategy", "UNKNOWN"),
                    trade_data.get("dry_run", True),
                    trade_data.get("edge_at_entry", trade_data.get("edge", 0.0)),
                    trade_data.get("time_decay_factor")
                )
            )
            conn.commit()
            cur.close()
            _pg_release_conn(conn)
            return True
        except Exception as e:
            logger.debug(f"PostgreSQL log_trade error: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            _pg_release_conn(conn)
    return _log_trade_to_sqlite(trade_data)


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
        _pg_release_conn(conn)
        return True
    except Exception as e:
        logger.debug(f"PostgreSQL save error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        _pg_release_conn(conn)
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
        _pg_release_conn(conn)
        if row:
            return json.loads(row[0])
    except Exception as e:
        logger.debug(f"PostgreSQL load error: {e}")
        _pg_release_conn(conn)
    return None


STATE_FILE = os.path.join(config.DATA_DIR, "bot_state.json")


@dataclass
class BotState:
    initial_capital: float = config.INITIAL_CAPITAL
    current_capital: float = config.INITIAL_CAPITAL
    peak_capital: float = config.INITIAL_CAPITAL
    peak_equity: float = config.INITIAL_CAPITAL
    unrealized_pnl: float = 0.0
    portfolio_value: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    is_halted: bool = False
    halt_reason: str = ""
    start_time: str = datetime.now(timezone.utc).isoformat()
    last_update: str = datetime.now(timezone.utc).isoformat()
    open_positions: Dict = None
    closed_positions: Dict = None
    last_daily_capital: float = 0.0
    last_daily_reset: str = ""

    def __post_init__(self):
        if self.open_positions is None:
            self.open_positions = {}
        if self.closed_positions is None:
            self.closed_positions = {}
        if self.last_daily_capital <= 0:
            self.last_daily_capital = self.equity or self.current_capital
        if not self.last_daily_reset:
            self.last_daily_reset = datetime.now(timezone.utc).date().isoformat()
            self.closed_positions = {}

    @property
    def win_rate(self) -> float:
        total = self.winning_trades + self.losing_trades
        if total == 0:
            return 0.0
        return self.winning_trades / total

    @property
    def equity(self) -> float:
        return self.current_capital + self.portfolio_value

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity == 0:
            return 0.0
        return (self.peak_equity - self.equity) / self.peak_equity

    @property
    def roi_pct(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return (self.equity - self.initial_capital) / self.initial_capital


class SafeguardManager:

    def __init__(self, config_obj):
        self.config = config_obj
        self.state = self._load_state()
        self._trade_count_hour = 0
        self._hour_mark = datetime.now().hour
        self._pg_working = None
        self._last_unrealized_pnl = 0.0

    def _load_state(self) -> BotState:
        os.makedirs(config.DATA_DIR, exist_ok=True)

        # RESET_POSITIONS=true — ручний скид через Render Environment Variables.
        # Ставиш вручну в Render Dashboard → рестарт → чистий стан.
        # Прибираєш (або false) — нормальне завантаження з PostgreSQL.
        if os.environ.get("RESET_POSITIONS", "").lower() == "true":
            logger.warning("🔄 RESET_POSITIONS=true → ручний скид стану")
            return BotState()

        # PRIMARY: PostgreSQL (персистентний на Render)
        state = None
        pg_data = _pg_load()
        if pg_data:
            try:
                valid_keys = BotState.__dataclass_fields__
                state = BotState(**{k: v for k, v in pg_data.items() if k in valid_keys})
                if state.current_capital >= config.INITIAL_CAPITAL or state.total_pnl != 0.0 or state.total_trades > 0:
                    logger.info(f"🐘 Стан відновлено з PostgreSQL (cap=${state.current_capital:.2f}, PnL=${state.total_pnl:+.2f}, trades={state.total_trades})")
                else:
                    logger.warning(f"🐘 PostgreSQL стан виглядає як скидання (cap=${state.current_capital:.2f}) — перевіряємо локальний файл")
                    state = None
            except Exception as e:
                logger.warning(f"PostgreSQL parse error: {e}")

        # FALLBACK: локальний файл (надійний, без мережевих залежностей)
        if state is None and os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                    valid_keys = BotState.__dataclass_fields__
                    file_state = BotState(**{k: v for k, v in data.items() if k in valid_keys})
                    if file_state.current_capital >= config.INITIAL_CAPITAL or file_state.total_pnl != 0.0 or file_state.total_trades > 0:
                        state = file_state
                        logger.info(f"💾 Стан відновлено з локального файлу (cap=${state.current_capital:.2f})")
                    else:
                        logger.warning(f"💾 Локальний стан також скинутий (cap=${file_state.current_capital:.2f}) — ігноруємо")
            except Exception as e:
                logger.warning(f"Локальний стан пошкоджений: {e}")

        # Якщо стан не знайдено — новий запуск
        if state is None:
            logger.info("🆕 Новий стан (перший запуск)")
            return BotState()

        # БІЛЬШЕ НЕ СКИДАЄМО КАПІТАЛ ПРИ DRY-RUN!
        # Старий баг: кожен рестарт скидав капітал до INITIAL, 
        # що ламало весь PnL трекінг. Тепер капітал зберігається як є.
        
        # Тільки знімаємо halt якщо він був помилковим
        if state.is_halted and state.halt_reason == "":
            state.is_halted = False

        return state

    def save_state(self):
        self.state.last_update = datetime.now(timezone.utc).isoformat()
        data = asdict(self.state)

        # 1. PostgreSQL (персистентний на Render) — PRIMARY
        pg_ok = _pg_save(data)
        if pg_ok:
            if self._pg_working is None or not self._pg_working:
                logger.info("🐘 PostgreSQL: стан збережено")
            self._pg_working = True
        else:
            if self._pg_working is not False:
                logger.warning("⚠️ PostgreSQL недоступний, використовую локальний файл")
            self._pg_working = False

        # 2. Локальний файл (епемерний на Render, але працює локально)
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
                entry_time_str = pos.entry_time.isoformat() if hasattr(pos.entry_time, "isoformat") else str(pos.entry_time)
                end_date_str = ""
                if pos.end_date:
                    end_date_str = pos.end_date.isoformat() if hasattr(pos.end_date, "isoformat") else str(pos.end_date)
                
                serialized[cid] = {
                    "question":      pos.question,
                    "direction":     pos.direction,
                    "token_id":      pos.token_id,
                    "entry_price":   pos.entry_price,
                    "size_usd":      pos.size_usd,
                    "shares":        pos.shares,
                    "entry_time":    entry_time_str,
                    "end_date":      end_date_str,
                    "edge_at_entry": pos.edge_at_entry,
                    "city":          pos.city,
                    "market_type":   pos.market_type,
                    "peak_price":    getattr(pos, "peak_price", pos.entry_price),
                    "trailing_stop_activated": getattr(pos, "trailing_stop_activated", False),
                    "forecast_at_entry_c": getattr(pos, "forecast_at_entry_c", 0.0),
                    "threshold_at_entry_c": getattr(pos, "threshold_at_entry_c", 0.0),
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
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
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

                    # Відновлюємо ОРИГІНАЛЬНИЙ entry_time (а не поточний час)
                    entry_time = datetime.now(timezone.utc)
                    if "entry_time" in d and d["entry_time"]:
                        try:
                            entry_time = datetime.fromisoformat(d["entry_time"])
                        except Exception:
                            pass
                    if entry_time.tzinfo is None:
                        entry_time = entry_time.replace(tzinfo=timezone.utc)
                    
                    # Відновлюємо end_date (критично для resolution!)
                    end_date = None
                    if "end_date" in d and d["end_date"]:
                        try:
                            end_date = datetime.fromisoformat(d["end_date"])
                            if end_date.tzinfo is None:
                                end_date = end_date.replace(tzinfo=timezone.utc)
                        except Exception:
                            pass
                    
                    pos = _trader.Position(
                        condition_id=norm_cid,
                        question=d.get("question", ""),
                        direction=d.get("direction", "BUY_YES"),
                        token_id=d.get("token_id", ""),
                        entry_price=float(d.get("entry_price", 0)),
                        current_price=float(d.get("entry_price", 0)),
                        size_usd=float(d.get("size_usd", 0)),
                        shares=float(d.get("shares", 0)),
                        entry_time=entry_time,
                        edge_at_entry=float(d.get("edge_at_entry", 0)),
                        city=d.get("city", ""),
                        market_type=d.get("market_type", "temperature"),
                        end_date=end_date,
                        peak_price=float(d.get("peak_price", d.get("entry_price", 0))),
                        trailing_stop_activated=bool(d.get("trailing_stop_activated", False)),
                        forecast_at_entry_c=float(d.get("forecast_at_entry_c", 0.0)),
                        threshold_at_entry_c=float(d.get("threshold_at_entry_c", 0.0)),
                    )
                    restored[norm_cid] = pos

            # Відновлення списку нещодавно закритих угод (тільки < 24h)
            if self.state.closed_positions:
                now = datetime.now(timezone.utc)
                for cid, dt_str in self.state.closed_positions.items():
                    try:
                        dt = datetime.fromisoformat(dt_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if (now - dt).total_seconds() < 86400:
                            _trader._recently_closed[cid] = dt
                    except Exception:
                        pass
            
            if len(restored) > config.MAX_ACTIVE_POSITIONS:
                sorted_items = sorted(restored.items(), key=lambda item: item[1].entry_time, reverse=True)
                restored = dict(sorted_items[:config.MAX_ACTIVE_POSITIONS])
            
            if restored:
                logger.info(f"♻️  Відновлено {len(restored)} позицій після рестарту (з end_date та оригінальним entry_time)")
        except Exception as e:
            logger.error(f"Помилка відновлення позицій: {e}")
        return restored

    def _reset_daily_counters_if_needed(self):
        today = datetime.now(timezone.utc).date().isoformat()
        if self.state.last_daily_reset != today:
            self.state.last_daily_capital = self.state.equity
            self.state.last_daily_reset = today
            logger.info(f"🔄 Добова базова лінія оновлена: equity=${self.state.last_daily_capital:.2f}")
            self.save_state()

    def reset_daily_baseline(self, reason: str = ""):
        self.state.last_daily_capital = self.state.equity
        self.state.last_daily_reset = datetime.now(timezone.utc).date().isoformat()
        self.save_state()
        if reason:
            logger.info(f"🔄 Добова базова лінія скинута ({reason}): equity=${self.state.last_daily_capital:.2f}")

    def check_daily_loss(self) -> bool:
        self._reset_daily_counters_if_needed()
        if self.state.last_daily_capital <= 0:
            self.state.last_daily_capital = self.state.equity
        equity = float(self.state.equity)
        cash = float(self.state.current_capital)
        portfolio_value = float(getattr(self.state, "portfolio_value", 0.0))
        unrealized_pnl = float(getattr(self.state, "unrealized_pnl", 0.0))

        daily_loss = max(0.0, self.state.last_daily_capital - equity)
        daily_loss_pct = daily_loss / self.state.last_daily_capital if self.state.last_daily_capital else 0.0
        max_usd = float(getattr(config, "MAX_DAILY_LOSS_USD", float("inf")))
        max_pct = float(getattr(config, "MAX_DAILY_LOSS_PCT", 1.0))
        logger.debug(f"Daily loss check: equity={equity:.2f} USD, cash={cash:.2f} USD, portfolio_value={portfolio_value:.2f} USD, unrealized_pnl={unrealized_pnl:.2f} USD, daily_loss={daily_loss:.2f} USD ({daily_loss_pct:.1%}), limits={max_usd:.2f}/{max_pct:.1%}")
        if daily_loss >= max_usd or daily_loss_pct >= max_pct:
            if config.DRY_RUN:
                logger.warning(f"DRY-RUN daily-loss warning: equity ${daily_loss:.2f} ({daily_loss_pct:.1%}) >= ліміт; LIVE бот був би зупинений")
                return True
            self._halt(f"Добовий збиток за equity ${daily_loss:.2f} ({daily_loss_pct:.1%}) >= ліміт")
            return False
        return True

    def check_validation_gate(self) -> bool:
        if not getattr(config, "VALIDATION_REQUIRED_BEFORE_LIVE", True):
            return True
        if config.DRY_RUN:
            return True
        s = self.state
        total_resolved = s.winning_trades + s.losing_trades
        start_dt = datetime.fromisoformat(s.start_time)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        dry_run_hours = (datetime.now(timezone.utc) - start_dt).total_seconds() / 3600
        failures = []
        if total_resolved < getattr(config, "VALIDATION_MIN_RESOLVED_TRADES", 30):
            failures.append(f"resolved={total_resolved}/{config.VALIDATION_MIN_RESOLVED_TRADES}")
        if dry_run_hours < getattr(config, "VALIDATION_MIN_DRY_RUN_HOURS", 168):
            failures.append(f"hours={dry_run_hours:.1f}/{config.VALIDATION_MIN_DRY_RUN_HOURS}")
        if s.win_rate < getattr(config, "VALIDATION_MIN_WIN_RATE", 0.50):
            failures.append(f"win_rate={s.win_rate:.1%}/{config.VALIDATION_MIN_WIN_RATE:.0%}")
        if s.roi_pct < getattr(config, "VALIDATION_MIN_ROI", 0.00):
            failures.append(f"roi={s.roi_pct:.1%}/{config.VALIDATION_MIN_ROI:.0%}")
        if s.equity < getattr(config, "VALIDATION_MIN_EQUITY", 0.00):
            failures.append(f"equity=${s.equity:.2f}/${config.VALIDATION_MIN_EQUITY:.2f}")
        if failures:
            logger.critical("LIVE blocked by validation gate: " + ", ".join(failures))
            return False
        logger.info("Validation gate passed; LIVE trading allowed")
        return True

    def check_drawdown(self) -> bool:
        dd = self.state.drawdown_pct
        dd_limit = float(os.environ.get('DRAWDOWN_LIMIT', getattr(config, 'DRAWDOWN_LIMIT', 0.30)))
        if dd >= dd_limit:
            self._halt(f"Просадка {dd:.1%} >= ліміт {dd_limit:.0%}")
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
        if not self.check_daily_loss():
            return False
        if not self.check_drawdown():
            return False
        return True

    def pre_trade_check(self, size_usd: float, capital: float) -> bool:
        if size_usd < config.MIN_POSITION_USD:
            logger.warning(f"⚠️ pre_trade_check: size=${size_usd:.2f} < MIN=${config.MIN_POSITION_USD:.2f}")
            return False
        # Перевірка розміру відносно капіталу — працює і в DRY-RUN
        if size_usd > capital * 0.15:
            logger.debug(f"Позиція ${size_usd:.2f} > 15% капіталу ${capital:.2f}")
            return False
        if capital < config.MIN_POSITION_USD:
            logger.warning("Недостатньо капіталу для мінімальної ставки")
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

    def update_portfolio_value(self, portfolio_value: float, unrealized_pnl: float):
        """Оновлює ринкову вартість портфеля, unrealized PnL та оновлює пікове Equity."""
        self.state.portfolio_value = portfolio_value
        self.state.unrealized_pnl = unrealized_pnl
        
        # Розраховуємо поточне Equity
        equity = self.state.equity
        if equity > self.state.peak_equity:
            self.state.peak_equity = equity
            
        self.save_state()

    def record_trade_open(self, size_usd: float):
        self._trade_count_hour += 1
        self.state.total_trades += 1
        # Капітал ЗАВЖДИ трекається (і в DRY-RUN!)
        self.state.current_capital -= size_usd
        self.save_state()

    def record_trade_close(self, pnl_usd: float, size_usd: float = 0.0, condition_id: str = None):
        self.state.total_pnl += pnl_usd
        if pnl_usd > 0:
            self.state.winning_trades += 1
        else:
            self.state.losing_trades += 1
        # Повертаємо ставку + PnL
        self.state.current_capital += size_usd + pnl_usd
        if self.state.current_capital > self.state.peak_capital:
            self.state.peak_capital = self.state.current_capital
        
        # Оновлюємо пікове Equity
        equity = self.state.equity
        if equity > self.state.peak_equity:
            self.state.peak_equity = equity
        
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
        total_resolved = s.winning_trades + s.losing_trades
        logger.info("=" * 50)
        logger.info("📊 ЗВІТ БОТА")
        logger.info(f"   Капітал (кеш): ${s.current_capital:.2f} (старт ${s.initial_capital:.2f})")
        logger.info(f"   Позиції:       ${s.portfolio_value:.2f}")
        logger.info(f"   Unrealized:    ${s.unrealized_pnl:+.2f}")
        logger.info(f"   💎 Equity:     ${s.equity:.2f}")
        logger.info(f"   ROI:           {s.roi_pct:+.1%} (equity-based)")
        logger.info(f"   Просадка:      {s.drawdown_pct:.1%} (ліміт: {float(os.environ.get('DRAWDOWN_LIMIT', getattr(config, 'DRAWDOWN_LIMIT', 0.30))):.0%})")
        logger.info(f"   Угоди:         {s.total_trades} (виграш: {s.winning_trades}, програш: {s.losing_trades})")
        logger.info(f"   Win rate:      {s.win_rate:.1%} (з {total_resolved} resolved)")
        logger.info(f"   PnL total:     ${s.total_pnl:+.2f} (realized)")
        logger.info(f"   Режим:         {'🧪 DRY-RUN' if config.DRY_RUN else '💰 РЕАЛЬНИЙ'}")
        pg_status = "✅ активний" if self._pg_working else "❌ недоступний"
        logger.info(f"   PostgreSQL:    {pg_status}")
        logger.info("=" * 50)