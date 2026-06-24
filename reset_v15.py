"""
reset_v15.py — Повний reset стану WeatherBot для чистого 7-денного DRY-RUN тесту v15.

Очищає:
  1. data/bot_state.json       — локальний стан (capital, позиції, статистика)
  2. data/bot_trades.db        — SQLite fallback лог угод
  3. data/sigma_calibration.json — історія помилок прогнозів
  4. PostgreSQL (якщо є DATABASE_URL) — bot_state + trade_log таблиці

Після reset: capital=$100.00, 0 позицій, 0 trades, win_rate=0.
Альтернатива на Render: env RESET_POSITIONS=true (підтримується safeguards._load_state).
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timezone

# Дозволяємо запуск як з теки проекту, так і з будь-якого cwd
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.chdir(SCRIPT_DIR)

# Завантажуємо .env якщо є (для DATABASE_URL)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _print(msg: str):
    print(msg, flush=True)


def clear_local_files():
    """Крок 1-3: локальні файли стану, SQLite, sigma calibration."""
    _print("🧹 WeatherBot v15 — повний reset стану")
    _print("=" * 50)

    files_to_clear = [
        ("data/bot_state.json", "Локальний стан (capital, позиції, статистика)"),
        ("data/sigma_calibration.json", "Історія помилок прогнозів sigma"),
    ]
    for rel_path, desc in files_to_clear:
        full = os.path.join(SCRIPT_DIR, rel_path)
        if os.path.exists(full):
            try:
                os.remove(full)
                _print(f"✅ Видалено: {rel_path} ({desc})")
            except Exception as e:
                _print(f"❌ Помилка видалення {rel_path}: {e}")
        else:
            _print(f"ℹ️  Відсутній (вже чисто): {rel_path}")

    # SQLite: замість видалення файлу — очищаємо таблицю trade_log (безпечніше)
    db_path = os.path.join(DATA_DIR, "bot_trades.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("DELETE FROM trade_log")
            conn.commit()
            conn.close()
            _print(f"✅ Очищено таблицю trade_log у data/bot_trades.db")
        except Exception as e:
            _print(f"❌ Помилка очищення SQLite: {e}")
    else:
        _print("ℹ️  Відсутній (вже чисто): data/bot_trades.db")


def clear_postgres():
    """Крок 4: PostgreSQL (Neon/Render) — якщо є DATABASE_URL."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        _print("ℹ️  DATABASE_URL не задано — PostgreSQL reset пропущено")
        return

    # Спробуємо psycopg2 (основний драйвер у requirements)
    try:
        import psycopg2
    except ImportError:
        psycopg2 = None

    conn = None
    try:
        if psycopg2:
            conn_url = db_url
            if "sslmode=" not in conn_url:
                conn_url += "?sslmode=require"
            conn = psycopg2.connect(conn_url, connect_timeout=10)
        else:
            # Fallback: pg8000
            import pg8000
            from urllib.parse import urlparse
            parsed = urlparse(db_url)
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = pg8000.connect(
                host=parsed.hostname,
                port=parsed.port or 5432,
                user=parsed.username,
                password=parsed.password,
                database=parsed.path.lstrip("/"),
                timeout=10,
                ssl_context=ctx,
            )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS bot_state;")
        cur.execute("DROP TABLE IF EXISTS trade_log;")
        cur.close()
        _print("✅ PostgreSQL: видалено таблиці bot_state та trade_log")
    except Exception as e:
        _print(f"❌ PostgreSQL reset помилка: {e}")
        _print("   (б continued з локальним станом — SQLite fallback спрацює)")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def verify_clean_state():
    """Перевірка: створюємо новий BotState та показуємо стартові значення."""
    try:
        import config
        from safeguards import BotState
        fresh = BotState()
        _print("")
        _print("📊 Новий стартовий стан:")
        _print(f"   Капітал:        ${fresh.current_capital:.2f}")
        _print(f"   Позиції:        0")
        _print(f"   Total trades:   {fresh.total_trades}")
        _print(f"   Win/Loss:       {fresh.winning_trades}/{fresh.losing_trades}")
        _print(f"   Total PnL:      ${fresh.total_pnl:.2f}")
        _print(f"   DRY_RUN:        {config.DRY_RUN}")
        _print(f"   Стратегія:      v15 SNIPER PEAK GRID (neobrother-style)")
    except Exception as e:
        _print(f"⚠️  Не вдалось показати стартовий стан (не критично): {e}")


def main():
    _print(f"Час: {datetime.now(timezone.utc).isoformat()}")
    _print("")
    clear_local_files()
    _print("")
    clear_postgres()
    _print("")
    verify_clean_state()
    _print("")
    _print("=" * 50)
    _print("✨ Reset завершено! Наступний запуск бота почнеться з чистого аркуша.")
    _print("   На Render: також встановіть env RESET_POSITIONS=true для надійності.")
    _print("   Після 7 днів DRY-RUN: перед LIVE обов'язково backtest.")
    _print("=" * 50)


if __name__ == "__main__":
    main()
