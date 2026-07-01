"""
edge_calculator.py — Polymarket Weather Bot v15 (METAR ARBITRAGE SNIPER)

 Стратегія: арбітраж запізнілого ринку
- Тільки above/below + range/categorical ринки де METAR/observed ПІДТВЕРДЖУЄ напрямок
  (METAR required — no high-prob fallback without METAR)
- Поточна температура вже вище/нижче порогу / в межах range → ринок ще не оновив ціну
- Короткий горизонт (≤12h): ринок ще не встиг переосмислити
- Вхід 15-70¢ (реальна ліквідність), edge ≥4%, win rate 55-70%
- Climate sanity filter: reject impossible temperatures for city/season
- Prob ratio check: reject when our_prob/market_prob ratio suspiciously high
"""

import math
import re
import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from dataclasses import dataclass

import config
from data_fetcher import WeatherForecast, get_best_forecast, fetch_metar, _fetch_observed_daily_extremes
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
    time_decay_factor: float = 1.0
    size_usd: float = 0.0
    threshold_c: float = 0.0
    distance_c: float = 0.0
    metar_temp_c: float = 0.0
    observed_high_c: float = 0.0

    @property
    def edge_pct(self) -> str:
        return f"{self.edge:.1%}"

    @property
    def summary(self) -> str:
        metar_str = f"metar={self.metar_temp_c:.1f}°C" if self.metar_temp_c else "no-metar"
        obs_str = f"obs_hi={self.observed_high_c:.1f}°C" if self.observed_high_c else ""
        return (
            f"{self.edge_direction} | edge={self.edge:.1%} | "
            f"our_prob={self.estimated_prob:.2f} | market={self.market_prob:.2f} | "
            f"{self.reason} | {metar_str} {obs_str}"
        )


# ══════════════════════════════════════════════════════════════════
# ПАРСИНГ ДІАПАЗОНІВ ТА ПОРОГІВ
# ══════════════════════════════════════════════════════════════════

def _unit_from_question(question: str) -> str:
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
    return delta_f * 5.0 / 9.0


def _strip_temperature_conversions(question: str) -> str:
    return re.sub(r'=\s*[-+]?\d+\.?\d*\s*°?\s*[cf]\b', '', question, flags=re.IGNORECASE)


def _parse_range_or_threshold(question: str) -> Tuple[str, Optional[float], Optional[float], str]:
    q_lower = _strip_temperature_conversions(question).lower()
    unit = _unit_from_question(question)

    range_match = re.search(
        r'(?:between\s+)?([-+]?\d+\.?\d*)\s*°?\s*[cf]?\s*(?:-|–|to|and)\s*([-+]?\d+\.?\d*)\s*°?\s*[cf]?\b',
        q_lower,
    ) or re.search(
        r'(?:between\s+)?([-+]?\d+\.?\d*)\s*[-–]\s*([-+]?\d+\.?\d*)\s*°?\s*[cf]?\b',
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

    kind = "categorical"
    if any(w in q_lower for w in ["or higher", "or above", "above", "exceed"]):
        kind = "above"
    elif any(w in q_lower for w in ["or below", "or lower", "below", "under"]):
        kind = "below"

    m = re.search(r'([-+]?\d+\.?\d*)\s*°\s*([FfCc])\b', q_lower)
    if m:
        return kind, float(m.group(1)), None, 'F' if m.group(2).lower() == 'f' else 'C'

    m = re.search(r'([-+]?\d+\.?\d*)\s*([FfCc])\b', q_lower)
    if m:
        return kind, float(m.group(1)), None, 'F' if m.group(2).lower() == 'f' else 'C'

    m = re.search(r'\be\s+([-+]?\d+\.?\d*)', q_lower)
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
    if "between" in q and re.search(r'\d+.*(?:[-–]|and|to).*\d+', q):
        return "range"
    if "or higher" in q or "or above" in q or "above" in q or "exceed" in q:
        return "above"
    if "or below" in q or "or lower" in q or "below" in q or "under" in q or "or fewer" in q:
        return "below"
    if re.search(r'\d+\s*(?:[-–]|to)\s*\d+', q):
        return "range"
    return "categorical"


def _confidence_from_forecast(forecast: Optional[WeatherForecast]) -> float:
    if not forecast or not forecast.sources_used:
        return 0.60
    sources = forecast.sources_used
    if "METAR" in sources and "OBSERVED" in sources:
        return 0.95
    if "METAR" in sources:
        return 0.92
    if "OBSERVED" in sources:
        return 0.90
    if "Open-Meteo_ENSEMBLE" in sources or "ENSEMBLE" in sources:
        return 0.85
    n = len(sources)
    if "NOAA" in sources and n >= 2:
        return 0.85
    if any("GFS" in s for s in sources) and any("ECMWF" in s for s in sources):
        return 0.80
    return 0.70


def _time_decay_for_hours(hours: float) -> float:
    if hours <= 6.0:
        return 1.00
    if hours <= 12.0:
        return 0.95
    return 0.90


# ══════════════════════════════════════════════════════════════════
# РОЗРАХУНОК ЙМОВІРНОСТІ
# ══════════════════════════════════════════════════════════════════

def estimate_market_probability(market: PolyMarket, forecast: WeatherForecast) -> Tuple[float, float, str]:
    if not forecast:
        return 0.50, 0.0, "no_forecast"

    hours = market.hours_to_resolution
    kind, val_min, val_max, unit = _parse_range_or_threshold(market.question)
    is_low = 'lowest' in market.question.lower()

    if kind == "range":
        mean_c = forecast.temp_low_c if is_low else forecast.temp_high_c
        if unit == 'F':
            t_min_c = _f_to_c(val_min - 0.5)
            t_max_c = _f_to_c(val_max + 0.5)
            label = f"range|{val_min}-{val_max}°F"
            threshold_c = _f_to_c((val_min + val_max) / 2)
        else:
            t_min_c = val_min - 0.5
            t_max_c = val_max + 0.5
            label = f"range|{val_min}-{val_max}°C"
            threshold_c = (val_min + val_max) / 2

        sigma = forecast._get_sigma(hours)
        p_high = 0.5 * (1 + math.erf((t_max_c - mean_c) / (sigma * math.sqrt(2))))
        p_low = 0.5 * (1 + math.erf((t_min_c - mean_c) / (sigma * math.sqrt(2))))
        prob_parametric = max(0.0, p_high - p_low)

        if hours <= 6.0:
            _max_r = config.PROB_CAP_EXACT_SHORT
        elif hours <= 18.0:
            _max_r = config.PROB_CAP_EXACT_MID
        else:
            _max_r = config.PROB_CAP_EXACT_LONG

        members = forecast._get_adjusted_members(is_low)
        if members and len(members) >= 5:
            count_in = sum(1 for m in members if t_min_c <= m < t_max_c)
            prob_empirical = count_in / len(members)
            if prob_empirical == 0.0:
                p = prob_parametric * 0.50
            else:
                p = (prob_empirical * 0.40 + prob_parametric * 0.60) * 0.75
        else:
            p = prob_parametric * 0.75

        return round(max(0.01, min(_max_r, p)), 4), threshold_c, label

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
        p *= getattr(config, "PROB_THRESHOLD_CALIBRATION_SCALE", 0.95)
    elif kind == "range":
        p *= getattr(config, "PROB_RANGE_CALIBRATION_SCALE", 0.85)
    else:
        p *= getattr(config, "PROB_EXACT_CALIBRATION_SCALE", 0.85)

    if distance_c is not None:
        scale = getattr(config, "PROB_DISTANCE_SCALE_C", 3.0)
        power = getattr(config, "PROB_DISTANCE_POWER", 0.5)
        p *= 1.0 / (1.0 + (abs(distance_c) / scale) ** power)

    p *= _time_decay_for_hours(hours)

    confidence_weight = getattr(config, "PROB_CONFIDENCE_WEIGHT", 0.15)
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


# ══════════════════════════════════════════════════════════════════
# v15: METAR ARBITRAGE CONFIRMATION
# ══════════════════════════════════════════════════════════════════

def _check_metar_confirmation(
    city: str,
    kind: str,
    threshold_c: float,
    is_low: bool,
    range_low_c: Optional[float] = None,
    range_high_c: Optional[float] = None,
    forecast: Optional[WeatherForecast] = None,
) -> Tuple[bool, float, float, float]:
    """
    Перевіряє чи METAR та observed data підтверджують напрямок ринку.

    v17: Прогноз (fc_high/fc_low) ВИДАЛЕНО з confirmation logic для всіх kind.
    Причина: прогноз вже використовується для our_prob — повторне використання
    для "підтвердження" = circular logic = forecast-bet (12 програшів поспіль).
    Тепер тільки METAR + observed data (фізична істина) підтверджують ринок.

    Для "above X°C": best_evidence = max(metar_temp, obs_high) >= threshold - buffer
    Для "below X°C": best_evidence_cold = min(metar_temp, obs_low) <= threshold + buffer
    Для "range": best_evidence в межах [range_low-C..range_high+C]
    Для "categorical": те ж що range (одноточковий — з buffer)

    When observed data unavailable (429/no data), falls back to METAR only.

    Повертає: (confirmed, metar_temp_c, observed_high_c, distance_from_threshold_c)
    """
    metar_temp = 0.0
    obs_high = 0.0
    obs_low = 0.0
    fc_high = 0.0
    fc_low = 0.0
    distance = 0.0
    buffer_c = getattr(config, "METAR_ARB_TEMP_CONFIRM_C", 0.3)

    metar_fc = fetch_metar(city)
    metar_available = metar_fc is not None
    if metar_fc:
        metar_temp = metar_fc.temp_high_c

    if forecast:
        fc_high = forecast.temp_high_c
        fc_low = forecast.temp_low_c

    observed = _fetch_observed_daily_extremes(city)
    obs_available = observed is not None
    if observed:
        obs_high = observed[1]
        obs_low = observed[0]

    if not metar_available and not obs_available and not forecast:
        logger.debug(f"⚠️ METAR+OBS+FC недоступні для {city} — пропускаємо підтвердження")
        return True, 0.0, 0.0, 0.0

    if kind == "above":
        # v18: STRICT METAR-арбітраж — температура ВЖЕ досягла порогу.
        # "Майже досягнуто" (best_evidence >= threshold - buffer) = forecast-bet.
        # Буфер НЕ застосовується — бо our_prob вже використовує прогноз.
        candidates = [metar_temp]
        if obs_high > 0:
            candidates.append(obs_high)
        best_evidence = max(candidates)
        distance = best_evidence - threshold_c
        confirmed = best_evidence >= threshold_c
        if confirmed and obs_high > 0 and obs_high < threshold_c:
            confirmed = False
            logger.debug(
                f"⛔ ABOVE forecast-bet rejected: obs_hi={obs_high:.1f}°C < "
                f"threshold={threshold_c:.1f}°C | fc_high={fc_high:.1f}°C — not METAR arb"
            )
    elif kind == "below":
        # v18: STRICT — температура ВЖЕ нижче порогу.
        candidates = [metar_temp]
        if obs_low > 0:
            candidates.append(obs_low)
        best_evidence_cold = min(candidates)
        distance = threshold_c - best_evidence_cold
        confirmed = best_evidence_cold <= threshold_c
        if confirmed and obs_low > 0 and obs_low > threshold_c:
            confirmed = False
            logger.debug(
                f"⛔ BELOW forecast-bet rejected: obs_low={obs_low:.1f}°C > "
                f"threshold={threshold_c:.1f}°C | fc_low={fc_low:.1f}°C — not METAR arb"
            )
    elif kind == "range" and range_low_c is not None and range_high_c is not None:
        # v18: STRICT range — спостережена температура ВЖЕ в бакеті.
        # НІ buffer знизу (дозволяв forecast-bet), НІ buffer зверху
        # (температура вже вийшла за бакет = ринок програє).
        # Це найчистіший METAR-арбітраж: фізична істина в бакеті, ринок ще не цінує.
        candidates = [metar_temp]
        if obs_high > 0:
            candidates.append(obs_high)
        best_evidence = max(candidates)
        range_mid = (range_low_c + range_high_c) / 2.0
        distance = -abs(best_evidence - range_mid)
        confirmed = range_low_c <= best_evidence <= range_high_c
        if not confirmed and obs_high > 0:
            logger.debug(
                f"⛔ RANGE strict rejected: obs_hi={obs_high:.1f}°C outside "
                f"[{range_low_c:.1f}, {range_high_c:.1f}] | fc_high={fc_high:.1f}°C — not METAR arb"
            )
        if confirmed:
            half_width = (range_high_c - range_low_c) / 2.0
            distance = max(0.0, half_width - abs(best_evidence - range_mid))
    elif kind == "categorical":
        # v18: categorical — одноточковий, ПОТРЕБУЄ buffer (температура ≈ threshold).
        # Але тільки METAR/observed, без forecast.
        candidates = [metar_temp]
        if obs_high > 0:
            candidates.append(obs_high)
        best_evidence = max(candidates)
        distance = -(abs(best_evidence - threshold_c))
        confirmed = (threshold_c - buffer_c) <= best_evidence <= (threshold_c + buffer_c)
        if confirmed and obs_high > 0 and abs(obs_high - threshold_c) > buffer_c:
            confirmed = False
            logger.debug(
                f"⛔ CATEGORICAL forecast-bet rejected: obs_hi={obs_high:.1f}°C | "
                f"threshold={threshold_c:.1f}°C | fc_high={fc_high:.1f}°C — not METAR arb"
            )
        if confirmed:
            distance = max(0.0, buffer_c - abs(best_evidence - threshold_c))
    else:
        confirmed = False

    return confirmed, metar_temp, obs_high, distance


def _boost_prob_from_metar(
    our_prob: float,
    kind: str,
    metar_confirmed: bool,
    metar_distance_c: float,
    hours: float,
) -> float:
    """
    Якщо METAR підтверджує напрямок — підіймаємо ймовірність.
    Чим ближче resolution і чим далі METAR від порогу — тим більше підіймаємо.
    """
    if not metar_confirmed:
        return our_prob

    if kind not in ("above", "below", "range", "categorical"):
        return our_prob

    hours_factor = max(0.0, (8.0 - hours) / 8.0)
    dist_bonus = min(metar_distance_c / 3.0, 1.0)

    if kind in ("range", "categorical"):
        boost = 0.08 * hours_factor * max(dist_bonus, 0.3)
    else:
        boost = 0.10 * hours_factor * dist_bonus

    boosted = min(our_prob + boost, 0.90)

    if boost > 0.01:
        logger.debug(
            f"🔍 METAR BOOST: +{boost:.1%} | kind={kind} | hours={hours:.1f}h | "
            f"dist={metar_distance_c:.1f}°C → our_prob {our_prob:.0%}→{boosted:.0%}"
        )

    return round(boosted, 4)


# ══════════════════════════════════════════════════════════════════
# CLIMATE SANITY FILTER
# ══════════════════════════════════════════════════════════════════

_CLIMATE_BOUNDS_C = {
    "miami":         {"low": (8, 10, 12, 15, 20, 23, 24, 24, 24, 20, 14, 9),  "high": (27, 28, 29, 31, 33, 34, 35, 35, 34, 32, 30, 28)},
    "los angeles":   {"low": (8, 9, 10, 12, 14, 16, 18, 18, 17, 14, 10, 8),    "high": (22, 22, 23, 24, 25, 28, 32, 33, 31, 27, 24, 21)},
    "chicago":       {"low": (-16,-13,-7,1,7,13,16,15,9,2,-6,-13),              "high": (-2,1,7,16,22,28,30,29,24,16,7,0)},
    "new york":      {"low": (-4,-3,1,7,13,18,21,20,16,9,3,-2),                "high": (4,5,11,18,24,29,32,31,27,20,12,6)},
    "nyc":           {"low": (-4,-3,1,7,13,18,21,20,16,9,3,-2),                "high": (4,5,11,18,24,29,32,31,27,20,12,6)},
    "dallas":        {"low": (2,4,8,14,19,23,25,25,20,14,7,3),                 "high": (14,16,22,26,31,35,38,38,33,26,19,14)},
    "seattle":       {"low": (2,3,4,6,9,12,14,14,11,7,3,1),                    "high": (9,11,13,16,20,23,27,27,24,17,11,8)},
    "denver":        {"low": (-9,-7,-4,1,7,12,15,14,9,2,-4,-8),                 "high": (7,9,13,17,23,30,33,32,27,19,12,6)},
    "atlanta":       {"low": (-1,0,4,10,16,20,22,22,18,11,5,0),                "high": (13,15,20,24,28,32,34,34,30,24,18,13)},
    "boston":        {"low": (-6,-5,0,6,12,17,20,20,15,9,3,-4),                 "high": (3,4,9,16,22,27,30,29,25,18,11,5)},
    "houston":       {"low": (6,8,12,17,21,24,25,25,22,17,11,7),                "high": (18,20,24,27,31,34,36,36,33,28,23,18)},
    "london":        {"low": (2,2,3,5,8,11,14,13,11,8,4,2),                    "high": (9,10,12,15,19,22,25,24,20,15,11,9)},
    "paris":         {"low": (2,2,4,7,11,14,16,16,13,9,5,3),                    "high": (8,10,14,17,21,25,27,27,23,17,11,8)},
    "berlin":        {"low": (-3,-3,0,4,9,12,14,14,10,5,1,-2),                  "high": (4,5,10,15,20,24,26,25,20,14,8,4)},
    "munich":        {"low": (-5,-4,-1,3,8,12,14,14,10,5,0,-4),                 "high": (3,5,10,15,20,23,25,25,20,14,8,3)},
    "tokyo":         {"low": (2,3,6,11,16,20,24,25,22,15,9,4),                  "high": (10,11,15,20,24,27,31,33,28,22,17,12)},
    "seoul":         {"low": (-6,-4,0,6,12,17,22,22,16,8,1,-4),                 "high": (1,4,10,17,23,27,29,30,26,20,12,4)},
    "busan":         {"low": (-2,0,4,9,14,18,22,23,19,13,6,0),                  "high": (7,9,13,18,23,26,29,30,27,22,15,9)},
    "buenos aires":  {"low": (18,17,16,12,9,6,5,7,9,13,15,17),                 "high": (31,30,27,23,19,16,15,17,19,22,26,29)},
    "sao paulo":     {"low": (18,18,17,15,12,10,9,10,12,14,16,17),              "high": (29,29,28,26,24,22,22,24,24,25,27,28)},
    "cape town":     {"low": (14,14,12,10,7,5,4,5,7,10,12,14),                  "high": (28,28,26,24,20,18,17,18,20,23,25,27)},
    "sydney":        {"low": (18,18,16,13,10,8,7,8,10,13,15,17),                "high": (28,28,26,24,21,18,18,20,22,24,25,27)},
    "lucknow":       {"low": (8,11,16,22,26,28,27,26,25,20,13,8),               "high": (24,28,35,40,42,40,35,34,34,33,29,24)},
}

def _climate_sanity_check(city: str, threshold_c: float, kind: str, is_low: bool,
                          range_low_c: float = None, range_high_c: float = None,
                          months_ahead: int = 0) -> bool:
    """
    Перевіряє чи поріг/діапазон температури кліматично можливий для міста.
    Повертає True якщо ОК, False якщо неможливо (напр. Miami LOW 25°C влітку).
    """
    city_key = city.lower().strip()
    if city_key not in _CLIMATE_BOUNDS_C:
        return True

    bounds = _CLIMATE_BOUNDS_C[city_key]
    now = datetime.now(timezone.utc)
    month_idx = (now.month - 1 + months_ahead) % 12
    t_lo = bounds["low"][month_idx]
    t_hi = bounds["high"][month_idx]
    margin_hi_c = 8.0
    margin_lo_c = 5.0

    if kind in ("range", "categorical") and range_low_c is not None and range_high_c is not None:
        if range_high_c < t_lo - margin_lo_c:
            return False
        if range_low_c > t_hi + margin_hi_c:
            return False
        if is_low and range_high_c < t_lo:
            return False
        if not is_low and range_low_c > t_hi:
            return False
        return True

    if kind == "above":
        if is_low:
            if threshold_c >= t_lo + margin_lo_c:
                return False
        else:
            if threshold_c >= t_hi + margin_hi_c:
                return False
        return True

    if kind == "below":
        if is_low:
            if threshold_c < t_lo - margin_lo_c:
                return False
        else:
            if threshold_c <= t_lo - margin_hi_c:
                return False
            if threshold_c > t_hi + margin_hi_c:
                return False
        return True

    return True


# ══════════════════════════════════════════════════════════════════
# ОБЧИСЛЕННЯ EDGE (v15: METAR ARBITRAGE)
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

    kind = market.kind or _detect_market_kind(market.question)

    allowed_kinds = getattr(config, "METAR_ARB_KINDS_ONLY", ["above", "below"])
    if kind not in allowed_kinds:
        return None

    from market_scanner import get_target_date
    t_date = get_target_date(market.question, market.end_date, city)
    forecast = get_best_forecast(city, hours_to_resolution=market.hours_to_resolution, target_date=t_date)
    if not forecast:
        return None

    confidence = _confidence_from_forecast(forecast)
    time_decay = _time_decay_for_hours(market.hours_to_resolution)
    our_prob, threshold_c, kind_label = estimate_market_probability(market, forecast)

    _log_price_validation(market)
    market_prob = market.best_ask_yes

    if market_prob < 0.01:
        midpoint = getattr(market, "midpoint_yes", 0.0)
        grid_min_ask = getattr(config, "SNIPER_GRID_MIN_ASK", 0.15)
        if grid_min_ask <= midpoint <= 0.99:
            logger.debug(f"💡 Ask={market_prob:.4f} < 1¢, fallback на midpoint={midpoint:.4f}")
            market_prob = midpoint
        else:
            logger.debug(f"⏭️ SKIP: ask={market_prob:.4f} замалий, midpoint={midpoint:.4f} невалідний")
            return None

    if market_prob <= 0.001 or market_prob >= 0.99:
        return None

    is_low = 'lowest' in market.question.lower()
    fc_temp = forecast.temp_low_c if is_low else forecast.temp_high_c
    distance_c = abs(fc_temp - threshold_c)

    rl_raw = getattr(market, 'range_low', None)
    rh_raw = getattr(market, 'range_high', None)
    unit = _unit_from_question(market.question)
    range_low_c = _f_to_c(rl_raw) if rl_raw is not None and unit == 'F' else rl_raw
    range_high_c = _f_to_c(rh_raw) if rh_raw is not None and unit == 'F' else rh_raw

    if getattr(config, "CLIMATE_SANITY_ENABLED", True) and not _climate_sanity_check(city, threshold_c, kind, is_low, range_low_c, range_high_c):
        logger.debug(
            f"🚫 CLIMATE IMPOSSIBLE: {city} | {kind} | threshold={threshold_c:.1f}°C | "
            f"range=[{range_low_c},{range_high_c}] | is_low={is_low} | {market.question[:50]}"
        )
        return None

    our_prob = _apply_probability_calibration(
        our_prob,
        kind_label,
        market.hours_to_resolution,
        distance_c,
        _unit_from_question(market.question),
        confidence,
    )

    market_anchor_weight = getattr(config, "MARKET_ANCHOR_WEIGHT", 0.20)
    market_anchor_threshold = getattr(config, "MARKET_ANCHOR_THRESHOLD", 0.50)
    # v16: Skip market anchor when our forecast confidence is high (METAR-confirmed
    # or strong ensemble). Anchoring to a low market price destroys legitimate edge.
    _fc_sources = forecast.sources_used if forecast else []
    _high_confidence = (
        ("METAR" in _fc_sources and "OBSERVED" in _fc_sources)
        or ("Open-Meteo_ENSEMBLE" in _fc_sources or "ENSEMBLE" in _fc_sources)
    )
    if market_prob < market_anchor_threshold and not _high_confidence:
        our_prob = our_prob * (1 - market_anchor_weight) + market_prob * market_anchor_weight
        logger.debug(f"ANCHORED: market_prob={market_prob:.4f} → our_prob={our_prob:.4f} | {market.question[:40]}")

    metar_confirmed, metar_temp_c, observed_high_c, metar_distance_c = _check_metar_confirmation(
        city, kind, threshold_c, is_low,
        range_low_c=range_low_c,
        range_high_c=range_high_c,
        forecast=forecast,
    )

    _pre_edge = min(our_prob - market_prob, getattr(config, "MAX_EDGE_CAP", 0.50))
    _min_edge_pre = getattr(config, "METAR_ARB_MIN_EDGE", 0.04)
    if getattr(config, "METAR_ARB_REQUIRE_METAR", False) and not metar_confirmed:
        high_prob_fallback = our_prob >= 0.70 and _pre_edge >= _min_edge_pre
        if not high_prob_fallback:
            return EdgeResult(
                market=market,
                forecast=forecast,
                estimated_prob=our_prob,
                market_prob=market_prob,
                edge=0.0,
                edge_direction="BUY_YES",
                confidence=confidence,
                reason="SKIP_METAR",
                is_tradeable=False,
                time_decay_factor=time_decay,
                size_usd=0.0,
                threshold_c=threshold_c,
                distance_c=distance_c,
                metar_temp_c=metar_temp_c,
                observed_high_c=observed_high_c,
            )

    our_prob = _boost_prob_from_metar(our_prob, kind, metar_confirmed, metar_distance_c, market.hours_to_resolution)

    raw_edge = our_prob - market_prob
    eff_edge = min(raw_edge, getattr(config, "MAX_EDGE_CAP", 0.50))

    min_ask = getattr(config, "METAR_ARB_MIN_ASK", 0.05)
    max_ask = getattr(config, "METAR_ARB_MAX_ASK", 0.70)
    min_edge = getattr(config, "METAR_ARB_MIN_EDGE", 0.04)
    min_prob = getattr(config, "METAR_ARB_MIN_PROB_RANGE", 0.20) if kind in ("range", "categorical") else getattr(config, "METAR_ARB_MIN_PROB", 0.45)
    max_dist = getattr(config, "METAR_ARB_MAX_DIST_C", 5.0)

    tradeable = (
        market_prob >= min_ask
        and market_prob <= max_ask
        and our_prob >= min_prob
        and eff_edge >= min_edge
        and (metar_confirmed or distance_c <= max_dist)
    )

    if tradeable and market_prob < 0.05 and our_prob > 0.20:
        if not metar_confirmed:
            tradeable = False
        else:
            logger.info(
                f"✅ PHANTOM+METAR: our={our_prob:.0%} vs mkt={market_prob:.0%} — high-edge arb | {market.question[:40]}"
            )

    if tradeable and market_prob > 0.01:
        prob_ratio = our_prob / market_prob
        max_ratio = getattr(config, "PROB_RATIO_MAX_METAR", 5.0) if metar_confirmed else getattr(config, "PROB_RATIO_MAX_NO_METAR", 3.0)
        if prob_ratio > max_ratio:
            tradeable = False

    if tradeable:
        metar_tag = "✓METAR" if metar_confirmed else "NO-METAR"
        reason = (
            f"🎯 METAR ARB {kind.upper()} @ {market_prob:.3f} | {kind_label} | "
            f"our_prob={our_prob:.0%} | dist={distance_c:.1f}°C | decay={time_decay:.2f} | {metar_tag}"
        )
        logger.info(
            f"✅ EDGE: {market.question[:50]} | "
            f"our={our_prob:.0%} | mkt={market_prob:.0%} | edge={eff_edge:.1%} | "
            f"dist={distance_c:.1f}°C | {kind_label} | METAR={'✓' if metar_confirmed else '✗'}"
        )
    else:
        reason = "SKIP"

    return EdgeResult(
        market=market,
        forecast=forecast,
        estimated_prob=our_prob,
        market_prob=market_prob,
        edge=eff_edge if tradeable else raw_edge,
        edge_direction="BUY_YES",
        confidence=confidence,
        reason=reason,
        is_tradeable=tradeable,
        time_decay_factor=time_decay,
        size_usd=0.0,
        threshold_c=threshold_c,
        distance_c=distance_c,
        metar_temp_c=metar_temp_c,
        observed_high_c=observed_high_c,
    )


# ══════════════════════════════════════════════════════════════════
# СКАНУВАННЯ (v15: simplified — no adjacent grid)
# ══════════════════════════════════════════════════════════════════

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
        allowed_kinds = getattr(config, "METAR_ARB_KINDS_ONLY", ["above", "below"])
        if kind not in allowed_kinds:
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
        f"Edge scan: {len(results)} tradeable / {len(markets)} ринків "
        f"| skip: vol={skip_vol}, hours={skip_hours}, city={skip_city}, "
        f"kind={skip_kind}, spread={skip_spread}, none={skip_none}"
    )
    return results
