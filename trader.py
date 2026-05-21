"""
trader.py — Polymarket Weather Bot 2026 (CLEAN + COMPOUND + KELLY)
"""

import math
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field

import config
from edge_calculator import EdgeResult
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)

_vol_cache: Dict[str, tuple] = {}
_active_positions: Dict[str, "Position"] = {}
_recently_closed: Dict[str, Any] = {}

def normalize_condition_id(cid: str) -> str:
    if not cid:
        return ""
    cid = str(cid).strip().lower()
    if cid.startswith("0x"):
        cid = cid[2:]
    return "0x" + cid.zfill(64)

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
        if self.entry_price > 0 and self.shares > 0:
            self.pnl_usd = round(self.shares * current_price - self.size_usd, 4)
            self.pnl_pct = self.pnl_usd / self.size_usd

    def resolve(self, resolved_yes: bool):
        if self.direction == "BUY_YES":
            payout = self.shares * 1.0 if resolved_yes else 0.0
        else:
            payout = self.shares * 1.0 if not resolved_yes else 0.0

        self.pnl_usd = payout - self.size_usd
        self.pnl_pct = self.pnl_usd / self.size_usd if self.size_usd > 0 else 0.0
        self.status = "RESOLVED_YES" if resolved_yes else "RESOLVED_NO"
        self.current_price = 1.0 if resolved_yes else 0.0

_resolution_cache: Dict[str, Optional[bool]] = {}
_resolution_attempt_count: Dict[str, int] = {}


def _check_resolution_by_clob(position: "Position") -> Optional[bool]:
    if not position or not position.token_id:
        return None
    try:
        r = requests.get(
            f"{config.CLOB_URL}/midpoint",
            params={"token_id": position.token_id},
            timeout=6,
        )
        if r.status_code == 200:
            mid = float(r.json().get("mid", 0.5))
            if mid >= 0.98:
                return True if position.direction == "BUY_YES" else False
            if mid <= 0.02:
                return False if position.direction == "BUY_YES" else True
    except Exception:
        pass
    return None

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

def _try_query(target_hex: str):
    try:
        params = {"condition_id": target_hex}
        r = requests.get(f"{config.GAMMA_URL}/markets", params=params, timeout=10)
        if r.status_code == 200:
            markets = r.json()
            if isinstance(markets, dict) and "markets" in markets:
                markets = markets["markets"]
            elif isinstance(markets, dict):
                markets = [markets]
            
            if isinstance(markets, list) and markets:
                for m in markets:
                    m_cid = normalize_condition_id(m.get("conditionId", ""))
                    if m_cid == target_hex:
                        is_closed   = m.get("closed", False)
                        is_resolved = m.get("resolved", False)

                        if not is_closed and not is_resolved:
                            return "OPEN", None

                        tokens = m.get("tokens", [])
                        if tokens:
                            for t in tokens:
                                winner_val = t.get("winner")
                                if winner_val is True or str(winner_val).strip().lower() == "true":
                                    res = (t.get("outcome", "").strip().upper() == "YES")
                                    return "RESOLVED", res

                        res = _parse_outcome_prices(m.get("outcomePrices"))
                        if res is not None:
                            return "RESOLVED", res

                        return "CLOSED", None
    except Exception as e:
        logger.debug(f"API query exception for {target_hex[:15]}: {e}")
        
    return "UNKNOWN", None

def check_market_resolved(condition_id: str, position: "Position" = None) -> Optional[bool]:
    clean_id = normalize_condition_id(condition_id)
    if clean_id in _resolution_cache:
        return _resolution_cache[clean_id]

    _resolution_attempt_count[clean_id] = _resolution_attempt_count.get(clean_id, 0) + 1

    status, result = _try_query(clean_id)

    if status in ["OPEN", "UNKNOWN"]:
        return None

    if result is None and position is not None and status == "CLOSED":
        result = _check_resolution_by_clob(position)
        if result is not None:
            _resolution_cache[clean_id] = result
            logger.debug(f"Resolution via CLOB fallback: {result} for {condition_id[:20]}")

    if result is not None:
        _resolution_cache[clean_id] = result

    return result

def _get_market_vol(token_id: str) -> float:
    return 0.18  

# ══════════════════════════════════════════════════════════════════
# НОВА ЛОГІКА ВИЗНАЧЕННЯ РОЗМІРУ СТАВКИ (COMPOUND + KELLY)
# ══════════════════════════════════════════════════════════════════

def decide_position_size(edge_result: EdgeResult, current_capital: float) -> float:
    market = edge_result.market
    direction = edge_result.edge_direction
    entry_price = market.best_ask_yes if direction == "BUY_YES" else (1 - market.best_bid_yes)
    
    # 1. Volatility targeting (базова оцінка)
    vol = _get_market_vol("")
    target_dv = current_capital * config.TARGET_PORTFOLIO_VOL
    vol_size = min(target_dv / vol, current_capital * config.MAX_POSITION_PCT)

    # 2. Якщо використовуємо компаундинг
    if config.ENABLE_COMPOUND:
        if config.USE_KELLY:
            # Kelly для binary: f = (p*b - q) / b
            p = edge_result.estimated_prob
            q = 1.0 - p
            b = (1.0 - max(entry_price, 0.001)) / max(entry_price, 0.001) if entry_price > 0 else 0
            kelly_raw = max(0, (p * b - q) / b) if b > 0 else 0
            kelly_size = current_capital * kelly_raw * 0.25  # Quarter-Kelly
            final = min(vol_size, kelly_size)
        else:
            # Фіксований відсоток від капіталу
            fixed_pct_size = current_capital * config.COMPOUND_RISK_PCT
            final = min(vol_size, fixed_pct_size)
    else:
        # Стара логіка (без компаундингу)
        p = edge_result.estimated_prob
        q = 1.0 - p
        b = (1.0 - max(entry_price, 0.001)) / max(entry_price, 0.001) if entry_price > 0 else 0
        kelly_raw = max(0, (p * b - q) / b) if b > 0 else 0
        kelly_size = current_capital * kelly_raw * 0.25
        final = min(vol_size, kelly_size)
        final = max(config.MIN_POSITION_USD, min(final, config.MAX_POSITION_USD, 4.0))

    # Обмеження для Grid YES (дешеві контракти)
    if direction == "BUY_YES" and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES:
        final = min(final, config.EXTREME_TAIL_MAX_SIZE_USD)

    # Абсолютні межі
    final = max(config.MIN_POSITION_USD, min(final, config.MAX_POSITION_USD, current_capital * 0.1))

    return round(final, 2)


def place_trade(edge_result: EdgeResult, current_capital: float, clob_client) -> Optional[Position]:
    market = edge_result.market
    clean_cid = normalize_condition_id(market.condition_id)

    if clean_cid in _active_positions:
        return None

    if clean_cid in _recently_closed:
        closed_at = _recently_closed[clean_cid]
        if isinstance(closed_at, datetime):
            age_hours = (datetime.now(timezone.utc) - closed_at).total_seconds() / 3600
            if age_hours < 12.0:
                return None  

    if edge_result.edge_direction == "BUY_YES":
        price = market.best_ask_yes
    else:
        no_price = 1.0 - market.midpoint_yes
        if no_price < 0.015: return None
        price = no_price
        
    price = max(price, 0.001)
    size_usd = decide_position_size(edge_result, current_capital)
    token_id = market.token_yes_id if edge_result.edge_direction == "BUY_YES" else market.token_no_id

    pos = Position(
        condition_id=clean_cid,
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
        logger.info(f"🧪 DRY-RUN: Відкрито {edge_result.edge_direction} | {market.question[:60]} | size=${size_usd:.2f} (cap=${current_capital:.2f})")
        _active_positions[clean_cid] = pos
        return pos

    if not clob_client: return None
    try:
        order = clob_client.create_and_post_order({
            "token_id": token_id,
            "price": round(price, 4),
            "size": round(size_usd, 2),
            "side": "BUY",
            "order_type": "FOK",
        })
        if order:
            _active_positions[clean_cid] = pos
            return pos
    except Exception as e:
        logger.error(f"Trade Error: {e}")
    return None

_mtm_price_cache: Dict[str, tuple] = {}
MTM_CACHE_TTL = 90  

def _get_market_price_from_gamma(condition_id: str) -> Optional[float]:
    clean_id = normalize_condition_id(condition_id)
    cached = _mtm_price_cache.get(clean_id)
    if cached and time.time() - cached[0] < MTM_CACHE_TTL:
        return cached[1]

    try:
        r = requests.get(f"{config.GAMMA_URL}/markets", params={"conditionId": clean_id}, timeout=6)
        if r.status_code != 200: return None
        data = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        if not markets: return None

        m = markets[0]
        if m.get("closed", False) or m.get("resolved", False): return None

        best_ask = float(m.get("bestAsk") or 0.0)
        best_bid = float(m.get("bestBid") or 0.0)

        if best_ask == 0.0 or best_bid == 0.0: return None
        yes_price = (best_ask + best_bid) / 2.0
        
        _mtm_price_cache[clean_id] = (time.time(), yes_price)
        return yes_price
    except Exception:
        pass
    return None

WEATHER_KEYWORDS = ["temperature", "weather", "rain", "snow", "degrees", "london", "paris", "tokyo", "nyc", "chicago", "seoul", "busan", "lucknow", "cape town", "miami", "dallas", "seattle", "berlin", "sydney", "sao paulo", "munich"]

def cleanup_stale_positions() -> List[Position]:
    removed: List[Position] = []
    now = datetime.now(timezone.utc)
    for cid, pos in list(_active_positions.items()):
        is_weather = any(kw in pos.question.lower() for kw in WEATHER_KEYWORDS)
        age_hours = (now - pos.entry_time).total_seconds() / 3600

        if not is_weather or age_hours > 120:
            logger.info(f"🧹 Прибираємо застарілий ринок: {pos.question[:50]}")
            resolved = check_market_resolved(cid, position=pos)
            if resolved is not None:
                pos.resolve(resolved)
                logger.info(
                    f"{'✅ WIN' if pos.pnl_usd > 0 else '❌ LOSS'} (при cleanup): "
                    f"{pos.question[:50]} | PnL ${pos.pnl_usd:+.2f}"
                )
            del _active_positions[cid]
            removed.append(pos)

    return removed

def check_and_close_positions(clob_client) -> List[Position]:
    closed = []
    for cid, pos in list(_active_positions.items()):
        resolved = check_market_resolved(cid, position=pos)
        if resolved is not None:
            pos.resolve(resolved)
            logger.info(f"{'✅ WIN' if pos.pnl_usd > 0 else '❌ LOSS'}: {pos.question[:50]} | PnL ${pos.pnl_usd:+.2f}")
            closed.append(pos)
            del _active_positions[cid]
            _recently_closed[cid] = datetime.now(timezone.utc)
            continue

        price = _get_market_price_from_gamma(cid)
        if price:
            pos.update_pnl(1.0 - price if pos.direction == "BUY_NO" else price)

        if pos.pnl_pct <= -config.STOP_LOSS_PCT and pos.entry_price > 0.15:
            logger.info(f"🔴 Stop-loss: {pos.question[:50]}")
            closed.append(pos)
            del _active_positions[cid]

    return closed

def get_portfolio_summary() -> Dict:
    return {"active_positions": len(_active_positions), "total_pnl": sum(p.pnl_usd for p in _active_positions.values())}

def get_active_positions() -> Dict:
    return _active_positions
