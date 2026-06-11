"""
config.py — Polymarket Weather Bot (PEAK SNIPER EDITION v9)

СТРАТЕГІЯ ЗМІНЕНА: замість купівлі дешевих хвостів (1-5¢) →
купуємо ОДИН найвірогідніший бакет за 15-55¢ з edge ≥ 5%.
Причина: GRID tail стратегія дала 0% win rate з 16 resolved trades.
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
MAX_POSITION_PCT: float = 0.08          # ✅ v9: 5%→8% (менше угод, але більші ставки)
MIN_POSITION_USD: float = 2.0           # Мінімальний розмір ставки
BASE_POSITION_USD: float = 4.0          # ✅ v9: $3→$4 (ставки на пікових бакетах)
MAX_ACTIVE_POSITIONS: int = 5           # ✅ v9: 8→5 (менше, але якісніше)
RESERVED_FAST_SLOTS: int = 0            # Резервуємо 0 слоти для угод <= 6 годин до резолву
FAST_SLOT_THRESHOLD_HOURS: float = 6.0  # Поріг для швидких слотів (METAR/Observed зона)
MAX_POSITIONS_PER_CITY: int = 1         # ✅ v9: 3→1 (ОДИН бакет на місто на день!)
MAX_DRAWDOWN_PCT: float = 0.70          # 70% просадки дозволено (для DRY-RUN)
STOP_LOSS_PCT: float = 0.99             # Майже вимикаємо стоп-лоси для YES (чекаємо resolution)
MAX_POSITION_USD: float = 8.0           # ✅ v9: $5→$8 (дозволяємо більші ставки на якісних)

# ── 🛑 СТРАТЕГІЯ "ПАРОВИЙ КАТОК" (NO) ВИМКНЕНА ───────────────
ENABLE_COLDMATH_TAIL_NO: bool = False
COLDMATH_MIN_ASK_NO: float = 0.95
COLDMATH_MAX_ASK_NO: float = 0.99
COLDMATH_MIN_EDGE_NO: float = 0.12
COLDMATH_MAX_SIZE_USD: float = 0.0

# ── 🎯 СТРАТЕГІЯ "PEAK SNIPER" (ЗАМІСТЬ GRID) ────────────────
# ✅ v9: Повністю переосмислено — тепер купуємо ЛІКВІДНІ пікові бакети
ENABLE_EXTREME_TAIL_YES: bool = True
EXTREME_TAIL_MAX_ASK_YES: float = 0.55  # ✅ v9: 0.15→0.55 (купуємо бакети до 55¢!)
EXTREME_TAIL_MIN_ASK_YES: float = 0.10  # ✅ v9: 0.001→0.10 (НЕ КУПУЄМО дешевше 10¢!)
EXTREME_TAIL_MAX_SIZE_USD: float = 5.0  # ✅ v9: $3→$5 (більші ставки на якісних)
EXTREME_TAIL_MIN_EDGE_YES: float = 0.04 # ✅ v9: 0.12→0.04 (менший edge при вищій prob OK)

# ── 🎯 СТРАТЕГІЯ "СНАЙПЕРСЬКА СІТКА" (ADJACENT GRID) ────────
ENABLE_ADJACENT_GRID: bool = False       # ✅ v9: ВИМКНЕНО (сітка = розмазування капіталу)
ADJACENT_GRID_SIZE_USD: float = 2.0
ADJACENT_GRID_MIN_EDGE: float = 0.18
ADJACENT_GRID_MAX_ASK: float = 0.20

# ── СТАНДАРТНИЙ EDGE (Для Peak YES угод) ─────────────────────
MIN_EDGE_ENTRY: float = 0.05            # ✅ v9: 0.18→0.05 (менший edge потрібен при правильній prob)
MIN_EDGE_HOLD: float = 0.02             # 2% — тримаємо позицію поки є хоч якийсь edge

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
COMPOUND_RISK_PCT: float = 0.05         # ✅ v9: 3%→5% від капіталу (менше угод → більші ставки)
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
# ✅ v9: РІЗКО ЗНИЖЕНІ — попередні дані показали систематичне завищення
# prob_above_temp_c / prob_below_temp_c (відносно точні)
PROB_CAP_ABOVE_SHORT: float = 0.75    # hours <= 6.0  (v9: 0.78→0.75)
PROB_CAP_ABOVE_MID:   float = 0.68    # hours <= 18.0 (v9: 0.72→0.68)
PROB_CAP_ABOVE_LONG:  float = 0.60    # hours > 18.0  (v9: 0.65→0.60)

# prob_exact_temp_c / range / categorical — ГОЛОВНА ПРОБЛЕМА
# Реальний win rate 0% при our_prob=15-29% → ми завищуємо в 3-5 разів!
PROB_CAP_EXACT_SHORT: float = 0.40    # ✅ v9: 0.62→0.40 (РІЗКЕ зниження!)
PROB_CAP_EXACT_MID:   float = 0.32    # ✅ v9: 0.52→0.32
PROB_CAP_EXACT_LONG:  float = 0.25    # ✅ v9: 0.42→0.25

# Максимальний edge (запобігає фантомним edge > 35%)
MAX_EDGE_CAP: float = 0.35            # ✅ v9: 0.45→0.35 (суворіший)
