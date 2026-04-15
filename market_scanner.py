"""
market_scanner.py — Polymarket Weather Bot 2026 (ULTIMATE FIXED v5.0)
Виправлено: правильна фільтрація дат, розширені шаблони, підтримка 2026 року
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
    "london", "nyc", "new-york", "chicago", "los-angeles", "la", "miami",
    "dallas", "houston", "atlanta", "seattle", "boston", "denver", "detroit",
    "phoenix", "las-vegas", "san-francisco", "austin", "portland", "baltimore",
    "minneapolis", "nashville", "charlotte", "orlando", "philadelphia",
    "toronto", "vancouver", "montreal", "calgary", "ottawa",
    "paris", "berlin", "madrid", "rome", "amsterdam", "brussels",
    "dublin", "manchester", "liverpool", "glasgow", "edinburgh",
    "tokyo", "osaka", "kyoto", "seoul", "busan",
    "sydney", "melbourne", "brisbane", "perth", "auckland",
    "singapore", "hong-kong", "bangkok", "kuala-lumpur", "jakarta",
    "dubai", "mumbai", "delhi", "tel-aviv", "istanbul",
]

MONTH_NAMES = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}

MONTH_SHORT = {
    1: "jan", 2: "feb", 3: "mar", 4: "apr",
    5: "may", 6: "jun", 7: "jul", 8: "aug",
    9: "sep", 10: "oct", 11: "nov", 12: "dec",
}

WEATHER_KEYWORDS = [
    "temperature", "highest temperature", "lowest temperature", 
    "high temp", "low temp", "max temp", "min temp",
    "rain", "rainfall", "precipitation", "snow", "snowfall", 
    "weather", "degrees", "fahrenheit", "celsius", "hurricane",
    "storm", "tropical", "will it rain", "will it snow",
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
    """Парсинг дати з різних форматів"""
    if not s:
        return None
    try:
        # ISO format з Z
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        # Без мікросекунд
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        # Просто дата
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _is_future_date(dt: datetime) -> bool:
    """Перевірка що дата в майбутньому (з запасом 1 день)"""
    now = datetime.now(timezone.utc)
    return dt > (now - timedelta(days=1))


def _detect_city(text: str) -> str:
    """Розпізнавання міста з тексту"""
    t = text.lower()
    cities = {
        "london": "London", "new york": "NYC", "nyc": "NYC", 
        "new-york": "NYC", "chicago": "Chicago", "los angeles": "Los Angeles",
        "la ": "Los Angeles", "los-angeles": "Los Angeles", "miami": "Miami",
        "dallas": "Dallas", "houston": "Houston", "atlanta": "Atlanta",
        "seattle": "Seattle", "boston": "Boston", "denver": "Denver",
        "detroit": "Detroit", "phoenix": "Phoenix", "las vegas": "Las Vegas",
        "las-vegas": "Las Vegas", "san francisco": "San Francisco",
        "san-francisco": "San Francisco", "austin": "Austin",
        "portland": "Portland", "baltimore": "Baltimore",
        "minneapolis": "Minneapolis", "nashville": "Nashville",
        "charlotte": "Charlotte", "orlando": "Orlando",
        "philadelphia": "Philadelphia", "toronto": "Toronto",
        "vancouver": "Vancouver", "montreal": "Montreal",
        "calgary": "Calgary", "ottawa": "Ottawa", "paris": "Paris",
        "berlin": "Berlin", "madrid": "Madrid", "rome": "Rome",
        "amsterdam": "Amsterdam", "brussels": "Brussels",
        "dublin": "Dublin", "manchester": "Manchester",
        "liverpool": "Liverpool", "glasgow": "Glasgow",
        "edinburgh": "Edinburgh", "tokyo": "Tokyo", "osaka": "Osaka",
        "kyoto": "Kyoto", "seoul": "Seoul", "busan": "Busan",
        "sydney": "Sydney", "melbourne": "Melbourne",
        "brisbane": "Brisbane", "perth": "Perth", "auckland": "Auckland",
        "singapore": "Singapore", "hong kong": "Hong Kong",
        "hong-kong": "Hong Kong", "bangkok": "Bangkok",
        "kuala lumpur": "Kuala Lumpur", "kuala-lumpur": "Kuala Lumpur",
        "jakarta": "Jakarta", "dubai": "Dubai", "mumbai": "Mumbai",
        "delhi": "Delhi", "tel aviv": "Tel Aviv", "tel-aviv": "Tel Aviv",
        "istanbul": "Istanbul",
    }
    for key, city in cities.items():
        if key in t:
            return city
    return ""


def _parse_market(raw: Dict, hours_limit: float) -> Optional[PolyMarket]:
    """Парсинг ринку з перевіркою всіх умов"""
    try:
        # Отримуємо дату закінчення
        end_str = (raw.get("endDate") or raw.get("end_date_iso") or 
                   raw.get("end_date") or raw.get("resolution_date") or "")
        if not end_str:
            return None
            
        end_date = _parse_dt(end_str)
        if not end_date:
            return None

        # КРИТИЧНО: Перевіряємо що ринок ще активний (в майбутньому)
        if not _is_future_date(end_date):
            logger.debug(f"Пропускаємо закритий ринок: {raw.get('question', '')[:50]}")
            return None

        # Розрахунок годин до закінчення
        hours_left = (end_date - datetime.now(timezone.utc)).total_seconds() / 3600
        
        # Перевірка ліміту годин
        if hours_left <= 0 or hours_left > hours_limit:
            return None

        # Отримуємо об'єм
        volume = 0.0
        for k in ["volume", "volumeNum", "liquidity", "totalVolume"]:
            try:
                v = float(raw.get(k) or 0)
                if v > volume:
                    volume = v
            except Exception:
                pass

        # Мінімальний об'єм (з config)
        if volume < config.MIN_MARKET_VOLUME_USD:
            return None

        # Отримуємо токени
        tokens = raw.get("tokens", [])
        if not tokens and "markets" in raw:
            # Якщо це event, беремо перший market
            markets = raw.get("markets", [])
            if markets:
                tokens = markets[0].get("tokens", [])
        
        if not tokens:
            return None

        ty = next((t for t in tokens if str(t.get("outcome", "")).upper() == "YES"), None)
        tn = next((t for t in tokens if str(t.get("outcome", "")).upper() == "NO"), None)
        
        if not ty or not tn:
            return None

        # Ціни
        ask = float(ty.get("price", 0) or 0)
        bid = float(tn.get("price", 0) or 0)
        
        # Якщо ціни 0, пробуємо inverse
        if ask == 0:
            ask = 1.0 - bid if bid > 0 else 0.5
        if bid == 0:
            bid = 1.0 - ask if ask > 0 else 0.5
            
        mid = (ask + bid) / 2 if (ask > 0 and bid > 0) else 0.5

        # Тексти для аналізу
        question = raw.get("question", "") or raw.get("title", "")
        description = raw.get("description", "") or ""
        slug = raw.get("slug", "") or raw.get("ticker", "")
        full = f"{question} {description} {slug}".lower()

        # Визначаємо тип ринку
        mtype = "temperature"
        if any(w in full for w in ["rain", "precipitation", "rainfall", "will it rain"]):
            mtype = "rain"
        elif any(w in full for w in ["snow", "snowfall", "will it snow"]):
            mtype = "snow"
        elif any(w in full for w in ["hurricane", "storm", "tropical", "cyclone"]):
            mtype = "storm"

        # Парсинг порогового значення
        threshold = None
        patterns = [
            r'(\d+\.?\d*)\s*[°]?\s*[CcFf]',
            r'(\d+\.?\d*)\s+degrees',
            r'above\s+(\d+\.?\d*)',
            r'below\s+(\d+\.?\d*)',
        ]
        for pattern in patterns:
            m = re.search(pattern, question, re.IGNORECASE)
            if m:
                try:
                    threshold = float(m.group(1))
                    break
                except:
                    pass

        # Визначення напрямку (above/below)
        is_above = None
        above_words = ["above", "exceed", "or higher", "at least", "more than", "greater than", "higher than"]
        below_words = ["below", "under", "or lower", "less than", "lower than", "at most", "maximum"]
        
        if any(w in full for w in above_words):
            is_above = True
        elif any(w in full for w in below_words):
            is_above = False

        return PolyMarket(
            condition_id=raw.get("conditionId") or raw.get("id", "") or raw.get("marketId", ""),
            question=question,
            description=description,
            end_date=end_date,
            hours_to_resolution=round(hours_left, 1),
            volume_usd=volume,
            token_yes_id=ty.get("token_id", "") or ty.get("id", ""),
            token_no_id=tn.get("token_id", "") or tn.get("id", ""),
            best_ask_yes=ask,
            best_bid_yes=bid,
            midpoint_yes=mid,
            market_slug=slug,
            detected_city=_detect_city(f"{question} {description} {slug}"),
            market_type=mtype,
            threshold_value=threshold,
            is_above=is_above,
            raw=raw,
        )
    except Exception as e:
        logger.debug(f"Parse error: {e}")
        return None


def _markets_from_event(event: Dict, hours_limit: float) -> List[PolyMarket]:
    """Витягуємо ринки з події"""
    result = []
    event_end = event.get("endDate", "")
    
    # Перевіряємо саму подію як ринок (якщо є tokens)
    if "tokens" in event:
        pm = _parse_market(event, hours_limit)
        if pm:
            result.append(pm)
    
    # Перевіряємо внутрішні markets
    for m_raw in event.get("markets", []):
        if not m_raw.get("endDate") and event_end:
            m_raw = dict(m_raw)
            m_raw["endDate"] = event_end
        pm = _parse_market(m_raw, hours_limit)
        if pm:
            result.append(pm)
    
    return result


def _method1_events_tag(hours_limit: float) -> List[PolyMarket]:
    """Метод 1: /events?tag_slug=weather з фільтрацією майбутніх дат"""
    found = []
    try:
        r = requests.get(
            f"{config.GAMMA_URL}/events", 
            params={
                "tag_slug": "weather", 
                "active": "true", 
                "closed": "false",
                "archived": "false",  # Не архівні
                "limit": 100, 
                "order": "end_date_asc"
            }, 
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            events = data if isinstance(data, list) else data.get("events", [])
            logger.info(f"Метод 1 (tag_slug=weather): {len(events)} events отримано")
            
            for ev in events:
                markets = _markets_from_event(ev, hours_limit)
                found.extend(markets)
                
            # Логуємо що знайшли
            if found:
                logger.info(f"Метод 1: знайдено {len(found)} активних ринків")
                for pm in found[:3]:
                    logger.info(f"  ✓ [{pm.detected_city}] {pm.question[:60]} | {pm.hours_to_resolution:.1f}h")
            else:
                logger.warning(f"Метод 1: 0 активних ринків (всі закриті або не відповідають критеріям)")
                
    except Exception as e:
        logger.warning(f"Метод 1 error: {e}")
    return found


def _method2_slugs(hours_limit: float) -> List[PolyMarket]:
    """Метод 2: slug-шаблони для 2026 року"""
    found = []
    now = datetime.now(timezone.utc)
    current_year = now.year  # 2026
    
    slugs = []
    
    # Генеруємо шаблони на 14 днів вперед
    for day_offset in range(0, 14):
        d = now + timedelta(days=day_offset)
        mon = MONTH_NAMES[d.month]
        mon_short = MONTH_SHORT[d.month]
        
        for city in WEATHER_CITIES:
            # Різні варіанти slug
            variants = [
                f"highest-temperature-in-{city}-on-{mon}-{d.day}-{d.year}",
                f"highest-temperature-in-{city}-on-{mon}-{d.day:02d}-{d.year}",
                f"lowest-temperature-in-{city}-on-{mon}-{d.day}-{d.year}",
                f"lowest-temperature-in-{city}-on-{mon}-{d.day:02d}-{d.year}",
                f"will-it-rain-in-{city}-on-{mon}-{d.day}-{d.year}",
                f"will-it-rain-in-{city}-on-{mon}-{d.day:02d}-{d.year}",
                f"will-it-snow-in-{city}-on-{mon}-{d.day}-{d.year}",
                f"rain-in-{city}-{mon}-{d.day}-{d.year}",
                f"{city}-weather-{mon}-{d.day}-{d.year}",
                f"{city}-{mon}-{d.day}-weather",
            ]
            slugs.extend(variants)
    
    # Унікальні slugs
    slugs = list(set(slugs))
    logger.info(f"Метод 2 (slugs): перевіряємо {len(slugs)} унікальних шаблонів...")
    
    hits = 0
    checked = 0
    
    for slug in slugs:
        checked += 1
        try:
            r = requests.get(
                f"{config.GAMMA_URL}/events", 
                params={"slug": slug, "active": "true"}, 
                timeout=8
            )
            if r.status_code == 200:
                data = r.json()
                # Може бути одиночний об'єкт або список
                if isinstance(data, dict) and data:
                    events = [data]
                elif isinstance(data, list):
                    events = data
                else:
                    events = []
                
                for ev in events:
                    if ev:
                        ms = _markets_from_event(ev, hours_limit)
                        for pm in ms:
                            found.append(pm)
                            hits += 1
                            logger.info(f"  🎯 HIT: {slug} → [{pm.detected_city}] {pm.question[:50]}")
            
            # Логуємо прогрес кожні 100 перевірок
            if checked % 100 == 0:
                logger.info(f"  Прогрес: {checked}/{len(slugs)} перевірено, {hits} знайдено")
                
        except Exception as e:
            logger.debug(f"Slug {slug} error: {e}")
            
        time.sleep(0.03)  # Не перевантажуємо API
    
    logger.info(f"Метод 2 (slugs): перевірено {checked}, знайдено {hits} ринків")
    return found


def _method3_search(hours_limit: float) -> List[PolyMarket]:
    """Метод 3: пошук за ключовими словами"""
    found = []
    search_terms = ["temperature", "rain", "weather", "highest temperature", "lowest temperature"]
    
    for term in search_terms:
        try:
            r = requests.get(
                f"{config.GAMMA_URL}/events",
                params={
                    "active": "true",
                    "search": term,
                    "limit": 50,
                    "closed": "false"
                },
                timeout=15
            )
            if r.status_code == 200:
                data = r.json()
                events = data if isinstance(data, list) else data.get("events", [])
                
                for ev in events:
                    ms = _markets_from_event(ev, hours_limit)
                    found.extend(ms)
                    
                logger.info(f"  Пошук '{term}': {len(events)} events, {len([m for m in found if m])} ринків")
                
        except Exception as e:
            logger.debug(f"Search '{term}' error: {e}")
    
    return found


def _method4_ending_soon(hours_limit: float) -> List[PolyMarket]:
    """Метод 4: сканування найближчих подій"""
    found = []
    try:
        # Беремо перші 200 подій що закінчуються найскоріше
        for offset in range(0, 200, 50):
            r = requests.get(
                f"{config.GAMMA_URL}/events",
                params={
                    "active": "true",
                    "closed": "false",
                    "archived": "false",
                    "order": "end_date_asc",
                    "limit": 50,
                    "offset": offset
                },
                timeout=15
            )
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
                desc = (ev.get("description") or "").lower()
                
                # Перевіряємо weather ключові слова
                if any(kw in title or kw in slug or kw in desc for kw in WEATHER_KEYWORDS):
                    ms = _markets_from_event(ev, hours_limit)
                    found.extend(ms)
                    batch_found += len(ms)
            
            if batch_found:
                logger.info(f"  Метод 4 offset={offset}: +{batch_found} weather ринків")
            
            # Якщо події вже далеко в майбутньому — зупиняємось
            if events and len(events) > 0:
                last_end = events[-1].get("endDate", "")
                if last_end:
                    last_dt = _parse_dt(last_end)
                    if last_dt and (last_dt - datetime.now(timezone.utc)).days > 30:
                        break
                        
    except Exception as e:
        logger.debug(f"Метод 4 error: {e}")
    
    return found


def _method5_direct_api(hours_limit: float) -> List[PolyMarket]:
    """Метод 5: прямий запит до markets endpoint"""
    found = []
    try:
        # Спробуємо отримати активні ринки напряму
        r = requests.get(
            f"{config.GAMMA_URL}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": 100,
            },
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            markets = data if isinstance(data, list) else data.get("markets", [])
            
            for m in markets:
                # Фільтруємо за ключовими словами
                text = f"{m.get('question', '')} {m.get('slug', '')}".lower()
                if any(kw in text for kw in WEATHER_KEYWORDS):
                    pm = _parse_market(m, hours_limit)
                    if pm:
                        found.append(pm)
                        
            logger.info(f"Метод 5 (direct markets): знайдено {len(found)} weather ринків")
            
    except Exception as e:
        logger.debug(f"Метод 5 error: {e}")
    
    return found


def fetch_weather_markets(force_refresh: bool = False) -> List[PolyMarket]:
    """Головна функція — 5 методів пошуку"""
    cache_key = "weather_markets"
    if not force_refresh and cache_key in _market_cache:
        ts, markets = _market_cache[cache_key]
        if time.time() - ts < 60:  # Кеш 1 хвилина
            return markets

    hours_limit = float(config.MAX_RESOLUTION_HOURS)
    logger.info(f"=== Пошук weather ринків (ліміт: {hours_limit:.0f}h, min volume: ${config.MIN_MARKET_VOLUME_USD}) ===")
    
    all_markets: List[PolyMarket] = []
    seen: set = set()

    def add(lst: List[PolyMarket]) -> int:
        n = 0
        for pm in lst:
            if pm.condition_id and pm.condition_id not in seen:
                seen.add(pm.condition_id)
                all_markets.append(pm)
                n += 1
        return n

    # Метод 1: Тег weather
    n1 = add(_method1_events_tag(hours_limit))
    logger.info(f"Після методу 1 (tag_slug): {n1} нових, всього: {len(all_markets)}")

    # Метод 2: Slug шаблони
    n2 = add(_method2_slugs(hours_limit))
    logger.info(f"Після методу 2 (slugs): +{n2} нових, всього: {len(all_markets)}")

    # Метод 3: Пошук
    if len(all_markets) < 10:
        n3 = add(_method3_search(hours_limit))
        logger.info(f"Після методу 3 (search): +{n3} нових, всього: {len(all_markets)}")

    # Метод 4: Ending soon
    if len(all_markets) < 10:
        n4 = add(_method4_ending_soon(hours_limit))
        logger.info(f"Після методу 4 (ending_soon): +{n4} нових, всього: {len(all_markets)}")

    # Метод 5: Direct markets
    if len(all_markets) < 5:
        n5 = add(_method5_direct_api(hours_limit))
        logger.info(f"Після методу 5 (direct): +{n5} нових, всього: {len(all_markets)}")

    # Сортуємо за часом до закінчення
    all_markets.sort(key=lambda m: m.hours_to_resolution)
    total = len(all_markets)

    # Фінальний звіт
    if total == 0:
        logger.warning(f"❌ Знайдено 0 активних weather-ринків")
        logger.warning(f"   Ліміт: {hours_limit:.0f}h | Min volume: ${config.MIN_MARKET_VOLUME_USD}")
        logger.warning(f"   Можливі причини:")
        logger.warning(f"   1. На Polymarket зараз немає активних weather-ринків")
        logger.warning(f"   2. Ринки мають інші теги/slug")
        logger.warning(f"   3. Всі ринки закриті (перевір дати в API)")
        logger.warning(f"   4. Недостатній об'єм (< ${config.MIN_MARKET_VOLUME_USD})")
    else:
        logger.info(f"✅ РАЗОМ знайдено {total} активних weather-ринків:")
        for i, pm in enumerate(all_markets[:15], 1):
            city = pm.detected_city or "?"
            logger.info(f"   {i}. [{city}] {pm.question[:65]} | {pm.hours_to_resolution:.1f}h | YES={pm.midpoint_yes:.3f} | vol=${pm.volume_usd:,.0f}")

    _market_cache[cache_key] = (time.time(), all_markets)
    return all_markets


def get_orderbook_price(token_id: str) -> Optional[Dict]:
    """Отримання ціни з ордербука"""
    if not token_id:
        return None
        
    try:
        r = requests.get(
            f"{config.CLOB_URL}/book", 
            params={"token_id": token_id}, 
            timeout=10
        )
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
            "midpoint": midpoint
        }
    except Exception as e:
        logger.debug(f"Orderbook error for {token_id[:20]}: {e}")
        return None
