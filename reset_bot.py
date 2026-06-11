import os
import json
import pg8000
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

print("🧹 Starting full WeatherBot state reset...")

# 1. Clear local bot_state.json
state_file = "data/bot_state.json"
if os.path.exists(state_file):
    try:
        os.remove(state_file)
        print(f"✅ Removed local file: {state_file}")
    except Exception as e:
        print(f"❌ Error removing {state_file}: {e}")
else:
    print("ℹ️ Local state file not found (already clean).")

# 2. Clear PostgreSQL tables
db_url = os.environ.get("DATABASE_URL")
if db_url:
    try:
        parsed = urlparse(db_url)
        conn = pg8000.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
            timeout=10,
        )
        conn.autocommit = True
        cur = conn.cursor()
        
        # We can drop or truncate tables
        cur.execute("DROP TABLE IF EXISTS bot_state;")
        cur.execute("DROP TABLE IF EXISTS trade_log;")
        
        print("✅ Dropped tables 'bot_state' and 'trade_log' in PostgreSQL.")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ PostgreSQL reset error: {e}")
else:
    print("ℹ️ DATABASE_URL is not set, skipping DB reset.")

print("✨ Reset complete! The next bot run will start from scratch ($100.00 initial capital, v9 strategy).")
