"""
config.py — Polymarket Weather Bot 2026 (coldmath v7 — ultra conservative)
"""

from typing import List

DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

TARGET_CITIES: List[str] = ["London", "Paris", "NYC", "New York", "Chicago", "San Francisco"]

# ── ULTRA COLDMATH ──
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05
EXTREME_TAIL_MAX_SIZE_USD: float = 3.0          # зменшено
EXTREME_TAIL_MIN_EDGE_YES: float = 0.35         # сильно піднято!

ENABLE_COLDMATH_TAIL_NO: bool = True
COLDMATH_MIN_ASK_NO: float = 0.93
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.08
COLDMATH_MAX_SIZE_USD: float = 10.0

MIN_EDGE_ENTRY: float = 0.22                    # піднято
MIN_EDGE_HOLD: float = 0.15
MIN_CONFIDENCE: float = 0.90                    # піднято

INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.02                  # 2% максимум
MAX_ACTIVE_POSITIONS: int = 3
MAX_DRAWDOWN_PCT: float = 0.25
STOP_LOSS_PCT: float = 0.15
MIN_POSITION_USD: float = 3.0

TARGET_PORTFOLIO_VOL: float = 0.08
MAX_VOL_NO_TRADE: float = 0.40
BASE_POSITION_USD: float = 4.0

MAX_RESOLUTION_HOURS: int = 72
MIN_MARKET_VOLUME_USD: float = 4000.0           # вище
SCAN_INTERVAL_SEC: int = 180
OSINT_SCAN_INTERVAL_SEC: int = 360

DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"

MAX_USDC_APPROVAL: float = 500.0
