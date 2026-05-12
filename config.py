"""
config.py — Polymarket Weather Bot 2026 (coldmath v8 — фінальна робоча версія)

ВИПРАВЛЕННЯ:
  - MAX_POSITION_PCT: 0.025 → 0.05 (дозволяє $5 при $100 капіталі)
  - MIN_POSITION_USD: 4.0 → 2.0
  - BASE_POSITION_USD: 5.0 → 3.0
  - MIN_MARKET_VOLUME_USD: 5000 для точних сигналів
  - MAX_ACTIVE_POSITIONS: 5 → обмежуємо кількість відкритих угод
  - АДАПТАЦІЯ СТРАТЕГІЇ (v13): Знижено пороги edge для збільшення вибірки угод.
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
# ВИПРАВЛЕННЯ ГОЛОВНОЇ ПОМИЛКИ: 5% від $100 = $5 → дозволяє угоди
MAX_POSITION_PCT: float = 0.05          # було 0.025 → safeguards блокував $4 на $100
MIN_POSITION_USD: float = 2.0
BASE_POSITION_USD: float = 3.0
MAX_ACTIVE_POSITIONS: int = 6           # не більше 6 відкритих позицій
MAX_DRAWDOWN_PCT: float = 0.25
STOP_LOSS_PCT: float = 0.15

# ── COLDMATH TAIL NO (BUY NO @ 93-99¢) ───────────────────────
ENABLE_COLDMATH_TAIL_NO: bool = True
COLDMATH_MIN_ASK_NO: float = 0.95      # тільки NO ≥95¢
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.12     # БУЛО 0.22 → ТЕПЕР 0.12 (12% це вже чудовий край)
COLDMATH_MAX_SIZE_USD: float = 2.0     # макс $2.0 на NO tail угоду

# ── EXTREME TAIL YES (BUY YES @ 1-5¢) ────────────────────────
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05  # YES price ≤ 5¢
EXTREME_TAIL_MAX_SIZE_USD: float = 2.0  # $2 на угоду
EXTREME_TAIL_MIN_EDGE_YES: float = 0.35 # БУЛО 0.65 → ТЕПЕР 0.35

# ── СТАНДАРТНИЙ EDGE ──────────────────────────────────────────
MIN_EDGE_ENTRY: float = 0.10           # БУЛО 0.25 → ТЕПЕР 0.10
MIN_EDGE_HOLD: float = 0.05            # БУЛО 0.10 → ТЕПЕР 0.05

# ── MARKETS ───────────────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 72         
# Тільки ринки з достатнім об'ємом — менше шуму
MIN_MARKET_VOLUME_USD: float = 300.0   # для DRY_RUN тестування
SCAN_INTERVAL_SEC: int = 180           
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

# ── WHITELIST МІСТ (тільки якісні прогнози) ──────────────────
# Для categorical ринків бот використовує prob_exact_temp_c
# Тільки міста де прогноз точний і ринок ліквідний
# Топ міст з найточнішими прогнозами і найбільшими обсягами (розширено)
CITY_WHITELIST: List[str] = [
    "London", "Paris", "NYC", "Chicago",
    "Tokyo", "Seoul", "Buenos Aires",
    "Busan", "Lucknow", "Cape Town",
    "Miami", "Dallas", "Seattle", "Berlin", "Sydney"
]

KNOWN_WHALE_WALLETS: List[str] =[]
EXTREME_TAIL_CITIES: List[str] = CITY_WHITELIST

# ── АТРИБУТИ ДЛЯ СУМІСНОСТІ (використовуються trader.py / osint_module.py) ──
MIN_DATA_POINTS_FALLBACK: int = 5      # trader.py: мін. точок для volatility calc
MAX_POSITION_USD: float = 10.0         # trader.py: абсолютний макс. розмір позиції
WHALE_THRESHOLD_USD: float = 5000.0    # osint_module.py: поріг whale-угоди

# ── JSONBIN.IO (хмарне збереження стану) ─────────────────────
JSONBIN_KEY:    str = os.getenv("JSONBIN_KEY", "")     # X-Master-Key
JSONBIN_BIN_ID: str = os.getenv("JSONBIN_BIN_ID", "")  # ID bin-а
