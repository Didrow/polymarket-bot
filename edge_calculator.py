"""
edge_calculator.py — Weather Bot v23 (SNIPER GRID)

ПОВЕРТАЄМО ПРИБУТКОВУСТРАТЕГІЮ з logbest.md (ROI +105%, 36 trades, $+112 realized):
  - Сітка categorical бакетів навоколи forecast (крок 1°C/1°F)
  - Range бакети (84.0-85.0°F, 27-28°C) — найбільш ліквідні
  - Малі bets дешевими YES tails (1-20¢) з our_prob 3-30%
  - Kelly quarter sizing

УРОКИ ВІД v9-v22 (не повторювати!):
  - v9:   0.30 over-calibration → вбила сітку (0/6 win)
  - v14:  forecast-bets + trailing stops → 0% WR
  - v15-v19: METAR arb → 0% WR (13 збитків підряд)
  - v21:  SIGMA_MIN=1.5 → our_prob=0.0000 для 90% ринків
  - v22:  правильні SIGMA_MIN=4.5 + PROB_BIAS=1.0 — OK, ЗАЛИШАЄМО

СТРАТЕГІЯ:
  - KINDS_ONLY = ["above", "below", "categorical", "range"]
  - Купуємо YES дешевші 30¢ коли our_prob > market_prob + edge
  - Купуємо NO  дорожчі 70¢ коли market_prob < our_prob + edge
  - Сітка бакетів ±1.0°C/1.0°F навоколи прогнозу через adjacent grid
"""

import math
import re
import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from dataclasses import dataclass

import config
from data_fetcher import WeatherForecast, get_best_forecast
from market_scanner import PolyMarket, get_target_date

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


def _c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


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

    # categorical "be 20 on" / "be exactly 20"
    m = re.search(r'\bbe\s+(-?\d+\.?\d*)\s+(?:on\b|or\b)', q_lower)
    if m:
        return "categorical", float(m.group(1)), unit

    m = re.search(r'\b(\d+\.?\d*)\s*(?:°|degree)', q_lower)
    if m:
        return "categorical", float(m.group(1)), unit

    return kind, None, unit


def _parse_range(question: str, unit: str) -> Optional[Tuple[float, float]]:
    """Повертає (low_c, high_c) або None."""
    _range_re = re.search(
        r'between\s+([-+]?\d+\.?\d*)\s*°?\s*[cf]?\s*(?:-|–|to|and)\s*([-+]?\d+\.?\d*)\s*°?\s*[cf]?',
        question, re.IGNORECASE
    ) or re.search(
        r'([-+]?\d+\.?\d*)\s*°?\s*[cf]?\s*(?:-|–)\s*([-+]?\d+\.?\d*)\s*°?\s*[cf]?\b',
        question, re.IGNORECASE
    )
    if not _range_re:
        return None

    try:
        lo = float(_range_re.group(1))
        hi = float(_range_re.group(2))
        if abs(lo) < 200 and abs(hi) < 200 and lo != hi:
            lo, hi = min(lo, hi), max(lo, hi)
            if unit == 'F':
                return (_f_to_c(lo), _f_to_c(hi))
            return (lo, hi)
    except ValueError:
        pass
    return None


# ── PROBABILITY ─────────────────────────────────────────────

def _get_cap_exact(hours: float) -> float:
    """для categorical/range бакетів — conservative caps."""
    if hours <= 6.0:
        return getattr(config, 'CAP_EXACT_SHORT', 0.60)
    elif hours <= 18.0:
        return getattr(config, 'CAP_EXACT_MID', 0.48)
    return getattr(config, 'CAP_EXACT_LONG', 0.38)


def _get_cap_trend(hours: float) -> float:
    """для above/below — трохи вищі caps (Гаусівський хвіст)."""
    if hours <= 6.0:
        return getattr(config, 'CAP_SHORT', 0.85)
    elif hours <= 18.0:
        return getattr(config, 'CAP_MID', 0.75)
    return getattr(config, 'CAP_LONG', 0.65)


def _prob_exact_gauss(forecast: WeatherForecast, low_c: float, high_c: float,
                     is_low: bool, hours: float) -> float:
    """
    Ймовірність потрапити в бакет [low_c, high_c] через Гаусівський CDF.
    Це і є ключ func для снайперської сітки (categorical center + range).
    Stays between [0.01, cap].
    """
    sigma = forecast._get_sigma(hours)
    mean = forecast.temp_low_c if is_low else forecast.temp_high_c

    # P(X ≤ high) - P(X ≤ low)
    cdf_high = 0.5 * (1 + math.erf((high_c - mean) / (sigma * math.sqrt(2))))
    cdf_low  = 0.5 * (1 + math.erf((low_c  - mean) / (sigma * math.sqrt(2))))
    raw = max(0.0, cdf_high - cdf_low)

    # empirical blend (beween 0-30%, common v13 value):
    # if adj_members available, mix in count-based prob
    emp_weight = getattr(config, 'EMPIRICAL_WEIGHT', 0.30)
    if emp_weight > 0:
        members = forecast._get_adjusted_members(is_low)
        if members and len(members) >= 5:
            count_in = sum(1 for m in members if low_c <= m < high_c)
            prob_emp = count_in / len(members)
            raw = (1 - emp_weight) * raw + emp_weight * prob_emp

    # categorical discount — slight reduction for ensemble member correlation
    # v23b: 0.75 was too aggressive, making our_prob systematically too low
    # → bot only found extreme tails that rarely hit (0% WR over 11 trades)
    discount = getattr(config, 'CATEGORICAL_DISCOUNT', 0.90)
    raw *= discount

    cap = _get_cap_exact(hours)
    return max(0.01, min(cap, round(raw, 4)))


def _prob_trend_gauss(forecast: WeatherForecast, threshold_c: float, kind: str,
                     is_low: bool, hours: float) -> float:
    """Ймовірність для above/below — Гаус хвіст (from v21/v22)."""
    sigma = forecast._get_sigma(hours)
    mean = forecast.temp_low_c if is_low else forecast.temp_high_c

    if kind == "above":
        raw = 0.5 * (1 + math.erf((mean - threshold_c) / (sigma * math.sqrt(2))))
    else:
        raw = 0.5 * (1 + math.erf((threshold_c - mean) / (sigma * math.sqrt(2))))

    prob_bias = getattr(config, 'PROB_BIAS', 1.0)
    if prob_bias != 1.0:
        raw *= prob_bias

    cap = _get_cap_trend(hours)
    return max(0.01, min(cap, round(raw, 4)))


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

    # вид ринку from market_scanner parse
    kind = market.kind or _detect_market_kind(market.question)
    allowed = getattr(config, 'KINDS_ONLY', ['above', 'below', 'categorical', 'range'])
    if kind not in allowed:
        return None

    from market_scanner import get_target_date
    t_date = get_target_date(market.question, market.end_date, city)
    forecast = get_best_forecast(city, hours_to_resolution=market.hours_to_resolution, target_date=t_date)
    if not forecast:
        return None

    is_low = 'lowest' in market.question.lower()

    # визначаємо цент prior to порога / бакета
    unit = _unit_from_question(market.question)
    if kind in ("categorical", "range"):
        # для range — беремо проміжок ринку, для categorical — будуємо бакет ± halfWidth
        if kind == "range":
            range_c = _parse_range(market.question, unit)
            if range_c is None:
                # fall back to scanner data
                rl = market.range_low
                rh = market.range_high
                if rl is None or rh is None:
                    return None
                if unit == 'F':
                    range_c = (_f_to_c(rl), _f_to_c(rh))
                else:
                    range_c = (rl, rh)
            low_c, high_c = range_c
            threshold_c = (low_c + high_c) / 2.0
            our_prob = _prob_exact_gauss(forecast, low_c, high_c, is_low, market.hours_to_resolution)
        else:  # categorical
            parsed_kind, threshold_value, punit = _parse_threshold(market.question)
            if threshold_value is None:
                threshold_value = market.threshold_value
            if threshold_value is None:
                return None
            if punit == 'F' or (punit == '' and unit == 'F'):
                threshold_c = _f_to_c(threshold_value)
            else:
                threshold_c = threshold_value
            # бакет ±0.5°C для Celsius, ±0.5°F (≈0.278°C) для Fahrenheit
            if unit == 'F':
                half_c = 0.278  # 0.5°F у °C
            else:
                half_c = 0.5
            low_c = threshold_c - half_c
            high_c = threshold_c + half_c
            our_prob = _prob_exact_gauss(forecast, low_c, high_c, is_low, market.hours_to_resolution)
    else:  # above / below
        parsed_kind, threshold_value, punit = _parse_threshold(market.question)
        if threshold_value is None:
            threshold_value = market.threshold_value
        if threshold_value is None:
            return None
        if punit == 'F' or (punit == '' and unit == 'F'):
            threshold_c = _f_to_c(threshold_value)
        else:
            threshold_c = threshold_value
        our_prob = _prob_trend_gauss(forecast, threshold_c, kind, is_low, market.hours_to_resolution)

    # Відстань forecast від порога
    fc_temp = forecast.temp_low_c if is_low else forecast.temp_high_c
    distance_c = abs(fc_temp - threshold_c)

    # SNIPER GRID distance limit — skip бакети, що далеко від прогнозу
    max_dist_c = getattr(config, 'SNIPER_GRID_DISTANCE_C', 4.0)
    if kind in ("categorical", "range"):
        if distance_c > max_dist_c:
            return None
    else:
        # above/below — tail-chase check (from v22)
        max_dist_sigma = getattr(config, 'MAX_DISTANCE_SIGMA', 3.5)
        sigma_edge = forecast._get_sigma(market.hours_to_resolution)
        if distance_c > max_dist_sigma * sigma_edge and our_prob > 0.50:
            return None

    # ── v24 TAIL FILTERS ──────────────────────────────────────
    # (B) distance×prob: далеко від прогнозу + низька ймовірність = tail gamble
    max_tail_dist = getattr(config, 'MAX_TAIL_DIST_C', 1.5)
    max_tail_prob = getattr(config, 'MAX_TAIL_COMBINED_PROB', 0.12)
    if distance_c > max_tail_dist and our_prob < max_tail_prob:
        logger.info(
            f"🚫 TAIL-FILTER (B): skip {market.question[:40]} | "
            f"dist={distance_c:.1f}°C > {max_tail_dist} & our_prob={our_prob:.0%} < {max_tail_prob:.0%}"
        )
        return None

    # (D) MAX_TAIL_PROB — забороняємо BUY_YES коли our_prob занадто низька
    # (незалежно від edge%, бо наші our_prob=5-12% на дешевих YES — майже 0% WR)
    # Цей фільтр застосовується до BUY_YES; BUY_NO не потребує (NO завжди має високу our_prob)
    max_tail_yes_prob = getattr(config, 'MAX_TAIL_PROB', 0.20)
    if kind in ("categorical", "range") and our_prob < max_tail_yes_prob:
        logger.info(
            f"🚫 TAIL-FILTER (D): skip BUY_YES {market.question[:40]} | "
            f"our_prob={our_prob:.0%} < MAX_TAIL_PROB={max_tail_yes_prob:.0%}"
        )
        return None

    # Ринкова ціна
    market_prob = market.best_ask_yes
    if market_prob is None or market_prob < 0.01:
        midpoint = getattr(market, "midpoint_yes", 0.0)
        if 0.01 <= midpoint <= 0.99:
            market_prob = midpoint
        else:
            return None

    if market_prob <= 0.001 or market_prob >= 0.999:
        return None

    # Edge для YES та NO
    edge_yes = our_prob - market_prob
    edge_no = market_prob - our_prob

    # Минимальні пороги: диференціація по типах ринку
    if kind in ("categorical", "range"):
        min_edge_yes = getattr(config, 'SNIPER_GRID_MIN_EDGE', 0.04)
        min_edge_no  = getattr(config, 'SNIPER_GRID_MIN_EDGE_NO', 0.10)
    else:
        min_edge_yes = getattr(config, 'MIN_EDGE_YES', 0.20)
        min_edge_no  = getattr(config, 'MIN_EDGE_NO', 0.20)

    min_prob = getattr(config, 'MIN_PROB_ENTRY', 0.03)
    max_edge = getattr(config, 'MAX_EDGE_CAP', 0.50)

    # SNIPER GRID price range: дозволяємо дешеві YES (1-50¢)
    # — це головний секрет прибутковості (coldmath, neobrother, logbest.md)
    ask_max_grid = getattr(config, 'SNIPER_GRID_MAX_ASK', 0.50)
    ask_min_grid = getattr(config, 'SNIPER_GRID_MIN_ASK', 0.01)

    if kind in ("categorical", "range"):
        # для grid YES потрібна ціна в діапазоні
        if edge_yes >= min_edge_yes and our_prob >= min_prob:
            if not (ask_min_grid <= market_prob <= ask_max_grid):
                return None
            edge_direction = "BUY_YES"
            eff_edge = min(edge_yes, max_edge)
            reason = f"SNIPER GRID YES @ {market_prob:.3f}"
        elif edge_no >= min_edge_no and (1 - market_prob) >= min_prob:
            # NO — продаємо ринки > 60¢
            if market_prob < getattr(config, 'SNIPER_GRID_NO_MIN_MARKET', 0.60):
                return None
            edge_direction = "BUY_NO"
            eff_edge = min(edge_no, max_edge)
            reason = f"SNIPER GRID NO @ {market_prob:.3f}"
        else:
            return None
    else:
        # above/below — standard
        if edge_yes >= min_edge_yes and our_prob >= min_prob:
            if market_prob > getattr(config, 'TREND_MAX_ASK', 0.70):
                return None
            edge_direction = "BUY_YES"
            eff_edge = min(edge_yes, max_edge)
            reason = f"TREND YES {kind.upper()} @ {market_prob:.3f}"
        elif edge_no >= min_edge_no and (1 - market_prob) >= min_prob:
            if market_prob < getattr(config, 'TREND_MIN_NO_MARKET', 0.30):
                return None
            edge_direction = "BUY_NO"
            eff_edge = min(edge_no, max_edge)
            reason = f"TREND NO {kind.upper()} @ {market_prob:.3f}"
        else:
            return None

    tradeable = True
    confidence = _confidence_from_forecast(forecast)

    # dettaglio лоg для tradeable
    bucket_str = ""
    if kind == "range":
        bucket_str = f" | {low_c:.1f}-{high_c:.1f}°C"
    elif kind == "categorical":
        bucket_str = f" | bucket≈{threshold_c:.1f}°C"

    logger.info(
        f"✅ EDGE: {market.question[:50]} | "
        f"our={our_prob:.0%} | mkt={market_prob:.0%} | edge={eff_edge:.1%} | "
        f"{edge_direction} | dist={distance_c:.1f}°C | {kind}{bucket_str}"
    )
    logger.info(
        f"✅ EDGE: {edge_direction} | edge={eff_edge:.1%} | "
        f"our_prob={our_prob:.2f} | market={market_prob:.2f} | "
        f"{reason}"
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

def _max_spread_for(market: PolyMarket) -> float:
    """Адаптивний spread filter — для дешевих дозволяємо ширший."""
    ask = market.best_ask_yes
    if ask is None:
        return 0.05
    if ask <= 0.15:
        return max(0.10, ask)  # cheap tail — дозволяємо спред рівно ask
    if ask <= 0.50:
        return 0.20
    return 0.08


def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results = []
    skip_vol = skip_city = skip_hours = skip_none = skip_spread = skip_kind = 0

    # ── PREFETCH: зігріваємо кеш forecast для унікальних міст ──
    # Беремо тільки міста з whitelist, сортуємо за кількістю ринків
    # (проксі ліквідності) і обрізаємо до MAX_PREFETCH_CITIES — щоб час
    # скану був ОБМЕЖЕНИЙ незалежно від розміру CITY_WHITELIST (захист від
    # повільного prefetch non-US міст, що б'ють 429 на ensemble-api).
    city_market_count: Dict[str, int] = {}
    for m in markets:
        c = m.detected_city
        if c and c in getattr(config, 'CITY_WHITELIST', []):
            city_market_count[c] = city_market_count.get(c, 0) + 1
    max_prefetch = getattr(config, 'MAX_PREFETCH_CITIES', 24)
    unique_cities = [c for c, _ in sorted(city_market_count.items(), key=lambda x: -x[1])[:max_prefetch]]
    if city_market_count:
        logger.info(
            f"🌤️ Prefetch forecast для {len(unique_cities)}/{len(city_market_count)} міст "
            f"(cap={max_prefetch}, топ за обсягом)..."
        )
        for i, city in enumerate(unique_cities, 1):
            try:
                get_best_forecast(city, hours_to_resolution=24.0, target_date=None)
            except Exception as e:
                logger.debug(f"Prefetch {city} skip: {e}")
            if i % 10 == 0:
                logger.info(f"  prefetch: {i}/{len(unique_cities)} міст done")
        logger.info(f"  prefetch завершено ({len(unique_cities)} міст)")

    for idx, market in enumerate(markets):
        if len(markets) > 400 and idx % 200 == 0 and idx > 0:
            logger.info(f"  scan progress: {idx}/{len(markets)} processed, {len(results)} tradeable so far")
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
        allowed = getattr(config, 'KINDS_ONLY', ['above', 'below', 'categorical', 'range'])
        if kind not in allowed:
            skip_kind += 1
            continue

        spread = (market.best_ask_yes or 0) - (market.best_bid_yes or 0)
        if spread > _max_spread_for(market):
            skip_spread += 1
            continue

        edge = calculate_edge(market)
        if edge is None:
            skip_none += 1
            continue

        if edge.is_tradeable:
            results.append(edge)

    # Sort: peak бакет (найменший distance_c) з високим edge — першим
    results.sort(key=lambda r: (r.edge, -r.distance_c), reverse=True)

    # SNIPER GRID PRINT: детальний вигляд сітки по містах
    _print_grid_summary(results)

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} markets "
        f"| skip: vol={skip_vol}, hours={skip_hours}, city={skip_city}, "
        f"kind={skip_kind}, spread={skip_spread}, none={skip_none}"
    )
    return results


def _print_grid_summary(results: List[EdgeResult]) -> None:
    """Допоміжна функція: показує сітку по містах (як у logbest.md)."""
    if not results:
        return
    by_city: dict = {}
    for r in results:
        c = r.market.detected_city or "?"
        by_city.setdefault(c, []).append(r)

    for city, edges in sorted(by_city.items()):
        # show top 3 per city
        for e in edges[:3]:
            bucket = ""
            if e.kind == "range":
                rl = e.market.range_low
                rh = e.market.range_high
                unit = _unit_from_question(e.market.question)
                if rl is not None and rh is not None:
                    bucket = f"|{rl:g}-{rh:g}{unit} "
            elif e.kind == "categorical":
                unit = _unit_from_question(e.market.question)
                # categorical мали threshold_c; show у вихідній одиниці
                if unit == 'F':
                    bucket = f"|{_c_to_f(e.threshold_c):.1f}{unit} "
                else:
                    bucket = f"|{e.threshold_c:.0f}{unit} "
            logger.info(
                f"✅ EDGE: {e.edge_direction} | edge={e.edge:.1%} | "
                f"our_prob={e.estimated_prob:.0%} | market={e.market_prob:.0%} | "
                f"{e.reason} | {e.kind}{bucket} | "
                f"our_prob={int(e.estimated_prob*100)}% | dist={e.distance_c:.1f}°C | "
                f"decay=0.90 | ENSEMBLE"
            )
