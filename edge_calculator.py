"""
edge_calculator.py — Polymarket Weather Bot 2026
Розрахунок edge: порівняння прогнозу погоди з ринковою ціною.
Edge = різниця між "реальною" ймовірністю (з метеоданих) та ціною ринку.
"""

import math
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

import config
from data_fetcher import WeatherForecast, get_best_forecast, get_multi_source_consensus
from market_scanner import PolyMarket

logger = logging.getLogger(__name__)


@dataclass
class EdgeResult:
    """Результат розрахунку edge для ринку."""
    market: PolyMarket
    forecast: Optional[WeatherForecast]
    estimated_prob: Optional[float]    # Наша оцінка ймовірності (0–1)
    market_prob: float                 # Ринкова ймовірність (midpoint_yes)
    edge: float                        # edge = estimated_prob - market_prob
    edge_direction: str                # "BUY_YES" / "BUY_NO" / "SKIP"
    confidence: float                  # Рівень впевненості (0–1)
    reason: str                        # Причина рішення
    is_tradeable: bool                 # Чи варто торгувати?

    @property
    def edge_pct(self) -> str:
        return f"{self.edge:.1%}"

    @property
    def summary(self) -> str:
        return (f"{self.edge_direction} | edge={self.edge_pct} | "
                f"our_prob={self.estimated_prob:.2f} | market={self.market_prob:.2f} | "
                f"{self.reason}")


def _confidence_from_sources(forecast: WeatherForecast) -> float:
    """
    Рівень впевненості залежить від джерела та типу прогнозу.
    NOAA для США — найвища точність.
    """
    if forecast.source == "consensus_noaa+open_meteo":
        return 0.90
    if forecast.source == "noaa":
        return 0.85
    if forecast.source == "open_meteo":
        return 0.75
    return 0.60


def _estimate_prob_temperature(
    market: PolyMarket,
    forecast: WeatherForecast
) -> Optional[float]:
    """
    Оцінити ймовірність для temperature-ринку.
    Використовує нормальний розподіл із σ = 3–5°F (помилка прогнозу NWS).
    """
    if market.threshold_value is None:
        return None

    # Якщо поріг у Celsius — конвертуємо у Fahrenheit для NOAA
    threshold_f = market.threshold_value

    # Визначаємо, яка температура релевантна (high vs low)
    q_lower = market.question.lower()
    if "low" in q_lower or "overnight" in q_lower or "minimum" in q_lower:
        forecast_temp = forecast.temp_low_f
    else:
        forecast_temp = forecast.temp_high_f

    if forecast_temp is None:
        return None

    # Стандартне відхилення прогнозу (помилка NWS ~3-5°F для 24h)
    hours = market.hours_to_resolution
    sigma = max(2.5, min(6.0, 2.5 + hours / 24.0))  # Більше sigma для довших прогнозів

    diff = forecast_temp - threshold_f
    prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))

    if market.is_above is False:  # "below X"
        prob = 1 - prob

    return round(prob, 4)


def _estimate_prob_rain(
    market: PolyMarket,
    forecast: WeatherForecast
) -> Optional[float]:
    """
    Оцінити ймовірність для rain/precipitation ринків.
    """
    if forecast.precip_prob is not None:
        prob = forecast.precip_prob

        # Якщо є поріг опадів (наприклад "more than 0.5 inches")
        if market.threshold_value is not None and forecast.precip_mm is not None:
            threshold_mm = market.threshold_value * 25.4  # inches → mm
            # Пуасонівська апроксимація для кількості опадів
            if forecast.precip_mm > 0:
                ratio = forecast.precip_mm / threshold_mm
                prob = prob * min(1.0, ratio ** 0.5)
            else:
                prob = prob * 0.2  # Мала ймовірність перевищення

        return round(max(0.01, min(0.99, prob)), 4)

    return None


def _estimate_prob_snow(
    market: PolyMarket,
    forecast: WeatherForecast
) -> Optional[float]:
    """Оцінити ймовірність снігу."""
    if forecast.snow_mm is not None:
        if market.threshold_value is not None:
            threshold_mm = market.threshold_value * 25.4  # inches → mm
            if forecast.snow_mm >= threshold_mm:
                return round(min(0.90, forecast.precip_prob or 0.5), 4)
            else:
                ratio = forecast.snow_mm / (threshold_mm + 1e-6)
                return round(ratio * (forecast.precip_prob or 0.3) * 0.5, 4)

    if forecast.precip_prob and forecast.temp_high_f and forecast.temp_high_f < 35:
        return round(forecast.precip_prob * 0.85, 4)

    return None


def estimate_market_probability(
    market: PolyMarket,
    forecast: WeatherForecast
) -> Optional[float]:
    """
    Головна функція: оцінити ймовірність із метеоданих.
    """
    mtype = market.market_type

    if mtype == "temperature":
        return _estimate_prob_temperature(market, forecast)
    elif mtype == "rain":
        return _estimate_prob_rain(market, forecast)
    elif mtype == "snow":
        return _estimate_prob_snow(market, forecast)
    elif mtype == "freeze":
        # Freeze ≈ температура < 32°F
        if forecast.temp_low_f is not None:
            sigma = 3.0
            diff = 32 - forecast.temp_low_f
            prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))
            return round(prob, 4)
    elif mtype == "wind":
        if forecast.wind_kmh is not None and market.threshold_value:
            # Ймовірність перевищення порогу вітру
            diff = forecast.wind_kmh - market.threshold_value
            sigma = 5.0
            prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))
            if market.is_above is False:
                prob = 1 - prob
            return round(prob, 4)
    return None


def calculate_edge(market: PolyMarket) -> EdgeResult:
    """
    Головна функція: розрахувати edge для ринку.
    Отримує прогноз з двох джерел → оцінює ймовірність → порівнює з ринком.
    """
    city = market.detected_city or "unknown"
    market_prob = market.midpoint_yes

    # Отримати прогноз (консенсус двох джерел для вищої точності)
    forecast = get_multi_source_consensus(city) if city != "unknown" else None
    if not forecast:
        forecast = get_best_forecast(city)

    if not forecast:
        return EdgeResult(
            market=market,
            forecast=None,
            estimated_prob=None,
            market_prob=market_prob,
            edge=0.0,
            edge_direction="SKIP",
            confidence=0.0,
            reason=f"Немає прогнозу для міста: {city}",
            is_tradeable=False,
        )

    # Оцінити ймовірність
    estimated_prob = estimate_market_probability(market, forecast)

    if estimated_prob is None:
        return EdgeResult(
            market=market,
            forecast=forecast,
            estimated_prob=None,
            market_prob=market_prob,
            edge=0.0,
            edge_direction="SKIP",
            confidence=0.0,
            reason=f"Не вдалося розрахувати prob для типу: {market.market_type}",
            is_tradeable=False,
        )

    # Розрахунок edge
    edge_yes = estimated_prob - market_prob   # >0 = YES дешевий → BUY YES
    edge_no = (1 - estimated_prob) - (1 - market_prob)  # >0 = NO дешевий → BUY NO

    confidence = _confidence_from_sources(forecast)

    # Ефективний edge з урахуванням впевненості
    effective_edge = max(abs(edge_yes), abs(edge_no)) * confidence

    # Перевірка підозрілого edge (занадто великий = можливо помилка парсингу)
    if effective_edge > config.MAX_EDGE_CAP:
        return EdgeResult(
            market=market,
            forecast=forecast,
            estimated_prob=estimated_prob,
            market_prob=market_prob,
            edge=effective_edge,
            edge_direction="SKIP",
            confidence=confidence,
            reason=f"Edge {effective_edge:.1%} > max {config.MAX_EDGE_CAP:.0%} — підозрілий, пропускаємо",
            is_tradeable=False,
        )

    # Рішення
    if effective_edge < config.MIN_EDGE_ENTRY:
        direction = "SKIP"
        reason = f"Edge {effective_edge:.1%} < min {config.MIN_EDGE_ENTRY:.0%}"
        is_tradeable = False
    elif edge_yes >= edge_no:
        direction = "BUY_YES"
        reason = (f"YES дешевий: наша P={estimated_prob:.2f} > ринок {market_prob:.2f} | "
                  f"джерело: {forecast.source}")
        is_tradeable = True
    else:
        direction = "BUY_NO"
        reason = (f"NO дешевий: наша P={estimated_prob:.2f} < ринок {market_prob:.2f} | "
                  f"джерело: {forecast.source}")
        is_tradeable = True

    return EdgeResult(
        market=market,
        forecast=forecast,
        estimated_prob=estimated_prob,
        market_prob=market_prob,
        edge=effective_edge,
        edge_direction=direction,
        confidence=confidence,
        reason=reason,
        is_tradeable=is_tradeable,
    )


def scan_all_edges(markets: list) -> list:
    """
    Розрахувати edge для всіх ринків.
    Повертає відсортований список EdgeResult (найкращі нагорі).
    """
    results = []
    for market in markets:
        result = calculate_edge(market)
        results.append(result)
        if result.is_tradeable:
            logger.info(f"✅ EDGE: {result.summary}")
        else:
            logger.debug(f"⏭ SKIP: {result.reason} | {market.question[:50]}")

    # Сортуємо за edge (найбільший — першим)
    results.sort(key=lambda r: r.edge, reverse=True)
    tradeable = [r for r in results if r.is_tradeable]
    logger.info(f"Знайдено {len(tradeable)} можливостей з {len(results)} ринків")
    return results
