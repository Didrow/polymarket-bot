"""
edge_calculator.py — coldmath v7 (динамічний threshold + жорсткі фільтри)
"""

from dataclasses import dataclass
from typing import Optional, List
import logging
import re

import config
from data_fetcher import WeatherForecast, get_best_forecast
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)

@dataclass
class EdgeResult:
    market: PolyMarket
    estimated_prob: float
    market_prob: float
    edge: float
    direction: str
    confidence: float
    reason: str
    size_usd: float = 0.0
    is_extreme_tail: bool = False

def _parse_threshold(question: str) -> Optional[float]:
    """Динамічно витягуємо температуру з назви ринку"""
    match = re.search(r'(\d+\.?\d*)\s*°?C', question)
    if match:
        return float(match.group(1))
    match = re.search(r'be (\d+)', question)
    if match:
        return float(match.group(1))
    return None

def _confidence_from_forecast(forecast: Optional[WeatherForecast]) -> float:
    if not forecast or not forecast.sources_used:
        return 0.65
    sources = forecast.sources_used
    if "NOAA" in sources:
        return 0.95
    if "NASA_POWER" in sources and any("GFS" in s or "ECMWF" in s for s in sources):
        return 0.88
    if any("GFS" in s for s in sources):
        return 0.84
    return 0.78

def estimate_market_probability(market: PolyMarket, forecast: WeatherForecast) -> float:
    threshold = _parse_threshold(market.question) or market.threshold_value
    if threshold is None:
        return 0.50

    # Для "highest temperature be X°C" або "X°C or higher"
    if "highest" in market.question.lower() or "or higher" in market.question.lower():
        return forecast.prob_above_temp_c(threshold)
    elif "below" in market.question.lower() or "or lower" in market.question.lower():
        return forecast.prob_below_temp_c(threshold)
    else:
        # Categorical bin
        return forecast.prob_above_temp_c(threshold)

def calculate_edge(market: PolyMarket) -> Optional[EdgeResult]:
    city = market.detected_city
    if not city:
        return None

    forecast = get_best_forecast(city)
    if not forecast:
        return None

    our_prob = estimate_market_probability(market, forecast)
    market_prob = market.midpoint_yes
    confidence = _confidence_from_forecast(forecast)

    edge_yes = our_prob - market_prob
    edge_no = (1 - our_prob) - (1 - market_prob)

    # Жорсткий фільтр
    if confidence < config.MIN_CONFIDENCE:
        return None

    ask_yes = market.best_ask_yes
    no_price = 1.0 - market_prob

    # COLDMATH TAIL NO
    if (config.ENABLE_COLDMATH_TAIL_NO and 
        no_price >= config.COLDMATH_MIN_ASK_NO and 
        edge_no >= config.COLDMATH_MIN_EDGE_NO):
        direction = "BUY_NO"
        edge = edge_no
        reason = f"COLDMATH NO @ {no_price:.3f}"
        size = config.COLDMATH_MAX_SIZE_USD
        is_extreme = False

    # EXTREME TAIL YES
    elif (config.ENABLE_EXTREME_TAIL_YES and 
          ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES and 
          edge_yes >= config.EXTREME_TAIL_MIN_EDGE_YES):
        direction = "BUY_YES"
        edge = edge_yes
        reason = f"EXTREME YES @ {ask_yes*100:.3f}¢ | {forecast.temp_high_c:.1f}C"
        size = config.EXTREME_TAIL_MAX_SIZE_USD
        is_extreme = True

    # Normal edge
    elif max(edge_yes, edge_no) >= config.MIN_EDGE_ENTRY:
        if edge_yes > edge_no:
            direction = "BUY_YES"
            edge = edge_yes
            reason = f"YES {our_prob:.2f} vs {market_prob:.2f}"
        else:
            direction = "BUY_NO"
            edge = edge_no
            reason = f"NO {our_prob:.2f} vs {market_prob:.2f}"
        size = config.BASE_POSITION_USD
        is_extreme = False
    else:
        return None

    return EdgeResult(
        market=market,
        estimated_prob=our_prob,
        market_prob=market_prob,
        edge=edge,
        direction=direction,
        confidence=confidence,
        reason=reason,
        size_usd=size,
        is_extreme_tail=is_extreme
    )

def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    for market in markets:
        if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
            continue
        edge = calculate_edge(market)
        if edge:
            logger.info(f"✅ EDGE: {edge.direction} | edge={edge.edge*100:.1f}% | "
                       f"our_prob={edge.estimated_prob:.2f} | market={edge.market_prob:.2f} | {edge.reason}")
            results.append(edge)
    logger.info(f"Знайдено {len(results)} можливостей з {len(markets)} ринків")
    return results
