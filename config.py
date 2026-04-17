"""
config.py — Polymarket Weather Bot 2026 (фінальна стабільна версія для @coldmath-style)
"""

from typing import List
import os

# ─────────────────────────────────────────────
# РЕЖИМ РОБОТИ
# ─────────────────────────────────────────────
DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

# ─────────────────────────────────────────────
# POLYMARKET ENDPOINTS
# ─────────────────────────────────────────────
CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ─────────────────────────────────────────────
# МІСТА
# ─────────────────────────────────────────────
TARGET_CITIES: List[str] = [
    "NYC", "New York", "Chicago", "Los Angeles", "San Francisco", "Miami",
    "Dallas", "Houston", "Seattle", "Atlanta", "Boston", "Denver", "Phoenix",
    "Las Vegas", "Austin", "Orlando", "Nashville", "London", "Paris", "Berlin",
    "Tokyo", "Seoul", "Singapore", "Sydney", "Melbourne", "Toronto"
]

# ─────────────────────────────────────────────
# EXTREME TAIL YES + COLDMATH TAIL NO
# ─────────────────────────────────────────────
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05
EXTREME_TAIL_MAX_SIZE_USD: float = 2.0
EXTREME_TAIL_MIN_EDGE_YES: float = 0.12

ENABLE_COLDMATH_TAIL_NO: bool = True
COLDMATH_MIN_ASK_NO: float = 0.93
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.04
COLDMATH_MAX_SIZE_USD: float = 12.0

# ─────────────────────────────────────────────
# EDGE + CONFIDENCE
# ─────────────────────────────────────────────
MIN_EDGE_ENTRY: float = 0.10
MIN_EDGE_HOLD: float = 0.06
MIN_CONFIDENCE: float = 0.78

# ─────────────────────────────────────────────
# RISK MANAGEMENT
# ─────────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.025
MAX_ACTIVE_POSITIONS: int = 6
MAX_DRAWDOWN_PCT: float = 0.25
STOP_LOSS_PCT: float = 0.15
MIN_POSITION_USD: float = 2.0

# ─────────────────────────────────────────────
# VOLATILITY
# ─────────────────────────────────────────────
TARGET_PORTFOLIO_VOL: float = 0.12
MAX_VOL_NO_TRADE: float = 0.40
BASE_POSITION_USD: float = 5.0
MIN_DATA_POINTS_FALLBACK: int = 5

# ─────────────────────────────────────────────
# SCAN + OSINT
# ─────────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 72
MIN_MARKET_VOLUME_USD: float = 800.0
SCAN_INTERVAL_SEC: int = 90
OSINT_SCAN_INTERVAL_SEC: int = 300

# ─────────────────────────────────────────────
# ШЛЯХИ (обов’язково для safeguards.py)
# ─────────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"

# ─────────────────────────────────────────────
# БЕЗПЕКА + EMAIL
# ─────────────────────────────────────────────
MAX_USDC_APPROVAL: float = 1000.0
EMAIL_ENABLED: bool = False
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_RECIPIENT: str = os.getenv("EMAIL_RECIPIENT", "")

# ─────────────────────────────────────────────
# ДОДАТКОВІ
# ─────────────────────────────────────────────
EXTREME_TAIL_CITIES: List[str] = ["NYC", "New York", "London", "Paris", "Berlin", "Tokyo"]
KNOWN_WHALE_WALLETS: List[str] = []
