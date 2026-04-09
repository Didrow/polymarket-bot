"""
market_scanner.py — Polymarket Weather Bot 2026
Пошук активних weather-ринків через Gamma API.
Без API ключів — публічний ендпоінт.
"""

import re
import time
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

import requests

import config

logger = logging.getLogger(__name__)

_market_cache: Dict[str, Any] = {}


@dataclass
class PolyMarket:
    """Структура активного weather-ринку Polymarket."""
    condition_id: str
    question: str
    description: str
    end_date: datetime
    hours_to_resolution: float
    volume_usd: float
    token_yes_id: str
    token_no_id: str
    best_ask_yes: float       # Ціна купівлі YES (0–1)
    best_bid_yes: float       # Ціна продажу YES (0–1)
    midpoint_yes: float       # Середня ціна YES
    market_slug: str = ""
    detected_city: str = ""   # Розпізнане місто
    market_type: str = ""     # "temperature", "rain", "snow" тощо
    threshold_value: Optional[float] = None  # Числовий поріг (наприклад 70°F)
    is_above: Optional[bool] = None           # True = above, False = below
    raw: Dict = field(default_factory=dict)


def _detect_city(text: str) -> str:
    """Розпізнати місто з тексту ринку."""
    text_lower = text.lower()
    for city in config.TARGET_CITIES:
        if city.lower() in text_lower:
            return city
    return ""


def _detect_market_type(text: str) -> str:
    """Визначити тип погодного ринку."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["rain", "precipitation", "rainfall"]):
        return "rain"
    if any(w in text_lower for w in ["snow", "snowfall"]):
        return "snow"
    if any(w in text_lower for w in ["temperature", "degrees", "high temp",
                                       "low temp", "fahrenheit", "celsius"]):
        return "temperature"
    if "freeze" in text_lower or "frost" in text_lower:
        return "freeze"
    if "wind" in text_lower:
        return "wind"
    return "weather"


def _extract_threshold(text: str) -> Optional[float]:
    """Витягти числовий поріг із тексту ринку (наприклад '75°F')."""
    patterns = [
        r'(\d+\.?\d*)\s*°?\s*[Ff]',    # 75°F, 75F
        r'(\d+\.?\d*)\s*°?\s*[Cc]',    # 20°C
        r'above\s+(\d+\.?\d*)',
        r'below\s+(\d+\.?\d*)',
        r'exceed\s+(\d+\.?\d*)',
        r'reach\s+(\d+\.?\d*)',
        r'(\d+\.?\d*)\s*(?:inches|mm|cm)',  # опади
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
    return None


def _is_above_market(text: str) -> Optional[bool]:
    """Визначити напрям: above чи below."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["above", "exceed", "higher", "over", "at least",
                                       "reach", "or more", "hotter"]):
        return True
    if any(w in text_lower for w in ["below", "under", "lower", "less than",
                                       "cooler", "colder"]):
        return False
    return None


def _parse_market(raw: Dict) -> Optional[PolyMarket]:
    """Парсинг одного ринку з Gamma API відповіді."""
    try:
        # Перевірка кінцевої дати
        end_date_str = raw.get("endDate") or raw.get("end_date_iso")
        if not end_date_str:
            return None

        # Парсинг дати
        for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z"]:
            try:
                if end_date_str.endswith("Z"):
                    end_date = datetime.strptime(end_date_str, fmt).replace(tzinfo=timezone.utc)
                else:
                    end_date = datetime.fromisoformat(end_date_str)
                break
            except ValueError:
                continue
        else:
            return None

        now = datetime.now(timezone.utc)
        hours_left = (end_date - now).total_seconds() / 3600

        if hours_left <= 0 or hours_left > config.MAX_RESOLUTION_HOURS:
            return None

        # Обсяг ринку
        volume = float(raw.get("volume", 0) or raw.get("volumeNum", 0) or 0)
        if volume < config.MIN_MARKET_VOLUME_USD:
            return None

        # Токени YES/NO
        tokens = raw.get("tokens", [])
        token_yes = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), None)
        token_no = next((t for t in tokens if t.get("outcome", "").upper() == "NO"), None)

        if not token_yes or not token_no:
            return None

        # Ціни
        best_ask_yes = float(token_yes.get("price", 0.5))
        best_bid_yes = 1 - float(token_no.get("price", 0.5))
        midpoint_yes = (best_ask_yes + best_bid_yes) / 2

        question = raw.get("question", "")
        description = raw.get("description", "")
        full_text = f"{question} {description}"

        return PolyMarket(
            condition_id=raw.get("conditionId", raw.get("id", "")),
            question=question,
            description=description,
            end_date=end_date,
            hours_to_resolution=round(hours_left, 1),
            volume_usd=volume,
            token_yes_id=token_yes.get("token_id", ""),
            token_no_id=token_no.get("token_id", ""),
            best_ask_yes=best_ask_yes,
            best_bid_yes=best_bid_yes,
            midpoint_yes=midpoint_yes,
            market_slug=raw.get("marketSlug", ""),
            detected_city=_detect_city(full_text),
            market_type=_detect_market_type(full_text),
            threshold_value=_extract_threshold(full_text),
            is_above=_is_above_market(full_text),
            raw=raw,
        )
    except Exception as e:
        logger.debug(f"Помилка парсингу ринку: {e}")
        return None


def fetch_weather_markets(force_refresh: bool = False) -> List[PolyMarket]:
    """
    Отримати всі активні weather-ринки з Gamma API.
    Фільтрує за: погодними ключовими словами, resolution < 72h, обсяг > мінімум.
    """
    cache_key = "weather_markets"
    if not force_refresh and cache_key in _market_cache:
        ts, markets = _market_cache[cache_key]
        if time.time() - ts < 60:  # Кеш 1 хвилина
            return markets

    all_markets = []

    # Запит через тег weather
    try:
        url = f"{config.GAMMA_URL}/markets"
        params = {
            "tag": "weather",
            "active": "true",
            "closed": "false",
            "limit": 100,
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        raw_list = data if isinstance(data, list) else data.get("markets", [])
        logger.info(f"Gamma API: отримано {len(raw_list)} weather ринків")
                # ── НОВЕ: ЛОГУЄМО ВСІ ОТРИМАНІ РИНКИ (для діагностики) ──
        logger.info(f"🔍 Отримано {len(raw_list)} ринків. Перевіряємо фільтри...")
        for raw in raw_list[:20]:  # показуємо тільки перші 20, щоб не засмічувати логи
            hours = 999
            vol = float(raw.get("volume", 0) or raw.get("volumeNum", 0) or 0)
            question = raw.get("question", "Без назви")[:100]
            try:
                end_date_str = raw.get("endDate") or raw.get("end_date_iso")
                if end_date_str:
                    from datetime import datetime, timezone
                    end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                    hours = (end - datetime.now(timezone.utc)).total_seconds() / 3600
                logger.info(f"   → Ринок: {question}... | vol=${vol:,.0f} | hours_to_end={hours:.1f}h")
            except:
                logger.info(f"   → Ринок: {question}... | vol=${vol:,.0f} | hours_to_end=???")

        for raw in raw_list:
            market = _parse_market(raw)
            if market:
                all_markets.append(market)

    except Exception as e:
        logger.error(f"Gamma API weather tag error: {e}")

    # Додатковий пошук за ключовими словами (якщо tag не дав результатів)
    if len(all_markets) < 3:
        for keyword in ["temperature", "rain", "snow", "weather"]:
            try:
                url = f"{config.GAMMA_URL}/markets"
                params = {"keyword": keyword, "active": "true", "limit": 50}
                r = requests.get(url, params=params, timeout=15)
                if r.status_code == 200:
                    raw_list = r.json()
                    if isinstance(raw_list, list):
                        for raw in raw_list:
                            market = _parse_market(raw)
                            if market and market.condition_id not in [m.condition_id for m in all_markets]:
                                all_markets.append(market)
            except Exception as e:
                logger.debug(f"Keyword search '{keyword}' error: {e}")

    # Сортування: найближчі до закриття — найпріоритетніші
    all_markets.sort(key=lambda m: m.hours_to_resolution)

    logger.info(f"Знайдено {len(all_markets)} валідних weather-ринків (< {config.MAX_RESOLUTION_HOURS}h)")
    for m in all_markets[:5]:
        logger.info(f"  [{m.detected_city}] {m.question[:60]}... | {m.hours_to_resolution:.1f}h | "
                    f"YES={m.midpoint_yes:.2f} | vol=${m.volume_usd:,.0f}")

    _market_cache[cache_key] = (time.time(), all_markets)
    return all_markets


def get_orderbook_price(token_id: str) -> Optional[Dict]:
    """
    Отримати актуальний orderbook із CLOB API для точних цін.
    Повертає best_ask, best_bid, midpoint.
    """
    try:
        url = f"{config.CLOB_URL}/book"
        r = requests.get(url, params={"token_id": token_id}, timeout=10)
        r.raise_for_status()
        book = r.json()

        asks = book.get("asks", [])
        bids = book.get("bids", [])

        best_ask = float(asks[0]["price"]) if asks else None
        best_bid = float(bids[0]["price"]) if bids else None
        midpoint = (best_ask + best_bid) / 2 if (best_ask and best_bid) else None

        return {
            "best_ask": best_ask,
            "best_bid": best_bid,
            "midpoint": midpoint,
            "spread": (best_ask - best_bid) if (best_ask and best_bid) else None,
        }
    except Exception as e:
        logger.debug(f"Orderbook error для {token_id[:8]}...: {e}")
        return None


def refresh_market_prices(market: PolyMarket) -> PolyMarket:
    """Оновити ціни конкретного ринку з CLOB orderbook."""
    book = get_orderbook_price(market.token_yes_id)
    if book:
        market.best_ask_yes = book["best_ask"] or market.best_ask_yes
        market.best_bid_yes = book["best_bid"] or market.best_bid_yes
        market.midpoint_yes = book["midpoint"] or market.midpoint_yes
    return market
