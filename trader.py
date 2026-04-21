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
    # Консервативний Kelly: 0.15 базово, 0.10 для слабших NO сигналів
    kelly_fraction = 0.10 if (edge_result.edge_direction == "BUY_NO" and edge_result.edge < 0.45) else 0.15
    kelly_size = current_capital * kelly_raw * kelly_fraction
    kelly_size = max(config.MIN_POSITION_USD, min(kelly_size, config.MAX_POSITION_USD))
    logger.info(f"  Kelly size: ${kelly_size:.2f}")

    final = min(vol_size, kelly_size)
    final = max(final, config.MIN_POSITION_USD)
    final = min(final, config.MAX_POSITION_USD)
    final = min(final, 4.0)  # глобальний ліміт: ніколи > $4 при $100 капіталі

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

    # Дедуплікація з перевіркою значної зміни ціни
    if market.condition_id in _active_positions:
        existing = _active_positions[market.condition_id]
        # ВАЖЛИВО: використовуємо той самий метод розрахунку ціни що в entry_price
        # щоб уникнути хибного 9900% price_change через best_bid=0
        if edge_result.edge_direction == "BUY_YES":
            price_now = market.best_ask_yes
        else:
            price_now = max(1.0 - market.midpoint_yes, 0.01)  # NO price = 1 - midpoint
        price_change = abs(price_now - existing.entry_price) / max(existing.entry_price, 0.001)
        # Переоткриваємо тільки якщо ціна змінилась > 30% (суттєва нова інформація)
        if price_change < 0.30:
            logger.info(f"⏭ Пропускаємо: {market.question[:50]} | ціна {existing.entry_price:.3f}→{price_now:.3f} ({price_change:.0%} < 30%)")
            return None
        else:
            logger.info(f"Ціна змінилась {price_change:.0%} → переоткриваємо позицію")

    if edge_result.edge_direction == "BUY_YES":
        price = market.best_ask_yes  # платимо ASK за YES
    else:
        # BUY_NO: платимо ціну NO = 1 - YES bid
        # midpoint_yes надійніше ніж best_bid_yes (уникаємо 0.00)
        no_price = 1.0 - market.midpoint_yes
        if no_price < 0.01:
            logger.debug(f"NO price {no_price:.4f} < 0.01 — неліквідний ринок, пропускаємо")
            return None  # не торгуємо якщо NO майже недоступне
        price = no_price
    price = max(price, 0.001)  # абсолютний мінімум
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
            "token_id":   token_id,
            "price":      round(price, 4),
            "size":       round(size_usd, 2),
            "side":       "BUY",
            "order_type": "limit",    # limit order (не market)
            "post_only":  True,       # не брати ask одразу, чекати виконання
        })
        if order:
            order_id = order.get("orderID", order.get("id", ""))
            logger.info(f"✅ Order placed: {order_id} | {edge_result.edge_direction} {size_usd:.2f}")
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

# Кеш mark-to-market: TTL 90 секунд (не запитуємо щоцикл)
_mtm_price_cache: Dict[str, tuple] = {}   # {condition_id: (timestamp, price)}
MTM_CACHE_TTL = 90  # секунд


def _get_market_price_from_gamma(condition_id: str) -> Optional[float]:
    """
    Mark-to-market: поточна YES price через Gamma API.
    Повертає None якщо недоступно.
    """
    # Перевіряємо кеш
    cached = _mtm_price_cache.get(condition_id)
    if cached and time.time() - cached[0] < MTM_CACHE_TTL:
        return cached[1]

    try:
        r = requests.get(
            f"{config.GAMMA_URL}/markets",
            params={"conditionId": condition_id},
            timeout=6
        )
        if r.status_code != 200:
            return None
        data = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        if not markets:
            return None
        m = markets[0]
        import json
        prices_str = m.get("outcomePrices", None) or '["0.5","0.5"]'
        prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
        if len(prices) >= 2:
            # Gamma API може повертати [YES, NO] або [NO, YES]
            # YES price завжди менше для «невизначених» ринків (0.0-1.0)
            # Обираємо більшу ціну як YES (majority bet)
            p0, p1 = float(prices[0]), float(prices[1])
            # YES price = та що відповідає першому outcome (зазвичай prices[0])
            # Але для надійності: YES = ціна ближча до 0.5 або визначена через conditionId
            # Найпростіше: зберігаємо обидві і повертаємо prices[0] як YES
            yes_price = p0
            _mtm_price_cache[condition_id] = (time.time(), yes_price)
            return yes_price
        elif len(prices) == 1:
            price = float(prices[0])
            _mtm_price_cache[condition_id] = (time.time(), price)
            return price
    except Exception as e:
        logger.debug(f"Mark-to-market error {condition_id[:12]}: {e}")
    return None


def check_and_close_positions(clob_client) -> List[Position]:
    """
    Перевіряє кожну відкриту позицію:
      1. Чи ринок розв'язаний → PnL через resolve()
      2. Mark-to-market через Gamma API → оновлює pnl_pct
      3. Stop-loss якщо pnl_pct < -STOP_LOSS_PCT
    """
    closed = []
    for cid, pos in list(_active_positions.items()):

        # 1. Перевіряємо resolution
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

        # 2. Mark-to-market (Gamma API, не заглушка)
        current_price = _get_market_price_from_gamma(cid)
        if current_price is not None:
            if pos.direction == "BUY_NO":
                # NO position: прибуток коли YES падає
                effective_price = 1.0 - current_price
            else:
                effective_price = current_price
            pos.update_pnl(effective_price)

        # 3. Stop-loss
        if pos.pnl_pct <= -config.STOP_LOSS_PCT:
            logger.info(f"🔴 Stop-loss: {pos.direction} | PnL ${pos.pnl_usd:+.2f} ({pos.pnl_pct:+.1%})")
            closed.append(pos)
            del _active_positions[cid]

    return closed


def get_portfolio_summary() -> Dict:
    total_pnl = sum(p.pnl_usd for p in _active_positions.values())
    return {
        "active_positions": len(_active_positions),
        "total_pnl": total_pnl,
    }
