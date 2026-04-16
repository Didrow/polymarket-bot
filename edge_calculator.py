"""
edge_calculator.py — Polymarket Weather Bot 2026
З виправленим EXTREME TAIL під стиль mahera777
"""

import math
import logging
from typing import Optional
from dataclasses import dataclass

import config
from data_fetcher import WeatherForecast, get_best_forecast, get_multi_source_consensus
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)


@dataclass
class EdgeResult:
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
        return (f"{self.edge_direction} | edge={self.edge_pct} | "
                f"our_prob={self.estimated_prob:.2f} | market={self.market_prob:.2f} | "
                f"{self.reason}")


def _confidence_from_sources(forecast: WeatherForecast) -> float:
    if forecast.source in ["gfs_open_meteo", "ecmwf_open_meteo"]:
        return 0.92
    if forecast.source == "consensus_noaa+open_meteo":
        return 0.90
    if forecast.source == "noaa":
        return 0.85
    return 0.75


def calculate_edge(market: PolyMarket) -> EdgeResult:
    city = market.detected_city or "unknown"
    market_prob = market.midpoint_yes

    forecast = get_multi_source_consensus(city) if city != "unknown" else None
    if not forecast:
        forecast = get_best_forecast(city)

    if not forecast:
        return EdgeResult(market=market, forecast=None, estimated_prob=None,
                          market_prob=market_prob, edge=0.0, edge_direction="SKIP",
                          confidence=0.0, reason=f"Немає прогнозу для {city}", is_tradeable=False)

    estimated_prob = estimate_market_probability(market, forecast)  # функція нижче

    if estimated_prob is None:
        return EdgeResult(market=market, forecast=forecast, estimated_prob=None,
                          market_prob=market_prob, edge=0.0, edge_direction="SKIP",
                          confidence=0.0, reason=f"Не вдалося розрахувати prob", is_tradeable=False)

    edge_yes = estimated_prob - market_prob
    edge_no = (1 - estimated_prob) - (1 - market_prob)

    confidence = _confidence_from_sources(forecast)
    effective_edge = max(abs(edge_yes), abs(edge_no)) * confidence

    # ── EXTREME TAIL BOOST (стиль mahera777) ─────────────────
    if (config.ENABLE_EXTREME_TAIL and
        market.detected_city in config.EXTREME_TAIL_CITIES and
        market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES and
        edge_yes > 0):

        effective_edge = max(effective_edge, edge_yes * 1.45)   # +45% для 1-5¢ YES
        confidence = min(0.97, confidence + 0.10)
        reason = f"EXTREME TAIL: YES @ {market.best_ask_yes:.2f}¢ (потенціал 500–6000%) | {forecast.source}"
    else:
        reason = (f"YES дешевий: наша P={estimated_prob:.2f} > ринок {market_prob:.2f} | "
                  f"джерело: {forecast.source}" if edge_yes >= edge_no else
                  f"NO дешевий: наша P={estimated_prob:.2f} < ринок {market_prob:.2f} | "
                  f"джерело: {forecast.source}")

    if effective_edge < config.MIN_EDGE_ENTRY:
        direction = "SKIP"
        is_tradeable = False
    elif edge_yes >= edge_no:
        direction = "BUY_YES"
        is_tradeable = True
    else:
        direction = "BUY_NO"
        is_tradeable = True

    return EdgeResult(
        market=market, forecast=forecast, estimated_prob=estimated_prob,
        market_prob=market_prob, edge=effective_edge, edge_direction=direction,
        confidence=confidence, reason=reason, is_tradeable=is_tradeable
    )


# (решта функцій _estimate_prob_temperature, _estimate_prob_rain тощо залишаються без змін — вони вже є у твоєму файлі)
# Якщо потрібно — скажи, я дам повну версію з ними.
