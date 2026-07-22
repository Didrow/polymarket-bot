"""
config.py — Weather Bot v27 (PURE SNIPER GRID)

Повернення до стратегії logbest.md (2026-06-20: ROI +105.5%, 4.8% WR, $+112 realized).
coldmath (polymarket.com/@coldmath) + neobrother (polymarket.com/@neobrother):
  Прогноз 17.0°C → сітка YES на 16.5/17/17.5 (1°C buckets), small size, hold to resolution.
  Один WIN @ $1.00 покриває 5–8 LOSS-ів @ 5¢, якщо peak bucket реально попав.

Уроки (НЕ повторювати):
  v9:   discount 0.30 → 0% WR
  v15–v19: METAR arb → 0% WR (forecast-bets via circular METAR)
  v21:  SIGMA_MIN=1.5 → our_prob=0 (Gaussian sliver)
  v22:  SIGMA_MIN=4.5 → buckets too wide (under-priced prob)
  v23–v25: SNIPER_GRID_DISTANCE=4.0 + MAX_TAIL_PROB=0 → lottery 1–3¢ dist=3°C → 0% WR
  v26:  COLDMATH LADDER 0/8 WR: STOP_LOSS_PCT=0.40 kills rungs before resolution
        + KELLY_PROB_CAP=0.55 + CATEGORICAL_DISCOUNT=0.92 + SIGMA_MIN=2.2 + season bias

v27 фікси vs v26:
  1) STOP_LOSS повністю OFF для DRY-RUN ladder → тримати до resolution
  2) TRAILING_STOP OFF → coldmath holds to resolution
  3) CATEGORICAL_DISCOUNT 0.92 → 0.75 (logbest-era, SWEET SPOT)
  4) EMPIRICAL_WEIGHT 0.25 → 0.30 (logbest-era; 0 killed v20)
  5) SIGMA_MIN 2.2 → 3.0 (sweet spot between v21 fail & v22 fail; logbest used 3.0–3.5)
  6) MIN_EDGE_YES 0.15 → 0.03 (grid bucket acceptance, NOT trend threshold)
  7) Season bias NUKE (all cities → 0.0; was inflating US hot markets +1.7°C)
  8) SNIPER_GRID_MAX_ASK 0.40 → 0.25 (only cheap-YES zone, per coldmath)
  9) PEAK_DIST_C 0.60 → 0.50 + NEAR_WING_DIST_C 1.10 → 1.00 (tighter ladder)
  10) PEAK_SIZE_MULT 1.0 → 3.0 (peak earns 3× wing, real coldmath weighting)
  11) KELLY_PROB_CAP 0.55 → 0.85 (let high-conf peak buckets size up)
  12) KELLY_SCALE 0.20 → 0.25 (logbest-era)
  13) MAX_TAIL_PROB 0.08 → 0.05 + per-rung: distance-aware, not blanket
  14) KINDS_ONLY: range + categorical ONLY (above/below заборонені: не bucket grid)
  15) TREND_MIN_PROB_YES redundant — removed
"""

from typing import List
import os

DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── CAPITAL & RISK ──────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.035
MIN_POSITION_USD: float = 1.50       # крила ladder — малі ставки
MAX_POSITION_USD: float = 3.50       # peak bucket — трохи більше
MAX_ACTIVE_POSITIONS: int = 18       # 3-4 міста × 5 бакетів + запас
MAX_OPEN_PER_CYCLE: int = 5          # швидше збираємо ladder
MAX_POSITIONS_PER_CITY: int = 5      # buckets 16/17/18/19 → 5 на місто
# v27: STOP-LOSS & TRAILING OFF for DRY-RUN ladder (hold to resolution)
STOP_LOSS_PCT: float = 0.0           # OFF — was 0.40 (killed v26 0/8)
STOP_LOSS_MIN_HOLD_HOURS: float = 999.0  # effectively never
STOP_LOSS_HARD_PCT: float = 0.0      # OFF — was 0.50 very-cheap hard stop
TRAILING_STOP_ACTIVATION_PCT: float = 0.0  # OFF — was 0.35
MAX_TOTAL_EXPOSURE_PCT: float = 0.70
MAX_DAILY_LOSS_PCT: float = 0.20     # relaxed: ladder swings are normal
MAX_DAILY_LOSS_USD: float = 25.0
DRAWDOWN_LIMIT: float = 0.50         # v28: raised 0.40→0.50 in DRY-RUN to keep collecting samples past 14-trade loss streak for calibration analysis (was 0.40, originally 0.28)

# ── STRATEGY: PURE SNIPER GRID ─────────────────────────────
# v27: range + categorical ONLY (bucket grid). above/below disabled — not bucket ladder
KINDS_ONLY: List[str] = ["categorical", "range"]

MIN_EDGE_YES: float = 0.03           # grid bucket acceptance (was 0.15 — killed cheap YES)
MIN_EDGE_NO: float = 0.18
MIN_PROB_ENTRY: float = 0.05         # absolute floor for cheap YES (was 0.08)

MAX_RESOLUTION_HOURS: int = 60       # v28: bumped 36→60 to avoid "blind zone" after 10 UTC (markets on day+2)
MIN_RESOLUTION_HOURS: float = 1.5
MIN_MARKET_VOLUME_USD: float = 400.0
SCAN_INTERVAL_SEC: int = 300
SCAN_MAX_SLEEP_SEC: int = 600

# ── LADDER GRID (головне) ───────────────────────────────────
# v27: tight ±1.5°C, peak at ±0.5°C (coldmath standard grid)
SNIPER_GRID_DISTANCE_C: float = 1.5
SNIPER_GRID_MIN_EDGE: float = 0.03   # accept very small edge if peak bucket (lottery ticket upside)
SNIPER_GRID_MIN_EDGE_NO: float = 0.18
SNIPER_GRID_MIN_ASK: float = 0.01
SNIPER_GRID_MAX_ASK: float = 0.25    # only cheap-YES zone (was 0.40; coldmath cheap grid)
SNIPER_GRID_NO_MIN_MARKET: float = 0.75  # NO only on extreme misprice (market ≥75¢)
SNIPER_GRID_MAX_PER_CITY_CYCLE: int = 5

# Peak / wing sizing multipliers (applied in trader.decide_position_size)
# v27: PEAK_DIST 0.5, NEAR 1.0, FAR 1.5 — tight coldmath ladder
PEAK_DIST_C: float = 0.50            # dist ≤ 0.5°C = peak (forecast bucket itself)
NEAR_WING_DIST_C: float = 1.00      # 0.5–1.0 = near wing
PEAK_SIZE_MULT: float = 3.00        # v27: peak earns 3× wing (was 1.0 — flat ladder)
NEAR_WING_SIZE_MULT: float = 1.00   # was 0.60 (under-sized)
FAR_WING_SIZE_MULT: float = 0.50    # 1.0–1.5°C far wing (was 0.40)

# Ratio edge for cheap YES (our_prob / market_price)
# v27: MIN_PROB_CHEAP 0.05 (accept tiny prob — lottery ticket with high upside)
CHEAP_ASK_THRESHOLD: float = 0.10
MIN_EDGE_RATIO_CHEAP: float = 2.0    # our ≥ 2.0× market (was 2.2)
MIN_PROB_CHEAP: float = 0.05        # accept 5¢-implied prob (was 0.10)

# Quality floors by distance (v27 lowered — ladder accepts cheap far buckets)
MIN_PROB_PEAK: float = 0.08          # dist ≤ peak (peak bucket gets discount)
MIN_PROB_NEAR: float = 0.10          # near wing
MIN_PROB_FAR: float = 0.12          # far wing of ladder

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

# ── SIGMA ───────────────────────────────────────────────────
# v27: 3.0°C — logbest-era sweet spot (v21=1.5 fail; v22=4.5 fail; v26=2.2 fail)
SIGMA_MIN: float = 3.0
SIGMA_SPREAD_FACTOR: float = 1.25

# ── EMPIRICAL BLENDING ────────────────────────────────────
EMPIRICAL_WEIGHT: float = 0.30      # logbest-era (was 0.25; 0.0 killed v20)
ENSEMBLE_WEIGHT: float = 0.70

# ── KELLY ──────────────────────────────────────────────────
USE_KELLY: bool = True
KELLY_SCALE: float = 0.25           # logbest-era (was 0.20)
KELLY_MAX_POSITION_USD: float = 4.50  # peak gets more room (was 3.50)
KELLY_PROB_CAP: float = 0.85        # let high-conf peak buckets size up (was 0.55)

# ── SANITY ─────────────────────────────────────────────────
MAX_DISTANCE_SIGMA: float = 2.5

# v27: TRAILING STOP OFF (defined above with risk params; kept here for clarity if other modules reference)
# Trailing (optional profit lock — OFF in v27 for DRY-RUN ladder)

# ── PATHS ──────────────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"
EMAIL_ENABLED: bool = False

# ── CITIES ─────────────────────────────────────────────────
# 20 US (stable APIs) + 8 non-US that printed in profitable logbest windows
CITY_WHITELIST: List[str] = [
    "NYC", "New York", "Chicago", "Los Angeles", "Miami", "Dallas",
    "Seattle", "Denver", "Atlanta", "Boston", "Houston", "Austin",
    "San Francisco", "Phoenix", "Las Vegas", "Minneapolis",
    "Portland", "Nashville", "Charlotte", "Orlando",
    "London", "Paris", "Tokyo", "Singapore", "Sydney",
    "Sao Paulo", "Buenos Aires", "Busan",
]

MAX_PREFETCH_CITIES: int = 20

# ── DRY-RUN VALIDATION GATES ───────────────────────────────
VALIDATION_REQUIRED_BEFORE_LIVE: bool = True
VALIDATION_MIN_RESOLVED_TRADES: int = 30
VALIDATION_MIN_DRY_RUN_HOURS: int = 168
VALIDATION_MIN_WIN_RATE: float = 0.18   # ladder WR can be ~15–25%; ROI is king
VALIDATION_MIN_ROI: float = 0.03
VALIDATION_MIN_EQUITY: float = 0.00
