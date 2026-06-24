"""
config.py — Polymarket Weather Bot (PROFITABLE SNIPER GRID v15 — neobrother-style)

Strategy: sniper PEAK buckets near forecast (25-58¢) + tight grid wings
- BUY_YES only, focus on PEAK buckets NEAR the forecast (25-58¢)
- Peak bucket (dist<0.8°C) gets largest size; wings (dist<1.6°C) get smaller size
- No double-calibration: prob_exact blend already calibrates inside estimate_market_probability
- Quarter-Kelly position sizing for optimal bankroll growth
- v15 fixes: peak probability un-crushed (43%→43%, not 43%→23%), resolution actually fires
- v14 retained: self-calibrating sigma, trailing stop, forecast-shift close, dynamic TP
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
MAX_POSITION_PCT: float = 0.05          # v15: 0.04→0.05 (піки заслуговують більшої позиції)
MIN_POSITION_USD: float = 1.0
BASE_POSITION_USD: float = 1.5
MAX_ACTIVE_POSITIONS: int = 12          # повернуто з оригіналу
MAX_OPEN_PER_CYCLE: int = 4
RESERVED_FAST_SLOTS: int = 1
FAST_SLOT_THRESHOLD_HOURS: float = 6.0
MAX_POSITIONS_PER_CITY: int = 5         # сітка до 5 бакетів на місто
MAX_DRAWDOWN_PCT: float = 0.35
STOP_LOSS_PCT: float = 0.15             # v15: 0.13→0.15 (дати пікам дихати)
MAX_POSITION_USD: float = 5.0           # v15: 4.0→5.0 (Kelly на пікових бакетах)
MAX_DAILY_LOSS_PCT: float = 0.20
MAX_DAILY_LOSS_USD: float = 20.0
MAX_TOTAL_EXPOSURE_PCT: float = 0.55    # повернуто з оригіналу

# ── SNIPER PEAK GRID (v15: neobrother-style) ──────────────────
# Фокус на ПІКОВИХ бакетах 25-58¢ біля прогнозу (win rate 35-45%),
# НЕ на дешевих хвостах 1-8¢ (win rate 0%, лотерея).
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.58     # v15: 0.55→0.58 (трохи вище для гарячих піків)
EXTREME_TAIL_MIN_ASK_YES: float = 0.25     # v15: 0.08→0.25 (фокус на піки 25-58¢, відсіяти хвости)
EXTREME_TAIL_MAX_SIZE_USD: float = 5.0     # v15: 2.0→5.0 (більше на якісні піки)
EXTREME_TAIL_MIN_EDGE_YES: float = 0.02    # v15: 0.04→0.02 (піки мають малу абс. маржу але високу R:R)

ENABLE_ADJACENT_GRID: bool = True
SNIPER_GRID_DISTANCE_C: float = 1.5        # v15: 2.5→1.5 (сітка ±1.5°C, щільніша)
SNIPER_GRID_DISTANCE_F: float = 2.7        # v15: 4.5→2.7
SNIPER_GRID_MIN_EDGE: float = 0.02         # v15: 0.04→0.02
SNIPER_GRID_MIN_PROB: float = 0.20         # v15: 0.08→0.20 (піки ≥20% реального шансу)
SNIPER_GRID_MAX_ASK: float = 0.58          # v15: 0.60→0.58
SNIPER_GRID_MIN_ASK: float = 0.25          # v15: 0.08→0.25 (синхронно з EXTREME_TAIL_MIN_ASK_YES)
SNIPER_GRID_SIZE_USD: float = 5.0          # v15: 2.0→5.0
SNIPER_GRID_MAX_MARKETS_PER_CITY: int = 5  # сітка 5 бакетів: -2,-1,0,+1,+2
GRID_FORECAST_STEP_C: float = 1.0          # крок сітки 1°C
GRID_FORECAST_SPAN_C: float = 2.0          # половина сітки ±2°C

# Peak/wing диференціація за distance_c (v15)
PEAK_DISTANCE_C: float = 0.8               # dist<0.8°C → пік (size×1.4)
WING_DISTANCE_C: float = 1.6               # dist<1.6°C → крило (size×0.6)

ADJACENT_GRID_SIZE_USD: float = 1.5        # крила сітки — менші ставки
ADJACENT_GRID_MIN_EDGE: float = 0.02       # v15: 0.03→0.02
ADJACENT_GRID_MIN_PROB: float = 0.10       # v15: 0.06→0.10
ADJACENT_GRID_MAX_ASK: float = 0.58        # v15: 0.60→0.58

# ── STANDARD EDGE (for calibrated BUY_YES) ─────────────────────
MIN_EDGE_ENTRY: float = 0.02               # v15: 0.05→0.02 (дати сітці дихати)
MIN_EDGE_HOLD: float = 0.02
MIN_PROB_ENTRY: float = 0.10               # v15: 0.05→0.10

# ── MARKETS ТА ФІЛЬТРИ ────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 48          # Зменшено для більшої точності прогнозу
MIN_RESOLUTION_HOURS: float = 0.5       # Мінімум годин до резолву (0.5 = 30хв для DRY-RUN, у бойовому 1.5)
MIN_MARKET_VOLUME_USD: float = 500.0
SCAN_INTERVAL_SEC: int = 300            # v11: 600→300 (частіше сканувати для сітки)
OSINT_SCAN_INTERVAL_SEC: int = 300
MAX_VOL_NO_TRADE: float = 0.60
TARGET_PORTFOLIO_VOL: float = 0.10

# ── COMPOUNDING (re-investing) ────────────────────────────────
ENABLE_COMPOUND: bool = True
COMPOUND_RISK_PCT: float = 0.03            # v13: 0.02→0.03
USE_KELLY: bool = True                     # v13: False→True (Quarter-Kelly для оптимального розміру)
KELLY_SCALE: float = 0.25                  # Quarter-Kelly
KELLY_MAX_POSITION_USD: float = 5.0        # v13: 5.0 (залишаємо)
KELLY_PROB_CAP: float = 0.70               # v15: 0.60→0.70 (більше місця для Kelly на піках)

# ── DRY-RUN VALIDATION GATES BEFORE LIVE ───────────────────────
VALIDATION_REQUIRED_BEFORE_LIVE: bool = True
VALIDATION_MIN_RESOLVED_TRADES: int = 50
VALIDATION_MIN_DRY_RUN_HOURS: int = 240
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
PROB_THRESHOLD_CALIBRATION_SCALE: float = 0.95
PROB_EXACT_CALIBRATION_SCALE: float = 1.00
PROB_RANGE_CALIBRATION_SCALE: float = 1.00
PROB_DISTANCE_SCALE_C: float = 3.0
PROB_DISTANCE_SCALE_F: float = 4.5
PROB_DISTANCE_POWER: float = 0.5          # v15: 0.3→0.5 (менше роздавлювання крил на dist 1-2°C)
PROB_CONFIDENCE_WEIGHT: float = 0.25
PROB_TIME_DECAY_SHORT: float = 1.00
PROB_TIME_DECAY_MID: float = 0.95
PROB_TIME_DECAY_LONG: float = 0.90

# prob_above_temp_c / prob_below_temp_c (relatively reliable)
PROB_CAP_ABOVE_SHORT: float = 0.80         # v13: 0.75→0.80
PROB_CAP_ABOVE_MID:   float = 0.72         # v13: 0.68→0.72
PROB_CAP_ABOVE_LONG:  float = 0.65         # v13: 0.60→0.65

# prob_exact_temp_c / range / categorical — main calibration target
# v13: піднято caps щоб сітка бакетів поблизу прогнозу мала реалістичні our_prob
# Бакет 17°C при прогнозі 17.0°C: реальна ймовірність ~25-35%, не 11%
PROB_CAP_EXACT_SHORT: float = 0.60         # v13: 0.50→0.60
PROB_CAP_EXACT_MID:   float = 0.48         # v13: 0.38→0.48
PROB_CAP_EXACT_LONG:  float = 0.38         # v13: 0.30→0.38

# Maximum edge (prevents phantom edge > 35%)
MAX_EDGE_CAP: float = 0.35

# ── v15: MARKET ANCHOR ─────────────────────────────────────────
# v14 ввів цю фічу (0.30), але вона роздавлювала our_prob до ціни ринку
# і вбивала edge на пікових бакетах. ВИМКНЕНО в v15 (weight=0.0).
MARKET_ANCHOR_WEIGHT: float = 0.0          # v15: 0.30→0.0 (вбивав edge на піках)
MARKET_ANCHOR_THRESHOLD: float = 0.20

# Self-calibrating sigma (sigma_calibrator.py)
SIGMA_CAL_ENABLED: bool = True
SIGMA_CAL_MIN_SAMPLES: int = 5
SIGMA_CAL_MAX_SAMPLES: int = 50
SIGMA_CAL_BLEND_WEIGHT: float = 0.6

# Trailing stop (breakeven after +20% gain)
TRAILING_STOP_ENABLED: bool = True
TRAILING_STOP_ACTIVATION_PCT: float = 0.20

# Forecast-shift close (close if forecast moved 2+°C away from bucket)
FORECAST_SHIFT_CLOSE_ENABLED: bool = True
FORECAST_SHIFT_CLOSE_C: float = 2.0

# Dynamic take-profit (sell based on hold duration)
DYNAMIC_TAKE_PROFIT_ENABLED: bool = True
DTP_HOLD_HOURS_MID: int = 24
DTP_HOLD_HOURS_LONG: int = 48
DTP_PRICE_MID: float = 0.85
DTP_PRICE_LONG: float = 0.75
