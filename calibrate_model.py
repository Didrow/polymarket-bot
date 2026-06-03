import os
import pg8000
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
cur.execute("SELECT our_prob, status FROM trade_log WHERE action = 'CLOSE'")
rows = cur.fetchall()
cur.close()
conn.close()

if not rows:
    print("Немає закритих угод для калібрування.")
    exit(0)

buckets = {
    "0.0 - 0.2": {"total": 0, "wins": 0},
    "0.2 - 0.4": {"total": 0, "wins": 0},
    "0.4 - 0.6": {"total": 0, "wins": 0},
    "0.6 - 0.8": {"total": 0, "wins": 0},
    "0.8 - 1.0": {"total": 0, "wins": 0},
}

for our_prob, status in rows:
    p = float(our_prob)
    is_win = (status == "WIN")

    if 0.0 <= p < 0.2:
        key = "0.0 - 0.2"
    elif 0.2 <= p < 0.4:
        key = "0.2 - 0.4"
    elif 0.4 <= p < 0.6:
        key = "0.4 - 0.6"
    elif 0.6 <= p < 0.8:
        key = "0.6 - 0.8"
    else:
        key = "0.8 - 1.0"

    buckets[key]["total"] += 1
    if is_win:
        buckets[key]["wins"] += 1

print("\n📊 ЗВІТ КАЛІБРУВАННЯ МОДЕЛІ (Predicted vs Actual):")
print("=" * 60)
print(f"{'Прогнозний бакет':<20} | {'Всього угод':<12} | {'Очікуваний WR':<15} | {'Фактичний WR':<12}")
print("-" * 60)
for bucket, data in buckets.items():
    if data["total"] == 0:
        continue
    actual_wr = data["wins"] / data["total"]
    expected_wr = sum(map(float, bucket.split(" - "))) / 2
    print(f"{bucket:<20} | {data['total']:<12} | {expected_wr:<15.1%} | {actual_wr:<12.1%}")
print("=" * 60)
