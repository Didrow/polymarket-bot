"""
market_scanner.py — Polymarket Weather Bot 2026 (ФІНАЛЬНА ВЕРСІЯ 14.04.2026)
Максимально агресивний пошук daily temperature/rain ринків
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


def _detect_city(text: str) -> str:
    text_lower = text.lower()
    city_map = {
        "london": "London", "nyc": "NYC", "new york": "NYC",
        "chicago": "Chicago", "la": "Los Angeles", "los angeles": "Los Angeles",
        "paris": "Paris", "berlin": "Berlin"
    }
    for key, city in city_map.items():
        if key in text_lower:
            return city
    for city in config.TARGET_CITIES:
        if city.lower() in text_lower:
            return city
    return ""


def _detect_market_type(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in ["rain", "precipitation", "rainfall"]):
        return "rain"
    if any(w in text_lower for w in ["snow", "snowfall"]):
        return "snow"
    if any(w in text_lower for w in ["temperature", "highest", "lowest", "degrees"]):
        return "temperature"
    return "weather"


def _extract_threshold(text: str) -> Optional[float]:
    patterns = [r'(\d+\.?\d*)\s*°?\s*[FfCc]', r'above\s+(\d+)', r'below\s+(\d+)']
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except:
                pass
    return None


def _parse_market(raw: Dict) -> Optional[PolyMarket]:
    try:
        end_date_str = raw.get("endDate") or raw.get("end_date_iso")
        if not end_date_str:
            return None

        for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"]:
            try:
                if end_date_str.endswith("Z"):
                    end_date = datetime.strptime(end_date_str, fmt).replace(tzinfo=timezone.utc)
                else:
                    end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                break
            except:
                continue
        else:
            return None

        hours_left = (end_date - datetime.now(timezone.utc)).total_seconds() / 3600
        if hours_left <= 0 or hours_left > config.MAX_RESOLUTION_HOURS:
            return None

        volume = float(raw.get("volume", 0) or raw.get("volumeNum", 0) or 0)

        tokens = raw.get("tokens", [])
        token_yes = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), None)
        token_no = next((t for t in tokens if t.get("outcome", "").upper() == "NO"), None)
        if not token_yes or not token_no:
            return None

        best_ask_yes = float(token_yes.get("price", 0.5))
        best_bid_yes = 1 - float(token_no.get("price", 0.5))
        midpoint_yes = (best_ask_yes + best_bid_yes) / 2

        question = raw.get("question", "")
        full_text = f"{question} {raw.get('description', '')}"

        return PolyMarket(
            condition_id=raw.get("conditionId", raw.get("id", "")),
            question=question,
            description=raw.get("description", ""),
            end_date=end_date,
            hours_to_resolution=round(hours_left, 1),
            volume_usd=volume,
            token_yes_id=token_yes.get("token_id", ""),
            token_no_id=token_no.get("token_id", ""),
            best_ask_yes=best_ask_yes,
            best_bid_yes=best_bid_yes,
            midpoint_yes=midpoint_yes,
            detected_city=_detect_city(full_text),
            market_type=_detect_market_type(full_text),
            threshold_value=_extract_threshold(full_text),
            is_above=None,  # можна розширити
            raw=raw,
        )
    except Exception as e:
        logger.debug(f"Parse error: {e}")
        return None


def fetch_weather_markets(force_refresh: bool = False) -> List[PolyMarket]:
    if not force_refresh and "weather_markets" in _market_cache:
        ts, markets = _market_cache["weather_markets"]
        if time.time() - ts < 60:
            return markets

    all_markets: List[PolyMarket] = []

    # 1. Tag + keyword базовий пошук
    for search_type in ["tag", "keyword"]:
        try:
            url = f"{config.GAMMA_URL}/markets"
            params = {
                search_type: "weather" if search_type == "tag" else "temperature",
                "active": "true",
                "closed": "false",
                "limit": 100,
                "order": "end_date"
            }
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            raw_list = r.json() if isinstance(r.json(), list) else r.json().get("markets", [])
            for raw in raw_list:
                m = _parse_market(raw)
                if m and m not in all_markets:
                    all_markets.append(m)
        except Exception as e:
            logger.debug(f"{search_type} search error: {e}")

    # 2. Агресивний targeted search для daily temperature
    targeted = ["highest temperature", "lowest temperature", "London", "NYC", "New York", "on April"]
    for kw in targeted:
        try:
            url = f"{config.GAMMA_URL}/markets"
            params = {"keyword": kw, "active": "true", "limit": 100, "order": "end_date"}
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 200:
                raw_list = r.json() if isinstance(r.json(), list) else r.json().get("markets", [])
                for raw in raw_list:
                    m = _parse_market(raw)
                    if m and m.condition_id not in [x.condition_id for x in all_markets]:
                        all_markets.append(m)
                        logger.info(f"✅ Targeted '{kw}' → {m.question[:80]}... | {m.hours_to_resolution:.1f}h")
        except Exception as e:
            logger.debug(f"Targeted '{kw}' error: {e}")

    all_markets = list({m.condition_id: m for m in all_markets}.values())  # dedup
    all_markets.sort(key=lambda m: m.hours_to_resolution)

    logger.info(f"Знайдено {len(all_markets)} валідних weather-ринків (< {config.MAX_RESOLUTION_HOURS}h)")

    for m in all_markets[:10]:
        logger.info(f"  [{m.detected_city or '???'}] {m.question[:75]}... | {m.hours_to_resolution:.1f}h | YES={m.midpoint_yes:.3f} | vol=${m.volume_usd:,.0f}")

    _market_cache["weather_markets"] = (time.time(), all_markets)
    return all_markets
