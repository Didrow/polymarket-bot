"""
osint_module.py — Polymarket Weather Bot 2026
OSINT: відстеження китів, виявлення інсайдерів, аномальна активність.
Використовує Gamma API (безкоштовно, без ключів).
"""

import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field

import config

logger = logging.getLogger(__name__)

_whale_cache: Dict[str, List] = {}
_known_insiders: Set[str] = set()


@dataclass
class WhaleAlert:
    """Сповіщення про велику угоду (кит)."""
    wallet: str
    market_question: str
    condition_id: str
    side: str            # YES / NO
    size_usd: float
    price: float
    timestamp: datetime
    is_known_insider: bool = False

    @property
    def summary(self) -> str:
        insider_tag = " ⚠️INSIDER" if self.is_known_insider else ""
        return (f"🐋 WHALE{insider_tag} | {self.wallet[:8]}... | "
                f"{self.side} ${self.size_usd:.0f} @ {self.price:.2f} | "
                f"{self.market_question[:40]}")


@dataclass
class InsiderSignal:
    """Сигнал про можливу інсайдерську активність."""
    wallet: str
    market_question: str
    condition_id: str
    anomaly_score: float    # 0–1, вище = підозріліше
    reason: str
    size_usd: float
    entry_price: float
    timestamp: datetime


def fetch_market_trades(condition_id: str, limit: int = 50) -> List[Dict]:
    """
    Отримати останні угоди на ринку через Gamma API.
    """
    cache_key = f"trades_{condition_id}"
    # Очистка застарілих записів у кеші для запобігання витоку пам'яті
    now = time.time()
    for k in list(_whale_cache):
        if now - _whale_cache[k][0] > 3600:
            del _whale_cache[k]

    if cache_key in _whale_cache:
        ts = _whale_cache[cache_key][0]
        if time.time() - ts < 60:
            return _whale_cache[cache_key][1]

    try:
        url = f"{config.GAMMA_URL}/trades"
        params = {
            "conditionId": condition_id,
            "limit": limit,
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        trades = r.json()
        if isinstance(trades, list):
            _whale_cache[cache_key] = (time.time(), trades)
            return trades
    except Exception as e:
        logger.debug(f"Trades fetch error: {e}")

    return []


def detect_whales(condition_id: str, question: str) -> List[WhaleAlert]:
    """
    Виявити великі угоди (кити) на ринку.
    Поріг: угоди > WHALE_THRESHOLD_USD.
    """
    trades = fetch_market_trades(condition_id)
    alerts = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=12)  # Угоди за останні 12 годин

    for trade in trades:
        try:
            size = float(trade.get("size", 0)) * float(trade.get("price", 0))
            if size < config.WHALE_THRESHOLD_USD:
                continue

            # Парсинг дати
            ts_str = trade.get("timestamp", trade.get("created_at", ""))
            try:
                ts = datetime.fromisoformat(ts_str.rstrip("Z")).replace(tzinfo=timezone.utc)
            except Exception:
                ts = now

            if ts < cutoff:
                continue

            wallet = trade.get("maker", trade.get("taker", "unknown"))
            side = "YES" if trade.get("outcome", "").upper() == "YES" else "NO"
            price = float(trade.get("price", 0))

            is_insider = wallet.lower() in {w.lower() for w in config.KNOWN_WHALE_WALLETS}

            alert = WhaleAlert(
                wallet=wallet,
                market_question=question,
                condition_id=condition_id,
                side=side,
                size_usd=size,
                price=price,
                timestamp=ts,
                is_known_insider=is_insider,
            )
            alerts.append(alert)
            logger.info(alert.summary)

        except Exception as e:
            logger.debug(f"Trade parse error: {e}")

    return alerts


def detect_insider_anomaly(condition_id: str, question: str) -> Optional[InsiderSignal]:
    """
    Виявити аномальну активність (можливий інсайдер):
    - Раптова велика угода на малоліквідному ринку
    - Вхід значно раніше за новину
    - Повторні входи одного гаманця
    """
    trades = fetch_market_trades(condition_id, limit=100)
    if len(trades) < 3:
        return None

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=6)

    # Групуємо угоди по гаманцях за останні 6 годин
    wallet_activity: Dict[str, List[tuple]] = {}
    total_volume = 0

    for trade in trades:
        try:
            ts_str = trade.get("timestamp", "")
            ts = datetime.fromisoformat(ts_str.rstrip("Z")).replace(tzinfo=timezone.utc)
            if ts < cutoff:
                continue

            wallet = trade.get("maker", trade.get("taker", ""))
            price = float(trade.get("price", 0))
            size = float(trade.get("size", 0)) * price
            total_volume += size

            if wallet not in wallet_activity:
                wallet_activity[wallet] = []
            wallet_activity[wallet].append((size, price))
        except Exception:
            pass

    if total_volume == 0:
        return None

    # Шукаємо гаманець з аномальною концентрацією
    for wallet, trades_list in wallet_activity.items():
        sizes = [item[0] for item in trades_list]
        prices = [item[1] for item in trades_list]
        wallet_total = sum(sizes)
        concentration = wallet_total / total_volume

        # Аномалія: один гаманець > 40% обсягу за 6 годин
        if concentration > 0.40 and wallet_total > config.WHALE_THRESHOLD_USD:
            anomaly_score = min(0.99, concentration)
            avg_price = sum(sizes[i] * prices[i] for i in range(len(sizes))) / (wallet_total + 1e-6)

            signal = InsiderSignal(
                wallet=wallet,
                market_question=question,
                condition_id=condition_id,
                anomaly_score=anomaly_score,
                reason=f"Концентрація {concentration:.0%} обсягу ринку одним гаманцем",
                size_usd=wallet_total,
                entry_price=avg_price,
                timestamp=now,
            )
            logger.warning(
                f"🕵️ INSIDER SIGNAL: {wallet[:8]}... | "
                f"score={anomaly_score:.2f} | ${wallet_total:.0f} | "
                f"{question[:40]}"
            )
            _known_insiders.add(wallet)
            return signal

    return None


def scan_all_osint(markets: List) -> Dict:
    """
    Запустити OSINT сканування по всіх weather ринках.
    Returns: словник з китами та інсайдерами.
    """
    all_whales = []
    all_insiders = []

    for market in markets[:10]:  # Обмеження: перші 10 ринків (rate limit)
        whales = detect_whales(market.condition_id, market.question)
        all_whales.extend(whales)

        insider = detect_insider_anomaly(market.condition_id, market.question)
        if insider:
            all_insiders.append(insider)

        time.sleep(0.5)  # Ввічливий rate limit

    if all_whales:
        logger.info(f"🐋 Знайдено {len(all_whales)} whale угод")
    if all_insiders:
        logger.warning(f"🕵️ Виявлено {len(all_insiders)} insider сигналів")

    return {"whales": all_whales, "insiders": all_insiders}
