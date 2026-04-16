"""
config.py — Polymarket Weather Bot 2026 (адаптовано під @coldmath)
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
COLDMATH_MODE: bool = True  # ← Увімкнути стиль @coldmath (NO @93-99¢)

# ─────────────────────────────────────────────
# POLYMARKET ENDPOINTS
# ─────────────────────────────────────────────
CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
POLY_WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CHAIN_ID: int = 137

# ─────────────────────────────────────────────
# МІСТА (розширено під @coldmath)
# ─────────────────────────────────────────────
TARGET_CITIES: List[str] = [
    # США
    "NYC", "New York", "Chicago", "Los Angeles", "San Francisco",
    "Miami", "Dallas", "Houston", "Seattle", "Atlanta", "Boston",
    "Denver", "Phoenix", "Las Vegas", "Austin", "Orlando", "Nashville",
    # Європа
    "London", "Paris", "Berlin", "Madrid", "Rome", "Amsterdam",
    "Istanbul", "Moscow", "Vienna", "Prague", "Warsaw", "Helsinki",
    "Edinburgh", "Dublin", "Brussels",
    # Азія
    "Tokyo", "Seoul", "Singapore", "Hong Kong", "Beijing", "Shanghai",
    "Bangkok", "Taipei", "Dubai", "Mumbai", "Delhi", "Kuala Lumpur",
    "Jakarta", "Osaka", "Busan", "Chengdu", "Shenzhen", "Chongqing",
    "Wuhan", "Jeddah", "Karachi", "Lucknow",
    # Інші
    "Sydney", "Melbourne", "Toronto", "Vancouver", "Montreal",
    "Wellington", "Auckland", "Cape Town", "Lagos", "Cairo",
    "Buenos Aires", "Ankara",
]

# ─────────────────────────────────────────────
# EXTREME TAIL YES (ваш оригінальний mahera777 стиль)
# ─────────────────────────────────────────────
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05
EXTREME_TAIL_MAX_SIZE_USD: float = 1.0
EXTREME_TAIL_MIN_EDGE_YES: float = 0.04

# ─────────────────────────────────────────────
# COLDMATH TAIL NO (новий режим @coldmath)
# ─────────────────────────────────────────────
ENABLE_COLDMATH_TAIL_NO: bool = True
COLDMATH_MIN_ASK_NO: float = 0.93      # Купуємо NO тільки від 93¢
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.035     # Мінімальний edge для NO
COLDMATH_MAX_SIZE_USD: float = 15.0     # Більші позиції (як у coldmath)

# ─────────────────────────────────────────────
# EDGE (оновлено)
# ─────────────────────────────────────────────
MIN_EDGE_ENTRY: float = 0.06
MIN_EDGE_HOLD: float = 0.05
MAX_EDGE_CAP: float = 0.60

# ─────────────────────────────────────────────
# RISK MANAGEMENT (під coldmath — більші, але контрольовані позиції)
# ─────────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.025        # 2.5% (coldmath любить більші)
MAX_ACTIVE_POSITIONS: int = 8
MAX_DRAWDOWN_PCT: float = 0.20
STOP_LOSS_PCT: float = 0.13
CONFIRM_TRADE_ABOVE_USD: float = 20.0

# ─────────────────────────────────────────────
# VOLATILITY + LADDER (залишаємо для market-making)
# ─────────────────────────────────────────────
TARGET_PORTFOLIO_VOL: float = 0.12
LAMBDA_EWMA: float = 0.94
MM_MODE: bool = True
LADDER_LEVELS: int = 8
POST_ONLY: bool = True

# ─────────────────────────────────────────────
# WEATHER API + SCAN
# ─────────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 72
MIN_MARKET_VOLUME_USD: float = 1000.0   # Нижче — щоб ловити нові ринки
SCAN_INTERVAL_SEC: int = 90             # Швидше для sniper
OSINT_SCAN_INTERVAL_SEC: int = 300

# EMAIL (залишаємо)
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
