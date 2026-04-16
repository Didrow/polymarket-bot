"""
config.py — Polymarket Weather Bot 2026 (версія під стиль mahera777)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
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
POLY_WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CHAIN_ID: int = 137

# ─────────────────────────────────────────────
# МІСТА ТА РИНКИ
# ─────────────────────────────────────────────
TARGET_CITIES: List[str] = ["London", "NYC"]

# ─────────────────────────────────────────────
# EXTREME TAIL VALUE (стиль mahera777 — 1–5¢ YES)
# ─────────────────────────────────────────────
ENABLE_EXTREME_TAIL: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05      # Купуємо тільки до 5¢
EXTREME_TAIL_MAX_SIZE_USD: float = 4.0      # Максимум $4 на угоду
EXTREME_TAIL_CITIES: List[str] = ["London", "NYC"]
EXTREME_TAIL_MIN_EDGE: float = 0.04

# ─────────────────────────────────────────────
# EDGE
# ─────────────────────────────────────────────
MIN_EDGE_ENTRY: float = 0.08
MIN_EDGE_HOLD: float = 0.05
MAX_EDGE_CAP: float = 0.60

# ─────────────────────────────────────────────
# RISK MANAGEMENT
# ─────────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.07
MAX_ACTIVE_POSITIONS: int = 5
MAX_DRAWDOWN_PCT: float = 0.20
STOP_LOSS_PCT: float = 0.13
CONFIRM_TRADE_ABOVE_USD: float = 20.0

# ─────────────────────────────────────────────
# VOLATILITY TARGETING + LADDER
# ─────────────────────────────────────────────
TARGET_PORTFOLIO_VOL: float = 0.12
LAMBDA_EWMA: float = 0.94
MAX_VOL_NO_TRADE: float = 0.40
MIN_DATA_POINTS_FALLBACK: int = 5
BASE_POSITION_USD: float = 3.0
MIN_POSITION_USD: float = 1.0
MAX_POSITION_USD: float = 10.0

MM_MODE: bool = True
LADDER_LEVELS: int = 8
LADDER_SPREAD_START: float = 0.02
LADDER_SPREAD_STEP: float = 0.02
LADDER_K_FACTOR: float = 0.5
LADDER_BASE_VOL: float = 2.0
LADDER_REFRESH_SEC: int = 20
POST_ONLY: bool = True
CANCEL_ON_MOVE: float = 0.02
MAX_ORDERS_BATCH: int = 15

# ─────────────────────────────────────────────
# WEATHER API + SCAN
# ─────────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 720
MIN_MARKET_VOLUME_USD: float = 10.0
SCAN_INTERVAL_SEC: int = 120

# EMAIL
EMAIL_ENABLED: bool = True
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_RECIPIENT: str = os.getenv("EMAIL_RECIPIENT", "")

# БЕЗПЕКА
SECURITY_WALLET_CHECK: bool = True
SECURITY_AUDIT_DEPS: bool = True
MAX_USDC_APPROVAL: float = 500.0

# ШЛЯХИ
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
