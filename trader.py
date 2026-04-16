"""
trader.py — Polymarket Weather Bot 2026 (з обмеженням для extreme tail)
"""

import math
import time
import logging
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field

import config
from edge_calculator import EdgeResult
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)

_vol_cache: Dict[str, Tuple[float, float]] = {}
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
        self.pnl_pct = (current_price - self.entry_price) / self.entry_price
        self.pnl_usd = self.pnl_pct * self.size_usd


# (функції fetch_price_history, calculate_ewma_volatility, get_market_volatility,
# calculate_volatility_targeted_size, kelly_position_size — залишаються як у тебе)

def decide_position_size(edge_result: EdgeResult, current_capital: float) -> float:
    market = edge_result.market
    token_id = market.token_yes_id if edge_result.edge_direction == "BUY_YES" else market.token_no_id
    entry_price = market.best_ask_yes if edge_result.edge_direction == "BUY_YES" else 1 - market.best_bid_yes

    vol_size, vol_log = calculate_volatility_targeted_size(token_id, current_capital)
    logger.info(f"  Vol Targeting: {vol_log}")

    win_loss = (1 - entry_price) / entry_price if entry_price > 0 else 1.0
    kelly_size = kelly_position_size(edge_result.edge, current_capital, win_loss)
    logger.info(f"  Kelly size: ${kelly_size:.2f}")

    final_size = min(vol_size, kelly_size)
    final_size = max(final_size, config.MIN_POSITION_USD)
    final_size = min(final_size, config.MAX_POSITION_USD)

    # EXTREME TAIL — жорстке обмеження
    if (config.ENABLE_EXTREME_TAIL and
        market.detected_city in config.EXTREME_TAIL_CITIES and
        edge_result.edge_direction == "BUY_YES" and
        market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES):
        final_size = min(final_size, config.EXTREME_TAIL_MAX_SIZE_USD)
        logger.info(f"  EXTREME TAIL limit: ${final_size:.2f}")

    logger.info(f"  Final size: ${final_size:.2f}")
    return final_size


# (решта функцій place_trade, check_and_close_positions тощо — залишаються як у тебе)
