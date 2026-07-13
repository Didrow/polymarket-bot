# WORKSPACE.md — Поточний стан сесії

## Активна сесія
- Дата: 2026-07-13
- Ціль: WeatherBot **v26 COLDMATH LADDER**
- Статус: ✅ Код готовий (compile + smoke + imports). Manual push → Render.

## v26 — COLDMATH LADDER (neobrother / coldmath)

### Проблема (лог 13.07 ~12:19 UTC)
- 0% WR (0/8), ROI −9.3%, realized −$11
- Lottery tails dist 2–3.4°C, our_prob 6–9%, ask 1–3¢
- v25 disabled quality filters → regression

### Рішення
- Сітка YES **±1.5°C** навколо прогнозу (17 → 16/17/18)
- Peak-first ranking, peak size > wing size
- Cheap YES: our_prob ≥ 2.2× market AND our_prob ≥ 10%
- Distance-aware min_prob (10/12/15%)
- Completing city ladders preferred over scatter

### Файли змінено
- `config.py`, `edge_calculator.py`, `trader.py`, `main.py`, `MEMORY.md`

### Deploy checklist
1. Manual GitHub push (no auto commit)
2. Render env: `RESET_POSITIONS=true` once → restart → set `false`
3. DRY_RUN=true, 7 days / 30 resolved
4. LIVE only if WR≥28% and ROI≥+5%

### Smoke
- Ladder 17.0 → {16,17,18} tradeable; far tails blocked
- peak rank > tail; peak size ≥ wing
