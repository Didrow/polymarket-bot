"""
edge_calculator.py — Polymarket Weather Bot 2026 (@coldmath style)

ВИПРАВЛЕНО 4 критичних баги:
  BUG-1: market.get("title") → AttributeError на PolyMarket dataclass → 0 угод завжди
  BUG-2: prob_above_temp_c формула ОБЕРНЕНА → при temp=20,thr=18 давала 0.246 замість 0.788
  BUG-3: MIN_CONFIDENCE=0.88 блокував London/Paris/Berlin (max 0.82) → 0 угод
  BUG-4: threshold хардкодований 18.0 для всіх ринків → ігнорував реальний поріг
"""

import math
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
        return (f"{self.edge_direction} | edge={self.edge_pct} | "
                f"our_prob={self.estimated_prob:.2f} | market={self.market_prob:.2f} | "
                f"{self.reason}")


def _confidence_from_forecast(forecast: Optional[WeatherForecast]) -> float:
    if not forecast or not forecast.sources_used:
        return 0.65
    sources = forecast.sources_used
    if "NOAA" in sources and len(sources) >= 2:
        return 0.95
    if "NOAA" in sources:
        return 0.90
    if "NASA_POWER" in sources and any("GFS" in s or "ECMWF" in s for s in sources):
        return 0.85
    if any("GFS" in s for s in sources):
        return 0.82
    if any("ECMWF" in s for s in sources):
        return 0.82
    if "NASA_POWER" in sources:
        return 0.78
    return 0.72


def estimate_market_probability(market: PolyMarket, forecast: WeatherForecast) -> float:
    """
    BUG-1 FIX: market — PolyMarket dataclass, використовуємо market.question/.threshold_value
    BUG-2 FIX: нормальний розподіл замість зворотної sigmoid-формули
    BUG-4 FIX: threshold береться з market.threshold_value, не хардкод 18.0
    """
    if not forecast:
        return 0.50

    threshold = market.threshold_value
    is_above = market.is_above
    mtype = market.market_type

    if mtype == "temperature" and threshold is not None:
        if is_above is True:
            return forecast.prob_above_temp_c(threshold)
        elif is_above is False:
            return forecast.prob_below_temp_c(threshold)
        else:
            # Categorical: "Will temp be EXACTLY X°C?"
            tc = forecast.temp_high_c
            if tc == 0.0:
                return 0.50
            sigma = 2.0
            # Ймовірність потрапити в бін [threshold-0.5, threshold+0.5]
            from math import erf, sqrt
            p = (0.5 * (1 + erf((threshold + 0.5 - tc) / (sigma * sqrt(2)))) -
                 0.5 * (1 + erf((threshold - 0.5 - tc) / (sigma * sqrt(2)))))
            return max(0.02, min(0.98, round(p, 4)))

    if mtype == "rain":
        return max(0.05, min(0.95, forecast.prob_rain_or_snow()))
    if mtype == "snow":
        return max(0.03, min(0.95, float(forecast.prob_snow or 0.1)))

    return 0.50


def calculate_edge(market: PolyMarket) -> Optional[EdgeResult]:
    """
    BUG-1 FIX: city = market.detected_city (НЕ market.get("title"))
    BUG-3 FIX: видалено MIN_CONFIDENCE фільтр — тепер впевненість масштабує позицію
    """
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
    edge_no  = (1 - our_prob) - (1 - market_prob)
    eff_yes  = edge_yes * confidence
    eff_no   = edge_no  * confidence

    src_info = "+".join(forecast.sources_used[:2]) if forecast.sources_used else "?"
    fc_info  = f"{forecast.temp_high_c:.1f}C" if forecast.temp_high_c else "?"

    no_price = 1.0 - market_prob

    # @coldmath: BUY NO @ 93-99¢
    is_coldmath = (
        config.ENABLE_COLDMATH_TAIL_NO
        and no_price >= config.COLDMATH_MIN_ASK_NO
        and no_price <= config.COLDMATH_MAX_ASK_NO
        and eff_no > config.COLDMATH_MIN_EDGE_NO
        and market.volume_usd >= config.MIN_MARKET_VOLUME_USD
    )

    # Extreme tail YES @ 1-5¢
    is_extreme = (
        config.ENABLE_EXTREME_TAIL_YES
        and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES
        and eff_yes >= config.EXTREME_TAIL_MIN_EDGE_YES
        and market.volume_usd >= 1000
    )

    # Шум: ціна < 1¢ або > 99¢ та мало volume
    if (market.midpoint_yes < 0.01 or market.midpoint_yes > 0.99) and market.volume_usd < 2500:
        return None

    if is_coldmath:
        direction = "BUY_NO"
        eff_edge  = abs(eff_no) * 1.35
        size_usd  = min(config.COLDMATH_MAX_SIZE_USD,
                        max(config.MIN_POSITION_USD, config.BASE_POSITION_USD * confidence))
        reason    = f"COLDMATH NO @ {no_price:.3f} | {src_info} | прогноз:{fc_info}"
        tradeable = True

    elif is_extreme:
        direction = "BUY_YES"
        eff_edge  = abs(eff_yes) * 1.30
        size_usd  = min(config.EXTREME_TAIL_MAX_SIZE_USD,
                        max(config.MIN_POSITION_USD, 2.0))
        reason    = f"EXTREME YES @ {market.best_ask_yes:.3f} | {src_info} | прогноз:{fc_info}"
        tradeable = True

    elif abs(eff_yes) >= config.MIN_EDGE_ENTRY or abs(eff_no) >= config.MIN_EDGE_ENTRY:
        if eff_yes >= eff_no:
            direction = "BUY_YES"
            eff_edge  = eff_yes
            reason    = f"YES {our_prob:.2f} vs {market_prob:.2f} | {src_info} | прогноз:{fc_info}"
        else:
            direction = "BUY_NO"
            eff_edge  = eff_no
            reason    = f"NO {our_prob:.2f} vs {market_prob:.2f} | {src_info} | прогноз:{fc_info}"
        size_usd  = max(config.MIN_POSITION_USD,
                        min(config.BASE_POSITION_USD * confidence * 1.2,
                            config.INITIAL_CAPITAL * config.MAX_POSITION_PCT))
        tradeable = True

    else:
        direction = "SKIP"
        eff_edge  = max(abs(eff_yes), abs(eff_no))
        reason    = f"edge={eff_edge:.1%} < {config.MIN_EDGE_ENTRY:.0%} | {src_info}"
        size_usd  = 0.0
        tradeable = False

    return EdgeResult(
        market=market, forecast=forecast,
        estimated_prob=our_prob, market_prob=market_prob,
        edge=eff_edge, edge_direction=direction,
        confidence=confidence, reason=reason,
        is_tradeable=tradeable, size_usd=round(size_usd, 2),
    )


def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    skip_vol = skip_city = 0
    for market in markets:
        if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
            skip_vol += 1
            continue
        edge = calculate_edge(market)
        if edge is None:
            skip_city += 1
            continue
        if edge.is_tradeable:
            logger.info(f"✅ EDGE: {edge.summary}")
            results.append(edge)
        else:
            logger.debug(f"⏭  SKIP: {edge.reason} | {market.question[:45]}")

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} ринків "
        f"| skip vol={skip_vol} | skip city/fc={skip_city}"
    )
    return results
