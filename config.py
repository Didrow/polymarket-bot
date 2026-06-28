"""
config.py — Polymarket Weather Bot v15 (METAR ARBITRAGE SNIPER)

 Стратегія: арбітраж запізнілого ринку (neobrother-style)
 - Above/below + range/categorical ринки (мета: range бакети "between X-Y°F")
 - Тільки коли METAR підтверджує напрямок (або high-prob fallback ≥70%)
 - Ринки ≤12h до resolution (ринок ще не оновив ціну)
 - Ціна входу 5-70¢ (реальна ліквідність, ринок ще не на 90¢+)
 - Edge ≥4% (знижений поріг для DRY-RUN валідації)
- DRY-RUN validation gates must pass before LIVE trading is allowed.
"""

from typing import List
import os

DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── CAPITAL AND RISK MANAGEMENT ───────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.05
MIN_POSITION_USD: float = 2.0
BASE_POSITION_USD: float = 2.5
MAX_ACTIVE_POSITIONS: int = 5
MAX_OPEN_PER_CYCLE: int = 1
RESERVED_FAST_SLOTS: int = 2
FAST_SLOT_THRESHOLD_HOURS: float = 8.0
MAX_POSITIONS_PER_CITY: int = 2
MAX_DRAWDOWN_PCT: float = 0.30
STOP_LOSS_PCT: float = 0.15
MAX_POSITION_USD: float = 5.0
MAX_DAILY_LOSS_PCT: float = 0.15
MAX_DAILY_LOSS_USD: float = 15.0
MAX_TOTAL_EXPOSURE_PCT: float = 0.35

# ── v15: METAR ARBITRAGE STRATEGY ──────────────────────────────
METAR_ARB_ENABLED: bool = True
METAR_ARB_MAX_HOURS: float = 12.0
METAR_ARB_MIN_HOURS: float = 1.0
METAR_ARB_MIN_ASK: float = 0.05
METAR_ARB_MAX_ASK: float = 0.70
METAR_ARB_MIN_EDGE: float = 0.04
METAR_ARB_MIN_PROB: float = 0.45
METAR_ARB_MIN_PROB_RANGE: float = 0.20
METAR_ARB_REQUIRE_METAR: bool = False
METAR_ARB_REQUIRE_OBSERVED: bool = True
METAR_ARB_KINDS_ONLY: List[str] = ["above", "below", "range", "categorical"]
METAR_ARB_MAX_DIST_C: float = 5.0
METAR_ARB_TEMP_CONFIRM_C: float = 0.6

# ── LEGACY GRID (DISABLED — replaced by METAR arb) ────────────
ENABLE_EXTREME_TAIL_YES: bool = False
ENABLE_ADJACENT_GRID: bool = False
SNIPER_GRID_DISTANCE_C: float = 3.0
SNIPER_GRID_DISTANCE_F: float = 5.4
SNIPER_GRID_MIN_EDGE: float = 0.08
SNIPER_GRID_MIN_EDGE_NORMAL: float = 0.08
SNIPER_GRID_MIN_PROB: float = 0.55
SNIPER_GRID_MAX_ASK: float = 0.70
SNIPER_GRID_MIN_ASK: float = 0.15
SNIPER_GRID_SIZE_USD: float = 2.5
SNIPER_GRID_MAX_MARKETS_PER_CITY: int = 2
GRID_FORECAST_STEP_C: float = 1.0
GRID_FORECAST_SPAN_C: float = 1.5
EXTREME_TAIL_MAX_ASK_YES: float = 0.70
EXTREME_TAIL_MIN_ASK_YES: float = 0.15
EXTREME_TAIL_MAX_SIZE_USD: float = 2.0
EXTREME_TAIL_MIN_EDGE_YES: float = 0.08
ADJACENT_GRID_SIZE_USD: float = 1.5
ADJACENT_GRID_MIN_EDGE: float = 0.08
ADJACENT_GRID_MIN_PROB: float = 0.55
ADJACENT_GRID_MAX_ASK: float = 0.70

# ── STANDARD EDGE ─────────────────────────────────────────────
MIN_EDGE_ENTRY: float = 0.08
MIN_EDGE_HOLD: float = 0.03
MIN_PROB_ENTRY: float = 0.55

# ── MARKETS ТА ФІЛЬТРИ ────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 12
MIN_RESOLUTION_HOURS: float = 1.0
MIN_MARKET_VOLUME_USD: float = 500.0
SCAN_INTERVAL_SEC: int = 300
OSINT_SCAN_INTERVAL_SEC: int = 300
MAX_VOL_NO_TRADE: float = 0.60
TARGET_PORTFOLIO_VOL: float = 0.10

# ── COMPOUNDING ────────────────────────────────────────────────
ENABLE_COMPOUND: bool = True
COMPOUND_RISK_PCT: float = 0.03
USE_KELLY: bool = True
KELLY_SCALE: float = 0.25
KELLY_MAX_POSITION_USD: float = 5.0
KELLY_PROB_CAP: float = 0.70

# ── DRY-RUN VALIDATION GATES BEFORE LIVE ───────────────────────
VALIDATION_REQUIRED_BEFORE_LIVE: bool = True
VALIDATION_MIN_RESOLVED_TRADES: int = 30
VALIDATION_MIN_DRY_RUN_HOURS: int = 168
VALIDATION_MIN_WIN_RATE: float = 0.45
VALIDATION_MIN_ROI: float = 0.00
VALIDATION_MIN_EQUITY: float = 0.00

# ── ТЕХНІЧНЕ ТА ШЛЯХИ ────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"
MAX_USDC_APPROVAL: float = 1000.0

# ── EMAIL АЛЕРТИ ──────────────────────────────────────────────
EMAIL_ENABLED: bool = False
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_RECIPIENT: str = os.getenv("EMAIL_RECIPIENT", "")

# ── WHITELIST МІСТ (тільки з METAR/ICAO) ──────────────────────
CITY_WHITELIST: List[str] = [
    "NYC", "New York", "Chicago", "Los Angeles",
    "Miami", "Dallas", "Seattle", "Denver", "Atlanta",
    "Boston", "Houston", "London", "Paris", "Berlin",
    "Munich", "Tokyo", "Seoul", "Busan",
    "Buenos Aires", "Sao Paulo", "Cape Town", "Sydney",
    "Lucknow",
]

KNOWN_WHALE_WALLETS: List[str] = []
EXTREME_TAIL_CITIES: List[str] = CITY_WHITELIST

# ── СУМІСНІСТЬ ────────────────────────────────────────────────
MIN_DATA_POINTS_FALLBACK: int = 5
WHALE_THRESHOLD_USD: float = 2000.0

# ── PROBABILITY CALIBRATION (conservative for above/below) ────
PROBABILITY_CALIBRATION_ENABLED: bool = True
PROB_THRESHOLD_CALIBRATION_SCALE: float = 0.95
PROB_EXACT_CALIBRATION_SCALE: float = 0.80
PROB_RANGE_CALIBRATION_SCALE: float = 0.80
PROB_DISTANCE_SCALE_C: float = 3.0
PROB_DISTANCE_SCALE_F: float = 4.5
PROB_DISTANCE_POWER: float = 0.50
PROB_CONFIDENCE_WEIGHT: float = 0.15
PROB_TIME_DECAY_SHORT: float = 1.00
PROB_TIME_DECAY_MID: float = 0.95
PROB_TIME_DECAY_LONG: float = 0.90

PROB_CAP_ABOVE_SHORT: float = 0.85
PROB_CAP_ABOVE_MID: float = 0.75
PROB_CAP_ABOVE_LONG: float = 0.65

PROB_CAP_EXACT_SHORT: float = 0.70
PROB_CAP_EXACT_MID: float = 0.58
PROB_CAP_EXACT_LONG: float = 0.48

MAX_EDGE_CAP: float = 0.50

# ── v15: ENHANCED MARKET ANCHORING ─────────────────────────────
MARKET_ANCHOR_WEIGHT: float = 0.20
MARKET_ANCHOR_THRESHOLD: float = 0.50

# ── Self-calibrating sigma ────────────────────────────────────
SIGMA_CAL_ENABLED: bool = True
SIGMA_CAL_MIN_SAMPLES: int = 5
SIGMA_CAL_MAX_SAMPLES: int = 50
SIGMA_CAL_BLEND_WEIGHT: float = 0.6

# ── Trailing stop (tighter for arb trades) ────────────────────
TRAILING_STOP_ENABLED: bool = True
TRAILING_STOP_ACTIVATION_PCT: float = 0.15

# ── Forecast-shift close ──────────────────────────────────────
FORECAST_SHIFT_CLOSE_ENABLED: bool = True
FORECAST_SHIFT_CLOSE_C: float = 2.5

# ── Dynamic take-profit ────────────────────────────────────────
DYNAMIC_TAKE_PROFIT_ENABLED: bool = True
DTP_HOLD_HOURS_MID: int = 6
DTP_HOLD_HOURS_LONG: int = 12
DTP_PRICE_MID: float = 0.85
DTP_PRICE_LONG: float = 0.80
