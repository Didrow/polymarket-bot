"""
safeguards.py — Polymarket Weather Bot 2026
Захисні механізми: circuit breakers, drawdown monitor, аварійна зупинка.
"""

import logging
import json
import os
from datetime import datetime, timezone
from typing import Dict, Optional
from dataclasses import dataclass, asdict

import config

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(config.DATA_DIR, "bot_state.json")


@dataclass
class BotState:
    """Стан бота (зберігається між запусками)."""
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
    # Збереження відкритих позицій між рестартами (v24-fix)
    # Формат: {condition_id: {question, direction, entry_price, size_usd, shares, entry_time, edge_at_entry, city, market_type}}
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


class SafeguardManager:
    """
    Менеджер захисних механізмів.
    Перевіряє умови безпеки перед кожною угодою і після.
    """

    def __init__(self):
        self.state = self._load_state()
        self._trade_count_hour = 0
        self._hour_mark = datetime.now().hour

    # ─── Збереження стану ────────────────────────────────────

    def _load_state(self) -> BotState:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                    return BotState(**data)
            except Exception:
                pass
        return BotState()

    def save_state(self):
        try:
            self.state.last_update = datetime.now(timezone.utc).isoformat()
            with open(STATE_FILE, "w") as f:
                json.dump(asdict(self.state), f, indent=2)
        except Exception as e:
            logger.error(f"Помилка збереження стану: {e}")

    def save_positions(self, active_positions: dict):
        """Зберігає відкриті позиції в bot_state.json (захист від рестарту)."""
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
                    "entry_time":    pos.entry_time.isoformat() if hasattr(pos.entry_time, 'isoformat') else str(pos.entry_time),
                    "edge_at_entry": pos.edge_at_entry,
                    "city":          pos.city,
                    "market_type":   pos.market_type,
                }
            self.state.open_positions = serialized
            self.save_state()
        except Exception as e:
            logger.error(f"Помилка збереження позицій: {e}")

    def restore_positions(self) -> dict:
        """Відновлює позиції з bot_state.json після рестарту."""
        import trader as _trader
        from datetime import timezone as _tz
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

    # ─── Circuit Breakers ─────────────────────────────────────

    def check_drawdown(self) -> bool:
        """
        Перевірити просадку. Зупинитися якщо > MAX_DRAWDOWN_PCT.
        Returns True якщо все ОК, False якщо HALT.
        """
        dd = self.state.drawdown_pct
        if dd >= config.MAX_DRAWDOWN_PCT:
            self._halt(f"Просадка {dd:.1%} ≥ ліміт {config.MAX_DRAWDOWN_PCT:.0%}")
            return False
        return True

    def check_high_edge_warning(self, edge_results: list, threshold: float = 0.70) -> None:
        """Попередження якщо за цикл > 3 угод з дуже високим edge."""
        high_edge = [r for r in edge_results if r.edge > threshold]
        if len(high_edge) > 3:
            logger.warning(
                f"⚠️ {len(high_edge)} угод з edge > {threshold:.0%} за цикл "
                f"— можлива аномалія прогнозу!"
            )

    def check_hourly_trade_limit(self, max_per_hour: int = 50) -> bool:
        """Не більше N угод на годину."""
        current_hour = datetime.now().hour
        if current_hour != self._hour_mark:
            self._trade_count_hour = 0
            self._hour_mark = current_hour

        if self._trade_count_hour >= max_per_hour:
            logger.warning(f"Ліміт угод: {self._trade_count_hour}/{max_per_hour} за годину")
            return False
        return True

    def can_trade(self) -> bool:
        """Глобальна перевірка: чи може бот торгувати?"""
        if self.state.is_halted:
            logger.error(f"БОТ ЗУПИНЕНО: {self.state.halt_reason}")
            return False

        if not self.check_drawdown():
            return False

        if config.DRY_RUN:
            return True  # У dry-run завжди "можна" (симуляція)

        return True

    def pre_trade_check(self, size_usd: float, capital: float) -> bool:
        """Перевірки перед конкретною угодою."""
        # Мінімальний розмір
        if size_usd < config.MIN_POSITION_USD:
            logger.debug(f"Розмір ${size_usd:.2f} < мінімум ${config.MIN_POSITION_USD}")
            return False

        # Максимальний % від капіталу
        if size_usd > capital * config.MAX_POSITION_PCT:
            if config.DRY_RUN:
                logger.info(f"DRY_RUN: розмір ${size_usd:.2f} ({size_usd/capital:.1%} капіталу)")
                return True
            else:
                logger.warning(f"Розмір ${size_usd:.2f} > {config.MAX_POSITION_PCT:.0%} від капіталу")
                return False

        # Достатньо капіталу (тільки реальний режим)
        if not config.DRY_RUN and size_usd > capital * 0.95:
            logger.warning("Недостатньо капіталу для угоди")
            return False

        return True

    def _halt(self, reason: str):
        logger.critical(f"🚨 АВАРІЙНА ЗУПИНКА: {reason}")
        self.state.is_halted = True
        self.state.halt_reason = reason
        self.save_state()

    def resume(self):
        """Відновити роботу (вручну, після перевірки)."""
        self.state.is_halted = False
        self.state.halt_reason = ""
        self.save_state()
        logger.info("✅ Бот відновлено")

    # ─── Оновлення після угод ────────────────────────────────

    def record_trade_open(self, size_usd: float):
        """Записати відкриття угоди."""
        self._trade_count_hour += 1
        self.state.total_trades += 1
        if not config.DRY_RUN:
            self.state.current_capital -= size_usd
        self.save_state()

    def record_trade_close(self, pnl_usd: float):
        """Записати закриття угоди з PnL.

        ВИПРАВЛЕННЯ: статистика (winning_trades, losing_trades, total_pnl)
        оновлюється ЗАВЖДИ — і в DRY_RUN, і в реальному режимі.
        Тільки current_capital (реальний баланс гаманця) змінюється
        виключно в реальному режимі.
        """
        # Статистика — завжди (DRY_RUN валідація потребує WIN/LOSS лічильників)
        self.state.total_pnl += pnl_usd
        if pnl_usd > 0:
            self.state.winning_trades += 1
        else:
            self.state.losing_trades += 1

        # Реальний баланс гаманця — тільки в живому режимі
        if not config.DRY_RUN:
            self.state.current_capital += pnl_usd
            if self.state.current_capital > self.state.peak_capital:
                self.state.peak_capital = self.state.current_capital

        self.save_state()

    def print_summary(self):
        """Вивести щогодинний звіт у логи."""
        s = self.state
        logger.info("=" * 50)
        logger.info(f"📊 ЗВІТ БОТА")
        logger.info(f"   Капітал:    ${s.current_capital:.2f} (старт ${s.initial_capital:.2f})")
        logger.info(f"   ROI:        {s.roi_pct:+.1%}")
        logger.info(f"   Просадка:   {s.drawdown_pct:.1%}")
        logger.info(f"   Угоди:      {s.total_trades} (виграш: {s.winning_trades}, програш: {s.losing_trades})")
        logger.info(f"   Win rate:   {s.win_rate:.1%}")
        logger.info(f"   PnL total:  ${s.total_pnl:+.2f}")
        logger.info(f"   Режим:      {'🧪 DRY-RUN' if config.DRY_RUN else '💰 РЕАЛЬНИЙ'}")
        logger.info("=" * 50)
