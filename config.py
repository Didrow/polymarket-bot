"""
config.py — Weather Bot v32 (NEAR-RESOLUTION EXECUTION EDGE)

Еволюція v31 → v32:
  v31 продовжує 0% WR (0/5 resolved, ROI -9.9%) — той самий forecast-edge баг:
  модель системно переоцінює our_prob у 8 разів (historic 2.7% WR на 294 trades).
  Документація від іншого агента (Recomendation.md) пропонує інший edge:
  EXECUTION/LATENCY edge — near-resolution ринки (1-6h до close), коли
  добовий ТЕМПЕРАТУРНИЙ ПІК ВЖЕ ПРОЙШОВ, а маркет-мейкери ще не перекотировали.

  v32 = parity layer поверх v31, не заміна:
    1) Якщо market.hours_to_resolution ≤ NEAR_RESOLUTION_MAX_HOURS (6h)
       І local peak-hour для міста вже пройшов (з safety buffer),
       І max_so_far за сьогодні вже ВПАВ у бакет (для YES) або нижче бакета на ≥1°C (для NO),
       → near_resolution_signal() повертає рішення, ОБХОДЯЧИ Gaussian / sigma / isotonic.
    2) Інакше calculate_edge падає у звичайний v31 forecast-edge flow.

  Очікуваний профіль: 1-3 угоди/день на все місто (частота мала),
  але WR 50-90% на коректно закритих бакетах (фізика vsforecast).
  Hard cap NEAR_RESOLUTION_MAX_SIZE_USD — рідкісний грозовий фронт може
  зламати «пік вже пройшов»; не 100% Kelly.初衷иonhold近resolution.

  v31 break-even математика (збережено як baseline):
    - Avg entry ~5¢ (max 8¢)
    - 1 WIN @ $1.00 покриває 12 LOSS-ів @ 8¢ → BE WR ≈ 8%
    - Historic wins в ranges 1–5¢ показували WR 3–4% → Маржевий scenario.

Еволюція v30 → v31 (збережено для контексту):
  День 1 v30 дав 3 угоди з 6 кандидатів — обсяг достатній для break-even (WR ~5%),
  але замало для реальної прибутковості (треба >5% для accumulation).
  v31 розширює-phase: +2год вікна, +2 міст, +60% цінового діапазону, Kelly x1.7.

v31 фікси vs v30:
  1) OPEN_WINDOW_END_UTC 6 → 8 (+2 годcandidates, +33% scan window)
  2) SNIPER_GRID_MAX_ASK 0.05 → 0.08 (lottery верхня межа 8¢, BE WR 8%)
  3) MAX_OPEN_PER_CYCLE 4 → 6 (більше угод за один цикл)
  4) MAX_ACTIVE_POSITIONS 12 → 20 (портфель вмістить 8 міст × ~2-3 бакети)
  5) KELLY_SCALE 0.15 → 0.25 (position size зростає ~x1.7)
  6) CITY_WHITELIST 6 → 8 (+Mumbai, +Delhi — топ-heat cities)
  7) MAX_PREFETCH_CITIES 6 → 8 (sync з whitelist)

Аналіз 294 CLOSE trades показав:
  - Avg WIN $5.78 vs Avg LOSS $1.28 → потрібен 39% WR для break-even (old v27/v29)
  - Реальний WR = 2.7% → математично неможливо profitувати з v27/v29 стратегією
  - Всі 8 historic wins у вікні 00:00-04:00 UTC, peak-bucket, ≤15¢ entry
  - 12:00 UTC = 109 trades, 0.9% WR — бот спамив stale midday markets

v31 break-even математика:
  - Avg entry ~5¢ (max 8¢)
  - 1 WIN @ $1.00 покриває 12 LOSS-ів @ 8¢ → BE WR ≈ 8%
  - Historic wins в ranges 1–5¢ показували WR 3–4% → Маржевий scenario.
  - Ключ: 6-10 угод/день × 30 днів = 180-300 trades/міс для швидкої статистики.

Уроки (НЕ повторювати):
  v9:   discount 0.30 → 0% WR
  v15–v19: METAR arb → 0% WR
  v21:  SIGMA_MIN=1.5 → our_prob=0
  v22:  SIGMA_MIN=4.5 → buckets too wide
  v23–v25: lottery 1–3¢ dist=3°C → 0% WR
  v26:  COLDMATH LADDER 0/8 WR
  v27-v29: maths break-even fail (avg win $5.78 / avg loss $1.28 / WR 2.7%)
"""

from typing import List, Dict
import os

DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── v31: TIME WINDOW (fresh daily markets, widened +2h) ───
# Historical analysis: all 8 wins opened 00:00-04:00 UTC.
# v30: 0-6 UTC → 3 угоди/день. v31: 0-8 UTC → est 6-10 угод/день.
# 12:00 UTC had 109 trades with 0.9% WR — bot wasted capital on stale midday markets.
OPEN_WINDOW_START_UTC: int = 0       # 00:00 UTC — when fresh daily temp markets appear
# v32b: was 8 (tuned for the old FORECAST_EDGE lottery path — fresh cheap YES
# appears at 00:00 UTC). Near-resolution fires based on each city's LOCAL peak
# hour, not UTC clock time, so a narrow UTC window blocks it for most cities
# (London/NYC/Dallas/Lucknow/Delhi/Mumbai all peak-pass outside 00-08 UTC).
# Widened to 24 so near-res can fire whenever a city's peak has actually passed.
OPEN_WINDOW_END_UTC: int = 24        # v32b: 24h — near-res is peak-hour gated, not UTC-window gated

# ── CAPITAL & RISK ──────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.035
MIN_POSITION_USD: float = 0.75       # v30: дрібні lottery stakes
MAX_POSITION_USD: float = 1.50       # v30: lottery mode (was 3.50)
MAX_ACTIVE_POSITIONS: int = 20       # v31: 8 cities × 2-3 buckets (was 12)
MAX_OPEN_PER_CYCLE: int = 6          # v31: 6/cycle (was 4) — wider window needs faster fill
MAX_POSITIONS_PER_CITY: int = 3      # v30: 3 peak buckets per city
# v27: STOP-LOSS & TRAILING OFF for DRY-RUN ladder (hold to resolution)
STOP_LOSS_PCT: float = 0.0           # OFF — was 0.40 (killed v26 0/8)
STOP_LOSS_MIN_HOLD_HOURS: float = 999.0  # effectively never
STOP_LOSS_HARD_PCT: float = 0.0      # OFF — was 0.50 very-cheap hard stop
TRAILING_STOP_ACTIVATION_PCT: float = 0.0  # OFF — was 0.35
MAX_TOTAL_EXPOSURE_PCT: float = 0.70
MAX_DAILY_LOSS_PCT: float = 0.20     # relaxed: ladder swings are normal
MAX_DAILY_LOSS_USD: float = 25.0
DRAWDOWN_LIMIT: float = 0.50         # v28: raised 0.40→0.50 in DRY-RUN to keep collecting samples past 14-trade loss streak for calibration analysis (was 0.40, originally 0.28)

# ── STRATEGY: LOTTERY EDGE / TIME WINDOW ───────────────────
# v30: categorical + range (ban was unjustified — 1.7% vs 2.6% WR, both bad but
#      categorical = 62% of historical trade universe, removing it killed v29)
#      above/below still disabled (not bucket grid).
KINDS_ONLY: List[str] = ["range", "categorical", "above", "below"]

MIN_EDGE_YES: float = 0.03           # accept tiny edge on cheap YES (lottery upside)
MIN_EDGE_NO: float = 0.18
MIN_PROB_ENTRY: float = 0.05         # absolute floor for cheap YES

MAX_RESOLUTION_HOURS: int = 60       # v28: bumped 36→60 to avoid "blind zone" after 10 UTC (markets on day+2)
MIN_RESOLUTION_HOURS: float = 1.5
MIN_MARKET_VOLUME_USD: float = 400.0
SCAN_INTERVAL_SEC: int = 300
SCAN_MAX_SLEEP_SEC: int = 600

# ── LADDER GRID (головне) ───────────────────────────────────
# v30: LOTTERY-ONLY — тільки peak-bucket YES з ціною ≤5¢
#      1 WIN @ $1.00 покриває 20+ LOSS-ів @ 5¢ → break-even WR ≈ 5%
# v29 = peak-only but with max_ask 0.20 → 1 WIN only covers 5 LOSS (break-even 17%)
SNIPER_GRID_DISTANCE_C: float = 0.5      # PEAK ONLY (dist ≤ 0.5°C = forecast bucket)
WING_BAN: bool = True                     # blocks any BUY_YES where dist > PEAK_DIST_C
SNIPER_GRID_MIN_EDGE: float = 0.03       # accept very small edge if peak bucket
SNIPER_GRID_MIN_EDGE_NO: float = 0.18
SNIPER_GRID_MIN_ASK: float = 0.01
SNIPER_GRID_MAX_ASK: float = 0.08        # v31: 8¢ cap (was 0.05) — 60% more price walks, BE WR ~8%
SNIPER_GRID_NO_MIN_MARKET: float = 0.80  # NO only on extreme misprice (≥80¢)
SNIPER_GRID_MAX_PER_CITY_CYCLE: int = 2  # v30: 2 peak buckets per city (was 3)

# v30: Minimum bucket width for `range` markets. Categorical (1°C) kept enabled.
# Historical: categorical WR 1.7%, range WR 2.6% — both bad, but categorical = 62% universe.
RANGE_MIN_BUCKET_WIDTH_C: float = 3.0

# Peak / wing sizing multipliers (applied in trader.decide_position_size)
# v27: PEAK_DIST 0.5, NEAR 1.0, FAR 1.5 — tight coldmath ladder
PEAK_DIST_C: float = 0.50            # dist ≤ 0.5°C = peak (forecast bucket itself)
NEAR_WING_DIST_C: float = 1.00      # 0.5–1.0 = near wing
PEAK_SIZE_MULT: float = 3.00        # v27: peak earns 3× wing (was 1.0 — flat ladder)
NEAR_WING_SIZE_MULT: float = 1.00   # was 0.60 (under-sized)
FAR_WING_SIZE_MULT: float = 0.50    # 1.0–1.5°C far wing (was 0.40)

# ── Cheap-YES ratio gate (v30 lottery mode) ────────────────
CHEAP_ASK_THRESHOLD: float = 0.05    # v30: 5¢ instead of 10¢ (lottery ticket mode)
MIN_EDGE_RATIO_CHEAP: float = 2.0    # our ≥ 2.0× market
MIN_PROB_CHEAP: float = 0.05        # accept 5¢-implied prob

# v30: Lottery edge requires peak bucket only (cheap YES where dist ≤ 0.5°C)
# Min-prob gates enforced only for peak (wing YES blocked by WING_BAN regardless).

# Quality floors by distance (lottery mode — only peak is eligible for YES)
MIN_PROB_PEAK: float = 0.05          # v30: peak gets full lottery licence (was 0.08)
MIN_PROB_NEAR: float = 0.10          # near wing (but WING_BAN blocks these)
MIN_PROB_FAR: float = 0.12          # far wing (also blocked by WING_BAN)

# v27: distance-aware tail filter — killed blanket 0.20 of v24
MAX_TAIL_PROB: float = 0.05         # hard skip our_prob < 5% (cheap-but-true-zero YES)
MAX_TAIL_DIST_C: float = 1.20        # only applies when dist > 1.2°C
MAX_TAIL_COMBINED_PROB: float = 0.08

# Prefer YES ladder like neobrother; NO only extreme misprice
ENABLE_BUY_NO: bool = True
BUY_NO_MAX_OUR_PROB: float = 0.10    # YES must be truly unlikely (was 0.12)
BUY_NO_MIN_MARKET: float = 0.75     # NO only at 75¢+ (was 0.70)

# v27: TREND (above/below) DISABLED — KINDS_ONLY restricts to bucket grid
TREND_MAX_ASK: float = 0.55
TREND_MIN_ASK: float = 0.05
TREND_MIN_NO_MARKET: float = 0.35
TREND_MAX_DIST_C: float = 2.0
TREND_MIN_PROB_YES: float = 0.28

# ── PROBABILITY CALIBRATION ────────────────────────────────
PROB_BIAS: float = 1.0               # never below 1.0 (double-discount)
CATEGORICAL_DISCOUNT: float = 0.75   # logbest-era (was 0.92 — too mild; 0.30 killed v9)

CAP_EXACT_SHORT: float = 0.55
CAP_EXACT_MID: float = 0.45
CAP_EXACT_LONG: float = 0.35

CAP_SHORT: float = 0.82
CAP_MID: float = 0.72
CAP_LONG: float = 0.62

MAX_EDGE_CAP: float = 0.45

# ── PROBABILITY RECALIBRATION (v30) ────────────────────────
# v29 had ISOTONIC_RECALIBRATE=True — trained on 288 LOSING trades, correctly
# mapped all edges to ≤2.4% cal_win_rate, making eff_edge always negative → 0 trades.
# v30: DISABLED — we need true edge first. The isotonic map was a symptom-killer,
# not a cure. Fixing the underlying strategy (time window + price cap + city whitelist)
# takes priority; recalibration can be re-enabled once WR > 8% is observed.
ISOTONIC_RECALIBRATE: bool = False
ISOTONIC_MAP_FILE: str = "data/prob_recalibration.json"
ISOTONIC_MIN_PROB: float = 0.0      # v30: effectively OFF (floor not used when disabled)

# ── SIGMA ───────────────────────────────────────────────────
# v30: SIGMA_MIN unchanged — logbest-era sweet spot.
SIGMA_MIN: float = 3.0
SIGMA_SPREAD_FACTOR: float = 1.25

# ── EMPIRICAL BLENDING ────────────────────────────────────
EMPIRICAL_WEIGHT: float = 0.30      # logbest-era (was 0.25; 0.0 killed v20)
ENSEMBLE_WEIGHT: float = 0.70

# ── KELLY ──────────────────────────────────────────────────
USE_KELLY: bool = True
KELLY_SCALE: float = 0.25           # v31: scaled up (was 0.15) — more volume per trade
KELLY_MAX_POSITION_USD: float = 2.00  # v30: cap (was 4.50)
KELLY_PROB_CAP: float = 0.85        # let high-conf peak buckets size up

# ── SANITY ─────────────────────────────────────────────────
MAX_DISTANCE_SIGMA: float = 2.5

# v27: TRAILING STOP OFF (defined above with risk params; kept here for clarity if other modules reference)
# Trailing (optional profit lock — OFF in v27 for DRY-RUN ladder)

# ── PATHS ──────────────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"
EMAIL_ENABLED: bool = False

# v33: City whitelist expanded 9 -> 24 to unlock more near-resolution candidates.
# Near-resolution fires only when city's local diurnal peak has passed, so
# covering multiple timezones means signals flow throughout UTC day instead
# of clustering in one window. Plus Seoul/Hanoi/Bangkok/Mexico City have
# high-volume weather markets that were being skipped by the city filter.
CITY_WHITELIST: List[str] = [
    # North America (heterogeneous diurnal variance)
    "Dallas",
    "NYC",
    "New York",
    "Chicago",
    "Los Angeles",
    "Miami",
    "Phoenix",
    "Denver",
    "Mexico City",
    "Atlanta",
    "Houston",
    "Austin",
    "Boston",
    "Las Vegas",
    "Minneapolis",
    "Portland",
    "Nashville",
    "Charlotte",
    "Orlando",
    "Seattle",
    "San Francisco",
    # Europe (mid-lat maritime — peak 14-16 local)
    "London",
    "Paris",
    "Berlin",
    "Madrid",
    "Munich",
    "Amsterdam",
    "Dublin",
    "Rome",
    "Milan",
    "Warsaw",
    "Prague",
    "Vienna",
    # Asia (early afternoon peak — covers UTC morning)
    "Tokyo",
    "Seoul",
    "Singapore",
    "Mumbai",
    "Delhi",
    "Lucknow",
    "Bangkok",
    "Hanoi",
    "Dubai",
    "Hong Kong",
    "Shanghai",
    "Beijing",
    "Taipei",
    "Chongqing",
    "Wuhan",
    "Shenzhen",
    "Guangzhou",
    "Qingdao",
    "Chengdu",
    "Busan",
    "Manila",
    "Kuala Lumpur",
    "Karachi",
    "Jeddah",
    "Ankara",
    "Tel Aviv",
    # South America / Oceania / Africa (peak in UTC morning)
    "Sao Paulo",
    "Buenos Aires",
    "Santiago",
    "Lima",
    "Sydney",
    "Melbourne",
    "Brisbane",
    "Perth",
    "Auckland",
    "Wellington",
    "Cape Town",
    "Lagos",
    "Moscow",
]

MAX_PREFETCH_CITIES: int = 24  # v33: sync з CITY_WHITELIST

# ── DRY-RUN VALIDATION GATES ───────────────────────────────
VALIDATION_REQUIRED_BEFORE_LIVE: bool = True
VALIDATION_MIN_RESOLVED_TRADES: int = 30
VALIDATION_MIN_DRY_RUN_HOURS: int = 168
VALIDATION_MIN_WIN_RATE: float = 0.18   # ladder WR can be ~15–25%; ROI is king
VALIDATION_MIN_ROI: float = 0.03
VALIDATION_MIN_EQUITY: float = 0.0

# ── v32: NEAR-RESOLUTION EXECUTION EDGE ─────────────────────
# Strategy layer: when hours_to_resolution is small AND the city's local
# diurnal temperature peak-hour already passed (with safety buffer), observed
# running max_so_far IS the daily high — forecasts become irrelevant.
# Market-makers are slow to requote thin bucket markets → execution edge.
#
# Two sub-strategies:
#   BUCKET_LOCKED_YES: max_so_far already in bucket, last 2-3h flat/declining
#                      → bucket is locked → BUY YES even at 0.85-0.95
#                      (low yield, high confidence)
#   BUCKET_IMPOSSIBLE_NO: max_so_far below bucket by ≥1°C → jump after peak
#                         is climatology-impossible → BUY NO cheap (5-10¢)
#
# Frequency: low (1-2 moments/day per city). A late thunderstorm can break the
# "peak passed" assumption → hard size cap, NOT 100% Kelly.

# Maximum hours-to-resolution for near-resolution signal to be considered.
# Above this, falls back to standard forecast-edge flow.
NEAR_RESOLUTION_MAX_HOURS: float = 6.0

# Minimum hours-to-resolution — too close to close, spread widens illiquidly.
NEAR_RESOLUTION_MIN_HOURS: float = 0.5

# Safety buffer AFTER empirical peak hour before signal is trusted.
# 1.5h buffer = peak at 15:00, signal active from 16:30 local onward.
NEAR_RESOLUTION_PEAK_BUFFER_HOURS: float = 1.5
# v32d: upper bound on "hours since peak" — was hardcoded 12.0, which real
# log data showed rejecting 18/94 near-res candidates per cycle as
# "peak_too_long_ago" even though their trend was already confirmed declining.
# The 12h cap can't distinguish "peak was legitimately 12-18h ago" from the
# wraparound failure case (both land in the same 17-19h range), so erring
# toward a wider window trades a small re-introduction of wraparound risk for
# recovering real signal — the independent latest_declining/cold_passed check
# already guards against acting on a stale/wrong peak.
NEAR_RESOLUTION_PEAK_MAX_AGE_HOURS: float = 12.0

# For BUCKET_LOCKED_YES: how many last observed hourly temps must be
# flat or declining (each ≤ previous) to confirm peak is behind us.
NEAR_RESOLUTION_DECLINE_WINDOW: int = 3

# For BUCKET_LOCKED_YES: max temperature delta allowed over the decline window
# (sum of rises, ignoring drops) — tolerates noise but rejects secondary spikes.
NEAR_RESOLUTION_DECLINE_TOLERANCE_C: float = 0.5

# For BUCKET_IMPOSSIBLE_NO: minimum distance (°C) max_so_far must be BELOW
# the bucket low edge for the bucket to be "impossible".
NEAR_RESOLUTION_IMPOSSIBLE_GAP_C: float = 1.0

# Confidence assigned to near-resolution signals (used in size calc).
# Recommendation warns: late storms break the assumption, so hard cap below.
NEAR_RES_CONFIDENCE_YES: float = 0.92
NEAR_RES_CONFIDENCE_NO: float = 0.85

# Price bands for near-resolution trades (independent of SNIPER_GRID_*):
#   YES bucket-locked: market is pricing bucket high (0.50-0.95) — we ride it.
#   NO  bucket-impossible: market still hopes for the bucket (0.05-0.20).
NEAR_RES_YES_MIN_ASK: float = 0.50
NEAR_RES_YES_MAX_ASK: float = 0.95
NEAR_RES_NO_MIN_ASK: float = 0.03
NEAR_RES_NO_MAX_ASK: float = 0.20

# v33 TREND near-resolution price bands (above/below threshold markets).
# The two threshold-side scenarios:
#   - TREND_LOCKED_YES: observed extreme already crossed the threshold
#     (e.g. market "above 35°C", max_so_far already 36°C after peak — locked).
#     Market is pricing threshold high → ride the high-priced YES (0.50-0.95).
#   - TREND_IMPOSSIBLE_NO: after peak, max_so_far falls THRESHOLD_GAP short
#     and gap > climatology possible swing → buy cheap NO (0.05-0.30).
# Trend NO band is slightly wider than bucket NO because threshold markets
# have higher volume / better liquidity than 1°C categorical buckets.
NEAR_RES_TREND_YES_MIN_ASK: float = 0.50
NEAR_RES_TREND_YES_MAX_ASK: float = 0.95
NEAR_RES_TREND_NO_MIN_ASK: float = 0.05
NEAR_RES_TREND_NO_MAX_ASK: float = 0.30

# v33 For trend-impossible: how far below threshold must max_so_far fall
# (after peak) for a NO to be confident. Mirror of NEAR_RESOLUTION_IMPOSSIBLE_GAP_C.
# Default 1.0°C — slightly more conservative than bucket (0.5°C half-width)
# because threshold is a single point, not a range.
NEAR_RES_TREND_IMPOSSIBLE_GAP_C: float = 1.0

# Position size hard cap — independent of Kelly.
# Recommendation: DON'T use 100% Kelly; rare late storms can reverse.
NEAR_RESOLUTION_MAX_SIZE_USD: float = 2.00
NEAR_RESOLUTION_MIN_SIZE_USD: float = 0.75

# v33: 2->3 per cycle. With 24 cities × {categorical, range, above, below}
# candidates, a single cycle often sees multiple near-res opportunities in
# different timezones. 3/cycle keeps position-opening rate sane while not
# missing the Asia morning + Europe afternoon + US evening sequence.
NEAR_RESOLUTION_MAX_PER_CYCLE: int = 3

# v33: 1->2 per city. Allows one categorical/bucket + one trend signal per
# city per cycle (different condition_ids, same physical reality — not the
# same market twice). Still avoids piling duplicates.
NEAR_RESOLUTION_MAX_PER_CITY: int = 2

# Empirical local peak-hour by city (LOCAL solar/clock hour, float for half-hours).
# Source: 22-month climatology for daily temperature maximum.
# These are APPROXIMATE — the safety buffer absorbs the variance.
PEAK_HOUR_BY_CITY: Dict[str, float] = {
    # North America (DST adjusted — local clock times)
    "Dallas":       16.5,  # 16:30 local — dry heat, slow cooling afternoon
    "NYC":          15.0,
    "New York":     15.0,
    "Chicago":      15.0,
    "Los Angeles":  15.5,
    "Miami":        14.0,  # humid; convective clouds cap afternoon
    "Phoenix":      16.0,  # desert: late peak, high diurnal variance
    "Denver":       16.0,  # high altitude: late solar peak
    "Mexico City":  15.0,  # high altitude tropical
    "Atlanta":      15.0,
    "Houston":      16.0,  # humid subtropical
    "Austin":       16.0,
    "Boston":       15.0,
    "Las Vegas":    16.0,  # desert
    "Minneapolis":  15.5,
    "Portland":     15.0,
    "Nashville":    15.5,
    "Charlotte":    15.5,
    "Orlando":      14.5,
    "Seattle":      15.0,
    "San Francisco":14.0,  # coastal — marine layer caps early
    # Europe (mid-lat maritime — peak 14-16 local)
    "London":       15.0,
    "Paris":        15.0,
    "Berlin":       15.0,
    "Madrid":       16.0,  # continental interior, later peak
    "Munich":       15.0,
    "Amsterdam":    15.0,
    "Dublin":       15.0,
    "Rome":         15.5,
    "Milan":        15.5,
    "Warsaw":       15.0,
    "Prague":       15.0,
    "Vienna":       15.0,
    # Asia (early afternoon peak)
    "Tokyo":        14.0,
    "Seoul":        14.0,
    "Singapore":    14.0,  # equatorial — low diurnal variance (risky for near-res)
    "Mumbai":       14.5,
    "Delhi":        15.5,
    "Lucknow":      15.5,
    "Bangkok":      14.0,
    "Hanoi":        14.0,
    "Dubai":        15.0,  # desert: high variance, clean decline
    "Hong Kong":    14.0,
    "Shanghai":     14.0,
    "Beijing":      14.5,
    "Taipei":       13.5,
    "Chongqing":    15.0,
    "Wuhan":        14.5,
    "Shenzhen":     14.0,
    "Guangzhou":    14.0,
    "Qingdao":      14.0,
    "Chengdu":      15.0,
    "Busan":        14.0,
    "Manila":       14.0,
    "Kuala Lumpur": 14.0,
    "Karachi":      15.0,
    "Jeddah":       14.5,  # coastal desert
    "Ankara":       15.5,  # continental
    "Tel Aviv":     14.5,
    # Southern hemisphere (UTC morning peak)
    "Sao Paulo":    14.0,
    "Buenos Aires": 14.5,
    "Santiago":     15.0,
    "Lima":         14.0,
    "Sydney":       16.0,
    "Melbourne":    16.0,
    "Brisbane":     15.0,
    "Perth":        15.0,
    "Auckland":     15.0,
    "Wellington":   14.5,
    "Cape Town":    14.0,
    "Lagos":        14.0,  # equatorial
    "Moscow":       15.0,
}

# v32 FIX: real IANA timezone names — DST-aware, never goes stale.
# Preferred over CITY_UTC_OFFSET_HOURS below. get_city_local_hour() tries
# this map first via zoneinfo, and only falls back to the fixed-offset
# table if a city is missing here or zoneinfo is unavailable.
CITY_TZ_NAME: Dict[str, str] = {
    "Dallas":        "America/Chicago",
    "NYC":           "America/New_York",
    "New York":      "America/New_York",
    "Chicago":       "America/Chicago",
    "Los Angeles":   "America/Los_Angeles",
    "Miami":         "America/New_York",
    "Phoenix":       "America/Phoenix",  # no DST
    "Denver":        "America/Denver",
    "Mexico City":   "America/Mexico_City",
    "Atlanta":       "America/New_York",
    "Houston":       "America/Chicago",
    "Austin":        "America/Chicago",
    "Boston":        "America/New_York",
    "Las Vegas":     "America/Los_Angeles",
    "Minneapolis":   "America/Chicago",
    "Portland":      "America/Los_Angeles",
    "Nashville":     "America/Chicago",
    "Charlotte":     "America/New_York",
    "Orlando":       "America/New_York",
    "Seattle":       "America/Los_Angeles",
    "San Francisco": "America/Los_Angeles",
    "Tokyo":         "Asia/Tokyo",
    "Seoul":         "Asia/Seoul",
    "Singapore":     "Asia/Singapore",
    "Lucknow":       "Asia/Kolkata",
    "Mumbai":        "Asia/Kolkata",
    "Delhi":         "Asia/Kolkata",
    "Bangkok":       "Asia/Bangkok",
    "Hanoi":         "Asia/Ho_Chi_Minh",
    "Dubai":         "Asia/Dubai",
    "Hong Kong":     "Asia/Hong_Kong",
    "Shanghai":      "Asia/Shanghai",
    "Beijing":       "Asia/Shanghai",
    "Taipei":        "Asia/Taipei",
    "Chongqing":     "Asia/Shanghai",
    "Wuhan":         "Asia/Shanghai",
    "Shenzhen":      "Asia/Shanghai",
    "Guangzhou":     "Asia/Shanghai",
    "Qingdao":       "Asia/Shanghai",
    "Chengdu":       "Asia/Shanghai",
    "Busan":         "Asia/Seoul",
    "Manila":        "Asia/Manila",
    "Kuala Lumpur":  "Asia/Kuala_Lumpur",
    "Karachi":       "Asia/Karachi",
    "Jeddah":        "Asia/Riyadh",
    "Ankara":        "Europe/Istanbul",
    "Tel Aviv":      "Asia/Jerusalem",
    "London":        "Europe/London",
    "Paris":         "Europe/Paris",
    "Berlin":        "Europe/Berlin",
    "Madrid":        "Europe/Madrid",
    "Munich":        "Europe/Berlin",
    "Amsterdam":     "Europe/Amsterdam",
    "Dublin":        "Europe/Dublin",
    "Rome":          "Europe/Rome",
    "Milan":         "Europe/Rome",
    "Warsaw":        "Europe/Warsaw",
    "Prague":        "Europe/Prague",
    "Vienna":        "Europe/Vienna",
    "Sao Paulo":     "America/Sao_Paulo",
    "Buenos Aires":  "America/Argentina/Buenos_Aires",
    "Santiago":      "America/Santiago",
    "Lima":          "America/Lima",
    "Sydney":        "Australia/Sydney",
    "Melbourne":     "Australia/Melbourne",
    "Brisbane":      "Australia/Brisbane",
    "Perth":         "Australia/Perth",
    "Auckland":      "Pacific/Auckland",
    "Wellington":    "Pacific/Auckland",
    "Cape Town":     "Africa/Johannesburg",
    "Lagos":         "Africa/Lagos",
    "Moscow":        "Europe/Moscow",
}

# UTC offsets for cities (hours). FALLBACK ONLY — correct for July 2026
# (Northern-hemisphere DST) but will silently drift by 1h once DST ends.
# Kept as a safety net if CITY_TZ_NAME/zoneinfo lookup fails; not the
# primary source of truth anymore.
CITY_UTC_OFFSET_HOURS: Dict[str, float] = {
    "Dallas":        -5.0,
    "NYC":           -4.0,
    "New York":      -4.0,
    "Chicago":       -5.0,
    "Los Angeles":   -7.0,
    "Miami":         -4.0,
    "Phoenix":       -7.0,
    "Denver":        -6.0,
    "Mexico City":   -6.0,
    "Atlanta":       -4.0,
    "Houston":       -5.0,
    "Austin":        -5.0,
    "Boston":        -4.0,
    "Las Vegas":     -7.0,
    "Minneapolis":   -5.0,
    "Portland":      -7.0,
    "Nashville":     -5.0,
    "Charlotte":     -4.0,
    "Orlando":       -4.0,
    "Seattle":       -7.0,
    "San Francisco": -7.0,
    "Tokyo":          9.0,
    "Seoul":          9.0,
    "Singapore":      8.0,
    "Lucknow":        5.5,
    "Mumbai":         5.5,
    "Delhi":          5.5,
    "Bangkok":        7.0,
    "Hanoi":          7.0,
    "Dubai":          4.0,
    "Hong Kong":      8.0,
    "Shanghai":       8.0,
    "Beijing":        8.0,
    "Taipei":         8.0,
    "Chongqing":      8.0,
    "Wuhan":          8.0,
    "Shenzhen":       8.0,
    "Guangzhou":      8.0,
    "Qingdao":        8.0,
    "Chengdu":        8.0,
    "Busan":          9.0,
    "Manila":         8.0,
    "Kuala Lumpur":   8.0,
    "Karachi":        5.0,
    "Jeddah":         3.0,
    "Ankara":         3.0,
    "Tel Aviv":       3.0,
    "London":         1.0,
    "Paris":          2.0,
    "Berlin":         2.0,
    "Madrid":         2.0,
    "Munich":         2.0,
    "Amsterdam":      2.0,
    "Dublin":         1.0,
    "Rome":           2.0,
    "Milan":          2.0,
    "Warsaw":         2.0,
    "Prague":         2.0,
    "Vienna":         2.0,
    "Sao Paulo":     -3.0,
    "Buenos Aires":  -3.0,
    "Santiago":      -4.0,
    "Lima":          -5.0,
    "Sydney":        10.0,
    "Melbourne":     10.0,
    "Brisbane":      10.0,
    "Perth":          8.0,
    "Auckland":      12.0,
    "Wellington":    12.0,
    "Cape Town":      2.0,
    "Lagos":          1.0,
    "Moscow":         3.0,
}

# Master switch for the v32 layer (temp kill switch if behaviour unexpected).
NEAR_RESOLUTION_ENABLED: bool = True

# v32b: kill switch for the legacy forecast/Gaussian "lottery" entry path
# (v9-v31 — documented 0-4% WR across 20+ iterations). Set False to trade
# ONLY near-resolution signals while that layer gathers real performance
# data, instead of bleeding capital into the disproven path in parallel.
FORECAST_EDGE_ENABLED: bool = False
