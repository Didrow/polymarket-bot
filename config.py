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
MAX_ACTIVE_POSITIONS: int = 12  # 12 для всіх режимів (реалістичне тестування)
RESERVED_FAST_SLOTS: int = 5            # Резервуємо 5 слотів для угод <= 6 годин до резолву
FAST_SLOT_THRESHOLD_HOURS: float = 6.0  # Поріг для швидких слотів (METAR/Observed зона)
MAX_POSITIONS_PER_CITY: int = 3          # Макс позицій на одне місто (антикореляція)
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
EXTREME_TAIL_MIN_ASK_YES: float = 0.05  # ✅ НОВЕ: не купуємо YES < 5¢ (фантомні ставки)
EXTREME_TAIL_MAX_SIZE_USD: float = 4.0  # $2-$4 на кожен тікет у "сітці"
EXTREME_TAIL_MIN_EDGE_YES: float = 0.20 # ✅ 15% → 20% (підвищено після аналізу cap issue)

# ── 🎯 СТРАТЕГІЯ "СНАЙПЕРСЬКА СІТКА" (ADJACENT GRID) ────────
ENABLE_ADJACENT_GRID: bool = True
ADJACENT_GRID_SIZE_USD: float = 2.0
ADJACENT_GRID_MIN_EDGE: float = 0.25    # ✅ 20% → 25% (підвищено для якості)
ADJACENT_GRID_MAX_ASK: float = 0.20

# ── СТАНДАРТНИЙ EDGE (Для Sniper YES угод) ───────────────────
MIN_EDGE_ENTRY: float = 0.25            # ✅ 20% → 25% (підвищено для фільтрації phantom edges)
MIN_EDGE_HOLD: float = 0.05             # ✅ 2% → 5% (раніше закриваємо при втраті edge)

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

# ── ЙМОВІРНІСНІ CAPS (ДЖЕРЕЛО ІСТИНИ) ─────────────────────────
# УВАГА: змінювати тут — зміна поведінки в data_fetcher.py та edge_calculator.py
# prob_above_temp_c / prob_below_temp_c
PROB_CAP_ABOVE_SHORT: float = 0.78    # hours <= 6.0
PROB_CAP_ABOVE_MID: float = 0.72      # hours <= 18.0
PROB_CAP_ABOVE_LONG: float = 0.68     # hours > 18.0

# prob_exact_temp_c / range / categorical
PROB_CAP_EXACT_SHORT: float = 0.72    # hours <= 6.0
PROB_CAP_EXACT_MID: float = 0.62      # hours <= 18.0
PROB_CAP_EXACT_LONG: float = 0.55     # hours > 18.0

# Максимальний edge (запобігає фантомним edge > 60%)
MAX_EDGE_CAP: float = 0.60
