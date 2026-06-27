# WORKSPACE.md — Поточний стан сесії

## Активна сесія
- Дата: 2026-06-12
- Ціль: CopyBot — аналіз логів, оптимізація, моніторинг
- Статус: ✅ Бот стабільний, DRY_RUN, 5 китів

## Поточний стан CopyBot

### Архітектура (станом на 12.06.2026)
- **Entry point:** `uvicorn web.app:app` (port $PORT)
- **DB:** PostgreSQL на Render Free (asyncpg)
- **Scheduler:** APScheduler 5 jobs (copy, positions, balance, daily_reset, heartbeat)
- **Scan:** inline всередині `_job_copy` (не окремий job)
- **Scoring:** Two-phase: `_discovery_t()` (soft) + `_copy_t()` (strict)

### Ключові параметри
| Параметр | Значення |
|----------|----------|
| DRY_RUN | `True` |
| SCAN_INTERVAL_SECONDS | 1800 (30 хв) |
| COPY_INTERVAL_SECONDS | 900 (15 хв) |
| MAX_TRADE_USD | $3 |
| MAX_POSITION_PCT | 4% |
| MIN_WHALE_WIN_RATE | 0.55 |
| MIN_CONFIDENCE_SCORE | 0.40 |
| MIN_WHALE_VOLUME_USD | 1000 |
| TRACK_TOP_N_WHALES | 5 |
| ENABLE_REVERSE_COPY | True |
| RESET_SIGNALS | false (після тесту) |

### Активні кити (5)
1. `0x780a7539` — WR=83%, PF=52.6, $921k vol (елітний)
2. `0x4328b28f` — WR=55%, PF=1.2, $123k vol
3. `0x29b7a9a6` — активний
4. `0x35353a3a` — активний
5. `0x7815aee0` — активний

### Деплой
- **GitHub:** `Didrow/copybot-polymarket` (приватний)
- **Render:** `copybot-6b41.onrender.com` (Free tier)
- **Cron-job:** keep-alive кожні 15 хв
- **Dashboard:** `/api/trades`, `/api/whales`, `/api/stats`

### Нотифікації
- ТІЛЬКИ через webhook (Discord/Slack/Generic)
- Gmail/Telegram НЕ використовуються

## Інші проекти
- **WeatherBot:** DRY_RUN=true, weather prediction markets
  - 12.06.2026: виправлено калібрування ймовірностей для above/below ринків, суміжних бакетів Adjacent Grid та подвійний підрахунок експозиції в `main.py`; `compileall` + import smoke пройдені.
  - 12.06.2026: розблоковано forecast ladder grid 17.0 → 16.8/16.9/17.0/17.1/17.2: `SNIPER_GRID_MAX_MARKETS_PER_CITY=5`, `MAX_POSITIONS_PER_CITY=5`, додано distance tie-breaker, виправлено grid-ліміт у `main.py`; `compileall` + synthetic smoke пройдені.
  - 13.06.2026: виправлено хибний `daily_loss` у `safeguards.py` — тепер рахується по equity (`cash + portfolio_value`), без подвійного додавання unrealized PnL; додано price validation у `edge_calculator.py`; `compileall` + price smoke пройдені.
  - 13.06.2026: `skip: city=99` розшифровано як кількість відсіяних ринків по `CITY_WHITELIST`, а не як код міста.
  - 13.06.2026: після перевірки аналізу — WeatherBot v10.2 готовий до 7-денного DRY-RUN; перед LIVE потрібен backtest: Sharpe > 1.0, win rate > 45%, ≥50 resolved trades.
  - **26.06.2026: v14.4 — Довіра ринку (MARKET_ANCHOR_WEIGHT 0.10→0.35) — виправлення систематичної переоцінки екстремальних температур:** `config.py` піднято `MARKET_ANCHOR_WEIGHT` до 0.35 (анкор до 40¢), `PROB_DISTANCE_POWER` 0.3→0.5 (крутіше спадання), `SNIPER_GRID_MIN_EDGE_NORMAL` 0.18→0.05 (розблокування нормальних ринків); `trader.py` додано логування sigma_calibrator + виправлено `True`→`pos.pnl_usd > 0`; `edge_calculator.py` видалено мертвий код `raw_edge`/`eff_edge` до calibration.
  - **27.06.2026: v14.5 — Фантомний edge + sigma calibrator діагностика:**
    - `edge_calculator.py`: додано hard filter `⛔ PHANTOM EDGE` (ПІСЛЯ `_grid_tradeable`, не до — fix 27.06 08:15) — якщо `market_prob < 1¢` + `our_prob > 15%` → skip
    - `data_fetcher.py`: `fetch_historical_extreme` — додано `past_days` fallback для forecast API (Open-Meteo не дає архів через `start_date` для минулих днів; `past_days=N` працює); покращено логування: `warning` замість `debug` для помилок + null values + empty arrays
    - `trader.py`: sigma skip логі піднято з `debug` до `warning` — тепер видно чому sigma не калібрується
- **B2B Lead Agent:** nanobot + Gemini API
- **UA Skills:** 30+ скілів для opencode (юрист, лікар, продажі тощо)

## Файли для ручного push
Дивись `git status` або `git diff` — надається список змінених файлів.

## Verification
- ✅ `pytest tests/` — 23/23 passed
- ✅ `ruff check` — чисто
- ✅ `python -m compileall` — OK
