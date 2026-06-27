# WORKSPACE.md — Поточний стан сесії

## Активна сесія
- Дата: 2026-06-27
- Ціль: WeatherBot v15 — METAR Arbitrage Sniper
- Статус: ✅ Код готовий до деплою, compileall + smoke test пройдені

## WeatherBot v15 — METAR ARBITRAGE SNIPER

### Кардинальна зміна стратегії
**Було (v14.7):** мікс 12+ позицій — categorical grid + adjacent grid + tail-yes → 7.4% win rate, PnL $-23.93
**Стало (v15):** METAR-арбітраж — тільки above/below ринки де METAR/observed підтверджують напрямок

### Ключові зміни v15 (27.06.2026)

#### config.py — повний перепис
| Параметр | v14.7 | v15 |
|----------|-------|-----|
| MAX_RESOLUTION_HOURS | 48 | **8** |
| MIN_RESOLUTION_HOURS | 0.5 | **1.5** |
| ENABLE_ADJACENT_GRID | True | **False** |
| ENABLE_EXTREME_TAIL_YES | True | **False** |
| SNIPER_GRID_MIN_ASK | 0.005 | **0.15** |
| SNIPER_GRID_MAX_ASK | 0.85 | **0.70** |
| MIN_POSITION_USD | 1.0 | **2.0** |
| MAX_OPEN_PER_CYCLE | 2 | **1** |
| METAR_ARB_ENABLED | — | **True** |
| METAR_ARB_MAX_HOURS | — | **8.0** |
| METAR_ARB_MIN_HOURS | — | **1.5** |
| METAR_ARB_MIN_ASK | — | **0.15** |
| METAR_ARB_MAX_ASK | — | **0.70** |
| METAR_ARB_MIN_EDGE | — | **0.08** |
| METAR_ARB_MIN_PROB | — | **0.55** |
| METAR_ARB_KINDS_ONLY | — | **["above","below"]** |
| METAR_ARB_REQUIRE_METAR | — | **True** |
| VALIDATION_REQUIRED_BEFORE_LIVE | — | **True** |
| VALIDATION_MIN_WIN_RATE | — | **0.45** |

#### edge_calculator.py — повний перепис
- **.only above/below markets** — categorical/range пропускаються (METAR_ARB_KINDS_ONLY)
- **METAR confirmation gate** — `_check_metar_confirmation()` перевіряє чи поточна METAR-температура підтверджує напрямок
- **METAR probability boost** — `_boost_prob_from_metar()` піднімає ймовірність коли METAR підтверджений (+10% max)
- **Adjacent grid ВИДАЛЕНО** — не генерується, не trader'ом не обробляється
- **Phantom edge filter** — збережено (market_prob < 0.10 + our_prob > 0.20 skip)
- **Market anchor** — збережено (MARKET_ANCHOR_WEIGHT=0.20)

#### data_fetcher.py — METAR-first weighting
| Горизонт | METAR вага | Ensemble | GFS | ECMWF | NOAA | NASA |
|----------|-----------|----------|-----|-------|------|------|
| ≤8h | **60%** | 20% | 10% | 10% | — | — |
| ≤12h | **40%** | 35% | 15% | 10% | — | — |
| >12h | — | 45% | 15% | 10% | 25% | 5% |

- METAR більше не "10% корекція після консенсусу" — він **першим** входить у зважування
- Observed = фізичний floor/ceiling (жорстке обмеження)
- Ensemble members пусті при METAR вага ≥ 50% (правильно — METAR-підтвердження не потребує емпіричного розподілу)

#### main.py — v15 startup banner
- Логування: "METAR ARBITRAGE SNIPER", горизонт 1.5-8h, вхід 15-70%, Adjacent ВИМКНЕНО
- Стратегія в trade_log: "METAR_ARB" замість "ADJACENT_GRID"/"GRID"

### Smoke test результати
```
METAR_ARB_ENABLED: True
METAR_ARB_MAX_HOURS: 8.0
METAR_ARB_MIN_ASK: 0.15
METAR_ARB_MAX_ASK: 0.70
ENABLE_ADJACENT_GRID: False
MAX_RESOLUTION_HOURS: 8
METAR_ARB_KINDS_ONLY: ['above', 'below']
METAR_ARB_MIN_EDGE: 0.08
MAX_OPEN_PER_CYCLE: 1
MAX_ACTIVE_POSITIONS: 5
DRY_RUN: True

Miami 6h forecast: 31.8°C / 26.0°C
Sources: ['METAR', 'Open-Meteo_ENSEMBLE', 'Open-Meteo_GFS', 'Open-Meteo_ECMWF', 'BIAS_CORR']
Members: 0 (METAR weight 60% > 0.5 → members excluded = correct)
```

### Залишені без змін
- `market_scanner.py` — не потребує змін (той самий парсинг ринків)
- `trader.py` — не потребує змін логіки (position management, resolution, MTM)
- `safeguards.py` — не потребує змін
- `sigma_calibrator.py` — працює, але при METAR-арбітражі фактично не критичний

## CopyBot (стабільний)
- DRY_RUN=true, 5 китів
- Render Free tier, webhook нотифікації
- Детально: CopyBot/README.md

## Інші проекти
- **B2B Lead Agent:** nanobot + Gemini API
- **UA Skills:** 31 скіл для opencode

## Наступні кроки
1. Push на GitHub (manual)
2. Deploy на Render
3. Моніторинг 7+ днів DRY-RUN
4. Перехід на LIVE після VALIATION_MIN_DRY_RUN_HOURS=168 + MIN_WIN_RATE=0.45
