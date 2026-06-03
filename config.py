"""
config.py — Polymarket Weather Bot (SNIPER GRID EDITION v3)
"""

from typing import List
import os

DRY_RUN: bool = True                    # 🧪 Тестовий режим (реальна торгівля — False)
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── КАПІТАЛ ТА РИЗИК-МЕНЕДЖМЕНТ ──────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.05          # Максимум 5% ($5) на ОДИН контракт
MIN_POSITION_USD: float = 2.0           # Мінімальний розмір ставки
BASE_POSITION_USD: float = 3.0          # Базовий розмір ставки (не використовується при compound)
MAX_ACTIVE_POSITIONS: int = 20 if DRY_RUN else 12  # 20 для симуляції, 12 для LIVE
RESERVED_FAST_SLOTS: int = 5            # Резервуємо 5 слотів для угод <= 6 годин до резолву
FAST_SLOT_THRESHOLD_HOURS: float = 6.0  # Поріг для швидких слотів (METAR/Observed зона)
MAX_POSITIONS_PER_CITY: int = 2          # Макс позицій на одне місто (антикореляція)
MAX_DRAWDOWN_PCT: float = 0.70          # 70% просадки дозволено (для DRY-RUN)
STOP_LOSS_PCT: float = 0.99             # Майже вимикаємо стоп-лоси для дешевих YES (чекаємо фінального resolution)
MAX_POSITION_USD: float = 5.0           # Абсолютний ліміт позиції в $

# ── 🛑 СТРАТЕГІЯ "ПАРОВИЙ КАТОК" (NO) ВИМКНЕНА ───────────────
ENABLE_COLDMATH_TAIL_NO: bool = False   
COLDMATH_MIN_ASK_NO: float = 0.95
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.12
COLDMATH_MAX_SIZE_USD: float = 0.0

# ── 🎣 СТРАТЕГІЯ "РЯТУВАЛЬНА СІТКА" (GRID YES) ───────────────
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.12  # Купуємо YES, якщо він коштує 12¢ або дешевше
EXTREME_TAIL_MAX_SIZE_USD: float = 4.0  # $2-$4 на кожен тікет у "сітці"
EXTREME_TAIL_MIN_EDGE_YES: float = 0.20 # 20% мінімум після виправлення prob_exact

# ── 🎯 СТРАТЕГІЯ "СНАЙПЕРСЬКА СІТКА" (ADJACENT GRID) ────────
ENABLE_ADJACENT_GRID: bool = True
ADJACENT_GRID_SIZE_USD: float = 2.0
ADJACENT_GRID_MIN_EDGE: float = 0.15
ADJACENT_GRID_MAX_ASK: float = 0.20

# ── СТАНДАРТНИЙ EDGE (Для Sniper YES угод) ───────────────────
MIN_EDGE_ENTRY: float = 0.15            # 15% мінімум — після виправлення prob_exact
MIN_EDGE_HOLD: float = 0.02             # Поріг для утримання

# ── MARKETS ТА ФІЛЬТРИ ────────────────────────────────────────
MAX_RESOLUTION_HOURS: int = 48          # Зменшено для більшої точності прогнозу
MIN_RESOLUTION_HOURS: float = 0.5       # Мінімум годин до резолву (0.5 = 30хв для DRY-RUN, у бойовому 1.5)
MIN_MARKET_VOLUME_USD: float = 500.0    
SCAN_INTERVAL_SEC: int = 600            # Сканування кожні 10 хвилин
OSINT_SCAN_INTERVAL_SEC: int = 300
MAX_VOL_NO_TRADE: float = 0.60
TARGET_PORTFOLIO_VOL: float = 0.15

# ── КОМПАУНДИНГ (Re-investing) ───────────────────────────────
ENABLE_COMPOUND: bool = True            # Якщо True, розмір ставки = % від поточного капіталу
COMPOUND_RISK_PCT: float = 0.03         # 3% від капіталу на одну угоду (можна змінювати)
USE_KELLY: bool = True                  # Використовувати Quarter-Kelly замість фіксованого %

# ── ТЕХНІЧНЕ ТА ШЛЯХИ ────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"
MAX_USDC_APPROVAL: float = 1000.0       

# ── EMAIL АЛЕРТИ ──────────────────────────────────────────────
EMAIL_ENABLED: bool = False
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_RECIPIENT: str = os.getenv("EMAIL_RECIPIENT", "")

# ── WHITELIST МІСТ ────────────────────────────────────────────
CITY_WHITELIST: List[str] = [
    "London", "Paris", "NYC", "Chicago", "Los Angeles",
    "Tokyo", "Seoul", "Buenos Aires",
    "Busan", "Lucknow", "Cape Town",
    "Miami", "Dallas", "Seattle", "Berlin", "Sydney",
    "Sao Paulo", "Munich",
]

KNOWN_WHALE_WALLETS: List[str] = []
EXTREME_TAIL_CITIES: List[str] = CITY_WHITELIST

# ── СУМІСНІСТЬ ────────────────────────────────────────────────
MIN_DATA_POINTS_FALLBACK: int = 5      
WHALE_THRESHOLD_USD: float = 2000.0    

# ── EDGE CAP (запобігання хибним сигналам) ───────────────────
MAX_EDGE_CAP: float = 0.75             # Якщо edge > 75% — підозріло, cap до цього значення
MAX_PROB_CAP: float = 0.92             # Загальний cap (fallback, використовується в data_fetcher)
MAX_PROB_CAP_RANGE: float = 0.88        # Cap для range/categorical бакетів (точне попадання)
MAX_PROB_CAP_ABOVE_BELOW: float = 0.94  # Cap для above/below (вища реальна впевненість)
