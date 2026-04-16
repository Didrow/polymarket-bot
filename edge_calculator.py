"""
edge_calculator.py — Polymarket Weather Bot 2026 (@coldmath style)

ВИПРАВЛЕНО:
  - мертвий код після return видалено
  - prob_above_temp_f тепер працює правильно через °C
  - our_prob=0.00 більше не буде (межі 0.01-0.99)
  - categorical ринки (точна температура) обробляються правильно
  - логування показує реальний прогноз vs ринок
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
        ep = f"{self.estimated_prob:.2f}" if self.estimated_prob is not None else "N/A"
        return (f"{self.edge_direction} | edge={self.edge_pct} | "
                f"our_prob={ep} | market={self.market_prob:.2f} | "
                f"{self.reason}")


def _confidence_from_sources(forecast: WeatherForecast) -> float:
    src = forecast.source
    if "consensus" in src:
        # Більше джерел = вища впевненість
        n = src.count("_") + 1
        return min(0.97, 0.88 + n * 0.02)
    if src == "noaa":
        return 0.92
    if src in ["ecmwf_open_meteo"]:
        return 0.90
    if src in ["gfs_open_meteo"]:
        return 0.87
    if src == "nasa_power":
        return 0.80
    return 0.78


def estimate_market_probability(market: PolyMarket,
                                 forecast: WeatherForecast) -> float:
    """
    Розрахунок нашої оцінки ймовірності.
    
    Ключове виправлення: threshold_value у ринках Polymarket — ЗАВЖДИ в °C
    (ринки типу "Will temp be 32°C?"), а не у °F.
    Тому ми порівнюємо з temp_high_c, а не temp_high_f.
    """
    if not forecast:
        return 0.50

    q = market.question.lower()
    threshold = market.threshold_value
    is_above = market.is_above

    # ── Температурні ринки ────────────────────────────────────
    if market.market_type == "temperature" and threshold is not None:

        # Визначаємо одиниці виміру
        is_fahrenheit = "°f" in q or " f " in q or "fahrenheit" in q
        is_celsius = "°c" in q or " c " in q or "celsius" in q or not is_fahrenheit

        if is_celsius:
            threshold_c = threshold
        else:
            # Якщо Fahrenheit — конвертуємо в Celsius
            threshold_c = (threshold - 32) * 5 / 9

        if is_above is True:
            # "Will temp be ABOVE X°C?"
            return forecast.prob_above_temp_c(threshold_c)
        elif is_above is False:
            # "Will temp be BELOW X°C?"
            return 1.0 - forecast.prob_above_temp_c(threshold_c)
        else:
            # Categorical: "Will temp be EXACTLY X°C?"
            # Використовуємо prob_exact_temp_c
            return forecast.prob_exact_temp_c(threshold_c)

    # ── Дощ ──────────────────────────────────────────────────
    if market.market_type == "rain":
        p = forecast.prob_rain()
        if p is not None:
            return p
        # Fallback: якщо precip_mm > 0.5mm
        if forecast.precip_mm is not None:
            return min(0.90, max(0.05, forecast.precip_mm / 15.0))
        return 0.30

    # ── Сніг ─────────────────────────────────────────────────
    if market.market_type == "snow":
        p = forecast.prob_snow()
        if p is not None:
            return p
        return 0.10

    return 0.50


def calculate_edge(market: PolyMarket) -> EdgeResult:
    city = market.detected_city or "unknown"
    market_prob = market.midpoint_yes

    # Отримуємо найкращий прогноз
    forecast = None
    if city != "unknown":
        forecast = get_multi_source_consensus(city)
    if not forecast:
        forecast = get_best_forecast(city) if city != "unknown" else None

    # Розрахунок нашої ймовірності
    if forecast:
        raw_prob = estimate_market_probability(market, forecast)
    else:
        raw_prob = 0.50

    # Жорсткі межі — ніколи не 0 або 1
    estimated_prob = max(0.02, min(0.98, raw_prob))

    edge_yes = estimated_prob - market_prob
    edge_no = (1 - estimated_prob) - (1 - market_prob)
    confidence = _confidence_from_sources(forecast) if forecast else 0.65

    effective_edge = max(abs(edge_yes), abs(edge_no)) * confidence

    # ── @coldmath стратегія: BUY NO @ 93-99¢ ─────────────────
    is_coldmath_tail_no = (
        config.ENABLE_COLDMATH_TAIL_NO
        and market.midpoint_yes <= (1 - config.COLDMATH_MIN_ASK_NO)
        and (1 - market.midpoint_yes) >= config.COLDMATH_MIN_ASK_NO
        and edge_no > config.COLDMATH_MIN_EDGE_NO
        and market.volume_usd >= config.MIN_MARKET_VOLUME_USD
    )

    # ── Extreme tail YES @ 1-5¢ ───────────────────────────────
    is_extreme_tail_yes = (
        config.ENABLE_EXTREME_TAIL_YES
        and market.best_ask_yes <= config.EXTREME_TAIL_MAX_ASK_YES
        and edge_yes > config.EXTREME_TAIL_MIN_EDGE_YES
        and market.volume_usd >= 500
    )

    # ── Фільтр шуму ───────────────────────────────────────────
    # Пропускаємо ринки де ціна < 2¢ або > 98¢ і об'єм < $1000
    if (market.midpoint_yes < 0.02 or market.midpoint_yes > 0.98) and market.volume_usd < 1000:
        return EdgeResult(
            market=market, forecast=forecast,
            estimated_prob=estimated_prob, market_prob=market_prob,
            edge=0.0, edge_direction="SKIP", confidence=confidence,
            reason="LOW_VOL_EXTREME_BIN → шум",
            is_tradeable=False
        )

    src = forecast.source if forecast else "no_forecast"
    fc_info = ""
    if forecast:
        thc = forecast.temp_high_c
        pp = forecast.precip_prob
        fc_info = f"forecast: {thc:.1f}°C" if thc else ""
        if pp is not None:
            fc_info += f", rain={pp:.0%}"

    if is_coldmath_tail_no:
        effective_edge = max(effective_edge, abs(edge_no) * 1.40)
        confidence = min(0.97, confidence + 0.08)
        reason = (f"COLDMATH NO @ {market.midpoint_yes:.3f} YES "
                  f"({1-market.midpoint_yes:.2f}¢ NO) | {src} | {fc_info}")
        direction = "BUY_NO"
        is_tradeable = True

    elif is_extreme_tail_yes:
        effective_edge = max(effective_edge, abs(edge_yes) * 1.35)
        confidence = min(0.95, confidence + 0.07)
        reason = (f"EXTREME YES @ {market.best_ask_yes:.3f}¢ | "
                  f"{src} | {fc_info}")
        direction = "BUY_YES"
        is_tradeable = True

    elif effective_edge >= config.MIN_EDGE_ENTRY:
        if edge_yes >= edge_no:
            direction = "BUY_YES"
            reason = (f"YES дешевий: наша P={estimated_prob:.2f} > "
                      f"ринок {market_prob:.2f} | {src} | {fc_info}")
        else:
            direction = "BUY_NO"
            reason = (f"NO дешевий: наша P={estimated_prob:.2f} < "
                      f"ринок {market_prob:.2f} | {src} | {fc_info}")
        is_tradeable = True

    else:
        direction = "SKIP"
        reason = (f"edge={effective_edge:.1%} < min {config.MIN_EDGE_ENTRY:.0%} | "
                  f"{src}")
        is_tradeable = False

    return EdgeResult(
        market=market, forecast=forecast,
        estimated_prob=estimated_prob, market_prob=market_prob,
        edge=effective_edge, edge_direction=direction,
        confidence=confidence, reason=reason, is_tradeable=is_tradeable
    )


def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    skipped = 0
    for market in markets:
        if market.volume_usd < config.MIN_MARKET_VOLUME_USD:
            skipped += 1
            continue
        edge = calculate_edge(market)
        if edge.is_tradeable:
            logger.info(f"✅ EDGE: {edge.summary}")
            results.append(edge)
        else:
            logger.debug(f"⏭  SKIP: {edge.reason} | {market.question[:45]}")

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} ринків "
        f"({skipped} пропущено за volume)"
    )
    return results
