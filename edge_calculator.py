"""
edge_calculator.py — Edge calculation для Polymarket Weather Bot 2026 (сумісний з новим data_fetcher)
"""

from dataclasses import dataclass
from typing import Optional, List
import logging
from datetime import datetime

from config import (
    MIN_EDGE_ENTRY, MIN_EDGE_HOLD, MIN_CONFIDENCE,
    ENABLE_EXTREME_TAIL_YES, EXTREME_TAIL_MIN_EDGE_YES, EXTREME_TAIL_MAX_SIZE_USD,
    ENABLE_COLDMATH_TAIL_NO, COLDMATH_MIN_EDGE_NO, COLDMATH_MAX_SIZE_USD,
    COLDMATH_MIN_ASK_NO, COLDMATH_MAX_ASK_NO
)
from data_fetcher import WeatherForecast, get_best_forecast

logger = logging.getLogger(__name__)

@dataclass
class EdgeResult:
    market_slug: str
    direction: str  # "BUY_YES" або "BUY_NO"
    edge: float
    estimated_prob: float
    market_prob: float
    confidence: float
    size_usd: float = 0.0
    reason: str = ""
    is_extreme_tail: bool = False

def _confidence_from_sources(forecast: Optional[WeatherForecast]) -> float:
    if not forecast or not forecast.sources_used:
        return 0.60
    sources = forecast.sources_used
    if "NOAA" in sources:
        return 0.92
    if "NASA_POWER" in sources:
        return 0.78
    if any("GFS" in s or "ECMWF" in s for s in sources):
        return 0.82
    return 0.70

def estimate_market_probability(market, forecast: Optional[WeatherForecast]) -> float:
    if not forecast:
        return 0.50

    # Приклад логіки для різних типів ринків (температура, дощ тощо)
    if "highest" in market.get("title", "").lower() or "temperature" in market.get("title", "").lower():
        # Для highest temperature — ймовірність "above" певного рівня
        threshold = 18.0  # приклад, можна динамічно парсити
        raw_prob = forecast.prob_above_temp_c(threshold)
    elif "rain" in market.get("title", "").lower():
        raw_prob = forecast.prob_rain_or_snow()
    else:
        raw_prob = 0.50

    estimated = max(0.15, min(0.85, raw_prob))  # жорсткий clamp
    return estimated

def calculate_edge(market) -> Optional[EdgeResult]:
    try:
        city = None
        for c in ["London", "Paris", "NYC", "New York", "Chicago", "Los Angeles", "Berlin", "Tokyo"]:
            if c.lower() in market.get("title", "").lower():
                city = c
                break
        if not city:
            return None

        forecast = get_best_forecast(city)
        if not forecast:
            return None

        our_prob = estimate_market_probability(market, forecast)
        market_prob = market.get("midpoint_yes", 0.50)  # або best_ask / best_bid

        edge_yes = (our_prob / max(market_prob, 0.001)) - 1.0 if market_prob > 0 else 0.0
        edge_no = ((1 - our_prob) / max(1 - market_prob, 0.001)) - 1.0 if market_prob < 1 else 0.0

        edge = max(edge_yes, edge_no)
        direction = "BUY_YES" if edge_yes > edge_no else "BUY_NO"

        confidence = _confidence_from_sources(forecast)

        if edge < MIN_EDGE_ENTRY or confidence < MIN_CONFIDENCE:
            return None

        # Extreme Tail YES
        is_extreme = False
        size = 0.0
        ask_price = market.get("best_ask_yes", 1.0)

        if ENABLE_EXTREME_TAIL_YES and ask_price <= EXTREME_TAIL_MAX_ASK_YES and edge_yes >= EXTREME_TAIL_MIN_EDGE_YES:
            is_extreme = True
            size = min(EXTREME_TAIL_MAX_SIZE_USD, 4.0)
            reason = f"EXTREME YES @ {ask_price*100:.3f}¢"
        elif ENABLE_COLDMATH_TAIL_NO and direction == "BUY_NO" and ask_price >= COLDMATH_MIN_ASK_NO:
            size = min(COLDMATH_MAX_SIZE_USD, 12.0)
            reason = "COLDMATH TAIL NO"
        else:
            size = 5.0  # базовий розмір
            reason = f"Normal edge {edge:.1%}"

        return EdgeResult(
            market_slug=market.get("slug", ""),
            direction=direction,
            edge=edge,
            estimated_prob=our_prob,
            market_prob=market_prob,
            confidence=confidence,
            size_usd=size,
            reason=reason,
            is_extreme_tail=is_extreme
        )
    except Exception as e:
        logger.debug(f"Edge calc error: {e}")
        return None

def scan_all_edges(markets: List[dict]) -> List[EdgeResult]:
    results = []
    for market in markets:
        edge = calculate_edge(market)
        if edge:
            results.append(edge)
            logger.info(f"✅ EDGE: {edge.direction} | edge={edge.edge*100:.1f}% | "
                       f"our_prob={edge.estimated_prob:.2f} | market={edge.market_prob:.2f} | {edge.reason}")
    logger.info(f"Знайдено {len(results)} можливостей з {len(markets)} ринків")
    return results
