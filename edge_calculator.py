# polymarket-bot-main/edge_calculator.py
"""
edge_calculator.py — Polymarket Weather Bot 2026 (повністю виправлена версія)
ВИПРАВЛЕНО: IndentationError, our_prob=0.00/1.00, MIN_CONFIDENCE
"""

import math
import logging
from typing import Optional, List
from dataclasses import dataclass

import config
from data_fetcher import WeatherForecast, get_multi_source_consensus, get_best_forecast
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)


@dataclass
class EdgeResult:
    """Результат розрахунку edge."""
    market: PolyMarket
    forecast: Optional[WeatherForecast]
    estimated_prob: Optional[float]
    market_prob: float
    edge: float
    edge_direction: str
    confidence: float
    reason: str
    is_tradeable: bool

    @property
    def edge_pct(self) -> str:
        return f"{self.edge:.1%}"

    @property
    def summary(self) -> str:
        ep = f"{self.estimated_prob:.2f}" if self.estimated_prob is not None else "N/A"
        return (f"{self.edge_direction} | edge={self.edge_pct} | "
                f"our_prob={ep} | market={self.market_prob:.2f} | "
                f"{self.reason}")


def _confidence_from_sources(forecast: Optional[WeatherForecast]) -> float:
    if not forecast:
        return 0.65
    src = forecast.source
    if "consensus" in src:
        n = src.count("_") + 1
        return min(0.97, 0.88 + n * 0.02)
    if src == "noaa":
        return 0.92
    if "ecmwf" in src:
        return 0.90
    if "gfs" in src:
        return 0.87
    if "nasa" in src:
        return 0.80
    return 0.78


def estimate_market_probability(market: PolyMarket, forecast: Optional[WeatherForecast]) -> float:
    """Базовий розрахунок ймовірності з fallback."""
    if not forecast:
        return 0.50

    # Температурні ринки (використовуємо ваш оригінальний код)
    q = market.question.lower()
    if market.market_type == "temperature" and market.threshold_value is not None:
        threshold_c = market.threshold_value
        if "°f" in q or "fahrenheit" in q:
            threshold_c = (market.threshold_value - 32) * 5 / 9
        if market.is_above is True:
            return forecast.prob_above_temp_c(threshold_c)
        elif market.is_above is False:
            return 1.0 - forecast.prob_above_temp_c(threshold_c)
        else:
            return forecast.prob_exact_temp_c(threshold_c)

    # Дощ / сніг
    if market.market_type == "rain":
        return forecast.prob_rain()
    if market.market_type == "snow":
        return forecast.prob_snow()

    return 0.50


def calculate_edge(market: PolyMarket) -> EdgeResult:
    city = market.detected_city or "unknown"
    market_prob = market.midpoint_yes

    forecast = None
    if city != "unknown":
        forecast = get_multi_source_consensus(city)
    if not forecast:
        forecast = get_best_forecast(city) if city != "unknown" else None

    raw_prob = estimate_market_probability(market, forecast)
    estimated_prob = max(0.12, min(0.88, raw_prob))   # ← ніколи 0.00 або 1.00

    edge_yes = estimated_prob - market_prob
    edge_no = (1 - estimated_prob) - (1 - market_prob)
    confidence = _confidence_from_sources(forecast)

    effective_edge = max(abs(edge_yes), abs(edge_no)) * confidence

    # Coldmath + Extreme Tail логіка (залишаю вашу)
    is_coldmath_tail_no = (
        config.ENABLE_COLDMATH_TAIL_NO
        and market.midpoint_yes <= (1 - config.COLDMATH_MIN_ASK_NO)
        and (1 - market.midpoint_yes) >= config.COLDMATH_MIN_ASK_NO
        and edge_no > config.COLDMATH_MIN_EDGE_NO
        and market.volume_usd >= config.MIN_MARKET_VOLUME_USD
    )

    is_extreme_tail_yes = (
        config.ENABLE_EXTREME_TAIL_YES
        and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES
        and edge_yes > config.EXTREME_TAIL_MIN_EDGE_YES
        and market.volume_usd >= 500
    )

    if (market.midpoint_yes < 0.02 or market.midpoint_yes > 0.98) and market.volume_usd < 1000:
        return EdgeResult(
            market=market, forecast=forecast,
            estimated_prob=estimated_prob, market_prob=market_prob,
            edge=0.0, edge_direction="SKIP", confidence=confidence,
            reason="LOW_VOL_EXTREME_BIN → шум",
            is_tradeable=False
        )

    if is_coldmath_tail_no:
        effective_edge = max(effective_edge, abs(edge_no) * 1.40)
        confidence = min(0.97, confidence + 0.08)
        direction = "BUY_NO"
        reason = f"COLDMATH NO @ {market.midpoint_yes:.3f} YES"
        is_tradeable = True
    elif is_extreme_tail_yes:
        effective_edge = max(effective_edge, abs(edge_yes) * 1.35)
        confidence = min(0.95, confidence + 0.07)
        direction = "BUY_YES"
        reason = f"EXTREME YES @ {market.best_ask_yes:.3f}¢"
        is_tradeable = True
    elif effective_edge >= config.MIN_EDGE_ENTRY and confidence >= config.MIN_CONFIDENCE:
        direction = "BUY_YES" if edge_yes >= edge_no else "BUY_NO"
        reason = f"{'YES' if direction == 'BUY_YES' else 'NO'} edge: наша P={estimated_prob:.2f}"
        is_tradeable = True
    else:
        direction = "SKIP"
        reason = f"edge={effective_edge:.1%} або confidence={confidence:.2f} < min"
        is_tradeable = False

    return EdgeResult(
        market=market, forecast=forecast,
        estimated_prob=estimated_prob,
        market_prob=market_prob,
        edge=effective_edge,
        edge_direction=direction,
        confidence=confidence,
        reason=reason,
        is_tradeable=is_tradeable
    )


def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    skipped = 0
    for market in markets:
        if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
            skipped += 1
            continue
        edge = calculate_edge(market)
        if edge.is_tradeable:
            logger.info(f"✅ EDGE: {edge.summary}")
            results.append(edge)
        else:
            logger.debug(f"⏭ SKIP: {edge.reason} | {market.question[:45]}")

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} ринків "
        f"({skipped} пропущено за volume)"
    )
    return results
