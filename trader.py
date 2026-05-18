"""
trader.py — Polymarket Weather Bot 2026 (v9 production)

ВИПРАВЛЕНО:
  - PnL tracking через Gamma API polling (resolved markets)
  - Hourly limit підвищено до 50/год
  - Дедуплікація: не відкриваємо угоду на той самий condition_id двічі
  - DRY_RUN статистика: pnl_usd рахується після resolution
  - Збільшено таймаут для погодних ринків (до 100 годин)
  - Розширено допустимий спред для відображення Unrealized PnL (до 25%)
  - Виправлено баг зависання: пошук правильного ринку у відповіді API
"""

import math
import time
import logging
import requests
import numpy as np
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field

import config
from edge_calculator import EdgeResult
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)

_vol_cache: Dict[str, tuple] = {}
_active_positions: Dict[str, "Position"] = {}
# Умови щойно закриті — блокуємо повторне відкриття на 24h
# {condition_id: datetime_closed}
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
    status: str = "OPEN"    # OPEN | RESOLVED_YES | RESOLVED_NO | CLOSED

    def update_pnl(self, current_price: float):
        self.current_price = current_price
        if self.entry_price > 0:
            # MTM вже захищений в _get_market_price_from_gamma (bestAsk/bestBid)
            # Tail YES може дати +900% — це не помилка, це прибуток
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
_resolution_attempt_count: Dict[str, int] = {}


def _parse_outcome_prices(outcome_prices) -> Optional[bool]:
    """Витягує resolved_yes з outcomePrices різних форматів."""
    import json as _json
    try:
        if isinstance(outcome_prices, str):
            prices = _json.loads(outcome_prices)
        else:
            prices = outcome_prices
        if len(prices) >= 2:
            yes_p = float(prices[0])
            no_p  = float(prices[1])
            if yes_p >= 0.99:
                return True
            if no_p >= 0.99:
                return False
            # Іноді значення 1 / 0 замість 0.99/0.01
            if yes_p == 1.0:
                return True
            if no_p == 1.0:
                return False
    except Exception:
        pass
    return None


def check_market_resolved(condition_id: str, position: "Position" = None) -> Optional[bool]:
    if condition_id.startswith("0x") and len(condition_id) < 66:
        condition_id = "0x" + condition_id[2:].zfill(64)

    if condition_id in _resolution_cache:
        return _resolution_cache[condition_id]

    _resolution_attempt_count[condition_id] = _resolution_attempt_count.get(condition_id, 0) + 1
    attempt = _resolution_attempt_count[condition_id]

    def _try_query(param_name: str, param_val: str) -> Optional[bool]:
        for closed_status in [None, "true", "false"]:
            try:
                # Gamma API краще розуміє condition_id у snake_case
                api_param = "condition_id" if param_name == "conditionId" else param_name
                params = {api_param: param_val}
                if closed_status:
                    params["closed"] = closed_status

                r = requests.get(f"{config.GAMMA_URL}/markets", params=params, timeout=10)
                if r.status_code != 200:
                    continue

                data = r.json()
                markets = data if isinstance(data, list) else data.get("markets", [])

                if not markets:
                    continue

                target_id = str(param_val).lower().replace("0x", "")

                # ШУКАЄМО НАШ РИНОК У ВСЬОМУ МАСИВІ (API часто повертає сміття першим)
                found_m = None
                for m in markets:
                    api_cid   = str(m.get("conditionId", "")).lower().replace("0x", "")
                    api_id    = str(m.get("id", "")).lower().replace("0x", "")
                    if api_cid == target_id or api_id == target_id or api_cid.startswith(target_id) or api_id.startswith(target_id):
                        found_m = m
                        break

                if not found_m:
                    if closed_status == "true" and (attempt <= 2 or attempt % 20 == 0):
                        logger.info(f"⚠️ API не знайшло ринок {target_id[:16]} у відповіді.")
                    continue

                m = found_m
                is_closed   = m.get("closed", False)
                is_resolved = m.get("resolved", False)
                outcome_prices = m.get("outcomePrices")
                winner_idx  = m.get("winnerIndex")

                if not is_closed and not is_resolved:
                    if attempt <= 2 or attempt % 10 == 0:
                        logger.info(f"⏳ Resolution: ринок ще активний | cid={target_id[:8]}")
                    return None

                tokens = m.get("tokens", [])
                if tokens:
                    for token in tokens:
                        if token.get("winner") is True:
                            result = (token.get("outcome", "").strip().upper() == "YES")
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
                logger.debug(f"Gamma API error під час резолюції: {e}")
        return None

    result = _try_query("conditionId", condition_id)
    if result is not None: return result

    if not condition_id.startswith("0x"):
        result = _try_query("id", condition_id)
        if result is not None: return result

    if position is not None:
        age_hours = (datetime.now(timezone.utc) - position.entry_time).total_seconds() / 3600
        if age_hours > 100:  # Timeout збільшено до 100 годин
            if position.current_price <= 0.05:
                logger.info(f"⏰ TIMEOUT → YES≈0 → resolved=NO | {position.question[:50]}")
                _resolution_cache[condition_id] = False
                return False
            elif position.current_price >= 0.95:
                logger.info(f"⏰ TIMEOUT → YES≈1 → resolved=YES | {position.question[:50]}")
                _resolution_cache[condition_id] = True
                return True
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
    kelly_fraction = 0.08 if (edge_result.edge_direction == "BUY_NO" and edge_result.edge < 0.55) else (0.12 if edge_result.edge_direction == "BUY_NO" else 0.15)
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
    # Проста надійна дедуплікація: один ринок = одна позиція.
    # Якщо ціна змінилась — resolution polling сам закриє позицію з правильним PnL.
    if market.condition_id in _active_positions:
        logger.debug(f"⏭ Skip: позиція вже відкрита | {market.question[:50]}")
        return None

    # v28-fix: блокуємо повторне відкриття ринку що щойно закрився (24h cooldown)
    if market.condition_id in _recently_closed:
        closed_at = _recently_closed[market.condition_id]
        hours_since = (datetime.now(timezone.utc) - closed_at).total_seconds() / 3600
        if hours_since < 24:
            logger.debug(f"⏭ Skip: ринок закрився {hours_since:.1f}h тому, cooldown | {market.question[:50]}")
            return None
        else:
            del _recently_closed[market.condition_id]  # cooldown минув

    if edge_result.edge_direction == "BUY_YES":
        price = market.best_ask_yes  # платимо ASK за YES
    else:
        # BUY_NO: платимо ціну NO = 1 - YES bid
        # midpoint_yes надійніше ніж best_bid_yes (уникаємо 0.00)
        no_price = 1.0 - market.midpoint_yes
        if no_price < 0.015:  # market=1.00 → NO price = 0 → skip
            logger.debug(f"NO price {no_price:.4f} < 0.015 — market майже вирішений, пропускаємо")
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
            "order_type": "FOK",      # v27: Fill-or-Kill — виконати повністю або скасувати
            "post_only":  False,      # v27: False — дозволяємо взяти зустрічний ордер одразу
        })
        if order:
            order_id = order.get("orderID", order.get("id", ""))
            # Перевіряємо реальне виконання — не додаємо позицію за невиконаний ордер
            status     = order.get("status", "")
            filled_pct = float(order.get("sizeMatched", 0)) / max(float(order.get("size", size_usd)), 0.001)
            if status in ("matched", "filled") or filled_pct >= 0.5:
                logger.info(f"✅ Угода виконана: {order_id} | filled={filled_pct:.0%} | {edge_result.edge_direction} {size_usd:.2f}")
                _active_positions[market.condition_id] = pos
                return pos
            else:
                logger.warning(f"⚠️ Ордер не виконався (status={status}, filled={filled_pct:.0%}): {order_id}")
                return None
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
    cached = _mtm_price_cache.get(condition_id)
    if cached and time.time() - cached[0] < MTM_CACHE_TTL:
        return cached[1]

    try:
        r = requests.get(f"{config.GAMMA_URL}/markets", params={"condition_id": condition_id}, timeout=6)
        if r.status_code != 200: return None
        data = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        
        if not markets: return None

        target_id = str(condition_id).lower().replace("0x", "")
        
        # ПРАВИЛЬНИЙ ПОШУК РИНКУ В СТАКАНІ
        found_m = None
        for m in markets:
            api_cid   = str(m.get("conditionId", "")).lower().replace("0x", "")
            api_id    = str(m.get("id", "")).lower().replace("0x", "")
            if api_cid == target_id or api_id == target_id or api_cid.startswith(target_id) or api_id.startswith(target_id):
                found_m = m
                break

        if not found_m: return None
        m = found_m

        if m.get("closed", False) or m.get("resolved", False):
            return None

        best_ask = float(m.get("bestAsk") if m.get("bestAsk") is not None else 0.0)
        best_bid = float(m.get("bestBid") if m.get("bestBid") is not None else 0.0)

        if best_ask == 0.0 or best_bid == 0.0: return None
        if (best_ask - best_bid) > 0.25: return None

        yes_price = (best_ask + best_bid) / 2.0
        if yes_price > 0.99 or yes_price < 0.005: return None

        _mtm_price_cache[condition_id] = (time.time(), yes_price)
        return yes_price

    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════
# CLEANUP: очищення фіктивних/застарілих позицій при старті
# ══════════════════════════════════════════════════════════════════

WEATHER_KEYWORDS =[
    "temperature", "celsius", "fahrenheit", "cold", "warm", "hot",
    "weather", "rain", "snow", "wind", "humidity", "degrees",
    "london", "paris", "tokyo", "new york", "chicago", "seoul",
    "busan", "buenos aires", "lucknow", "cape town", "nyc",
]

def cleanup_stale_positions() -> List[Position]:
    removed: List[Position] = []
    now = datetime.now(timezone.utc)
    for cid, pos in list(_active_positions.items()):
        question_lower = pos.question.lower()
        is_weather = any(kw in question_lower for kw in WEATHER_KEYWORDS)
        age_hours = (now - pos.entry_time).total_seconds() / 3600

        if not is_weather:
            pos.pnl_usd = 0.0
            del _active_positions[cid]
            removed.append(pos)
            continue

        # ЗБІЛЬШЕНО З 48 ДО 100 ГОДИН (погодні ринки довго закриваються)
        if age_hours > 100:
            price = pos.current_price if pos.current_price > 0 else pos.entry_price
            if price >= 0.80:
                pnl = (1.0 - pos.entry_price) * pos.size_usd
                logger.info(f"✅ WIN (100h timeout): {pos.question[:60]} | PnL +${pnl:.2f}")
                pos.pnl_usd = pnl
            elif price <= 0.20:
                pnl = -pos.entry_price * pos.size_usd
                logger.info(f"❌ LOSS (100h timeout): {pos.question[:60]} | PnL ${pnl:.2f}")
                pos.pnl_usd = pnl
            else:
                pnl = -pos.entry_price * pos.size_usd
                pos.pnl_usd = pnl
            del _active_positions[cid]
            removed.append(pos)
            continue

        if pos.pnl_usd > 50.0:
            pos.pnl_usd = 0.0
            del _active_positions[cid]
            removed.append(pos)
            continue

    return removed

def check_and_close_positions(clob_client) -> List[Position]:
    """
    Перевіряє кожну відкриту позицію:
      1. Чи ринок розв'язаний → PnL через resolve()
      2. Mark-to-market через Gamma API → оновлює pnl_pct
      3. Stop-loss якщо pnl_pct < -STOP_LOSS_PCT
    """
    closed =[]
    for cid, pos in list(_active_positions.items()):

        # 1. Перевіряємо resolution
        resolved = check_market_resolved(cid, position=pos)
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
            _recently_closed[cid] = datetime.now(timezone.utc)  # v28-fix: cooldown
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
        # Tail угоди (entry < 10¢) — бінарні опціони: або $0 або profit.
        # Стоп-лос їм шкодить: шум в стакані на рівні 0.3¢ = ±50% коливань.
        # Чекаємо resolution — він і є "закриттям" tail угод.
        is_yes_tail = pos.direction == "BUY_YES" and pos.entry_price < 0.10
        is_no_tail  = pos.direction == "BUY_NO"  and pos.entry_price > 0.90
        if is_yes_tail or is_no_tail:
            continue  # tail: без стоп-лосу, чекаємо resolution

        # Для звичайних позицій (entry ≥ 10¢) — стандартний стоп
        if pos.pnl_pct <= -config.STOP_LOSS_PCT:
            logger.info(
                f"🔴 Stop-loss ({config.STOP_LOSS_PCT:.0%}): {pos.direction} | "
                f"PnL ${pos.pnl_usd:+.2f} ({pos.pnl_pct:+.1%}) | entry={pos.entry_price:.3f}"
            )
            closed.append(pos)
            del _active_positions[cid]
            _recently_closed[cid] = datetime.now(timezone.utc)  # v28-fix: cooldown

    return closed


def get_portfolio_summary() -> Dict:
    total_pnl = sum(p.pnl_usd for p in _active_positions.values())
    return {
        "active_positions": len(_active_positions),
        "total_pnl": total_pnl,
    }


def get_active_positions() -> Dict:
    """Повертає словник активних позицій (для збереження стану)."""
    return _active_positions
