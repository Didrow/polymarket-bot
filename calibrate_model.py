import os
import pg8000
from collections import defaultdict
from urllib.parse import urlparse

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL variable not set!")
    exit(1)

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

if not rows:
    print("Немає закритих угод для калібрування.")
    exit(0)


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

closed = []
for row in rows:
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

trades = len(closed)
wins = sum(1 for x in closed if x["is_win"])
losses = trades - wins
actual_wr = wins / trades if trades else 0.0
avg_prob = sum(x["our_prob"] for x in closed) / trades
avg_edge = sum(x["edge_at_entry"] for x in closed) / trades
expected_pnl = sum(x["expected_pnl"] for x in closed)
realized_pnl = sum(x["pnl_usd"] for x in closed)
realized_roi = realized_pnl / sum(x["size_usd"] for x in closed)

print("\n📊 ЗВІТ КАЛІБРУВАННЯ МОДЕЛІ (Predicted vs Actual)")
print("=" * 86)
print(f"Закритих угод: {trades} | Wins: {wins} | Losses: {losses} | Actual WR: {pct(actual_wr)}")
print(f"Avg predicted prob: {avg_prob:.1%} | Avg edge_at_entry: {avg_edge:.1%}")
print(f"Expected PnL from edge: ${expected_pnl:+.2f} | Realized PnL: ${realized_pnl:+.2f} | Realized ROI: {pct(realized_roi)}")
print("=" * 86)

prob_buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "edge": 0.0, "prob": 0.0, "size": 0.0})
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

print_bucket_table("Калібрування за our_prob", prob_buckets, show_prob=True)
print_bucket_table("Backtest sanity за edge_at_entry", edge_buckets)
print_bucket_table("Time-decay sanity", decay_buckets)
print_bucket_table("Strategy sanity", strategy_buckets)

print("\n⚠️ Якщо bias стабільно від'ємний у tail-бакетах 0-10% / 10-20%, треба піднімати EXTREME_TAIL_MIN_EDGE_YES.")
print("⚠️ Якщо Expected PnL позитивний, а Realized PnL мінусовий — проблема в resolution/model calibration, не в edge-фільтрі.")
