# 🌤️ Polymarket Weather Bot 2026

Автономний торговий агент для **weather-ринків Polymarket**.  
Працює 24/7, використовує безкоштовні API прогнозів погоди, знаходить статистичний edge між прогнозом та ринковою ціною.

---

## ⚠️ ДИСКЛЕЙМЕР

> Торгівля на ринках передбачень — **висококризикована діяльність**.  
> Ви можете **втратити весь капітал ($100)**. Починай з DRY-RUN режиму.  
> Це не фінансова порада. Автор не несе відповідальності за збитки.

---

## 🏗️ Архітектура

```
polymarket_weather_bot/
├── main.py              ← Головний цикл 24/7 (ЗАПУСКАТИ ЦЕЙ ФАЙЛ)
├── config.py            ← Всі налаштування бота
├── data_fetcher.py      ← NOAA + Open-Meteo прогнози (безкоштовно)
├── market_scanner.py    ← Пошук weather ринків через Gamma API
├── edge_calculator.py   ← Порівняння прогноз vs ринок → edge
├── trader.py            ← Volatility Targeting + Kelly + позиції
├── safeguards.py        ← Circuit breakers, drawdown, статистика
├── security.py          ← Bot Bible 2026: безпека гаманця
├── osint_module.py      ← Whale tracking, insider detection
├── notifier.py          ← Telegram алерти (опціонально)
├── requirements.txt     ← Python залежності
├── .env.example         ← Шаблон конфігурації (скопіюй у .env)
└── .gitignore           ← НІКОЛИ не завантажуй .env у Git!
```

---

## 🚀 Швидкий старт (5 кроків)

### Крок 1: Встановлення

```bash
git clone <твій_репо> && cd polymarket_weather_bot

# Встановити Python залежності (всі безкоштовні)
pip install -r requirements.txt
```

### Крок 2: Налаштування .env

```bash
cp .env.example .env
nano .env   # або будь-який текстовий редактор
```

Заповни:
```env
# ТІЛЬКИ від DEDICATED trading wallet (не основний!)
PRIVATE_KEY=0xYOUR_DEDICATED_WALLET_PRIVATE_KEY_HERE

# Залиш пустими — бот створить автоматично
POLY_API_KEY=
POLY_API_SECRET=
POLY_API_PASSPHRASE=

# Telegram (опціонально)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Починай з dry-run!
DRY_RUN=true
```

### Крок 3: Налаштування гаманця

1. Створи **новий** MetaMask гаманець (не основний!)
2. Поповни тільки Polygon USDC (наприклад, через Binance → Polygon мережа)
3. Зайди на [polymarket.com](https://polymarket.com) та задепозить USDC
4. Скопіюй приватний ключ → встав у `.env`

### Крок 4: DRY-RUN тест (обов'язково!)

```bash
python main.py
```

Бот буде **симулювати** угоди без реальних грошей.  
Перевір що логіка правильна, перегляй `logs/bot.log`.

### Крок 5: Реальна торгівля (після тижня тесту!)

```bash
# У .env змінити:
DRY_RUN=false

python main.py
```

---

## ⚙️ Ключові налаштування (config.py)

| Параметр | Значення | Опис |
|----------|---------|------|
| `DRY_RUN` | `True` | Симуляція (не торгує реально) |
| `INITIAL_CAPITAL` | `100.0` | Стартовий капітал ($) |
| `MIN_EDGE_ENTRY` | `0.08` | Мінімальний edge 8% для входу |
| `MAX_RESOLUTION_HOURS` | `72` | Тільки ринки з resolution < 72h |
| `MIN_MARKET_VOLUME_USD` | `5000` | Мінімальний обсяг ринку |
| `MAX_POSITION_PCT` | `0.07` | Максимум 7% капіталу на угоду |
| `STOP_LOSS_PCT` | `0.13` | Стоп-лос 13% |
| `TARGET_PORTFOLIO_VOL` | `0.12` | Цільова vol портфеля 12% |
| `SCAN_INTERVAL_SEC` | `120` | Сканування кожні 2 хвилини |

---

## 🧠 Як бот визначає edge

```
NOAA API (США) + Open-Meteo (глобально)
           ↓
    Прогноз: P(NYC > 70°F) = 0.75
           ↓
    Ринкова ціна YES = 0.55
           ↓
    Edge = (0.75 - 0.55) × 0.90 (confidence) = 18%
           ↓
    18% > 8% (мінімум) → BUY YES
           ↓
    Розмір: Volatility Targeting + Kelly = $3–7
```

---

## 📊 Правила виходу

| Умова | Дія |
|-------|-----|
| PnL ≤ -13% | Стоп-лос (закрити позицію) |
| Edge < 5% + збиток | Закрити (edge зник) |
| Edge < 5% | Зафіксувати прибуток |
| Resolution | Ринок вирішується автоматично |

---

## 🛡️ Безпека (Bot Bible 2026)

1. **RULE 1**: Тільки dedicated trading wallet
2. **RULE 2**: Аудит всіх pip залежностей при запуску
3. **RULE 3**: Приватний ключ тільки у `.env` файлі
4. **RULE 4**: Обмеження USDC approval (revoke.cash)
5. **RULE 5**: DRY-RUN за замовчуванням

---

## 📡 Telegram алерти

1. Створи бота через @BotFather
2. Отримай `BOT_TOKEN` та `CHAT_ID`
3. Заповни у `.env`
4. Встанови `TELEGRAM_ENABLED=True` у `config.py`

Бот надсилатиме сповіщення про:
- Кожну відкриту/закриту угоду
- Whale та insider активність
- Щогодинний звіт PnL
- Помилки та аварійні зупинки

---

## 🖥️ Запуск 24/7 на VPS

### Варіант A: systemd (рекомендовано для Linux VPS)

```bash
# Створити сервіс
sudo nano /etc/systemd/system/polybot.service
```

```ini
[Unit]
Description=Polymarket Weather Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/polymarket_weather_bot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=30
EnvironmentFile=/home/ubuntu/polymarket_weather_bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable polybot
sudo systemctl start polybot
sudo journalctl -u polybot -f  # Перегляд логів
```

### Варіант B: screen (простіше)

```bash
screen -S polybot
python main.py
# Ctrl+A, D — відключитись (бот продовжує працювати)
screen -r polybot  # Повернутись
```

### Варіант C: Google Cloud (безкоштовно до $300 credits)

```bash
# e2-micro instance — безкоштовно!
gcloud compute instances create polybot \
  --machine-type=e2-micro \
  --zone=us-east1-b \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud
```

---

## 📈 Реалістичні очікування для $100

| Сценарій | Win Rate | Edge avg | Місячний PnL |
|----------|---------|---------|-------------|
| Консервативний | 55% | 10% | +$3–8 |
| Базовий | 60% | 13% | +$8–20 |
| Оптимістичний | 65% | 16% | +$20–40 |
| Невдалий | 45% | — | -$10–25 |

> **Важливо**: Прибуток на малому капіталі ($100) буде невеликим в абсолютних числах.  
> При успіху — поступово збільшуй капітал до $500–1000.

---

## 🔍 Перегляд результатів

```bash
# Логи в реальному часі
tail -f logs/bot.log

# Стан бота (JSON)
cat data/bot_state.json

# Статистика
python -c "
import json
with open('data/bot_state.json') as f:
    s = json.load(f)
print(f'Капітал: \${s[\"current_capital\"]:.2f}')
print(f'PnL: \${s[\"total_pnl\"]:+.2f}')
print(f'Win rate: {s[\"winning_trades\"]/max(1,s[\"total_trades\"]):.1%}')
print(f'Угод: {s[\"total_trades\"]}')
"
```

---

## ❓ Поширені питання

**Q: Чому бот не знаходить ринків?**  
A: Weather ринки з'являються не завжди. Перевір `gamma-api.polymarket.com/markets?tag=weather`

**Q: Чи потрібен API ключ Polymarket?**  
A: Ключ генерується автоматично з твого приватного ключа при першому запуску.

**Q: Яка VPS потрібна?**  
A: Найдешевша (1 CPU, 512MB RAM) — бот дуже легкий. Google Cloud e2-micro (безкоштовно).

**Q: Скільки часу до першого прибутку?**  
A: Weather ринки вирішуються за 24–72h. Перші результати — через 3–7 днів.

---

# Sigma Calibration Bootstrap (v28+)

## What changed

The bot from v9 through v27 had a fatal structural flaw diagnosed in
`Recomeng.md`: `sigma_calibrator.py` was wired in (`data_fetcher._get_sigma`
calls `get_adaptive_sigma`, `trader.py` calls `record_forecast_error` on
resolution) but `data/sigma_calibration.json` did not exist, so every
(city, source) pair had 0 samples and `get_adaptive_sigma` always fell back
to the hardcoded `SIGMA_MIN = 3.0 C` floor.

A 3.0 sigma floor combined with a 1 C bucket Gaussian produces phantom
edges: actual forecast errors are 0.6-2.3 C per city (real ERA5-vs-archive
numbers), so the bot systematically believed a 20 % probability bucket was
"18 % edge" when in truth the bucket was already covered by the forecast's
actual uncertainty. Result: v27 ended 0/15 win rate and self-halted on
drawdown limit.

`bootstrap_calibration.py` fixes the foundation: pulls 365 days of ERA5
reanalysis (ground truth) + 3 independent archived forecast models per city,
computes per-(city, source) RMS error, and primes `sigma_calibration.json`
so `get_adaptive_sigma` returns real, calibrated sigmas from the first live
trade onward.

## Files

- `bootstrap_calibration.py` - one-time primer (run manually, not at every
  bot cycle).
- `sigma_calibrator.py` - extended with `_lookup_errors` that splits `+`-joined
  runtime source keys (e.g. `Open-Meteo_ENSEMBLE+NOAA+NASA_POWER`) and
  aggregates per-component errors, so per-component bootstrap data is
  lookup-able at runtime regardless of how live sources combine.
- `data/sigma_calibration.json` - the calibration dataset (29 cities,
  174 (city, source) pairs, ~8,700 samples).
- `data/calibration_summary.json` - human-readable summary, overwritten per
  run.

## Honest limits (proxy disclosure)

Open-Meteo free-tier archive does NOT expose the archived GEFS ensemble
feed that the live bot consumes as `Open-Meteo_ENSEMBLE`. Bootstrap uses
`icon_seamless` (DWD German seamless forecast) as a PROXY for the live
ensemble source. This estimates **city-level error structure**, not the
exact as-issued N-hour-ahead ensemble forecast error. The live calibrator
will continue to refine `Open-Meteo_ENSEMBLE` and `NASA_POWER` (the latter
has no free archive at all) at trade-resolution pace during normal bot
operation.

## How to run (one-time bootstrap)

```powershell
cd D:\Temp\Instal\Openbot\WeatherBot
python bootstrap_calibration.py --days 365
```

This overwrites `data/sigma_calibration.json` with fresh data for all 28
whitelist cities. Expect ~15-25 min wall time (112 API calls at 0.65 s
throttle + occasional 429 backoff).

## Weekly rerun (recommended)

Forecasts slowly drift with seasons. To keep calibration fresh:

```powershell
python bootstrap_calibration.py --days 365 --append
```

`--append` keeps existing samples (up to `_MAX_SAMPLES = 50` per pair) and
adds new days. Run weekly; the calibrator self-trims to the rolling 50 most
recent samples per (city, source) pair.

## Verification (post-bootstrap)

Each pair must have >= 5 samples for adaptive sigma to activate. To verify:

```powershell
python -c "import json; d=json.load(open('data/sigma_calibration.json'));
import statistics; bad=[(c,s,len(v)) for c in d['errors'] for s,v in d['errors'][c].items() if len(v)<5];
print('pairs under 5 samples:', bad or 'NONE')"
```

Expected output: `pairs under 5 samples: NONE`

## Deployment to Render

`data/sigma_calibration.json` and `data/calibration_summary.json` MUST be
pushed to GitHub alongside `bootstrap_calibration.py` and the modified
`sigma_calibrator.py` so the live Render worker reads calibrated sigmas
from the first cycle after deploy. Do NOT gitignore `data/sigma_calibration.json`.

After deploy, set Render env `RESET_POSITIONS=true` for ONE cycle (to clear
the v27 halted state and 0/15 resolved LOSS positions), then set it back to
`false` to preserve capital state across the v28 validation run.
