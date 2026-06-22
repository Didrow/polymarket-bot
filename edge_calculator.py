"""
edge_calculator.py — Polymarket Weather Bot (PROFITABLE SNIPER GRID v13)

Стратегія neobrother-style forecast ladder grid:
- BUY_YES only, focus on buckets NEAR the forecast peak (8-60¢)
- Grid of 3-5 adjacent temperature buckets (±1°C from forecast)
- Realistic probabilities via reduced over-calibration (v13)
- Quarter-Kelly position sizing for optimal bankroll growth
- Adaptive spread filter for liquid markets
- Distance filter for categorical markets (max 2.5°C from forecast)
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
    time_decay_factor: float = 1.0
    size_usd: float = 0.0
    threshold_c: float = 0.0
    distance_c: float = 0.0

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
# ПАРСИНГ ДІАПАЗОНІВ ТА ПОРОГІВ (оновлено для підтримки range)
# ══════════════════════════════════════════════════════════════════

def _unit_from_question(question: str) -> str:
    """Визначає одиницю температури з питання. США за замовчуванням — °F."""
    q = question.lower()
    explicit_units = re.findall(r'[-+]?\d+\.?\d*\s*°?\s*([cf])\b', q)
    if explicit_units:
        return 'F' if explicit_units[0] == 'f' else 'C'
    if 'fahrenheit' in q:
        return 'F'
    if 'celsius' in q or 'centigrade' in q:
        return 'C'

    fahrenheit_cities = {"chicago", "dallas", "nyc", "new york", "san francisco",
                         "miami", "los angeles", "seattle", "atlanta", "boston",
                         "denver", "phoenix", "las vegas", "austin", "minneapolis",
                         "portland", "houston", "nashville", "charlotte", "orlando"}
    if any(city in q for city in fahrenheit_cities):
        return 'F'
    return 'C'


def _f_delta_to_c(delta_f: float) -> float:
    """Converts a Fahrenheit temperature difference to Celsius."""
    return delta_f * 5.0 / 9.0


def _strip_temperature_conversions(question: str) -> str:
    """Прибирає конверсії типу 83°F=28.3°C, щоб вони не парсились як range."""
    return re.sub(r'=\s*[-+]?\d+\.?\d*\s*°?\s*[cf]\b', '', question, flags=re.IGNORECASE)


def _parse_range_or_threshold(question: str) -> Tuple[str, Optional[float], Optional[float], str]:
    """
    Парсить запитання на предмет діапазонів або одинарних порогів температур.
    Повертає: (kind, val_min, val_max, unit)
    """
    q_lower = _strip_temperature_conversions(question).lower()
    unit = _unit_from_question(question)

    # 1. Діапазони: "between 16 and 18°C", "16-18°C", "16°C to 18°C".
    range_match = re.search(
        r'(?:between\s+)?([-+]?\d+\.?\d*)\s*°?\s*[cf]\s*(?:-|–|to|and)\s*([-+]?\d+\.?\d*)\s*°?\s*[cf]',
        q_lower,
    ) or re.search(
        r'(?:between\s+)?([-+]?\d+\.?\d*)\s*[-–]\s*([-+]?\d+\.?\d*)\s*°?\s*[cf]\b',
        q_lower,
    )
    if range_match:
        try:
            val1 = float(range_match.group(1))
            val2 = float(range_match.group(2))
            if abs(val1) < 120 and abs(val2) < 120 and val1 != val2:
                val_min = min(val1, val2)
                val_max = max(val1, val2)
                return "range", val_min, val_max, unit
        except ValueError:
            pass

    # 2. Одинарні пороги
    kind = "categorical"
    if any(w in q_lower for w in ["or higher", "or above", "above", "exceed"]):
        kind = "above"
    elif any(w in q_lower for w in ["or below", "or lower", "below", "under"]):
        kind = "below"

    # Парсинг одинарного значення з явною одиницею.
    m = re.search(r'([-+]?\d+\.?\d*)\s*°\s*([FfCc])\b', q_lower)
    if m:
        return kind, float(m.group(1)), None, 'F' if m.group(2).lower() == 'f' else 'C'

    m = re.search(r'([-+]?\d+\.?\d*)\s*([FfCc])\b', q_lower)
    if m:
        return kind, float(m.group(1)), None, 'F' if m.group(2).lower() == 'f' else 'C'

    m = re.search(r'\bbe\s+([-+]?\d+\.?\d*)', q_lower)
    if m:
        return kind, float(m.group(1)), None, unit

    m = re.search(r'\b(?:above|below|exceed|over|under)\s+([-+]?\d+\.?\d*)', q_lower)
    if m:
        return kind, float(m.group(1)), None, unit

    return kind, None, None, unit


def _f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


def _detect_market_kind(question: str) -> str:
    q = question.lower()
    if "or higher" in q or "or above" in q or "above" in q or "exceed" in q:
        return "above"
    if "or below" in q or "or lower" in q or "below" in q or "under" in q or "or fewer" in q:
        return "below"
    return "categorical"


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


def _time_decay_for_hours(hours: float) -> float:
    if hours <= 12.0:
        return getattr(config, "PROB_TIME_DECAY_SHORT", 1.00)
    if hours <= 24.0:
        return getattr(config, "PROB_TIME_DECAY_MID", 0.95)
    return getattr(config, "PROB_TIME_DECAY_LONG", 0.90)


# ══════════════════════════════════════════════════════════════════
# РОЗРАХУНОК ЙМОВІРНОСТІ (з підтримкою діапазонів)
# ══════════════════════════════════════════════════════════════════

def estimate_market_probability(market: PolyMarket, forecast: WeatherForecast) -> Tuple[float, float, str]:
    if not forecast:
        return 0.50, 0.0, "no_forecast"

    hours = market.hours_to_resolution
    kind, val_min, val_max, unit = _parse_range_or_threshold(market.question)
    is_low = 'lowest' in market.question.lower()

    if kind == "range":
        mean_c = forecast.temp_low_c if is_low else forecast.temp_high_c
        members = forecast._get_adjusted_members(is_low)

        if unit == 'F':
            # Конвертуємо F-межі в C для узгодженого Gaussian
            t_min_c = _f_to_c(val_min - 0.5)
            t_max_c = _f_to_c(val_max + 0.5)
            label = f"range|{val_min}-{val_max}°F"
            threshold_c = _f_to_c((val_min + val_max) / 2)
        else:
            t_min_c = val_min - 0.5
            t_max_c = val_max + 0.5
            label = f"range|{val_min}-{val_max}°C"
            threshold_c = (val_min + val_max) / 2

        # Використовуємо динамічну sigma конкретного міста та горизонту прогнозу
        sigma = forecast._get_sigma(hours)

        # Gaussian CDF (узгоджена з prob_above_temp_c / prob_exact_temp_c)
        p_high = 0.5 * (1 + math.erf((t_max_c - mean_c) / (sigma * math.sqrt(2))))
        p_low  = 0.5 * (1 + math.erf((t_min_c - mean_c) / (sigma * math.sqrt(2))))
        prob_parametric = max(0.0, p_high - p_low)

        # Кап для range (з config.py, спільний з prob_exact_temp_c)
        if hours <= 6.0:
            _max_r = config.PROB_CAP_EXACT_SHORT
        elif hours <= 18.0:
            _max_r = config.PROB_CAP_EXACT_MID
        else:
            _max_r = config.PROB_CAP_EXACT_LONG

        # v13: реалістичні ймовірності для range бакетів (сітка ±1°C)
        # Було 0.30/0.70*0.55 (discount 45%) — our_prob занадто малий.
        # Тепер 0.40/0.60*0.75 (discount 25%) — our_prob реалістичний для сітки.
        if members and len(members) >= 5:
            count_in = sum(1 for m in members if t_min_c <= m < t_max_c)
            prob_empirical = count_in / len(members)
            if prob_empirical == 0.0:
                p = prob_parametric * 0.50  # v13: 0.30→0.50
            else:
                p = (prob_empirical * 0.40 + prob_parametric * 0.60) * 0.75  # v13: 0.30/0.70*0.55 → 0.40/0.60*0.75
        else:
            p = prob_parametric * 0.75  # v13: 0.55→0.75

        return round(max(0.01, min(_max_r, p)), 4), threshold_c, label

    # Звичайний одинарний поріг
    if val_min is not None:
        if unit == 'F':
            threshold_c = _f_to_c(val_min)
            unit_label = f"{val_min:.0f}°F={threshold_c:.1f}°C"
        else:
            threshold_c = val_min
            unit_label = f"{val_min:.0f}°C"
    else:
        threshold_c = market.threshold_value or 0.0
        unit_label = f"{threshold_c:.0f}°C(fallback)"

    if kind == "above":
        p = forecast.prob_above_temp_c(threshold_c, is_low, hours=hours)
    elif kind == "below":
        p = forecast.prob_below_temp_c(threshold_c, is_low, hours=hours)
    else:
        half_width = 0.2778 if unit == 'F' else 0.5
        p = forecast.prob_exact_temp_c(threshold_c, is_low, half_width=half_width, hours=hours)

    return round(p, 4), threshold_c, f"{kind}|{unit_label}"


def _calibrated_probability(
    raw_prob: float,
    kind: str,
    hours: float,
    distance_c: Optional[float],
    unit: str,
    confidence: float,
) -> float:
    if not getattr(config, "PROBABILITY_CALIBRATION_ENABLED", True):
        return raw_prob

    p = max(0.0, min(1.0, raw_prob))

    if kind in {"above", "below"}:
        p *= getattr(config, "PROB_THRESHOLD_CALIBRATION_SCALE", 0.85)
    elif kind == "range":
        p *= getattr(config, "PROB_RANGE_CALIBRATION_SCALE", 0.85)
    else:
        p *= getattr(config, "PROB_EXACT_CALIBRATION_SCALE", 0.85)

    if distance_c is not None:
        scale = getattr(config, "PROB_DISTANCE_SCALE_C", 2.0)
        power = getattr(config, "PROB_DISTANCE_POWER", 0.5)
        p *= 1.0 / (1.0 + (abs(distance_c) / scale) ** power)

    p *= _time_decay_for_hours(hours)

    confidence_weight = getattr(config, "PROB_CONFIDENCE_WEIGHT", 0.25)
    p *= (1.0 - confidence_weight) + confidence_weight * confidence

    return round(max(0.01, min(0.95, p)), 4)


def _apply_probability_calibration(
    raw_prob: float,
    kind_label: str,
    hours: float,
    distance_c: Optional[float],
    unit: str,
    confidence: float,
) -> float:
    kind = kind_label.split("|")[0]
    return _calibrated_probability(
        raw_prob,
        kind,
        hours,
        distance_c,
        unit,
        confidence,
    )


def _distance_filter_ok(
    kind: str,
    threshold_c: Optional[float],
    fc_temp: Optional[float],
    unit: str,
    range_max_c: Optional[float] = None,
) -> Tuple[bool, Optional[float]]:
    if threshold_c is None or fc_temp is None:
        return True, None

    max_dist = _f_delta_to_c(getattr(config, "SNIPER_GRID_DISTANCE_F", 5.0)) if unit == 'F' else getattr(config, "SNIPER_GRID_DISTANCE_C", 2.5)
    if kind == "range" and range_max_c is not None:
        if threshold_c <= fc_temp <= range_max_c:
            return True, 0.0
        distance = min(abs(fc_temp - threshold_c), abs(fc_temp - range_max_c))
        return distance <= max_dist, distance

    distance = abs(fc_temp - threshold_c)
    if kind in {"categorical", "range"}:
        return distance <= max_dist, distance

    return True, distance


def _valid_yes_price(value: float, field_name: str) -> bool:
    return value is not None and math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0


def _log_price_validation(market: PolyMarket) -> None:
    ask = market.best_ask_yes
    bid = market.best_bid_yes
    mid = market.midpoint_yes
    if not (_valid_yes_price(ask, "ask") and _valid_yes_price(bid, "bid") and _valid_yes_price(mid, "mid")):
        logger.debug(
            f"⚠️ PRICE VALIDATION: {market.question[:60]} | "
            f"ask={ask} bid={bid} mid={mid} cid={market.condition_id[:12]}"
        )


def _is_extreme_tail_yes_market(market_prob: float) -> bool:
    return (
        getattr(config, "ENABLE_EXTREME_TAIL_YES", True)
        and market_prob >= getattr(config, "SNIPER_GRID_MIN_ASK", 0.08)
        and market_prob <= getattr(config, "EXTREME_TAIL_MAX_ASK_YES", 0.55)
    )


def _grid_min_edge_for_market(market_prob: float) -> float:
    if _is_extreme_tail_yes_market(market_prob):
        return getattr(config, "EXTREME_TAIL_MIN_EDGE_YES", getattr(config, "SNIPER_GRID_MIN_EDGE", 0.015))
    return getattr(config, "SNIPER_GRID_MIN_EDGE", 0.015)


def _grid_tradeable(
    kind: str,
    kind_label: str,
    market_prob: float,
    our_prob: float,
    eff_edge: float,
    dist_ok: bool,
    min_edge: Optional[float] = None,
    min_prob: Optional[float] = None,
) -> bool:
    grid_min_edge = min_edge if min_edge is not None else _grid_min_edge_for_market(market_prob)
    grid_min_prob = min_prob if min_prob is not None else getattr(config, "SNIPER_GRID_MIN_PROB", 0.05)
    return (
        dist_ok
        and market_prob >= getattr(config, "SNIPER_GRID_MIN_ASK", 0.01)
        and market_prob <= getattr(config, "SNIPER_GRID_MAX_ASK", getattr(config, "EXTREME_TAIL_MAX_ASK_YES", 0.75))
        and our_prob >= grid_min_prob
        and eff_edge >= grid_min_edge
    )


# ══════════════════════════════════════════════════════════════════
# ОБЧИСЛЕННЯ EDGE (з каліброваною ймовірністю та снайперською сіткою)
# ══════════════════════════════════════════════════════════════════

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

    from market_scanner import get_target_date
    t_date = get_target_date(market.question, market.end_date, city)
    forecast = get_best_forecast(city, hours_to_resolution=market.hours_to_resolution, target_date=t_date)
    if not forecast:
        return None

    confidence = _confidence_from_forecast(forecast)
    time_decay = _time_decay_for_hours(market.hours_to_resolution)
    our_prob, threshold_c, kind_label = estimate_market_probability(market, forecast)
    
    # Використовуємо ASK для розрахунку реальної вартості входу
    _log_price_validation(market)
    market_prob = market.best_ask_yes

    # Якщо ask підозріло малий (< 1¢) — пробуємо midpoint
    if market_prob < 0.01:
        midpoint = getattr(market, "midpoint_yes", 0.0)
        if 0.01 <= midpoint <= 0.99:
            logger.debug(f"💡 Ask={market_prob:.4f} < 1¢, fallback на midpoint={midpoint:.4f}")
            market_prob = midpoint
        else:
            logger.debug(f"⏭️ SKIP: ask={market_prob:.4f} замалий, midpoint={midpoint:.4f} невалідний")
            return None

    if market_prob <= 0.001 or market_prob >= 0.99:
        return None

    kind = _detect_market_kind(market.question)
    is_low = 'lowest' in market.question.lower()
    fc_temp = forecast.temp_low_c if is_low else forecast.temp_high_c   # float
    src = "ENSEMBLE" if (hasattr(forecast, 'temp_high_members') and forecast.temp_high_members) else "SINGLE"

    direction = "BUY_YES"
    # Edge = різниця між нашою ймовірністю та ціною ринку
    # НЕ множимо на confidence — це має впливати на РОЗМІР позиції, а не на фільтрацію
    raw_edge = our_prob - market_prob
    # Cap edge щоб запобігти хибним сигналам від METAR artifacts
    eff_edge = min(raw_edge, getattr(config, 'MAX_EDGE_CAP', 0.75))

    kind, val_min, val_max, unit = _parse_range_or_threshold(market.question)
    range_max_c = _f_to_c(val_max) if kind == "range" and val_max is not None and unit == 'F' else val_max
    dist_ok, distance_c = _distance_filter_ok(kind, threshold_c, fc_temp, unit, range_max_c)
    if not dist_ok:
        logger.debug(
            f"Пропускаємо поза сіткою: прогноз={fc_temp:.1f}°C, "
            f"ціль={threshold_c:.1f}°C, різниця={distance_c:.1f}°C ({unit})"
        )

    our_prob = _apply_probability_calibration(
        our_prob,
        kind_label,
        market.hours_to_resolution,
        distance_c,
        unit,
        confidence,
    )
    raw_edge = our_prob - market_prob
    eff_edge = min(raw_edge, getattr(config, "MAX_EDGE_CAP", 0.75))

    grid_min_edge = _grid_min_edge_for_market(market_prob)
    tradeable = _grid_tradeable(kind, kind_label, market_prob, our_prob, eff_edge, dist_ok, min_edge=grid_min_edge)

    if tradeable:
        # ── v13: Kelly position sizing ──
        if getattr(config, "USE_KELLY", False) and our_prob > 0 and market_prob > 0:
            kelly_fraction = (our_prob - market_prob) / (1 - market_prob)
            kelly_fraction = max(0, min(kelly_fraction, 1.0))
            kelly_scale = getattr(config, "KELLY_SCALE", 0.25)
            size_usd = round(kelly_fraction * kelly_scale * config.INITIAL_CAPITAL, 2)
            size_usd = max(config.MIN_POSITION_USD, min(size_usd, getattr(config, "KELLY_MAX_POSITION_USD", config.MAX_POSITION_USD)))
            # Для пікових бакетів (ближче до прогнозу) — більший розмір
            if distance_c is not None and distance_c < 1.0:
                size_usd = min(size_usd * 1.3, config.MAX_POSITION_USD)
        else:
            size_usd = min(config.MAX_POSITION_USD, config.EXTREME_TAIL_MAX_SIZE_USD)
            if "categorical" in kind_label or "range" in kind_label:
                size_usd = min(size_usd, config.SNIPER_GRID_SIZE_USD)
        reason = (
            f"SNIPER GRID YES @ {market_prob:.3f} | {kind_label} | "
            f"our_prob={our_prob:.0%} | dist={distance_c:.1f}°C | decay={time_decay:.2f} | {src}"
        )
    elif eff_edge >= config.MIN_EDGE_ENTRY and market_prob > config.EXTREME_TAIL_MAX_ASK_YES and our_prob >= config.MIN_PROB_ENTRY:
        size_usd = config.BASE_POSITION_USD
        reason = f"SNIPER YES @ {market_prob:.3f} | {kind_label} | our_prob={our_prob:.0%} | decay={time_decay:.2f} | {src}"
    else:
        logger.debug(
            f"⏭️ SKIP: {market.question[:55]} | ask={market_prob:.3f} | "
            f"our_prob={our_prob:.2f} | edge={eff_edge:.1%} | "
            f"min_edge={grid_min_edge:.1%} | dist_ok={dist_ok} | kind={kind_label}"
        )
        return None

    return EdgeResult(
        market=market,
        forecast=forecast,
        estimated_prob=our_prob,
        market_prob=market_prob,
        edge=eff_edge,
        edge_direction=direction,
        confidence=confidence,
        time_decay_factor=time_decay,
        reason=reason,
        is_tradeable=True,
        size_usd=round(size_usd, 2),
        threshold_c=threshold_c,
        distance_c=distance_c or 0.0,
    )


# ══════════════════════════════════════════════════════════════════
# СКАНУВАННЯ
# ══════════════════════════════════════════════════════════════════

def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    skip_vol = skip_city = skip_hours = skip_none = skip_spread = 0

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
        # ✅ v5 FIX: spread filter адаптований під GRID YES
        # Для tail-ринків (ask ≤ 15¢) bid=0 — це норма, spread = ask
        # Тому для них дозволяємо spread до ask (тобто весь spread)
        spread = market.best_ask_yes - market.best_bid_yes
        if market.best_ask_yes <= 0.15:
            max_spread = max(0.10, market.best_ask_yes)
        elif market.best_ask_yes <= getattr(config, "SNIPER_GRID_MAX_ASK", 0.75):
            max_spread = max(0.08, market.best_ask_yes * 0.25)
        else:
            max_spread = 0.05
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

    # 🎯 Другий прохід: пошук Adjacent Grid (сусідніх бакетів для SNIPER)
    if getattr(config, 'ENABLE_ADJACENT_GRID', False):
        sniper_results = [r for r in results if "SNIPER" in r.reason]
        for sniper in sniper_results:
            city = sniper.market.detected_city
            end_date = sniper.market.end_date
            threshold = sniper.threshold_c
            
            for market in markets:
                if market.detected_city != city: continue
                if abs((market.end_date - end_date).total_seconds()) > 7200: continue
                if any(r.market.condition_id == market.condition_id for r in results): continue
                
                if market.volume_usd < config.MIN_MARKET_VOLUME_USD: continue
                if market.best_ask_yes > config.ADJACENT_GRID_MAX_ASK: continue
                
                kind, val_min, val_max, unit = _parse_range_or_threshold(market.question)
                if val_min is None:
                    continue
                adj_threshold = _f_to_c(val_min) if unit == 'F' else val_min
                adj_range_max_c = _f_to_c(val_max) if kind == "range" and val_max is not None and unit == 'F' else val_max
                if kind == "range" and val_max is not None:
                    adj_threshold = _f_to_c((val_min + val_max) / 2) if unit == 'F' else (val_min + val_max) / 2
                max_dist_adj = _f_delta_to_c(config.SNIPER_GRID_DISTANCE_F) if unit == 'F' else config.SNIPER_GRID_DISTANCE_C
                if 0 < abs(adj_threshold - threshold) <= max_dist_adj:
                    from market_scanner import get_target_date
                    adj_t_date = get_target_date(market.question, market.end_date, city)
                    forecast = get_best_forecast(city, hours_to_resolution=market.hours_to_resolution, target_date=adj_t_date)
                    if not forecast: continue
                    
                    our_prob, adj_th_c, adj_kind_label = estimate_market_probability(market, forecast)
                    confidence = _confidence_from_forecast(forecast)
                    time_decay = _time_decay_for_hours(market.hours_to_resolution)
                    _log_price_validation(market)
                    market_prob = market.best_ask_yes
                    if market_prob < 0.01:
                        midpoint = getattr(market, "midpoint_yes", 0.0)
                        if 0.01 <= midpoint <= 0.99:
                            logger.debug(f"💡 Adjacent Ask={market_prob:.4f} < 1¢, fallback на midpoint={midpoint:.4f}")
                            market_prob = midpoint
                        else:
                            logger.debug(f"⏭️ SKIP Adjacent: ask={market_prob:.4f} замалий, midpoint={midpoint:.4f} невалідний")
                            continue

                    if market_prob <= 0.001 or market_prob >= 0.99:
                        continue
                    adj_kind, adj_val_min, adj_val_max, adj_unit = _parse_range_or_threshold(market.question)
                    adj_is_low = 'lowest' in market.question.lower()
                    adj_fc_temp = forecast.temp_low_c if adj_is_low else forecast.temp_high_c
                    adj_range_max_c = _f_to_c(adj_val_max) if adj_kind == "range" and adj_val_max is not None and adj_unit == 'F' else adj_val_max
                    adj_dist_ok, adj_distance_c = _distance_filter_ok(adj_kind, adj_th_c, adj_fc_temp, adj_unit, adj_range_max_c)
                    our_prob = _apply_probability_calibration(
                        our_prob,
                        adj_kind_label,
                        market.hours_to_resolution,
                        adj_distance_c,
                        adj_unit,
                        confidence,
                    )
                    if not adj_dist_ok:
                        continue
                    if our_prob < getattr(config, "ADJACENT_GRID_MIN_PROB", getattr(config, "SNIPER_GRID_MIN_PROB", 0.05)):
                        continue
                    raw_edge = our_prob - market_prob
                    eff_edge = min(raw_edge, getattr(config, "MAX_EDGE_CAP", 0.75))
                    
                    if eff_edge < getattr(config, "ADJACENT_GRID_MIN_EDGE", getattr(config, "SNIPER_GRID_MIN_EDGE", 0.01)):
                        continue
                    if _grid_tradeable(
                        adj_kind,
                        adj_kind_label,
                        market_prob,
                        our_prob,
                        eff_edge,
                        adj_dist_ok,
                        min_edge=getattr(config, "ADJACENT_GRID_MIN_EDGE", getattr(config, "SNIPER_GRID_MIN_EDGE", 0.03)),
                        min_prob=getattr(config, "ADJACENT_GRID_MIN_PROB", getattr(config, "SNIPER_GRID_MIN_PROB", 0.06)),
                    ):
                        # ── v13: Kelly sizing for adjacent grid buckets ──
                        if getattr(config, "USE_KELLY", False) and our_prob > 0 and market_prob > 0:
                            kelly_fraction = (our_prob - market_prob) / (1 - market_prob)
                            kelly_fraction = max(0, min(kelly_fraction, 1.0))
                            kelly_scale = getattr(config, "KELLY_SCALE", 0.25)
                            adj_size_usd = round(kelly_fraction * kelly_scale * config.INITIAL_CAPITAL * 0.70, 2)
                            adj_size_usd = max(config.MIN_POSITION_USD, min(adj_size_usd, getattr(config, "KELLY_MAX_POSITION_USD", config.MAX_POSITION_USD)))
                        else:
                            adj_size_usd = config.ADJACENT_GRID_SIZE_USD
                        edge = EdgeResult(
                            market=market,
                            forecast=forecast,
                            estimated_prob=our_prob,
                            market_prob=market_prob,
                            edge=eff_edge,
                            edge_direction="BUY_YES",
                            confidence=confidence,
                            reason=f"🎯 ADJACENT GRID YES @ {market_prob:.3f} | {adj_kind_label} | our_prob={our_prob:.0%} | dist={adj_distance_c:.1f}°C | decay={time_decay:.2f}",
                            is_tradeable=True,
                            size_usd=adj_size_usd,
                            threshold_c=adj_th_c,
                            distance_c=adj_distance_c or 0.0,
                            time_decay_factor=time_decay,
                        )
                        logger.info(f"✅ EDGE: {edge.summary}")
                        results.append(edge)

    # Сортування: спершу edge, потім ближчі до прогнозу grid-ринки.
    def sort_key(r: EdgeResult):
        grid_distance = abs(r.distance_c) if "GRID" in r.reason else 0.0
        return (r.edge, -grid_distance)

    results.sort(key=sort_key, reverse=True)

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} ринків "
        f"| skip: vol={skip_vol}, hours={skip_hours}, city={skip_city}, spread={skip_spread}, none={skip_none}"
    )
    return results
