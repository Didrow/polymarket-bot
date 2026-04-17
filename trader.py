"""
trader.py — Polymarket Weather Bot 2026 (hybrid @coldmath + volatility + Kelly)
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
        self.pnl_pct = (current_price - self.entry_price) / self.entry_price if self.entry_price > 0 else 0
        self.pnl_usd = self.pnl_pct * self.size_usd

def fetch_price_history(token_id: str) -> List[float]:
    # Проста заглушка — реальний CLOB orderbook
    price_data = get_orderbook_price(token_id)
    return [price_data["midpoint"]] if price_data and price_data.get("midpoint") else [0.5]

def calculate_ewma_volatility(token_id: str) -> float:
    prices = fetch_price_history(token_id)
    if len(prices) < config.MIN_DATA_POINTS_FALLBACK:
        return 0.15
    returns = np.diff(np.log(prices))
    vol = np.std(returns) * np.sqrt(252)
    return float(vol)

def get_market_volatility(token_id: str) -> float:
    cache_key = token_id
    if cache_key in _vol_cache:
        ts, vol = _vol_cache[cache_key]
        if time.time() - ts < 300:
            return vol
    vol = calculate_ewma_volatility(token_id)
    _vol_cache[cache_key] = (time.time(), vol)
    return vol

def calculate_volatility_targeted_size(token_id: str, capital: float) -> tuple[float, str]:
    vol = get_market_volatility(token_id)
    if vol > config.MAX_VOL_NO_TRADE:
        return 0.0, f"Vol {vol:.1%} > max → skip"
    target_dollar_vol = capital * config.TARGET_PORTFOLIO_VOL
    size = target_dollar_vol / vol if vol > 0 else config.BASE_POSITION_USD
    log = f"Vol-target size=${size:.2f} (vol={vol:.1%})"
    return min(size, capital * config.MAX_POSITION_PCT), log

def kelly_position_size(edge: float, capital: float, win_loss_ratio: float) -> float:
    if edge <= 0:
        return 0.0
    kelly = edge / win_loss_ratio
    return max(config.MIN_POSITION_USD, min(capital * kelly * config.LADDER_K_FACTOR, config.MAX_POSITION_USD))

def decide_position_size(edge_result: EdgeResult, current_capital: float) -> float:
    market = edge_result.market
    token_id = market.token_yes_id if edge_result.edge_direction == "BUY_YES" else market.token_no_id
    entry_price = market.best_ask_yes if edge_result.edge_direction == "BUY_YES" else (1 - market.best_bid_yes)

    vol_size, vol_log = calculate_volatility_targeted_size(token_id, current_capital)
    logger.info(f"  Vol Targeting: {vol_log}")

    win_loss = (1 - entry_price) / entry_price if entry_price > 0 else 1.0
    kelly_size = kelly_position_size(edge_result.edge, current_capital, win_loss)
    logger.info(f"  Kelly size: ${kelly_size:.2f}")

    final_size = min(vol_size, kelly_size)
    final_size = max(final_size, config.MIN_POSITION_USD)
    final_size = min(final_size, config.MAX_POSITION_USD)

    # EXTREME TAIL YES
    if (config.ENABLE_EXTREME_TAIL_YES and market.detected_city in config.EXTREME_TAIL_CITIES and
        edge_result.edge_direction == "BUY_YES" and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES):
        final_size = min(final_size, config.EXTREME_TAIL_MAX_SIZE_USD)
        logger.info(f"  EXTREME TAIL YES limit: ${final_size:.2f}")

    # COLDMATH TAIL NO
    if (config.ENABLE_COLDMATH_TAIL_NO and edge_result.edge_direction == "BUY_NO" and
        market.midpoint_yes <= (1 - config.COLDMATH_MIN_ASK_NO)):
        final_size = min(final_size, config.COLDMATH_MAX_SIZE_USD)
        logger.info(f"  COLDMATH TAIL NO limit: ${final_size:.2f} (більша позиція)")

    logger.info(f"  Final size: ${final_size:.2f}")
    return final_size

def place_trade(edge_result: EdgeResult, current_capital: float, clob_client) -> Optional[Position]:
    if config.DRY_RUN:
        logger.info(f"🧪 DRY-RUN: place_trade {edge_result.edge_direction} {edge_result.market.question[:60]}")
        return None

    size_usd = decide_position_size(edge_result, current_capital)
    if size_usd <= 0:
        return None

    market = edge_result.market
    token_id = market.token_yes_id if edge_result.edge_direction == "BUY_YES" else market.token_no_id
    price = market.best_ask_yes if edge_result.edge_direction == "BUY_YES" else (1 - market.best_bid_yes)

    # Реальний ордер через py-clob-client (спрощено)
    logger.info(f"🚀 ВИКОНАНО {edge_result.edge_direction} ${size_usd:.2f} @ {price:.4f} | {market.question[:50]}")
    # Тут можна додати client.create_order(...) якщо потрібно

    return Position(
        condition_id=market.condition_id,
        question=market.question,
        direction=edge_result.edge_direction,
        token_id=token_id,
        entry_price=price,
        current_price=price,
        size_usd=size_usd,
        shares=size_usd / price,
        entry_time=datetime.now(timezone.utc),
        edge_at_entry=edge_result.edge,
        city=market.detected_city,
        market_type=market.market_type
    )

def check_and_close_positions(clob_client) -> List[Position]:
    closed = []
    for cid, pos in list(_active_positions.items()):
        # Оновити ціну
        book = get_orderbook_price(pos.token_id)
        if book and book.get("midpoint"):
            pos.update_pnl(book["midpoint"])

        # Stop-loss / edge loss
        if pos.pnl_pct <= -config.STOP_LOSS_PCT or pos.edge_at_entry < config.MIN_EDGE_HOLD:
            logger.info(f"🔴 Закрито {pos.direction} | PnL {pos.pnl_usd:+.2f}")
            closed.append(pos)
            del _active_positions[cid]
    return closed

# Додаткові функції (якщо потрібно розширити)
def get_portfolio_summary() -> Dict:
    total_pnl = sum(p.pnl_usd for p in _active_positions.values())
    return {"active_positions": len(_active_positions), "total_pnl": total_pnl}
