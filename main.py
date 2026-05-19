"""
main.py — Polymarket Weather Bot 2026
Головний 24/7 цикл бота. Запуск: python main.py

СТЕК:
  data_fetcher     → NOAA + Open-Meteo прогнози (безкоштовно)
  market_scanner   → Gamma API weather ринки
  edge_calculator  → Порівняння прогноз vs ринок
  trader           → Volatility Targeting + Kelly позиції
  safeguards       → Circuit breakers, drawdown limit
  security         → Bot Bible 2026 rules
  osint_module     → Whale tracking, insider detection
  notifier         → Email сповіщення (Gmail SMTP)

ЗАПУСК:
  1. cp .env.example .env && nano .env  (встанови PRIVATE_KEY)
  2. pip install -r requirements.txt
  3. python main.py                     (DRY_RUN=true за замовчуванням)
"""

import os
import sys
import time
import signal
import logging
import logging.handlers
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

from dotenv import load_dotenv

# Завантажуємо .env перед імпортом config
load_dotenv()

import config
import security
import notifier
from safeguards import SafeguardManager
from market_scanner import fetch_weather_markets
from edge_calculator import scan_all_edges
from trader import place_trade, check_and_close_positions, get_portfolio_summary, get_active_positions, cleanup_stale_positions
import trader as _trader  # v26-fix: global module ref (NameError fix)
from osint_module import scan_all_osint

# ─── Логування ───────────────────────────────────────────────
os.makedirs(config.LOGS_DIR, exist_ok=True)
os.makedirs(config.DATA_DIR, exist_ok=True)
os.makedirs(config.CACHE_DIR, exist_ok=True)

log_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Файловий хендлер (rotating, 10MB × 5 файлів)
file_handler = logging.handlers.RotatingFileHandler(
    config.LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
)
file_handler.setFormatter(log_formatter)

# Консольний хендлер
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

# ─── Глобальний контроль зупинки ─────────────────────────────

# ─── Health check сервер для Render Free Tier ─────────────────
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def _start_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"🌐 Health check сервер запущено на порту {port}")

_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("⏹ Отримано сигнал завершення, зупиняємось...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ─── Ініціалізація CLOB клієнта ──────────────────────────────
def init_clob_client():
    """
    Ініціалізувати py-clob-client з приватним ключем з .env.
    В dry-run режимі повертає None.
    """
    if config.DRY_RUN:
        logger.info("🧪 DRY-RUN: CLOB клієнт не ініціалізується")
        return None

    private_key = os.getenv("PRIVATE_KEY", "")
    if not private_key:
        logger.error("PRIVATE_KEY не знайдено в .env! Перемикаємось на DRY-RUN")
        return None

    try:
        from py_clob_client.client import ClobClient

        client = ClobClient(
            host=config.CLOB_URL,
            key=private_key,
            chain_id=config.CHAIN_ID,
        )

        # Отримати або створити API credentials
        api_key = os.getenv("POLY_API_KEY", "")
        if not api_key:
            logger.info("Генеруємо нові API credentials...")
            creds = client.create_or_derive_api_creds()
            logger.info(f"API Key: {creds.api_key}")
            logger.info("💡 Збережи ці credentials в .env файлі!")
            # Встановлюємо credentials
            client.set_api_creds(creds)
        else:
            from py_clob_client.clob_types import ApiCreds
            creds = ApiCreds(
                api_key=api_key,
                api_secret=os.getenv("POLY_API_SECRET", ""),
                api_passphrase=os.getenv("POLY_API_PASSPHRASE", ""),
            )
            client.set_api_creds(creds)

        logger.info("✅ CLOB клієнт ініціалізовано (реальна торгівля)")
        return client

    except Exception as e:
        logger.error(f"❌ Помилка ініціалізації CLOB: {e}")
        logger.info("Перемикаємось на DRY-RUN режим")
        return None


# ─── Один цикл сканування ────────────────────────────────────
def run_scan_cycle(safeguard: SafeguardManager, clob_client, cycle_count: int = 0) -> int:
    """
    Один цикл бота:
    1. Перевірити безпеку
    2. Закрити позиції що виходять
    3. Знайти weather ринки
    4. Розрахувати edge
    5. Відкрити нові позиції
    """
    current_capital = safeguard.state.current_capital

    # ── 1. Circuit breaker check ────────────────────────────
    if not safeguard.can_trade():
        return 0

    # ── 2. Перевірка та закриття існуючих позицій ──────────
    # v27: cleanup у кожному циклі — видаляє non-weather позиції що з'явились під час роботи
    stale = cleanup_stale_positions()
    for pos in stale:
        safeguard.record_trade_close(pos.pnl_usd, pos.size_usd)
        logger.warning(f"🧹 Cleanup closed: {pos.question[:60]} | PnL ${pos.pnl_usd:.2f}")

    closed = check_and_close_positions(clob_client)
    for pos in closed:
        safeguard.record_trade_close(pos.pnl_usd, pos.size_usd)
        notifier.notify_trade_close(
            direction=pos.direction,
            question=pos.question,
            pnl_usd=pos.pnl_usd,
            pnl_pct=pos.pnl_pct,
            reason=pos.status,
        )

    # ── 3. Пошук weather ринків ─────────────────────────────
    markets = fetch_weather_markets()
    if not markets:
        logger.info("Немає активних weather-ринків (< 72h). Чекаємо...")
        return 0

    # ── 4. OSINT сканування (кожен 5-й цикл) ───────────────
    if cycle_count % 5 == 0:
        osint_data = scan_all_osint(markets)
        for whale in osint_data.get("whales", []):
            if whale.is_known_insider:
                notifier.notify_whale_alert(whale.summary)

    # ── 5. Розрахунок edge ──────────────────────────────────
    edge_results = scan_all_edges(markets)
    tradeable = [r for r in edge_results if r.is_tradeable]
    safeguard.check_high_edge_warning(tradeable)  # попередження про аномальні edge

    # Логуємо відкриті позиції
    portfolio = get_portfolio_summary()
    if portfolio["active_positions"] > 0:
        logger.info(
            f"📂 Відкриті позиції: {portfolio['active_positions']} | "
            f"Unrealized PnL: ${portfolio['total_pnl']:+.2f}"
        )
        # Детальний дамп позицій кожні 10 циклів для діагностики resolution
        if cycle_count % 10 == 0:
            for cid, pos in _trader._active_positions.items():
                age_h = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 3600
                logger.info(
                    f"  📌 {pos.direction} | {pos.question[:55]} | "
                    f"entry={pos.entry_price:.4f} | size=${pos.size_usd:.2f} | "
                    f"age={age_h:.1f}h | cid={cid[:20]}"
                )

    if not tradeable:
        logger.info("Немає ринків з достатнім edge. Чекаємо...")
        return 0

    # ── 6. Відкриття позицій ─────────────────────────────────
    # Ітеруємо ВЕСЬ список, зупиняємось після 2 УСПІШНО ВІДКРИТИХ.
    # Якщо перші 2 заблоковані (market=1.00, дублікат) — йдемо далі.
    opened_this_cycle = 0
    for edge_result in tradeable:
        if opened_this_cycle >= 2:
            break  # Відкрили 2 — достатньо
        # Перевірка ліміту активних позицій
        if portfolio["active_positions"] >= config.MAX_ACTIVE_POSITIONS:
            logger.info(f"📊 Ліміт {config.MAX_ACTIVE_POSITIONS} позицій — пропускаємо")
            break
        if not safeguard.check_hourly_trade_limit():
            break
        # Перевіряємо ліміт активних позицій
        portfolio = get_portfolio_summary()
        if portfolio["active_positions"] >= config.MAX_ACTIVE_POSITIONS:
            logger.info(f"📊 Ліміт позицій {config.MAX_ACTIVE_POSITIONS} — нові не відкриваємо")
            break

        size = getattr(edge_result, 'size_usd', edge_result.edge * current_capital * config.MAX_POSITION_PCT)
        if not safeguard.pre_trade_check(size, current_capital):
            continue

        logger.info(f"🎯 УГОДА: {edge_result.summary}")
        position = place_trade(edge_result, current_capital, clob_client)

        if position:
            opened_this_cycle += 1
            safeguard.record_trade_open(position.size_usd)
            notifier.notify_trade_open(
                direction=position.direction,
                question=position.question,
                size_usd=position.size_usd,
                price=position.entry_price,
                edge=edge_result.edge,
                dry_run=config.DRY_RUN,
            )

    return opened_this_cycle


# ─── ГОЛОВНИЙ ЦИКЛ ───────────────────────────────────────────
def main():
    _start_health_server()
    logger.info("")
    logger.info("=" * 60)
    logger.info("  🌤️  POLYMARKET WEATHER BOT 2026  🌤️")
    logger.info("=" * 60)
    logger.info(f"  Режим:    {'🧪 DRY-RUN (симуляція)' if config.DRY_RUN else '💰 РЕАЛЬНА ТОРГІВЛЯ'}")
    logger.info(f"  Капітал:  ${config.INITIAL_CAPITAL:.2f}")
    logger.info(f"  Сканування: кожні {config.SCAN_INTERVAL_SEC}s")
    logger.info(f"  Edge min:  {config.MIN_EDGE_ENTRY:.0%}")
    logger.info(f"  Правило:   тільки weather ринки < {config.MAX_RESOLUTION_HOURS}h")
    logger.info("=" * 60)
    logger.info("")

    # Security checks
    sec_ok = security.run_security_checks()
    if not sec_ok and not config.DRY_RUN:
        logger.error("Security checks провалено. Зупинка.")
        sys.exit(1)

    # Ініціалізація
    clob_client = init_clob_client()
    safeguard = SafeguardManager()

    # ♻️ Відновлення позицій після рестарту
    restored = safeguard.restore_positions()
    if restored:
        _trader._active_positions.update(restored)

    # Одразу після restore — примусово видаляємо фантомні позиції:
    # старі > 48h або з нереальним PnL > $50
    removed = cleanup_stale_positions()
    if removed:
        logger.warning(f"🧹 Очищено {len(removed)} застарілих/фантомних позицій при старті")
        # Зберігаємо в JSONBin щоб вони не повернулись після наступного рестарту
        safeguard.save_positions(_trader._active_positions)
        logger.info(f"💾 JSONBin оновлено: {len(_trader._active_positions)} активних позицій")

    # Email startup notification
    notifier.notify_startup(config.DRY_RUN, safeguard.state.current_capital)

    logger.info("🚀 Бот запущено. Ctrl+C для зупинки.\n")

    cycle_count = 0
    last_summary_time = time.time()
    empty_cycles = 0

    while _running:
        try:
            cycle_count += 1
            cap = safeguard.state.current_capital
            pnl = safeguard.state.total_pnl
            pnl_sign = "+" if pnl >= 0 else ""
            logger.info(
                f"─── Цикл #{cycle_count} | {datetime.now().strftime('%H:%M:%S')} | "
                f"💰 Капітал: ${cap:.2f} | PnL: {pnl_sign}${pnl:.2f} ───"
            )

            opened_trades = run_scan_cycle(safeguard, clob_client, cycle_count)
            
            if opened_trades == 0:
                empty_cycles += 1
            else:
                empty_cycles = 0

            # Зберігаємо позиції після кожного циклу (захист від рестарту)
            safeguard.save_positions(_trader._active_positions)

            # Щогодинний звіт
            if time.time() - last_summary_time >= 1800:  # Кожні 30 хвилин
                safeguard.print_summary()
                summary = safeguard.state
                notifier.notify_daily_summary(
                    capital=summary.current_capital,
                    total_pnl=summary.total_pnl,
                    win_rate=summary.win_rate,
                    total_trades=summary.total_trades,
                )
                last_summary_time = time.time()

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"❌ Помилка циклу: {e}", exc_info=True)
            notifier.notify_error(str(e))
            time.sleep(30)  # Пауза після помилки

        if _running:
            sleep_time = config.SCAN_INTERVAL_SEC
            if empty_cycles >= 3:
                sleep_time = int(config.SCAN_INTERVAL_SEC * 1.5)
                logger.info(f"💤 Адаптивний сон (немає угод): {sleep_time}s...\n")
            else:
                logger.info(f"💤 Сплю {sleep_time}s...\n")
            time.sleep(sleep_time)

    # Завершення
    logger.info("\n⏹ Бот зупинено")
    safeguard.print_summary()
    safeguard.save_state()



if __name__ == "__main__":
    main()
