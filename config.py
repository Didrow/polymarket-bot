"""
config.py — Weather Bot v26 (COLDMATH LADDER)

Стратегія neobrother / coldmath:
  Прогноз 17.0°C → сітка YES на 15/16/17/18/19°C (або 16.8…17.2 якщо крок тонкий)
  Малі ставки, один WIN покриває 4–5 LOSS-ів на крилах.

Уроки (НЕ повторювати):
  v9:  discount 0.30 → 0% WR
  v15–v19: METAR arb → 0% WR
  v21: SIGMA_MIN=1.5 → our_prob=0 майже всюди
  v22: SIGMA_MIN=4.5 → our_prob занижений у 2×
  v23–v25: SNIPER_GRID_DISTANCE=4.0 + MAX_TAIL_PROB=0 → lottery 1–3¢ dist=3°C → 0% WR (0/8+)
  v24 filter правильний за ідеєю; v25 його вимкнув ДО підтвердження fix resolution

v26 принципи:
  1) Тільки бакети біля прогнозу (±1.5°C) — щільна ladder
  2) Peak-first (dist↑) — не сортувати за phantom edge на хвостах
  3) Min our_prob + ratio edge на дешевих YES
  4) Peak size > wing size
  5) DRY_RUN=True, 7 днів валідації перед LIVE
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
MAX_ACTIVE_POSITIONS: int = 15       # місце під 3 міста × 5 бакетів
MAX_OPEN_PER_CYCLE: int = 5          # швидше збираємо ladder
MAX_POSITIONS_PER_CITY: int = 5      # 17±2 → до 5 бакетів на місто
STOP_LOSS_PCT: float = 0.40          # ladder тримаємо до resolution
STOP_LOSS_MIN_HOLD_HOURS: float = 6.0
MAX_TOTAL_EXPOSURE_PCT: float = 0.70
MAX_DAILY_LOSS_PCT: float = 0.12
MAX_DAILY_LOSS_USD: float = 15.0
DRAWDOWN_LIMIT: float = 0.28

# ── STRATEGY: COLDMATH LADDER ───────────────────────────────
# categorical + range = сходинки сітки; above/below — лише якісні тренди
KINDS_ONLY: List[str] = ["categorical", "range", "above", "below"]

MIN_EDGE_YES: float = 0.15           # trend above/below
MIN_EDGE_NO: float = 0.18
MIN_PROB_ENTRY: float = 0.08         # absolute floor for YES

MAX_RESOLUTION_HOURS: int = 36       # tighter horizon = better forecast
MIN_RESOLUTION_HOURS: float = 1.5
MIN_MARKET_VOLUME_USD: float = 400.0
SCAN_INTERVAL_SEC: int = 300
SCAN_MAX_SLEEP_SEC: int = 600

# ── LADDER GRID (головне) ───────────────────────────────────
# dist ≤ 1.5°C: якщо прогноз 17.0 — беремо ~15.5…18.5 (1°C buckets: 16,17,18)
SNIPER_GRID_DISTANCE_C: float = 1.5
SNIPER_GRID_MIN_EDGE: float = 0.03
SNIPER_GRID_MIN_EDGE_NO: float = 0.18
SNIPER_GRID_MIN_ASK: float = 0.01
SNIPER_GRID_MAX_ASK: float = 0.40     # ladder на дешевих/середніх YES
SNIPER_GRID_NO_MIN_MARKET: float = 0.72
SNIPER_GRID_MAX_PER_CITY_CYCLE: int = 5

# Peak / wing sizing multipliers (applied in trader.decide_position_size)
PEAK_DIST_C: float = 0.60            # dist ≤ 0.6°C = peak (17.0 @ 17)
NEAR_WING_DIST_C: float = 1.10       # 0.6–1.1 = near wing
PEAK_SIZE_MULT: float = 1.00
NEAR_WING_SIZE_MULT: float = 0.60
FAR_WING_SIZE_MULT: float = 0.40     # 1.1–1.5°C

# Ratio edge for cheap YES (our_prob / market_price)
# 1¢ market needs our_prob ≥ 2.5¢-implied = 0.025*2.5… but we use absolute floor too
CHEAP_ASK_THRESHOLD: float = 0.10
MIN_EDGE_RATIO_CHEAP: float = 2.2    # our ≥ 2.2× market
MIN_PROB_CHEAP: float = 0.10         # never buy 1–3¢ lottery with our_prob 6%

# Quality floors by distance
MIN_PROB_PEAK: float = 0.10          # dist ≤ peak
MIN_PROB_NEAR: float = 0.12          # near wing
MIN_PROB_FAR: float = 0.15           # far wing of ladder

# Soft tail filters (re-enabled, calibrated — NOT the broken 0.20 blanket of v24
# that blocked all cheap peak 12–18% buckets; distance-aware instead)
MAX_TAIL_PROB: float = 0.08          # hard skip our_prob < 8% for YES grid
MAX_TAIL_DIST_C: float = 1.20
MAX_TAIL_COMBINED_PROB: float = 0.12

# Prefer YES ladder like neobrother; NO only extreme misprice
ENABLE_BUY_NO: bool = True
BUY_NO_MAX_OUR_PROB: float = 0.12    # YES must be truly unlikely
BUY_NO_MIN_MARKET: float = 0.70

# Trend (above/below) — no 1¢ phantom "94°F or higher"
TREND_MAX_ASK: float = 0.55
TREND_MIN_ASK: float = 0.05          # reject 1–4¢ trend lottery
TREND_MIN_NO_MARKET: float = 0.35
TREND_MAX_DIST_C: float = 2.0
TREND_MIN_PROB_YES: float = 0.28

# ── PROBABILITY CALIBRATION ────────────────────────────────
PROB_BIAS: float = 1.0
CATEGORICAL_DISCOUNT: float = 0.92   # mild (Gaussian already conservative)

CAP_EXACT_SHORT: float = 0.55
CAP_EXACT_MID: float = 0.45
CAP_EXACT_LONG: float = 0.35

CAP_SHORT: float = 0.82
CAP_MID: float = 0.72
CAP_LONG: float = 0.62

MAX_EDGE_CAP: float = 0.45

# ── SIGMA ───────────────────────────────────────────────────
# 2.2: between v21(1.5 fail) and v22(4.5 too wide). Peak bucket ~15–25% at 24h.
SIGMA_MIN: float = 2.2
SIGMA_SPREAD_FACTOR: float = 1.25

# ── EMPIRICAL BLENDING ────────────────────────────────────
EMPIRICAL_WEIGHT: float = 0.25
ENSEMBLE_WEIGHT: float = 0.75

# ── KELLY ──────────────────────────────────────────────────
USE_KELLY: bool = True
KELLY_SCALE: float = 0.20            # slightly more conservative than 0.25
KELLY_MAX_POSITION_USD: float = 3.50
KELLY_PROB_CAP: float = 0.55

# ── SANITY ─────────────────────────────────────────────────
MAX_DISTANCE_SIGMA: float = 2.5

# Trailing (optional profit lock — keep mild)
TRAILING_STOP_ACTIVATION_PCT: float = 0.35

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
VALIDATION_MIN_WIN_RATE: float = 0.28   # ladder WR can be ~25–40%; ROI is king
VALIDATION_MIN_ROI: float = 0.05
VALIDATION_MIN_EQUITY: float = 0.00
