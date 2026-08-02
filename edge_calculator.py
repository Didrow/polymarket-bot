"""
edge_calculator.py — Weather Bot v32 (NEAR-RESOLUTION EXECUTION EDGE + LOTTERY EDGE)

v32 adds a parity layer: near_resolution_signal() — when the local diurnal peak
has passed and observed extremes lock (or kill) the bucket, physical reality
trumps forecast modeling. Bypasses Gaussian/sigma/isotonic entirely.
Falls back to v31 LOTTERY EDGE forecast-edge flow (cheap peak YES ≤8¢).

neobrother / coldmath style:
  Forecast 17.0 → buy YES ladder on nearby buckets (16/17/18 or 16.8…17.2)
  Small sizes, peak-first ranking, quality gates against lottery tails.

Lessons hard-coded:
  - Distance 4°C + cheap 1¢ + our_prob 6% = 0% WR (v23–v25)
  - Sort by edge alone → fills slots with phantom tails
  - Sort by distance first → real ladder around forecast
"""

import math
import re
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Dict
from dataclasses import dataclass

import config
from data_fetcher import WeatherForecast, get_best_forecast, fetch_running_temperatures, get_city_local_hour, RunningTemps
from market_scanner import PolyMarket, get_target_date

logger = logging.getLogger(__name__)


# ── PROBABILITY RECALIBRATION (v29) ──────────────────────────
# Loaded once at import time. Mapping is a sorted list of [raw_prob, calibrated_prob]
# pairs produced by calibrate_model.py via PAV isotonic regression. Remap is
# applied inside _prob_exact_gauss / _prob_trend_gauss BEFORE any discount/cap.
# Hard floors (ISOTONIC_MIN_PROB) are enforced after remap so model overconfidence
# above the 0% empirical WR in the CSV is no longer able to manufacture edge.
_RECALIB_POINTS: Optional[List[Tuple[float, float]]] = None
_RECALIB_SCHEMA: int = 1  # 1 = our_prob-based (legacy), 2 = edge_at_entry-based (v29.1)


def _load_recalibration_map() -> Optional[List[Tuple[float, float]]]:
    """Load isotonic-recalibration mapping from JSON. Returns sorted points or None."""
    global _RECALIB_SCHEMA
    if not getattr(config, 'ISOTONIC_RECALIBRATE', False):
        return None
    path = getattr(config, 'ISOTONIC_MAP_FILE', 'data/prob_recalibration.json')
    try:
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        pts = data.get('points')
        if not pts or not isinstance(pts, list):
            return None
        clean = [(float(a), float(b)) for a, b in pts if 0.0 <= float(a) <= 1.0 and 0.0 <= float(b) <= 1.0]
        clean.sort(key=lambda p: p[0])
        if len(clean) < 2:
            return None
        _RECALIB_SCHEMA = int(data.get('schema', 1))
        # v29.1: check 'key' field for edge-based maps (schema=2)
        if data.get('key') == 'edge_at_entry':
            _RECALIB_SCHEMA = 2
        logger.info(f"📐 Recalibration map loaded (schema={_RECALIB_SCHEMA}): {len(clean)} points "
                    f"(raw {clean[0][0]:.3f}→{clean[0][1]:.3f} … {clean[-1][0]:.3f}→{clean[-1][1]:.3f})")
        return clean
    except Exception as e:
        logger.warning(f"Recalibration map load failed: {e}")
        return None


def _apply_recalibration_prob(raw_prob: float) -> float:
    """Schema 1 (legacy): remap our_prob → calibrated prob via piecewise-linear."""
    pts = _RECALIB_POINTS
    if not pts or raw_prob <= pts[0][0]:
        return raw_prob
    if raw_prob >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= raw_prob <= x1:
            if x1 == x0:
                return y0
            t = (raw_prob - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return raw_prob


def _apply_recalibration_edge(raw_edge: float) -> float:
    """Schema 2 (v29.1): remap raw edge_at_entry → actual win rate.
    The returned value estimates the TRUE probability that this trade will win,
    given the historical relationship between predicted edge and realized outcome.
    Bot then compares this calibrated win-rate vs market price to recompute effective edge."""
    pts = _RECALIB_POINTS
    if not pts or len(pts) < 2:
        return raw_edge
    # Clamp to extremes
    if raw_edge <= pts[0][0]:
        return pts[0][1]
    if raw_edge >= pts[-1][0]:
        return pts[-1][1]
    # Piecewise-linear interpolate
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= raw_edge <= x1:
            if x1 == x0:
                return y0
            t = (raw_edge - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return raw_edge


def _apply_recalibration(raw_prob: float, raw_edge: Optional[float] = None) -> Tuple[float, float]:
    """Apply recalibration. Returns (calibrated_prob_or_raw, calibrated_edge_or_raw).
    - Schema 1 (our_prob-based): only prob is remapped, edge is recomputed as our_cal - mkt.
    - Schema 2 (edge-based): edge is remapped to actual_win_rate (serves as our_cal), prob unchanged.

    Falls back to raw_prob if mapping missing."""
    global _RECALIB_POINTS
    if _RECALIB_POINTS is None:
        _RECALIB_POINTS = _load_recalibration_map()
    pts = _RECALIB_POINTS
    if not pts or len(pts) < 2:
        return raw_prob, raw_edge
    if _RECALIB_SCHEMA == 2 and raw_edge is not None:
        cal_win_rate = _apply_recalibration_edge(raw_edge)
        return raw_prob, cal_win_rate
    # Schema 1 (legacy)
    cal_prob = _apply_recalibration_prob(raw_prob)
    return cal_prob, raw_edge


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
    ladder_rank: float = 0.0  # higher = better for coldmath fill order

    @property
    def edge_pct(self) -> str:
        return f"{self.edge:.1%}"

    @property
    def summary(self) -> str:
        return (
            f"{self.edge_direction} | edge={self.edge:.1%} | "
            f"our_prob={self.estimated_prob:.2f} | market={self.market_prob:.2f} | "
            f"dist={self.distance_c:.1f}°C | {self.reason}"
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
    if "between" in q or re.search(r'\d+\s*[-–]\s*\d+', q):
        if any(w in q for w in ["or higher", "or above", "or below", "or lower"]):
            pass
        else:
            return "range"
    if any(w in q for w in ["or higher", "or above", "above", "exceed"]):
        return "above"
    if any(w in q for w in ["or below", "or lower", "below", "under", "or fewer"]):
        return "below"
    return "categorical"


def _parse_threshold(question: str) -> Tuple[str, Optional[float], str]:
    """Returns (kind, threshold_value, unit). Never use for range — call _parse_range first."""
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

    m = re.search(r'\bbe\s+(-?\d+\.?\d*)\s+(?:on\b|or\b)', q_lower)
    if m:
        return "categorical", float(m.group(1)), unit

    m = re.search(r'\b(\d+\.?\d*)\s*(?:°|degree)', q_lower)
    if m:
        return "categorical", float(m.group(1)), unit

    return kind, None, unit


def _parse_range(question: str, unit: str) -> Optional[Tuple[float, float]]:
    """Returns (low_c, high_c) or None. Always Celsius."""
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
    if hours <= 6.0:
        return getattr(config, 'CAP_EXACT_SHORT', 0.55)
    elif hours <= 18.0:
        return getattr(config, 'CAP_EXACT_MID', 0.45)
    return getattr(config, 'CAP_EXACT_LONG', 0.35)


def _get_cap_trend(hours: float) -> float:
    if hours <= 6.0:
        return getattr(config, 'CAP_SHORT', 0.82)
    elif hours <= 18.0:
        return getattr(config, 'CAP_MID', 0.72)
    return getattr(config, 'CAP_LONG', 0.62)


def _prob_exact_gauss(forecast: WeatherForecast, low_c: float, high_c: float,
                     is_low: bool, hours: float) -> float:
    """P(temp in [low_c, high_c]) via Gaussian + mild empirical blend."""
    sigma = forecast._get_sigma(hours)
    mean = forecast.temp_low_c if is_low else forecast.temp_high_c

    cdf_high = 0.5 * (1 + math.erf((high_c - mean) / (sigma * math.sqrt(2))))
    cdf_low = 0.5 * (1 + math.erf((low_c - mean) / (sigma * math.sqrt(2))))
    raw = max(0.0, cdf_high - cdf_low)

    emp_weight = getattr(config, 'EMPIRICAL_WEIGHT', 0.25)
    if emp_weight > 0:
        members = forecast._get_adjusted_members(is_low)
        if members and len(members) >= 5:
            count_in = sum(1 for m in members if low_c <= m < high_c)
            prob_emp = count_in / len(members)
            raw = (1 - emp_weight) * raw + emp_weight * prob_emp

    # v29: isotonic-recalibrate the Gaussian output BEFORE discount & cap.
    # Why before discount: discount is a multiplicative term that was tuned on
    # raw Gaussian. Recalibration learns the empirical wins/total mapping of
    # the *post-discount* probability, so the mapping already folds in any
    # structural bias the discount encoded. To avoid double-discounting after
    # calibration data is available, we skip the historical discount when the
    # recalibration map is active.
    if _RECALIB_POINTS is not None or _load_recalibration_map() is not None:
        raw, _edge_cal = _apply_recalibration(raw, raw_edge=None)
    else:
        discount = getattr(config, 'CATEGORICAL_DISCOUNT', 0.92)
        raw *= discount

    cap = _get_cap_exact(hours)
    return max(0.01, min(cap, round(raw, 4)))


def _prob_trend_gauss(forecast: WeatherForecast, threshold_c: float, kind: str,
                     is_low: bool, hours: float) -> float:
    sigma = forecast._get_sigma(hours)
    mean = forecast.temp_low_c if is_low else forecast.temp_high_c

    if kind == "above":
        raw = 0.5 * (1 + math.erf((mean - threshold_c) / (sigma * math.sqrt(2))))
    else:
        raw = 0.5 * (1 + math.erf((threshold_c - mean) / (sigma * math.sqrt(2))))

    if _RECALIB_POINTS is not None or _load_recalibration_map() is not None:
        raw, _edge_cal = _apply_recalibration(raw, raw_edge=None)
    else:
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
        base = 0.85
    elif "NOAA" in sources:
        base = 0.80
    elif "OBSERVED" in sources:
        base = 0.90
    else:
        base = 0.70
    n = len(sources)
    if n >= 3:
        base = min(0.92, base + 0.05)
    return base


def _min_prob_for_distance(distance_c: float) -> float:
    # v27 ladder: peak 0.5°C, near 1.0°C, far 1.5°C (coldmath tight grid)
    peak = getattr(config, 'PEAK_DIST_C', 0.50)
    near = getattr(config, 'NEAR_WING_DIST_C', 1.00)
    if distance_c <= peak:
        return getattr(config, 'MIN_PROB_PEAK', 0.08)
    if distance_c <= near:
        return getattr(config, 'MIN_PROB_NEAR', 0.10)
    return getattr(config, 'MIN_PROB_FAR', 0.12)


def _ladder_size_mult(distance_c: float) -> float:
    # v27: PEAK earns 3× wing (logbest coldmath weighting)
    peak = getattr(config, 'PEAK_DIST_C', 0.50)
    near = getattr(config, 'NEAR_WING_DIST_C', 1.00)
    if distance_c <= peak:
        return getattr(config, 'PEAK_SIZE_MULT', 3.0)
    if distance_c <= near:
        return getattr(config, 'NEAR_WING_SIZE_MULT', 1.0)
    return getattr(config, 'FAR_WING_SIZE_MULT', 0.50)


def _coldmath_rank(edge: float, our_prob: float, market_prob: float,
                   distance_c: float, kind: str, direction: str) -> float:
    """Higher = fill first. Peak near forecast beats phantom far-tail edge."""
    dist_score = max(0.0, 1.5 - distance_c) / 1.5  # 1.0 at 0°, 0 at 1.5°
    ratio = our_prob / max(market_prob, 0.01)
    kind_bonus = 1.25 if kind in ("categorical", "range") else 0.75
    dir_bonus = 1.0 if direction == "BUY_YES" else 0.45
    # Prefer closer rungs heavily — this is the coldmath ladder secret
    return (
        dist_score * 4.0
        + min(ratio, 6.0) * 0.35
        + edge * 2.5
        + our_prob * 1.5
    ) * kind_bonus * dir_bonus


def _suggest_size(our_prob: float, market_prob: float, edge: float,
                  distance_c: float, confidence: float) -> float:
    """Quarter-Kelly style suggestion stored on EdgeResult (trader re-checks)."""
    if not getattr(config, 'USE_KELLY', True):
        base = config.MIN_POSITION_USD
    else:
        # v27: KELLY_PROB_CAP 0.85 — let high-conf peak buckets size up (was 0.55)
        p = min(our_prob, getattr(config, 'KELLY_PROB_CAP', 0.85))
        q = 1.0 - p
        price = max(market_prob, 0.01)
        b = (1.0 - price) / price
        kelly_raw = max(0.0, (p * b - q) / b) if b > 0 else 0.0
        scale = getattr(config, 'KELLY_SCALE', 0.25)
        base = config.INITIAL_CAPITAL * kelly_raw * scale * confidence

    mult = _ladder_size_mult(distance_c)
    size = base * mult
    # v27: peak can reach KELLY_MAX_POSITION_USD=4.50; wings capped at MAX_POSITION_USD=3.50
    max_usd = getattr(config, 'KELLY_MAX_POSITION_USD', 4.50) if mult >= 1.5 else config.MAX_POSITION_USD
    size = max(config.MIN_POSITION_USD, min(size, max_usd))
    return round(size, 2)


# ── v32: NEAR-RESOLUTION EXECUTION EDGE ─────────────────────
# When hours_to_resolution is small AND the city's empirical local peak-hour
# has already passed (with safety buffer), the observed max_so_far IS the
# daily high — forecasts become irrelevant. We bypass Gaussian / sigma /
# isotonic and trade on physical reality + market-maker latency.
#
# Returns Optional[Tuple[direction, confidence, reason_key, peak_local_hour]]
# direction ∈ {"BUY_YES", "BUY_NO"}
# reason_key ∈ {"bucket_locked_yes", "bucket_impossible_no"}
# Caller is responsible for market price gates + size cap.

# v32c: diagnostic counters — WHY near_resolution_signal() returns None.
# Reset once per scan_all_edges() call, logged in the "Edge scan:" summary line.
# Added because 100+ consecutive cycles showed 0 near-res trades with a
# suspiciously constant "none" count, and there was no visibility into which
# of the ~9 rejection branches was responsible (rate-limited running-temps
# fetch on Render free tier vs peak-gate vs no bucket match, etc).
_NR_STATS: Dict[str, int] = {}


def _nr_note(reason: str) -> None:
    _NR_STATS[reason] = _NR_STATS.get(reason, 0) + 1


def _near_resolution_pre_signal(market: "PolyMarket",
                                hours: float,
                                pre_max_h: float,
                                low_c: float,
                                high_c: float,
                                is_low: bool,
                                kind: str = "range",
                                threshold_c: float = 0.0) -> Optional[Tuple[str, float, str, float]]:
    """
    v36: PRE-RESOLUTION branch for markets with hours_to_resolution in
    (NEAR_RESOLUTION_MAX_HOURS, NEAR_RESOLUTION_PRE_MAX_HOURS].

    Polymarket resolves ALL daily-temperature markets at 12:00 UTC regardless
    of city (verified via gamma-api.polymarket.com endDate on London, Tokyo,
    Seoul, Buenos Aires — all 2026-08-03T12:00:00Z). This is FIXED, not per-
    city local noon. As a result, most markets at any scan time have:
      • hours_to_resolution = 18-30h (resolved tomorrow at 12 UTC)
      • local meteorological peak still in the future

    These markets cannot satisfy the peak_passed gate inside near_resolution_signal,
    but they ARE safe for IMPOSSIBLE_NO when the gap is large enough that no
    conceivable diurnal climb can close it before 12 UTC. The required gap is
    NEAR_RESOLUTION_PRE_MIN_GAP_C (2.5°C — vs 1.0°C post-peak), because we have
    no peak_passed safety net; the gap itself must guarantee impossibility.

    SAFETY INVARIANTS:
      1. ONLY IMPOSSIBLE_NO is emitted (never BUY_YES). Locked-YES requires
         peak_passed to confirm max_so_far is monotonically near-final.
      2. Bucket-impossible (heat): max_so_far < low_c - PRE_MIN_GAP_C
      3. Bucket-impossible (cold): min_so_far > high_c + PRE_MIN_GAP_C
      4. Trend-impossible (above): max_so_far < threshold_c - PRE_MIN_GAP_C
      5. Trend-impossible (below): min_so_far > threshold_c + PRE_MIN_GAP_C

    Why this is safe: max_so_far in a day has a hard ceiling — the day's
    climatological max. If we observe max_so_far=23.0°C at, say, 10:00 local
    time and the bucket low edge is 26.0°C, we'd need a 3°C climb in the
    remaining hours. Even on the hottest day of the year in temperate
    latitudes, the climb rate past midday is <0.5°C/hour and slows to ~0
    by evening. A 2.5°C+ gap is therefore physically infeasible to close
    regardless of when in the day we observe it.
    """
    city = market.detected_city
    if not city:
        _nr_note("pre_no_city")
        return None

    rt = fetch_running_temperatures(city)
    if rt is None or len(rt.hourly_temps) < 4:
        _nr_note("pre_running_temps_unavailable")
        return None

    pre_gap = getattr(config, 'NEAR_RESOLUTION_PRE_MIN_GAP_C', 2.5)
    conf = getattr(config, 'NEAR_RES_CONFIDENCE_NO', 0.85)
    # Slightly discounted confidence for pre-resolution path — we lack the
    # peak_passed confirmation, so the model gets a 5pt haircut.
    pre_conf = max(0.78, conf - 0.05)

    cold_market = is_low or (kind == "below")

    if kind == "above":
        if rt.max_so_far < threshold_c - pre_gap:
            return ("BUY_NO", pre_conf, "pre_trend_impossible_above_no", 0.0)
        _nr_note("pre_trend_above_gap_too_small")
        return None

    if kind == "below":
        if rt.min_so_far > threshold_c + pre_gap:
            return ("BUY_NO", pre_conf, "pre_trend_impossible_below_no", 0.0)
        _nr_note("pre_trend_below_gap_too_small")
        return None

    # categorical / range
    if cold_market:
        # Cold side — bucket impossible if min_so_far is ABOVE bucket by gap
        # (warmer than bucket high edge → can't get cold enough to hit bucket).
        if rt.min_so_far > high_c + pre_gap:
            return ("BUY_NO", pre_conf, "pre_bucket_impossible_no_cold", 0.0)
        _nr_note("pre_cold_bucket_gap_too_small")
        return None

    # Heat side — bucket impossible if max_so_far is BELOW bucket by gap
    if rt.max_so_far < low_c - pre_gap:
        return ("BUY_NO", pre_conf, "pre_bucket_impossible_no", 0.0)
    _nr_note("pre_heat_bucket_gap_too_small")
    return None


def near_resolution_signal(market: "PolyMarket",
                           low_c: float,
                           high_c: float,
                           is_low: bool,
                           kind: str = "range",
                           threshold_c: float = 0.0) -> Optional[Tuple[str, float, str, float]]:
    """
    v32: Decide if the market is in a near-resolution execution-edge state.
    v33: extended to trend markets (`kind in ("above","below")`) — threshold pass.

    A market qualifies when ALL of:
      1. NEAR_RESOLUTION_ENABLED is True
      2. NEAR_RESOLUTION_MIN_HOURS ≤ market.hours_to_resolution ≤ NEAR_RESOLUTION_MAX_HOURS
      3. city has PEAK_HOUR_BY_CITY entry
      4. current local hour > peak_hour + NEAR_RESOLUTION_PEAK_BUFFER_HOURS
         (peak already passed with safety margin)
      5. fetch_running_temperatures() succeeds

    Then by kind:
      categorical/range — bucket logic (low_c..high_c):
         BUCKET_LOCKED_YES:        extreme in bucket AND declining/passing
         BUCKET_IMPOSSIBLE_NO:     extreme below bucket by ≥gap (or above for cold)
         BUCKET_EXCEEDED_NO:       (heat) extreme already past high edge by ≥gap
      above (heat threshold):     threshold_c is the line, max_so_far extreme
         TREND_LOCKED_ABOVE_YES:  max_so_far ≥ threshold_c (the "above" already true)
         TREND_IMPOSSIBLE_ABOVE_NO: max_so_far < threshold_c - gap after peak
      below (cold threshold):     threshold_c is the line, min_so_far extreme
         TREND_LOCKED_BELOW_YES:  min_so_far ≤ threshold_c (the "below" already true)
         TREND_IMPOSSIBLE_BELOW_NO: min_so_far > threshold_c + gap after trough

    For 'low' temperature markets (lowest temp/win), peak-hour logic is INVERTED:
      - Coldest temp happens around sunrise. The "peak" of cold is a trough.

    NB: We do NOT touch the forecast layer or sigma/isotonic — these signals
    are KEPT SEPARATE per the v32 design (parity layer, not replacement).
    """
    if not getattr(config, 'NEAR_RESOLUTION_ENABLED', False):
        _nr_note("disabled")
        return None

    hours = market.hours_to_resolution
    max_h = getattr(config, 'NEAR_RESOLUTION_MAX_HOURS', 6.0)
    pre_max_h = getattr(config, 'NEAR_RESOLUTION_PRE_MAX_HOURS', 24.0)
    min_h = getattr(config, 'NEAR_RESOLUTION_MIN_HOURS', 0.5)
    if hours < min_h:
        _nr_note("hours_too_close")
        return None
    # v36: PRE-RESOLUTION window (12-24h to 12:00 UTC resolution). These markets
    # do NOT qualify for peak-passed timing logic but CAN fire IMPOSSIBLE_NO
    # if extreme_so_far falls far enough below the bucket/threshold that no
    # conceivable late-day climb can close the gap before 12 UTC. We branch
    # early and never invoke the peak_passed gate below.
    if hours > max_h and hours <= pre_max_h:
        # Pre-resolution path: skip peak_passed entirely. Dispatch to the
        # IMPOSSIBLE_NO logic for the relevant kind with tightened gap.
        return _near_resolution_pre_signal(market, hours, pre_max_h, low_c, high_c,
                                           is_low, kind, threshold_c)
    if hours > pre_max_h:
        _nr_note("hours_out_of_range")
        return None

    city = market.detected_city
    if not city:
        _nr_note("no_city")
        return None

    peak_hours = getattr(config, 'PEAK_HOUR_BY_CITY', {})
    if not peak_hours or city not in peak_hours:
        _nr_note("no_peak_hour_entry")
        return None
    peak_hour = float(peak_hours[city])

    # v33: 'below' trend is also a cold extreme — treat it as a cold-side market
    # for the timing check (cold trough ~06:00 local, not the heat peak).
    cold_market = is_low or (kind == "below")

    if cold_market:
        cold_trough_hour = 6.0
        empirical_extreme_hour = cold_trough_hour
    else:
        empirical_extreme_hour = peak_hour

    local_hour = get_city_local_hour(city)
    if local_hour is None:
        _nr_note("no_local_hour")
        return None

    buffer_h = getattr(config, 'NEAR_RESOLUTION_PEAK_BUFFER_HOURS', 1.5)
    hours_since_extreme = (local_hour - empirical_extreme_hour) % 24.0
    peak_passed = hours_since_extreme >= buffer_h
    peak_not_too_old = hours_since_extreme <= getattr(config, 'NEAR_RESOLUTION_PEAK_MAX_AGE_HOURS', 12.0)
    # v33d: allow impossible-NO even before peak if max_so_far physically can't
    # reach the bucket in the remaining hours (peak max swing << gap).
    # Tight gate: requires hours < 4h to resolution AND max_so_far check below.
    # v34: extended to trend kinds ("above","below"). Same physical argument —
    # if max_so_far is ≥gap below the threshold AND only <4h left, even a
    # strong peak swing cannot close the gap (climatology daily swing is
    # bounded). Pre-peak YES_locked for trend still requires declining=True
    # (see below) — only the impossible-NO branch is safe without peak_passed.
    allow_pre_peak_impossible = (
        not peak_passed
        and peak_not_too_old
        and hours <= 4.0
        and kind in ("categorical", "range", "above", "below")
    )
    if not peak_passed and not allow_pre_peak_impossible:
        _nr_note("peak_not_passed_yet")
        return None
    if not peak_not_too_old:
        _nr_note("peak_too_long_ago")
        return None

    # Fetch running observed temperatures (cached 30 min).
    rt = fetch_running_temperatures(city)
    if rt is None or len(rt.hourly_temps) < 4:
        _nr_note("running_temps_unavailable")
        return None

    # ──────────────────────────────────────────────────────────
    # v33: TREND (above/below) — threshold pass / fail detection
    # ──────────────────────────────────────────────────────────
    if kind == "above":
        # Heat threshold. max_so_far is monotonically non-decreasing through
        # the day → once it crossed threshold_c the "above" outcome is sealed.
        extreme_so_far = rt.max_so_far
        gap = getattr(config, 'NEAR_RES_TREND_IMPOSSIBLE_GAP_C', 1.0)
        # v33d: impossible-NO safe without declining gate (gap > 1°C hard to climb)
        if extreme_so_far < threshold_c - gap:
            conf = getattr(config, 'NEAR_RES_CONFIDENCE_NO', 0.85)
            return ("BUY_NO", conf, "trend_impossible_above_no", empirical_extreme_hour)
        # For YES_locked we DO require declining (peak-passed safety).
        if not rt.latest_declining:
            _nr_note("trend_above_not_declining")
            return None
        if extreme_so_far >= threshold_c:
            conf = getattr(config, 'NEAR_RES_CONFIDENCE_YES', 0.92)
            return ("BUY_YES", conf, "trend_locked_above_yes", empirical_extreme_hour)
        _nr_note("trend_above_ambiguous")
        return None

    if kind == "below":
        # Cold threshold. min_so_far is monotonically non-increasing through the
        # day's early hours → once below threshold_c the outcome is sealed.
        # Use cold_passed gate (latest reading no longer dropping).
        extreme_so_far = rt.min_so_far
        latest = rt.hourly_temps[-1]
        cold_passed = (latest - extreme_so_far) >= -0.2
        if not cold_passed:
            _nr_note("trend_below_still_dropping")
            return None
        gap = getattr(config, 'NEAR_RES_TREND_IMPOSSIBLE_GAP_C', 1.0)
        if extreme_so_far <= threshold_c:
            conf = getattr(config, 'NEAR_RES_CONFIDENCE_YES', 0.92)
            return ("BUY_YES", conf, "trend_locked_below_yes", empirical_extreme_hour)
        if extreme_so_far > threshold_c + gap:
            conf = getattr(config, 'NEAR_RES_CONFIDENCE_NO', 0.85)
            return ("BUY_NO", conf, "trend_impossible_below_no", empirical_extreme_hour)
        _nr_note("trend_below_ambiguous")
        return None

    # ──────────────────────────────────────────────────────────
    # Bucket markets (categorical / range) — original v32 logic
    # ──────────────────────────────────────────────────────────
    if is_low:
        # COLD-trough logic — bucket locked if min_so_far IS in bucket, AND temps
        # have been flat/rising (cold already bottomed). Bucket impossible if
        # min_so_far is ABOVE bucket by ≥gap_C (warmer than bucket → bucket already busted going down).
        extreme_so_far = rt.min_so_far
        # Cold trough passed = latest reading no longer dropping (tolerate noise).
        latest = rt.hourly_temps[-1]
        cold_passed = (latest - extreme_so_far) >= -0.2
        if not cold_passed:
            _nr_note("cold_still_dropping")
            return None

        gap = getattr(config, 'NEAR_RESOLUTION_IMPOSSIBLE_GAP_C', 1.0)
        # Bucket locked (cold already hit bucket): min_so_far in [low, high]
        if low_c <= extreme_so_far <= high_c:
            conf = getattr(config, 'NEAR_RES_CONFIDENCE_YES', 0.92)
            return ("BUY_YES", conf, "bucket_locked_yes_cold", empirical_extreme_hour)
        # Bucket impossible (cold already warmer than bucket by gap → bucket can't be hit)
        if extreme_so_far > high_c + gap:
            conf = getattr(config, 'NEAR_RES_CONFIDENCE_NO', 0.85)
            return ("BUY_NO", conf, "bucket_impossible_no_cold", empirical_extreme_hour)
        _nr_note("cold_bucket_ambiguous")
        return None

    # HEAT-side logic — bucket locked if max_so_far in bucket AND declining.
    extreme_so_far = rt.max_so_far
    gap = getattr(config, 'NEAR_RESOLUTION_IMPOSSIBLE_GAP_C', 1.0)

    # v33d: Bucket IMPOSSIBLE_NO — max_so_far already ≥gap below bucket.
    # This is physically safe BEFORE the peak too: even at peak + 1.5h buffer,
    # a >1.0°C climb from max_so_far to low_c in <4h is climatology-impossible.
    # So skip the declining gate for this branch.
    if extreme_so_far < low_c - gap:
        conf = getattr(config, 'NEAR_RES_CONFIDENCE_NO', 0.85)
        return ("BUY_NO", conf, "bucket_impossible_no", empirical_extreme_hour)

    # For BUY_YES_locked and BUY_NO_exceeded we DO require declining=True,
    # because peak-passed is the safety condition for acting on current extreme.
    if not rt.latest_declining:
        _nr_note("heat_not_declining_yet")
        return None

    # Bucket locked: max_so_far ALREADY in bucket → daily high almost certainly
    # in bucket (we'd need a new higher reading after the peak hour, which the
    # safety buffer + decline-window check protects against).
    if low_c <= extreme_so_far <= high_c:
        conf = getattr(config, 'NEAR_RES_CONFIDENCE_YES', 0.92)
        return ("BUY_YES", conf, "bucket_locked_yes", empirical_extreme_hour)
    # v32 FIX: bucket ALREADY EXCEEDED from above — max_so_far is monotonically
    # non-decreasing through the day, so once it's past high_c the final daily
    # high can only be >= extreme_so_far > high_c. This is the single safest
    # signal (doesn't even depend on the peak-passed timing check), and was
    # missing entirely before this patch.
    if extreme_so_far > high_c + gap:
        conf = getattr(config, 'NEAR_RES_CONFIDENCE_NO', 0.85)
        return ("BUY_NO", conf, "bucket_impossible_no_exceeded", empirical_extreme_hour)
    _nr_note("heat_bucket_ambiguous")
    return None


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
    # Prefer scanner kind; if question is range, force range
    if _parse_range(market.question, _unit_from_question(market.question)) is not None:
        if kind not in ("above", "below"):
            kind = "range"

    allowed = getattr(config, 'KINDS_ONLY', ['categorical', 'range', 'above', 'below'])
    if kind not in allowed:
        return None

    t_date = get_target_date(market.question, market.end_date, city)
    forecast = get_best_forecast(city, hours_to_resolution=market.hours_to_resolution, target_date=t_date)
    if not forecast:
        return None

    is_low = 'lowest' in market.question.lower()
    unit = _unit_from_question(market.question)
    low_c = high_c = 0.0

    if kind in ("categorical", "range"):
        if kind == "range":
            range_c = _parse_range(market.question, unit)
            if range_c is None:
                rl = market.range_low
                rh = market.range_high
                if rl is None or rh is None:
                    return None
                if unit == 'F':
                    range_c = (_f_to_c(rl), _f_to_c(rh))
                else:
                    range_c = (rl, rh)
            low_c, high_c = range_c
            # v29: minimum bucket width — anything narrower than forecast
            # uncertainty is essentially a coin flip on station noise.
            min_width = getattr(config, 'RANGE_MIN_BUCKET_WIDTH_C', 3.0)
            if (high_c - low_c) < min_width:
                return None
            threshold_c = (low_c + high_c) / 2.0
        else:
            parsed_kind, threshold_value, punit = _parse_threshold(market.question)
            if threshold_value is None:
                threshold_value = market.threshold_value
            if threshold_value is None:
                return None
            if punit == 'F' or (punit == '' and unit == 'F'):
                threshold_c = _f_to_c(threshold_value)
            else:
                threshold_c = threshold_value
            if unit == 'F':
                half_c = 0.278  # 0.5°F
            else:
                half_c = 0.5
            low_c = threshold_c - half_c
            high_c = threshold_c + half_c
    else:
        parsed_kind, threshold_value, punit = _parse_threshold(market.question)
        if threshold_value is None:
            threshold_value = market.threshold_value
        if threshold_value is None:
            return None
        if punit == 'F' or (punit == '' and unit == 'F'):
            threshold_c = _f_to_c(threshold_value)
        else:
            threshold_c = threshold_value

    # ═══════════════════════════════════════════════════════════════
    # v32: NEAR-RESOLUTION SIGNAL — bypass Gaussian/sigma/isotonic
    # ═══════════════════════════════════════════════════════════════
    # When the local diurnal peak has passed AND observed extremes already
    # lock (or kill) the bucket, physical reality trumps forecast models.
    # This check runs BEFORE Gaussian/sigma/isotonic — near-resolution
    # signals short-circuit the entire forecast-edge path.
    # v33: now also applies to trend (above/below) markets — they were given
    # 0% WR at 24-60h entry but never tested near-resolution; with threshold
    # markets being higher-volume, this is the originally-intended use case.
    if kind in ("categorical", "range", "above", "below"):
        # threshold_c for trend; for bucket use low_c/high_c (sentinel).
        nr_threshold = threshold_c if kind in ("above", "below") else 0.0
        nr = near_resolution_signal(market, low_c, high_c, is_low, kind=kind, threshold_c=nr_threshold)
        if nr is not None:
            nrs_dir, nrs_conf, nrs_reason_key, nrs_peak_hr = nr
            mkt_prob = market.best_ask_yes
            if mkt_prob is None or mkt_prob < 0.01:
                mkt_prob = getattr(market, "midpoint_yes", 0.0)
            if mkt_prob <= 0.001 or mkt_prob >= 0.999:
                return None

            # v33: trend markets use their own price bands (slightly wider NO).
            if kind in ("above", "below"):
                if nrs_dir == "BUY_YES":
                    min_ask = getattr(config, 'NEAR_RES_TREND_YES_MIN_ASK', 0.50)
                    max_ask = getattr(config, 'NEAR_RES_TREND_YES_MAX_ASK', 0.95)
                else:
                    min_ask = getattr(config, 'NEAR_RES_TREND_NO_MIN_ASK', 0.05)
                    max_ask = getattr(config, 'NEAR_RES_TREND_NO_MAX_ASK', 0.30)
            else:
                if nrs_dir == "BUY_YES":
                    min_ask = getattr(config, 'NEAR_RES_YES_MIN_ASK', 0.50)
                    max_ask = getattr(config, 'NEAR_RES_YES_MAX_ASK', 0.95)
                else:
                    min_ask = getattr(config, 'NEAR_RES_NO_MIN_ASK', 0.03)
                    max_ask = getattr(config, 'NEAR_RES_NO_MAX_ASK', 0.20)
            if not (min_ask <= mkt_prob <= max_ask):
                return None

            eff_edge_nr = (nrs_conf - mkt_prob) if nrs_dir == "BUY_YES" else (mkt_prob - nrs_conf)
            eff_edge_nr = min(max(eff_edge_nr, 0.0), getattr(config, 'MAX_EDGE_CAP', 0.45))
            if eff_edge_nr <= 0.0:
                return None

            max_sz = getattr(config, 'NEAR_RESOLUTION_MAX_SIZE_USD', 2.00)
            min_sz = getattr(config, 'NEAR_RESOLUTION_MIN_SIZE_USD', 0.75)
            size_nr = round(max(min_sz, min(max_sz, eff_edge_nr * 3.0)), 2)

            logger.info(
                f"✅ NEAR-RES {nrs_reason_key}: {market.question[:55]} | "
                f"dir={nrs_dir} conf={nrs_conf:.0%} mkt={mkt_prob:.0%} "
                f"edge={eff_edge_nr:.1%} peak_hr={nrs_peak_hr:.1f}"
            )
            return EdgeResult(
                market=market, forecast=None,
                estimated_prob=nrs_conf, market_prob=mkt_prob,
                edge=eff_edge_nr, edge_direction=nrs_dir,
                confidence=nrs_conf,
                reason=f"NEAR-RES {nrs_reason_key} @ {mkt_prob:.3f}",
                is_tradeable=True, size_usd=size_nr,
                threshold_c=low_c if is_low else high_c,
                distance_c=0.0, kind=kind,
                ladder_rank=999.0,
            )

    # Compute Gaussian prob now that we know near-res didn't fire.
    if kind in ("categorical", "range"):
        our_prob = _prob_exact_gauss(forecast, low_c, high_c, is_low, market.hours_to_resolution)
    else:
        our_prob = _prob_trend_gauss(forecast, threshold_c, kind, is_low, market.hours_to_resolution)

    # v32b: legacy forecast/lottery path kill switch. near_resolution_signal()
    # above already ran and returned/short-circuited if it found something;
    # if we reach here, either it found nothing or kind isn't categorical/range.
    # When disabled, don't fall through into the disproven Gaussian/sigma path.
    if not getattr(config, 'FORECAST_EDGE_ENABLED', True):
        return None

    # v29: hard floor on recalibrated our_prob — blocks the entire 0-10% bucket
    # (calibration CSV: avg_pred 6.6% → actual 2.2%) from ever entering positions.
    iso_floor = getattr(config, 'ISOTONIC_MIN_PROB', 0.0)
    if iso_floor > 0.0 and our_prob < iso_floor:
        return None

    fc_temp = forecast.temp_low_c if is_low else forecast.temp_high_c
    distance_c = abs(fc_temp - threshold_c)

    # ── DISTANCE GATE (ladder band) ──
    max_dist_c = getattr(config, 'SNIPER_GRID_DISTANCE_C', 1.5)
    if kind in ("categorical", "range"):
        if distance_c > max_dist_c:
            return None
    else:
        trend_max_dist = getattr(config, 'TREND_MAX_DIST_C', 2.0)
        if distance_c > trend_max_dist:
            return None
        max_dist_sigma = getattr(config, 'MAX_DISTANCE_SIGMA', 2.5)
        sigma_edge = forecast._get_sigma(market.hours_to_resolution)
        if distance_c > max_dist_sigma * sigma_edge and our_prob > 0.50:
            return None

    # v29: WING_BAN — eliminate any BUY_YES where forecast is not inside the
    # peak bucket (dist > PEAK_DIST_C). Calibration CSV: 14/14 losses included
    # wing-style 0.7-1.2°C off-forecast entries; no wins ever came from a wing.
    # The ban is enforced here (in addition to SNIPER_GRID_DISTANCE_C tightening)
    # so the NO path remains eligible for far-from-forecast mispriced extremes.
    if getattr(config, 'WING_BAN', False):
        peak_only_dist = getattr(config, 'PEAK_DIST_C', 0.50)
        if distance_c > peak_only_dist:
            # Not a peak — only allow further evaluation if it becomes a NO
            # (handled below). Mark with sentinel: skip yes acceptance later.
            pass

    # ── QUALITY GATES (distance-aware, not blanket ban) ──
    max_tail_prob = getattr(config, 'MAX_TAIL_PROB', 0.08)
    if kind in ("categorical", "range") and our_prob < max_tail_prob:
        return None

    max_tail_dist = getattr(config, 'MAX_TAIL_DIST_C', 1.20)
    max_tail_combined = getattr(config, 'MAX_TAIL_COMBINED_PROB', 0.12)
    if distance_c > max_tail_dist and our_prob < max_tail_combined:
        return None

    min_prob_dist = _min_prob_for_distance(distance_c)
    if kind in ("categorical", "range") and our_prob < min_prob_dist:
        return None

    # Market price
    market_prob = market.best_ask_yes
    if market_prob is None or market_prob < 0.01:
        midpoint = getattr(market, "midpoint_yes", 0.0)
        if 0.01 <= midpoint <= 0.99:
            market_prob = midpoint
        else:
            return None

    if market_prob <= 0.001 or market_prob >= 0.999:
        return None

    # Cheap-YES ratio gate (kills 1¢ lottery with our_prob 6–9%)
    cheap_thr = getattr(config, 'CHEAP_ASK_THRESHOLD', 0.10)
    if market_prob <= cheap_thr and kind in ("categorical", "range"):
        ratio = our_prob / max(market_prob, 0.001)
        min_ratio = getattr(config, 'MIN_EDGE_RATIO_CHEAP', 2.2)
        min_cheap = getattr(config, 'MIN_PROB_CHEAP', 0.10)
        if ratio < min_ratio or our_prob < min_cheap:
            return None

    edge_yes = our_prob - market_prob
    edge_no = market_prob - our_prob

    if kind in ("categorical", "range"):
        min_edge_yes = getattr(config, 'SNIPER_GRID_MIN_EDGE', 0.03)
        min_edge_no = getattr(config, 'SNIPER_GRID_MIN_EDGE_NO', 0.18)
    else:
        min_edge_yes = getattr(config, 'MIN_EDGE_YES', 0.15)
        min_edge_no = getattr(config, 'MIN_EDGE_NO', 0.18)

    min_prob = getattr(config, 'MIN_PROB_ENTRY', 0.08)
    max_edge = getattr(config, 'MAX_EDGE_CAP', 0.45)

    ask_max_grid = getattr(config, 'SNIPER_GRID_MAX_ASK', 0.40)
    ask_min_grid = getattr(config, 'SNIPER_GRID_MIN_ASK', 0.01)

    edge_direction = None
    eff_edge = 0.0
    reason = ""

    # v29.1: schema=2 recalibration — remap raw edge_yes via isotonic map.
    # The map was trained on (edge_at_entry, is_win), so the remapped value
    # estimates P(win) directly. We then recompute effective edge as
    # P(win) - market, which reflects E[profit per dollar] honestly.
    if _RECALIB_POINTS is not None and getattr(config, 'ISOTONIC_RECALIBRATE', False):
        if _load_recalibration_map() is not None and _RECALIB_SCHEMA == 2:
            cal_win_p = _apply_recalibration_edge(max(edge_yes, 0.0))
            # Bot uses eff_edge to decide entry — honest expectancy means
            # if cal_win_p < market_prob, eff_edge should be negative → skip.
            edge_yes_cal = cal_win_p - market_prob
            edge_no_cal = market_prob - cal_win_p
            edge_yes = edge_yes_cal
            edge_no = edge_no_cal

    if kind in ("categorical", "range"):
        # v29: WING_BAN — peak-only YES gate. Any distance beyond PEAK_DIST_C
        # turns this market into an automatic NO-only consideration.
        wing_banned = False
        if getattr(config, 'WING_BAN', False):
            peak_only_dist = getattr(config, 'PEAK_DIST_C', 0.50)
            if distance_c > peak_only_dist:
                wing_banned = True

        if not wing_banned and edge_yes >= min_edge_yes and our_prob >= min_prob:
            if not (ask_min_grid <= market_prob <= ask_max_grid):
                return None
            edge_direction = "BUY_YES"
            eff_edge = min(edge_yes, max_edge)
            rung = "PEAK" if distance_c <= getattr(config, 'PEAK_DIST_C', 0.6) else "WING"
            reason = f"LADDER {rung} YES @ {market_prob:.3f}"
        elif (
            getattr(config, 'ENABLE_BUY_NO', True)
            and edge_no >= min_edge_no
            and our_prob <= getattr(config, 'BUY_NO_MAX_OUR_PROB', 0.12)
            and market_prob >= getattr(config, 'BUY_NO_MIN_MARKET', 0.70)
        ):
            edge_direction = "BUY_NO"
            eff_edge = min(edge_no, max_edge)
            reason = f"LADDER NO @ {market_prob:.3f}"
        else:
            return None
    else:
        # Trend above/below — quality only
        if edge_yes >= min_edge_yes and our_prob >= max(min_prob, getattr(config, 'TREND_MIN_PROB_YES', 0.28)):
            if market_prob > getattr(config, 'TREND_MAX_ASK', 0.55):
                return None
            if market_prob < getattr(config, 'TREND_MIN_ASK', 0.05):
                return None
            edge_direction = "BUY_YES"
            eff_edge = min(edge_yes, max_edge)
            reason = f"TREND YES {kind.upper()} @ {market_prob:.3f}"
        elif (
            getattr(config, 'ENABLE_BUY_NO', True)
            and edge_no >= min_edge_no
            and market_prob >= getattr(config, 'TREND_MIN_NO_MARKET', 0.35)
        ):
            edge_direction = "BUY_NO"
            eff_edge = min(edge_no, max_edge)
            reason = f"TREND NO {kind.upper()} @ {market_prob:.3f}"
        else:
            return None

    confidence = _confidence_from_forecast(forecast)
    rank = _coldmath_rank(eff_edge, our_prob, market_prob, distance_c, kind, edge_direction)
    size = _suggest_size(our_prob, market_prob, eff_edge, distance_c, confidence)

    bucket_str = ""
    if kind == "range":
        bucket_str = f" | {low_c:.1f}-{high_c:.1f}°C"
    elif kind == "categorical":
        bucket_str = f" | bucket≈{threshold_c:.1f}°C"

    logger.info(
        f"✅ EDGE: {market.question[:50]} | "
        f"our={our_prob:.0%} | mkt={market_prob:.0%} | edge={eff_edge:.1%} | "
        f"{edge_direction} | dist={distance_c:.1f}°C | fc={fc_temp:.1f}°C | {kind}{bucket_str}"
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
        is_tradeable=True,
        size_usd=size,
        threshold_c=threshold_c,
        distance_c=distance_c,
        kind=kind,
        ladder_rank=rank,
    )


# ── SCANNER ─────────────────────────────────────────────────

def _max_spread_for(market: PolyMarket) -> float:
    ask = market.best_ask_yes
    if ask is None:
        return 0.05
    if ask <= 0.15:
        return max(0.12, ask)
    if ask <= 0.50:
        return 0.18
    return 0.08


def scan_all_edges(markets: List[PolyMarket]) -> List[EdgeResult]:
    results: List[EdgeResult] = []
    skip_vol = skip_city = skip_hours = skip_none = skip_spread = skip_kind = skip_window = 0
    _NR_STATS.clear()

    # v32: Time window gate — near-res trades can also fire outside LOTTERY window
    # (peak-hour based, not UTC-time based), but we keep the lottery window check
    # as a fallback filter — markets mainly have fresh liquidity 00:00-08:00 UTC.
    win_start = getattr(config, 'OPEN_WINDOW_START_UTC', 0)
    win_end = getattr(config, 'OPEN_WINDOW_END_UTC', 24)
    now_utc = datetime.now(timezone.utc)
    in_window = win_start <= now_utc.hour < win_end
    if not in_window:
        logger.info(
            f"⊓ v32 time-window: outside open window ({now_utc.hour:02d}:{now_utc.minute:02d} UTC, "
            f"window={win_start:02d}-{win_end:02d} UTC). Skipping all trades this cycle."
        )

    city_market_count: Dict[str, int] = {}
    for m in markets:
        c = m.detected_city
        if c and c in getattr(config, 'CITY_WHITELIST', []):
            city_market_count[c] = city_market_count.get(c, 0) + 1
    max_prefetch = getattr(config, 'MAX_PREFETCH_CITIES', 20)
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

        # v31: Skip all market processing if outside time window
        if not in_window:
            skip_window += 1
            continue

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
        allowed = getattr(config, 'KINDS_ONLY', ['categorical', 'range', 'above', 'below'])
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

    # COLDMATH: highest ladder_rank first (peak near forecast), NOT raw edge
    results.sort(key=lambda r: r.ladder_rank, reverse=True)

    # Cap rungs per city in the result list (still allow main to re-check)
    max_per_city = getattr(config, 'SNIPER_GRID_MAX_PER_CITY_CYCLE', 5)
    city_seen: Dict[str, int] = {}
    capped: List[EdgeResult] = []
    for r in results:
        c = r.market.detected_city or "?"
        if city_seen.get(c, 0) >= max_per_city:
            continue
        city_seen[c] = city_seen.get(c, 0) + 1
        capped.append(r)
    results = capped

    _print_grid_summary(results)

    logger.info(
        f"Edge scan: {len(results)} tradeable / {len(markets)} markets "
        f"| skip: vol={skip_vol}, hours={skip_hours}, city={skip_city}, "
        f"kind={skip_kind}, spread={skip_spread}, none={skip_none}, window={skip_window}"
    )
    if _NR_STATS:
        nr_summary = ", ".join(f"{k}={v}" for k, v in sorted(_NR_STATS.items(), key=lambda x: -x[1]))
        logger.info(f"  near-res reasons: {nr_summary}")
    return results


def _print_grid_summary(results: List[EdgeResult]) -> None:
    """Log ladder by city — like coldmath/neobrother grid view."""
    if not results:
        return
    by_city: Dict[str, List[EdgeResult]] = {}
    for r in results:
        c = r.market.detected_city or "?"
        by_city.setdefault(c, []).append(r)

    for city, edges in sorted(by_city.items()):
        # sort rungs by distance for display (true ladder view)
        edges_sorted = sorted(edges, key=lambda e: e.distance_c)
        fc = None
        if edges_sorted[0].forecast:
            is_low = 'lowest' in edges_sorted[0].market.question.lower()
            fc = edges_sorted[0].forecast.temp_low_c if is_low else edges_sorted[0].forecast.temp_high_c
        fc_str = f"{fc:.1f}°C" if fc is not None else "?"
        rungs = []
        for e in edges_sorted[:5]:
            unit = _unit_from_question(e.market.question)
            if e.kind == "range" and e.market.range_low is not None:
                label = f"{e.market.range_low:g}-{e.market.range_high:g}{unit}"
            elif e.kind == "categorical":
                if unit == 'F':
                    label = f"{_c_to_f(e.threshold_c):.0f}{unit}"
                else:
                    label = f"{e.threshold_c:.0f}{unit}"
            else:
                label = e.kind
            rungs.append(
                f"{label}@{e.market_prob:.0%}(our{e.estimated_prob:.0%},d{e.distance_c:.1f})"
            )
        logger.info(f"🪜 LADDER {city} fc={fc_str}: {' | '.join(rungs)}")
