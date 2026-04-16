"""
edge_calculator.py — Polymarket Weather Bot 2026 (виправлено під mahera777 extreme tail)
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
                f"our_prob={self.estimated_prob:.3f} | market={self.market_prob:.3f} | "
                f"{self.reason}")

def _confidence_from_sources(forecast: WeatherForecast) -> float:
    if forecast.source in ["gfs_open_meteo", "ecmwf_open_meteo"]:
        return 0.93
    if forecast.source == "consensus_noaa+open_meteo":
        return 0.91
    if forecast.source == "noaa":
        return 0.87
    return 0.78

def estimate_market_probability(market: PolyMarket, forecast: WeatherForecast) -> Optional[float]:
    """Головний розрахунок ймовірності (виправлено 0.00/1.00)"""
    if not forecast:
        return 0.50

    q = market.question.lower()

    # Температура (найпоширеніший тип)
    if market.threshold_value is not None and market.is_above is not None:
        if market.is_above:
            prob = forecast.prob_above_temp_f(market.threshold_value)
        else:
            prob = 1.0 - forecast.prob_above_temp_f(market.threshold_value) if forecast.prob_above_temp_f(market.threshold_value) is not None else 0.50
        return prob

    # Дощ / сніг
    if "rain" in q or "precipitation" in q:
        return forecast.prob_rain() or 0.50
    if "snow" in q:
        return forecast.prob_snow() or 0.50

    # Fallback — середнє по consensus
    return 0.50

def calculate_edge(market: PolyMarket) -> EdgeResult:
    city = market.detected_city or "unknown"
    market_prob = market.midpoint_yes

    forecast = get_multi_source_consensus(city) if city != "unknown" else None
    if not forecast:
        forecast = get_best_forecast(city)

    estimated_prob = estimate_market_probability(market, forecast) if forecast else 0.50

    edge_yes = estimated_prob - market_prob
    edge_no = (1 - estimated_prob) - (1 - market_prob)
    confidence = _confidence_from_sources(forecast) if forecast else 0.70

    effective_edge = max(abs(edge_yes), abs(edge_no)) * confidence

    # ── EXTREME TAIL BOOST (mahera777 стиль) ─────────────────
    is_extreme_tail = (config.ENABLE_EXTREME_TAIL and
                       market.detected_city in config.EXTREME_TAIL_CITIES and
                       market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES and
                       edge_yes > 0)

    if is_extreme_tail:
        effective_edge = max(effective_edge, edge_yes * 1.50)   # +50% буст для 1–5¢
        confidence = min(0.98, confidence + 0.12)
        reason = f"EXTREME TAIL YES @ {market.best_ask_yes:.3f}¢ (потенціал ×20–100) | {forecast.source if forecast else 'unknown'}"
    else:
        reason = (f"YES: наша P={estimated_prob:.3f} > ринок {market_prob:.3f}" if edge_yes >= edge_no else
                  f"NO: наша P={estimated_prob:.3f} < ринок {market_prob:.3f}") + f" | {forecast.source if forecast else 'unknown'}"

    if effective_edge < config.MIN_EDGE_ENTRY:
        direction = "SKIP"
        is_tradeable = False
    elif edge_yes >= edge_no and (not is_extreme_tail or market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES):
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

# ─────────────────────────────────────────────────────────────
# НОВА ФУНКЦІЯ — критична для main.py
# ─────────────────────────────────────────────────────────────
def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    """Сканує всі ринки, фільтрує low-volume та повертає тільки tradeable edge (mahera777 стиль)"""
    results = []
    for market in markets:
        if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
            continue
        # Жорсткий фільтр extreme tail (як у оригінального трейдера)
        if config.ENABLE_EXTREME_TAIL and market.best_ask_yes > config.EXTREME_TAIL_MAX_ASK_YES:
            continue
        edge = calculate_edge(market)
        if edge.is_tradeable:
            results.append(edge)
    logger.info(f"✅ Знайдено {len(results)} крайніх tail-можливостей з {len(markets)} ринків")
    return results
