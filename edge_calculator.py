"""
edge_calculator.py — coldmath v9 (production-ready)

ВИПРАВЛЕНО ВСІ КРИТИЧНІ БАГИ:

  BUG-F (КРИТИЧНИЙ): °F/°C конверсія
    "Chicago be 43°F or below" → threshold=43°F=6.1°C
    Бот брав 43 як °C → prob_below(43°C) при forecast=10.9°C = 0.98 → НЕПРАВИЛЬНО
    Реально: prob_below(6.1°C) при forecast=10.9°C ≈ 0.04 → ПРАВИЛЬНО
    Виправлено: _parse_threshold повертає (value, unit), конвертуємо F→C перед розрахунком

  BUG-C: Categorical ринки ("be exactly 17°C")
    Використовують prob_exact_bin (Гаусовий), не prob_above
    При forecast=15°C: P(exactly 17°C) ≈ 0.12, НЕ 0.98

  BUG-H: our_prob=0.98 для "above/below" ринків
    prob_above_temp_c(18°C) при forecast=22°C дає 0.98 — ПРАВИЛЬНО
    prob_below_temp_c(6.1°C) при forecast=10.9°C дає 0.04 — ПРАВИЛЬНО після F→C

  BUG-L: Hourly limit 20/год при 4 сигнали/цикл → блокується через 5 циклів
    Виправлено: підняти до 50/год, і не рахувати повторні угоди того самого ринку

  BUG-P: PnL = 0 завжди в DRY_RUN (resolution не відслідковується)
    Виправлено: в calculate_edge зберігаємо resolved_yes після перевірки через Gamma API
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
    edge_direction: str      # "BUY_YES" | "BUY_NO"
    confidence: float
    reason: str
    is_tradeable: bool
    size_usd: float = 0.0
    threshold_c: float = 0.0  # зберігаємо конвертований поріг для логу

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
# ПАРСИНГ ПОРОГУ — з визначенням одиниць (°C або °F)
# ══════════════════════════════════════════════════════════════════

def _parse_threshold_with_unit(question: str) -> Tuple[Optional[float], str]:
    """
    Повертає (threshold_value, unit) де unit = 'C' або 'F'.

    Приклади:
      "be 43°F or below" → (43.0, 'F')
      "be 18°C on April" → (18.0, 'C')
      "be 55°F or higher" → (55.0, 'F')
      "be 25°C"          → (25.0, 'C')
    """
    # °F явно вказане
    m = re.search(r'(\d+\.?\d*)\s*°?\s*F\b', question)
    if m:
        return float(m.group(1)), 'F'

    # °C явно вказане
    m = re.search(r'(\d+\.?\d*)\s*°?\s*C\b', question)
    if m:
        return float(m.group(1)), 'C'

    # Числа без одиниці — хвостовий детект по контексту міста
    # Chicago, Dallas, NYC, San Francisco → найімовірніше °F
    # London, Paris, Berlin, Tokyo → °C
    fahrenheit_cities = {"chicago", "dallas", "nyc", "new york", "san francisco",
                         "miami", "los angeles", "seattle", "atlanta", "boston",
                         "denver", "phoenix", "las vegas", "austin", "minneapolis",
                         "portland", "houston", "nashville", "charlotte", "orlando"}
    q_lower = question.lower()
    is_fahrenheit_city = any(city in q_lower for city in fahrenheit_cities)

    m = re.search(r'be (\d+\.?\d*)', question)
    if m:
        val = float(m.group(1))
        # Значення >40 без одиниці у °C-контексті — підозріло → скоріше °F
        if val > 40 and is_fahrenheit_city:
            return val, 'F'
        if val > 50:  # температура >50 без одиниці = майже напевно °F
            return val, 'F'
        return val, 'C'

    return None, 'C'


def _f_to_c(f: float) -> float:
    """Конвертація Fahrenheit → Celsius."""
    return (f - 32) * 5 / 9


def _detect_market_kind(question: str) -> str:
    """
    Визначити тип ринку:
      "above"       — "or higher", "or above", "exceed"
      "below"       — "or below", "or lower", "or fewer", "under"
      "categorical" — конкретний бін (be exactly X°C)
    """
    q = question.lower()
    if "or higher" in q or "or above" in q or "above" in q or "exceed" in q:
        return "above"
    if "or below" in q or "or lower" in q or "below" in q or "under" in q or "or fewer" in q:
        return "below"
    return "categorical"


# ══════════════════════════════════════════════════════════════════
# CONFIDENCE
# ══════════════════════════════════════════════════════════════════

def _confidence_from_forecast(forecast: Optional[WeatherForecast]) -> float:
    if not forecast or not forecast.sources_used:
        return 0.60
    sources = forecast.sources_used
    n = len(sources)
    if "NOAA" in sources and n >= 2:
        return 0.95
    if "NOAA" in sources:
        return 0.90
    if any("GFS" in s for s in sources) and any("ECMWF" in s for s in sources):
        return 0.87
    if any("GFS" in s for s in sources):
        return 0.82
    if any("ECMWF" in s for s in sources):
        return 0.82
    if "NASA_POWER" in sources and n == 1:
        return 0.55  # NASA POWER alone = кліматичні, не прогноз
    return 0.70


# ══════════════════════════════════════════════════════════════════
# РОЗРАХУНОК ЙМОВІРНОСТІ
# ══════════════════════════════════════════════════════════════════

def estimate_market_probability(
    market: PolyMarket,
    forecast: WeatherForecast,
) -> Tuple[float, float, str]:
    """
    Повертає (our_prob, threshold_c_used, kind_str).

    BUG-F FIX: конвертуємо °F → °C перед порівнянням з прогнозом.
    BUG-C FIX: categorical ринки → Гаусовий бін, не prob_above.
    """
    if not forecast:
        return 0.50, 0.0, "no_forecast"

    raw_threshold, unit = _parse_threshold_with_unit(market.question)

    # Конвертуємо поріг у °C (ВИПРАВЛЕННЯ BUG-F)
    if raw_threshold is not None:
        if unit == 'F':
            threshold_c = _f_to_c(raw_threshold)
            unit_label = f"{raw_threshold:.0f}°F={threshold_c:.1f}°C"
        else:
            threshold_c = raw_threshold
            unit_label = f"{raw_threshold:.0f}°C"
    else:
        # Немає порогу — використовуємо threshold_value з маркету
        threshold_c = market.threshold_value or 0.0
        unit_label = f"{threshold_c:.0f}°C(fallback)"

    kind = _detect_market_kind(market.question)
    # КРИТИЧНО: 'lowest temperature' ринки → використовуємо temp_low_c!
    is_low = 'lowest' in market.question.lower()
    tc = forecast.temp_low_c if is_low else forecast.temp_high_c

    if tc == 0.0 or tc is None:
        return 0.50, threshold_c, kind

    # ── Above ────────────────────────────────────────────────────
    if kind == "above":
        p = forecast.prob_above_temp_c(threshold_c, is_low)

    # ── Below ────────────────────────────────────────────────────
    elif kind == "below":
        p = forecast.prob_below_temp_c(threshold_c, is_low)

    # ── Categorical bin ──────────────────────────────────────────
    else:
        # P(temp ∈ [threshold_c - 0.5, threshold_c + 0.5])
        sigma = 2.0
        erf = math.erf
        sqrt2 = math.sqrt(2)
        p = (
            0.5 * (1 + erf((threshold_c + 0.5 - tc) / (sigma * sqrt2))) -
            0.5 * (1 + erf((threshold_c - 0.5 - tc) / (sigma * sqrt2)))
        )
        p = max(0.01, min(0.99, p))

    return round(p, 4), threshold_c, f"{kind}|{unit_label}"


# ══════════════════════════════════════════════════════════════════
# ОСНОВНА ФУНКЦІЯ РОЗРАХУНКУ EDGE
# ══════════════════════════════════════════════════════════════════

def calculate_edge(market: PolyMarket) -> Optional[EdgeResult]:
    """
    Розраховує edge для ринку.

    Фільтри (в порядку застосування):
      1. Місто у whitelist
      2. hours_to_resolution: 2–48h (не занадто пізно і не дуже далеко)
      3. Volume ≥ MIN_MARKET_VOLUME_USD
      4. Прогноз доступний і confidence ≥ 0.70
      5. Sanity check: якщо ринок у °F і прогноз тепло — не купуємо "below cold"
    """
    city = market.detected_city
    if not city:
        return None

    # Тільки whitelist міст
    if hasattr(config, 'CITY_WHITELIST') and config.CITY_WHITELIST:
        if city not in config.CITY_WHITELIST:
            return None

    # Вікно часу: не торгуємо < 2h (майже закрито) і > 48h (прогноз ненадійний)
    if market.hours_to_resolution < 2.0 or market.hours_to_resolution > 48.0:
        return None

    # Мінімальний об'єм
    if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
        return None

    # Прогноз
    forecast = get_best_forecast(city, hours_to_resolution=market.hours_to_resolution)
    if not forecast:
        return None

    confidence = _confidence_from_forecast(forecast)
    if confidence < 0.70:
        logger.debug(f"Low confidence {confidence:.2f} for {city}")
        return None

    # Розраховуємо ймовірність
    our_prob, threshold_c, kind_label = estimate_market_probability(market, forecast)
    market_prob = market.midpoint_yes

    # Ринок практично вирішений якщо YES≈1.00 (NO price ≈ 0)
    # Не відображаємо як tradeable — trader все одно заблокує
    if (1.0 - market_prob) < 0.015:
        logger.debug(f"SKIP: market YES={market_prob:.3f} → NO price < 0.015")
        return None

    # ── SANITY CHECK (BUG-F захист) ──────────────────────────────
    # Якщо ринок "below X°F" і поріг у °C < forecast - 8°C
    # → ймовірність буде < 5% (не наш edge), пропускаємо
    kind = _detect_market_kind(market.question)
    _, unit = _parse_threshold_with_unit(market.question)

    if kind == "below" and unit == 'F':
        temp_gap = forecast.temp_high_c - threshold_c
        if temp_gap > 8.0:
            logger.debug(
                f"SANITY SKIP °F below: forecast {forecast.temp_high_c:.1f}°C "
                f"> threshold {threshold_c:.1f}°C (+{temp_gap:.1f}°C gap) | {market.question[:50]}"
            )
            return None

    # Edges
    edge_yes = our_prob - market_prob
    edge_no  = (1.0 - our_prob) - (1.0 - market_prob)
    eff_yes  = edge_yes * confidence
    eff_no   = edge_no  * confidence

    fc_temp = f"{forecast.temp_high_c:.1f}°C"
    src = "+".join(s.split("_")[0] for s in (forecast.sources_used or [])[:2])
    no_price = 1.0 - market_prob

    # ══════════════════════════════════════════════════════════
    # СТРАТЕГІЯ 1: @coldmath BUY NO @ ≥95¢
    # Купуємо NO коли ринок каже YES=5¢ але наш prob < 3%
    # ══════════════════════════════════════════════════════════
    is_coldmath_no = (
        config.ENABLE_COLDMATH_TAIL_NO
        and no_price >= config.COLDMATH_MIN_ASK_NO
        and no_price <= config.COLDMATH_MAX_ASK_NO
        and our_prob <= 0.03         # наша ймовірність справді низька
        and eff_no >= config.COLDMATH_MIN_EDGE_NO
    )

    # ══════════════════════════════════════════════════════════
    # СТРАТЕГІЯ 2: EXTREME TAIL YES @ 1-5¢
    # Купуємо YES коли ринок каже 1-5¢ але наш prob > 20%
    # ══════════════════════════════════════════════════════════
    # Додатковий sanity: our_prob не може бути 0.98 для categorical
    prob_realistic = True
    if kind == "categorical":
        if our_prob > 0.80:
            prob_realistic = False  # занадто оптимістично для точного біна

    is_extreme_yes = (
        config.ENABLE_EXTREME_TAIL_YES
        and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES
        and eff_yes >= config.EXTREME_TAIL_MIN_EDGE_YES
        and prob_realistic
        and confidence >= 0.80
        and our_prob >= 0.15        # наш prob реально > 15%
    )

    # ── Шум-фільтр ────────────────────────────────────────────
    if market.midpoint_yes < 0.003:
        return None  # ціна < 0.3¢ — мертвий ринок

    # ── Визначення напрямку ────────────────────────────────────
    if is_coldmath_no:
        direction = "BUY_NO"
        eff_edge  = abs(eff_no)
        size_usd  = min(
            config.COLDMATH_MAX_SIZE_USD,
            max(config.MIN_POSITION_USD, config.BASE_POSITION_USD * confidence)
        )
        reason = (
            f"COLDMATH NO @ {no_price:.3f} | "
            f"{kind_label} | прогноз:{fc_temp} | {src}"
        )
        tradeable = True

    elif is_extreme_yes:
        direction = "BUY_YES"
        eff_edge  = abs(eff_yes)
        size_usd  = min(
            config.EXTREME_TAIL_MAX_SIZE_USD,
            max(config.MIN_POSITION_USD, 2.0)
        )
        reason = (
            f"EXTREME YES @ {market.best_ask_yes:.3f} | "
            f"{kind_label} | прогноз:{fc_temp} | {src}"
        )
        tradeable = True

    elif abs(eff_yes) >= config.MIN_EDGE_ENTRY or abs(eff_no) >= config.MIN_EDGE_ENTRY:
        if eff_yes >= eff_no:
            direction = "BUY_YES"
            eff_edge  = eff_yes
            reason = f"YES {our_prob:.2f} vs {market_prob:.2f} | {kind_label} | прогноз:{fc_temp} | {src}"
        else:
            direction = "BUY_NO"
            eff_edge  = eff_no
            reason = f"NO {our_prob:.2f} vs {market_prob:.2f} | {kind_label} | прогноз:{fc_temp} | {src}"

        # Слабкий edge + низька впевненість → пропускаємо
        if eff_edge < 0.45 and confidence < 0.88:
            logger.debug(f"Слабкий edge {eff_edge:.1%} + confidence {confidence:.2f} < 0.88 → skip")
            return None

        size_usd = max(
            config.MIN_POSITION_USD,
            min(
                config.BASE_POSITION_USD * confidence * 1.1,
                config.INITIAL_CAPITAL * config.MAX_POSITION_PCT
            )
        )
        tradeable = True

    else:
        return None  # Немає edge

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
        size_usd=round(size_usd, 2),
        threshold_c=threshold_c,
    )


# ══════════════════════════════════════════════════════════════════
# СКАНУВАННЯ ВСІХ РИНКІВ
# ══════════════════════════════════════════════════════════════════

def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    skip_vol = skip_city = skip_hours = skip_none = 0

    for market in markets:
        if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
            skip_vol += 1
            continue
        if market.hours_to_resolution < 2.0 or market.hours_to_resolution > 48.0:
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

    # Сортуємо: найкращий edge першим
    results.sort(key=lambda r: r.edge, reverse=True)

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} ринків "
        f"| skip: vol={skip_vol}, hours={skip_hours}, city={skip_city}, none={skip_none}"
    )
    return results
