"""
config.py — Polymarket Weather Bot 2026 (coldmath-style v9 — виправлена)
"""

from typing import List
import os

DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── КАПІТАЛ І ПОЗИЦІЇ ───────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.05
MIN_POSITION_USD: float = 2.0
BASE_POSITION_USD: float = 3.0
MAX_POSITION_USD: float = 12.0          # додано
MAX_ACTIVE_POSITIONS: int = 5
MAX_DRAWDOWN_PCT: float = 0.25
STOP_LOSS_PCT: float = 0.15

# ── COLDMATH TAIL NO ────────────────────────────────────────
ENABLE_COLDMATH_TAIL_NO: bool = True
COLDMATH_MIN_ASK_NO: float = 0.93
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.06
COLDMATH_MAX_SIZE_USD: float = 8.0

# ── EXTREME TAIL YES ────────────────────────────────────────
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05
EXTREME_TAIL_MAX_SIZE_USD: float = 4.0
EXTREME_TAIL_MIN_EDGE_YES: float = 0.35   # підвищено для якості

# ── СТАНДАРТНИЙ EDGE ────────────────────────────────────────
MIN_EDGE_ENTRY: float = 0.18
MIN_EDGE_HOLD: float = 0.10

# ── MARKETS ─────────────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 72
MIN_MARKET_VOLUME_USD: float = 3000.0     # оптимально
SCAN_INTERVAL_SEC: int = 150
OSINT_SCAN_INTERVAL_SEC: int = 300

# ── ТЕХНІЧНЕ ────────────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"
MAX_USDC_APPROVAL: float = 1000.0
TARGET_PORTFOLIO_VOL: float = 0.10
MAX_VOL_NO_TRADE: float = 0.45
MIN_DATA_POINTS_FALLBACK: int = 5         # додано — фікс помилки
WHALE_THRESHOLD_USD: float = 500.0        # додано

# Email (якщо потрібно)
EMAIL_ENABLED: bool = False
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_RECIPIENT: str = os.getenv("EMAIL_RECIPIENT", "")

# ── WHITELIST МІСТ ──────────────────────────────────────────
CITY_WHITELIST: List[str] = [
    "London", "Paris", "Berlin", "NYC", "Chicago", "Tokyo", "Seoul",
    "Singapore", "Sydney", "Toronto", "Amsterdam", "Madrid", "Rome",
    "Dubai", "Istanbul", "Bangkok", "Buenos Aires", "Cape Town"
]

KNOWN_WHALE_WALLETS: List[str] = []
