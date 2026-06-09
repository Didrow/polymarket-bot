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
    "dallas", "buenos-aires", "lagos", "lucknow", "paris", "berlin", "seoul",
    "miami", "seattle", "sydney", "sao-paulo", "munich", "los-angeles",
]


def normalize_condition_id(cid: str) -> str:
    """
    Нормалізує condition_id до єдиного формату (0x + 64 символи).
    Це вирішує проблему дублювання та зависання ринків під час resolution polling.
    """
    if not cid:
        return ""
    cid = str(cid).strip().lower()
    if cid.startswith("0x"):
        cid = cid[2:]
    return "0x" + cid.zfill(64)


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
        "lucknow": "Lucknow", "paris": "Paris", "berlin": "Berlin", "seoul": "Seoul",
        "miami": "Miami", "seattle": "Seattle", "sydney": "Sydney",
        "sao paulo": "Sao Paulo", "são paulo": "Sao Paulo",
        "munich": "Munich", "münchen": "Munich",
        "los angeles": "Los Angeles", "los angeles ca": "Los Angeles",
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
        if hours_left <= 0 or hours_left > hours_limit:
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

        # ВИПРАВЛЕННЯ: ЗАХИСТ ВІД NONE для bestAsk/bestBid (float() від None викликає TypeError)
        best_ask = float(raw.get("bestAsk") or 0.0)
        best_bid = float(raw.get("bestBid") or 0.0)
        
        # Якщо немає продавців — купити можна тільки за 100¢ (неліквідний)
        if best_ask == 0.0:
            best_ask = 1.0
        midpoint = (best_ask + best_bid) / 2.0

        question = raw.get("question", "") or raw.get("title", "")
        description = raw.get("description", "") or ""
        slug = raw.get("slug", "") or ""
        full = f"{question} {description} {slug}".lower()

        # v28: явний skip для non-temperature weather ринків що мають тег 'weather'
        _skip_keywords = [
            "space weather", "spaceweather", "geomagnetic",
            "earthquake", "magnitude", "seismic",
            "flu", "hospitalization", "influenza", "covid",
        ]
        if any(kw in full for kw in _skip_keywords):
            return None

        mtype = "unknown"   # default unknown — відсіює GTA VI, Russia-Ukraine і т.д.
        if any(w in full for w in ["rain", "precipitation"]):
            mtype = "rain"
        elif any(w in full for w in ["snow", "snowfall"]):
            mtype = "snow"
        elif any(w in full for w in ["temperature", "°c", "°f", "highest", "lowest", "degrees"]):
            mtype = "temperature"

        threshold = None
        # Каскад патернів для вилучення температурного порогу.
        # Порядок важливий: специфічні патерни йдуть першими.
        _threshold_patterns = [
            # P1: "20°C", "-5°F", "20 °C"  — явний градус + одиниця (найнадійніший)
            r'(-?\d+\.?\d*)\s*°\s*[CcFf](?!\w)',
            # P2: "20C", "20F"  — без пробілу, але з (?!\w) щоб не зачепити "Chicago", "Fahrenheit"
            r'(-?\d+\.?\d*)[CcFf](?!\w)',
            # P3: "be 20 on April" / "be 20 or below"  — Polymarket categorical без одиниці
            r'\bbe\s+(-?\d+\.?\d*)\s+(?:on\b|or\b)',
            # P4: "exceed 20", "above 20"  — порогові формулювання
            r'\b(?:exceed|above|over)\s+(-?\d+\.?\d*)',
            # P5: "below 20", "under 20"  — нижні пороги
            r'\b(?:below|under)\s+(-?\d+\.?\d*)',
        ]
        for _pat in _threshold_patterns:
            _m = re.search(_pat, question, re.IGNORECASE)
            if _m:
                try:
                    threshold = float(_m.group(1))
                    break
                except Exception:
                    pass

        is_above = None
        if any(w in full for w in ["above", "exceed", "or higher"]):
            is_above = True
        elif any(w in full for w in ["below", "under", "or lower"]):
            is_above = False

        # НОРМАЛІЗАЦІЯ ID ОДРАЗУ НА ЕТАПІ ПАРСИНГУ
        raw_cid = raw.get("conditionId") or raw.get("id", "")
        norm_cid = normalize_condition_id(raw_cid)

        return PolyMarket(
            condition_id=norm_cid,
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
            cid = pm.condition_id  # condition_id тут ВЖЕ нормалізований
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

    # Slug-шаблони (coldmath-specific) — ЛИШЕ якщо tag search знайшов мало
    # Оптимізовано: топ-8 міст, 2 дні, тільки highest/lowest (без bin brute-force)
    # Бюджет часу: 20 секунд максимум, щоб не блокувати бот на Render Free Tier
    _SLUG_CITIES = ["london", "nyc", "tokyo", "chicago", "paris", "berlin", "seoul", "sydney"]
    _SLUG_TIME_BUDGET = 20  # секунд

    if len(all_markets) < 3:
        logger.info("Метод slug-шаблони (fallback, топ-8 міст, 2 дні)...")
        now = datetime.now(timezone.utc)
        slug_start = time.time()
        slug_hits = 0
        _budget_exceeded = False
        for day_offset in range(0, 2):
            if _budget_exceeded:
                break
            d = now + timedelta(days=day_offset)
            mon = MONTH_NAMES[d.month]
            for city in _SLUG_CITIES:
                if time.time() - slug_start > _SLUG_TIME_BUDGET:
                    logger.warning(f"⏱ Slug-scan бюджет {_SLUG_TIME_BUDGET}s вичерпано, зупиняємо")
                    _budget_exceeded = True
                    break
                for temp_type in ["highest-temperature", "lowest-temperature"]:
                    slug = f"{temp_type}-in-{city}-on-{mon}-{d.day}-{d.year}"
                    ms = _fetch_event_by_slug(slug, hours_limit)
                    for pm in ms:
                        if pm.condition_id not in seen:
                            seen.add(pm.condition_id)
                            all_markets.append(pm)
                            slug_hits += 1
                # Early exit якщо вже достатньо ринків
                if len(all_markets) >= 10:
                    break
        elapsed = time.time() - slug_start
        logger.info(f"Slug-шаблони: +{slug_hits} ринків за {elapsed:.1f}s")
    else:
        logger.info(f"Tag search достатній ({len(all_markets)} ринків), slug-scan пропущено")

    all_markets.sort(key=lambda m: m.hours_to_resolution)
    total = len(all_markets)

    if total == 0:
        logger.warning("Знайдено 0 ринків. Нові daily-temperature з’являються ~00:00 UTC")
    else:
        logger.info(f"✅ Знайдено {total} weather-ринків (coldmath-ready):")
        for pm in all_markets[:12]:
            logger.info(f"  [{pm.detected_city or '?'}] {pm.question[:65]} | {pm.hours_to_resolution:.1f}h | ASK={pm.best_ask_yes:.3f} | vol=${pm.volume_usd:,.0f}")

    _market_cache[cache_key] = (time.time(), all_markets)
    return all_markets


def parse_date_from_question(question: str, end_date: datetime) -> Optional[datetime.date]:
    """
    Парсить дату події безпосередньо із запитання ринку.
    Приклад: 'highest temperature in Seoul ... on June 11?' -> June 11
    """
    q_lower = question.lower()
    months_pattern = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
    
    # Шаблон 1: on [Month] [Day]
    m1 = re.search(r'on\s+' + months_pattern + r'\s+(\d+)', q_lower)
    if m1:
        month_name = m1.group(1)[:3]
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
        }
        month_val = months.get(month_name)
        day_val = int(m1.group(2))
        if month_val and 1 <= day_val <= 31:
            try:
                return datetime(end_date.year, month_val, day_val).date()
            except ValueError:
                pass
            
    # Шаблон 2: on [Day] [Month]
    m2 = re.search(r'on\s+(\d+)\s+' + months_pattern, q_lower)
    if m2:
        day_val = int(m2.group(1))
        month_name = m2.group(2)[:3]
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
        }
        month_val = months.get(month_name)
        if month_val and 1 <= day_val <= 31:
            try:
                return datetime(end_date.year, month_val, day_val).date()
            except ValueError:
                pass
            
    return None


def get_target_date(question: str, end_date: datetime, city: str) -> datetime.date:
    """
    Повертає цільову дату події з урахуванням зміщення часових поясів або парсингу запитання.
    """
    parsed = parse_date_from_question(question, end_date)
    if parsed:
        return parsed
    
    # Fallback зміщення часових поясів
    offsets = {
        "NYC": -4, "New York": -4, "Chicago": -5, "Los Angeles": -7, "San Francisco": -7,
        "Miami": -4, "Dallas": -5, "Seattle": -7, "Boston": -4, "Denver": -6, "Atlanta": -4,
        "London": 1, "Paris": 2, "Berlin": 2, "Munich": 2, "Rome": 2, "Madrid": 2, "Amsterdam": 2,
        "Tokyo": 9, "Seoul": 9, "Busan": 9, "Lucknow": 5.5, "Singapore": 8, "Dubai": 4, "Bangkok": 7,
        "Sydney": 10, "Buenos Aires": -3, "Cape Town": 2, "Sao Paulo": -3, "Moscow": 3, "Lagos": 1,
        "Wellington": 12,
    }
    offset = offsets.get(city, 0)
    local_end = end_date + timedelta(hours=offset)
    # Віднімаємо 3 години, щоб гарантовано потрапити на день події
    return (local_end - timedelta(hours=3)).date()

