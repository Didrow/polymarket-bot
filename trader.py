"""
trader.py — Polymarket Weather Bot 2026 (v9 production)

ВИПРАВЛЕНО:
  - PnL tracking через Gamma API polling (resolved markets)
  - Hourly limit підвищено до 50/год
  - Дедуплікація: не відкриваємо угоду на той самий condition_id двічі
  - DRY_RUN статистика: pnl_usd рахується після resolution
"""

import math
import time
import logging
import requests
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, List
from dataclasses import dataclass, field

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
    status: str = "OPEN"    # OPEN | RESOLVED_YES | RESOLVED_NO | CLOSED

    def update_pnl(self, current_price: float):
        self.current_price = current_price
        if self.entry_price > 0:
            self.pnl_pct = (current_price - self.entry_price) / self.entry_price
            self.pnl_usd = self.pnl_pct * self.size_usd

    def resolve(self, resolved_yes: bool):
        """
        Розрахунок реального PnL після resolution.
        Shares × $1 (якщо виграш) - size_usd = profit/loss
        """
        if self.direction == "BUY_YES":
            payout = self.shares * 1.0 if resolved_yes else 0.0
        else:  # BUY_NO
            # NO share виплачує $1 якщо NOT resolved_yes
            payout = self.shares * 1.0 if not resolved_yes else 0.0

        self.pnl_usd = payout - self.size_usd
        self.pnl_pct = self.pnl_usd / self.size_usd if self.size_usd > 0 else 0.0
        self.status = "RESOLVED_YES" if resolved_yes else "RESOLVED_NO"
        self.current_price = 1.0 if resolved_yes else 0.0


# ══════════════════════════════════════════════════════════════════
# RESOLUTION POLLING (Gamma API)
# ══════════════════════════════════════════════════════════════════

_resolution_cache: Dict[str, Optional[bool]] = {}


def check_market_resolved(condition_id: str) -> Optional[bool]:
    """
    Перевіряє чи ринок розв'язаний через Gamma API.
    Повертає True (YES win), False (NO win), None (ще відкритий).
    """
    if condition_id in _resolution_cache:
        return _resolution_cache[condition_id]

    try:
        r = requests.get(
            f"{config.GAMMA_URL}/markets",
            params={"conditionId": condition_id},
            timeout=8
        )
        if r.status_code != 200:
            return None

        data = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        if not markets:
            return None

        m = markets[0]
        if not m.get("closed", False) and not m.get("resolved", False):
            return None  # ще відкритий

        # Визначаємо winner
        outcome_prices = m.get("outcomePrices")
        if outcome_prices:
            import json
            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            if len(prices) >= 2:
                resolved_yes = float(prices[0]) >= 0.99
                _resolution_cache[condition_id] = resolved_yes
                return resolved_yes

        # Альтернативний спосіб через winnerIndex
        winner = m.get("winnerIndex")
        if winner is not None:
            resolved_yes = int(winner) == 0  # 0 = YES wins
            _resolution_cache[condition_id] = resolved_yes
            return resolved_yes

    except Exception as e:
        logger.debug(f"Resolution check error {condition_id[:12]}: {e}")

    return None


# ══════════════════════════════════════════════════════════════════
# ПОЗИЦІЯ-РОЗМІР (спрощена, реалістична)
# ══════════════════════════════════════════════════════════════════

def _get_market_vol(token_id: str) -> float:
    """Консервативна заглушка — vol 18% (заглушка, бо немає orderbook)."""
    cached = _vol_cache.get(token_id)
    if cached and time.time() - cached[0] < 1800:
        return cached[1]
    vol = 0.18  # fallback
    _vol_cache[token_id] = (time.time(), vol)
    return vol


def decide_position_size(edge_result: EdgeResult, current_capital: float) -> float:
    """
    Розмір позиції: мін(Kelly, vol-target, config.MAX_POSITION_USD).
    """
    market = edge_result.market
    direction = edge_result.edge_direction
    entry_price = market.best_ask_yes if direction == "BUY_YES" else (1 - market.best_bid_yes)
    token_id = market.token_yes_id if direction == "BUY_YES" else market.token_no_id

    # Vol-target
    vol = _get_market_vol(token_id)
    if vol > config.MAX_VOL_NO_TRADE:
        vol_size = config.BASE_POSITION_USD
    else:
        target_dv = current_capital * config.TARGET_PORTFOLIO_VOL
        vol_size = min(target_dv / vol, current_capital * config.MAX_POSITION_PCT)
    logger.info(f"  Vol Targeting: Vol-target size=${vol_size:.2f} (vol={vol:.1%})")

    # Kelly (25% Kelly для консервативності)
    win_loss = (1 - entry_price) / max(entry_price, 0.001)
    kelly_raw = edge_result.edge / max(win_loss, 0.01)
    kelly_size = current_capital * kelly_raw * 0.25
    kelly_size = max(config.MIN_POSITION_USD, min(kelly_size, config.MAX_POSITION_USD))
    logger.info(f"  Kelly size: ${kelly_size:.2f}")

    final = min(vol_size, kelly_size)
    final = max(final, config.MIN_POSITION_USD)
    final = min(final, config.MAX_POSITION_USD)

    # Ліміти по типу стратегії
    if direction == "BUY_NO":
        no_price = 1 - market.midpoint_yes
        if no_price >= config.COLDMATH_MIN_ASK_NO:
            final = min(final, config.COLDMATH_MAX_SIZE_USD)
            logger.info(f"  COLDMATH TAIL NO limit: ${final:.2f}")

    if direction == "BUY_YES" and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES:
        final = min(final, config.EXTREME_TAIL_MAX_SIZE_USD)
        logger.info(f"  EXTREME TAIL YES limit: ${final:.2f}")

    logger.info(f"  Final size: ${final:.2f}")
    return round(final, 2)


# ══════════════════════════════════════════════════════════════════
# PLACE TRADE
# ══════════════════════════════════════════════════════════════════

def place_trade(
    edge_result: EdgeResult,
    current_capital: float,
    clob_client
) -> Optional[Position]:
    """
    Відкриває позицію (DRY_RUN або реальна).
    Дедуплікація: якщо condition_id вже в _active_positions — пропускаємо.
    """
    market = edge_result.market

    # Дедуплікація — не відкриваємо той самий ринок двічі
    if market.condition_id in _active_positions:
        logger.debug(f"Вже відкрито: {market.condition_id[:12]}")
        return None

    price = market.best_ask_yes if edge_result.edge_direction == "BUY_YES" else (1 - market.best_bid_yes)
    price = max(price, 0.001)
    size_usd = decide_position_size(edge_result, current_capital)

    token_id = (market.token_yes_id if edge_result.edge_direction == "BUY_YES"
                else market.token_no_id)

    pos = Position(
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
        market_type=market.market_type,
    )

    if config.DRY_RUN:
        logger.info(
            f"🧪 DRY-RUN: place_trade {edge_result.edge_direction} | "
            f"{market.question[:60]}"
        )
        _active_positions[market.condition_id] = pos
        return pos

    # ── Реальна торгівля ──────────────────────────────────────
    if clob_client is None:
        logger.warning("CLOB клієнт недоступний")
        return None

    try:
        order = clob_client.create_and_post_order({
            "token_id": token_id,
            "price": round(price, 4),
            "size": round(size_usd, 2),
            "side": "BUY",
        })
        if order:
            logger.info(f"✅ Реальна угода виконана: {order}")
            _active_positions[market.condition_id] = pos
            return pos
    except Exception as e:
        logger.error(f"CLOB order error: {e}")

    return None


# ══════════════════════════════════════════════════════════════════
# ПЕРЕВІРКА ТА ЗАКРИТТЯ ПОЗИЦІЙ (з resolution polling)
# ══════════════════════════════════════════════════════════════════

def check_and_close_positions(clob_client) -> List[Position]:
    """
    Перевіряє кожну відкриту позицію:
      1. Чи ринок розв'язаний (через Gamma API)?
         → Розраховує реальний PnL і закриває
      2. Чи перевищений stop-loss?
         → Закриває
    """
    closed = []
    for cid, pos in list(_active_positions.items()):

        # Перевіряємо resolution
        resolved = check_market_resolved(cid)
        if resolved is not None:
            pos.resolve(resolved)
            result = "✅ WIN" if pos.pnl_usd > 0 else "❌ LOSS"
            logger.info(
                f"{result}: {pos.direction} | "
                f"PnL ${pos.pnl_usd:+.2f} ({pos.pnl_pct:+.1%}) | "
                f"{pos.question[:50]}"
            )
            closed.append(pos)
            del _active_positions[cid]
            continue

        # Stop-loss (тільки якщо є ринкова ціна)
        if pos.pnl_pct <= -config.STOP_LOSS_PCT:
            logger.info(f"🔴 Stop-loss: {pos.direction} | PnL {pos.pnl_usd:+.2f}")
            closed.append(pos)
            del _active_positions[cid]

    return closed


def get_portfolio_summary() -> Dict:
    total_pnl = sum(p.pnl_usd for p in _active_positions.values())
    return {
        "active_positions": len(_active_positions),
        "total_pnl": total_pnl,
    }
