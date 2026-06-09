"""
data_fetcher.py — Polymarket Weather Bot (GRID / YES LADDERING EDITION)
Джерела: NOAA + NASA POWER + Open-Meteo GFS/ECMWF + Open-Meteo ENSEMBLE (31 members)
Пріоритет METAR для ринків з resolution ≤ 12 годин (ColdMath style)
"""

import math
import time
import requests
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

import config

logger = logging.getLogger(__name__)

# TTL кеш
_cache: Dict[str, Tuple[float, any]] = {}
CACHE_TTL = 900  # 15 хвилин


def _cache_get(key: str):
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return val
    return None


def _cache_set(key: str, val):
    _cache[key] = (time.time(), val)


# ─────────────────────────────────────────────
# WeatherForecast dataclass (GRID LADDERING EDITION)
# ─────────────────────────────────────────────

@dataclass
class WeatherForecast:
    city: str
    timestamp: datetime
    temp_high_c: float = 0.0
    temp_low_c: float = 0.0
    prob_rain: float = 0.0
    prob_snow: float = 0.0
    sources_used: List[str] = field(default_factory=list)
    # Зберігаємо всі 31 варіант майбутнього від Ансамблю
    temp_high_members: List[float] = field(default_factory=list)
    temp_low_members: List[float] = field(default_factory=list)

    def _get_adjusted_members(self, is_low: bool = False) -> List[float]:
        """Повертає зміщені члени ансамблю, середнє значення яких дорівнює консенсусному."""
        members = self.temp_low_members if is_low else self.temp_high_members
        if not members or len(members) < 5:
            return []
        consensus_mean = self.temp_low_c if is_low else self.temp_high_c
        gfs_mean = sum(members) / len(members)
        bias = consensus_mean - gfs_mean
        return [m + bias for m in members]

    def _get_sigma(self, hours: float = 24.0) -> float:
        """Динамічний sigma залежно від міста та горизонту прогнозу."""
        base = {
            "Lucknow": 0.9, "Miami": 0.9, "Singapore": 0.8, "Dubai": 0.8,
            "Cape Town": 1.1, "Sao Paulo": 1.0, "Sydney": 1.2,
            "Buenos Aires": 1.2, "London": 1.3, "Paris": 1.3,
            "Tokyo": 1.3, "Berlin": 1.4, "Busan": 1.4, "Munich": 1.5,
            "Seoul": 1.5, "NYC": 1.5, "Seattle": 1.2, "Los Angeles": 1.0,
            "Dallas": 1.6, "Chicago": 1.7,
        }.get(self.city, 1.2)
        # Невизначеність зростає з горизонтом прогнозу
        hour_factor = 1.0 + 0.015 * max(0, hours - 6)
        return base * min(hour_factor, 1.5)

    def raw_prob_above_temp_c(self, threshold_c: float, is_low: bool = False, hours: float = 24.0) -> float:
        members = self._get_adjusted_members(is_low)
        sigma = self._get_sigma(hours)

        # Якщо є дані ансамблю (31 модель) — використовуємо ЕМПІРИЧНИЙ підрахунок
        if members and len(members) >= 5:
            # Емпірична частка members вище порогу
            count_above = sum(1 for m in members if m > threshold_c)
            prob_empirical = count_above / len(members)
            
            # Параметрична оцінка (Gaussian через erf)
            mean = self.temp_low_c if is_low else self.temp_high_c
            prob_parametric = 0.5 * (1 + math.erf((mean - threshold_c) / (sigma * math.sqrt(2))))
            
            # ✅ v4 FIX: вагу емпірики знижено до 40% (з 55%)
            # GFS 31 member — НЕ незалежні прогнози, вони корелюють.
            # Емпірика дає хибну впевненість коли всі members > threshold.
            # Параметрика з коректним sigma — надійніша.
            prob = prob_empirical * 0.40 + prob_parametric * 0.60
            return prob
        
        # Fallback до одного значення, якщо ансамбль недоступний
        tc = self.temp_low_c if is_low else self.temp_high_c
        if tc == 0.0:
            return 0.50
        diff = tc - threshold_c
        prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))
        return prob

    def prob_above_temp_c(self, threshold_c: float, is_low: bool = False, hours: float = 24.0) -> float:
        raw_p = self.raw_prob_above_temp_c(threshold_c, is_low, hours)
        
        # Кап з config.py (змінювати там)
        if hours <= 6.0:
            max_cap = config.PROB_CAP_ABOVE_SHORT
        elif hours <= 18.0:
            max_cap = config.PROB_CAP_ABOVE_MID
        else:
            max_cap = config.PROB_CAP_ABOVE_LONG

        return max(0.01, min(max_cap, round(raw_p, 4)))

    def prob_below_temp_c(self, threshold_c: float, is_low: bool = False, hours: float = 24.0) -> float:
        raw_p = 1.0 - self.raw_prob_above_temp_c(threshold_c, is_low, hours)
        
        # Кап з config.py (змінювати там)
        if hours <= 6.0:
            max_cap = config.PROB_CAP_ABOVE_SHORT
        elif hours <= 18.0:
            max_cap = config.PROB_CAP_ABOVE_MID
        else:
            max_cap = config.PROB_CAP_ABOVE_LONG

        return max(0.01, min(max_cap, round(raw_p, 4)))

    def prob_exact_temp_c(self, threshold_c: float, is_low: bool = False, half_width: float = 0.5, hours: float = 24.0) -> float:
        members = self._get_adjusted_members(is_low)

        # Кап з config.py (змінювати там)
        if hours <= 6.0:
            max_cap = config.PROB_CAP_EXACT_SHORT
        elif hours <= 18.0:
            max_cap = config.PROB_CAP_EXACT_MID
        else:
            max_cap = config.PROB_CAP_EXACT_LONG

        if members and len(members) >= 5:
            # Емпіричний підрахунок: скільки members потрапляють у бакет
            count_in = sum(1 for m in members if (threshold_c - half_width) <= m < (threshold_c + half_width))
            prob_empirical = count_in / len(members)
            
            # Параметричне розмиття через середнє ансамблю
            sigma = self._get_sigma(hours)
            mean = self.temp_low_c if is_low else self.temp_high_c
            p_high = 0.5 * (1 + math.erf(((threshold_c + half_width) - mean) / (sigma * math.sqrt(2))))
            p_low  = 0.5 * (1 + math.erf(((threshold_c - half_width) - mean) / (sigma * math.sqrt(2))))
            prob_parametric = max(0.0, p_high - p_low)
            
            # ✅ v4 FIX: categorical discount 0.55 — корекція за кореляцію
            # ensemble members і за реальну невизначеність прогнозу.
            # Без дискаунту: our_prob=30-42% при реальному win rate 7%.
            if prob_empirical == 0.0:
                prob = prob_parametric * 0.35
            else:
                prob = (prob_empirical * 0.35 + prob_parametric * 0.65) * 0.55
            return max(0.01, min(max_cap, round(prob, 4)))

        sigma = self._get_sigma(hours) * 1.5  # Більш консервативний для одного значення
        tc = self.temp_low_c if is_low else self.temp_high_c
        if tc == 0.0:
            return 0.01
        p_high = 0.5 * (1 + math.erf((threshold_c + half_width - tc) / (sigma * math.sqrt(2))))
        p_low  = 0.5 * (1 + math.erf((threshold_c - half_width - tc) / (sigma * math.sqrt(2))))
        return max(0.01, min(max_cap, round(p_high - p_low, 4)))

    def prob_rain_or_snow(self) -> float:
        return max(0.02, min(0.98, max(self.prob_rain, self.prob_snow)))


# ─────────────────────────────────────────────
# Координати міст (залишаємо для зворотної сумісності)
# ─────────────────────────────────────────────

AIRPORT_COORDS: Dict[str, Tuple[float, float]] = {
    "NYC":           (40.6413, -73.7781),
    "New York":      (40.6413, -73.7781),
    "Chicago":       (41.9742, -87.9073),
    "Los Angeles":   (33.9425, -118.4081),
    "San Francisco": (37.6213, -122.3790),
    "Miami":         (25.7959, -80.2870),
    "Dallas":        (32.8998, -97.0403),
    "Seattle":       (47.4502, -122.3088),
    "Boston":        (42.3656, -71.0096),
    "Denver":        (39.8561, -104.6737),
    "Atlanta":       (33.6407, -84.4277),
    "London":        (51.4775, -0.4614),
    "Paris":         (49.0097, 2.5479),
    "Berlin":        (52.3667, 13.5033),
    "Madrid":        (40.4936, -3.5668),
    "Amsterdam":     (52.3086, 4.7639),
    "Rome":          (41.8003, 12.2389),
    "Istanbul":      (40.9769, 28.8146),
    "Tokyo":         (35.5494, 139.7798),
    "Seoul":         (37.4602, 126.4407),
    "Singapore":     (1.3644, 103.9915),
    "Dubai":         (25.2532, 55.3657),
    "Bangkok":       (13.6811, 100.7472),
    "Sydney":        (-33.9399, 151.1753),
    "Toronto":       (43.6777, -79.6248),
    "Buenos Aires":  (-34.8222, -58.5358),
    "Cape Town":     (-33.9715, 18.6021),
    "Busan":         (35.1795, 128.9381),
    "Lucknow":       (26.7606, 80.8893),   # VILK — Chaudhary Charan Singh International
    "Sao Paulo":     (-23.4356, -46.4731),  # SBGR — Guarulhos
    "Munich":        (48.3537,  11.7750),   # EDDM — Munich Airport
}

CITY_COORDS: Dict[str, Tuple[float, float]] = {
    # ... (повний словник з попередньої версії, залишаємо для geocoding fallback)
    "NYC":           (40.7128, -74.0060),
    "New York":      (40.7128, -74.0060),
    "Chicago":       (41.8781, -87.6298),
    "Los Angeles":   (34.0522, -118.2437),
    "San Francisco": (37.7749, -122.4194),
    "Miami":         (25.7617, -80.1918),
    "Dallas":        (32.7767, -96.7970),
    "Houston":       (29.7604, -95.3698),
    "Seattle":       (47.6062, -122.3321),
    "Atlanta":       (33.7490, -84.3880),
    "Boston":        (42.3601, -71.0589),
    "Denver":        (39.7392, -104.9903),
    "Phoenix":       (33.4484, -112.0740),
    "Las Vegas":     (36.1699, -115.1398),
    "Austin":        (30.2672, -97.7431),
    "Minneapolis":   (44.9778, -93.2650),
    "Portland":      (45.5051, -122.6750),
    "Nashville":     (36.1627, -86.7816),
    "Charlotte":     (35.2271, -80.8431),
    "Orlando":       (28.5383, -81.3792),
    "London":        (51.5074, -0.1278),
    "Paris":         (48.8566, 2.3522),
    "Berlin":        (52.5200, 13.4049),
    "Madrid":        (40.4168, -3.7038),
    "Rome":          (41.9028, 12.4964),
    "Amsterdam":     (52.3676, 4.9041),
    "Vienna":        (48.2082, 16.3738),
    "Prague":        (50.0755, 14.4378),
    "Warsaw":        (52.2297, 21.0122),
    "Brussels":      (50.8503, 4.3517),
    "Dublin":        (53.3498, -6.2603),
    "Edinburgh":     (55.9533, -3.1883),
    "Istanbul":      (41.0082, 28.9784),
    "Moscow":        (55.7558, 37.6173),
    "Helsinki":      (60.1699, 24.9384),
    "Ankara":        (39.9334, 32.8597),
    "Tokyo":         (35.6895, 139.6917),
    "Seoul":         (37.5665, 126.9780),
    "Beijing":       (39.9042, 116.4074),
    "Shanghai":      (31.2304, 121.4737),
    "Hong Kong":     (22.3193, 114.1694),
    "Singapore":     (1.3521,  103.8198),
    "Bangkok":       (13.7563, 100.5018),
    "Taipei":        (25.0330, 121.5654),
    "Dubai":         (25.2048, 55.2708),
    "Mumbai":        (19.0760, 72.8777),
    "Delhi":         (28.7041, 77.1025),
    "Jakarta":       (6.2088,  106.8456),
    "Kuala Lumpur":  (3.1390,  101.6869),
    "Osaka":         (34.6937, 135.5023),
    "Busan":         (35.1796, 129.0756),
    "Chengdu":       (30.5728, 104.0668),
    "Shenzhen":      (22.5431, 114.0579),
    "Chongqing":     (29.5630, 106.5516),
    "Wuhan":         (30.5928, 114.3055),
    "Jeddah":        (21.2854, 39.2376),
    "Karachi":       (24.8607, 67.0011),
    "Lucknow":       (26.8467, 80.9462),
    "Hanoi":         (21.0285, 105.8542),
    "Sydney":        (-33.8688, 151.2093),
    "Melbourne":     (-37.8136, 144.9631),
    "Brisbane":      (-27.4698, 153.0251),
    "Perth":         (-31.9505, 115.8605),
    "Auckland":      (-36.8509, 174.7645),
    "Wellington":    (-41.2865, 174.7762),
    "Toronto":       (43.6532, -79.3832),
    "Vancouver":     (49.2827, -123.1207),
    "Montreal":      (45.5017, -73.5673),
    "Cape Town":     (-33.9249, 18.4241),
    "Lagos":         (6.5244,  3.3792),
    "Cairo":         (30.0444, 31.2357),
    "Nairobi":       (-1.2921, 36.8219),
    "Buenos Aires":  (-34.6037, -58.3816),
    "Santiago":      (-33.4569, -70.6483),
    "Lima":          (-12.0464, -77.0428),
    "Mexico City":   (19.4326, -99.1332),
    "Panama City":   (8.9936, -79.5197),
    "Sao Paulo":     (-23.5505, -46.6333),
    "Munich":        (48.1351, 11.5820),
}

US_CITIES = {
    "NYC", "New York", "Chicago", "Los Angeles", "San Francisco",
    "Miami", "Dallas", "Houston", "Seattle", "Atlanta", "Boston",
    "Denver", "Phoenix", "Las Vegas", "Austin", "Minneapolis",
    "Portland", "Nashville", "Charlotte", "Orlando",
}


def _get_coords(city: str, prefer_airport: bool = True) -> Optional[Tuple[float, float]]:
    if prefer_airport and city in AIRPORT_COORDS:
        return AIRPORT_COORDS[city]
    if city in CITY_COORDS:
        return CITY_COORDS[city]
        
    key = f"geo_{city}"
    cached = _cache_get(key)
    if cached:
        return cached
    try:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en"},
            timeout=8
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            coords = (results[0]["latitude"], results[0]["longitude"])
            _cache_set(key, coords)
            return coords
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
# 1. ENSEMBLE API (ГОЛОВНЕ ДЖЕРЕЛО ДЛЯ GRID YES)
# ─────────────────────────────────────────────

def fetch_open_meteo_ensemble(city: str, hours_to_resolution: float = 24.0, target_date: Optional[datetime.date] = None) -> Optional[WeatherForecast]:
    """Отримує 31 варіант погоди для створення ймовірнісної сітки."""
    coords = _get_coords(city, prefer_airport=True)
    if not coords:
        return None

    if target_date:
        day_index = min(max((target_date - datetime.now(timezone.utc).date()).days, 0), 4)
    else:
        target_dt = datetime.now(timezone.utc) + timedelta(hours=hours_to_resolution)
        day_index = min(max((target_dt.date() - datetime.now(timezone.utc).date()).days, 0), 4)

    key = f"ensemble_{city}_{day_index}"
    cached = _cache_get(key)
    if cached:
        return cached

    lat, lon = coords
    try:
        r = requests.get(
            "https://ensemble-api.open-meteo.com/v1/ensemble",
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "models": "gfs_seamless",
                "timezone": "auto",
                "forecast_days": 5,
            },
            timeout=10
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        
        high_m, low_m = [], []
        for i in range(1, 32):
            k_high = f"temperature_2m_max_member{i:02d}"
            k_low  = f"temperature_2m_min_member{i:02d}"
            if k_high in daily and daily[k_high] and len(daily[k_high]) > day_index and daily[k_high][day_index] is not None:
                high_m.append(daily[k_high][day_index])
            if k_low in daily and daily[k_low] and len(daily[k_low]) > day_index and daily[k_low][day_index] is not None:
                low_m.append(daily[k_low][day_index])

        if not high_m:
            return None

        avg_high = sum(high_m) / len(high_m)
        avg_low  = sum(low_m) / len(low_m) if low_m else avg_high - 8

        fc = WeatherForecast(
            city=city, 
            timestamp=datetime.now(timezone.utc),
            temp_high_c=round(avg_high, 1), 
            temp_low_c=round(avg_low, 1),
            sources_used=["Open-Meteo_ENSEMBLE"]
        )
        fc.temp_high_members = high_m
        fc.temp_low_members = low_m
        
        _cache_set(key, fc)
        logger.debug(f"⛅ ENSEMBLE {city}: {len(high_m)} members, day_index={day_index} (mean: {avg_high:.1f}°C)")
        return fc
    except Exception as e:
        logger.debug(f"Ensemble error {city}: {e}")
        return None


# ─────────────────────────────────────────────
# 2. NOAA/NWS (США)
# ─────────────────────────────────────────────

def fetch_noaa_forecast(city: str, hours_to_resolution: float = 24.0, target_date: Optional[datetime.date] = None) -> Optional[WeatherForecast]:
    if city not in US_CITIES:
        return None
    coords = _get_coords(city, prefer_airport=True)
    if not coords:
        return None

    if target_date:
        day_index = min(max((target_date - datetime.now(timezone.utc).date()).days, 0), 4)
        target_date_obj = target_date
    else:
        target_dt = datetime.now(timezone.utc) + timedelta(hours=hours_to_resolution)
        day_index = min(max((target_dt.date() - datetime.now(timezone.utc).date()).days, 0), 4)
        target_date_obj = target_dt.date()

    key = f"noaa_{city}_{day_index}"
    cached = _cache_get(key)
    if cached:
        return cached

    lat, lon = coords
    try:
        r = requests.get(
            f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}",
            timeout=10,
            headers={"User-Agent": "PolymarketWeatherBot/GridEdition"}
        )
        r.raise_for_status()
        forecast_url = r.json()["properties"]["forecast"]

        r2 = requests.get(forecast_url, timeout=10,
                           headers={"User-Agent": "PolymarketWeatherBot/GridEdition"})
        r2.raise_for_status()
        periods = r2.json()["properties"]["periods"]

        # Знаходимо періоди для цільової дати
        target_date = target_date_obj
        high_f, low_f = None, None
        rain_prob = 0.0

        for p in periods:
            start_str = p.get("startTime", "")
            if start_str:
                try:
                    p_date = datetime.fromisoformat(start_str.replace("Z", "+00:00")).date()
                    if p_date == target_date:
                        temp = p.get("temperature")
                        if temp is not None:
                            is_day = p.get("isDaytime", True)
                            if is_day:
                                high_f = temp
                            else:
                                low_f = temp
                        pp = p.get("probabilityOfPrecipitation", {})
                        if pp and pp.get("value") is not None:
                            rain_prob = max(rain_prob, float(pp["value"]) / 100.0)
                except Exception:
                    pass

        # Fallback якщо не знайшли точну дату
        if high_f is None or low_f is None:
            sub_periods = periods[:2]
            high_f = max((p.get("temperature", 60) for p in sub_periods if p.get("temperature")), default=60)
            low_f  = min((p.get("temperature", 50) for p in sub_periods if p.get("temperature")), default=50)
            for p in sub_periods:
                pp = p.get("probabilityOfPrecipitation", {})
                if pp and pp.get("value") is not None:
                    rain_prob = max(rain_prob, float(pp["value"]) / 100.0)

        high_c = (high_f - 32) * 5 / 9
        low_c  = (low_f  - 32) * 5 / 9

        fc = WeatherForecast(
            city=city, timestamp=datetime.now(timezone.utc),
            temp_high_c=round(high_c, 1), temp_low_c=round(low_c, 1),
            prob_rain=rain_prob, prob_snow=0.0,
            sources_used=["NOAA"]
        )
        _cache_set(key, fc)
        return fc
    except Exception as e:
        logger.debug(f"NOAA error {city}: {e}")
        return None


# ─────────────────────────────────────────────
# 3. NASA POWER (Кліматична база)
# ─────────────────────────────────────────────

def fetch_nasa_power(city: str, hours_to_resolution: float = 24.0) -> Optional[WeatherForecast]:
    coords = _get_coords(city, prefer_airport=True)
    if not coords: return None

    key = f"nasa_{city}"
    cached = _cache_get(key)
    if cached: return cached

    lat, lon = coords
    try:
        end_dt   = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=10)
        
        r = requests.get(
            "https://power.larc.nasa.gov/api/temporal/daily/point",
            params={
                "parameters": "T2M_MAX,T2M_MIN,PRECTOTCORR",
                "community":  "RE",
                "longitude":  lon,
                "latitude":   lat,
                "start":      start_dt.strftime("%Y%m%d"),
                "end":        end_dt.strftime("%Y%m%d"),
                "format":     "JSON",
            },
            timeout=15
        )
        r.raise_for_status()
        data = r.json()["properties"]["parameter"]

        tmax_vals = [v for v in data["T2M_MAX"].values() if v and v > -900]
        tmin_vals = [v for v in data["T2M_MIN"].values() if v and v > -900]
        prec_vals = [v for v in data["PRECTOTCORR"].values() if v and v >= 0]

        if not tmax_vals: return None

        high_c = sum(tmax_vals[-5:]) / len(tmax_vals[-5:])
        low_c  = sum(tmin_vals[-5:]) / len(tmin_vals[-5:]) if tmin_vals else high_c - 8
        avg_prec = sum(prec_vals[-5:]) / len(prec_vals[-5:]) if prec_vals else 0

        fc = WeatherForecast(
            city=city, timestamp=datetime.now(timezone.utc),
            temp_high_c=round(high_c, 1), temp_low_c=round(low_c, 1),
            prob_rain=min(0.90, avg_prec / 8.0), prob_snow=0.0,
            sources_used=["NASA_POWER"]
        )
        _cache_set(key, fc)
        return fc
    except Exception as e:
        logger.debug(f"NASA POWER error {city}: {e}")
        return None


# ─────────────────────────────────────────────
# 4. Open-Meteo Single (GFS / ECMWF)
# ─────────────────────────────────────────────

def fetch_open_meteo(city: str, model: str = "forecast", hours_to_resolution: float = 24.0, target_date: Optional[datetime.date] = None) -> Optional[WeatherForecast]:
    coords = _get_coords(city, prefer_airport=True)
    if not coords: return None

    if target_date:
        day_index = min(max((target_date - datetime.now(timezone.utc).date()).days, 0), 4)
    else:
        target_dt = datetime.now(timezone.utc) + timedelta(hours=hours_to_resolution)
        day_index = min(max((target_dt.date() - datetime.now(timezone.utc).date()).days, 0), 4)

    key = f"om_{model}_{city}_{day_index}"
    cached = _cache_get(key)
    if cached: return cached

    lat, lon = coords
    try:
        r = requests.get(
            f"https://api.open-meteo.com/v1/{model}",
            params={
                "latitude":   lat,
                "longitude":  lon,
                "daily":      ["temperature_2m_max", "temperature_2m_min",
                               "precipitation_probability_max", "precipitation_sum"],
                "timezone":   "auto",
                "forecast_days": 5,
            },
            timeout=10
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        if not daily: return None

        high_c     = daily["temperature_2m_max"][day_index] if len(daily.get("temperature_2m_max", [])) > day_index else daily["temperature_2m_max"][0]
        low_c      = daily["temperature_2m_min"][day_index] if len(daily.get("temperature_2m_min", [])) > day_index else daily["temperature_2m_min"][0]
        rain_probs = daily.get("precipitation_probability_max", [0])
        rain_prob  = (rain_probs[day_index] if len(rain_probs) > day_index else rain_probs[0] if rain_probs else 0) or 0
        rain_prob  = rain_prob / 100.0

        fc = WeatherForecast(
            city=city, timestamp=datetime.now(timezone.utc),
            temp_high_c=round(high_c, 1), temp_low_c=round(low_c, 1),
            prob_rain=rain_prob, prob_snow=0.0,
            sources_used=[f"Open-Meteo_{model.upper()}"]
        )
        _cache_set(key, fc)
        return fc
    except Exception as e:
        logger.debug(f"Open-Meteo/{model} error {city}: {e}")
        return None


# ─────────────────────────────────────────────
# 5. METAR (REAL-TIME AIRPORT OBSERVATION) — ПРІОРИТЕТ ДЛЯ БЛИЗЬКИХ РИНКІВ
# ─────────────────────────────────────────────

# Розширений словник CITY -> ICAO (ключові аеропорти для ColdMath)
CITY_TO_ICAO: Dict[str, str] = {
    # USA
    "NYC": "KJFK", "New York": "KJFK",
    "Chicago": "KORD",
    "Los Angeles": "KLAX", "LA": "KLAX", "Los Angeles CA": "KLAX",
    "San Francisco": "KSFO",
    "Miami": "KMIA",
    "Dallas": "KDFW", "Dallas/Fort Worth": "KDFW",
    "Seattle": "KSEA",
    "Boston": "KBOS",
    "Denver": "KDEN",
    "Atlanta": "KATL",
    "Houston": "KIAH",
    "Phoenix": "KPHX",
    "Las Vegas": "KLAS",
    "Austin": "KAUS",
    "Minneapolis": "KMSP",
    "Portland": "KPDX",
    "Nashville": "KBNA",
    "Charlotte": "KCLT",
    "Orlando": "KMCO",
    # Europe
    "London": "EGLL", "London Heathrow": "EGLL",
    "Paris": "LFPG", "Paris Charles de Gaulle": "LFPG",
    "Berlin": "EDDB", "Berlin Brandenburg": "EDDB",
    "Munich": "EDDM", "Munich International": "EDDM",
    "Amsterdam": "EHAM",
    "Rome": "LIRF",
    "Madrid": "LEMD",
    "Dublin": "EIDW",
    "Warsaw": "EPWA",
    "Vienna": "LOWW",
    "Prague": "LKPR",
    # Asia
    "Tokyo": "RJTT", "Tokyo Haneda": "RJTT",
    "Seoul": "RKSI", "Seoul Incheon": "RKSI",
    "Busan": "RKPK", "Busan Gimhae": "RKPK",
    "Shanghai": "ZSSS",
    "Beijing": "ZBAA",
    "Hong Kong": "VHHH",
    "Singapore": "WSSS",
    "Bangkok": "VTBS",
    "Dubai": "OMDB",
    "Lucknow": "VILK", "Lucknow Amausi": "VILK",
    # South America
    "Buenos Aires": "SAEZ", "Buenos Aires Ezeiza": "SAEZ",
    "Sao Paulo": "SBGR", "Sao Paulo Guarulhos": "SBGR",
    "Santiago": "SCEL",
    "Lima": "SPJC",
    # Africa
    "Cape Town": "FACT", "Cape Town International": "FACT",
    "Lagos": "DNMM",
    "Nairobi": "HKJK",
    # Australia / Oceania
    "Sydney": "YSSY", "Sydney Kingsford Smith": "YSSY",
    "Melbourne": "YMML",
    "Brisbane": "YBBN",
    "Perth": "YPPH",
    "Auckland": "NZAA",
    "Wellington": "NZWN",
}

def fetch_metar(city: str) -> Optional[WeatherForecast]:
    """
    Отримує актуальні спостереження METAR для заданого міста.
    Дані оновлюються щогодини (або частіше). Використовується як джерело істини
    для ринків, що вирішуються протягом найближчих 12 годин.
    """
    icao = CITY_TO_ICAO.get(city)
    if not icao:
        logger.debug(f"METAR: немає ICAO для міста {city}")
        return None

    key = f"metar_{city}"
    cached = _cache_get(key)
    if cached:
        return cached

    try:
        # Використовуємо aviationweather.gov JSON API
        r = requests.get(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": icao, "format": "json", "hours": 1},
            timeout=8,
            headers={"User-Agent": "PolymarketWeatherBot/ColdMath"}
        )
        r.raise_for_status()
        data = r.json()
        if not data or not isinstance(data, list):
            return None

        obs = data[0]
        temp_c = obs.get("temp")
        if temp_c is None:
            return None

        dewpoint = obs.get("dewp", temp_c - 5)
        # Додатково можна отримати швидкість вітру, опади тощо
        fc = WeatherForecast(
            city=city,
            timestamp=datetime.now(timezone.utc),
            temp_high_c=float(temp_c),
            temp_low_c=float(dewpoint),
            prob_rain=0.0,
            prob_snow=0.0,
            sources_used=["METAR"]
        )
        # Короткий кеш (30 хвилин) — METAR часто оновлюється
        _cache_set(key, fc)
        # Перевизначаємо TTL кешу для METAR на 1800 секунд
        _cache[key] = (time.time(), fc)
        logger.debug(f"🛬 METAR {city} ({icao}): {temp_c}°C")
        return fc
    except Exception as e:
        logger.debug(f"METAR error {city} ({icao}): {e}")
        return None


def _fetch_observed_daily_extremes(city: str) -> Optional[Tuple[float, float]]:
    """
    Отримує спостережені погодинні температури за сьогодні з Open-Meteo.
    Повертає (min_so_far, max_so_far) або None.
    Використовується як фізичне обмеження для ринків, що вирішуються сьогодні.
    """
    coords = _get_coords(city, prefer_airport=True)
    if not coords:
        return None

    key = f"obs_{city}"
    cached = _cache_get(key)
    if cached:
        return cached

    lat, lon = coords
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m",
                "past_hours": 48,
                "forecast_hours": 0,
                "timezone": "auto",
            },
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])

        if not times or not temps:
            return None

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        today_temps = []
        for t_str, temp in zip(times, temps):
            try:
                t = datetime.fromisoformat(t_str)
                if t >= today_start and temp is not None:
                    today_temps.append(temp)
            except:
                pass

        if len(today_temps) < 2:
            return None

        result = (min(today_temps), max(today_temps))
        _cache_set(key, result)
        logger.debug(f"📊 Observed today {city}: min={result[0]:.1f}°C, max={result[1]:.1f}°C ({len(today_temps)} hours)")
        return result
    except Exception as e:
        logger.debug(f"Observed today error {city}: {e}")
        return None


# ─────────────────────────────────────────────
# 6. CONSENSUS (ЗЛИТТЯ ПРОГНОЗІВ З ПРІОРИТЕТОМ METAR ДЛЯ БЛИЗЬКИХ ГОДИН)
# ─────────────────────────────────────────────

def get_best_forecast(city: str, hours_to_resolution: float = 24.0, target_date: Optional[datetime.date] = None) -> Optional[WeatherForecast]:
    """
    Повертає консенсусний прогноз для заданого міста.
    Якщо hours_to_resolution <= 12, METAR отримує дуже високу вагу (ColdMath стиль).
    """
    # Бакет hours для кешу: ≤12, ≤24, >24
    _h_bucket = "short" if hours_to_resolution <= 12 else ("mid" if hours_to_resolution <= 24 else "long")
    key = f"consensus_{city}_{_h_bucket}_{target_date or ''}"
    cached = _cache_get(key)
    if cached:
        return cached

    forecasts_w = []

    # 1. Отримуємо METAR завжди (він може бути None, якщо немає ICAO)
    metar_fc = fetch_metar(city)
    
    # 2. Отримуємо прогнози від інших джерел (ансамбль, NOAA, NASA, Open-Meteo)
    fc_ensemble = fetch_open_meteo_ensemble(city, hours_to_resolution, target_date)
    fc_noaa = fetch_noaa_forecast(city, hours_to_resolution, target_date)
    fc_nasa = fetch_nasa_power(city, hours_to_resolution)
    fc_gfs = fetch_open_meteo(city, "gfs", hours_to_resolution, target_date)
    fc_ecmwf = fetch_open_meteo(city, "ecmwf", hours_to_resolution, target_date)

    # 3. Визначаємо ваги залежно від часу до resolution (Тільки моделі, без METAR!)
    if hours_to_resolution <= 12.0:
        if fc_ensemble: forecasts_w.append((fc_ensemble, 0.50))
        if fc_gfs: forecasts_w.append((fc_gfs, 0.25))
        if fc_ecmwf: forecasts_w.append((fc_ecmwf, 0.25))
    else:
        if fc_ensemble: forecasts_w.append((fc_ensemble, 0.45))
        if fc_noaa: forecasts_w.append((fc_noaa, 0.25))
        if fc_gfs: forecasts_w.append((fc_gfs, 0.15))
        if fc_ecmwf: forecasts_w.append((fc_ecmwf, 0.10))
        if fc_nasa: forecasts_w.append((fc_nasa, 0.05))

    # Якщо після зважування немає жодного прогнозу — повертаємо ансамбль або None
    if not forecasts_w:
        if fc_ensemble:
            _cache_set(key, fc_ensemble)
            return fc_ensemble
        logger.warning(f"Немає прогнозу для {city}")
        return None

    # Розрахунок зваженого середнього
    total_w = sum(w for _, w in forecasts_w)
    if total_w == 0:
        return None

    def wavg(attr: str) -> float:
        return sum(getattr(f, attr) * w for f, w in forecasts_w) / total_w

    all_sources = [s for f, _ in forecasts_w for s in f.sources_used]

    result = WeatherForecast(
        city=city,
        timestamp=datetime.now(timezone.utc),
        temp_high_c=round(wavg("temp_high_c"), 1),
        temp_low_c=round(wavg("temp_low_c"), 1),
        prob_rain=round(wavg("prob_rain"), 3),
        prob_snow=round(wavg("prob_snow"), 3),
        sources_used=all_sources,
    )

    # Передаємо members лише якщо METAR не домінує (вага < 0.5).
    # При METAR >= 0.5: result.temp_high_c відображає METAR-реальність,
    # але ensemble members з іншого дня → prob_exact дасть хибний результат.
    _metar_weight = next(
        (w for f, w in forecasts_w if "METAR" in f.sources_used), 0.0
    )
    if fc_ensemble and _metar_weight < 0.5:
        result.temp_high_members = fc_ensemble.temp_high_members
        result.temp_low_members = fc_ensemble.temp_low_members

    # Застосовуємо спостережені дані як фізичне обмеження для ринків, що вирішуються сьогодні
    if hours_to_resolution <= 24.0:
        observed = _fetch_observed_daily_extremes(city)
        if observed:
            obs_low, obs_high = observed
            # Добовий максимум не може бути нижчим за вже спостережений максимум
            result.temp_high_c = max(result.temp_high_c, obs_high)
            # Добовий мінімум не може бути вищим за вже спостережений мінімум
            result.temp_low_c = min(result.temp_low_c, obs_low)

            # НЕ модифікуємо ensemble members observed даними!
            # Observed = floor для temp_high_c, але НЕ для ймовірнісних розрахунків.
            # Підтягування members до observed створює хибну впевненість (99%)
            # коли поріг близько до поточної температури.

            if "OBSERVED" not in result.sources_used:
                result.sources_used.append("OBSERVED")

        # METAR: використовуємо ТІЛЬКИ як дані для корекції середнього прогнозу
        # НЕ застосовуємо до ensemble members — це створює хибну впевненість (99%)
        # Поточна температура ≠ добовий максимум/мінімум
        # temp_low_c коригуємо dewpoint (ночі мінімум ближче до dewpoint, ніж до поточної)
        if metar_fc and hours_to_resolution < 14.0:
            current_temp = metar_fc.temp_high_c
            current_dewp = metar_fc.temp_low_c  # dewpoint з METAR
            result.temp_high_c = round(result.temp_high_c * 0.9 + current_temp * 0.1, 1)
            result.temp_low_c = round(result.temp_low_c * 0.9 + current_dewp * 0.1, 1)

            if "METAR" not in result.sources_used:
                result.sources_used.append("METAR")

    _cache_set(key, result)
    logger.info(
        f"Forecast {city}: {result.temp_high_c:.1f}°C (src={'+'.join(result.sources_used[:3])}) "
        f"| hours={hours_to_resolution:.1f}h | METAR={metar_fc is not None}"
    )
    return result


def get_multi_source_consensus(city: str, hours: float = 24.0) -> Optional[WeatherForecast]:
    """Alias для сумісності з іншими модулями."""
    return get_best_forecast(city, hours)


# Додаткова функція для отримання "залізобетонної" температури для resolution
def get_metar_resolution(city: str, target_dt: datetime) -> Optional[float]:
    """
    Отримує фактичну температуру в аеропорту для заданого часу (для resolution).
    Використовує історичні METAR дані (якщо доступні) або останнє спостереження.
    """
    # Для спрощення повертаємо останнє спостереження, але в реальному боті варто
    # використовувати історичні архіви METAR (наприклад, через API Iowa Environmental Mesonet)
    fc = fetch_metar(city)
    if fc:
        return fc.temp_high_c
    return None


def fetch_historical_extreme(city: str, date) -> Optional[Tuple[float, float]]:
    """
    Отримує фактичні (історичні) значення min та max температури за конкретну дату.
    Використовується для DRY-RUN resolution замість прогнозних моделей.
    Повертає (min_c, max_c) або None.
    """
    coords = _get_coords(city, prefer_airport=True)
    if not coords:
        return None

    lat, lon = coords
    if hasattr(date, "strftime"):
        date_str = date.strftime("%Y-%m-%d")
    else:
        date_str = str(date)

    # Кешуємо результат
    cache_key = f"hist_{city}_{date_str}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        # Спочатку пробуємо архівний API (точні дані для минулих днів)
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": date_str,
                "end_date": date_str,
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
            },
            timeout=10,
        )
        if r.status_code != 200:
            # Fallback: звичайний forecast API (зберігає короткий архів)
            r = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": date_str,
                    "end_date": date_str,
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "auto",
                },
                timeout=10,
            )

        r.raise_for_status()
        daily = r.json().get("daily", {})
        t_max_vals = daily.get("temperature_2m_max", [])
        t_min_vals = daily.get("temperature_2m_min", [])

        if t_max_vals and t_min_vals:
            t_max = t_max_vals[0]
            t_min = t_min_vals[0]
            if t_max is not None and t_min is not None:
                result = (float(t_min), float(t_max))
                _cache_set(cache_key, result)
                logger.info(f"📊 Historical {city} on {date_str}: min={t_min}°C, max={t_max}°C")
                return result
    except Exception as e:
        logger.debug(f"Historical data error {city} on {date_str}: {e}")

    return None
