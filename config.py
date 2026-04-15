"""
config.py — Polymarket Weather Bot 2026
Центральна конфігурація для всього стеку.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
import os

# ─────────────────────────────────────────────
# РЕЖИМ РОБОТИ
# ─────────────────────────────────────────────
DRY_RUN: bool = True          # ЗАВЖДИ True на старті! Реальні угоди тільки після тестів
LOG_LEVEL: str = "INFO"       # DEBUG / INFO / WARNING
LOG_FILE: str = "logs/bot.log"

# ─────────────────────────────────────────────
# POLYMARKET ENDPOINTS
# ─────────────────────────────────────────────
CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
POLY_WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CHAIN_ID: int = 137  # Polygon mainnet

# ─────────────────────────────────────────────
# МІСТА ТА РИНКИ
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# МІСТА ТА РИНКИ (автоматичне розширення)
# ─────────────────────────────────────────────
TARGET_CITIES: List[str] = [
    # США (основні)
    "New York", "NYC", "Chicago", "Seattle", "Atlanta", "Dallas", "Miami",
    "Los Angeles", "San Francisco", "Boston", "Houston", "Phoenix", "Denver",
    # Європа
    "London", "Paris", "Berlin", "Madrid", "Rome", "Amsterdam", "Brussels",
    "Vienna", "Prague", "Warsaw", "Budapest", "Moscow", "Kyiv", "Istanbul",
    # Канада та інші
    "Toronto", "Vancouver", "Montreal", "Hong Kong", "Tokyo", "Sydney",
    "Melbourne", "Singapore", "Dubai", "Bangkok", "Mexico City",
    # Додаткові популярні для weather-ринків
    "Las Vegas", "Orlando", "New Orleans", "Philadelphia", "Detroit",
    "Minneapolis", "Salt Lake City", "Portland", "Austin", "Charlotte"
]

WEATHER_KEYWORDS: List[str] = [
    "temperature", "rain", "snow", "precipitation",
    "high temp", "low temp", "above", "below",
    "weather", "degrees", "fahrenheit", "celsius",
    "rainfall", "snowfall", "freeze",
]

MARKET_TAGS: List[str] = ["weather"]

# ─────────────────────────────────────────────
# ПРАВИЛО 72 ГОДИН
# ─────────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 240         # ← було 72, тепер 10 днів (більше ринків)
MIN_MARKET_VOLUME_USD: float = 100.0  # ← було 5000, тепер $1000 (більше ліквідних ринків)

# === EXTREME TAIL STRATEGY (для 1-5¢ YES на London/NYC) ===
ENABLE_EXTREME_TAIL: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.05      # Купуємо тільки якщо YES ≤ 5 центів
EXTREME_TAIL_MAX_SIZE_USD: float = 4.0      # Максимум $4 на одну таку угоду
EXTREME_TAIL_CITIES: List[str] = ["London", "NYC"]   # Тільки ці міста
EXTREME_TAIL_MIN_EDGE: float = 0.04         # Мінімальний edge для активації

# ─────────────────────────────────────────────
# EDGE (перевага)
# ─────────────────────────────────────────────
MIN_EDGE_ENTRY: float = 0.08    # Мінімальний edge для входу (8%)
MIN_EDGE_HOLD: float = 0.05     # Мінімальний edge для утримання позиції (5%)
MAX_EDGE_CAP: float = 0.60      # Edge > 60% — підозра, пропустити

# ─────────────────────────────────────────────
# УПРАВЛІННЯ РИЗИКАМИ
# ─────────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0          # Стартовий капітал ($)
MAX_POSITION_PCT: float = 0.07          # Максимум 7% від капіталу на одну угоду
MAX_ACTIVE_POSITIONS: int = 5           # Максимум активних позицій
MAX_DRAWDOWN_PCT: float = 0.20          # Зупинка при просадці 20%
STOP_LOSS_PCT: float = 0.13             # Стоп-лос 13%
CONFIRM_TRADE_ABOVE_USD: float = 20.0   # Підтвердження вручну для угод > $20

# ─────────────────────────────────────────────
# VOLATILITY TARGETING
# ─────────────────────────────────────────────
TARGET_PORTFOLIO_VOL: float = 0.12      # Цільова волатильність портфеля (12% річних)
LAMBDA_EWMA: float = 0.94              # Коефіцієнт EWMA (стандарт JP Morgan)
VOL_WINDOW_DAYS: int = 10              # Вікно для розрахунку волатильності
VOL_PRICE_INTERVAL: str = "1h"        # Інтервал цін: 1h / 4h
MAX_VOL_NO_TRADE: float = 0.40        # При vol > 40% — не торгуємо
MIN_DATA_POINTS_FALLBACK: int = 5     # Менше N точок → використовуємо fallback
BASE_POSITION_USD: float = 3.0        # Базовий розмір позиції ($3)
MIN_POSITION_USD: float = 1.0         # Мінімальний розмір ($1)
MAX_POSITION_USD: float = 10.0        # Максимальний розмір ($10) — для $100 капіталу

# ─────────────────────────────────────────────
# MARKET MAKING (Ladder)
# ─────────────────────────────────────────────
MM_MODE: bool = True              # # Ladder-стратегія увімкнена (найприбутковіше на temperature) Раніше Market Making режим (вимкнений за замовчуванням)
LADDER_LEVELS: int = 8             # Кількість рівнів ladder (8–15)
LADDER_SPREAD_START: float = 0.02  # Перший рівень ± 2¢ від midpoint
LADDER_SPREAD_STEP: float = 0.02   # Крок між рівнями
LADDER_K_FACTOR: float = 0.5       # Коефіцієнт нелінійного розподілу
LADDER_BASE_VOL: float = 2.0       # Базовий об'єм на рівень ($)
LADDER_REFRESH_SEC: int = 20       # Оновлення ladder кожні 20 секунд
POST_ONLY: bool = True             # Тільки maker ордери (уникати taker fee)
CANCEL_ON_MOVE: float = 0.02       # Скасувати ордери при русі ціни > 2¢
MAX_ORDERS_BATCH: int = 15         # Batch до 15 ордерів за виклик

# Merge positions
MERGE_THRESHOLD_SHARES: int = 50   # Merge при N+ shared Yes+No
MERGE_IMBALANCE_PCT: float = 0.30  # Merge при дисбалансі > 30%

# ─────────────────────────────────────────────
# WEATHER API
# ─────────────────────────────────────────────
NOAA_BASE_URL: str = "https://api.weather.gov"
OPEN_METEO_URL: str = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEO_URL: str = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_CACHE_SEC: int = 900  # Кеш прогнозів 15 хвилин

# Координати міст (lat, lon)
CITY_COORDS: Dict[str, tuple] = {
    "New York": (40.7128, -74.0060),
    "NYC": (40.7128, -74.0060),
    "Chicago": (41.8781, -87.6298),
    "Seattle": (47.6062, -122.3321),
    "Atlanta": (33.7490, -84.3880),
    "Dallas": (32.7767, -96.7970),
    "Miami": (25.7617, -80.1918),
    "Los Angeles": (34.0522, -118.2437),
    "London": (51.5074, -0.1278),
    "Paris": (48.8566, 2.3522),
    "Berlin": (52.5200, 13.4050),
    "Toronto": (43.6532, -79.3832),
}

# Які міста обслуговує NOAA (США)
NOAA_CITIES: List[str] = ["New York", "NYC", "Chicago", "Seattle",
                           "Atlanta", "Dallas", "Miami", "Los Angeles"]

# ─────────────────────────────────────────────
# СКАНУВАННЯ
# ─────────────────────────────────────────────
SCAN_INTERVAL_SEC: int = 120   # Сканування кожні 2 хвилини

# ─────────────────────────────────────────────
# EMAIL СПОВІЩЕННЯ (через Gmail SMTP, безкоштовно)
EMAIL_ENABLED: bool = True        # True після налаштування App Password
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")      # твій gmail
EMAIL_RECIPIENT: str = os.getenv("EMAIL_RECIPIENT", "") # куди слати
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# БЕЗПЕКА (Security Bible Rules)
# ─────────────────────────────────────────────
SECURITY_WALLET_CHECK: bool = True    # Перевірка dedicated wallet
SECURITY_AUDIT_DEPS: bool = True      # Аудит залежностей при старті
MAX_USDC_APPROVAL: float = 500.0      # Ліміт USDC approval (revoke.cash principle)
DEDICATED_WALLET_ONLY: bool = True    # Нагадування: тільки окремий гаманець!

# ─────────────────────────────────────────────
# OSINT / WHALE TRACKING
# ─────────────────────────────────────────────
WHALE_THRESHOLD_USD: float = 1_000.0   # Розмір угоди для визначення whale
INSIDER_ALERT_THRESHOLD: float = 0.15  # Аномальний edge від інсайдера
OSINT_SCAN_INTERVAL_SEC: int = 300     # OSINT скан кожні 5 хвилин
KNOWN_WHALE_WALLETS: List[str] = []    # Заповни після аналізу ринку

# ─────────────────────────────────────────────
# ШЛЯХИ
# ─────────────────────────────────────────────
DATA_DIR: str = "data"
CACHE_DIR: str = "data/cache"
LOGS_DIR: str = "logs"
BACKTEST_DIR: str = "data/backtest"
