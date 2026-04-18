"""
edge_calculator.py — coldmath v9 (виправлена версія)
"""

import math
import re
import logging
from typing import Optional, List
from dataclasses import dataclass

import config
from data_fetcher import WeatherForecast, get_best_forecast
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)


@dataclass
class EdgeResult:
    market: PolyMarket
    forecast: Optional[WeatherForecast]
    estimated_prob: float
    market_prob: float
    edge: float
    edge_direction: str
    confidence: float
    reason: str
    is_tradeable: bool
    size_usd: float = 0.0

    @property
    def edge_pct(self) -> str:
        return f"{self.edge:.1%}"

    @property
    def summary(self) -> str:
        return f"{self.edge_direction} | edge={self.edge_pct} | our_prob={self.estimated_prob:.3f} | market={self.market_prob:.3f} | {self.reason}"


def _confidence_from_forecast(forecast: Optional[WeatherForecast]) -> float:
    if not forecast or not forecast.sources_used:
        return 0.65
    sources = forecast.sources_used
    if "NOAA" in sources and len(sources) >= 2:
        return 0.94
    if "NOAA" in sources:
        return 0.90
    if any("GFS" in s or "ECMWF" in s for s in sources):
        return 0.85
    return 0.75


def _detect_market_kind(question: str) -> str:
    q = question.lower()
    if any(x in q for x in ["or higher", "above", "exceed"]):
        return "above"
    if any(x in q for x in ["or below", "or lower", "under"]):
        return "below"
    if re.search(r'be \d+\.?\d*\s*°?c', q) or re.search(r'be \d+\.?\d*\s*°?f', q):
        return "categorical"
    return "categorical"


def _parse_threshold(question: str) -> Optional[float]:
    # Підтримка °C і °F
    m = re.search(r'(\d+\.?\d*)\s*°?\s*[Cc]', question)
    if m:
        return float(m.group(1))
    m = re.search(r'(\d+\.?\d*)\s*°?\s*[Ff]', question)
    if m:
        f = float(m.group(1))
        return (f - 32) * 5 / 9   # конвертуємо °F → °C
    return None


def estimate_market_probability(market: PolyMarket, forecast: WeatherForecast) -> float:
    if not forecast:
        return 0.50

    threshold_c = _parse_threshold(market.question) or market.threshold_value
    kind = _detect_market_kind(market.question)
    tc = forecast.temp_high_c

    if threshold_c is None or tc == 0.0:
        return 0.50

    if kind == "above":
        return forecast.prob_above_temp_c(threshold_c)
    elif kind == "below":
        return forecast.prob_below_temp_c(threshold_c)
    else:
        # Categorical / bin
        sigma = 2.0
        p = (0.5 * (1 + math.erf((threshold_c + 0.5 - tc) / (sigma * math.sqrt(2)))) -
             0.5 * (1 + math.erf((threshold_c - 0.5 - tc) / (sigma * math.sqrt(2)))))
        return max(0.01, min(0.99, round(p, 4)))


def calculate_edge(market: PolyMarket) -> Optional[EdgeResult]:
    if market.hours_to_resolution < 4.0:   # посилили фільтр
        return None
    if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
        return None
    if market.detected_city and market.detected_city not in config.CITY_WHITELIST:
        return None

    forecast = get_best_forecast(market.detected_city)
    if not forecast:
        return None

    confidence = _confidence_from_forecast(forecast)
    our_prob = estimate_market_probability(market, forecast)
    market_prob = market.midpoint_yes

    edge_yes = our_prob - market_prob
    edge_no = (1.0 - our_prob) - (1.0 - market_prob)
    eff_yes = edge_yes * confidence
    eff_no = edge_no * confidence

    no_price = 1.0 - market_prob
    kind = _detect_market_kind(market.question)
    threshold = _parse_threshold(market.question)
    src = "+".join(s.split("_")[0] for s in forecast.sources_used[:2])

    # Sanity check для extreme tail (запобігає 0.98 на "43°F or below")
    if kind == "below" and forecast.temp_high_c > (threshold or 0) + 8:
        if our_prob > 0.10:
            our_prob = max(0.02, 1.0 - (forecast.temp_high_c - (threshold or 0)) / 20.0)

    # COLDMATH TAIL NO
    is_coldmath_no = (
        config.ENABLE_COLDMATH_TAIL_NO and
        no_price >= config.COLDMATH_MIN_ASK_NO and
        no_price <= config.COLDMATH_MAX_ASK_NO and
        eff_no >= config.COLDMATH_MIN_EDGE_NO
    )

    # EXTREME TAIL YES
    is_extreme_yes = (
        config.ENABLE_EXTREME_TAIL_YES and
        market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES and
        eff_yes >= config.EXTREME_TAIL_MIN_EDGE_YES and
        confidence >= 0.80
    )

    if is_coldmath_no:
        direction = "BUY_NO"
        eff_edge = abs(eff_no)
        size_usd = min(config.COLDMATH_MAX_SIZE_USD, max(config.MIN_POSITION_USD, config.BASE_POSITION_USD * confidence))
        reason = f"COLDMATH NO @ {no_price:.3f} | {src} | {forecast.temp_high_c:.1f}°C"
        tradeable = True
    elif is_extreme_yes:
        direction = "BUY_YES"
        eff_edge = abs(eff_yes)
        size_usd = min(config.EXTREME_TAIL_MAX_SIZE_USD, config.MIN_POSITION_USD * 1.5)
        reason = f"EXTREME YES @ {market.best_ask_yes:.3f} | {kind} | {src} | {forecast.temp_high_c:.1f}°C"
        tradeable = True
    elif abs(eff_yes) >= config.MIN_EDGE_ENTRY or abs(eff_no) >= config.MIN_EDGE_ENTRY:
        if eff_yes >= eff_no:
            direction = "BUY_YES"
            eff_edge = eff_yes
            reason = f"YES edge | {our_prob:.3f} vs {market_prob:.3f}"
        else:
            direction = "BUY_NO"
            eff_edge = eff_no
            reason = f"NO edge | {our_prob:.3f} vs {market_prob:.3f}"
        size_usd = max(config.MIN_POSITION_USD, min(config.BASE_POSITION_USD * confidence * 1.1, config.INITIAL_CAPITAL * config.MAX_POSITION_PCT))
        tradeable = True
    else:
        return None

    return EdgeResult(
        market=market,
        forecast=forecast,
        estimated_prob=our_prob,
        market_prob=market_prob,
        edge=eff_edge,
        edge_direction=direction,
        confidence=confidence,
        reason=reason,
        is_tradeable=tradeable,
        size_usd=round(size_usd, 2)
    )


def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    for market in markets:
        edge = calculate_edge(market)
        if edge and edge.is_tradeable:
            logger.info(f"✅ EDGE: {edge.summary}")
            results.append(edge)

    results.sort(key=lambda r: r.edge, reverse=True)
    logger.info(f"Edge scan: {len(results)} tradeable / {len(markets)} ринків")
    return results
