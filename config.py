"""
config.py — Weather Prediction Bot v20 (CLEAN ENSEMBLE STRATEGY)

Стратегія: чиста ensemble-прогнозна модель без METAR arb.
- Використовуємо тільки Open-Meteo ENSEMBLE (31 member GFS)
- Купуємо YES коли ensemble ймовірність > ринкова ціна + edge
- Купуємо NO  коли ринкова ціна > ensemble ймовірність + edge
- Без METAR (aviationweather.gov timeout на Render)
- Без range/categorical ринків (тільки above/below)
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
MAX_POSITION_PCT: float = 0.04
MIN_POSITION_USD: float = 2.0
MAX_POSITION_USD: float = 5.0
MAX_ACTIVE_POSITIONS: int = 3
MAX_OPEN_PER_CYCLE: int = 1
MAX_POSITIONS_PER_CITY: int = 1
STOP_LOSS_PCT: float = 0.30
STOP_LOSS_MIN_HOLD_HOURS: float = 0.5
MAX_TOTAL_EXPOSURE_PCT: float = 0.30
MAX_DAILY_LOSS_PCT: float = 0.15

# ── STRATEGY: ENSEMBLE PREDICTION ──────────────────────────
# Тільки above/below ринки
KINDS_ONLY: List[str] = ["above", "below"]

# Мінімальний edge для входу
MIN_EDGE_YES: float = 0.20   # buy YES: our_prob > market + 20%
MIN_EDGE_NO: float = 0.20    # buy NO:  market > our_prob + 20%
MIN_PROB_ENTRY: float = 0.10

MAX_RESOLUTION_HOURS: int = 48
MIN_RESOLUTION_HOURS: float = 0.5
MIN_MARKET_VOLUME_USD: float = 500.0
SCAN_INTERVAL_SEC: int = 300

# ── PROBABILITY CALIBRATION ────────────────────────────────
# Систематична похибка: модель переоцінює → множимо на bias
PROB_BIAS: float = 0.75  # calibrate: multiply prob by 0.75

# Для above/below:
CAP_SHORT: float = 0.85   # ≤6h
CAP_MID: float = 0.75     # ≤18h
CAP_LONG: float = 0.65    # >18h

MAX_EDGE_CAP: float = 0.50

# ── KELLY ──────────────────────────────────────────────────
USE_KELLY: bool = True
KELLY_SCALE: float = 0.25
KELLY_MAX_POSITION_USD: float = 5.0

# ── MISC ───────────────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"
EMAIL_ENABLED: bool = False

# ── CITIES ─────────────────────────────────────────────────
CITY_WHITELIST: List[str] = [
    "NYC", "New York", "Chicago", "Los Angeles", "Miami", "Dallas",
    "Seattle", "Denver", "Atlanta", "Boston", "Houston", "Austin",
    "San Francisco", "Phoenix", "Las Vegas", "Minneapolis",
    "Portland", "Nashville", "Charlotte", "Orlando",
    "London", "Paris", "Berlin", "Munich", "Amsterdam", "Rome",
    "Madrid", "Dublin", "Warsaw", "Vienna", "Prague", "Moscow", "Ankara",
    "Tokyo", "Seoul", "Busan", "Singapore", "Hong Kong", "Shanghai",
    "Beijing", "Dubai", "Bangkok", "Lucknow", "Chengdu", "Karachi", "Jeddah",
    "Buenos Aires", "Sao Paulo", "Santiago", "Lima",
    "Cape Town", "Lagos",
    "Sydney", "Melbourne", "Brisbane", "Perth", "Auckland", "Wellington",
]

# ── DRY-RUN VALIDATION GATES ───────────────────────────────
VALIDATION_REQUIRED_BEFORE_LIVE: bool = True
VALIDATION_MIN_RESOLVED_TRADES: int = 30
VALIDATION_MIN_DRY_RUN_HOURS: int = 168
VALIDATION_MIN_WIN_RATE: float = 0.40
VALIDATION_MIN_ROI: float = 0.00
VALIDATION_MIN_EQUITY: float = 0.00
