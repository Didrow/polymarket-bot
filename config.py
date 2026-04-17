"""
config.py — Polymarket Weather Bot 2026 (жорстка coldmath версія)
"""

from typing import List
import os

DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

TARGET_CITIES: List[str] = [
    "NYC", "New York", "Chicago", "Los Angeles", "San Francisco", "Miami",
    "London", "Paris", "Berlin", "Tokyo"
]

# ── COLDMATH-STYLE ──
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05
EXTREME_TAIL_MAX_SIZE_USD: float = 3.0
EXTREME_TAIL_MIN_EDGE_YES: float = 0.18     # сильно піднято

ENABLE_COLDMATH_TAIL_NO: bool = True
COLDMATH_MIN_ASK_NO: float = 0.93
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.05
COLDMATH_MAX_SIZE_USD: float = 10.0

MIN_EDGE_ENTRY: float = 0.12
MIN_EDGE_HOLD: float = 0.08
MIN_CONFIDENCE: float = 0.82

INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.02
MAX_ACTIVE_POSITIONS: int = 5
MAX_DRAWDOWN_PCT: float = 0.30
STOP_LOSS_PCT: float = 0.18
MIN_POSITION_USD: float = 3.0

TARGET_PORTFOLIO_VOL: float = 0.10
MAX_VOL_NO_TRADE: float = 0.45
BASE_POSITION_USD: float = 5.0
MIN_DATA_POINTS_FALLBACK: int = 5

MAX_RESOLUTION_HOURS: int = 72
MIN_MARKET_VOLUME_USD: float = 1500.0   # підвищили, щоб менше шуму
SCAN_INTERVAL_SEC: int = 120

DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"

MAX_USDC_APPROVAL: float = 1000.0
EMAIL_ENABLED: bool = False

EXTREME_TAIL_CITIES: List[str] = ["NYC", "New York", "London", "Paris", "Berlin", "Tokyo"]
KNOWN_WHALE_WALLETS: List[str] = []
