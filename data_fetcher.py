"""
data_fetcher.py — Polymarket Weather Bot (GRID / YES LADDERING EDITION)
Джерела: NOAA + NASA POWER + Open-Meteo GFS/ECMWF + Open-Meteo ENSEMBLE (31 members)

РЕАЛІЗАЦІЯ СТРАТЕГІЇ:
  Використовує Ансамблеві прогнози для розрахунку ймовірності кожної температури
  в "сітці". Завдяки Гауссовому згладжуванню (sigma=1.3) бот бачить шанси навіть 
  для тих температур, які відхиляються на 1-2 градуси від середнього прогнозу.
"""

import math
import time
import requests
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

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

    def prob_above_temp_c(self, threshold_c: float, is_low: bool = False) -> float:
        members = self.temp_low_members if is_low else self.temp_high_members
        sigma = 1.3  # Метеорологічна похибка (розкид)
        
        # Якщо є дані ансамблю (31 модель)
        if members:
            prob = sum(0.5 * (1 + math.erf((m - threshold_c) / (sigma * math.sqrt(2)))) for m in members) / len(members)
            return max(0.01, min(0.99, round(prob, 4)))
        
        # Fallback до одного значення, якщо ансамбль недоступний
        tc = self.temp_low_c if is_low else self.temp_high_c
        if tc == 0.0:
            return 0.50
        diff = tc - threshold_c
        prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))
        return max(0.01, min(0.99, round(prob, 4)))

    def prob_below_temp_c(self, threshold_c: float, is_low: bool = False) -> float:
        return 1.0 - self.prob_above_temp_c(threshold_c, is_low)

    def prob_exact_temp_c(self, threshold_c: float, is_low: bool = False) -> float:
        """
        Рахує ймовірність того, що температура потрапить у Polymarket бакет [threshold-0.5, threshold+0.5).

        БАГ ВИПРАВЛЕНО: попередня версія застосовувала додатковий σ=1.3 до кожного члена ансамблю.
        Ансамблеві члени вже є реалізаціями розподілу — подвійний σ завищував ймовірності у 3-10x.
        Наприклад: Seoul 17°C при mean=18.1°C → хибно 21%, реально 0-3%. Ринок = 2.6¢ (3%).

        Правильний підхід: пряма частота (скільки членів потрапляє в бакет).
        Fallback (без ансамблю): Gaussian із σ=2.0 — реалістична добова σ.
        """
        members = self.temp_low_members if is_low else self.temp_high_members

        if members:
            # Пряма частота: члени ансамблю вже є ймовірнісним розподілом.
            # Рахуємо скільки з них потрапляє в бакет Polymarket.
            count = sum(1 for m in members if threshold_c - 0.5 <= m < threshold_c + 0.5)
            raw = count / len(members)
            if raw == 0.0:
                # Жоден член не потрапив. Якщо threshold близький до mean — мала ймовірність,
                # але не нульова (ансамбль може недооцінювати хвости при 31 members).
                mean = sum(members) / len(members)
                return 0.03 if abs(mean - threshold_c) <= 1.5 else 0.01
            return max(0.01, min(0.99, round(raw, 4)))

        # Fallback: один детермінований прогноз без ансамблю.
        # σ=2.0 — реалістична добова невизначеність прогнозу температури.
        sigma = 2.0
        tc = self.temp_low_c if is_low else self.temp_high_c
        if tc == 0.0:
            return 0.01
        p_high = 0.5 * (1 + math.erf((threshold_c + 0.5 - tc) / (sigma * math.sqrt(2))))
        p_low  = 0.5 * (1 + math.erf((threshold_c - 0.5 - tc) / (sigma * math.sqrt(2))))
        return max(0.01, min(0.99, round(p_high - p_low, 4)))

    def prob_rain_or_snow(self) -> float:
        return max(0.02, min(0.98, max(self.prob_rain, self.prob_snow)))


# ─────────────────────────────────────────────
# Координати міст
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
}

CITY_COORDS: Dict[str, Tuple[float, float]] = {
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

def fetch_open_meteo_ensemble(city: str, hours_to_resolution: float = 24.0) -> Optional[WeatherForecast]:
    """Отримує 31 варіант погоди для створення ймовірнісної сітки."""
    coords = _get_coords(city, prefer_airport=True)
    if not coords:
        return None

    key = f"ensemble_{city}"
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
                "models": "gfs_seamless", # Отримуємо 31 members
                "timezone": "auto",
                "forecast_days": 5,
            },
            timeout=10
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        
        # Вибираємо правильний день за hours_to_resolution
        day_index = min(max(int(hours_to_resolution / 24), 0), 4)
        
        high_m, low_m = [],[]
        for i in range(1, 32):
            k_high = f"temperature_2m_max_member{i:02d}"
            k_low  = f"temperature_2m_min_member{i:02d}"
            if k_high in daily and daily[k_high] and len(daily[k_high]) > day_index and daily[k_high][day_index] is not None:
                high_m.append(daily[k_high][day_index])
            if k_low in daily and daily[k_low] and len(daily[k_low]) > day_index and daily[k_low][day_index] is not None:
                low_m.append(daily[k_low][day_index])

        if not high_m:
            return None

        # Розрахунок середнього прогнозу Ансамблю
        avg_high = sum(high_m) / len(high_m)
        avg_low  = sum(low_m) / len(low_m) if low_m else avg_high - 8

        fc = WeatherForecast(
            city=city, 
            timestamp=datetime.now(timezone.utc),
            temp_high_c=round(avg_high, 1), 
            temp_low_c=round(avg_low, 1),
            sources_used=["Open-Meteo_ENSEMBLE"]
        )
        # Зберігаємо "мемберів" для розрахунку сітки ймовірностей
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

def fetch_noaa_forecast(city: str, hours_to_resolution: float = 24.0) -> Optional[WeatherForecast]:
    if city not in US_CITIES:
        return None
    coords = _get_coords(city, prefer_airport=True)
    if not coords:
        return None

    key = f"noaa_{city}"
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
        periods = r2.json()["properties"]["periods"][:2]

        high_f = max((p.get("temperature", 60) for p in periods if p.get("temperature")), default=60)
        low_f  = min((p.get("temperature", 50) for p in periods if p.get("temperature")), default=50)
        high_c = (high_f - 32) * 5 / 9
        low_c  = (low_f  - 32) * 5 / 9

        rain_prob = 0.0
        for p in periods:
            pp = p.get("probabilityOfPrecipitation", {})
            if pp and pp.get("value") is not None:
                rain_prob = max(rain_prob, float(pp["value"]) / 100.0)

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

def fetch_open_meteo(city: str, model: str = "forecast", hours_to_resolution: float = 24.0) -> Optional[WeatherForecast]:
    coords = _get_coords(city, prefer_airport=True)
    if not coords: return None

    key = f"om_{model}_{city}"
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

        day_index = min(max(int(hours_to_resolution / 24), 0), 4)
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
# 5. METAR (Real-time airport observation)
# ─────────────────────────────────────────────

CITY_TO_ICAO: Dict[str, str] = {
    "NYC": "KJFK", "New York": "KJFK", "Chicago": "KORD", "Los Angeles": "KLAX",
    "San Francisco": "KSFO", "Miami": "KMIA", "Dallas": "KDFW", "Seattle": "KSEA",
    "Boston": "KBOS", "Denver": "KDEN", "Atlanta": "KATL", "London": "EGLL",
    "Paris": "LFPG", "Berlin": "EDDB", "Amsterdam": "EHAM", "Istanbul": "LTFM",
    "Tokyo": "RJTT", "Seoul": "RKSI", "Singapore": "WSSS", "Dubai": "OMDB",
    "Sydney": "YSSY", "Toronto": "CYYZ", "Buenos Aires": "SAEZ", "Busan": "RKPK",
}

def fetch_metar(city: str) -> Optional[WeatherForecast]:
    icao = CITY_TO_ICAO.get(city)
    if not icao: return None

    key = f"metar_{city}"
    cached = _cache_get(key)
    if cached: return cached

    try:
        r = requests.get(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": icao, "format": "json", "hours": 1},
            timeout=8,
            headers={"User-Agent": "PolymarketWeatherBot"}
        )
        r.raise_for_status()
        data = r.json()
        if not data or not isinstance(data, list): return None

        obs = data[0]
        temp_c = obs.get("temp")
        if temp_c is None: return None

        fc = WeatherForecast(
            city=city, timestamp=datetime.now(timezone.utc),
            temp_high_c=float(temp_c), temp_low_c=float(obs.get("dewp", temp_c - 5)),
            prob_rain=0.0, prob_snow=0.0,
            sources_used=["METAR"]
        )
        _cache[key] = (time.time() - CACHE_TTL + 1800, fc) # Короткий кеш (30 хв)
        return fc
    except Exception as e:
        logger.debug(f"METAR error {city} ({icao}): {e}")
        return None


# ─────────────────────────────────────────────
# CONSENSUS (Злиття прогнозів + ENSEMBLE)
# ─────────────────────────────────────────────

def get_best_forecast(city: str, hours_to_resolution: float = 24.0) -> Optional[WeatherForecast]:
    key = f"consensus_{city}"
    cached = _cache_get(key)
    if cached: return cached

    forecasts_w =[]

    fc = fetch_noaa_forecast(city, hours_to_resolution)
    if fc: forecasts_w.append((fc, 0.45))

    fc = fetch_nasa_power(city, hours_to_resolution)
    if fc: forecasts_w.append((fc, 0.10))

    fc = fetch_open_meteo(city, "gfs", hours_to_resolution)
    if fc: forecasts_w.append((fc, 0.30))

    fc = fetch_open_meteo(city, "ecmwf", hours_to_resolution)
    if fc: forecasts_w.append((fc, 0.25))

    metar = fetch_metar(city)
    if metar and hours_to_resolution <= 6.0:
        gfs_fc = next((f for f, _ in forecasts_w if any("GFS" in s or "NOAA" in s for s in f.sources_used)), None)
        if not gfs_fc or abs(metar.temp_high_c - gfs_fc.temp_high_c) <= 5.0:
            metar_w = 0.50 if hours_to_resolution <= 2.0 else 0.35 if hours_to_resolution <= 4.0 else 0.20
            forecasts_w.append((metar, metar_w))

    # ОТРИМУЄМО АНСАМБЛЕВИЙ ПРОГНОЗ ДЛЯ СІТКИ
    ens_fc = fetch_open_meteo_ensemble(city, hours_to_resolution)

    if not forecasts_w and not ens_fc:
        logger.warning(f"Немає прогнозу для {city}")
        return None

    def wavg(attr: str) -> float:
        if not forecasts_w:
            return getattr(ens_fc, attr)
        return sum(getattr(f, attr) * w for f, w in forecasts_w) / total_w

    total_w = sum(w for _, w in forecasts_w) if forecasts_w else 0.0
    all_sources = [s for f, _ in forecasts_w for s in f.sources_used]

    result = WeatherForecast(
        city=city,
        timestamp=datetime.now(timezone.utc),
        temp_high_c=round(wavg("temp_high_c"), 1),
        temp_low_c=round(wavg("temp_low_c"), 1),
        prob_rain=round(wavg("prob_rain"), 3) if forecasts_w else 0.0,
        prob_snow=round(wavg("prob_snow"), 3) if forecasts_w else 0.0,
        sources_used=all_sources + (["ENSEMBLE"] if ens_fc else[]),
    )

    # ПЕРЕДАЄМО "МЕМБЕРІВ" АНСАМБЛЮ В РЕЗУЛЬТАТ (Саме вони формують Сітку)
    if ens_fc:
        result.temp_high_members = ens_fc.temp_high_members
        result.temp_low_members = ens_fc.temp_low_members

    _cache_set(key, result)
    logger.info(
        f"Forecast {city}: {result.temp_high_c:.1f}°C "
        f"| src={'+'.join(result.sources_used[:3])} "
        f"| Members: {len(result.temp_high_members) if result.temp_high_members else 0}"
    )
    return result

def get_multi_source_consensus(city: str, hours: float = 24.0) -> Optional[WeatherForecast]:
    """Alias для сумісності з іншими модулями."""
    return get_best_forecast(city, hours)
