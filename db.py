# db.py — Database utilities for Polymarket Weather Bot

import os
import logging
from datetime import datetime

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

def get_connection():
    """Return a new psycopg2 connection using DATABASE_URL env var."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL not set in environment")
        raise RuntimeError("DATABASE_URL not configured")
    # Render provides SSL; enforce sslmode
    return psycopg2.connect(dsn, sslmode="require")

def init_schema():
    """Create tables if they don't exist."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_log (
                    id SERIAL PRIMARY KEY,
                    condition_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    size_usd NUMERIC NOT NULL,
                    entry_price NUMERIC NOT NULL,
                    pnl_usd NUMERIC,
                    created_at TIMESTAMPTZ DEFAULT now()
                );
                CREATE TABLE IF NOT EXISTS error_log (
                    id SERIAL PRIMARY KEY,
                    error_message TEXT NOT NULL,
                    traceback TEXT,
                    occurred_at TIMESTAMPTZ DEFAULT now()
                );
                CREATE TABLE IF NOT EXISTS heartbeat (
                    id SERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now()
                );
                """
            )
        conn.commit()
    finally:
        conn.close()

def log_trade(condition_id: str, direction: str, size_usd: float, entry_price: float, pnl_usd: float | None = None):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO trade_log (condition_id, direction, size_usd, entry_price, pnl_usd) VALUES (%s, %s, %s, %s, %s)",
                (condition_id, direction, size_usd, entry_price, pnl_usd),
            )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log trade: {e}")
    finally:
        conn.close()

def log_error(message: str, tb: str | None = None):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO error_log (error_message, traceback) VALUES (%s, %s)",
                (message, tb),
            )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log error: {e}")
    finally:
        conn.close()

def send_heartbeat():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO heartbeat DEFAULT VALUES")
        conn.commit()
    finally:
        conn.close()
