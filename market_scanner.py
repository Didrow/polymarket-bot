"""
market_scanner.py — Polymarket Weather Bot 2026 (під @coldmath — точні slug + highest/lowest)
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

MONTH_NAMES = {1: "january", 2: "february", 3: "march", 4: "april", 5: "may", 6: "june",
               7: "july", 8: "august", 9: "september", 10: "october", 11: "november", 12: "december"}

WEATHER_CITIES = [
    "london", "nyc", "new-york", "cape-town", "wellington", "moscow", "tokyo",
    "san-francisco", "chicago", "chengdu", "ankara", "busan", "jeddah", "karachi",
    "dallas", "buenos-aires", "lagos", "lucknow", "paris", "berlin", "seoul"
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
        "london": "London", "new york": "NYC", "nyc": "NYC", "cape town": "Cape Town",
        "wellington": "Wellington", "moscow": "Moscow", "tokyo": "Tokyo",
        "san francisco": "San Francisco", "chicago": "Chicago", "chengdu": "Chengdu",
        "ankara": "Ankara", "busan": "Busan", "jeddah": "Jeddah", "karachi": "Karachi",
        "dallas": "Dallas", "buenos aires": "Buenos Aires", "lagos": "Lagos",
        "lucknow": "Lucknow", "paris": "Paris", "berlin": "Berlin", "seoul": "Seoul"
    }
    for key, city in cities.items():
        if key in t:
            return city
    return ""

def _parse_market_from_api(raw: Dict, hours_limit: float) -> Optional[PolyMarket]:
    try:
        if not raw.get("acceptingOrders", False):
            return None

        end_str = raw.get("endDate") or raw.get("endDateIso") or ""
        if not end_str:
            return None
        end_date = _parse_dt(end_str)
        if not end_date:
            return None

        now = datetime.now(timezone.utc)
        hours_left = (end_date - now).total_seconds() / 3600
        if hours_left < 4.0 or hours_left > hours_limit:   # було <2
    return None

        volume = 0.0
        for k in ["volume", "volumeNum", "liquidity", "volume24hr", "totalVolume"]:
            try:
                v = float(raw.get(k) or 0)
                if v > volume:
                    volume = v
            except Exception:
                pass

        clob_str = raw.get("clobTokenIds", "[]")
        token_ids = json.loads(clob_str) if isinstance(clob_str, str) else clob_str
        if len(token_ids) < 2:
            return None
        token_yes_id = str(token_ids[0])
        token_no_id = str(token_ids[1])

        prices_str = raw.get("outcomePrices", "[\"0.5\", \"0.5\"]")
        prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
        if len(prices) < 2:
            prices = ["0.5", "0.5"]
        yes_price = float(prices[0]) if prices[0] else 0.5
        no_price = float(prices[1]) if prices[1] else 0.5
        if yes_price <= 0:
            yes_price = 0.001
        if no_price <= 0:
            no_price = 0.001

        best_ask = float(raw.get("bestAsk") or yes_price)
        best_bid = float(raw.get("bestBid") or yes_price)
        midpoint = (best_ask + best_bid) / 2

        question = raw.get("question", "") or raw.get("title", "")
        description = raw.get("description", "") or ""
        slug = raw.get("slug", "") or ""
        full = f"{question} {description} {slug}".lower()

        mtype = "temperature"
        if any(w in full for w in ["rain", "precipitation"]):
            mtype = "rain"
        elif any(w in full for w in ["snow", "snowfall"]):
            mtype = "snow"

        threshold = None
        m = re.search(r'(\d+\.?\d*)\s*[°]?\s*[CcFf]', question)
        if m:
            try:
                threshold = float(m.group(1))
            except Exception:
                pass

        is_above = None
        if any(w in full for w in ["above", "exceed", "or higher"]):
            is_above = True
        elif any(w in full for w in ["below", "under", "or lower"]):
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
    result = []
    event_end = event.get("endDate", "")
    for m_raw in event.get("markets", []):
        if not m_raw.get("endDate") and event_end:
            m_raw = dict(m_raw)
            m_raw["endDate"] = event_end
        pm = _parse_market_from_api(m_raw, hours_limit)
        if pm:
            result.append(pm)
    return result

def _fetch_events_by_tag(tag_slug: str, hours_limit: float) -> List[PolyMarket]:
    found = []
    try:
        r = requests.get(
            f"{config.GAMMA_URL}/events",
            params={"tag_slug": tag_slug, "active": "true", "closed": "false", "limit": 100},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            events = data if isinstance(data, list) else data.get("events", [])
            for ev in events:
                found.extend(_extract_markets_from_event(ev, hours_limit))
    except Exception as e:
        logger.debug(f"tag={tag_slug} error: {e}")
    return found

def _fetch_event_by_slug(slug: str, hours_limit: float) -> List[PolyMarket]:
    try:
        r = requests.get(f"{config.GAMMA_URL}/events", params={"slug": slug}, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json()
        events = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        result = []
        for ev in events:
            result.extend(_extract_markets_from_event(ev, hours_limit))
        return result
    except Exception:
        return []

def fetch_weather_markets(force_refresh: bool = False) -> List[PolyMarket]:
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

    logger.info(f"Пошук weather-ринків (coldmath-style, ліміт={hours_limit:.0f}h)...")

    # Tag-based
    n1 = add(_fetch_events_by_tag("daily-temperature", hours_limit))
    n2 = add(_fetch_events_by_tag("weather", hours_limit))
    n3 = add(_fetch_events_by_tag("highest-temperature", hours_limit))
    logger.info(f"Tag search: daily-temperature={n1}, weather={n2}, highest={n3}")

    # Slug-шаблони (coldmath-specific)
    if len(all_markets) < 15:
        logger.info("Метод slug-шаблони (highest/lowest + bin)...")
        now = datetime.now(timezone.utc)
        slug_hits = 0
        for day_offset in range(0, 5):
            d = now + timedelta(days=day_offset)
            mon = MONTH_NAMES[d.month]
            for city in WEATHER_CITIES:
                # highest / lowest
                for temp_type in ["highest-temperature", "lowest-temperature"]:
                    slug = f"{temp_type}-in-{city}-on-{mon}-{d.day}-{d.year}"
                    ms = _fetch_event_by_slug(slug, hours_limit)
                    for pm in ms:
                        if pm.condition_id not in seen:
                            seen.add(pm.condition_id)
                            all_markets.append(pm)
                            slug_hits += 1
                # bin-ринки (наприклад will-the-highest...-be-18)
                for temp in range(5, 35):
                    slug = f"will-the-highest-temperature-in-{city}-be-{temp}-on-{mon}-{d.day}"
                    ms = _fetch_event_by_slug(slug, hours_limit)
                    for pm in ms:
                        if pm.condition_id not in seen:
                            seen.add(pm.condition_id)
                            all_markets.append(pm)
                            slug_hits += 1
            time.sleep(0.03)
        logger.info(f"Slug-шаблони: +{slug_hits} ринків")

    all_markets.sort(key=lambda m: m.hours_to_resolution)
    total = len(all_markets)

    if total == 0:
        logger.warning("Знайдено 0 ринків. Нові daily-temperature з’являються ~00:00 UTC")
    else:
        logger.info(f"✅ Знайдено {total} weather-ринків (coldmath-ready):")
        for pm in all_markets[:12]:
            logger.info(f"  [{pm.detected_city or '?'}] {pm.question[:65]} | {pm.hours_to_resolution:.1f}h | YES={pm.midpoint_yes:.3f} | vol=${pm.volume_usd:,.0f}")

    _market_cache[cache_key] = (time.time(), all_markets)
    return all_markets
