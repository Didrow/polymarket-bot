"""
main.py — Polymarket Weather Bot v23b (SNIPER GRID)
"""

import os
import sys
import time
import signal
import atexit
import logging
import logging.handlers
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

import config
import security
import notifier
from safeguards import SafeguardManager, reset_pg_reconnect_counter
from market_scanner import fetch_weather_markets
from edge_calculator import scan_all_edges
from data_fetcher import test_all_apis
from trader import place_trade, check_and_close_positions, get_portfolio_summary, get_active_positions, cleanup_stale_positions, startup_cleanup
import trader as _trader
from osint_module import scan_all_osint

os.makedirs(config.LOGS_DIR, exist_ok=True)
os.makedirs(config.DATA_DIR, exist_ok=True)
os.makedirs(config.CACHE_DIR, exist_ok=True)

log_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

file_handler = logging.handlers.RotatingFileHandler(
    config.LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
)
file_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.DEBUG))
for _lib in ("urllib3", "certifi", "charset_normalizer", "idna", "requests", "httpcore", "httpx"):
    logging.getLogger(_lib).setLevel(logging.WARNING)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

_running = True
_safeguard = None
_initialized = False

class _HealthHandler(BaseHTTPRequestHandler):
    safeguard_manager = None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"NOT FOUND")
    def log_message(self, *args):
        pass

def _start_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"🌐 Health check сервер запущено на порту {port}")

def _save_state_and_exit():
    global _safeguard, _initialized
    if not _initialized:
        logger.warning("⚠️ Бот ще не ініціалізовано — НЕ зберігаємо порожній стан у PostgreSQL!")
        return
    logger.info("💾 Зберігаємо стан перед виходом...")
    if _safeguard is not None:
        _safeguard.save_positions(_trader._active_positions)
        _safeguard.save_state()
        logger.info("✅ Стан збережено")
    else:
        logger.warning("SafeguardManager не ініціалізовано, пропускаємо збереження")

def _signal_handler(sig, frame):
    global _running
    logger.info(f"⏹ Отримано сигнал {sig}, завершуємо роботу...")
    _running = False

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
atexit.register(_save_state_and_exit)

def init_clob_client():
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
        api_key = os.getenv("POLY_API_KEY", "")
        if not api_key:
            logger.info("Генеруємо нові API credentials...")
            creds = client.create_or_derive_api_creds()
            logger.info(f"API Key: {creds.api_key}")
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

def run_scan_cycle(safeguard: SafeguardManager, clob_client, cycle_count: int = 0) -> int:
    stale = cleanup_stale_positions()
    for pos in stale:
        safeguard.record_trade_close(pos.pnl_usd, pos.size_usd, condition_id=pos.condition_id)
        logger.warning(f"🧹 Cleanup closed: {pos.question[:60]} | PnL ${pos.pnl_usd:.2f}")

    closed = check_and_close_positions(clob_client)
    for pos in closed:
        safeguard.record_trade_close(pos.pnl_usd, pos.size_usd, condition_id=pos.condition_id)
        from safeguards import log_trade_to_pg
        log_trade_to_pg({
            "cycle": cycle_count,
            "action": "CLOSE",
            "direction": pos.direction,
            "market_question": pos.question,
            "city": pos.city,
            "forecast_c": 0.0,
            "threshold_c": 0.0,
            "our_prob": 0.0,
            "market_prob": 0.0,
            "edge": 0.0,
            "edge_at_entry": pos.edge_at_entry,
            "size_usd": pos.size_usd,
            "entry_price": pos.entry_price,
            "pnl_usd": pos.pnl_usd,
            "status": "WIN" if pos.pnl_usd > 0 else "LOSS",
            "strategy": "UNKNOWN",
            "dry_run": config.DRY_RUN,
            "time_decay_factor": 0.0
        })
        notifier.notify_trade_close(
            direction=pos.direction,
            question=pos.question,
            pnl_usd=pos.pnl_usd,
            pnl_pct=pos.pnl_pct,
            reason=pos.status,
        )

    portfolio = get_portfolio_summary()
    safeguard.update_portfolio_value(portfolio.get("total_value", 0.0), portfolio.get("total_pnl", 0.0))
    current_capital = safeguard.state.current_capital
    current_equity = safeguard.state.equity
    # Drawdown check — use safeguards manager (peak_equity based), NOT separate check
    if not safeguard.can_trade():
        return 0

    markets = fetch_weather_markets()
    if not markets:
        now_utc = datetime.now(timezone.utc)
        next_midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        sleep_sec = min((next_midnight - now_utc).total_seconds(), 43200)
        logger.info(f"😴 Немає активних weather-ринків. Сплю {sleep_sec/60:.0f} хв до 00:00 UTC...")
        time.sleep(sleep_sec)
        return -1

    if cycle_count % 5 == 0:
        osint_data = scan_all_osint(markets)
        for whale in osint_data.get("whales", []):
            if whale.is_known_insider:
                notifier.notify_whale_alert(whale.summary)

    edge_results = scan_all_edges(markets)
    tradeable = [r for r in edge_results if r.is_tradeable]
    safeguard.check_high_edge_warning(tradeable)

    if portfolio["active_positions"] > 0:
        logger.info(
            f"📂 Відкриті позиції: {portfolio['active_positions']} | "
            f"Unrealized PnL: ${portfolio['total_pnl']:+.2f}"
        )
        if cycle_count % 10 == 0:
            for cid, pos in _trader._active_positions.items():
                age_h = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 3600
                logger.info(
                    f"  📌 {pos.direction} | {pos.question[:55]} | "
                    f"entry={pos.entry_price:.4f} | size=${pos.size_usd:.2f} | "
                    f"age={age_h:.1f}h | cid={cid[:20]}"
                )

    # Фільтруємо edge для вже відкритих позицій за condition_id та нормалізованим запитанням
    active_cids = set(_trader._active_positions.keys())

    def _normalize_q(q: str) -> str:
        return "".join(c for c in q.lower() if c.isalnum())

    active_questions = {_normalize_q(pos.question) for pos in _trader._active_positions.values()}

    tradeable = [
        r for r in tradeable
        if _trader.normalize_condition_id(r.market.condition_id) not in active_cids
        and _normalize_q(r.market.question) not in active_questions
    ]

    if not tradeable:
        logger.info("Немає ринків з достатнім edge. Чекаємо...")
        return 0

    slow_positions_count = portfolio["active_positions"]
    _max_slow = config.MAX_ACTIVE_POSITIONS

    opened_this_cycle = 0
    city_counts_this_cycle = {}
    dup_count = 0
    city_limit_count = 0
    for edge_result in tradeable:
        if opened_this_cycle >= config.MAX_OPEN_PER_CYCLE:
            break

        if portfolio["active_positions"] >= config.MAX_ACTIVE_POSITIONS:
            logger.info(f"📊 Абсолютний ліміт {config.MAX_ACTIVE_POSITIONS} позицій — пропускаємо")
            break

        norm_q = _normalize_q(edge_result.market.question)
        if norm_q in active_questions:
            dup_count += 1
            continue

        # Перевірка ліміту позицій на місто (антикореляція)
        city = edge_result.market.detected_city
        if city and hasattr(config, 'MAX_POSITIONS_PER_CITY'):
            city_count = _trader.get_positions_count_by_city(city)
            city_count += city_counts_this_cycle.get(city, 0)
            if city_count >= config.MAX_POSITIONS_PER_CITY:
                city_limit_count += 1
                continue
        if not safeguard.check_hourly_trade_limit():
            break

        _raw_size = getattr(edge_result, 'size_usd', 0.0)
        if _raw_size <= 0:
            _raw_size = edge_result.edge * current_capital * config.MAX_POSITION_PCT
        size = max(config.MIN_POSITION_USD, min(_raw_size, config.MAX_POSITION_USD))
        logger.debug(f"💰 Size: ${size:.2f} для {edge_result.market.question[:50]} (raw={_raw_size:.2f}, edge={edge_result.edge:.1%})")
        projected_exposure = portfolio.get("total_value", 0.0) + size
        exposure_base = getattr(config, 'INITIAL_CAPITAL', 100.0)
        if projected_exposure > exposure_base * getattr(config, "MAX_TOTAL_EXPOSURE_PCT", 0.50):
            logger.info("Ліміт сумарної експозиції досягнуто — пропускаємо")
            continue
        if not safeguard.pre_trade_check(size, current_capital):
            logger.warning(f"⚠️ pre_trade_check FAIL: size=${size:.2f}, capital=${current_capital:.2f} | {edge_result.market.question[:50]}")
            continue

        position = place_trade(edge_result, current_capital, clob_client)

        if position:
            opened_this_cycle += 1
            city_counts_this_cycle[city] = city_counts_this_cycle.get(city, 0) + 1
            active_questions.add(norm_q)
            logger.info(f"🎯 УГОДА: {edge_result.summary}")
            safeguard.record_trade_open(position.size_usd)
            
            from safeguards import log_trade_to_pg
            _strategy = "SNIPER_GRID"
            log_trade_to_pg({
                "cycle": cycle_count,
                "action": "OPEN",
                "direction": position.direction,
                "market_question": position.question,
                "city": position.city,
                "forecast_c": getattr(edge_result.forecast, "temp_low_c" if "lowest" in position.question.lower() else "temp_high_c", 0.0) if edge_result.forecast else 0.0,
                "threshold_c": getattr(edge_result, "threshold_c", 0.0),
                "our_prob": edge_result.estimated_prob,
                "market_prob": edge_result.market_prob,
                "edge": edge_result.edge,
                "edge_at_entry": edge_result.edge,
                "size_usd": position.size_usd,
                "entry_price": position.entry_price,
                "pnl_usd": 0.0,
                "status": _strategy,
                "strategy": _strategy,
                "dry_run": config.DRY_RUN,
                "time_decay_factor": 1.0
            })
            
            notifier.notify_trade_open(
                direction=position.direction,
                question=position.question,
                size_usd=position.size_usd,
                price=position.entry_price,
                edge=edge_result.edge,
                dry_run=config.DRY_RUN,
            )

    if dup_count > 0 or city_limit_count > 0:
        logger.info(f"📊 Пропущено: {dup_count} дублікатів, {city_limit_count} ліміт міста")
    return opened_this_cycle

def main():
    global _safeguard
    _start_health_server()
    logger.info("")
    logger.info("=" * 60)
    logger.info("  🌤️  POLYMARKET WEATHER BOT v23  🌤️")
    logger.info("  🎯 SNIPER GRID (coldmath-style forecast buckets)")
    logger.info("=" * 60)
    logger.info(f"  Режим:    {'🧪 DRY-RUN (симуляція)' if config.DRY_RUN else '💰 РЕАЛЬНА ТОРГІВЛЯ'}")
    logger.info(f"  Капітал:  ${config.INITIAL_CAPITAL:.2f}")
    logger.info(f"  Kelly:    {'✅' if config.USE_KELLY else '❌'} (scale={config.KELLY_SCALE})")
    logger.info(f"  Сканування: кожні {config.SCAN_INTERVAL_SEC}s")
    kinds = "/".join(getattr(config, 'KINDS_ONLY', ['above','below']))
    logger.info(f"  Стратегія:  SNIPER GRID ({kinds})")
    logger.info(f"  Горизонт:   {config.MIN_RESOLUTION_HOURS:.0f}-{config.MAX_RESOLUTION_HOURS}h")
    logger.info(f"  Grid edge:  {getattr(config, 'SNIPER_GRID_MIN_EDGE', 0.04):.0%} YES | {getattr(config, 'SNIPER_GRID_MIN_EDGE_NO', 0.10):.0%} NO | Trend {getattr(config, 'MIN_EDGE_YES', 0.20):.0%}")
    logger.info(f"  Categ disc: {getattr(config, 'CATEGORICAL_DISCOUNT', 0.75):.0%} | Empirical: {getattr(config, 'EMPRICICAL_WEIGHT', 0.30):.0%}")
    logger.info(f"  Sigma min:  {config.SIGMA_MIN:.1f}°C | Prob bias: {getattr(config, 'PROB_BIAS', 1.0):.2f}")
    logger.info(f"  Stake:      ${config.MIN_POSITION_USD:.2f}-${config.MAX_POSITION_USD:.2f}, max {config.MAX_ACTIVE_POSITIONS} slots, {getattr(config, 'MAX_POSITIONS_PER_CITY', 1)}/city")
    logger.info("=" * 60)
    logger.info("")

    logger.info("🔍 Тестування API джерел...")
    api_results = test_all_apis()
    working = sum(1 for v in api_results.values() if v)
    total = len(api_results)
    logger.info(f"📊 API діагностика: {working}/{total} працюють")
    for name, ok in api_results.items():
        if not ok:
            logger.warning(f"  ❌ {name} — НЕ ДОСТУПНИЙ")

    sec_ok = security.run_security_checks()
    if not sec_ok and not config.DRY_RUN:
        logger.error("Security checks провалено. Зупинка.")
        sys.exit(1)

    clob_client = init_clob_client()
    if not config.DRY_RUN and clob_client is None:
        logger.error("LIVE mode requires CLOB client; stopping instead of silently DRY-RUN")
        sys.exit(1)
    safeguard = SafeguardManager(config)
    _safeguard = safeguard
    _HealthHandler.safeguard_manager = safeguard

    restored = safeguard.restore_positions()
    if restored:
        _trader._active_positions.update(restored)

    removed = startup_cleanup()
    if removed:
        logger.warning(f"🧹 Очищено {len(removed)} застарілих/фантомних позицій при старті")
        for pos in removed:
            safeguard.record_trade_close(pos.pnl_usd, pos.size_usd, condition_id=pos.condition_id)
            from safeguards import log_trade_to_pg
            log_trade_to_pg({
                "cycle": 0,
                "action": "CLOSE",
                "direction": pos.direction,
                "market_question": pos.question,
                "city": pos.city,
                "forecast_c": 0.0,
                "threshold_c": 0.0,
                "our_prob": 0.0,
                "market_prob": 0.0,
                "edge": 0.0,
                "edge_at_entry": pos.edge_at_entry,
                "size_usd": pos.size_usd,
                "entry_price": pos.entry_price,
                "pnl_usd": pos.pnl_usd,
                "status": "WIN" if pos.pnl_usd > 0 else "LOSS",
                "strategy": "STARTUP_CLEANUP",
                "dry_run": config.DRY_RUN,
                "time_decay_factor": 0.0
            })
        safeguard.save_positions(_trader._active_positions)
        safeguard.save_state()
        logger.info(f"💾 Стан збережено: {len(_trader._active_positions)} активних позицій")

    portfolio = get_portfolio_summary()
    safeguard.update_portfolio_value(portfolio.get("total_value", 0.0), portfolio.get("total_pnl", 0.0))
    if removed:
        safeguard.reset_daily_baseline("startup cleanup")

    if not config.DRY_RUN and not safeguard.check_validation_gate():
        logger.error("LIVE trading blocked; bot will not place real orders")
        sys.exit(1)

    notifier.notify_startup(config.DRY_RUN, safeguard.state.current_capital)
    _initialized = True
    logger.info("🚀 Бот запущено. Ctrl+C для зупинки.\n")

    cycle_count = 0
    last_summary_time = time.time()
    empty_cycles = 0
    _debug_auto_revert_cycle = None

    while _running:
        try:
            cycle_count += 1
            cap = safeguard.state.current_capital
            pnl = safeguard.state.total_pnl
            pnl_sign = "+" if pnl >= 0 else ""
            reset_pg_reconnect_counter()
            logger.info(
                f"─── Цикл #{cycle_count} | {datetime.now().strftime('%H:%M:%S')} | "
                f"💰 Капітал: ${cap:.2f} | PnL: {pnl_sign}${pnl:.2f} ───"
            )

            opened_trades = run_scan_cycle(safeguard, clob_client, cycle_count)
            
            if opened_trades == 0:
                empty_cycles += 1
            else:
                empty_cycles = 0

            if _debug_auto_revert_cycle is not None and cycle_count > _debug_auto_revert_cycle:
                root_logger.setLevel(logging.INFO)
                _debug_auto_revert_cycle = None
                logger.info("🔧 LOG_LEVEL → INFO (автовернення після DEBUG-циклу)")

            safeguard.save_positions(_trader._active_positions)

            safeguard.print_summary()

            if time.time() - last_summary_time >= 1800:
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
            time.sleep(30)

        if _running:
            sleep_time = config.SCAN_INTERVAL_SEC
            if empty_cycles >= 3:
                if _trader._active_positions:
                    end_dates = [p.end_date for p in _trader._active_positions.values() if p.end_date]
                    if end_dates:
                        nearest = min(end_dates)
                        hours_to_nearest = (nearest - datetime.now(timezone.utc)).total_seconds() / 3600
                        if hours_to_nearest > 2.0:
                            sleep_time = min(3600, max(300, int(hours_to_nearest * 300)))
                        else:
                            sleep_time = int(config.SCAN_INTERVAL_SEC * 1.0)
                    else:
                        sleep_time = int(config.SCAN_INTERVAL_SEC * 1.0)
                else:
                    now_utc = datetime.now(timezone.utc)
                    # Розумний сон: якщо зараз 06:00-23:59 UTC — сьогоднішні ринки вже мертві,
                    # спімо до 00:30 UTC коли з'являються свіжі daily-temperature ринки
                    if 6 <= now_utc.hour < 24:
                        target = now_utc.replace(hour=0, minute=30, second=0, microsecond=0) + timedelta(days=1)
                        sleep_seconds = int((target - now_utc).total_seconds())
                        sleep_time = max(300, min(sleep_seconds, 23 * 3600))
                        logger.info(f"🌙 Мертва зона ({now_utc.hour:02d}:{now_utc.minute:02d} UTC) — сьогоднішні ринки висохли. "
                                   f"Сплю {sleep_time//3600}г {sleep_time%3600//60}хв до {target.strftime('%H:%M UTC')} (нові ринки)")
                    elif empty_cycles >= 12:
                        sleep_time = 1800
                    elif empty_cycles >= 6:
                        sleep_time = 900
                    else:
                        sleep_time = int(config.SCAN_INTERVAL_SEC * 2.0)
                    if _debug_auto_revert_cycle is None:
                        _debug_auto_revert_cycle = cycle_count + 5
                        root_logger.setLevel(logging.DEBUG)
                        logger.info("🔧 LOG_LEVEL → DEBUG (перший активний цикл, автовернення через 5 циклів)")
                sleep_time = min(sleep_time, config.SCAN_MAX_SLEEP_SEC)
                logger.info(f"💤 Адаптивний сон (немає угод): {sleep_time}s ({empty_cycles} порожніх циклів поспіль)...\n")
            else:
                sleep_time = min(sleep_time, config.SCAN_MAX_SLEEP_SEC)
                logger.info(f"💤 Сплю {sleep_time}s...\n")
            time.sleep(sleep_time)

    logger.info("\n⏹ Бот зупинено")
    safeguard.print_summary()
    safeguard.save_state()

if __name__ == "__main__":
    main()
