"""
config.py — Weather Bot v23 (SNIPER GRID)

Повернення до стратегії снайперської сітки (logbest.md: ROI +105%, $+112 за 36 trades).
Уроки:
- v9: 0.30 over-calibration вбила сітку → 0% WR
- v15-v19: METAR arb → 0% WR (13 збитків)
- v21: SIGMA_MIN=1.5 → our_prob=0.0000 для 90% ринків
- v22: SIGMA_MIN=4.5 + PROB_BIAS=1.0 — OK, ЗАЛИШАЄМО

Стратегія SNIPER GRID:
- Сітка categorical бакетів навкologi forecast (крок 1°C/1°F) — холодний снайпер
- Range бакети (84-85°F) — найліквідніші
- Дешеві YES tails 1-30¢ з our_prob 5-30% — це генератор прибутку
- above/below залишаються для трендових ринків
- Kelly quarter sizing
"""

from typing import List
import os

DRY_RUN: bool = True
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "logs/bot.log"

CLOB_URL: str = "https://clob.polymarket.com"
GAMMA_URL: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = 137

# ── CAPITAL & RISK ──────────────────────────────────────────
INITIAL_CAPITAL: float = 100.0
MAX_POSITION_PCT: float = 0.04
MIN_POSITION_USD: float = 2.0
MAX_POSITION_USD: float = 4.0
MAX_ACTIVE_POSITIONS: int = 12        # сітка потребує багато слотів (v11/v13: 12)
MAX_OPEN_PER_CYCLE: int = 3           # відкриваємо до 3 бакетів за цикл = ladder
MAX_POSITIONS_PER_CITY: int = 5       # ladder з 5 бакетів на місто (16.8/16.9/17.0/17.1/17.2)
STOP_LOSS_PCT: float = 0.30           # сітка має час — даємо дихати
STOP_LOSS_MIN_HOLD_HOURS: float = 2.0 # SL не працює перші 2 години (дапустимо втратити ранню поз)
MAX_TOTAL_EXPOSURE_PCT: float = 0.80  # сітка потребує більше експозиції (0.50 блокувало нові угоди)
MAX_DAILY_LOSS_PCT: float = 0.15
MAX_DAILY_LOSS_USD: float = 20.0
DRAWDOWN_LIMIT: float = 0.30          # tolerant для сітки (logbest мав drawdown 24.5% з ROI +105%)

# ── STRATEGY: SNIPER GRID ───────────────────────────────────
# всі типи ринків — сітка працює на categorical + range, above/below як трендові
KINDS_ONLY: List[str] = ["above", "below", "categorical", "range"]

# пороги для тренду (above/below) — менш пріоритетні за сітку
MIN_EDGE_YES: float = 0.20
MIN_EDGE_NO: float = 0.20
MIN_PROB_ENTRY: float = 0.03

MAX_RESOLUTION_HOURS: int = 48
MIN_RESOLUTION_HOURS: float = 1.0    # 1 година (v11/v13: працював на 1-30h ринках)
MIN_MARKET_VOLUME_USD: float = 500.0
SCAN_INTERVAL_SEC: int = 300          # 5 хв (logbest: 300s)
SCAN_MAX_SLEEP_SEC: int = 600

# ── SNIPER GRID PARAMETERS (головне — для categorical + range) ──
SNIPER_GRID_MIN_EDGE: float = 0.07      # v24: 0.04→0.07 — фільтр tail-gambling (0% WR при 0.04)
SNIPER_GRID_MIN_EDGE_NO: float = 0.10  # NO — консервативніший
SNIPER_GRID_MIN_ASK: float = 0.01      # дозволяємо дешеві хвости до 1¢ (logbest: 1¢ YES)
SNIPER_GRID_MAX_ASK: float = 0.50      # верхня межа для grid YES
SNIPER_GRID_NO_MIN_MARKET: float = 0.60  # NO продаємо ринки де ціна > 60¢
SNIPER_GRID_DISTANCE_C: float = 4.0     # бакети за 4°C від прогнозу відсіваємо
SNIPER_GRID_MAX_PER_CITY_CYCLE: int = 3  # до 3 бакетів на місто за один цикл

# ── ABOVE/BELOW (тренд) ──
TREND_MAX_ASK: float = 0.70            # YES до 70¢
TREND_MIN_NO_MARKET: float = 0.30      # NO від 30¢

# ── PROBABILITY CALIBRATION ────────────────────────────────
# sigma кодує uncertainty (4.5°C min — v22 урок)
PROB_BIAS: float = 1.0

# Categorical discount — v23b: 0.75→0.90
# 0.75 was too aggressive: Gaussian CDF already conservative, extra 25% cut
# made our_prob systematically too low → bot only found extreme tails → 0% WR
CATEGORICAL_DISCOUNT: float = 0.90

# caps для бакетів (categorical/range) — conservative для точкового прогнозу
CAP_EXACT_SHORT: float = 0.60   # ≤6h
CAP_EXACT_MID: float = 0.48     # ≤18h
CAP_EXACT_LONG: float = 0.38    # >18h

# caps для тренду (above/below) — Gaussian хвости
CAP_SHORT: float = 0.85   # ≤6h
CAP_MID: float = 0.75     # ≤18h
CAP_LONG: float = 0.65    # >18h

MAX_EDGE_CAP: float = 0.50

# ── TAIL GAMBLING FILTER (v24) ─────────────────────────────
# Бот купував YES tails по 1-3¢ з our_prob 5-12% — майже ніколи не виграш.
# Тепер відкидаємо BUY_YES коли our_prob < MAX_TAIL_PROB (20%).
# Компроміс: ми пропускаємо деякі дешеві хвости з великим edge% (edge розрахунковий),
# але our_prob < 20% фактично = довгий шанс, що згідно з 0% WR — не materialize.
MAX_TAIL_PROB: float = 0.20

# ── DISTANCE×PROB FILTER (v24) ────────────────────────────
# Коли forecast далеко від бакета (dist > 1.5°C) И our_prob < 12% → skip.
# Такі ринки — tail gamble, що рідко матеріалізується (Dallas 100-101°F, SF 77-78°F
# — обидва втратили через forecast занадто далеко від бакета).
MAX_TAIL_DIST_C: float = 1.5
MAX_TAIL_COMBINED_PROB: float = 0.12

# ── SIGMA (ENSEMBLE SPREAD) ────────────────────────────────
# 11.07.2026: SIGMA_MIN 4.5 → 2.5 (v23b). 12.07.2026: 2.5 → 3.0 (v24).
# При 2.5°C our_prob для бакетів все ще занадто висока для tail-ринків → 0% WR.
# 3.0°C — більш консервативний: our_prob для in-brackets знижується ~20-25%,
# що відсіює найгірші tail-gambles (разом з MAX_TAIL_PROB=0.20 фільтром).
SIGMA_MIN: float = 3.0
SIGMA_SPREAD_FACTOR: float = 1.30

# ── EMPIRICAL BLENDING ────────────────────────────────────
# 30% empirical count + 70% parametric Gaussian (v13 config)
EMPIRICAL_WEIGHT: float = 0.30
ENSEMBLE_WEIGHT: float = 0.70

# ── KELLY ──────────────────────────────────────────────────
USE_KELLY: bool = True
KELLY_SCALE: float = 0.25           # Quarter Kelly (v13/v11 успішний)
KELLY_MAX_POSITION_USD: float = 4.0

# ── SANITY CHECKS ─────────────────────────────────────────
MAX_DISTANCE_SIGMA: float = 3.5   # tail-chase filter (above/below only)

# ── MISC ───────────────────────────────────────────────────
DATA_DIR: str = "data"
LOGS_DIR: str = "logs"
CACHE_DIR: str = "cache"
EMAIL_ENABLED: bool = False

# ── CITIES ─────────────────────────────────────────────────
# ОПТИМІЗОВАНО (11.07.2026): 20 US (швидкі, без 429) + 8 куратованих
# non-US, що РЕАЛЬНО генерували прибуток у logbest.md (London, Paris,
# Sao Paulo, Buenos Aires, Busan) + високоліквідні (Tokyo, Singapore, Sydney).
# НЕ всі 58 міст — бо non-US б'ють 429 на ensemble-api.open-meteo.com
# (~26с backoff/місто), що вбиває 5-хв скан. Prefetch обмежено
# MAX_PREFETCH_CITIES (див. нижче) — сканується тільки топ за обсягом.
CITY_WHITELIST: List[str] = [
    # ── 20 US міст (без 429, надійні) ──
    "NYC", "New York", "Chicago", "Los Angeles", "Miami", "Dallas",
    "Seattle", "Denver", "Atlanta", "Boston", "Houston", "Austin",
    "San Francisco", "Phoenix", "Las Vegas", "Minneapolis",
    "Portland", "Nashville", "Charlotte", "Orlando",
    # ── 8 non-US (logbest-proven + високоліквідні) ──
    "London", "Paris", "Tokyo", "Singapore", "Sydney",
    "Sao Paulo", "Buenos Aires", "Busan",
]

# Ліміт prefetch прогнозів за цикл (захист від повільного скану).
# Унікальні міста з ринків сортуються за кількістю ринків (проксі ліквідності)
# і prefetch-ться тільки топ-N. Скан НІКОЛИ не перевищить цей ліміт,
# незалежно від розміру CITY_WHITELIST.
MAX_PREFETCH_CITIES: int = 24

# ── DRY-RUN VALIDATION GATES ───────────────────────────────
VALIDATION_REQUIRED_BEFORE_LIVE: bool = True
VALIDATION_MIN_RESOLVED_TRADES: int = 30
VALIDATION_MIN_DRY_RUN_HOURS: int = 168
VALIDATION_MIN_WIN_RATE: float = 0.30      # сітка: 30%+ WR (logbest: 4.8% по 21 trades, але ROI +105%)
VALIDATION_MIN_ROI: float = 0.05            # ROI принаймні +5%
VALIDATION_MIN_EQUITY: float = 0.00
