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

    def check_hourly_trade_limit(self, max_per_hour: int = 20) -> bool:
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
            logger.warning(f"Розмір ${size_usd:.2f} > {config.MAX_POSITION_PCT:.0%} від капіталу")
            return False

        # Достатньо капіталу
        if size_usd > capital * 0.95:
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
        self._trade_count_hour += 1
        self.state.total_trades += 1
        self.state.current_capital -= size_usd
        self.save_state()

    def record_trade_close(self, pnl_usd: float):
        self.state.current_capital += pnl_usd
        self.state.total_pnl += pnl_usd
        if pnl_usd > 0:
            self.state.winning_trades += 1
        else:
            self.state.losing_trades += 1

        # Оновлення пікового капіталу
        if self.state.current_capital > self.state.peak_capital:
            self.state.peak_capital = self.state.current_capital

        self.save_state()

    def print_summary(self):
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
