"""
config.py — Polymarket Weather Bot 2026
Оптимізовано на основі реальних даних з логів (v28 + fixes-v3)
"""

from typing import List
import os

DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── КАПІТАЛ ──────────────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.06          # 6% = $6 на угоду при $100
MIN_POSITION_USD: float = 2.0
BASE_POSITION_USD: float = 4.0          # підвищено з 3.0 — більший розмір на якісних сигналах
MAX_POSITION_USD: float = 10.0
MAX_ACTIVE_POSITIONS: int = 8           # підвищено з 6 — бот знаходить 7-8 сигналів/цикл
MAX_DRAWDOWN_PCT: float = 0.20          # знижено з 0.25 — зупинятись раніше при просадці
STOP_LOSS_PCT: float = 0.15

# ── COLDMATH TAIL NO (BUY NO @ 93-99¢) ───────────────────────
ENABLE_COLDMATH_TAIL_NO: bool = True
COLDMATH_MIN_ASK_NO: float = 0.94       # трохи знижено з 0.95 — більше можливостей
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.25      # підвищено — тільки надійні NO сигнали
COLDMATH_MAX_SIZE_USD: float = 3.0      # підвищено з 2.0 — NO tail угоди дуже надійні

# ── EXTREME TAIL YES (BUY YES @ 1-5¢) ────────────────────────
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05
EXTREME_TAIL_MIN_EDGE_YES: float = 0.65
EXTREME_TAIL_MAX_SIZE_USD: float = 2.0

# ── СТАНДАРТНИЙ EDGE ──────────────────────────────────────────
MIN_EDGE_ENTRY: float = 0.35            # підвищено з 0.25 — відсікаємо слабкі сигнали
MIN_EDGE_HOLD: float = 0.10

# ── MARKETS ───────────────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 48          # знижено з 72 — прогноз точніший на 48h
MIN_MARKET_VOLUME_USD: float = 1000.0   # підвищено з 300 — тільки ліквідні ринки
SCAN_INTERVAL_SEC: int = 240            # підвищено з 180 — менше навантаження на Render
OSINT_SCAN_INTERVAL_SEC: int = 300

# ── ТЕХНІЧНЕ ─────────────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"
MAX_USDC_APPROVAL: float = 1000.0
TARGET_PORTFOLIO_VOL: float = 0.10
MAX_VOL_NO_TRADE: float = 0.45

# Email
EMAIL_ENABLED: bool = False
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_RECIPIENT: str = os.getenv("EMAIL_RECIPIENT", "")

# ── WHITELIST МІСТ ────────────────────────────────────────────
# Залишаємо тільки міста з найвищим об'ємом ринків за логами:
# London ($22k), Paris ($18k), NYC, Chicago, Seoul, Tokyo, Buenos Aires, Busan
CITY_WHITELIST: List[str] = [
    "London", "Paris", "NYC", "Chicago",
    "Tokyo", "Seoul", "Buenos Aires", "Busan",
]

KNOWN_WHALE_WALLETS: List[str] = []
EXTREME_TAIL_CITIES: List[str] = CITY_WHITELIST

# ── АТРИБУТИ ДЛЯ СУМІСНОСТІ ───────────────────────────────────
MIN_DATA_POINTS_FALLBACK: int = 5
WHALE_THRESHOLD_USD: float = 5000.0

# ── JSONBIN.IO ────────────────────────────────────────────────
JSONBIN_KEY:    str = os.getenv("JSONBIN_KEY", "")
JSONBIN_BIN_ID: str = os.getenv("JSONBIN_BIN_ID", "")
