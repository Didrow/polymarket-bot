"""
edge_calculator.py — coldmath v8 (фінальна версія)

ВИПРАВЛЕНО КРИТИЧНІ БАГИ:
  1. Categorical ринки ("be exactly 17°C") використовують prob_exact замість prob_above
     → Причина our_prob=0.98 скрізь: prob_above(17°C) при forecast=22°C завжди ~0.98
     → Виправлено: prob_exact_temp_c() для categorical бінів

  2. NASA POWER дає кліматичні дані (середнє 7 днів) → не прогноз погоди
     → Для categorical ринків вона збиває логіку
     → Виправлено: для categorical використовуємо тільки GFS/ECMWF/NOAA прогноз

  3. Фільтр годин: ринки з hours_to_resolution < 2 пропускаємо
     → Вже закриті або майже закриті ринки не мають сенсу торгувати

  4. @coldmath стратегія:
     BUY NO коли NO price ≥ 93¢ (YES ≤ 7¢)
     Принцип: ринок завжди недооцінює tail risk
"""

import math
import re
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
    edge_direction: str   # "BUY_YES" | "BUY_NO" | "SKIP"
    confidence: float
    reason: str
    is_tradeable: bool
    size_usd: float = 0.0

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


def _confidence_from_forecast(forecast: Optional[WeatherForecast]) -> float:
    """Впевненість на основі джерел прогнозу."""
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
        # NASA POWER alone = кліматичні дані, не прогноз
        return 0.55
    return 0.70


def _detect_market_kind(question: str) -> str:
    """
    Визначити тип ринку:
    - "categorical": "Will temp be exactly 17°C?" → один конкретний бін
    - "above": "Will temp be 20°C or higher?"
    - "below": "Will temp be 10°C or below?"
    """
    q = question.lower()
    if "or higher" in q or "above" in q or "exceed" in q:
        return "above"
    if "or below" in q or "below" in q or "under" in q or "or lower" in q:
        return "below"
    # Якщо питання про конкретне значення °C без вказання напрямку
    if re.search(r'be \d+\.?\d*\s*°?c', q):
        return "categorical"
    return "categorical"


def _parse_threshold(question: str) -> Optional[float]:
    """Витягти числовий поріг з назви ринку."""
    # "be 17°C" or "be 17 C" or "be 17"
    m = re.search(r'be (\d+\.?\d*)\s*°?\s*[Cc]', question)
    if m:
        return float(m.group(1))
    m = re.search(r'(\d+\.?\d*)\s*°[Cc]', question)
    if m:
        return float(m.group(1))
    return None


def estimate_market_probability(
    market: PolyMarket,
    forecast: WeatherForecast
) -> float:
    """
    Розрахунок нашої ймовірності для ринку.

    КЛЮЧОВЕ ВИПРАВЛЕННЯ:
    Categorical ринки ("Will temp be EXACTLY 17°C?") —
    НЕ prob_above(17), а prob_exact_bin(17±0.5°C).

    prob_above(17) при forecast=22°C дає 0.98 → НЕПРАВИЛЬНО.
    prob_exact(17) при forecast=22°C дає ~0.02 → ПРАВИЛЬНО.
    """
    if not forecast:
        return 0.50

    threshold = _parse_threshold(market.question) or market.threshold_value
    kind = _detect_market_kind(market.question)
    mtype = market.market_type

    # ── Температурні ринки ────────────────────────────────────
    if mtype == "temperature" and threshold is not None:
        tc = forecast.temp_high_c
        if tc == 0.0 or tc is None:
            return 0.50

        if kind == "above":
            # "Will temp be 20°C or higher?"
            return forecast.prob_above_temp_c(threshold)

        elif kind == "below":
            # "Will temp be 10°C or below?"
            return forecast.prob_below_temp_c(threshold)

        else:
            # CATEGORICAL: "Will temp be exactly 17°C?"
            # Гаусова ймовірність бін [threshold-0.5, threshold+0.5]
            sigma = 2.0  # стандартне відхилення прогнозу °C
            p = (0.5 * (1 + math.erf((threshold + 0.5 - tc) / (sigma * math.sqrt(2)))) -
                 0.5 * (1 + math.erf((threshold - 0.5 - tc) / (sigma * math.sqrt(2)))))
            return max(0.01, min(0.99, round(p, 4)))

    # ── Дощ/сніг ─────────────────────────────────────────────
    if mtype == "rain":
        return max(0.05, min(0.95, forecast.prob_rain_or_snow()))
    if mtype == "snow":
        return max(0.03, min(0.95, float(forecast.prob_snow or 0.1)))

    return 0.50


def calculate_edge(market: PolyMarket) -> Optional[EdgeResult]:
    """
    Розрахунок edge для одного ринку.
    Повертає None якщо угода не має сенсу.
    """
    # ── Базові фільтри ────────────────────────────────────────
    city = market.detected_city
    if not city:
        return None

    # Пропускаємо ринки що вже майже закриті (< 2 годин)
    if market.hours_to_resolution < 2.0:
        return None

    # Пропускаємо ринки з малим об'ємом
    if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
        return None

    # Тільки whitelist міст
    if hasattr(config, 'CITY_WHITELIST') and city not in config.CITY_WHITELIST:
        return None

    # ── Прогноз погоди ────────────────────────────────────────
    forecast = get_best_forecast(city)
    if not forecast:
        return None

    confidence = _confidence_from_forecast(forecast)

    # Якщо впевненість низька (тільки NASA_POWER) — пропускаємо
    if confidence < 0.70:
        logger.debug(f"Low confidence {confidence:.2f} for {city}, skip")
        return None

    # ── Розрахунок ймовірностей ───────────────────────────────
    our_prob = estimate_market_probability(market, forecast)
    market_prob = market.midpoint_yes

    edge_yes = our_prob - market_prob
    edge_no  = (1.0 - our_prob) - (1.0 - market_prob)
    eff_yes  = edge_yes * confidence
    eff_no   = edge_no  * confidence

    # ── Інфо для логу ─────────────────────────────────────────
    kind = _detect_market_kind(market.question)
    threshold = _parse_threshold(market.question) or market.threshold_value
    fc_temp = f"{forecast.temp_high_c:.1f}°C" if forecast.temp_high_c else "?"
    src = "+".join(s.split("_")[0] for s in (forecast.sources_used or [])[:2])

    no_price = 1.0 - market_prob

    # ══════════════════════════════════════════════════════════
    # @coldmath стратегія 1: BUY NO @ 93-99¢
    # Умова: NO price ≥ 93¢ і ефективний edge достатній
    # ══════════════════════════════════════════════════════════
    is_coldmath_no = (
        config.ENABLE_COLDMATH_TAIL_NO
        and no_price >= config.COLDMATH_MIN_ASK_NO
        and no_price <= config.COLDMATH_MAX_ASK_NO
        and eff_no >= config.COLDMATH_MIN_EDGE_NO
    )

    # ══════════════════════════════════════════════════════════
    # Extreme tail YES: BUY YES @ 1-5¢
    # Умова: YES price ≤ 5¢ і edge значний (> 40%)
    # Виправлено: фільтруємо low-confidence сигнали our_prob=0.98
    # ══════════════════════════════════════════════════════════
    # Додаткова перевірка: наша ймовірність повинна бути
    # реалістичною (не 0.98 для categorical коли threshold далеко)
    prob_sanity_ok = True
    if kind == "categorical" and threshold is not None:
        # Для categorical: our_prob має бути в розумних межах (0.05-0.90)
        if our_prob > 0.85 or our_prob < 0.05:
            prob_sanity_ok = False

    is_extreme_yes = (
        config.ENABLE_EXTREME_TAIL_YES
        and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES
        and eff_yes >= config.EXTREME_TAIL_MIN_EDGE_YES
        and prob_sanity_ok
        and confidence >= 0.80  # Тільки з хорошим прогнозом
    )

    # ── Шум-фільтр ────────────────────────────────────────────
    # Пропускаємо ринки де ціна < 0.5¢ (вже по суті закриті)
    if market.midpoint_yes < 0.005 and not is_coldmath_no:
        return None

    # ── Визначення напрямку та розміру ────────────────────────
    if is_coldmath_no:
        direction = "BUY_NO"
        eff_edge  = abs(eff_no)
        size_usd  = min(
            config.COLDMATH_MAX_SIZE_USD,
            max(config.MIN_POSITION_USD,
                config.BASE_POSITION_USD * confidence)
        )
        reason = (
            f"COLDMATH NO @ {no_price:.3f} | "
            f"прогноз:{fc_temp} | {src}"
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
            f"kind={kind} threshold={threshold}°C | "
            f"прогноз:{fc_temp} | {src}"
        )
        tradeable = True

    elif abs(eff_yes) >= config.MIN_EDGE_ENTRY or abs(eff_no) >= config.MIN_EDGE_ENTRY:
        if eff_yes >= eff_no:
            direction = "BUY_YES"
            eff_edge  = eff_yes
            reason = f"YES {our_prob:.2f} vs {market_prob:.2f} | {kind} | прогноз:{fc_temp} | {src}"
        else:
            direction = "BUY_NO"
            eff_edge  = eff_no
            reason = f"NO {our_prob:.2f} vs {market_prob:.2f} | {kind} | прогноз:{fc_temp} | {src}"
        size_usd  = min(
            config.BASE_POSITION_USD * confidence * 1.1,
            config.INITIAL_CAPITAL * config.MAX_POSITION_PCT
        )
        size_usd  = max(size_usd, config.MIN_POSITION_USD)
        tradeable = True

    else:
        # Немає достатнього edge
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
        is_tradeable=tradeable,
        size_usd=round(size_usd, 2),
    )


def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    """Сканувати всі ринки і повернути tradeable сигнали."""
    results = []
    skip_vol = skip_city = skip_hours = skip_none = 0

    for market in markets:
        # Попередні фільтри
        if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
            skip_vol += 1
            continue
        if market.hours_to_resolution < 2.0:
            skip_hours += 1
            continue
        if market.detected_city and hasattr(config, 'CITY_WHITELIST'):
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

    # Сортуємо за edge (найкращі спочатку)
    results.sort(key=lambda r: r.edge, reverse=True)

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} ринків "
        f"| skip: vol={skip_vol}, hours={skip_hours}, city={skip_city}, none={skip_none}"
    )
    return results
