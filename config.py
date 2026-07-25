"""
config.py — Weather Bot v30 (LOTTERY EDGE / TIME WINDOW)

Стратегія фокусується на дешевих YES (≤5¢) тільки у вікні 00:00-06:00 UTC,
де один WIN @ $1.00 покриває 20+ LOSS-ів @ 5¢. Break-even WR знижується до 5-12%.

Аналіз 294 CLOSE trades показав:
  - Avg WIN $5.78 vs Avg LOSS $1.28 → потрібен 39% WR для break-even
  - Реальний WR = 2.7% → математично неможливо profitувати з v27/v29 стратегією
  - Всі 8 historic wins у вікні 00:00-04:00 UTC, peak-bucket, ≤15¢ entry
  - 12:00 UTC = 109 trades, 0.9% WR — бот спамив stale midday markets

v30 фікси vs v29:
  1) ISOTONIC_RECALIBRATE OFF — правильний, але вбиває все (вивчив 288 losers)
  2) KINDS_ONLY range + categorical (categorical ban was unjustified: 1.7% vs 2.6% WR)
  3) SNIPER_GRID_MAX_ASK 0.20 → 0.05 — ТІЛЬКИ lottery tickets (1 WIN = $1, 1 LOSS = 5¢)
  4) CITY_WHITELIST 28 → 6 (тільки historic winner-cities)
  5) OPEN_WINDOW_START_UTC/END_UTC = 0/6 — trades тільки у вікні fresh daily markets
  6) MAX_POSITION_USD 3.50 → 1.50 — дрібні ставки для lottery mode
  7) KELLY_SCALE 0.25 → 0.15 — conservative sizing для high-variance lottery

Уроки (НЕ повторювати):
  v9:   discount 0.30 → 0% WR
  v15–v19: METAR arb → 0% WR
  v21:  SIGMA_MIN=1.5 → our_prob=0
  v22:  SIGMA_MIN=4.5 → buckets too wide
  v23–v25: lottery 1–3¢ dist=3°C → 0% WR
  v26:  COLDMATH LADDER 0/8 WR
  v27-v29: maths break-even fail (avg win $5.78 / avg loss $1.28 / WR 2.7%)
"""

from typing import List
import os

DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── v30: TIME WINDOW (fresh daily markets only) ───────────
# Historical analysis: all 8 wins opened 00:00-04:00 UTC.
# 12:00 UTC had 109 trades with 0.9% WR — bot wasted capital on stale midday markets.
OPEN_WINDOW_START_UTC: int = 0       # 00:00 UTC — when fresh daily temp markets appear
OPEN_WINDOW_END_UTC: int = 6         # 06:00 UTC — cutoff (after this, markets are stale)

# ── CAPITAL & RISK ──────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.035
MIN_POSITION_USD: float = 0.75       # v30: дрібні lottery stakes
MAX_POSITION_USD: float = 1.50       # v30: lottery mode (was 3.50)
MAX_ACTIVE_POSITIONS: int = 12       # v30: 6 cities × 2 buckets each
MAX_OPEN_PER_CYCLE: int = 4          # v30: щільніший grid в active window
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
KINDS_ONLY: List[str] = ["range", "categorical"]

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
SNIPER_GRID_MAX_ASK: float = 0.05        # v30: LOTTERY ONLY — 5¢ cap (was 0.20)
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
KELLY_SCALE: float = 0.15           # v30: conservative (was 0.25) — high-variance lottery
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

# v30: City whitelist — ТІЛЬКИ 6 historic winner-cities (з 294 trades аналізу)
# v29 28-city whitelist включав багато 0% WR міст → витрачав scan time + capital
CITY_WHITELIST: List[str] = [
    "Dallas",
    "Singapore",
    "London",
    "NYC",
    "New York",
    "Lucknow",
    "Tokyo",
]

MAX_PREFETCH_CITIES: int = 6

# ── DRY-RUN VALIDATION GATES ───────────────────────────────
VALIDATION_REQUIRED_BEFORE_LIVE: bool = True
VALIDATION_MIN_RESOLVED_TRADES: int = 30
VALIDATION_MIN_DRY_RUN_HOURS: int = 168
VALIDATION_MIN_WIN_RATE: float = 0.18   # ladder WR can be ~15–25%; ROI is king
VALIDATION_MIN_ROI: float = 0.03
VALIDATION_MIN_EQUITY: float = 0.00
