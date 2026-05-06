"""
safeguards.py — Polymarket Weather Bot 2026
Захисні механізми: circuit breakers, drawdown monitor, аварійна зупинка.

v26-railway: Подвійне збереження стану
  PRIMARY:  JSONBin.io (хмара) — виживає після будь-якого рестарту
  FALLBACK: локальний bot_state.json — якщо JSONBin недоступний
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

STATE_FILE = os.path.join(config.DATA_DIR, "bot_state.json")

# ══════════════════════════════════════════════════════════════════
# JSONBin.io — хмарне збереження стану
# ══════════════════════════════════════════════════════════════════

_JSONBIN_KEY    = os.getenv("JSONBIN_KEY", "")
_JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID", "")
_JSONBIN_BASE   = "https://api.jsonbin.io/v3/b"
_JSONBIN_TTL    = 15  # v28: збільшено timeout


def _jsonbin_save(data: dict, _retries: int = 2) -> bool:
    """v28: retry 2 рази при timeout."""
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
            logger.warning(f"JSONBin save HTTP {r.status_code}: {r.text[:80]}")
            return False
        except Exception as e:
            if attempt < _retries:
                _time.sleep(2)
            else:
                logger.warning(f"JSONBin save error (спроба {attempt+1}/{_retries+1}): {e}")
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


# ══════════════════════════════════════════════════════════════════
# BotState
# ══════════════════════════════════════════════════════════════════

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

    def __post_init__(self):
        if self.open_positions is None:
            self.open_positions = {}

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


# ══════════════════════════════════════════════════════════════════
# SafeguardManager
# ══════════════════════════════════════════════════════════════════

class SafeguardManager:

    def __init__(self):
        self.state = self._load_state()
        self._trade_count_hour = 0
        self._hour_mark = datetime.now().hour
        self._jsonbin_fail_count = 0

    def _load_state(self) -> BotState:
        os.makedirs(config.DATA_DIR, exist_ok=True)

        # 1. JSONBin (хмара)
        cloud_data = _jsonbin_load()
        if cloud_data:
            try:
                valid_keys = BotState.__dataclass_fields__
                state = BotState(**{k: v for k, v in cloud_data.items() if k in valid_keys})
                logger.info("☁️  Стан відновлено з JSONBin (хмара)")
                return state
            except Exception as e:
                logger.warning(f"JSONBin parse error: {e}")

        # 2. Локальний файл
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                    valid_keys = BotState.__dataclass_fields__
                    state = BotState(**{k: v for k, v in data.items() if k in valid_keys})
                    logger.info("💾 Стан відновлено з локального файлу")
                    return state
            except Exception as e:
                logger.warning(f"Локальний стан пошкоджений: {e}")

        # 3. Новий стан
        logger.info("🆕 Новий стан (перший запуск)")
        return BotState()

    def save_state(self):
        self.state.last_update = datetime.now(timezone.utc).isoformat()
        data = asdict(self.state)

        # Primary: JSONBin
        ok = _jsonbin_save(data)
        if ok:
            self._jsonbin_fail_count = 0
        else:
            self._jsonbin_fail_count += 1
            if self._jsonbin_fail_count <= 3 or self._jsonbin_fail_count % 20 == 0:
                logger.warning(
                    f"⚠️  JSONBin недоступний (#{self._jsonbin_fail_count}) — fallback на локальний файл"
                )

        # Backup: локальний файл
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
            self.save_state()
        except Exception as e:
            logger.error(f"Помилка збереження позицій: {e}")

    def restore_positions(self) -> dict:
        import trader as _trader
        restored = {}
        try:
            for cid, d in self.state.open_positions.items():
                entry_time = datetime.fromisoformat(d["entry_time"]) if "entry_time" in d else datetime.now(timezone.utc)
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
                pos = _trader.Position(
                    condition_id=cid,
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
                restored[cid] = pos
            if restored:
                logger.info(f"♻️  Відновлено {len(restored)} позицій після рестарту")
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
        if not config.DRY_RUN:
            self.state.current_capital -= size_usd
        self.save_state()

    def record_trade_close(self, pnl_usd: float, size_usd: float = 0.0):
        self.state.total_pnl += pnl_usd
        if pnl_usd > 0:
            self.state.winning_trades += 1
        else:
            self.state.losing_trades += 1
        if not config.DRY_RUN:
            # Повертаємо тіло позиції + PnL (record_trade_open вже відняв size_usd)
            self.state.current_capital += size_usd + pnl_usd
            if self.state.current_capital > self.state.peak_capital:
                self.state.peak_capital = self.state.current_capital
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
