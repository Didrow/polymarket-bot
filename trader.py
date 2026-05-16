"""
trader.py — Polymarket Weather Bot 2026 (v15 Ultimate API Fix)

ВИПРАВЛЕНО:
  - Gamma API тепер опитується за `clobTokenIds` замість `condition_id`. 
  - Це на 100% усуває баг "API повертає невідповідний ринок" і дозволяє 
    бачити статуси "Очікуємо вердикту суддів".
"""

import math
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List
from dataclasses import dataclass

import config
from edge_calculator import EdgeResult
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)

_vol_cache: Dict[str, tuple] = {}
_active_positions: Dict[str, "Position"] = {}
_recently_closed: Dict[str, Any] = {}

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

    def resolve(self, resolved_yes: bool):
        if self.direction == "BUY_YES":
            payout = self.shares * 1.0 if resolved_yes else 0.0
        else:
            payout = self.shares * 1.0 if not resolved_yes else 0.0

        self.pnl_usd = payout - self.size_usd
        self.pnl_pct = self.pnl_usd / self.size_usd if self.size_usd > 0 else 0.0
        self.status = "RESOLVED_YES" if resolved_yes else "RESOLVED_NO"
        self.current_price = 1.0 if resolved_yes else 0.0


# ══════════════════════════════════════════════════════════════════
# RESOLUTION POLLING (Gamma API)
# ══════════════════════════════════════════════════════════════════

_resolution_cache: Dict[str, Optional[bool]] = {}
_resolution_attempt_count: Dict[str, int] = {}


def _parse_outcome_prices(outcome_prices) -> Optional[bool]:
    import json as _json
    try:
        if isinstance(outcome_prices, str):
            prices = _json.loads(outcome_prices)
        else:
            prices = outcome_prices
        if len(prices) >= 2:
            yes_p = float(prices[0])
            no_p  = float(prices[1])
            if yes_p >= 0.99 or yes_p == 1.0: return True
            if no_p >= 0.99 or no_p == 1.0: return False
    except Exception:
        pass
    return None


def check_market_resolved(condition_id: str, position: "Position" = None) -> Optional[bool]:
    if condition_id in _resolution_cache:
        return _resolution_cache[condition_id]

    _resolution_attempt_count[condition_id] = _resolution_attempt_count.get(condition_id, 0) + 1
    attempt = _resolution_attempt_count[condition_id]

    def _try_query() -> Optional[bool]:
        for closed_status in [None, "true"]:
            try:
                # МАГІЯ ТУТ: Використовуємо token_id замість проблемного condition_id
                params = {}
                if position and position.token_id:
                    params["clobTokenIds"] = position.token_id
                else:
                    params["condition_id"] = condition_id
                    
                if closed_status:
                    params["closed"] = closed_status

                r = requests.get(f"{config.GAMMA_URL}/markets", params=params, timeout=10)
                if r.status_code != 200:
                    continue

                data = r.json()
                markets = data if isinstance(data, list) else data.get("markets", [])

                if not markets:
                    continue

                m = markets[0]

                # Перевірка безпеки (спрацює і для токена, і для condition_id)
                api_tokens = str(m.get("clobTokenIds", "")).lower()
                api_cid = str(m.get("conditionId", "")).lower().replace("0x", "")
                target_id = str(condition_id).lower().replace("0x", "")
                
                # Якщо шукали за токеном, він точно має бути в результаті
                if position and position.token_id:
                    if position.token_id.lower() not in api_tokens:
                        continue
                else:
                    # Порівнюємо: чи один ID є префіксом іншого (короткий vs повний bytes32)
                    def _ids_match(a: str, b: str) -> bool:
                        return a == b or a.startswith(b) or b.startswith(a)
                    if not _ids_match(api_cid, target_id):
                        continue

                is_closed   = m.get("closed", False)
                is_resolved = m.get("resolved", False)
                outcome_prices = m.get("outcomePrices")
                winner_idx  = m.get("winnerIndex")

                if not is_closed and not is_resolved:
                    return None

                if is_closed and not is_resolved:
                    if attempt <= 2 or attempt % 15 == 0:
                        logger.info(f"⚖️ ОЧІКУВАННЯ: Торги закриті, чекаємо офіційних даних погоди | {position.question[:50]}")
                    return None

                tokens = m.get("tokens", [])
                if tokens:
                    for token in tokens:
                        if token.get("winner") is True:
                            outcome = token.get("outcome", "").strip().upper()
                            result = (outcome == "YES")
                            _resolution_cache[condition_id] = result
                            return result

                result = _parse_outcome_prices(outcome_prices)
                if result is not None:
                    _resolution_cache[condition_id] = result
                    return result

                if winner_idx is not None:
                    result = (int(winner_idx) == 0)
                    _resolution_cache[condition_id] = result
                    return result

            except Exception as e:
                pass
        return None

    result = _try_query()
    if result is not None:
        return result

    # Timeout failsafe для занадто старих позицій
    if position is not None:
        age_hours = (datetime.now(timezone.utc) - position.entry_time).total_seconds() / 3600
        if age_hours > 100:  
            if position.current_price <= 0.05:
                logger.info(f"⏰ TIMEOUT ({age_hours:.1f}h) → YES≈0 → force resolved=NO | {position.question[:50]}")
                _resolution_cache[condition_id] = False
                return False
            elif position.current_price >= 0.95:
                logger.info(f"⏰ TIMEOUT ({age_hours:.1f}h) → YES≈1 → force resolved=YES | {position.question[:50]}")
                _resolution_cache[condition_id] = True
                return True

    return None


# ══════════════════════════════════════════════════════════════════
# ПОЗИЦІЯ-РОЗМІР 
# ══════════════════════════════════════════════════════════════════

def _get_market_vol(token_id: str) -> float:
    cached = _vol_cache.get(token_id)
    if cached and time.time() - cached[0] < 1800:
        return cached[1]
    vol = 0.18
    _vol_cache[token_id] = (time.time(), vol)
    return vol


def decide_position_size(edge_result: EdgeResult, current_capital: float) -> float:
    market = edge_result.market
    direction = edge_result.edge_direction
    entry_price = market.best_ask_yes if direction == "BUY_YES" else (1 - market.best_bid_yes)
    token_id = market.token_yes_id if direction == "BUY_YES" else market.token_no_id

    vol = _get_market_vol(token_id)
    if vol > config.MAX_VOL_NO_TRADE:
        vol_size = config.BASE_POSITION_USD
    else:
        target_dv = current_capital * config.TARGET_PORTFOLIO_VOL
        vol_size = min(target_dv / vol, current_capital * config.MAX_POSITION_PCT)

    win_loss = (1 - entry_price) / max(entry_price, 0.001)
    kelly_raw = edge_result.edge / max(win_loss, 0.01)
    kelly_fraction = 0.08 if (edge_result.edge_direction == "BUY_NO" and edge_result.edge < 0.55) else (0.12 if edge_result.edge_direction == "BUY_NO" else 0.15)
    kelly_size = current_capital * kelly_raw * kelly_fraction
    kelly_size = max(config.MIN_POSITION_USD, min(kelly_size, config.MAX_POSITION_USD))

    final = min(vol_size, kelly_size)
    final = max(final, config.MIN_POSITION_USD)
    final = min(final, config.MAX_POSITION_USD)
    final = min(final, 4.0)

    if direction == "BUY_YES" and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES:
        final = min(final, config.EXTREME_TAIL_MAX_SIZE_USD)

    return round(final, 2)


# ══════════════════════════════════════════════════════════════════
# PLACE TRADE
# ══════════════════════════════════════════════════════════════════

def place_trade(edge_result: EdgeResult, current_capital: float, clob_client) -> Optional[Position]:
    market = edge_result.market

    if market.condition_id in _active_positions:
        return None

    if market.condition_id in _recently_closed:
        closed_at = _recently_closed[market.condition_id]
        hours_since = (datetime.now(timezone.utc) - closed_at).total_seconds() / 3600
        if hours_since < 24:
            return None
        else:
            del _recently_closed[market.condition_id]

    if edge_result.edge_direction == "BUY_YES":
        price = market.best_ask_yes
    else:
        no_price = 1.0 - market.midpoint_yes
        if no_price < 0.015:
            return None
        price = no_price
        
    price = max(price, 0.001)
    size_usd = decide_position_size(edge_result, current_capital)
    token_id = market.token_yes_id if edge_result.edge_direction == "BUY_YES" else market.token_no_id

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
        logger.info(f"🧪 DRY-RUN: place_trade {edge_result.edge_direction} | {market.question[:60]}")
        _active_positions[market.condition_id] = pos
        return pos

    if clob_client is None:
        logger.warning("CLOB клієнт недоступний")
        return None

    try:
        order = clob_client.create_and_post_order({
            "token_id":   token_id,
            "price":      round(price, 4),
            "size":       round(size_usd, 2),
            "side":       "BUY",
            "order_type": "FOK",
            "post_only":  False,
        })
        if order:
            order_id = order.get("orderID", order.get("id", ""))
            status = order.get("status", "")
            filled_pct = float(order.get("sizeMatched", 0)) / max(float(order.get("size", size_usd)), 0.001)
            if status in ("matched", "filled") or filled_pct >= 0.5:
                logger.info(f"✅ Угода виконана: {order_id} | filled={filled_pct:.0%} | {edge_result.edge_direction} {size_usd:.2f}")
                _active_positions[market.condition_id] = pos
                return pos
            else:
                logger.warning(f"⚠️ Ордер не виконався (status={status}): {order_id}")
                return None
    except Exception as e:
        logger.error(f"CLOB order error: {e}")

    return None


# ══════════════════════════════════════════════════════════════════
# ПЕРЕВІРКА ТА ЗАКРИТТЯ ПОЗИЦІЙ
# ══════════════════════════════════════════════════════════════════

_mtm_price_cache: Dict[str, tuple] = {}
MTM_CACHE_TTL = 90

def _get_market_price_from_gamma(condition_id: str, token_id: str = None) -> Optional[float]:
    cached = _mtm_price_cache.get(condition_id)
    if cached and time.time() - cached[0] < MTM_CACHE_TTL:
        return cached[1]

    try:
        params = {"clobTokenIds": token_id} if token_id else {"condition_id": condition_id}
        r = requests.get(f"{config.GAMMA_URL}/markets", params=params, timeout=6)
        
        if r.status_code != 200:
            return None
            
        data = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        
        if not markets:
            params["closed"] = "true"
            try:
                r2 = requests.get(f"{config.GAMMA_URL}/markets", params=params, timeout=6)
                if r2.status_code == 200:
                    data2 = r2.json()
                    markets = data2 if isinstance(data2, list) else data2.get("markets", [])
            except Exception:
                pass
            if not markets:
                return None
                
        m = markets[0]

        if m.get("closed", False) or m.get("resolved", False):
            return None

        _raw_ask = m.get("bestAsk")
        _raw_bid = m.get("bestBid")
        best_ask = float(_raw_ask) if _raw_ask is not None else 0.0
        best_bid = float(_raw_bid) if _raw_bid is not None else 0.0

        if best_ask == 0.0 or best_bid == 0.0:
            return None

        spread = best_ask - best_bid
        if spread > 0.25:
            return None

        yes_price = (best_ask + best_bid) / 2.0
        if yes_price > 0.99 or yes_price < 0.005:
            return None

        _mtm_price_cache[condition_id] = (time.time(), yes_price)
        return yes_price

    except Exception:
        pass
    return None


WEATHER_KEYWORDS = [
    "temperature", "celsius", "fahrenheit", "cold", "warm", "hot",
    "weather", "rain", "snow", "wind", "humidity", "degrees",
    "london", "paris", "tokyo", "new york", "chicago", "seoul",
    "busan", "buenos aires", "lucknow", "cape town", "nyc",
]

def cleanup_stale_positions() -> List[str]:
    removed = []
    now = datetime.now(timezone.utc)
    for cid, pos in list(_active_positions.items()):
        question_lower = pos.question.lower()
        is_weather = any(kw in question_lower for kw in WEATHER_KEYWORDS)
        age_hours = (now - pos.entry_time).total_seconds() / 3600

        if not is_weather:
            del _active_positions[cid]
            removed.append(cid)
            continue

        if age_hours > 120:
            del _active_positions[cid]
            removed.append(cid)
            continue
    return removed

def check_and_close_positions(clob_client) -> List[Position]:
    closed = []
    for cid, pos in list(_active_positions.items()):
        resolved = check_market_resolved(cid, position=pos)
        
        if resolved is not None:
            pos.resolve(resolved)
            result = "✅ WIN" if pos.pnl_usd > 0 else "❌ LOSS"
            logger.info(f"{result}: {pos.direction} | PnL ${pos.pnl_usd:+.2f} ({pos.pnl_pct:+.1%}) | {pos.question[:50]}")
            closed.append(pos)
            del _active_positions[cid]
            _recently_closed[cid] = datetime.now(timezone.utc)
            continue

        current_price = _get_market_price_from_gamma(cid, pos.token_id)
        if current_price is not None:
            if pos.direction == "BUY_NO":
                effective_price = 1.0 - current_price
            else:
                effective_price = current_price
            pos.update_pnl(effective_price)

        is_yes_tail = pos.direction == "BUY_YES" and pos.entry_price < 0.10
        is_no_tail  = pos.direction == "BUY_NO"  and pos.entry_price > 0.90
        if is_yes_tail or is_no_tail:
            continue

        if pos.pnl_pct <= -config.STOP_LOSS_PCT:
            logger.info(f"🔴 Stop-loss ({config.STOP_LOSS_PCT:.0%}): {pos.direction} | PnL ${pos.pnl_usd:+.2f} ({pos.pnl_pct:+.1%}) | entry={pos.entry_price:.3f}")
            closed.append(pos)
            del _active_positions[cid]
            _recently_closed[cid] = datetime.now(timezone.utc)

    return closed


def get_portfolio_summary() -> Dict:
    total_pnl = sum(p.pnl_usd for p in _active_positions.values())
    return {
        "active_positions": len(_active_positions),
        "total_pnl": total_pnl,
    }


def get_active_positions() -> Dict:
    return _active_positions
