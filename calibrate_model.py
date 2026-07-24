"""
calibrate_model.py — Калібрування моделі прогнозу ймовірностей (v29)

Нове в v29:
  • PAV (Pool Adjacent Violators) isotonic regression — без sklearn-залежності
    (требования Render / Neon не підтягають ще один важкий пакет).
  • Збереження відображення у `data/prob_recalibration.json` — цей файл
    підхоплює edge_calculator.py при старті, щоб замінити сирі Gaussian
    ймовірності на емпірично калібровані ДО розрахунку edge/Kelly/size.
  • Захист від невизначених єдиних пар: якщо у trade_log < 5 записів з дії CLOSE,
    файл не перезаписується (залишається попередній, або一旁 взагалі не створюється).

Файл compatible з Neon (DATABASE_URL) та SQLite (через _log_trade_to_sqlite fallback).
"""

import os
import json
from collections import defaultdict
from urllib.parse import urlparse

try:
    import pg8000
    _HAS_PG8000 = True
except ImportError:
    _HAS_PG8000 = False


# ── DB CONNECTION ────────────────────────────────────────────
def _fetch_closed_trades():
    """Return list of dicts with our_prob + is_win from trade_log."""
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if not DATABASE_URL:
        print("Error: DATABASE_URL variable not set!")
        return None

    rows = None
    if _HAS_PG8000:
        try:
            parsed = urlparse(DATABASE_URL)
            conn = pg8000.connect(
                host=parsed.hostname,
                port=parsed.port or 5432,
                user=parsed.username,
                password=parsed.password,
                database=parsed.path.lstrip("/"),
            )
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    our_prob,
                    market_prob,
                    edge,
                    edge_at_entry,
                    entry_price,
                    size_usd,
                    pnl_usd,
                    status,
                    strategy,
                    dry_run,
                    time_decay_factor,
                    timestamp
                FROM trade_log
                WHERE action = 'CLOSE'
                ORDER BY timestamp
                """
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"pg8000 недоступний: {e}. Спроба SQLAlchemy URL-фолбеку...")

    if rows is None:
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    our_prob, market_prob, edge, edge_at_entry, entry_price,
                    size_usd, pnl_usd, status, strategy, dry_run, time_decay_factor, timestamp
                FROM trade_log
                WHERE action = 'CLOSE'
                ORDER BY timestamp
            """)
            rows = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"psycopg2 також недоступний: {e}")
            return None

    closed = []
    for row in rows:
        def f(v):
            try:
                return float(v) if v is not None else 0.0
            except Exception:
                return 0.0
        our_prob = f(row[0])
        market_prob = f(row[1])
        edge = f(row[2])
        edge_at_entry = f(row[3]) if row[3] is not None else edge
        entry_price = f(row[4])
        size_usd = f(row[5])
        pnl_usd = f(row[6])
        status = str(row[7])
        strategy = str(row[8] or "UNKNOWN")
        dry_run = bool(row[9])
        time_decay = f(row[10]) if row[10] is not None else 0.0
        is_win = status == "WIN"
        expected_pnl = edge_at_entry * size_usd
        realized_roi = pnl_usd / size_usd if size_usd > 0 else 0.0
        closed.append({
            "our_prob": our_prob,
            "market_prob": market_prob,
            "edge": edge,
            "edge_at_entry": edge_at_entry,
            "entry_price": entry_price,
            "size_usd": size_usd,
            "pnl_usd": pnl_usd,
            "status": status,
            "strategy": strategy,
            "dry_run": dry_run,
            "time_decay": time_decay,
            "is_win": is_win,
            "expected_pnl": expected_pnl,
            "realized_roi": realized_roi,
        })
    return closed


# ── PAV ISOTONIC REGRESSION ───────────────────────────────────
def _pav_isotonic(x: list, y: list) -> list:
    """Pool Adjacent Violators — повертає y_isotonic (не спадний), довжиною = len(x).
    Замінник sklearn.isotonic.IsotonicRegression(out_of_bounds='clip').

    Алгоритм: пулує сусідні точки де y порушує неспадність, замінюючи їх
    середньозваженим значенням. Зберігає відображення кожного оригінального x
    на відповідний isotonic-pooled y (повертає список тієї ж довжини, що x).
    """
    if not x:
        return []
    if len(x) != len(y):
        return list(y)
    # Сортуємо за x, запам'ятовуємо оригінальні індекси
    pairs = sorted(enumerate(zip(x, y)), key=lambda p: p[1][0])
    orig_idx = [p[0] for p in pairs]
    xs_sorted = [p[1][0] for p in pairs]
    ys_sorted = [float(p[1][1]) for p in pairs]
    weights_sorted = [1.0] * len(ys_sorted)

    # PAV in-place —保险rаж pool track range
    # reps[i] = (left, right) inclus ranges of original sorted positions in pool
    reps = list(range(len(ys_sorted)))  # кожен x має свій rep на старті

    i = 0
    while i < len(ys_sorted) - 1:
        if ys_sorted[i] > ys_sorted[i + 1]:
            wi = weights_sorted[i]
            wi1 = weights_sorted[i + 1]
            pooled = (ys_sorted[i] * wi + ys_sorted[i + 1] * wi1) / (wi + wi1)
            ys_sorted[i] = pooled
            weights_sorted[i] = wi + wi1
            ys_sorted.pop(i + 1)
            weights_sorted.pop(i + 1)
            reps[i] = reps.pop(i + 1)  # об'єднуємо rep ranges (latest is adjacent)
            while i > 0 and ys_sorted[i - 1] > ys_sorted[i]:
                wi = weights_sorted[i - 1]
                wi1 = weights_sorted[i]
                pooled = (ys_sorted[i - 1] * wi + ys_sorted[i] * wi1) / (wi + wi1)
                ys_sorted[i - 1] = pooled
                weights_sorted[i - 1] = wi + wi1
                ys_sorted.pop(i)
                weights_sorted.pop(i)
                reps[i - 1] = reps.pop(i)
                i -= 1
        else:
            i += 1

    # Розширюємо ys_sorted до оригінальної довжини по reps: кожен оригінальний
    # index (у відсортированому порядку) набуває pooled значення свого rep.
    # reps[i] after merging = останній sorted index в pool (коли зливались i+1 → i,
    # спочатку reps[i] був відокремлений, потім поглинений).
    # Простіший підхід: для кожного sorted-index j шукаємо його пул через weights.
    # Вважаємо що reps[i] завжди вкл. усі sorted-індекси об'єднані в пул i.
    # Простіше — перебудуємо по прямій: iter sorted-ys + weights.
    expanded = []
    for k in range(len(ys_sorted)):
        expanded.extend([ys_sorted[k]] * int(round(weights_sorted[k])))
    if len(expanded) != len(xs_sorted):
        # Unexpected fallback: pad/clamp
        while len(expanded) < len(xs_sorted):
            expanded.append(ys_sorted[-1] if ys_sorted else 0.0)
        expanded = expanded[:len(xs_sorted)]
    # expanded[j] відповідає sorted-index j → повертаємо до оригінального порядку
    out = [0.0] * len(x)
    for j, val in enumerate(expanded):
        out[orig_idx[j]] = val
    return out


def _build_recalibration_map(closed: list, output_path: str):
    """Будує isotonic-recalibration map з (edge_at_entry, is_win) пар.

    v29.1: Sanity check показав що trade_log.our_prob завжди 0.0 (логгер-баг
    в старому trader.py). Але edge_at_entry має реальні значення (3-15%+).
    Будуємо map у просторі edge_at_entry → actual win rate.
    edge_calculator._apply_recalibration має відповідно мапити edge, не our_prob.
    """
    # v29.1: Поки що our_prob == 0.0 в trade_log → map через our_prob неможливий.
    # Використовуємо edge_at_entry як осмислений ключ.
    # Дляサnity добавимо ancherу (0.0, 0.0) та великий edge → low-win.
    samples = [(c["edge_at_entry"], 1.0 if c["is_win"] else 0.0) for c in closed
               if 0.0 <= c["edge_at_entry"] <= 1.0 and c["strategy"] != "STARTUP_CLEANUP"]
    if len(samples) < 5:
        print(f"⚠️ Недостатньо CLOSE записів для recalibration: {len(samples)} < 5")
        print("   Файл prob_recalibration.json НЕ перезаписано.")
        return False

    # Сортуємо
    samples.sort(key=lambda p: p[0])
    xs = [p[0] for p in samples]
    ys_raw = [p[1] for p in samples]
    ys_iso = _pav_isotonic(xs, ys_raw)

    # Збираємо унікальні x з усередненим по них y_iso
    uniq: dict = {}
    for x, y in zip(xs, ys_iso):
        if x not in uniq:
            uniq[x] = []
        uniq[x].append(y)
    deduped = [(x, sum(v) / len(v)) for x, v in sorted(uniq.items())]

    payload = {
        "schema": 2,
        "algorithm": "PAV_isotonic_regression_v29_edge",
        "key": "edge_at_entry",
        "description": "Maps edge_at_entry → actual win rate. Use in edge_calculator to recalibrate raw edge before computing effective edge.",
        "n_samples": len(samples),
        "n_win": int(sum(1 for _, y in samples if y > 0.5)),
        "n_loss": int(sum(1 for _, y in samples if y <= 0.5)),
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "points": [[round(x, 6), round(y, 6)] for x, y in deduped],
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"✅ Збережено recalibration map: {output_path}")
    print(f"   точки: {len(payload['points'])}")
    print(f"   перша: raw {deduped[0][0]:.3f} → cal {deduped[0][1]:.3f}")
    print(f"   остання: raw {deduped[-1][0]:.3f} → cal {deduped[-1][1]:.3f}")
    return True


# ── REPORTING (старий звіт зберігається) ─────────────────────
def f(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def pct(v):
    return f"{v:.1%}"


def prob_bucket(p):
    if p < 0.10:
        return "0-10%"
    if p < 0.20:
        return "10-20%"
    if p < 0.30:
        return "20-30%"
    if p < 0.50:
        return "30-50%"
    return "50%+"


def edge_bucket(e):
    if e < 0.03:
        return "<3%"
    if e < 0.05:
        return "3-5%"
    if e < 0.07:
        return "5-7%"
    if e < 0.10:
        return "7-10%"
    return "10%+"


def decay_bucket(d):
    if d >= 0.99:
        return "0-12h"
    if d >= 0.94:
        return "12-24h"
    return "24h+"


def print_bucket_table(title, buckets, show_prob=False):
    print(f"\n{title}")
    print("-" * 76)
    if show_prob:
        print(f"{'Bucket':<14} | {'N':>4} | {'Exp WR':<8} | {'Actual WR':<9} | {'Bias':<8} | {'Avg edge':<9} | {'PnL':>10}")
    else:
        print(f"{'Bucket':<14} | {'N':>4} | {'Actual WR':<9} | {'Expected $':<12} | {'PnL':>10} | {'Avg PnL':>9}")
    print("-" * 76)
    for bucket, data in sorted(buckets.items(), key=lambda item: item[0]):
        n = data["trades"]
        wr = data["wins"] / n if n else 0.0
        pnl = data["pnl"]
        if show_prob:
            expected_wr = sum(map(float, bucket.replace("%", "").split("-"))) / 200.0 if bucket != "50%+" else 0.50
            avg_prob = data["prob"] / n if n else 0.0
            avg_edge = data["edge"] / n if n else 0.0
            print(f"{bucket:<14} | {n:>4} | {expected_wr:<8.1%} | {wr:<9.1%} | {wr - expected_wr:<+8.1%} | {avg_edge:<9.1%} | ${pnl:+9.2f}")
        else:
            expected = data["expected"]
            avg_pnl = pnl / n if n else 0.0
            print(f"{bucket:<14} | {n:>4} | {wr:<9.1%} | ${expected:+11.2f} | ${pnl:+9.2f} | ${avg_pnl:+8.2f}")


def _report(closed: list):
    trades = len(closed)
    wins = sum(1 for x in closed if x["is_win"])
    losses = trades - wins
    actual_wr = wins / trades if trades else 0.0
    avg_prob = sum(x["our_prob"] for x in closed) / trades if trades else 0.0
    avg_edge = sum(x["edge_at_entry"] for x in closed) / trades if trades else 0.0
    expected_pnl = sum(x["expected_pnl"] for x in closed)
    realized_pnl = sum(x["pnl_usd"] for x in closed)
    total_size = sum(x["size_usd"] for x in closed)
    realized_roi = realized_pnl / total_size if total_size > 0 else 0.0

    print("\n" + "=" * 86)
    print(f"📊 ЗВІТ КАЛІБРУВАННЯ МОДЕЛІ (Predicted vs Actual)")
    print("=" * 86)
    print(f"Закритих угод: {trades} | Wins: {wins} | Losses: {losses} | Actual WR: {pct(actual_wr)}")
    print(f"Avg predicted prob: {avg_prob:.1%} | Avg edge_at_entry: {avg_edge:.1%}")
    print(f"Expected PnL from edge: ${expected_pnl:+.2f} | Realized PnL: ${realized_pnl:+.2f} | Realized ROI: {pct(realized_roi)}")
    print("=" * 86)

    prob_buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "expected": 0.0, "edge": 0.0, "prob": 0.0, "size": 0.0})
    edge_buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "expected": 0.0})
    decay_buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "expected": 0.0})
    strategy_buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "expected": 0.0})

    for x in closed:
        pb = prob_bucket(x["our_prob"])
        eb = edge_bucket(x["edge_at_entry"])
        db = decay_bucket(x["time_decay"])
        sb = x["strategy"]
        for bucket, key in [(prob_buckets, pb), (edge_buckets, eb), (decay_buckets, db), (strategy_buckets, sb)]:
            bucket[key]["trades"] += 1
            bucket[key]["wins"] += int(x["is_win"])
            bucket[key]["pnl"] += x["pnl_usd"]
            bucket[key]["expected"] += x["expected_pnl"]
        prob_buckets[pb]["prob"] += x["our_prob"]
        prob_buckets[pb]["edge"] += x["edge_at_entry"]
        prob_buckets[pb]["size"] += x["size_usd"]

    print_bucket_table("Калібрування за our_prob", prob_buckets, show_prob=True)
    print_bucket_table("Backtest sanity за edge_at_entry", edge_buckets)
    print_bucket_table("Time-decay sanity", decay_buckets)
    print_bucket_table("Strategy sanity", strategy_buckets)

    print("\n⚠️ Якщо bias стабільно від'ємний у tail-бакетах 0-10% / 10-20%,")
    print("   треба піднімати EXTREME_TAIL_MIN_EDGE_YES або ISOTONIC_MIN_PROB.")
    print("⚠️ Якщо Expected PnL позитивний, а Realized PnL мінусовий —")
    print("   проблема в resolution/model calibration, не в edge-фільтрі.")


# ── MAIN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    closed = _fetch_closed_trades()
    if closed is None:
        exit(1)
    if not closed:
        print("Немає закритих угод для калібрування.")
        exit(0)

    _report(closed)

    # v29: зберегти isotonic-recalibration map для edge_calculator.py
    output_path = os.environ.get("RECALIBRATION_MAP_PATH", "data/prob_recalibration.json")
    print(f"\n📐 Будуємо isotonic-recalibration map → {output_path}")
    ok = _build_recalibration_map(closed, output_path)
    if ok:
        print("   Бот підхопить новий map на наступному циклі.")
    else:
        print("   Залишаємо попередній map (якщо існує).")
