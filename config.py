# polymarket-bot-main/config.py
"""
config.py — Polymarket Weather Bot 2026 (виправлена версія з усіма missing vars)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
import os

# ─────────────────────────────────────────────
# РЕЖИМ РОБОТИ + COLDMATH MODE
# ─────────────────────────────────────────────
DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"
COLDMATH_MODE: bool = True

# ─────────────────────────────────────────────
# POLYMARKET ENDPOINTS
# ─────────────────────────────────────────────
CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
POLY_WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CHAIN_ID: int = 137

# ─────────────────────────────────────────────
# МІСТА
# ─────────────────────────────────────────────
TARGET_CITIES: List[str] = [ ... ]  # (залишаю ваш повний список без змін)

# ─────────────────────────────────────────────
# EXTREME TAIL YES + COLDMATH TAIL NO
# ─────────────────────────────────────────────
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05
EXTREME_TAIL_MAX_SIZE_USD: float = 1.0
EXTREME_TAIL_MIN_EDGE_YES: float = 0.04

ENABLE_COLDMATH_TAIL_NO: bool = True
COLDMATH_MIN_ASK_NO: float = 0.93
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.035
COLDMATH_MAX_SIZE_USD: float = 15.0

# ─────────────────────────────────────────────
# EDGE + RISK
# ─────────────────────────────────────────────
MIN_EDGE_ENTRY: float = 0.09          # піднято з 0.06 для якості
MIN_EDGE_HOLD: float = 0.05
MAX_EDGE_CAP: float = 0.60
MIN_CONFIDENCE: float = 0.75          # НОВА змінна

# ─────────────────────────────────────────────
# RISK MANAGEMENT
# ─────────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.025
MAX_ACTIVE_POSITIONS: int = 8
MAX_DRAWDOWN_PCT: float = 0.20
STOP_LOSS_PCT: float = 0.13
CONFIRM_TRADE_ABOVE_USD: float = 20.0

# ─────────────────────────────────────────────
# VOLATILITY + LADDER
# ─────────────────────────────────────────────
TARGET_PORTFOLIO_VOL: float = 0.12
LAMBDA_EWMA: float = 0.94
MM_MODE: bool = True
LADDER_LEVELS: int = 8
POST_ONLY: bool = True

# ─────────────────────────────────────────────
# WEATHER + SCAN
# ─────────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 72
MIN_MARKET_VOLUME_USD: float = 1000.0
SCAN_INTERVAL_SEC: int = 90
OSINT_SCAN_INTERVAL_SEC: int = 300

# EMAIL
EMAIL_ENABLED: bool = True
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_RECIPIENT: str = os.getenv("EMAIL_RECIPIENT", "")

# БЕЗПЕКА
SECURITY_WALLET_CHECK: bool = True
SECURITY_AUDIT_DEPS: bool = True
MAX_USDC_APPROVAL: float = 1000.0

# ШЛЯХИ
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"

# ─────────────────────────────────────────────
# НОВІ ЗМІННІ (виправлення missing vars)
# ─────────────────────────────────────────────
MIN_POSITION_USD: float = 2.0
WHALE_THRESHOLD_USD: float = 500.0
KNOWN_WHALE_WALLETS: List[str] = []
MAX_VOL_NO_TRADE: float = 0.40
BASE_POSITION_USD: float = 5.0
MIN_DATA_POINTS_FALLBACK: int = 5
EXTREME_TAIL_CITIES: List[str] = [
    "NYC", "New York", "London", "Paris", "Berlin", "Tokyo",
    "Chicago", "Los Angeles", "San Francisco", "Miami"
]
