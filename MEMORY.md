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

## Оновлення після повного аналізу (27.05.2026)

### 6. PostgreSQL та Логування (safeguards.py)
**Проблема:** Бот видавав помилку `dsn` при спробі підключитись до Render PostgreSQL.
**Виправлення:** `DATABASE_URL` тепер парситься через `urllib.parse.urlparse`, також створено таблицю `trade_log` для фіксації кожної угоди (`log_trade_to_pg()`).

### 7. Розморожування позицій (trader.py, main.py)
**Проблема:** Бот зависав на ліміті 6 позицій через застарілі нерезолвнуті ринки, які чекали 36 годин.
**Виправлення:** Зменшено stale timeout до 26h. Введено функцію `startup_cleanup()`, яка одразу при старті примусово перевіряє статус всіх відновлених угод. 

### 8. PnL та Capital Tracking в DRY-RUN
**Проблема:** Симуляція не зменшувала і не збільшувала капітал, бо перевірки ігнорували `DRY_RUN`. Більше того, не було перевірки resolution для минулих угод.
**Виправлення:** Прибрано ігнорування `DRY_RUN` в `safeguards.py`. У `trader.py` додано симуляцію resolution на основі порівняння факту погоди з порогом для ринків, у яких вже минув `end_date`.

### 9. Observed bounds для LOW (data_fetcher.py)
**Проблема:** `obs_low` обмежував `temp_low_members` знизу (`max`), а треба було обмежувати зверху (`min`), оскільки фактичний мінімум не міг бути вище вже спостереженого.
**Виправлення:** `result.temp_low_members = [min(m, obs_low) for m in result.temp_low_members]`.

### 10. Стратегія "Снайперська Сітка" (Adjacent Grid)
**Опис:** Введено підтримку автоматичного створення "сітки" угод: при знаходженні SNIPER YES-сигналу, бот шукає суміжні температурні бакети (±1°C або ±1°F) і бере їх меншими ставками ($2), якщо ціна ≤ $0.20 і edge ≥ 15%.
**Виправлення:** В `config.py` збільшено `MAX_ACTIVE_POSITIONS` до 12. В `edge_calculator.py` додано другий прохід циклу в `scan_all_edges()`.

## Поточний стан конфігурації
- `DRY_RUN = True` (тестовий режим)
- Капітал: $100.00
- Макс позицій: 10
- Grid YES: до $4.00, ціна ≤ $0.12, edge ≥ 20%
- Sniper YES: edge ≥ 25%
- Kelly/Compound: увімкнено (Quarter-Kelly)
- Спред макс: 3 центи
- Сканування: кожні 600 секунд
- Джерела прогнозу: Open-Meteo Ensemble, NOAA, NASA POWER, GFS, ECMWF + METAR (boundary only)

## Реалізовані покращення (28.05.2026 - 29.05.2026)
1. ✅ **Облік нереалізованого PnL та Equity**: Повністю переписано систему розрахунку капіталу. Тепер ROI та Drawdown розраховуються на основі справжнього **Equity** (Кеш + Поточна ринкова вартість відкритих позицій), що унеможливлює помилкове спрацювання circuit breakers та дезінформацію про ROI.
2. ✅ **Динамічна волатильність**: Функція `_get_market_vol` тепер розраховує волатильність динамічно на основі стандартного відхилення бінарного опціону $\sqrt{p(1-p)}$ від `entry_price`.
3. ✅ **Антикореляційні ліміти**: Додано `MAX_POSITIONS_PER_CITY = 3` для захисту від концентрованих втрат по одному місту (наприклад, Miami).
4. ✅ **Покращення Resolution у DRY-RUN**: Реалізовано fallback на поточні спостереження `_fetch_observed_daily_extremes` при затримках архівного API, що розблокувало завислі позиції.
5. ✅ **Оптимізація сканування**: З логів та розрахунків виключено дублікати сигналів для вже відкритих угод.
6. ✅ **Зменшення Stale Timeout**: Stale Timeout зменшено до 28 годин (з 36h).
7. ✅ **Виправлення Resolution та PnL у DRY-RUN (29.05.2026)**: Виправлено критичний баг у `cleanup_stale_positions`, через який закриття застарілих нерезолвнутих (stale) позицій у DRY-RUN режимі не записувалося як втрата капіталу (`pnl_usd = 0.0` замість `-pos.size_usd`). Це призводило до штучного роздування симуляційного капіталу. Тепер прострочені (age > 28h) нерезолвнуті позиції коректно маркуються як `EXPIRED` з повним збитком (`pnl_usd = -size_usd`). Також знижено `MAX_ACTIVE_POSITIONS` до 10 для уникнення надмірного замороження капіталу.

## Рекомендації на майбутнє (пріоритетні)
1. **Backtesting Module** — тестування стратегій на історичних даних
2. **Rate Limiting** — захист від блокування Polymarket API
3. **Асинхронність** — паралельні запити до джерел даних
4. **Розширені сповіщення** — Telegram/Discord
5. **Динамічне зважування джерел** — за історичною точністю

## Важливі зауваження
- При переході з DRY-RUN на реальну торгівлю скинути статистику через `/reset-stats-9922`
- Для реальної торгівлі потрібні `PRIVATE_KEY`, `POLY_API_KEY`, `POLY_API_SECRET`, `POLY_API_PASSPHRASE`
- METAR працює тільки для міст з ICAO-кодом (аеропорт)

## Реалізовані покращення (06.06.2026 — Оптимізація Прибутковості)
1. ✅ **Виправлено критичний баг інверсії капів для above/below ринків**: Раніше `prob_below_temp_c` обчислювався як `1 - prob_above_temp_c`. Через те, що `prob_above_temp_c` обмежувався зверху (`max_cap` = 0.58 для тривалих прогнозів), `prob_below_temp_c` отримував штучний ліміт знизу у **42%** навіть при реальній ймовірності 0.001% (наприклад, при прогнозі 40°C ймовірність "нижче 15°C" розраховувалась як 42%). Це створювало фантомні "edge" і змушувало бота купувати завідомо програшні YES-квитки. Створено метод `raw_prob_above_temp_c()`, а кап тепер накладається на фінальну ймовірність обраного напрямку незалежно.
2. ✅ **Реалістичне калібрування Sigma**: Збільшено базові значення sigma на ~70% (до 1.8-3.5°C залежно від міста), оскільки реальна помилка прогнозів на горизонті 20+ годин є значно вищою за старі вузькі межі (1.0-2.2°C). Це знизило переоцінку ймовірностей точкових (categorical) бакетів.
3. ✅ **Корекція кореляції ансамблю (Categorical Discount)**: Додано множник 0.55 для точкових (categorical) ймовірностей та знижено вагу емпіричного підрахунку до 40% (замість 55%). Оскільки 31 модель GFS Ensemble корелюють між собою і не є незалежними, простий підрахунок часток давав хибну надмірну впевненість.
4. ✅ **Посилення фільтрів edge та відстані**:
   - Вузький фільтр відстані для categorical: зменшено `max_dist` з 2.5°C до 1.5°C (та з 2.7°C до 2.0°C для F).
   - Піднято мінімальну ймовірність GRID з 8% до 15% (виключає лотерейні квитки з мізерним шансом).
   - Піднято `MIN_EDGE_ENTRY` до 25% (замість 18%) та ліміт GRID edge до 20%.
   - Піднято `VALUE YES` мін. prob до 25% та мін. confidence до 85%.
5. ✅ **Виправлено втрату PnL при старті**: Усунено баг у `main.py`, через який позиції, очищені під час `startup_cleanup()`, видалялися з пам'яті без запису PnL в статистику SafeguardManager та без логування в PG, що викривлювало фінансову історію після перезапусків.
6. ✅ **Капіталоємність та ризик**: Знижено `MAX_ACTIVE_POSITIONS` до 10 та `MAX_POSITIONS_PER_CITY` до 3 для кращої диверсифікації та вищої середньої якості угод.

## Реалізовані покращення (07.06.2026 — Оптимізація та зняття обмежень торгової логіки)
1. ✅ **Знижено EXTREME_TAIL_MIN_ASK_YES**: Зменшено мінімальну ціну купівлі YES-опціонів для стратегії GRID з 0.05 до 0.01 (1¢). Це дозволяє боту входити у надзвичайно вигідні cheap tail контракти, де ринок дає 1-2¢, але реальний прогноз показує помітний шанс на resolution.
2. ✅ **Адаптовано спред-фільтр під GRID YES**: Для дешевих ринків (ask ≤ 15¢) bid=0 є абсолютно нормальним явищем через низьку ліквідність (спред дорівнює ціні ask). Раніше жорсткий спред-фільтр (max_spread = 10¢) блокував такі ринки. Тепер для дешевих контрактів дозволяється спред аж до самої ціни ask (`max(0.10, best_ask_yes)`).
3. ✅ **Впровадзенно коефіцієнтний edge (Ratio-based Edge) для дешевих ринків**: На контрактах з ask ≤ 5¢ абсолютний edge у 20% є математично неможливим через малість самої ціни. Тепер для ask ≤ 5¢ діє правило: угода дозволяється, якщо наша ймовірність перевищує ціну ринку в 3 або більше разів (R:R ≥ 3x) при мінімальній ймовірності нашої моделі від 5% та абсолютному edge від 5%. Для дорожчих ринків GRID (5-15¢) збережено класичний фільтр (edge ≥ 20%, prob ≥ 15%).
4. ✅ **Підвищено ймовірнісні капси (Probability Caps)**: Раніше caps були занадто агресивно знижені, що повністю перекривало будь-які сигнали. Нові збалансовані капси враховують горизонт прогнозу (короткострокові прогнози ≤6h точніші, тому кап вищий):
   - PROB_CAP_ABOVE: SHORT=0.78, MID=0.72, LONG=0.65
   - PROB_CAP_EXACT: SHORT=0.62, MID=0.52, LONG=0.42
   Це розблокувало розрахунок edge для якісних короткострокових та середньострокових сигналів.
5. ✅ **Впроваджено діагностичне логування пропущених ринків**: Додано детальні інформаційні повідомлення для ринків, що мають позитивний edge, але не проходять стратегічні фільтри (наприклад, через обмеження по спреду, мінімальному ask або ліміту відстані). Це значно полегшує моніторинг рішень бота.

## Реалізовані покращення (07.06.2026 — Оптимізація Прибутковості та Очищення Логів - v6)
1. ✅ **Знижено торгові пороги для Sniper, Value та Grid (config.py)**:
   - `MIN_EDGE_ENTRY` знижено з `0.25` до `0.18`. Це розблокувало якісні снайперські угоди (SNIPER YES), які раніше математично не могли відкритись через дію probability caps та categorical discounts.
   - `EXTREME_TAIL_MIN_EDGE_YES` знижено з `0.20` до `0.12` для стандартної сітки GRID (5-15¢), що дозволяє ширше охоплення перспективних tail-контрактів.
   - `ADJACENT_GRID_MIN_EDGE` знижено з `0.25` до `0.18` для покращення ефективності суміжних температурних сіток.
2. ✅ **Усунено дублювання повідомлень про ліміти слотів (main.py)**:
   - Додано прапорець `logged_slow_slots_full`, що запобігає спаму повідомлення `📊 Слоти для довгих позицій заповнені` по 10-15 разів за один цикл сканування. Тепер воно логується рівно один раз за цикл.

## Реалізовані покращення (08.06.2026 — Виправлення ймовірностей та узгодження ансамблю - v7)
1. ✅ **Корекція ймовірностей Range ринків (edge_calculator.py)**: Range ринки (наприклад, "84-85°F") тепер використовують ті самі дискаунт-коефіцієнти (`0.55` для звичайних та `0.35` для нульової емпірики), що й точкові (categorical) ринки. Це усунуло критичний баг штучного завищення ймовірностей tail-контрактів (наприклад, оцінка в 22% при реальних ~2%).
2. ✅ **Активація Distance Filter для Range ринків (edge_calculator.py)**: Змінено логіку фільтрації відстані (`max_dist = 1.5°C / 2.0°F` від поточного прогнозу) — тепер він застосовується до range ринків так само, як і до categorical. Це запобігає відкриттю YES позицій на температурні діапазони, які занадто далекі від поточного прогнозу.
3. ✅ **Усунення зміщення GFS ансамблю (Consensus Bias Correction) (data_fetcher.py)**: Додано метод `_get_adjusted_members()`, який зміщує (bias-corrects) окремі моделі GFS ансамблю так, щоб their середнє значення збігалося з консенсус-прогнозом (який об'єднує NOAA, ECMWF, GFS та NASA). Всі подальші розрахунки емпіричної та параметричної ймовірності (`raw_prob_above_temp_c`, `prob_exact_temp_c`, range) тепер використовують ці скориговані члени ансамблю, що ліквідувало систематичну похибку GFS.

## Реалізовані покращення (09.06.2026 — Виправлення зміщення дат та ліквідація передчасного закриття угод - v8)
1. ✅ **Впровадження timezone-aware Event Date Parsing (market_scanner.py)**: Додано функції `parse_date_from_question` (регулярні вирази для зчитування дати безпосередньо з назви контракту) та `get_target_date` (зміщення за часовими поясами). Це усунуло баг зміщення дати на 1 день вперед (Day N+1 замість Day N), який виникав через те, що контракти закриваються рано вранці UTC наступного дня.
2. ✅ **Синхронізація дати в прогнозах та резолвах**: Передано `target_date` у `get_best_forecast()` та всі погодні моделі (`fetch_open_meteo_ensemble`, `fetch_noaa_forecast`, `fetch_open_meteo`), що вирівняло розрахунок ймовірностей із цільовим днем події. Також це забезпечило правильну звірку результатів у `check_market_resolved` за архівними даними.
3. ✅ **Запобігання передчасному видаленню та закриттю угод (trader.py)**:
   - Збільшено інтервал очищення застарілих ринків (`cleanup_stale_positions`) з 2 годин до 48 годин (`hours=48`).
   - Збільшено поріг визначення статусу `EXPIRED` для DRY-RUN з 12 до 48 годин.
   - Збільшено час примусового закриття угод за CLOB midpoint з 12 до 48 годин.
    - Заборонено видаляти позиції з активних, якщо вони ще не розв'язалися і не закінчився 48-годинний ліміт. Це вирішило баг, через який кожен контракт автоматично фіксувався як збиток -100% через 2 години після закінчення, до того як API історичної погоди встигало оновитися.


## Реалізовані покращення (11.06.2026 — Стратегічний півот на PEAK SNIPER - v9)
1. ✅ **Повний стратегічний півот з дешевих хвостів (GRID YES) на ліквідні пікові бакети (PEAK YES)**:
   - Історичний бектест та логи показали 0% win rate на cheap tail (1-5¢) YES-контрактах. Ринок-мейкери на Polymarket занадто точно оцінюють малоймовірні події.
   - Переписано логіку `edge_calculator.py` для відсікання угод дешевше 10¢ (`EXTREME_TAIL_MIN_ASK_YES = 0.10`).
   - Дозволено купівлю найбільш вірогідних бакетів навколо прогнозованого середнього (діапазон цін 15-55¢) з очікуваним win rate ~30-40%.
2. ✅ **Сувора калібровка та заниження ймовірнісних caps**:
   - Categorical discount зменшено з 0.55 до 0.30, а для range з 0.55/0.35 до 0.30/0.15. Це ліквідує системне завищення розрахункових ймовірностей бота.
   - Емпіричну вагу ансамблю в `raw_prob_above_temp_c` та `prob_exact_temp_c` знижено до 20% (замість 40%), оскільки члени ансамблю GFS є корельованими та переоцінюють впевненість.
   - Знижено `PROB_CAP_EXACT` для exact/range ринків: SHORT=0.40, MID=0.32, LONG=0.25 (запобігає фантомним edge).
3. ✅ **Посилений ризик-менеджмент**:
   - `MAX_ACTIVE_POSITIONS` обмежено 5 (замість 8), `MAX_POSITIONS_PER_CITY = 1` (тільки один бакет на місто на день).
   - Kelly-cap probability в `trader.py` знижено з 0.80 до 0.55 для запобігання надмірним об'ємам ставок.
   - Створено та застосовано скрипт `reset_bot.py` для повного скидання застарілого збиткового стану при переході на нову стратегію.

## Реалізовані виправлення (12.06.2026 — Calibrated Sniper Grid fix)
1. ✅ **Увімкнено калібрування ймовірностей для above/below ринків**: `edge_calculator.py` тепер застосовує `_calibrated_probability()` до всіх типів температурних ринків (`above`, `below`, `range`, `categorical`), а не лише до `categorical/range`.
2. ✅ **Виправлено подвійний підрахунок експозиції**: `main.py` тепер рахує `projected_exposure = portfolio.total_value + new_size`, без повторного додавання `sum(pos.size_usd)` для вже відкритих позицій.
3. ✅ **Adjacent Grid також проходить калібрування**: суміжні бакети в другому проході `scan_all_edges()` тепер отримують ту саму консервативну калібровку ймовірностей, що й основні сигнали.
4. ✅ **Верифікація**: `python3 -m compileall .`, runtime import smoke та calibration smoke-test пройдені.

## Реалізовані виправлення (12.06.2026 — Forecast ladder grid, v10.1)
1. ✅ **Розблоковано forecast ladder 17.0 → 16.8/16.9/17.0/17.1/17.2**: `SNIPER_GRID_MAX_MARKETS_PER_CITY` піднято з `2` до `5`, щоб бот міг будувати сітку з 5 суміжних температурних бакетів у межах одного міста.
2. ✅ **Підвищено загальний ліміт на місто**: `MAX_POSITIONS_PER_CITY` піднято з `3` до `5`, щоб grid-лadder не блокувався старішим антикореляційним лімітом.
3. ✅ **Додано distance tie-breaker**: `EdgeResult` тепер зберігає `distance_c`, а `scan_all_edges()` при однаковому edge віддає пріоритет бакетам ближчим до прогнозу.
4. ✅ **Виправлено логіку grid-ліміту в `main.py`**: ліміт `SNIPER_GRID_MAX_MARKETS_PER_CITY` застосовується тільки до grid/adjacent-grid угод; звичайні `SNIPER YES` користуються `MAX_POSITIONS_PER_CITY`.
5. ✅ **Верифікація**: `python3 -m compileall /home/troyan/Mega/Project/OpenCode/WeatherBot`, import smoke та synthetic forecast-ladder smoke-test пройдені; тест показав 5 tradeable бакетів навколо прогнозу 17.0°C.

## Реалізовані виправлення (18.06.2026 — SQLite fallback + більше угод + логи, v12)
1. ✅ **SQLite fallback для trade_log**: Коли Render PostgreSQL expire (25.06.2026), бот автоматично переключається на локальний SQLite (`data/bot_trades.db`). Безкоштовно, без терміну дії. Функція `_log_trade_to_sqlite()` викликається автоматично коли PostgreSQL недоступний.
2. ✅ **Збільшено ліміти позицій**: 
   - `MAX_ACTIVE_POSITIONS`: 8 → 12 (більше одночасних угод)
   - `MAX_OPEN_PER_CYCLE`: 3 → 4 (більше угод за цикл)
   - `RESERVED_FAST_SLOTS`: 2 → 1 (slow слотів: 6 → 11)
   - `MAX_POSITIONS_PER_CITY`: 4 → 5 (сітка до 5 бакетів на місто)
3. ✅ **Радикально зменшено логування**:
   - SKIP повідомлення в `edge_calculator.py`: INFO → DEBUG (було ~100 SKIP/цикл, тепер 0)
   - Список ринків у `market_scanner.py`: 12→5, INFO→DEBUG
   - Економить диск на Render free tier
4. ✅ **Верифікація**: compileall + import + SQLite fallback test — всі пройшли.

### Стан бота після v11 (18.06.2026 03:43)
- 16-21 tradeable ринків (було 1!) — сітка працює
- 6 відкритих позицій, Unrealized +$2.52 (+21.8% на вкладене)
- ROI +2.5%, Drawdown 1.2% — здоровий стан
- Проблема: слоти 6/6 повні → v12 збільшує до 11 slow слотів

### Рекомендація по PostgreSQL
Render PostgreSQL expire 25.06.2026. Варіанти:
1. **SQLite fallback** (вже додано) — працює автоматично, безкоштовно
2. **Supabase** (supabase.com) — безкоштовний PostgreSQL 500MB, без терміну
3. **Neon** (neon.tech) — безкоштовний PostgreSQL 0.5GB, без терміну
Для DRY-RUN тесту SQLite достатньо. Для LIVE — рекомендується Supabase.

## Реалізовані виправлення (17.06.2026 — Forecast Ladder Grid Unlock, v11)
1. ✅ **Розблоковано forecast ladder grid (сітку прогнозів)**: Знижено over-calibration для categorical бакетів, яка блокувала 99.8% ринків (1 tradeable з 605). Тепер бот може будувати сітку 16/17/18/19°C навколо прогнозу 17.0°C.
2. ✅ **Categorical discount знижено з 0.30 до 0.55** (edge_calculator.py + data_fetcher.py): Було 0.30 (знижка 70%) → 0.55 (знижка 45%). Емпірична вага піднята з 0.20 до 0.30, параметрична знижена з 0.80 до 0.70. Для нульової емпірики: 0.15 → 0.30. Це збільшило our_prob для бакетів поблизу прогнозу в 2.1-2.6 рази (наприклад, бакет 17°C при прогнозі 17.0°C: 11.3% → 24.3%).
3. ✅ **Знижено пороги grid**: 
   - `EXTREME_TAIL_MIN_EDGE_YES`: 0.04 → 0.02
   - `SNIPER_GRID_MIN_EDGE`: 0.03 → 0.02
   - `SNIPER_GRID_MIN_PROB`: 0.03 → 0.02
   - `ADJACENT_GRID_MIN_EDGE`: 0.03 → 0.02
   - `ADJACENT_GRID_MIN_PROB`: 0.03 → 0.02
4. ✅ **Піднято prob caps для exact бакетів**:
   - `PROB_CAP_EXACT_SHORT`: 0.40 → 0.50
   - `PROB_CAP_EXACT_MID`: 0.32 → 0.38
   - `PROB_CAP_EXACT_LONG`: 0.25 → 0.30
5. ✅ **Збільшено `SNIPER_GRID_MAX_MARKETS_PER_CITY`**: 4 → 5 (сітка до 5 бакетів на місто)
6. ✅ **Зменшено `SCAN_INTERVAL_SEC`**: 600 → 300 (частіше сканувати)
7. ✅ **Зменшено адаптивний сон**: 900s → 300s (в main.py)
8. ✅ **Верифікація**: `python3 -m compileall` пройшов; smoke test сітки 17.0°C → 16/17/18 показав our_prob 8.8%/24.3%/8.3% (було ~4%/11%/5%), бакет 16°C з ask=0.03 тепер tradeable з edge +5.8%.

### Діагноз з логу (17.06.2026 12:50-14:26)
- Бот знайшов лише **1 tradeable з 605 ринків** (99.8% skip)
- **0% win rate** (0 виграшів з 6 resolved), PnL -$9.00 (-9.8%)
- our_prob занадто мала (0.01-0.05) через over-calibration (множник 0.30)
- Бот купував лише екстремальні хвости (Lucknow "above 42°C") які рідко виграють
- Адаптивний сон 900s (15 хв) — пропускав можливості

### Очікуваний результат v11
- Сітка бакетів поблизу прогнозу (±1-2°C) з дешевими ask (1-15¢)
- our_prob 8-25% для основних бакетів (було 3-11%)
- Більше tradeable ринків (очікувано 5-15 замість 1)
- Win rate 30-40% на дешевих сусідніх бакетах
- Частіше сканування (5 хв замість 10-15)

## Реалізовані виправлення (13.06.2026 — Daily loss equity fix + price validation)

## v13 — PROFITABLE SNIPER GRID (22.06.2026)

### Діагноз перед v13
- **0% win rate** (0 wins, 1 resolved loss, -$0.20)
- Капітал: $83.54 (з $100), ROI -16.5%
- 11/12 слотів заповнені — нові угоди не відкриваються
- Дешеві хвости 5-6¢ з our_prob 9-14% — лотерейні квитки, не +EV
- Over-calibration: prob_exact при прогнозі 17.0°C → our_prob=0.11 (реально ~20-30%)
- Kelly вимкнено — фіксований $1.50 не оптимальний

### Ключові зміни v13

**1. config.py — Реалістичні prob caps + якісні пороги:**
- `PROB_CAP_EXACT_SHORT`: 0.50 → 0.60
- `PROB_CAP_EXACT_MID`: 0.38 → 0.48
- `PROB_CAP_EXACT_LONG`: 0.30 → 0.38
- `PROB_CAP_ABOVE_SHORT`: 0.75 → 0.80
- `PROB_CAP_ABOVE_MID`: 0.68 → 0.72
- `PROB_CAP_ABOVE_LONG`: 0.60 → 0.65
- `EXTREME_TAIL_MIN_ASK_YES`: 0.05 → 0.08 (відсіяти мертві хвости <8¢)
- `EXTREME_TAIL_MAX_ASK_YES`: 0.25 → 0.55 (фокус на бакети 8-55¢)
- `EXTREME_TAIL_MIN_EDGE_YES`: 0.02 → 0.04 (реальний edge, не лотерея)
- `SNIPER_GRID_MIN_ASK`: 0.05 → 0.08; `SNIPER_GRID_MAX_ASK`: 0.75 → 0.60
- `SNIPER_GRID_MIN_EDGE`: 0.02 → 0.04; `ADJACENT_GRID_MIN_EDGE`: 0.02 → 0.03
- `SNIPER_GRID_DISTANCE_C`: 4.0 → 2.5 (сітка ±1°C, не ±2°C)
- `MIN_EDGE_ENTRY`: 0.03 → 0.05; `MIN_PROB_ENTRY`: 0.03 → 0.05
- `MAX_ACTIVE_POSITIONS`: 12 → 10 (less = more quality)
- `MAX_POSITION_USD`: 3.0 → 4.0; `MAX_POSITION_PCT`: 0.03 → 0.04
- `USE_KELLY`: False → True; `KELLY_PROB_CAP` = 0.60 (новий параметр)
- `COMPOUND_RISK_PCT`: 0.02 → 0.03
- `MAX_TOTAL_EXPOSURE_PCT`: 0.50 → 0.55 (сітка потребує більше експозиції)
- `STOP_LOSS_PCT`: 0.13 → 0.15 (дати сітці дихати)
- `GRID_FORECAST_STEP_C`: 0.1 → 1.0; `GRID_FORECAST_SPAN_C`: 0.2 → 2.0

**2. data_fetcher.py — Зменшено over-calibration:**
- `prob_exact_temp_c`: empirical 0.30→0.40, parametric 0.70→0.60, discount 0.55→0.75
- Нульова емпірика: 0.30→0.50
- `raw_prob_above_temp_c`: empirical 0.20→0.30, parametric 0.80→0.70

**3. edge_calculator.py — Kelly sizing + оновлені пороги:**
- `_is_extreme_tail_yes_market`: оновлено під 8-55¢ замість 5-15¢
- `calculate_edge`: додано Quarter-Kelly position sizing (пік бакети ×1.3)
- Adjacent grid: Kelly sizing ×0.70 (хвоти сітки — менші ставки)
- Range ринки: discount 0.55→0.75,零零 емпірика 0.30→0.50

**4. trader.py — Швидший resolution + Kelly cap:**
- Stale timeout: 48h → 30h (швидше звільняти слоти)
- Non-weather stale: 48h → 36h; EXPIRED 48h → 36h
- `KELLY_PROB_CAP`: 0.55 → 0.60 (через config)

### Smoke test результати (прогноз 17.0°C, бакет 17°C):
- v12: our_prob=0.143; v13: our_prob=0.195
- ask=0.08: edge=+11.5%, Kelly=$3.12 ✅
- ask=0.15: edge=+4.5% > MIN_EDGE=4% ✅
- ask=0.20: edge=-0.5% — відсіяно ✅

### Очікуваний результат v13
- Сітка бакетів 8-30¢ з реалістичними our_prob (15-30%)
- Kelly sizing: більші позиції на високий edge, менші на хвости
- Win rate 35-50% (порівняно з 0% на v12)
- Швидше resolution (30h замість 48h = менше забитих слотів)
1. ✅ **Виправлено хибний daily-loss circuit breaker**: `safeguards.py` тепер рахує `daily_loss` від equity (`cash + portfolio_value`), а не від cash. `_reset_daily_counters_if_needed()` зберігає baseline equity, тому unrealized PnL не губиться і не рахується двічі.
2. ✅ **Усунено подвійний підрахунок PnL**: `BotState.equity` повертає `cash + portfolio_value`; `trader.get_portfolio_summary()` вже містить unrealized PnL у `portfolio_value`, тому додавати `unrealized_pnl` окремо не потрібно.
3. ✅ **Додано price validation для YES-цін**: `edge_calculator.py` тепер валідує `best_ask_yes`, `best_bid_yes`, `midpoint_yes` на скінченність і діапазон `[0, 1]`, але не відкидає cheap-tail `0.003–0.004` як фантом без окремої перевірки Gamma API.
4. ✅ **Пояснено `skip: city=99`**: це не код міста; у логах це кількість ринків, відсіяних через `CITY_WHITELIST`, а не Polymarket city id.
5. ✅ **Верифікація**: `python3 -m compileall /home/troyan/Mega/Project/OpenCode/WeatherBot` пройшов; smoke-test для `_valid_yes_price()` і `_detect_city()` пройшов.
6. ✅ **Висновок після незалежної перевірки (13.06.2026)**: v10.2 готовий до 7-денного DRY-RUN; перед LIVE обов'язково запустити backtest і не переходити на реальні гроші без Sharpe > 1.0, win rate > 45%, ≥50 resolved trades.

## v14 — ALTEREGOETH INTEGRATION (22.06.2026)

### Діагноз перед v14
- v13 на render.com: 0% win rate (0/6), PnL -$9.27, capital $78.20, ROI -6.6%
- 8 open positions з unrealized PnL +$1.42 to +$3.11
- Hardcoded sigma — не адаптується до реальних помилок прогнозу
- Немає механізму захисту прибутку (тільки stop-loss на збитки)
- Позиції не закриваються коли прогноз суттєво змінився

### Ключові зміни v14 (4 фічі alteregoeth)

1. **Self-calibrating sigma** (`sigma_calibrator.py` — НОВИЙ файл):
   - Зберігає прогнозні помилки per city per source у `DATA_DIR/sigma_calibration.json`
   - Обчислює rolling RMS (мин 5, макс 50 семплів)
   - Blended sigma: 60% adaptive RMS + 40% hardcoded base sigma
   - `hour_factor` для scaling залежно від горизонту прогнозу
   - `data_fetcher._get_sigma()` → викликає `get_adaptive_sigma()` (fallback на hardcoded)

2. **Trailing stop** (breakeven at +20%):
   - Нові поля Position: `peak_price`, `trailing_stop_activated`
   - Коли PnL ≥ +20% → активується trailing stop на рівні entry_price
   - Якщо після активації цена падає нижче entry → закриває з прибутком ~0%
   - Захищає від повернення прибутку у мінус

3. **Forecast-shift close** (2+°C shift):
   - Нові поля Position: `forecast_at_entry_c`, `threshold_at_entry_c`
   - Кожен цикл: бере поточний прогноз → якщо змістився на 2+°C від entry
   - додаткова перевірка: bucket віддалився від прогнозу на >30%
   - Закриває позицію早期, поки ціна не впала

4. **Dynamic take-profit** (hold-duration based):
   - <24h hold: не продаємо (даємо час)
   - 24-48h hold: продаємо при price ≥ 0.85
   - 48h+ hold: продаємо при price ≥ 0.75
   - Забирає прибуток швидше на старих позиціях

### Додаткові зміни
- `Position` dataclass: +4 поля (peak_price, trailing_stop_activated, forecast_at_entry_c, threshold_at_entry_c)
- `Position.update_pnl()`: оновлює peak_price
- `safeguards.py`: серіалізація/десеріалізація +4 нових полів
- `check_and_close_positions()`: 3 нових виходи перед return (trailing stop → dynamic TP → forecast-shift)
- Sigma recording: `_try_record_sigma()` + `_record_sigma_on_resolution()` після `pos.resolve()` у всіх 3 місцях (check_and_close, force-close, cleanup_stale)
- `config.py`: +12 параметрів v14 (SIGMA_CAL_*, TRAILING_STOP_*, FORECAST_SHIFT_CLOSE_*, DTP_*)

### Очікуваний результат v14
- Adaptive sigma зменшить over/under-estimate ймовірностей
- Trailing stop захистить +20% прибуток від зливу
- Forecast-shift закриє позиції рано коли прогноз змінився
- Dynamic TP забере прибуток на старих позиціях (≥0.75-0.85)
- Ціль: win rate 40-55%, ROI позитивний за 7 днів DRY_RUN

## v15-v19 — METAR ARBITRAGE SNIPER (29.06.2026 — 01.07.2026)

### Стратегічний півот на METAR Arbitrage
Після невдачі v9-v14 (0% win rate на forecast-bets, дешевих хвостах та сітках), стратегія повністю переписана на **METAR Arbitrage Sniper** — купівля YES коли ФАКТИЧНА спостережена температура вже підтверджує напрямок ринку, але ринок ще не переоцінив ціну.

### v15 — Базовий METAR Arb (29.06.2026)
1. ✅ **METAR_ARB_ENABLED=True** — нова стратегія замість Grid/Sniper
2. ✅ `METAR_ARB_REQUIRE_METAR=True` — тільки METAR-підтверджені угоди
3. ✅ `METAR_ARB_KINDS_ONLY=["above","below","range","categorical"]` — всі типи
4. ✅ `METAR_ARB_MIN_ASK=0.08`, `MAX_ASK=0.70` — ліквідні ринки
5. ✅ `METAR_ARB_MIN_EDGE=0.04`, `MIN_PROB=0.45`, `MIN_PROB_RANGE=0.15`
6. ✅ `PROB_RATIO_MAX_METAR=12.0`, `PROB_RATIO_MAX_NO_METAR=6.0`
7. ✅ `MARKET_ANCHOR_WEIGHT=0.10` — мінімальне згасання edge
8. ✅ `MAX_ACTIVE_POSITIONS=5`, `MAX_OPEN_PER_CYCLE=1`
9. ✅ `_check_metar_confirmation()` — нова функція в edge_calculator.py
10. ✅ `_climate_sanity_check()` — 22-місний кліматичний довідник
11. ✅ `MIN_RESOLUTION_HOURS=1.0`, `MAX_RESOLUTION_HOURS=12`
12. ✅ `STOP_LOSS_MIN_HOLD_HOURS=0.5` — SL не працює першу півгодини

### v16 — ROOT-CAUSE AUDIT (29.06.2026, пізніше)
**Проблема:** `get_best_forecast()` змішував METAR поточну температуру у weighted average для **денної максимальної** температури з вагою 40-60%. METAR = ранкова температура аеропорту (~19.6°C), а ринки резолвляться по **daily high** (~24°C). → всі ринки виглядали без edge → `none=493`.

**Фікс 1 (data_fetcher.py:838-879):** METAR повністю видалено з weighted forecast. Тільки для confirmation через `_check_metar_confirmation`. Нові ваги:
- ≤8h: ensemble 50%, GFS 25%, ECMWF 15%, NOAA 10%
- ≤12h: ensemble 45%, GFS 25%, ECMWF 15%, NOAA 15%
- >12h: ensemble 45%, NOAA 25%, GFS 15%, ECMWF 10%, NASA 5%

**Фікс 2 (edge_calculator.py:618-628):** Market anchor пропускається при високій впевненості (METAR+OBSERVED або ENSEMBLE source).

**Фікс 3 (edge_calculator.py:677-686):** PHANTOM filter дозволяє угоди при `metar_confirmed=True`.

**Фікс 4 (config.py:58-59):** `PROB_RATIO_MAX_METAR: 7.0→12.0`, `PROB_RATIO_MAX_NO_METAR: 3.0→6.0`.

### v17 — FORECAST-BET BUG FIX (01.07.2026)
**Проблема:** `_check_metar_confirmation()` для range використовував `best_evidence = max(metar_temp, obs_high, fc_high)`. Коли день ще не досяг піку, `fc_high` ставав найбільшим і хибно "підтверджував" вхід — перетворюючи METAR-arb на forecast-bet (який вже провалився в v14).

**Доведено 2 збитковими угодами:**
- Houston 96-97°F: obs_hi=34.3°C (BELOW bucket), fc_high=35.9°C (IN bucket) → max=35.9 → "confirmed" → LOSS
- LA 72-73°F: obs_hi=21.5°C (BELOW bucket), fc_high=22.0°C (IN bucket) → max=22.0 → "confirmed" → LOSS

**Фікс (edge_calculator.py):** `fc_high`/`fc_low` видалено з `candidates` для ВСІХ kind (above/below/range/categorical). Тепер тільки `max(metar_temp, obs_high)` — фізична істина, не прогноз.

### v18 — STRICT METAR ARBITRAGE + LOG REDUCTION (01.07.2026)
**Проблема:** v17 фікс був недостатній — `buffer_c=1.2°C` симетрично до lower bound range все ще дозволяв forecast-bets. Math: LA obs=21.5°C, range_low=22.22, (22.22-1.2)=21.02 ≤ 21.5 → TRUE → confirmed → LOSS.

**Фікс 1 (edge_calculator.py) — STRICT METAR-арбітраж:**
- **above**: `confirmed = best_evidence >= threshold_c` (без buffer)
- **below**: `confirmed = best_evidence_cold <= threshold_c` (без buffer)
- **range**: `confirmed = range_low_c <= best_evidence <= range_high_c` (НІ buffer знизу, НІ зверху — obs вище range_high = ринок вже програв)
- **categorical**: залишив buffer (точкове значення потребує толерансу), тільки METAR/observed

**Фікс 2 (trader.py) — timezone fix для DRY-RUN resolution:**
- `get_target_date()` повертає LOCAL calendar date. Було: `day_completed = datetime(target_date, UTC) + 26h` — для LA (UTC-7) резолвився о 02:00 UTC, а день LA закінчується о 07:00 UTC = 5h завчасно.
- Стало: `day_completed = datetime(next_day, UTC) - timezone_offset + 3h_buffer`. Додано `_CITY_TZ_OFFSETS` dict.
- Тест: LA резолвиться о 10:00 UTC July 1 (correct), Tokyo о 18:00 UTC June 30 (correct).

**Фікс 3 — LOG REDUCTION (3 файли):**

*edge_calculator.py:*
- Видалено `METAR NOT CONFIRMED` debug (~500/цикл)
- Видалено `PHANTOM (no METAR)` debug
- Видалено `PROB-RATIO exceeded` debug
- `EDGE: {question}` → INFO тільки для tradeable edges (було ~600/цикл)
- Залишено `PHANTOM+METAR` INFO (рідкісне, важливе)
- Залишено всі forecast-bet rejected логи (above/below/range/categorical)
- Залишено scan summary: `Edge scan: N tradeable / M ринків | skip: ...`

*trader.py:*
- Додано `_suppressed_logs: set` — повторювані повідомлення логуються 1 раз за позицію
- `Market is still OPEN`, `SL deferred`, `Resolution чекає end_date`, `MTM: no price` — всі suppressed після першого логу
- Автоочищення при >200 записів

*main.py:*
- `Дублікат по питанням` → консолідований лічильник (1 рядок/цикл)
- `Ліміт міста` → консолідований лічильник
- **Виправлено баг**: `min(300, hours*300)` → `min(3600, max(300, hours*300))` (пропорційний сон)
- Прогресивний сон: 3-5 порожніх → 600s, 6-11 → 900s, 12+ → 1800s

### v19 — RECOMMENDATION ACCEPTED + METAR-CONFIRMED DISTANCE BYPASS (01.07.2026)

**Лог 09:55 UTC показав:** 0 tradeable, 13 збитків (0/13 win rate), ROI -7.1%. v18 strict правильно відхиляв forecast-bets (SF range: `obs_hi=12.4°C outside [22.2, 22.8]`), але бот не знаходив ЖОДНОЇ прибуткової угоди. `none=452` з 627 ринків.

**Прийнято рекомендацію користувача (Recomend.md) — 2 зміни в config.py:**
```python
METAR_ARB_MIN_PROB: float = 0.35       # was 0.45
METAR_ARB_KINDS_ONLY: ["above", "below"]  # was + range, categorical
```

**Чому погодився:**
1. Обидва збитки (Houston, LA) були **range**-ринки → прибираємо range назавжди
2. З v18 strict, range потребує `obs in bucket` (температура ВЖЕ в бакеті) — рідкісно, бо бот сканує коли день ще не пік
3. above/below легше підтвердити: `obs >= threshold` або `obs <= threshold` — перетин порогу, не влучання у вузький бакет
4. MIN_PROB 0.45→0.35 розблокує легітимні METAR-підтверджені ринки де our_prob помірний (0.35-0.44)

**Додаткова зміна (edge_calculator.py:706) — METAR-confirmed distance bypass:**
```python
# Було:
and distance_c <= max_dist
# Стало:
and (metar_confirmed or distance_c <= max_dist)
```
**Чому:** `distance_c` — відстань ПРОГНОЗУ від порогу. METAR-підтверджена угода — фізична істина, не потребує прогнозної близькості. Без цього фікс навіть METAR-confirmed above ринок міг бути відхилений через `distance_c > 5.0`.

### Стан бота після v19 (01.07.2026 09:55 UTC)
- DRY-RUN, капітал $92.85, ROI -7.1%, drawdown 7.5%
- 13 resolved trades: 0 wins, 13 losses (всі були forecast-bets до v17/v18 фіксів)
- 0 active positions, 11 порожніх циклів поспіль
- PostgreSQL active (Neon), стан відновлено
- Очікування: 0-3 tradeable edges/cycle (above/below METAR-confirmed)
- Потенційний WR 50%+ (фізична істина > прогноз)

### Ключові файли v19 (для ручного пушу на GitHub → Render)
1. `config.py` — METAR_ARB_MIN_PROB=0.35, METAR_ARB_KINDS_ONLY=["above","below"]
2. `edge_calculator.py` — strict METAR v18 + log reduction + distance bypass
3. `trader.py` — timezone DRY-RUN fix + _suppressed_logs
4. `main.py` — consolidated counters + progressive sleep + min/max bug fix

### Deployment notes
- **GitHub = MANUAL PUSH** (підтверджено 2026-06-04). НЕ використовувати git commit/push/gh.
- **Render: RESET_POSITIONS=false** (зберегти капітал $92.85, 0 позицій)
- Бот на Render.com free tier, DRY-RUN
- `METAR_ARB_TEMP_CONFIRM_C=1.2` ще в config.py, тепер ТІЛЬКИ для categorical branch

### Наступні кроки
1. Зачекати 25-30 resolved trades з v19 для статистичної валідації (95% CI для 0/12 = 0-26%, недостатньо)
2. Якщо WR < 40% → знизити MIN_PROB до 0.30
3. Якщо WR > 50% → готовий до LIVE з $20
4. Запустити `calibrate_model.py` (з SQL JOIN OPEN+CLOSE) після 20+ trades

### Архітектура v19 (ключові функції)
- `edge_calculator.py:326` — `_check_metar_confirmation()` (v18 strict, без forecast в candidates)
- `edge_calculator.py:452` — `_boost_prob_from_metar()` (METAR boost +8-10%)
- `edge_calculator.py:489` — `_climate_sanity_check()` (22-місний довідник)
- `edge_calculator.py:575` — `calculate_edge()` (головний розрахунок edge)
- `edge_calculator.py:647` — market anchor skip при high confidence
- `edge_calculator.py:660` — METAR confirmation call
- `edge_calculator.py:706` — distance bypass для METAR-confirmed (v19)
- `edge_calculator.py:760` — `scan_all_edges()` з skip counters
- `edge_calculator.py:809` — scan summary log
- `data_fetcher.py:84` — `raw_prob_above_temp_c()` (30% empirical + 70% parametric)
- `data_fetcher.py:829` — `get_best_forecast()` (METAR removed from weights v16)
- `data_fetcher.py:763` — `_fetch_observed_daily_extremes()` (Open-Meteo hourly)
- `data_fetcher.py:980` — `fetch_historical_extreme()` (archive API для DRY-RUN resolution)
- `trader.py:153` — `check_market_resolved()` (timezone fix v18)
- `trader.py:352` — `place_trade()` (Kelly quarter-scale)
- `trader.py:612` — `check_and_close_positions()` (SL, trailing, TP, forecast-shift)
- `main.py:220` — `run_scan_cycle()` (duplicates/city counters v18)
- `main.py:468` — main loop
- `main.py:517` — adaptive sleep (progressive v18)

## v20 — CLEAN ENSEMBLE PREDICTION STRATEGY (03.07.2026)

### Проблеми (лог 03.07):
1. **"Немає прогнозу для {city}"** для ВСІХ міст — всі API повертають None, помилки на DEBUG рівні невидимі
2. **0% win rate (0/14 resolved, 14 losses)** — METAR arb стратегія концептуально неспроможна (0% WR, ROI -8.4%)
3. **Aviationweather.gov timeout** — METAR API недоступний з Render

### Зміни:

#### config.py — повна переробка:
- Видалено ВСІ v15-v19 параметри: METAR_ARB_*, ENABLE_COMPOUND, COMPOUND_RISK_PCT, TRAILING_STOP_*, FORECAST_SHIFT_*, DYNAMIC_TP_*, PROBABILITY_CALIBRATION_*, MARKET_ANCHOR_*, SNIPER_GRID_*, EXTREME_TAIL_*, ADJACENT_GRID_*, SIGMA_CAL_*
- Нова стратегія: ENSEMBLE prediction (above/below only)
- Додані: MIN_EDGE_YES=0.20, MIN_EDGE_NO=0.20, PROB_BIAS=0.75, CAP_SHORT/MID/LONG, KINDS_ONLY
- 202 рядки → 75 рядків

#### data_fetcher.py:
- Всі logger.debug API помилки → logger.warning (тепер видимі з LOG_LEVEL=INFO)
- Додано test_all_apis() — діагностика всіх джерел при старті

#### edge_calculator.py — повна переробка:
- Видалено: _check_metar_confirmation(), _boost_prob_from_metar(), _climate_sanity_check(), _apply_probability_calibration()
- Нова calculate() — чиста ensemble ймовірність + calibration bias 0.75
- Підтримка BUY_NO (edge_no = market_prob - our_prob)
- Тільки above/below ринки (без range/categorical)

#### trader.py:
- Видалено: _check_dynamic_take_profit(), _check_forecast_shift_close()
- Спрощено decide_position_size() — тільки Kelly flat 25%
- Замінено _parse_range_or_threshold → _parse_threshold для resolution

#### main.py:
- Додано API тест при старті (test_all_apis)
- Оновлено банер v20
- Видалено ENABLE_COMPOUND/COMPOUND_RISK refs

### Файли для ручного пушу:
- config.py, data_fetcher.py, edge_calculator.py, trader.py, main.py

### Render: RESET_POSITIONS=true (чистий старт, нова стратегія)
### Очікування: 25-30 resolved trades для валідації WR 40%+

## v26 — COLDMATH LADDER (13.07.2026)

### Діагноз перед v26 (лог 13.07.2026 ~12:19 UTC)
- Equity ~$90, ROI −9.3%, **0% WR (0 wins / 8 losses)**, realized −$11
- 12/12 слотів забиті lottery-хвостами: dist=2.1–3.4°C, our_prob 6–9%, ask 1–3¢
- v25 вимкнув `MAX_TAIL_PROB` / distance×prob → знову 0% materialize-rate
- Сортування за raw edge ставило phantom tails (Dallas 94°F+ edge 33%) вище peak ladder
- Unrealized +$20 на thin books — ілюзія (LESSON: не trust MTM tails)

### Стратегія (coldmath / neobrother)
Прогноз **17.0°C** → сітка YES на **16 / 17 / 18** (±1.5°C; F-range: сусідні 1°F бакети).
Малі ставки на крилах, більший size на peak. Один WIN покриває 3–4 LOSS.

### Ключові зміни
| Файл | Зміна |
|------|--------|
| `config.py` | SNIPER_GRID_DISTANCE_C=**1.5**, MAX_TAIL_PROB=**0.08**, cheap ratio≥**2.2×**, min_prob peak/near/far=10/12/15%, peak/wing size mult, MAX_OPEN=5, slots=15 |
| `edge_calculator.py` | distance-aware quality gates; `_coldmath_rank` (peak-first); per-city ladder cap; `🪜 LADDER` log; size suggestion |
| `trader.py` | peak/wing Kelly; blend suggested size; lower cap на wings |
| `main.py` | city ladder completion boost; exposure on open cost not MTM; strategy=`COLDMATH_LADDER`; banner v26 |

### Що свідомо НЕ робимо (уроки)
- ❌ METAR arb (v15–v19 → 0% WR)
- ❌ SIGMA_MIN 1.5 (v21) або 4.5 (v22 overcorrection)
- ❌ dist=4°C + 1¢ lottery (v23–v25)
- ❌ sort by edge only
- ❌ trust unrealized PnL on 1–3¢ books

### Smoke test (локально, 13.07)
- fc=17.0°C → tradeable buckets **{16,17,18}**; 14/15/19/20 blocked
- Far lottery dist=2.9 blocked
- RANK peak > phantom tail
- SIZE peak ≥ wing
- range parse 100–101°F → never −101 bug

### Deploy
1. Manual push to GitHub (NO auto git commit/push)
2. Render: `RESET_POSITIONS=true` один раз (скинути 0% WR junk + drawdown), потім `false`
3. DRY_RUN=true мінімум **7 днів / 30 resolved**
4. LIVE тільки якщо WR≥28% і ROI≥+5% (config gates)

### Очікування v26
- 5–15 tradeable/cycle (якість > кількість)
- Ladder logs: `🪜 LADDER Tokyo fc=35.0°C: 34C@… | 35C@… | 36C@…`
- Realized WR 25–40% на ladder; ROI додатній за рахунок cheap peak asymmetry


