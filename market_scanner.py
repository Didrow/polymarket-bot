"""
market_scanner.py — ФІНАЛЬНА ВЕРСІЯ (квітень 2026)

КОРЕНЕВА ПРИЧИНА ПОПЕРЕДНІХ ЗБОЇВ:
  1. API markets використовують clobTokenIds + outcomePrices (НЕ tokens[])
  2. Бот шукав поле tokens[] яке порожнє → завжди 0 ринків
  3. Фільтр closed=false відкидав sub-markets які acceptingOrders=true

РІШЕННЯ:
  - Парсинг через clobTokenIds та outcomePrices
  - Фільтрація по acceptingOrders=true (а не closed=false)
  - Пошук через tag_slug=daily-temperature (точний тег)
  - Slug-шаблони для надійності
"""

import json
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

import requests
import config

logger = logging.getLogger(__name__)
_market_cache: Dict[str, Any] = {}

MONTH_NAMES = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}

# Всі міста для яких є weather ринки на Polymarket
WEATHER_CITIES = [
    "london", "nyc", "new-york", "chicago", "paris", "berlin",
    "tokyo", "seoul", "hong-kong", "singapore", "sydney", "toronto",
    "los-angeles", "miami", "seattle", "boston", "atlanta", "dallas",
    "houston", "denver", "amsterdam", "madrid", "rome", "istanbul",
    "beijing", "shanghai", "taipei", "bangkok", "dubai", "mumbai",
    "jakarta", "kuala-lumpur", "moscow", "warsaw", "vienna", "prague",
    "melbourne", "auckland", "wellington", "cape-town", "lagos",
    "mexico-city", "austin", "phoenix", "minneapolis", "portland",
    "salt-lake-city", "nashville", "charlotte", "orlando", "las-vegas",
    "san-francisco", "montreal", "vancouver", "shenzhen", "wuhan",
    "chengdu", "chongqing", "lucknow", "jeddah", "panama-city",
    "helsinki", "buenos-aires", "busan", "hanoi", "singapore",
    "brisbane", "perth", "osaka", "edinburgh", "dublin", "brussels",
    "tel-aviv", "delhi", "karachi",
]


@dataclass
class PolyMarket:
    condition_id: str
    question: str
    description: str
    end_date: datetime
    hours_to_resolution: float
    volume_usd: float
    token_yes_id: str
    token_no_id: str
    best_ask_yes: float
    best_bid_yes: float
    midpoint_yes: float
    market_slug: str = ""
    detected_city: str = ""
    market_type: str = ""
    threshold_value: Optional[float] = None
    is_above: Optional[bool] = None
    raw: Dict = field(default_factory=dict)


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _detect_city(text: str) -> str:
    t = text.lower()
    cities = {
        "london": "London", "new york": "NYC", "nyc": "NYC",
        "chicago": "Chicago", "paris": "Paris", "berlin": "Berlin",
        "tokyo": "Tokyo", "seoul": "Seoul", "hong kong": "Hong Kong",
        "singapore": "Singapore", "sydney": "Sydney", "toronto": "Toronto",
        "los angeles": "Los Angeles", "miami": "Miami", "seattle": "Seattle",
        "boston": "Boston", "atlanta": "Atlanta", "dallas": "Dallas",
        "houston": "Houston", "denver": "Denver", "amsterdam": "Amsterdam",
        "madrid": "Madrid", "rome": "Rome", "istanbul": "Istanbul",
        "beijing": "Beijing", "shanghai": "Shanghai", "taipei": "Taipei",
        "bangkok": "Bangkok", "dubai": "Dubai", "melbourne": "Melbourne",
        "auckland": "Auckland", "wellington": "Wellington",
        "cape town": "Cape Town", "lagos": "Lagos",
        "mexico city": "Mexico City", "austin": "Austin",
        "shenzhen": "Shenzhen", "wuhan": "Wuhan", "chengdu": "Chengdu",
        "chongqing": "Chongqing", "lucknow": "Lucknow", "jeddah": "Jeddah",
        "helsinki": "Helsinki", "panama city": "Panama City",
        "busan": "Busan", "kuala lumpur": "Kuala Lumpur",
        "jakarta": "Jakarta", "mumbai": "Mumbai", "moscow": "Moscow",
        "warsaw": "Warsaw", "vienna": "Vienna", "prague": "Prague",
        "hanoi": "Hanoi", "buenos aires": "Buenos Aires",
        "montreal": "Montreal", "vancouver": "Vancouver",
        "phoenix": "Phoenix", "minneapolis": "Minneapolis",
        "portland": "Portland", "nashville": "Nashville",
        "charlotte": "Charlotte", "orlando": "Orlando",
        "las vegas": "Las Vegas", "san francisco": "San Francisco",
        "brisbane": "Brisbane", "perth": "Perth", "osaka": "Osaka",
        "edinburgh": "Edinburgh", "dublin": "Dublin",
        "brussels": "Brussels", "tel aviv": "Tel Aviv",
        "delhi": "Delhi", "karachi": "Karachi",
    }
    for key, city in cities.items():
        if key in t:
            return city
    return ""


def _parse_market_from_api(raw: Dict, hours_limit: float) -> Optional[PolyMarket]:
    """
    Парсинг ринку з реальної структури Gamma API.
    
    ВАЖЛИВО: Polymarket markets НЕ мають поле tokens[].
    Токени зберігаються як JSON-рядок у clobTokenIds.
    Ціни зберігаються як JSON-рядок у outcomePrices.
    """
    try:
        # ── 1. Перевірка що ринок приймає ордери ──────────────
        # acceptingOrders=true означає що ринок активний для торгівлі
        if not raw.get("acceptingOrders", False):
            return None

        # ── 2. Дата закінчення ─────────────────────────────────
        end_str = (raw.get("endDate") or raw.get("endDateIso") or
                   raw.get("end_date_iso") or "")
        if not end_str:
            return None

        end_date = _parse_dt(end_str)
        if not end_date:
            return None

        now = datetime.now(timezone.utc)
        hours_left = (end_date - now).total_seconds() / 3600

        # Дозволяємо ринки що вже трохи після дедлайну (до -2 годин)
        # бо деякі ринки закриваються пізніше за endDate
        if hours_left < -2 or hours_left > hours_limit:
            return None

        # ── 3. Об'єм ──────────────────────────────────────────
        volume = 0.0
        for k in ["volume", "volumeNum", "liquidity", "liquidityNum",
                  "volume24hr", "totalVolume"]:
            try:
                v = float(raw.get(k) or 0)
                if v > volume:
                    volume = v
            except Exception:
                pass

        # ── 4. Токени (clobTokenIds — JSON рядок) ─────────────
        clob_str = raw.get("clobTokenIds", "[]")
        try:
            token_ids = json.loads(clob_str) if isinstance(clob_str, str) else clob_str
        except Exception:
            token_ids = []

        if len(token_ids) < 2:
            return None

        token_yes_id = str(token_ids[0])
        token_no_id = str(token_ids[1])

        # ── 5. Ціни (outcomePrices — JSON рядок) ──────────────
        prices_str = raw.get("outcomePrices", "[\"0.5\", \"0.5\"]")
        try:
            prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
        except Exception:
            prices = ["0.5", "0.5"]

        if len(prices) < 2:
            prices = ["0.5", "0.5"]

        yes_price = float(prices[0]) if prices[0] else 0.5
        no_price = float(prices[1]) if prices[1] else 0.5

        # Нормалізація
        if yes_price <= 0:
            yes_price = 0.001
        if no_price <= 0:
            no_price = 0.001

        # Також перевіряємо bestAsk/bestBid якщо є
        best_ask = float(raw.get("bestAsk") or yes_price)
        best_bid = float(raw.get("bestBid") or yes_price)
        if best_ask <= 0:
            best_ask = yes_price
        if best_bid <= 0:
            best_bid = yes_price

        midpoint = (best_ask + best_bid) / 2

        # ── 6. Тексти ─────────────────────────────────────────
        question = raw.get("question", "") or raw.get("title", "")
        description = raw.get("description", "") or ""
        slug = raw.get("slug", "") or ""
        full = f"{question} {description} {slug}".lower()

        # ── 7. Тип ринку ──────────────────────────────────────
        mtype = "temperature"
        if any(w in full for w in ["rain", "precipitation", "rainfall"]):
            mtype = "rain"
        elif any(w in full for w in ["snow", "snowfall"]):
            mtype = "snow"

        # ── 8. Числовий поріг ─────────────────────────────────
        threshold = None
        m = re.search(r'(\d+\.?\d*)\s*[°]?\s*[CcFf]', question)
        if m:
            try:
                threshold = float(m.group(1))
            except Exception:
                pass

        is_above = None
        if any(w in full for w in ["above", "exceed", "or higher", "or above"]):
            is_above = True
        elif any(w in full for w in ["below", "under", "or lower", "or below"]):
            is_above = False

        return PolyMarket(
            condition_id=raw.get("conditionId") or raw.get("id", ""),
            question=question,
            description=description[:200],
            end_date=end_date,
            hours_to_resolution=round(max(hours_left, 0), 1),
            volume_usd=volume,
            token_yes_id=token_yes_id,
            token_no_id=token_no_id,
            best_ask_yes=best_ask,
            best_bid_yes=best_bid,
            midpoint_yes=midpoint,
            market_slug=slug,
            detected_city=_detect_city(f"{question} {description}"),
            market_type=mtype,
            threshold_value=threshold,
            is_above=is_above,
            raw=raw,
        )
    except Exception as e:
        logger.debug(f"Parse error: {e} | {raw.get('question', '')[:40]}")
        return None


def _extract_markets_from_event(event: Dict, hours_limit: float) -> List[PolyMarket]:
    """Витягнути всі активні sub-markets з event."""
    result = []
    event_end = event.get("endDate", "")

    for m_raw in event.get("markets", []):
        # Якщо market не має endDate — беремо з event
        if not m_raw.get("endDate") and event_end:
            m_raw = dict(m_raw)
            m_raw["endDate"] = event_end

        pm = _parse_market_from_api(m_raw, hours_limit)
        if pm:
            result.append(pm)

    return result


def _fetch_events_by_tag(tag_slug: str, hours_limit: float) -> List[PolyMarket]:
    """Отримати ринки за тегом."""
    found = []
    try:
        r = requests.get(
            f"{config.GAMMA_URL}/events",
            params={
                "tag_slug": tag_slug,
                "active":   "true",
                "closed":   "false",
                "limit":    100,
            },
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            events = data if isinstance(data, list) else data.get("events", [])
            logger.info(f"  tag_slug={tag_slug}: {len(events)} events")
            for ev in events:
                found.extend(_extract_markets_from_event(ev, hours_limit))
        else:
            logger.debug(f"  tag={tag_slug}: HTTP {r.status_code}")
    except Exception as e:
        logger.debug(f"  tag={tag_slug} error: {e}")
    return found


def _fetch_event_by_slug(slug: str, hours_limit: float) -> List[PolyMarket]:
    """Отримати конкретний event за slug."""
    try:
        r = requests.get(
            f"{config.GAMMA_URL}/events",
            params={"slug": slug},
            timeout=8,
        )
        if r.status_code != 200:
            return []

        data = r.json()
        if isinstance(data, list):
            events = data
        elif isinstance(data, dict) and data:
            events = [data]
        else:
            return []

        result = []
        for ev in events:
            if ev:
                result.extend(_extract_markets_from_event(ev, hours_limit))
        return result
    except Exception:
        return []


def fetch_weather_markets(force_refresh: bool = False) -> List[PolyMarket]:
    """
    Головна функція пошуку weather-ринків.
    
    Стратегія:
      1. /events?tag_slug=daily-temperature (найточніший тег)
      2. /events?tag_slug=weather (загальний weather тег)
      3. Slug-шаблони по містах і датах (надійний fallback)
    """
    cache_key = "weather_markets"
    if not force_refresh and cache_key in _market_cache:
        ts, markets = _market_cache[cache_key]
        if time.time() - ts < 90:
            return markets

    hours_limit = float(config.MAX_RESOLUTION_HOURS)
    all_markets: List[PolyMarket] = []
    seen: set = set()

    def add(lst: List[PolyMarket]) -> int:
        n = 0
        for pm in lst:
            cid = pm.condition_id
            if cid and cid not in seen:
                seen.add(cid)
                all_markets.append(pm)
                n += 1
        return n

    logger.info(f"Пошук weather-ринків (ліміт={hours_limit:.0f}h)...")

    # ── Метод 1: tag daily-temperature ────────────────────────
    n1 = add(_fetch_events_by_tag("daily-temperature", hours_limit))
    logger.info(f"Метод 1 (daily-temperature): {n1} ринків")

    # ── Метод 2: tag weather ───────────────────────────────────
    n2 = add(_fetch_events_by_tag("weather", hours_limit))
    logger.info(f"Метод 2 (weather): +{n2} ринків")

    # ── Метод 3: tag highest-temperature ──────────────────────
    n3 = add(_fetch_events_by_tag("highest-temperature", hours_limit))
    logger.info(f"Метод 3 (highest-temperature): +{n3} ринків")

    # ── Метод 4: Slug-шаблони ─────────────────────────────────
    if len(all_markets) < 10:
        logger.info("Метод 4: slug-шаблони по містах...")
        now = datetime.now(timezone.utc)
        slug_hits = 0

        for day_offset in range(0, 5):
            d = now + timedelta(days=day_offset)
            mon = MONTH_NAMES[d.month]
            for city in WEATHER_CITIES:
                slug = f"highest-temperature-in-{city}-on-{mon}-{d.day}-{d.year}"
                ms = _fetch_event_by_slug(slug, hours_limit)
                for pm in ms:
                    if pm.condition_id not in seen:
                        seen.add(pm.condition_id)
                        all_markets.append(pm)
                        slug_hits += 1
                        logger.info(
                            f"  ЗНАЙДЕНО [{pm.detected_city}] "
                            f"{pm.question[:55]} | "
                            f"{pm.hours_to_resolution:.1f}h | "
                            f"YES={pm.midpoint_yes:.3f}"
                        )
                time.sleep(0.03)

        logger.info(f"Метод 4 (slugs): +{slug_hits} ринків")

    # ── Підсумок ───────────────────────────────────────────────
    all_markets.sort(key=lambda m: m.hours_to_resolution)
    total = len(all_markets)

    if total == 0:
        logger.warning(
            "Знайдено 0 ринків. "
            "Нові weather-ринки з'являються щодня ~00:00 UTC."
        )
    else:
        logger.info(f"✅ Знайдено {total} weather-ринків:")
        for pm in all_markets[:10]:
            logger.info(
                f"  [{pm.detected_city or '?'}] "
                f"{pm.question[:65]} | "
                f"{pm.hours_to_resolution:.1f}h | "
                f"YES={pm.midpoint_yes:.3f} | "
                f"vol=${pm.volume_usd:,.0f}"
            )

    _market_cache[cache_key] = (time.time(), all_markets)
    return all_markets


def get_orderbook_price(token_id: str) -> Optional[Dict]:
    """Отримати актуальний orderbook із CLOB API."""
    if not token_id:
        return None
    try:
        r = requests.get(
            f"{config.CLOB_URL}/book",
            params={"token_id": token_id},
            timeout=10,
        )
        r.raise_for_status()
        book = r.json()
        asks = book.get("asks", [])
        bids = book.get("bids", [])
        best_ask = float(asks[0]["price"]) if asks else None
        best_bid = float(bids[0]["price"]) if bids else None
        midpoint = (best_ask + best_bid) / 2 if (best_ask and best_bid) else None
        return {"best_ask": best_ask, "best_bid": best_bid, "midpoint": midpoint}
    except Exception:
        return None
