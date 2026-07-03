"""
edge_calculator.py — Weather Prediction Bot v20 (CLEAN ENSEMBLE)

Стратегія:
- Ensemble forecast (31 member GFS) → наша ймовірність
- Купуємо YES коли our_prob > market_price + edge
- Купуємо NO  коли market_price > our_prob + edge
- Без METAR, без range, без categorical
"""

import math
import re
import logging
from datetime import datetime, timezone
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
    edge_direction: str  # "BUY_YES" or "BUY_NO"
    confidence: float
    reason: str
    is_tradeable: bool
    size_usd: float = 0.0
    threshold_c: float = 0.0
    distance_c: float = 0.0
    kind: str = ""

    @property
    def edge_pct(self) -> str:
        return f"{self.edge:.1%}"

    @property
    def summary(self) -> str:
        return (
            f"{self.edge_direction} | edge={self.edge:.1%} | "
            f"our_prob={self.estimated_prob:.2f} | market={self.market_prob:.2f} | "
            f"threshold={self.threshold_c:.1f}°C | {self.reason}"
        )


# ── PARSING ─────────────────────────────────────────────────
def _unit_from_question(question: str) -> str:
    q = question.lower()
    explicit = re.findall(r'[-+]?\d+\.?\d*\s*°?\s*([cf])\b', q)
    if explicit:
        return 'F' if explicit[0] == 'f' else 'C'
    if 'fahrenheit' in q:
        return 'F'
    if 'celsius' in q or 'centigrade' in q:
        return 'C'
    us_cities = {"chicago", "dallas", "nyc", "new york", "miami",
                 "los angeles", "seattle", "atlanta", "boston",
                 "denver", "phoenix", "las vegas", "austin",
                 "minneapolis", "portland", "houston", "nashville",
                 "charlotte", "orlando", "san francisco"}
    if any(c in q for c in us_cities):
        return 'F'
    return 'C'


def _f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


def _detect_market_kind(question: str) -> str:
    q = question.lower()
    if any(w in q for w in ["or higher", "or above", "above", "exceed"]):
        return "above"
    if any(w in q for w in ["or below", "or lower", "below", "under", "or fewer"]):
        return "below"
    return "categorical"


def _parse_threshold(question: str) -> Tuple[str, Optional[float], str]:
    """Повертає (kind, threshold_value, unit)."""
    kind = _detect_market_kind(question)
    unit = _unit_from_question(question)
    q_lower = question.lower()

    m = re.search(r'([-+]?\d+\.?\d*)\s*°\s*([FfCc])\b', q_lower)
    if m:
        return kind, float(m.group(1)), 'F' if m.group(2).lower() == 'f' else 'C'

    m = re.search(r'([-+]?\d+\.?\d*)\s*([FfCc])\b', q_lower)
    if m:
        return kind, float(m.group(1)), 'F' if m.group(2).lower() == 'f' else 'C'

    m = re.search(r'(?:above|below|exceed|over|under)\s+([-+]?\d+\.?\d*)', q_lower)
    if m:
        return kind, float(m.group(1)), unit

    return kind, None, unit


# ── PROBABILITY ─────────────────────────────────────────────

def _get_cap(hours: float) -> float:
    if hours <= 6.0:
        return getattr(config, 'CAP_SHORT', 0.85)
    elif hours <= 18.0:
        return getattr(config, 'CAP_MID', 0.75)
    return getattr(config, 'CAP_LONG', 0.65)


def calculate_our_probability(forecast: WeatherForecast, threshold_c: float, kind: str,
                              is_low: bool, hours: float) -> float:
    """
    Розраховує ймовірність на основі ensemble forecast.
    Використовує емпіричний підрахунок (count members above/below threshold)
    змішаний з Gaussian parametric.
    """
    members = forecast._get_adjusted_members(is_low)
    sigma = forecast._get_sigma(hours)
    mean_c = forecast.temp_low_c if is_low else forecast.temp_high_c

    if members and len(members) >= 5:
        if kind == "above":
            count = sum(1 for m in members if m > threshold_c)
        else:
            count = sum(1 for m in members if m < threshold_c)
        prob_empirical = count / len(members)

        if kind == "above":
            prob_parametric = 0.5 * (1 + math.erf((mean_c - threshold_c) / (sigma * math.sqrt(2))))
        else:
            prob_parametric = 0.5 * (1 + math.erf((threshold_c - mean_c) / (sigma * math.sqrt(2))))

        emp_w = getattr(config, 'EMPRICIAL_WEIGHT', 0.30)
        par_w = getattr(config, 'ENSEMBLE_WEIGHT', 0.70)
        raw = prob_empirical * emp_w + prob_parametric * par_w
    else:
        if kind == "above":
            raw = 0.5 * (1 + math.erf((mean_c - threshold_c) / (sigma * math.sqrt(2))))
        else:
            raw = 0.5 * (1 + math.erf((threshold_c - mean_c) / (sigma * math.sqrt(2))))

    cap = _get_cap(hours)
    bias = getattr(config, 'PROB_BIAS', 0.75)
    p = raw * bias
    return max(0.01, min(cap, round(p, 4)))


def _confidence_from_forecast(forecast: Optional[WeatherForecast]) -> float:
    if not forecast or not forecast.sources_used:
        return 0.60
    sources = forecast.sources_used
    if "Open-Meteo_ENSEMBLE" in sources or "ENSEMBLE" in sources:
        return 0.85
    if "NOAA" in sources:
        return 0.80
    if "OBSERVED" in sources:
        return 0.90
    n = len(sources)
    if n >= 2:
        return 0.80
    return 0.70


# ── MAIN EDGE CALCULATION ──────────────────────────────────

def calculate_edge(market: PolyMarket) -> Optional[EdgeResult]:
    if market.market_type != "temperature":
        return None

    city = market.detected_city
    if not city or (hasattr(config, 'CITY_WHITELIST') and city not in config.CITY_WHITELIST):
        return None

    if market.hours_to_resolution < config.MIN_RESOLUTION_HOURS or market.hours_to_resolution > config.MAX_RESOLUTION_HOURS:
        return None

    if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
        return None

    kind = market.kind or _detect_market_kind(market.question)
    allowed = getattr(config, 'KINDS_ONLY', ['above', 'below'])
    if kind not in allowed:
        return None

    from market_scanner import get_target_date
    t_date = get_target_date(market.question, market.end_date, city)
    forecast = get_best_forecast(city, hours_to_resolution=market.hours_to_resolution, target_date=t_date)
    if not forecast:
        return None

    # Парсинг порогу
    parsed_kind, threshold_value, unit = _parse_threshold(market.question)
    kind = parsed_kind

    if kind not in ("above", "below"):
        return None

    is_low = 'lowest' in market.question.lower()

    # Конвертуємо threshold в °C
    if threshold_value is None:
        threshold_c = market.threshold_value or 0.0
    elif unit == 'F':
        threshold_c = _f_to_c(threshold_value)
    else:
        threshold_c = threshold_value

    # Розраховуємо нашу ймовірність
    our_prob = calculate_our_probability(
        forecast, threshold_c, kind, is_low, market.hours_to_resolution
    )

    # Ринкова ціна
    market_prob = market.best_ask_yes

    # Fallback для дешевих ринків: використовуємо midpoint
    if market_prob is None or market_prob < 0.01:
        midpoint = getattr(market, "midpoint_yes", 0.0)
        if 0.01 <= midpoint <= 0.99:
            market_prob = midpoint
        else:
            return None

    if market_prob <= 0.001 or market_prob >= 0.99:
        return None

    fc_temp = forecast.temp_low_c if is_low else forecast.temp_high_c
    distance_c = abs(fc_temp - threshold_c)

    # Розраховуємо edge для YES та NO
    edge_yes = our_prob - market_prob
    edge_no = market_prob - our_prob

    min_edge_yes = getattr(config, 'MIN_EDGE_YES', 0.20)
    min_edge_no = getattr(config, 'MIN_EDGE_NO', 0.20)
    min_prob = getattr(config, 'MIN_PROB_ENTRY', 0.10)
    max_edge = getattr(config, 'MAX_EDGE_CAP', 0.50)

    # Вибираємо напрямок
    if edge_yes >= min_edge_yes and our_prob >= min_prob:
        edge_direction = "BUY_YES"
        eff_edge = min(edge_yes, max_edge)
        reason = f"ENSEMBLE YES {kind.upper()} @ {market_prob:.3f}"
    elif edge_no >= min_edge_no and (1 - market_prob) >= min_prob:
        edge_direction = "BUY_NO"
        eff_edge = min(edge_no, max_edge)
        reason = f"ENSEMBLE NO {kind.upper()} @ {market_prob:.3f}"
    else:
        return None

    tradeable = True
    confidence = _confidence_from_forecast(forecast)

    if tradeable:
        logger.info(
            f"✅ EDGE: {market.question[:50]} | "
            f"our={our_prob:.0%} | mkt={market_prob:.0%} | edge={eff_edge:.1%} | "
            f"{edge_direction} | dist={distance_c:.1f}°C | {kind}"
        )

    return EdgeResult(
        market=market,
        forecast=forecast,
        estimated_prob=our_prob,
        market_prob=market_prob,
        edge=eff_edge,
        edge_direction=edge_direction,
        confidence=confidence,
        reason=reason,
        is_tradeable=tradeable,
        size_usd=0.0,
        threshold_c=threshold_c,
        distance_c=distance_c,
        kind=kind,
    )


# ── SCANNER ─────────────────────────────────────────────────

def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    skip_vol = skip_city = skip_hours = skip_none = skip_spread = skip_kind = 0

    for market in markets:
        if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
            skip_vol += 1
            continue
        if market.hours_to_resolution < config.MIN_RESOLUTION_HOURS or market.hours_to_resolution > config.MAX_RESOLUTION_HOURS:
            skip_hours += 1
            continue
        if market.detected_city and hasattr(config, 'CITY_WHITELIST') and config.CITY_WHITELIST:
            if market.detected_city not in config.CITY_WHITELIST:
                skip_city += 1
                continue

        kind = market.kind or _detect_market_kind(market.question)
        allowed = getattr(config, 'KINDS_ONLY', ['above', 'below'])
        if kind not in allowed:
            skip_kind += 1
            continue

        spread = market.best_ask_yes - market.best_bid_yes
        if market.best_ask_yes <= 0.15:
            max_spread = max(0.15, market.best_ask_yes)
        elif market.best_ask_yes <= 0.55:
            max_spread = 0.25
        else:
            max_spread = 0.06
        if spread > max_spread:
            skip_spread += 1
            continue

        edge = calculate_edge(market)
        if edge is None:
            skip_none += 1
            continue

        if edge.is_tradeable:
            logger.info(f"✅ EDGE: {edge.summary}")
            results.append(edge)

    results.sort(key=lambda r: r.edge, reverse=True)

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} markets "
        f"| skip: vol={skip_vol}, hours={skip_hours}, city={skip_city}, "
        f"kind={skip_kind}, spread={skip_spread}, none={skip_none}"
    )
    return results
