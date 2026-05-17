"""
config.py — Polymarket Weather Bot (GRID / YES LADDERING EDITION)

Адаптовано під агресивну стратегію "fridius2":
- Повна відмова від купівлі NO (не збираємо копійки перед катком).
- Агресивна скупка дешевих YES (до 12 центів) з високим Expected Value (EV).
- Розкидування "сітки" на сусідні температури (хеджування похибки синоптиків).
"""

from typing import List
import os

DRY_RUN: bool = True                    # 🧪 Завжди починай з True для тестування
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── КАПІТАЛ ТА РИЗИК-МЕНЕДЖМЕНТ ──────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.05          # Максимум 5% ($5) на ОДИН контракт
MIN_POSITION_USD: float = 2.0           # Мінімальний розмір ставки
BASE_POSITION_USD: float = 3.0          # Базовий розмір ставки
MAX_ACTIVE_POSITIONS: int = 15          # ЗБІЛЬШЕНО: для "сітки" потрібно багато одночасних ордерів
MAX_DRAWDOWN_PCT: float = 0.35          # 35% просадки дозволено (високоризикова стратегія = сильніші гойдалки)
STOP_LOSS_PCT: float = 0.99             # Майже вимикаємо стоп-лоси для дешевих YES (чекаємо фінального resolution)
MAX_POSITION_USD: float = 5.0           # Абсолютний ліміт позиції в $ (захист від математичних помилок)

# ── 🛑 СТРАТЕГІЯ "ПАРОВИЙ КАТОК" (NO) ВИМКНЕНА ───────────────
ENABLE_COLDMATH_TAIL_NO: bool = False   # БІЛЬШЕ НЕ РИЗИКУЄМО $95 ЗАРАДИ $5
COLDMATH_MIN_ASK_NO: float = 0.95
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.12
COLDMATH_MAX_SIZE_USD: float = 0.0

# ── 🎣 СТРАТЕГІЯ "РЯТУВАЛЬНА СІТКА" (GRID YES) ───────────────
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.12  # Купуємо YES, якщо він коштує 12¢ або дешевше
EXTREME_TAIL_MAX_SIZE_USD: float = 4.0  # $2-$4 на кожен тікет у "сітці"
EXTREME_TAIL_MIN_EDGE_YES: float = 0.08 # 8% мінімум: 4% давало занадто багато сміттєвих угод

# ── СТАНДАРТНИЙ EDGE (Для Sniper YES угод) ───────────────────
MIN_EDGE_ENTRY: float = 0.08            # 8% мінімум для стандартних YES угод (якщо ціна вище 12¢)
MIN_EDGE_HOLD: float = 0.02             # Поріг для утримання (для опціонів менш актуально)

# ── MARKETS ТА ФІЛЬТРИ ────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 72         
MIN_MARKET_VOLUME_USD: float = 1000.0   # Знижено поріг, щоб ловити більше дешевих ринків для сітки
SCAN_INTERVAL_SEC: int = 600            # Сканування кожні 10 хвилини
OSINT_SCAN_INTERVAL_SEC: int = 300
MAX_VOL_NO_TRADE: float = 0.60
TARGET_PORTFOLIO_VOL: float = 0.15

# ── ТЕХНІЧНЕ ТА ШЛЯХИ ────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"
MAX_USDC_APPROVAL: float = 1000.0       # Для revoke.cash

# ── EMAIL АЛЕРТИ ──────────────────────────────────────────────
EMAIL_ENABLED: bool = False
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_RECIPIENT: str = os.getenv("EMAIL_RECIPIENT", "")

# ── WHITELIST МІСТ (Тільки якісні прогнози для Ансамблю) ──────
CITY_WHITELIST: List[str] = [
    "London", "Paris", "NYC", "Chicago",
    "Tokyo", "Seoul", "Buenos Aires",
    "Busan", "Lucknow", "Cape Town",
    "Miami", "Dallas", "Seattle", "Berlin", "Sydney"
]

KNOWN_WHALE_WALLETS: List[str] = []
EXTREME_TAIL_CITIES: List[str] = CITY_WHITELIST

# ── СУМІСНІСТЬ (Для osint_module.py та trader.py) ────────────
MIN_DATA_POINTS_FALLBACK: int = 5      
WHALE_THRESHOLD_USD: float = 2000.0    # Відстежувати китів з угодами від $2000

# ── JSONBIN.IO (Хмарне збереження стану) ──────────────────────
JSONBIN_KEY:    str = os.getenv("JSONBIN_KEY", "")     # X-Master-Key
JSONBIN_BIN_ID: str = os.getenv("JSONBIN_BIN_ID", "")  # ID bin-а
