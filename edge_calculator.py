"""
edge_calculator.py — Polymarket Weather Bot (GRID / YES LADDERING EDITION)

Адаптовано під стратегію "fridius2" + ColdMath:
- Повністю вимкнено пошук угод BUY_NO.
- Агресивний пошук дешевих BUY_YES (до 12 центів) через Ансамблеві ймовірності.
- Купівля сусідніх температур для створення "Рибальської сітки" навколо прогнозу.
- Адаптивна фільтрація за спредом (max spread 5 центів) для уникнення неліквідних ринків.
- Фільтр відстані для categorical ринків (max 3°C від прогнозу) з коректною обробкою 0°C та мінусових температур.
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
# ПАРСИНГ ДІАПАЗОНІВ ТА ПОРОГІВ (оновлено для підтримки range)
# ══════════════════════════════════════════════════════════════════

def _parse_range_or_threshold(question: str) -> Tuple[str, Optional[float], Optional[float], str]:
    """
    Парсить запитання на предмет діапазонів або одинарних порогів температур.
    Повертає: (kind, val_min, val_max, unit)
    """
    q_lower = question.lower()
    
    # Визначаємо одиницю вимірювання (США за замовчуванням Фаренгейт)
    unit = 'C'
    if '°f' in q_lower or ' f ' in q_lower or 'fahrenheit' in q_lower or q_lower.endswith('f') or 'f?' in q_lower:
        unit = 'F'
    else:
        fahrenheit_cities = {"chicago", "dallas", "nyc", "new york", "san francisco",
                             "miami", "los angeles", "seattle", "atlanta", "boston",
                             "denver", "phoenix", "las vegas", "austin", "minneapolis",
                             "portland", "houston", "nashville", "charlotte", "orlando"}
        if any(city in q_lower for city in fahrenheit_cities):
            unit = 'F'

    # 1. Пошук діапазону температур "between X and Y" або "X-Y°F"
    range_match = re.search(r'(?:between\s+)?(\d+\.?\d*)\s*[-–to\s+a-nd]+\s*(\d+\.?\d*)', q_lower)
    if range_match:
        try:
            val1 = float(range_match.group(1))
            val2 = float(range_match.group(2))
            if val1 < 120 and val2 < 120 and val1 != val2:
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

    # Парсинг одинарного значення
    m = re.search(r'(\d+\.?\d*)\s*°?\s*[FfCc]\b', q_lower)
    if m:
        return kind, float(m.group(1)), None, unit

    m = re.search(r'be (\d+\.?\d*)', q_lower)
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


# ══════════════════════════════════════════════════════════════════
# РОЗРАХУНОК ЙМОВІРНОСТІ (з підтримкою діапазонів)
# ══════════════════════════════════════════════════════════════════

def estimate_market_probability(market: PolyMarket, forecast: WeatherForecast) -> Tuple[float, float, str]:
    if not forecast:
        return 0.50, 0.0, "no_forecast"

    kind, val_min, val_max, unit = _parse_range_or_threshold(market.question)
    is_low = 'lowest' in market.question.lower()

    if kind == "range":
        members = forecast.temp_low_members if is_low else forecast.temp_high_members
        
        if unit == 'F':
            # Для Фаренгейту межі бакета [val_min - 0.5, val_max + 0.5]
            t_min_f = val_min - 0.5
            t_max_f = val_max + 0.5
            
            if members:
                # Частота попадання членів ансамблю в діапазон після конвертації в F
                count = sum(1 for m in members if t_min_f <= m * 9/5 + 32 < t_max_f)
                p = count / len(members)
                if p == 0.0:
                    mean_f = (sum(members) / len(members)) * 9/5 + 32
                    p = 0.03 if abs(mean_f - (val_min + val_max)/2) <= 2.5 else 0.01
            else:
                # Fallback: один прогноз
                mean_f = (forecast.temp_low_c if is_low else forecast.temp_high_c) * 9/5 + 32
                sigma = 3.6  # ~2.0°C у Фаренгейтах
                p_high = 0.5 * (1 + math.erf((t_max_f - mean_f) / (sigma * math.sqrt(2))))
                p_low  = 0.5 * (1 + math.erf((t_min_f - mean_f) / (sigma * math.sqrt(2))))
                p = p_high - p_low
            label = f"range|{val_min}-{val_max}°F"
            threshold_c = _f_to_c((val_min + val_max) / 2)
        else:
            # Для Цельсія межі [val_min - 0.5, val_max + 0.5]
            t_min_c = val_min - 0.5
            t_max_c = val_max + 0.5
            
            if members:
                count = sum(1 for m in members if t_min_c <= m < t_max_c)
                p = count / len(members)
                if p == 0.0:
                    mean_c = sum(members) / len(members)
                    p = 0.03 if abs(mean_c - (val_min + val_max)/2) <= 1.5 else 0.01
            else:
                mean_c = forecast.temp_low_c if is_low else forecast.temp_high_c
                sigma = 2.0
                p_high = 0.5 * (1 + math.erf((t_max_c - mean_c) / (sigma * math.sqrt(2))))
                p_low  = 0.5 * (1 + math.erf((t_min_c - mean_c) / (sigma * math.sqrt(2))))
                p = p_high - p_low
            label = f"range|{val_min}-{val_max}°C"
            threshold_c = (val_min + val_max) / 2

        return round(max(0.01, min(0.99, p)), 4), threshold_c, label

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
        p = forecast.prob_above_temp_c(threshold_c, is_low)
    elif kind == "below":
        p = forecast.prob_below_temp_c(threshold_c, is_low)
    else:
        half_width = 0.2778 if unit == 'F' else 0.5
        p = forecast.prob_exact_temp_c(threshold_c, is_low, half_width=half_width)

    return round(p, 4), threshold_c, f"{kind}|{unit_label}"


# ══════════════════════════════════════════════════════════════════
# ОБЧИСЛЕННЯ EDGE (з фільтром спреду та відстані)
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

    # 🛑 АДАПТИВНИЙ ФІЛЬТР ЗА СПРЕДОМ (MAX 5 ЦЕНТІВ, ДЛЯ ДЕШЕВИХ КВИТКІВ 10 ЦЕНТІВ)
    spread = market.best_ask_yes - market.best_bid_yes
    max_spread = 0.10 if market.best_ask_yes <= 0.15 else 0.05
    if spread > max_spread:
        logger.debug(f"Пропускаємо {market.question[:40]} через широкий спред: {spread:.3f}")
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
    fc_temp = forecast.temp_low_c if is_low else forecast.temp_high_c   # float
    src = "ENSEMBLE" if (hasattr(forecast, 'temp_high_members') and forecast.temp_high_members) else "SINGLE"

    direction = "BUY_YES"
    # Ефективний edge з урахуванням впевненості моделі
    eff_edge = (our_prob - market_prob) * confidence

    # 🎣 СТРАТЕГІЯ 1: GRID YES (Ловимо дешеві хвости)
    GRID_MIN_PRICE = 0.02
    
    # Фільтр відстані для categorical ринків.
    # Використовуємо is not None для коректної обробки 0°C та від'ємних температур.
    _dist_ok = True
    if "categorical" in kind_label and threshold_c is not None and fc_temp is not None:
        _dist_ok = abs(fc_temp - threshold_c) <= 1.5
        if not _dist_ok:
            logger.debug(f"Пропускаємо categorical: прогноз={fc_temp:.1f}°C, ціль={threshold_c:.1f}°C, різниця >1.5°C")
        # Не купуємо categorical YES, якщо прогноз НИЖЧЕ порогу
        # Polymarket categorical бакет = [threshold-0.5, threshold+0.5)
        # Якщо прогноз нижче бакета — майже гарантований програш
        if _dist_ok and fc_temp < threshold_c - 0.5:
            logger.debug(f"Пропускаємо categorical: прогноз={fc_temp:.1f}°C нижче порогу {threshold_c:.0f}°C (бакет від {threshold_c-0.5:.1f}°C)")
            _dist_ok = False
    
    is_grid_yes = (
        "range" not in kind_label           # Грід-сітка тільки для точкових категоріальних температур
        and _dist_ok                        # Ціль не далі 3°C від прогнозу
        and market_prob >= GRID_MIN_PRICE
        and market_prob <= config.EXTREME_TAIL_MAX_ASK_YES
        and our_prob >= 0.08
        and eff_edge >= config.EXTREME_TAIL_MIN_EDGE_YES
    )

    # 🎯 СТРАТЕГІЯ 2: SNIPER YES (Основний прогноз або діапазони)
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
        # Лічильник для спреду
        spread = market.best_ask_yes - market.best_bid_yes
        max_spread = 0.10 if market.best_ask_yes <= 0.15 else 0.05
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

    # Сортування: Grid угоди мають пріоритет за потенціалом R:R
    results.sort(key=lambda r: r.edge, reverse=True)

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} ринків "
        f"| skip: vol={skip_vol}, hours={skip_hours}, city={skip_city}, spread={skip_spread}, none={skip_none}"
    )
    return results
