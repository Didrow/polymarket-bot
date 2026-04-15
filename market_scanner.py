"""
market_scanner.py — Polymarket Weather Bot 2026 (ULTIMATE FIXED v4)
"""

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

WEATHER_CITIES = [
    "london", "nyc", "new-york", "chicago", "paris", "berlin", "tokyo", "seoul",
    "hong-kong", "singapore", "sydney", "toronto", "los-angeles", "miami",
    "seattle", "boston", "atlanta", "dallas", "houston", "denver", "amsterdam",
    "madrid", "rome", "istanbul", "beijing", "shanghai", "taipei", "bangkok",
    "dubai", "mumbai", "jakarta", "kuala-lumpur", "moscow", "warsaw", "vienna",
    "prague", "melbourne", "auckland", "wellington", "cape-town", "lagos",
    "mexico-city", "austin", "phoenix", "minneapolis", "portland",
    "salt-lake-city", "nashville", "charlotte", "orlando", "las-vegas",
    "san-francisco", "montreal", "vancouver", "shenzhen", "wuhan",
    "chengdu", "chongqing", "lucknow", "jeddah", "panama-city",
    "helsinki", "buenos-aires", "busan", "hanoi",
]

MONTH_NAMES = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}

WEATHER_KEYWORDS = ["temperature", "highest temperature", "lowest temperature", "rain", "snow", "precipitation", "weather"]


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
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _detect_city(text: str) -> str:
    t = text.lower()
    cities = {
        "london": "London", "new york": "NYC", "nyc": "NYC", "chicago": "Chicago",
        "paris": "Paris", "berlin": "Berlin", "tokyo": "Tokyo", "seoul": "Seoul",
        "hong kong": "Hong Kong", "singapore": "Singapore", "sydney": "Sydney",
        "toronto": "Toronto", "los angeles": "Los Angeles", "miami": "Miami",
        "seattle": "Seattle", "boston": "Boston", "atlanta": "Atlanta",
        "dallas": "Dallas", "houston": "Houston", "denver": "Denver",
        "amsterdam": "Amsterdam", "madrid": "Madrid", "rome": "Rome",
        "istanbul": "Istanbul", "beijing": "Beijing", "shanghai": "Shanghai",
        "taipei": "Taipei", "bangkok": "Bangkok", "dubai": "Dubai",
        "melbourne": "Melbourne", "auckland": "Auckland", "wellington": "Wellington",
        "cape town": "Cape Town", "lagos": "Lagos", "mexico city": "Mexico City",
        "austin": "Austin", "shenzhen": "Shenzhen", "wuhan": "Wuhan",
        "chengdu": "Chengdu", "chongqing": "Chongqing", "lucknow": "Lucknow",
        "jeddah": "Jeddah", "helsinki": "Helsinki", "panama city": "Panama City",
        "busan": "Busan", "kuala lumpur": "Kuala Lumpur", "jakarta": "Jakarta",
        "mumbai": "Mumbai", "moscow": "Moscow", "warsaw": "Warsaw",
        "vienna": "Vienna", "prague": "Prague", "hanoi": "Hanoi",
        "buenos aires": "Buenos Aires", "montreal": "Montreal",
        "vancouver": "Vancouver", "phoenix": "Phoenix", "minneapolis": "Minneapolis",
        "portland": "Portland", "nashville": "Nashville", "charlotte": "Charlotte",
        "orlando": "Orlando", "las vegas": "Las Vegas", "san francisco": "San Francisco",
    }
    for key, city in cities.items():
        if key in t:
            return city
    return ""


def _parse_market(raw: Dict, hours_limit: float) -> Optional[PolyMarket]:
    try:
        end_str = raw.get("endDate") or raw.get("end_date_iso") or raw.get("end_date") or ""
        if not end_str:
            return None
        end_date = _parse_dt(end_str)
        if not end_date:
            return None

        hours_left = (end_date - datetime.now(timezone.utc)).total_seconds() / 3600
        if hours_left <= 0 or hours_left > hours_limit:
            return None

        volume = 0.0
        for k in ["volume", "volumeNum", "liquidity"]:
            try:
                v = float(raw.get(k) or 0)
                if v > volume:
                    volume = v
            except Exception:
                pass

        tokens = raw.get("tokens", [])
        ty = next((t for t in tokens if str(t.get("outcome", "")).upper() == "YES"), None)
        tn = next((t for t in tokens if str(t.get("outcome", "")).upper() == "NO"), None)
        if not ty or not tn:
            return None

        ask = float(ty.get("price", 0.5))
        bid = 1.0 - float(tn.get("price", 0.5))
        mid = (ask + bid) / 2

        question = raw.get("question", "")
        description = raw.get("description", "")
        slug = raw.get("slug", "")
        full = f"{question} {description} {slug}".lower()

        mtype = "temperature"
        if any(w in full for w in ["rain", "precipitation", "rainfall"]):
            mtype = "rain"
        elif any(w in full for w in ["snow", "snowfall"]):
            mtype = "snow"

        threshold = None
        m = re.search(r'(\d+\.?\d*)\s*[°]?\s*[CcFf]', question)
        if m:
            threshold = float(m.group(1))

        is_above = None
        if any(w in full for w in ["above", "exceed", "or higher", "at least"]):
            is_above = True
        elif any(w in full for w in ["below", "under", "or lower", "less than"]):
            is_above = False

        return PolyMarket(
            condition_id=raw.get("conditionId") or raw.get("id", ""),
            question=question,
            description=description,
            end_date=end_date,
            hours_to_resolution=round(hours_left, 1),
            volume_usd=volume,
            token_yes_id=ty.get("token_id", ""),
            token_no_id=tn.get("token_id", ""),
            best_ask_yes=ask,
            best_bid_yes=bid,
            midpoint_yes=mid,
            market_slug=slug,
            detected_city=_detect_city(f"{question} {description}"),
            market_type=mtype,
            threshold_value=threshold,
            is_above=is_above,
            raw=raw,
        )
    except Exception as e:
        logger.debug(f"Parse error: {e}")
        return None


def _markets_from_event(event: Dict, hours_limit: float) -> List[PolyMarket]:
    result = []
    event_end = event.get("endDate", "")
    for m_raw in event.get("markets", []):
        if not m_raw.get("endDate") and event_end:
            m_raw = dict(m_raw)
            m_raw["endDate"] = event_end
        pm = _parse_market(m_raw, hours_limit)
        if pm:
            result.append(pm)
    return result


# === Методи 1-3 (залишаємо без змін) ===
def _method1_events_tag(hours_limit: float) -> List[PolyMarket]:
    found = []
    try:
        r = requests.get(f"{config.GAMMA_URL}/events", params={"tag_slug": "weather", "active": "true", "closed": "false", "limit": 100, "order": "end_date_asc"}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            events = data if isinstance(data, list) else data.get("events", [])
            logger.info(f"Метод 1 (tag_slug=weather): {len(events)} events")
            for ev in events:
                found.extend(_markets_from_event(ev, hours_limit))
    except Exception as e:
        logger.debug(f"Метод 1 error: {e}")
    return found


def _method2_slugs(hours_limit: float) -> List[PolyMarket]:
    found = []
    now = datetime.now(timezone.utc)
    slugs = []
    for day_offset in range(0, 10):
        d = now + timedelta(days=day_offset)
        mon = MONTH_NAMES[d.month]
        for city in WEATHER_CITIES:
            slugs.append(f"highest-temperature-in-{city}-on-{mon}-{d.day}-{d.year}")
            slugs.append(f"lowest-temperature-in-{city}-on-{mon}-{d.day}-{d.year}")
    logger.info(f"Метод 2 (slugs): перевіряємо {len(slugs)} шаблонів...")
    hits = 0
    for slug in slugs:
        try:
            r = requests.get(f"{config.GAMMA_URL}/events", params={"slug": slug, "active": "true"}, timeout=8)
            if r.status_code == 200:
                data = r.json()
                events = [data] if isinstance(data, dict) and data else (data if isinstance(data, list) else [])
                for ev in events:
                    if ev:
                        ms = _markets_from_event(ev, hours_limit)
                        for pm in ms:
                            found.append(pm)
                            hits += 1
                            logger.info(f"  ЗНАЙДЕНО [{pm.detected_city}] {pm.question[:60]} | {pm.hours_to_resolution:.1f}h | YES={pm.midpoint_yes:.3f}")
        except Exception:
            pass
        time.sleep(0.05)
    logger.info(f"Метод 2 (slugs): знайдено {hits} ринків")
    return found


def _method3_ending_soon(hours_limit: float) -> List[PolyMarket]:
    found = []
    try:
        for offset in range(0, 400, 100):
            r = requests.get(f"{config.GAMMA_URL}/events", params={"active": "true", "closed": "false", "order": "end_date_asc", "limit": 100, "offset": offset}, timeout=15)
            if r.status_code != 200:
                break
            data = r.json()
            events = data if isinstance(data, list) else data.get("events", [])
            if not events:
                break
            batch_found = 0
            for ev in events:
                title = (ev.get("title") or ev.get("question") or "").lower()
                slug = (ev.get("slug") or "").lower()
                if any(kw in title or kw in slug for kw in WEATHER_KEYWORDS):
                    ms = _markets_from_event(ev, hours_limit)
                    found.extend(ms)
                    batch_found += len(ms)
            if batch_found:
                logger.info(f"  Метод 3 offset={offset}: +{batch_found} ринків")
    except Exception as e:
        logger.debug(f"Метод 3 error: {e}")
    return found


# === МЕТОД 4 — ПРЯМІ СЛУГИ (оновлений динамічно) ===
def _method4_direct_slugs(hours_limit: float) -> List[PolyMarket]:
    found = []
    now = datetime.now(timezone.utc)
    direct_slugs = []

    for day_offset in range(0, 8):  # сьогодні + 7 днів вперед
        d = now + timedelta(days=day_offset)
        mon = MONTH_NAMES[d.month]
        for city in ["london", "nyc", "new-york", "chicago"]:
            base = f"highest-temperature-in-{city}-on-{mon}-{d.day}-{d.year}"
            direct_slugs.append(base)
            direct_slugs.append(f"lowest-temperature-in-{city}-on-{mon}-{d.day}-{d.year}")
            direct_slugs.append(base.replace("highest-temperature", "highest-temp"))
            direct_slugs.append(base.replace("highest-temperature", "highest-temperature-in"))

    logger.info(f"Метод 4 (direct slugs): перевіряємо {len(direct_slugs)} точних слугів...")
    hits = 0
    for slug in direct_slugs:
        try:
            r = requests.get(f"{config.GAMMA_URL}/events", params={"slug": slug, "active": "true"}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                events = [data] if isinstance(data, dict) and data else (data if isinstance(data, list) else [])
                for ev in events:
                    if ev:
                        ms = _markets_from_event(ev, hours_limit)
                        for pm in ms:
                            found.append(pm)
                            hits += 1
                            logger.info(f"✅ ПРЯМИЙ SLUG HIT: {slug} → [{pm.detected_city}] {pm.question[:70]} | {pm.hours_to_resolution:.1f}h | YES={pm.midpoint_yes:.3f}")
        except Exception as e:
            logger.debug(f"Direct slug {slug} error: {e}")
        time.sleep(0.08)

    logger.info(f"Метод 4 (direct slugs): знайдено {hits} ринків")
    return found


def fetch_weather_markets(force_refresh: bool = False) -> List[PolyMarket]:
    cache_key = "weather_markets"
    if not force_refresh and cache_key in _market_cache:
        ts, markets = _market_cache[cache_key]
        if time.time() - ts < 90:
            return markets

    hours_limit = float(config.MAX_RESOLUTION_HOURS)
    all_markets: List[PolyMarket] = []
    seen: set = set()

    def add(lst):
        n = 0
        for pm in lst:
            if pm.condition_id and pm.condition_id not in seen:
                seen.add(pm.condition_id)
                all_markets.append(pm)
                n += 1
        return n

    n1 = add(_method1_events_tag(hours_limit))
    logger.info(f"Після методу 1 (tag_slug): {n1} нових")

    n2 = add(_method2_slugs(hours_limit))
    logger.info(f"Після методу 2 (slugs): +{n2} нових")

    if len(all_markets) < 5:
        n3 = add(_method3_ending_soon(hours_limit))
        logger.info(f"Після методу 3 (ending_soon): +{n3} нових")

    n4 = add(_method4_direct_slugs(hours_limit))
    logger.info(f"Після методу 4 (direct slugs): +{n4} нових")

    all_markets.sort(key=lambda m: m.hours_to_resolution)
    total = len(all_markets)

    if total == 0:
        logger.warning(f"Знайдено 0 ринків (ліміт {hours_limit:.0f}h). Нові daily temperature ринки з'являються щодня ~00:00 UTC.")
    else:
        logger.info(f"РАЗОМ знайдено {total} weather-ринків:")
        for pm in all_markets[:12]:
            logger.info(f"  [{pm.detected_city or '?'}] {pm.question[:75]} | {pm.hours_to_resolution:.1f}h | YES={pm.midpoint_yes:.3f} | vol=${pm.volume_usd:,.0f}")

    _market_cache[cache_key] = (time.time(), all_markets)
    return all_markets


def get_orderbook_price(token_id: str) -> Optional[Dict]:
    try:
        r = requests.get(f"{config.CLOB_URL}/book", params={"token_id": token_id}, timeout=10)
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
