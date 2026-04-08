"""
trader.py — Polymarket Weather Bot 2026
Логіка торгівлі: Volatility Targeting + Kelly Criterion + Exit Rules.
Підтримує dry-run режим (жодних реальних транзакцій без явного дозволу).
"""

import math
import time
import logging
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass, field

import config
from edge_calculator import EdgeResult
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)

# Кеш волатильності (уникаємо повторних запитів до API)
_vol_cache: Dict[str, Tuple[float, float]] = {}  # token_id → (timestamp, vol)

# Активні позиції (in-memory)
_active_positions: Dict[str, "Position"] = {}


@dataclass
class Position:
    """Активна торгова позиція."""
    condition_id: str
    question: str
    direction: str            # "YES" або "NO"
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
    status: str = "OPEN"      # OPEN / CLOSED_WIN / CLOSED_STOP

    def update_pnl(self, current_price: float):
        self.current_price = current_price
        self.pnl_pct = (current_price - self.entry_price) / self.entry_price
        self.pnl_usd = self.pnl_pct * self.size_usd


# ═══════════════════════════════════════════════════════════
# VOLATILITY TARGETING
# ═══════════════════════════════════════════════════════════

def fetch_price_history(token_id: str) -> Optional[List[float]]:
    """
    Отримати історію цін токена з CLOB API.
    Endpoint: GET /prices-history?token_id=...&interval=1h&fidelity=60
    Повертає список mid-цін (0–1).
    """
    import requests
    try:
        url = f"{config.CLOB_URL}/prices-history"
        params = {
            "token_id": token_id,
            "interval": "1h",
            "fidelity": 60,  # 1 точка на годину
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        # Структура відповіді: {"history": [{"t": timestamp, "p": price}, ...]}
        history = data.get("history", [])
        if not history:
            return None

        prices = [float(h["p"]) for h in history if h.get("p") is not None]
        return prices if len(prices) >= config.MIN_DATA_POINTS_FALLBACK else None

    except Exception as e:
        logger.debug(f"Price history error для {token_id[:8]}...: {e}")
        return None


def calculate_ewma_volatility(prices: List[float], lam: float = config.LAMBDA_EWMA) -> float:
    """
    Розрахунок EWMA волатильності за JP Morgan RiskMetrics.
    
    λ = 0.94 (стандарт для денних даних)
    Returns: annualized volatility (0–1+)
    """
    if len(prices) < 2:
        return config.TARGET_PORTFOLIO_VOL  # Fallback

    # Лог-доходності між послідовними цінами
    log_returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0 and prices[i] > 0:
            lr = math.log(prices[i] / prices[i - 1])
            log_returns.append(lr)

    if len(log_returns) < 2:
        return config.TARGET_PORTFOLIO_VOL

    # EWMA на квадратах доходностей
    ewma_var = log_returns[0] ** 2
    for lr in log_returns[1:]:
        ewma_var = lam * ewma_var + (1 - lam) * lr ** 2

    # Annualize: дані погодинні → × sqrt(24 * 365)
    annual_factor = math.sqrt(24 * 365)
    annual_vol = math.sqrt(ewma_var) * annual_factor

    # Prediction markets мають специфічну vol — обмежуємо до розумного діапазону
    # 2% мінімум (дуже стабільний ринок) — 40% максимум (дуже волатильний)
    annual_vol = max(0.02, min(0.40, annual_vol))

    return round(annual_vol, 4)


def get_market_volatility(token_id: str) -> float:
    """
    Отримати волатильність ринку (з кешем).
    Якщо дані недоступні — повертає TARGET_PORTFOLIO_VOL як fallback.
    """
    # Перевірка кешу (оновлення раз на годину)
    if token_id in _vol_cache:
        ts, vol = _vol_cache[token_id]
        if time.time() - ts < 3600:
            return vol

    prices = fetch_price_history(token_id)

    if not prices or len(prices) < config.MIN_DATA_POINTS_FALLBACK:
        logger.debug(f"Vol fallback для {token_id[:8]}...: недостатньо даних")
        return config.TARGET_PORTFOLIO_VOL  # Fallback

    vol = calculate_ewma_volatility(prices)
    _vol_cache[token_id] = (time.time(), vol)

    logger.debug(f"Market vol для {token_id[:8]}...: {vol:.1%}")
    return vol


def calculate_volatility_targeted_size(
    token_id: str,
    current_capital: float,
    base_size_usd: Optional[float] = None,
) -> Tuple[float, str]:
    """
    Розрахунок розміру позиції через Volatility Targeting.
    
    Formula:
      adjusted_size = (TARGET_VOL / market_vol) × base_size
      
    Returns: (size_usd, log_message)
    """
    base_size = base_size_usd or config.BASE_POSITION_USD
    market_vol = get_market_volatility(token_id)

    log_msg = f"Market vol: {market_vol:.1%} | Target vol: {config.TARGET_PORTFOLIO_VOL:.1%}"

    # No-trade band: занадто волатильний ринок
    if market_vol > config.MAX_VOL_NO_TRADE:
        log_msg += f" → vol > {config.MAX_VOL_NO_TRADE:.0%}, розмір = мінімум"
        return config.MIN_POSITION_USD, log_msg

    # Formule Volatility Targeting
    vol_ratio = config.TARGET_PORTFOLIO_VOL / max(market_vol, 0.001)
    adjusted_size = base_size * vol_ratio

    # Hard cap: не більше MAX_POSITION_PCT від капіталу
    max_by_capital = current_capital * config.MAX_POSITION_PCT
    adjusted_size = min(adjusted_size, max_by_capital, config.MAX_POSITION_USD)
    adjusted_size = max(adjusted_size, config.MIN_POSITION_USD)

    log_msg += f" | Adjusted size: ${adjusted_size:.2f} (from base ${base_size:.2f})"
    return round(adjusted_size, 2), log_msg


def kelly_position_size(
    edge: float,
    capital: float,
    win_loss_ratio: float = 1.0,
) -> float:
    """
    Консервативний Kelly Criterion (25% від Kelly).
    Для бінарних ринків Polymarket (win = 1, loss = 0).
    """
    if edge <= 0:
        return 0.0

    # Kelly для бінарних ринків Polymarket:
    # p = ймовірність виграшу (наша оцінка)
    # b = odds (скільки виграємо за $1 ризику)
    # f* = (p*b - (1-p)) / b = p - (1-p)/b
    # edge ≈ p - market_price, тому p ≈ market_price + edge
    # Спрощено: kelly ≈ edge (для b≈1 ринків)
    p = min(0.99, 0.5 + edge)
    b = max(0.1, win_loss_ratio)
    kelly_f = (p * b - (1 - p)) / b
    kelly_f = max(0.0, min(0.25, kelly_f))  # Не більше 25% Kelly

    # 25% від Kelly для безпеки (quarter-Kelly)
    conservative_f = kelly_f * 0.25
    size = capital * conservative_f

    return min(size, capital * config.MAX_POSITION_PCT)


# ═══════════════════════════════════════════════════════════
# ОСНОВНА ЛОГІКА ТОРГІВЛІ
# ═══════════════════════════════════════════════════════════

def decide_position_size(edge_result: EdgeResult, current_capital: float) -> float:
    """
    Комбінований розрахунок розміру позиції:
    Мінімум з Kelly та Volatility Targeting.
    """
    market = edge_result.market
    token_id = (market.token_yes_id if edge_result.edge_direction == "BUY_YES"
                else market.token_no_id)
    entry_price = (market.best_ask_yes if edge_result.edge_direction == "BUY_YES"
                   else 1 - market.best_bid_yes)

    # Volatility Targeting
    vol_size, vol_log = calculate_volatility_targeted_size(token_id, current_capital)
    logger.info(f"  Vol Targeting: {vol_log}")

    # Kelly
    win_loss = (1 - entry_price) / entry_price if entry_price > 0 else 1.0
    kelly_size = kelly_position_size(edge_result.edge, current_capital, win_loss)
    logger.info(f"  Kelly size: ${kelly_size:.2f}")

    # Беремо мінімум для консервативного підходу
    final_size = min(vol_size, kelly_size)
    final_size = max(final_size, config.MIN_POSITION_USD)
    final_size = min(final_size, config.MAX_POSITION_USD)

    logger.info(f"  Final size: ${final_size:.2f}")
    return final_size


def should_close_position(position: Position, current_price: float, current_edge: float) -> Tuple[bool, str]:
    """
    Перевірити, чи потрібно закрити позицію.
    Returns: (should_close, reason)
    """
    pnl_pct = (current_price - position.entry_price) / position.entry_price

    # Стоп-лос
    if pnl_pct <= -config.STOP_LOSS_PCT:
        return True, f"СТОП-ЛОС: {pnl_pct:.1%} < -{config.STOP_LOSS_PCT:.0%}"

    # Edge зник
    if current_edge < config.MIN_EDGE_HOLD and pnl_pct < 0:
        return True, f"Edge зник ({current_edge:.1%} < {config.MIN_EDGE_HOLD:.0%}) + збиток"

    # Фіксація прибутку при edge нижче 5% (тримаємо якщо все ще є edge)
    if current_edge < config.MIN_EDGE_HOLD:
        return True, f"Edge впав нижче {config.MIN_EDGE_HOLD:.0%}, фіксуємо"

    return False, ""


def place_trade(edge_result: EdgeResult, current_capital: float, clob_client=None) -> Optional[Position]:
    """
    Розмістити угоду або симулювати її в dry-run режимі.
    
    clob_client: екземпляр py-clob-client (None = dry-run)
    """
    market = edge_result.market

    # Перевірка ліміту активних позицій
    if len(_active_positions) >= config.MAX_ACTIVE_POSITIONS:
        logger.warning(f"Максимум активних позицій ({config.MAX_ACTIVE_POSITIONS}) досягнуто")
        return None

    # Перевірка, чи вже є позиція в цьому ринку
    if market.condition_id in _active_positions:
        logger.info(f"Вже є позиція в ринку: {market.condition_id[:8]}...")
        return None

    # Розрахунок розміру
    size_usd = decide_position_size(edge_result, current_capital)

    # Підтвердження для великих угод
    is_dry_run = config.DRY_RUN or clob_client is None
    if not is_dry_run and size_usd > config.CONFIRM_TRADE_ABOVE_USD:
        logger.warning(f"⚠️  Угода ${size_usd:.2f} > ${config.CONFIRM_TRADE_ABOVE_USD} — потрібне підтвердження!")
        return None

    direction = edge_result.edge_direction  # BUY_YES або BUY_NO
    token_id = (market.token_yes_id if direction == "BUY_YES" else market.token_no_id)
    entry_price = (market.best_ask_yes if direction == "BUY_YES" else 1 - market.best_bid_yes)
    shares = size_usd / entry_price if entry_price > 0 else 0

    if is_dry_run:
        logger.info(
            f"🧪 DRY-RUN | {direction} | {market.question[:50]} | "
            f"${size_usd:.2f} @ {entry_price:.3f} = {shares:.1f} shares | "
            f"edge={edge_result.edge_pct}"
        )
    else:
        # ─── РЕАЛЬНА УГОДА ───────────────────────────────────────
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            order = OrderArgs(
                token_id=token_id,
                price=round(entry_price, 4),
                size=round(shares, 2),
                side="BUY",
            )
            resp = clob_client.create_and_post_order(order)
            logger.info(f"✅ ОРДЕР РОЗМІЩЕНО: {resp}")
        except Exception as e:
            logger.error(f"❌ Помилка розміщення ордера: {e}")
            return None

    # Реєструємо позицію
    position = Position(
        condition_id=market.condition_id,
        question=market.question,
        direction=direction.replace("BUY_", ""),
        token_id=token_id,
        entry_price=entry_price,
        current_price=entry_price,
        size_usd=size_usd,
        shares=shares,
        entry_time=datetime.now(timezone.utc),
        edge_at_entry=edge_result.edge,
        city=market.detected_city,
        market_type=market.market_type,
    )
    _active_positions[market.condition_id] = position
    return position


def check_and_close_positions(clob_client=None) -> List[Position]:
    """
    Перевірити всі активні позиції та закрити ті, що потрапили під правила виходу.
    """
    closed = []
    is_dry_run = config.DRY_RUN or clob_client is None

    for cid, position in list(_active_positions.items()):
        # TODO: отримати поточну ціну через get_orderbook_price
        import requests
        try:
            url = f"{config.CLOB_URL}/book"
            r = requests.get(url, params={"token_id": position.token_id}, timeout=5)
            data = r.json()
            asks = data.get("asks", [])
            current_price = float(asks[0]["price"]) if asks else position.current_price
        except Exception:
            current_price = position.current_price

        position.update_pnl(current_price)

        # Edge для поточного стану
        current_edge = max(0, position.edge_at_entry - abs(position.pnl_pct) * 0.5)

        should_close, reason = should_close_position(position, current_price, current_edge)

        if should_close:
            if is_dry_run:
                logger.info(
                    f"🧪 DRY-RUN CLOSE | {position.direction} {position.question[:40]} | "
                    f"PnL: {position.pnl_pct:.1%} (${position.pnl_usd:.2f}) | {reason}"
                )
            else:
                # Закрити позицію через sell
                logger.info(f"ЗАКРИВАЄМО позицію: {reason}")
                # clob_client.close_position(...)  # Деталі залежать від SDK

            position.status = "CLOSED_WIN" if position.pnl_usd > 0 else "CLOSED_STOP"
            del _active_positions[cid]
            closed.append(position)

    return closed


def get_portfolio_summary(capital: float) -> Dict:
    """Звіт по портфелю."""
    positions = list(_active_positions.values())
    total_invested = sum(p.size_usd for p in positions)
    total_pnl = sum(p.pnl_usd for p in positions)
    return {
        "capital": capital,
        "active_positions": len(positions),
        "total_invested": total_invested,
        "available_capital": capital - total_invested,
        "unrealized_pnl": total_pnl,
        "positions": positions,
    }
