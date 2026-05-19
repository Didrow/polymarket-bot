"""
edge_calculator.py — Polymarket Weather Bot (GRID / YES LADDERING EDITION)

Адаптовано під стратегію "fridius2":
- Повністю вимкнено пошук угод BUY_NO.
- Агресивний пошук дешевих BUY_YES (до 12 центів) через Ансамблеві ймовірності.
- Купівля сусідніх температур для створення "Рибальської сітки" навколо прогнозу.
"""

import math
import re
import logging
from typing import Optional, List, Tuple
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
    edge_direction: str      # Тепер завжди "BUY_YES"
    confidence: float
    reason: str
    is_tradeable: bool
    size_usd: float = 0.0
    threshold_c: float = 0.0

    @property
    def edge_pct(self) -> str:
        return f"{self.edge:.1%}"

    @property
    def summary(self) -> str:
        return (
            f"{self.edge_direction} | edge={self.edge_pct} | "
            f"our_prob={self.estimated_prob:.2f} | market={self.market_prob:.2f} | "
            f"{self.reason}"
        )


# ══════════════════════════════════════════════════════════════════
# ПАРСИНГ ПОРОГУ
# ══════════════════════════════════════════════════════════════════

def _parse_threshold_with_unit(question: str) -> Tuple[Optional[float], str]:
    m = re.search(r'(\d+\.?\d*)\s*°?\s*F\b', question)
    if m: return float(m.group(1)), 'F'

    m = re.search(r'(\d+\.?\d*)\s*°?\s*C\b', question)
    if m: return float(m.group(1)), 'C'

    fahrenheit_cities = {"chicago", "dallas", "nyc", "new york", "san francisco",
                         "miami", "los angeles", "seattle", "atlanta", "boston",
                         "denver", "phoenix", "las vegas", "austin", "minneapolis",
                         "portland", "houston", "nashville", "charlotte", "orlando"}
    q_lower = question.lower()
    is_fahrenheit_city = any(city in q_lower for city in fahrenheit_cities)

    m = re.search(r'be (\d+\.?\d*)', question)
    if m:
        val = float(m.group(1))
        if val > 40 and is_fahrenheit_city:
            return val, 'F'
        if val > 50:
            return val, 'F'
        return val, 'C'

    return None, 'C'


def _f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


def _detect_market_kind(question: str) -> str:
    q = question.lower()
    if "or higher" in q or "or above" in q or "above" in q or "exceed" in q:
        return "above"
    if "or below" in q or "or lower" in q or "below" in q or "under" in q or "or fewer" in q:
        return "below"
    return "categorical"


# ══════════════════════════════════════════════════════════════════
# CONFIDENCE (Впевненість)
# ══════════════════════════════════════════════════════════════════

def _confidence_from_forecast(forecast: Optional[WeatherForecast]) -> float:
    if not forecast or not forecast.sources_used:
        return 0.60
    sources = forecast.sources_used
    
    if "Open-Meteo_ENSEMBLE" in sources or "ENSEMBLE" in sources:
        return 0.90
        
    n = len(sources)
    if "NOAA" in sources and n >= 2: return 0.95
    if "NOAA" in sources: return 0.90
    if any("GFS" in s for s in sources) and any("ECMWF" in s for s in sources): return 0.87
    return 0.75


# ══════════════════════════════════════════════════════════════════
# РОЗРАХУНОК ЙМОВІРНОСТІ
# ══════════════════════════════════════════════════════════════════

def estimate_market_probability(market: PolyMarket, forecast: WeatherForecast) -> Tuple[float, float, str]:
    if not forecast:
        return 0.50, 0.0, "no_forecast"

    raw_threshold, unit = _parse_threshold_with_unit(market.question)

    if raw_threshold is not None:
        if unit == 'F':
            threshold_c = _f_to_c(raw_threshold)
            unit_label = f"{raw_threshold:.0f}°F={threshold_c:.1f}°C"
        else:
            threshold_c = raw_threshold
            unit_label = f"{raw_threshold:.0f}°C"
    else:
        threshold_c = market.threshold_value or 0.0
        unit_label = f"{threshold_c:.0f}°C(fallback)"

    kind = _detect_market_kind(market.question)
    is_low = 'lowest' in market.question.lower()

    if kind == "above":
        p = forecast.prob_above_temp_c(threshold_c, is_low)
    elif kind == "below":
        p = forecast.prob_below_temp_c(threshold_c, is_low)
    else:
        # Categorical ринки — серце стратегії fridius2
        p = forecast.prob_exact_temp_c(threshold_c, is_low)

    return round(p, 4), threshold_c, f"{kind}|{unit_label}"


# ══════════════════════════════════════════════════════════════════
# ОБЧИСЛЕННЯ EDGE
# ══════════════════════════════════════════════════════════════════

def calculate_edge(market: PolyMarket) -> Optional[EdgeResult]:
    if market.market_type != "temperature":
        return None

    city = market.detected_city
    if not city or (hasattr(config, 'CITY_WHITELIST') and city not in config.CITY_WHITELIST):
        return None

    if market.hours_to_resolution < 1.5 or market.hours_to_resolution > config.MAX_RESOLUTION_HOURS:
        return None

    if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
        return None

    forecast = get_best_forecast(city, hours_to_resolution=market.hours_to_resolution)
    if not forecast:
        return None

    confidence = _confidence_from_forecast(forecast)
    our_prob, threshold_c, kind_label = estimate_market_probability(market, forecast)
    
    # Використовуємо ASK для розрахунку реальної вартості входу
    market_prob = market.best_ask_yes
    if market_prob <= 0.001 or market_prob >= 0.99:
        return None 

    kind = _detect_market_kind(market.question)
    is_low = 'lowest' in market.question.lower()
    tc = forecast.temp_low_c if is_low else forecast.temp_high_c
    fc_temp = f"{tc:.1f}°C"
    src = "ENSEMBLE" if (hasattr(forecast, 'temp_high_members') and forecast.temp_high_members) else "SINGLE"

    direction = "BUY_YES"
    # Ефективний edge з урахуванням впевненості моделі
    eff_edge = (our_prob - market_prob) * confidence

    # 🎣 СТРАТЕГІЯ 1: GRID YES (Ловимо дешеві хвости)
    # Мінімум 2¢: ринки по 0.3-1¢ — мертві (нема ліквідності, ніхто не купить).
    # fridius2 купує по 3-10¢, не по 0.3¢.
    GRID_MIN_PRICE = 0.02
    is_grid_yes = (
        kind == "categorical"
        and market_prob >= GRID_MIN_PRICE
        and market_prob <= config.EXTREME_TAIL_MAX_ASK_YES
        and our_prob >= 0.08
        and eff_edge >= config.EXTREME_TAIL_MIN_EDGE_YES
    )

    # 🎯 СТРАТЕГІЯ 2: SNIPER YES (Основний прогноз)
    is_sniper_yes = (
        eff_edge >= config.MIN_EDGE_ENTRY 
        and market_prob > config.EXTREME_TAIL_MAX_ASK_YES
    )

    if is_grid_yes:
        size_usd = max(config.MIN_POSITION_USD, min(config.EXTREME_TAIL_MAX_SIZE_USD, 4.0))
        reason = f"🎣 GRID YES @ {market_prob:.3f} | {kind_label} | our_prob={our_prob:.0%} | {src}"
    elif is_sniper_yes:
        size_usd = config.BASE_POSITION_USD
        reason = f"🎯 SNIPER YES @ {market_prob:.3f} | {kind_label} | our_prob={our_prob:.0%} | {src}"
    else:
        # Ми ПОВНІСТЮ ігноруємо BUY_NO в цій версії
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
        is_tradeable=True,
        size_usd=round(size_usd, 2),
        threshold_c=threshold_c,
    )


# ══════════════════════════════════════════════════════════════════
# СКАНУВАННЯ
# ══════════════════════════════════════════════════════════════════

def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    skip_vol = skip_city = skip_hours = skip_none = 0

    for market in markets:
        if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
            skip_vol += 1
            continue
        if market.hours_to_resolution < 1.5 or market.hours_to_resolution > config.MAX_RESOLUTION_HOURS:
            skip_hours += 1
            continue
        if market.detected_city and hasattr(config, 'CITY_WHITELIST') and config.CITY_WHITELIST:
            if market.detected_city not in config.CITY_WHITELIST:
                skip_city += 1
                continue

        edge = calculate_edge(market)
        if edge is None:
            skip_none += 1
            continue

        if edge.is_tradeable:
            logger.info(f"✅ EDGE: {edge.summary}")
            results.append(edge)

    # Сортування: Grid угоди мають пріоритет за потенціалом R:R
    results.sort(key=lambda r: r.edge, reverse=True)

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} ринків "
        f"| skip: vol={skip_vol}, hours={skip_hours}, city={skip_city}, none={skip_none}"
    )
    return results
