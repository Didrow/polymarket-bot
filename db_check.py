import os
import pg8000
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("DATABASE_URL is not set!")
    exit(1)

parsed = urlparse(db_url)
conn = pg8000.connect(
    host=parsed.hostname,
    port=parsed.port or 5432,
    user=parsed.username,
    password=parsed.password,
    database=parsed.path.lstrip("/"),
    timeout=10,
)

cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM trade_log")
count = cur.fetchone()[0]
print(f"Total entries in trade_log: {count}")

cur.execute("SELECT id, timestamp, cycle, action, direction, market_question, city, size_usd, entry_price, pnl_usd, status, strategy, dry_run FROM trade_log ORDER BY id ASC")
rows = cur.fetchall()
for row in rows:
    print(row)

cur.close()
conn.close()
