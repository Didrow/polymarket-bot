"""
config.py — Polymarket Weather Bot (CALIBRATED SNIPER GRID v10)

Conservative strategy:
- Raw ensemble probabilities are intentionally discounted and calibrated.
- BUY_YES only.
- Trade a limited grid around the forecast peak, not unlimited cheap tails.
- DRY-RUN validation gates must pass before LIVE trading is allowed.
"""

from typing import List
import os

DRY_RUN: bool = True                    # 🧪 Тестовий режим (реальна торгівля — False)
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── CAPITAL AND RISK MANAGEMENT ───────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.04
MIN_POSITION_USD: float = 2.0
BASE_POSITION_USD: float = 3.0
MAX_ACTIVE_POSITIONS: int = 8
RESERVED_FAST_SLOTS: int = 0
FAST_SLOT_THRESHOLD_HOURS: float = 6.0
MAX_POSITIONS_PER_CITY: int = 5
MAX_DRAWDOWN_PCT: float = 0.35
STOP_LOSS_PCT: float = 0.99
MAX_POSITION_USD: float = 5.0
MAX_DAILY_LOSS_PCT: float = 0.20        # LIVE grid buffer: не зупиняти при нормальних unrealized grid swings
MAX_DAILY_LOSS_USD: float = 20.0
MAX_TOTAL_EXPOSURE_PCT: float = 0.35

# ── CALIBRATED SNIPER GRID ─────────────────────────────────────
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.75
EXTREME_TAIL_MIN_ASK_YES: float = 0.03
EXTREME_TAIL_MAX_SIZE_USD: float = 3.0
EXTREME_TAIL_MIN_EDGE_YES: float = 0.03

ENABLE_ADJACENT_GRID: bool = True
SNIPER_GRID_DISTANCE_C: float = 3.5
SNIPER_GRID_DISTANCE_F: float = 5.0
SNIPER_GRID_MIN_EDGE: float = 0.015
SNIPER_GRID_MIN_PROB: float = 0.05
SNIPER_GRID_MAX_ASK: float = 0.75
SNIPER_GRID_MIN_ASK: float = 0.01
SNIPER_GRID_SIZE_USD: float = 2.0
SNIPER_GRID_MAX_MARKETS_PER_CITY: int = 5
GRID_FORECAST_STEP_C: float = 0.1
GRID_FORECAST_SPAN_C: float = 0.2

ADJACENT_GRID_SIZE_USD: float = 2.0
ADJACENT_GRID_MIN_EDGE: float = 0.015
ADJACENT_GRID_MIN_PROB: float = 0.05
ADJACENT_GRID_MAX_ASK: float = 0.75

# ── STANDARD EDGE (for calibrated BUY_YES) ─────────────────────
MIN_EDGE_ENTRY: float = 0.015
MIN_EDGE_HOLD: float = 0.015
MIN_PROB_ENTRY: float = 0.05

# ── MARKETS ТА ФІЛЬТРИ ────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 48          # Зменшено для більшої точності прогнозу
MIN_RESOLUTION_HOURS: float = 0.5       # Мінімум годин до резолву (0.5 = 30хв для DRY-RUN, у бойовому 1.5)
MIN_MARKET_VOLUME_USD: float = 500.0
SCAN_INTERVAL_SEC: int = 600            # Сканування кожні 10 хвилин
OSINT_SCAN_INTERVAL_SEC: int = 300
MAX_VOL_NO_TRADE: float = 0.60
TARGET_PORTFOLIO_VOL: float = 0.15

# ── COMPOUNDING (re-investing) ────────────────────────────────
ENABLE_COMPOUND: bool = True
COMPOUND_RISK_PCT: float = 0.04
USE_KELLY: bool = False
KELLY_SCALE: float = 0.25
KELLY_MAX_POSITION_USD: float = 5.0

# ── DRY-RUN VALIDATION GATES BEFORE LIVE ───────────────────────
VALIDATION_REQUIRED_BEFORE_LIVE: bool = True
VALIDATION_MIN_RESOLVED_TRADES: int = 30
VALIDATION_MIN_DRY_RUN_HOURS: int = 168
VALIDATION_MIN_WIN_RATE: float = 0.50
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

# ── WHITELIST МІСТ ────────────────────────────────────────────
CITY_WHITELIST: List[str] = [
    "London", "Paris", "NYC", "Chicago", "Los Angeles",
    "Tokyo", "Seoul", "Buenos Aires",
    "Busan", "Lucknow", "Cape Town",
    "Miami", "Dallas", "Seattle", "Berlin", "Sydney",
    "Sao Paulo", "Munich",
]

KNOWN_WHALE_WALLETS: List[str] = []
EXTREME_TAIL_CITIES: List[str] = CITY_WHITELIST

# ── СУМІСНІСТЬ ────────────────────────────────────────────────
MIN_DATA_POINTS_FALLBACK: int = 5
WHALE_THRESHOLD_USD: float = 2000.0

# ── PROBABILITY CAPS AND CALIBRATION ──────────────────────────
PROBABILITY_CALIBRATION_ENABLED: bool = True
PROB_THRESHOLD_CALIBRATION_SCALE: float = 0.85
PROB_EXACT_CALIBRATION_SCALE: float = 0.85
PROB_RANGE_CALIBRATION_SCALE: float = 0.85
PROB_DISTANCE_SCALE_C: float = 2.0
PROB_DISTANCE_SCALE_F: float = 3.5
PROB_DISTANCE_POWER: float = 0.5
PROB_CONFIDENCE_WEIGHT: float = 0.25

# prob_above_temp_c / prob_below_temp_c (relatively reliable)
PROB_CAP_ABOVE_SHORT: float = 0.75
PROB_CAP_ABOVE_MID:   float = 0.68
PROB_CAP_ABOVE_LONG:  float = 0.60

# prob_exact_temp_c / range / categorical — main calibration target
PROB_CAP_EXACT_SHORT: float = 0.40
PROB_CAP_EXACT_MID:   float = 0.32
PROB_CAP_EXACT_LONG:  float = 0.25

# Maximum edge (prevents phantom edge > 35%)
MAX_EDGE_CAP: float = 0.35
