# polymarket-bot-main/edge_calculator.py
"""
edge_calculator.py — ВИПРАВЛЕНА ВЕРСІЯ (our_prob ніколи не 0.00/1.00 + MIN_CONFIDENCE)
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
    # ... (ваш dataclass без змін)

def estimate_market_probability(market: PolyMarket, forecast: WeatherForecast) -> float:
    if not forecast:
        return 0.50

    # ... (ваш оригінальний код для temperature / rain / snow)

    # Фінальний fallback
    return 0.50


def calculate_edge(market: PolyMarket) -> EdgeResult:
    city = market.detected_city or "unknown"
    market_prob = market.midpoint_yes

    forecast = None
    if city != "unknown":
        forecast = get_multi_source_consensus(city)
    if not forecast:
        forecast = get_best_forecast(city) if city != "unknown" else None

    raw_prob = estimate_market_probability(market, forecast) if forecast else 0.50
    estimated_prob = max(0.12, min(0.88, raw_prob))   # ← КРИТИЧНЕ ВИПРАВЛЕННЯ

    edge_yes = estimated_prob - market_prob
    edge_no = (1 - estimated_prob) - (1 - market_prob)
    confidence = _confidence_from_sources(forecast) if forecast else 0.65

    effective_edge = max(abs(edge_yes), abs(edge_no)) * confidence

    # ... (ваш coldmath + extreme tail логіка без змін)

    if confidence < config.MIN_CONFIDENCE:
        is_tradeable = False
        reason = f"LOW_CONFIDENCE ({confidence:.2f}) | {reason}"

    # ... (повернення EdgeResult як у вас, але з новим estimated_prob)

    return EdgeResult(
        market=market,
        forecast=forecast,
        estimated_prob=estimated_prob,   # тепер завжди 0.12–0.88
        market_prob=market_prob,
        edge=effective_edge,
        edge_direction=direction,
        confidence=confidence,
        reason=reason,
        is_tradeable=is_tradeable
    )


def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    # ... (ваш код)
    logger.info(f"Edge scan: {len(results)} tradeable / {len(markets)} (з урахуванням MIN_CONFIDENCE)")
    return results
