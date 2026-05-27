"""
trader.py — Polymarket Weather Bot 2026 (FIXED RESOLUTION)
"""

import math
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
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
    end_date: Optional[datetime] = None

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


# ------------------------------------------------------------------
#  ПОКРАЩЕНА ФУНКЦІЯ ПЕРЕВІРКИ RESOLUTION
# ------------------------------------------------------------------

def _check_resolution_by_clob(position: "Position") -> Optional[bool]:
    """Резервний метод через CLOB midpoint (тільки для закритих ринків)."""
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
    except Exception as e:
        logger.debug(f"CLOB fallback error: {e}")
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
            no_p = float(prices[1])
            if yes_p >= 0.99 or yes_p == 1.0:
                return True
            if no_p >= 0.99 or no_p == 1.0:
                return False
    except Exception:
        pass
    return None


def _get_market_from_gamma(condition_id: str) -> Optional[Dict]:
    clean_id = condition_id.lower()
    try:
        r = requests.get(f"{config.GAMMA_URL}/markets", params={"conditionId": clean_id}, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        if isinstance(data, list):
            markets = data
        elif isinstance(data, dict) and "markets" in data:
            markets = data["markets"]
        else:
            markets = [data] if isinstance(data, dict) else []
        for m in markets:
            m_cid = normalize_condition_id(m.get("conditionId", ""))
            m_id = normalize_condition_id(m.get("id", ""))
            if m_cid == clean_id or m_id == clean_id:
                return m
    except Exception as e:
        logger.debug(f"Gamma query error: {e}")
    return None


_resolution_cache: Dict[str, Optional[bool]] = {}

def check_market_resolved(condition_id: str, position: Optional["Position"] = None) -> Optional[bool]:
    """
    Визначає, чи вирішився ринок, і який результат (True = YES, False = NO).
    Повертає None, якщо ринок ще не закритий.
    """
    clean_id = normalize_condition_id(condition_id)
    if clean_id in _resolution_cache:
        return _resolution_cache[clean_id]

    # DRY-RUN fast path: якщо є end_date і він минув
    if config.DRY_RUN and position and position.end_date:
        now = datetime.now(timezone.utc)
        if now > position.end_date:
            from data_fetcher import get_best_forecast
            forecast = get_best_forecast(position.city, hours_to_resolution=0)
            if forecast:
                is_low = 'lowest' in position.question.lower()
                tc = forecast.temp_low_c if is_low else forecast.temp_high_c
                from edge_calculator import _parse_range_or_threshold
                kind, val_min, val_max, unit = _parse_range_or_threshold(position.question)
                
                resolved_yes = None
                if kind == "range":
                    if unit == 'F':
                        tc = tc * 9/5 + 32
                    resolved_yes = (val_min - 0.5) <= tc < (val_max + 0.5)
                elif val_min is not None:
                    threshold_c = val_min
                    if unit == 'F':
                        from edge_calculator import _f_to_c
                        threshold_c = _f_to_c(threshold_c)
                        
                    if kind == "above":
                        resolved_yes = tc > threshold_c
                    elif kind == "below":
                        resolved_yes = tc <= threshold_c
                    else:
                        half_width = 0.2778 if unit == 'F' else 0.5
                        resolved_yes = (threshold_c - half_width) <= tc < (threshold_c + half_width)
                        
                if resolved_yes is not None:
                    _resolution_cache[clean_id] = resolved_yes
                    logger.warning(f"🧪 DRY-RUN Simulated resolution via forecast: {resolved_yes} for {clean_id[:20]}")
                    return resolved_yes

    # 1. Отримуємо ринок з Gamma API
    market_data = _get_market_from_gamma(clean_id)
    if not market_data:
        logger.debug(f"Market not found in Gamma API: {clean_id[:20]}")
        return None

    # Перевіряємо статус
    is_closed = market_data.get("closed", False)
    is_resolved = market_data.get("resolved", False)
    end_date_str = market_data.get("endDate", "")
    end_date = None
    try:
        if end_date_str:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
    except:
        pass

    if not is_closed and not is_resolved:
        logger.debug(f"Market {clean_id[:20]} is still OPEN (end_date={end_date})")
        return None

    # Спробуємо визначити переможця з токенів
    tokens = market_data.get("tokens", [])
    for token in tokens:
        if token.get("winner") is True:
            outcome = token.get("outcome", "").upper()
            result = (outcome == "YES")
            _resolution_cache[clean_id] = result
            logger.info(f"✅ Resolution via winner token: {result} for {clean_id[:20]}")
            return result

    # Спробуємо через outcomePrices
    outcome_prices = market_data.get("outcomePrices")
    result = _parse_outcome_prices(outcome_prices)
    if result is not None:
        _resolution_cache[clean_id] = result
        logger.info(f"✅ Resolution via outcomePrices: {result} for {clean_id[:20]}")
        return result

    # Якщо ринок закритий, але переможця немає – використовуємо CLOB fallback
    if is_closed or is_resolved:
        if position:
            result = _check_resolution_by_clob(position)
            if result is not None:
                _resolution_cache[clean_id] = result
                logger.info(f"✅ Resolution via CLOB fallback (closed market): {result} for {clean_id[:20]}")
                return result
        # Якщо fallback не допоміг, але ринок закритий давно (end_date > 2h тому) – використовуємо поточну ціну
        if end_date and datetime.now(timezone.utc) > end_date + timedelta(hours=2):
            token_id = position.token_id if position else None
            if token_id:
                try:
                    r = requests.get(f"{config.CLOB_URL}/midpoint", params={"token_id": token_id}, timeout=5)
                    if r.status_code == 200:
                        mid = float(r.json().get("mid", 0.5))
                        result = (mid >= 0.99)  # YES переміг, якщо ціна ~1
                        _resolution_cache[clean_id] = result
                        logger.warning(f"⚠️ Forced resolution by expired end_date + price: {result} for {clean_id[:20]}")
                        return result
                except:
                    pass
            logger.warning(f"Market {clean_id[:20]} closed but no winner determined even after 2h, skipping")
            return None
    return None


# ------------------------------------------------------------------
#  ЛОГІКА РОЗМІРУ ПОЗИЦІЇ (COMPOUND + KELLY)
# ------------------------------------------------------------------

def _get_market_vol(token_id: str) -> float:
    return 0.18

def decide_position_size(edge_result: EdgeResult, current_capital: float) -> float:
    market = edge_result.market
    direction = edge_result.edge_direction
    entry_price = market.best_ask_yes if direction == "BUY_YES" else (1 - market.best_bid_yes)

    vol = _get_market_vol("")
    target_dv = current_capital * config.TARGET_PORTFOLIO_VOL
    vol_size = min(target_dv / vol, current_capital * config.MAX_POSITION_PCT)

    if config.ENABLE_COMPOUND:
        if config.USE_KELLY:
            p = edge_result.estimated_prob
            q = 1.0 - p
            b = (1.0 - max(entry_price, 0.001)) / max(entry_price, 0.001) if entry_price > 0 else 0
            kelly_raw = max(0, (p * b - q) / b) if b > 0 else 0
            kelly_size = current_capital * kelly_raw * 0.25
            final = min(vol_size, kelly_size)
        else:
            fixed_pct_size = current_capital * config.COMPOUND_RISK_PCT
            final = min(vol_size, fixed_pct_size)
    else:
        p = edge_result.estimated_prob
        q = 1.0 - p
        b = (1.0 - max(entry_price, 0.001)) / max(entry_price, 0.001) if entry_price > 0 else 0
        kelly_raw = max(0, (p * b - q) / b) if b > 0 else 0
        kelly_size = current_capital * kelly_raw * 0.25
        final = min(vol_size, kelly_size)
        final = max(config.MIN_POSITION_USD, min(final, config.MAX_POSITION_USD, 4.0))

    if direction == "BUY_YES" and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES:
        final = min(final, config.EXTREME_TAIL_MAX_SIZE_USD)

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
        if no_price < 0.015:
            return None
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
        end_date=market.end_date,
    )

    if config.DRY_RUN:
        logger.info(f"🧪 DRY-RUN: Відкрито {edge_result.edge_direction} | {market.question[:60]} | size=${size_usd:.2f} (cap=${current_capital:.2f})")
        _active_positions[clean_cid] = pos
        return pos

    if not clob_client:
        return None
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

    market_data = _get_market_from_gamma(clean_id)
    if not market_data:
        return None
    if market_data.get("closed", False) or market_data.get("resolved", False):
        return None

    best_ask = float(market_data.get("bestAsk") or 0.0)
    best_bid = float(market_data.get("bestBid") or 0.0)
    last_trade = float(market_data.get("lastTradePrice") or 0.0)

    if best_ask == 0.0 and best_bid == 0.0:
        if last_trade > 0.0:
            yes_price = last_trade
        else:
            return None
    else:
        if best_ask == 0.0:
            best_ask = 1.0
        if best_bid == 0.0:
            yes_price = best_ask / 2.0
        else:
            yes_price = (best_ask + best_bid) / 2.0

    _mtm_price_cache[clean_id] = (time.time(), yes_price)
    return yes_price


WEATHER_KEYWORDS = ["temperature", "weather", "rain", "snow", "degrees", "london", "paris", "tokyo", "nyc", "chicago", "seoul", "busan", "lucknow", "cape town", "miami", "dallas", "seattle", "berlin", "sydney", "sao paulo", "munich"]


def cleanup_stale_positions() -> List[Position]:
    removed: List[Position] = []
    now = datetime.now(timezone.utc)
    for cid, pos in list(_active_positions.items()):
        is_weather = any(kw in pos.question.lower() for kw in WEATHER_KEYWORDS)
        
        market_ended = (
            (pos.end_date and now > pos.end_date + timedelta(hours=2))
        )
        age_hours = (now - pos.entry_time).total_seconds() / 3600

        if not is_weather or market_ended or age_hours > 36:
            logger.info(f"🧹 Прибираємо застарілий ринок: {pos.question[:50]}")
            resolved = check_market_resolved(cid, position=pos)
            if resolved is not None:
                pos.resolve(resolved)
                logger.info(f"{'✅ WIN' if pos.pnl_usd > 0 else '❌ LOSS'} (при cleanup): {pos.question[:50]} | PnL ${pos.pnl_usd:+.2f}")
            del _active_positions[cid]
            removed.append(pos)
    return removed


def check_and_close_positions(clob_client) -> List[Position]:
    closed = []
    now = datetime.now(timezone.utc)
    for cid, pos in list(_active_positions.items()):
        age_hours = (now - pos.entry_time).total_seconds() / 3600

        resolved = check_market_resolved(cid, position=pos)

        if resolved is None and pos.token_id:
            market_ended = (
                (pos.end_date and now > pos.end_date)
            )
            if market_ended:
                resolved = _check_resolution_by_clob(pos)
                if resolved is not None:
                    logger.info(
                        f"🔍 Resolution via direct CLOB (Gamma miss, market_ended)"
                    )

        if resolved is not None:
            pos.resolve(resolved)
            logger.info(f"{'✅ WIN' if pos.pnl_usd > 0 else '❌ LOSS'}: {pos.question[:50]} | PnL ${pos.pnl_usd:+.2f}")
            closed.append(pos)
            del _active_positions[cid]
            _recently_closed[cid] = now
            continue

        if pos.token_id and pos.end_date and now > pos.end_date + timedelta(hours=24):
            resolved_yes = None
            try:
                r = requests.get(
                    f"{config.CLOB_URL}/midpoint",
                    params={"token_id": pos.token_id},
                    timeout=5,
                )
                if r.status_code == 200:
                    mid = float(r.json().get("mid", 0.5))
                    if mid >= 0.95:
                        resolved_yes = True if pos.direction == "BUY_YES" else False
                    elif mid <= 0.05:
                        resolved_yes = False if pos.direction == "BUY_YES" else True
                    else:
                        pos.update_pnl(mid)
            except Exception:
                pass
            if resolved_yes is not None:
                pos.resolve(resolved_yes)
            else:
                pos.status = "EXPIRED"
                if config.DRY_RUN:
                    pos.pnl_usd = -pos.size_usd
                    pos.pnl_pct = -1.0
            logger.warning(
                f"⏰ Force-close >24h after end_date: {pos.question[:50]} | {pos.status} | PnL ${pos.pnl_usd:+.2f}"
            )
            closed.append(pos)
            del _active_positions[cid]
            _recently_closed[cid] = now
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

def startup_cleanup() -> List[Position]:
    """Примусово перевіряє статус кожної позиції при старті бота"""
    closed = []
    now = datetime.now(timezone.utc)
    for cid, pos in list(_active_positions.items()):
        logger.info(f"🔄 Перевірка відновленої позиції: {pos.question[:50]}")
        resolved = check_market_resolved(cid, position=pos)
        if resolved is not None:
            pos.resolve(resolved)
            logger.info(f"{'✅ WIN' if pos.pnl_usd > 0 else '❌ LOSS'}: {pos.question[:50]} | PnL ${pos.pnl_usd:+.2f}")
            closed.append(pos)
            del _active_positions[cid]
            _recently_closed[cid] = now
            continue
            
        price = _get_market_price_from_gamma(cid)
        if price:
            pos.update_pnl(1.0 - price if pos.direction == "BUY_NO" else price)
    return closed
