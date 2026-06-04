# MEMORY.md — Polymarket Weather Bot

## Огляд проекту
Погодний бот для Polymarket, який торгує на ринках прогнозів погоди (температура) з використанням мультимодельного ансамблю, стратегії Grid YES (дешеві хвости) та Sniper YES (основні прогнози).

## Файлова структура
- `main.py` — головний цикл, ініціалізація, health-check сервер
- `config.py` — всі конфігураційні параметри (капітал, ризик, API)
- `data_fetcher.py` — отримання прогнозів (Ensemble, METAR, NOAA, NASA, GFS, ECMWF) + спостережених даних
- `edge_calculator.py` — розрахунок ймовірностей та edge для кожного ринку
- `market_scanner.py` — пошук погодних ринків на Polymarket через Gamma API
- `trader.py` — розміщення угод, відстеження позицій, resolution, Mark-to-Market
- `safeguards.py` — захист (circuit breaker, drawdown, JSONBin), збереження стану
- `security.py` — перевірки безпеки
- `notifier.py` — email-сповіщення
- `osint_module.py` — whale-трекінг
- `MEMORY.md` — цей файл (контекст для всіх сесій)

## Виправлені проблеми (25.05.2026)

### 1. METAR у вагах прогнозу (data_fetcher.py)
**Проблема:** METAR (поточна температура та точка роси) змішувався з добовими прогнозами з вагою 65–90% для ринків ≤12 годин.
**Наслідок:** Бот купував контракти на "Lowest temperature be X°C or below", використовуючи поточну точку роси як добовий мінімум, хоча реальний мінімум вже був зафіксований раніше вдень.
**Виправлення:** METAR повністю прибрано з вагового консенсусу. Додано `_fetch_observed_daily_extremes` — отримання реальних спостережених мінімуму/максимуму за сьогодні з Open-Meteo hourly. Спостережені дані та METAR застосовуються як пост-обмеження (hard bounds) на прогноз.

### 2. Нульовий Unrealized PnL (trader.py)
**Проблема:** `_get_market_price_from_gamma` повертала `None` при `best_bid == 0.0`, що блокувало MTM-оновлення для неліквідних позицій.
**Виправлення:** Додано fallback на `lastTradePrice` та оцінку ціни через `best_ask / 2` при нульовому біді.

### 3. Ретраї JSONBin (safeguards.py)
**Проблема:** HTTP-помилки (502) не викликали ретраїв — функція одразу повертала `False`.
**Виправлення:** Додано `if attempt < _retries: _time.sleep(2)` перед `return False` при HTTP-помилках.

### 4. Капітал у DRY-RUN (safeguards.py)
**Проблема:** `record_trade_open/close` не оновлювали `current_capital` при `DRY_RUN=True`, тому ROI/просадка завжди були нульовими.
**Виправлення:** Забрано умову `if not config.DRY_RUN`, капітал оновлюється завжди.

### 5. day_index: int → round (data_fetcher.py)
**Проблема:** `int(hours_to_resolution / 24)` у `fetch_open_meteo_ensemble` та `fetch_open_meteo` міг зміщувати день для 12–23 годин.
**Виправлення:** Замінено на `round(hours_to_resolution / 24)`.

## Поточний стан конфігурації
- `DRY_RUN = True` (тестовий режим)
- Капітал: $100.00
- Макс позицій: 12
- Grid YES: до $4.00, ціна 5-12¢, edge ≥ 20%
- Sniper YES: edge ≥ 25%
- Adjacent Grid: edge ≥ 25%
- Kelly/Compound: увімкнено (1/8-Kelly, prob cap 0.80)
- Спред макс: 5 центів
- Сканування: кожні 600 секунд
- Джерела прогнозу: Open-Meteo Ensemble, NOAA, NASA POWER, GFS, ECMWF + METAR (boundary only)

## Консервативні ймовірнісні caps (04.06.2026) — ЗАХИСТ ВІД ФАНТОМНИХ EDGE

**Проблема виявлена в лозі 04.06.2026 07:05-07:48 UTC:**
LA above 74°F (forecast=23.7°C=74.7°F, threshold=74°F) — реальна P(z=0.236)=0.63, але cap 0.96 повертав 0.92, створюючи фантомний edge 75% @ market 4¢.

**Виправлення (data_fetcher.py:65-77, 106-115):**
- prob_above: cap 0.96/0.94/0.92 → **0.78/0.72/0.68**
- prob_exact: cap 0.90/0.80/0.70 → **0.72/0.62/0.55**
- empirical weight 75% → **55%** (запобігає переоцінці unanimous ensemble)

**Виправлення (config.py:38-51, 95-98):**
- `EXTREME_TAIL_MIN_ASK_YES: 0.05` — фільтр фантомних ставок 1-4¢
- `EXTREME_TAIL_MIN_EDGE_YES: 0.20` (15% → 20%)
- `ADJACENT_GRID_MIN_EDGE: 0.25` (20% → 25%)
- `MIN_EDGE_ENTRY: 0.25` (20% → 25%)
- `MIN_EDGE_HOLD: 0.05` (2% → 5%)
- `MAX_EDGE_CAP: 0.60` (75% → 60%)
- `MAX_PROB_CAP_ABOVE_BELOW: 0.78` (94% → 78%)

**Виправлення (edge_calculator.py:268):**
- `GRID_MIN_PRICE = 0.02` → `getattr(config, 'EXTREME_TAIL_MIN_ASK_YES', 0.05)`

**Результат:** LA above 74°F @ 4¢ тепер: our_prob=0.78, edge=0.74 → capped to 0.60, eff_edge=0.60, position=$4.0 (4/100 = 4% ризику).

## Рекомендації на майбутнє (пріоритетні)
1. **Backtesting Module** — тестування стратегій на історичних даних
2. **Динамічна волатильність** — реальна оцінка `_get_market_vol` замість `0.18`
3. **Облік нереалізованого PnL** — в `current_capital` та `peak_capital`
4. **Rate Limiting** — захист від блокування Polymarket API
5. **Асинхронність** — паралельні запити до джерел даних
6. **Розширені сповіщення** — Telegram/Discord
7. **Динамічне зважування джерел** — за історичною точністю
8. **Ліміти кореляції** — обмеження експозиції на групу пов'язаних ринків

## Важливі зауваження
- При переході з DRY-RUN на реальну торгівлю скинути статистику через `/reset-stats-9922`
- JSONBin потребує налаштування змінних середовища `JSONBIN_KEY` та `JSONBIN_BIN_ID`
- Для реальної торгівлі потрібні `PRIVATE_KEY`, `POLY_API_KEY`, `POLY_API_SECRET`, `POLY_API_PASSPHRASE`
- METAR працює тільки для міст з ICAO-кодом (аеропорт)
