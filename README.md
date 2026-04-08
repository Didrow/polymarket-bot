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
