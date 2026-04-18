"""
trader.py — Polymarket Weather Bot 2026 (coldmath-style v9 — виправлена)
Використовує Volatility Targeting + Kelly + спеціальні правила для tail trades
"""

import math
import time
import logging
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, List
from dataclasses import dataclass

import config
from edge_calculator import EdgeResult
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)

_vol_cache: Dict[str, tuple] = {}
_active_positions: Dict[str, "Position"] = {}


@dataclass
class Position:
    condition_id: str
    question: str
    direction: str
    token_id: str
    entry_price: float
    current_price: float
    size_usd: float
    shares: float
    entry_time: datetime
    edge_at_entry: float
    city: str
    market_type: str
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    status: str = "OPEN"

    def update_pnl(self, current_price: float):
        self.current_price = current_price
        if self.entry_price > 0:
            self.pnl_pct = (current_price - self.entry_price) / self.entry_price
            self.pnl_usd = self.pnl_pct * self.size_usd
        else:
            self.pnl_pct = 0.0
            self.pnl_usd = 0.0


# ==================== ЗАГЛУШКИ ДЛЯ DRY-RUN ====================
def fetch_price_history(token_id: str) -> List[float]:
    """Проста заглушка для розрахунку волатильності в DRY-RUN режимі"""
    # Повертаємо кілька значень для стабільності розрахунку
    return [0.50, 0.51, 0.49, 0.505, 0.495]


def get_market_volatility(token_id: str) -> float:
    """Розрахунок волатильності з використанням заглушки"""
    prices = fetch_price_history(token_id)
    
    if len(prices) < config.MIN_DATA_POINTS_FALLBACK:
        return 0.18  # консервативне значення за замовчуванням
    
    # Розрахунок лог-доходностей
    returns = np.diff(np.log(prices))
    if len(returns) == 0:
        return 0.18
    
    vol = np.std(returns) * np.sqrt(252)  # річна волатильність
    return float(max(0.05, min(vol, config.MAX_VOL_NO_TRADE)))


def calculate_volatility_targeted_size(token_id: str, capital: float) -> tuple[float, str]:
    """Volatility Targeting — розмір позиції залежно від волатильності ринку"""
    vol = get_market_volatility(token_id)
    
    if vol > config.MAX_VOL_NO_TRADE:
        return 0.0, f"Vol {vol:.1%} > max → skip"
    
    target_dollar_vol = capital * config.TARGET_PORTFOLIO_VOL
    size = target_dollar_vol / vol if vol > 0 else config.BASE_POSITION_USD
    
    log = f"Vol-target size=${size:.2f} (vol={vol:.1%})"
    final_size = min(size, capital * config.MAX_POSITION_PCT)
    
    return final_size, log


def kelly_position_size(edge: float, capital: float, win_loss_ratio: float = 1.0) -> float:
    """Консервативний Kelly criterion"""
    if edge <= 0:
        return 0.0
    kelly = edge / win_loss_ratio
    # Використовуємо тільки 60% від Kelly для безпеки
    return max(config.MIN_POSITION_USD,
               min(capital * kelly * 0.6, config.MAX_POSITION_USD))


def decide_position_size(edge_result: EdgeResult, current_capital: float) -> float:
    """Фінальне рішення по розміру позиції з урахуванням типу угоди"""
    market = edge_result.market
    direction = edge_result.edge_direction
    
    # Визначаємо token_id і entry_price
    if direction == "BUY_YES":
        token_id = market.token_yes_id
        entry_price = market.best_ask_yes
    else:
        token_id = market.token_no_id
        entry_price = 1.0 - market.best_bid_yes   # для NO використовуємо bid

    # Volatility Targeting
    vol_size, vol_log = calculate_volatility_targeted_size(token_id, current_capital)
    logger.info(f"  Vol Targeting: {vol_log}")

    # Kelly
    win_loss = (1.0 - entry_price) / entry_price if entry_price > 0 else 1.0
    kelly_size = kelly_position_size(edge_result.edge, current_capital, win_loss)
    logger.info(f"  Kelly size: ${kelly_size:.2f}")

    # Фінальний розмір
    final_size = min(vol_size, kelly_size)
    final_size = max(final_size, config.MIN_POSITION_USD)
    final_size = min(final_size, config.MAX_POSITION_USD)

    # Спеціальні обмеження для tail-стратегій @coldmath
    if (config.ENABLE_COLDMATH_TAIL_NO and 
        direction == "BUY_NO" and 
        (1.0 - market.midpoint_yes) >= config.COLDMATH_MIN_ASK_NO):
        final_size = min(final_size, config.COLDMATH_MAX_SIZE_USD)
        logger.info(f"  COLDMATH TAIL NO limit applied: ${final_size:.2f}")

    if (config.ENABLE_EXTREME_TAIL_YES and 
        direction == "BUY_YES" and 
        market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES):
        final_size = min(final_size, config.EXTREME_TAIL_MAX_SIZE_USD)
        logger.info(f"  EXTREME TAIL YES limit applied: ${final_size:.2f}")

    logger.info(f"  Final position size: ${final_size:.2f}")
    return round(final_size, 2)


def place_trade(edge_result: EdgeResult, current_capital: float, clob_client) -> Optional[Position]:
    """Відкриття позиції (DRY-RUN або реальна)"""
    market = edge_result.market
    direction = edge_result.edge_direction
    
    if config.DRY_RUN:
        logger.info(f"🧪 DRY-RUN: place_trade {direction} | {market.question[:70]}...")
        
        price = market.best_ask_yes if direction == "BUY_YES" else (1.0 - market.best_bid_yes)
        size_usd = decide_position_size(edge_result, current_capital)
        
        return Position(
            condition_id=market.condition_id,
            question=market.question,
            direction=direction,
            token_id=market.token_yes_id if direction == "BUY_YES" else market.token_no_id,
            entry_price=price,
            current_price=price,
            size_usd=size_usd,
            shares=size_usd / price if price > 0 else 0,
            entry_time=datetime.now(timezone.utc),
            edge_at_entry=edge_result.edge,
            city=market.detected_city,
            market_type=market.market_type
        )

    # Реальна торгівля (поки заглушка — можна розширити пізніше)
    logger.warning("⚠️  Реальна торгівля ще не реалізована. Працюємо тільки в DRY-RUN режимі.")
    return None


def check_and_close_positions(clob_client) -> List[Position]:
    """Перевірка та закриття позицій (заглушка для DRY-RUN)"""
    closed = []
    for cid, pos in list(_active_positions.items()):
        # Оновлюємо PnL (в DRY-RUN використовуємо поточну ціну)
        pos.update_pnl(pos.current_price)

        # Умови закриття
        if (pos.pnl_pct <= -config.STOP_LOSS_PCT or 
            pos.edge_at_entry < config.MIN_EDGE_HOLD):
            
            logger.info(f"🔴 Закрито {pos.direction} | PnL ${pos.pnl_usd:+.2f} | {pos.question[:60]}...")
            closed.append(pos)
            del _active_positions[cid]

    return closed


def get_portfolio_summary() -> Dict:
    """Звіт по портфелю"""
    total_pnl = sum(p.pnl_usd for p in _active_positions.values())
    return {
        "active_positions": len(_active_positions),
        "total_pnl": round(total_pnl, 2)
    }
