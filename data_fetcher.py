"""
data_fetcher.py — Polymarket Weather Bot 2026
Джерела: NOAA + NASA POWER + Open-Meteo GFS/ECMWF

ВИПРАВЛЕНО:
  BUG-5: NASA POWER URL хардкодований "start=20260417" → завжди минула дата
  BUG-6: lru_cache без TTL → якщо перший запит None, залишається None назавжди
  BUG-7: fallback model="" → URL /v1/ (404), виправлено на "forecast"
  BUG-8: NOAA повертає °F, код зберігав як °C без конвертації
  BUG-9: prob_above_temp_c формула ОБЕРНЕНА → виправлено на нормальний розподіл
"""

import math
import time
import requests
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

# TTL кеш (замість lru_cache без TTL - BUG-6 fix)
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
# WeatherForecast dataclass
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

    def prob_above_temp_c(self, threshold_c: float) -> float:
        """
        BUG-9 FIX: правильний нормальний розподіл.
        Стара формула (обернена sigmoid) при temp=20,thr=18 давала 0.246!
        Нова: при temp=20,thr=18 → 0.788 (правильно)
        """
        if self.temp_high_c == 0.0:
            return 0.50
        diff = self.temp_high_c - threshold_c
        sigma = 2.5
        prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))
        return max(0.02, min(0.98, round(prob, 4)))

    def prob_below_temp_c(self, threshold_c: float) -> float:
        return 1.0 - self.prob_above_temp_c(threshold_c)

    def prob_rain_or_snow(self) -> float:
        return max(0.02, min(0.98, max(self.prob_rain, self.prob_snow)))


# ─────────────────────────────────────────────
# Координати міст (великий список)
# ─────────────────────────────────────────────

CITY_COORDS: Dict[str, Tuple[float, float]] = {
    # США (NOAA покриває)
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
    # Європа
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
    # Азія
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
    # Інші
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
}

US_CITIES = {
    "NYC", "New York", "Chicago", "Los Angeles", "San Francisco",
    "Miami", "Dallas", "Houston", "Seattle", "Atlanta", "Boston",
    "Denver", "Phoenix", "Las Vegas", "Austin", "Minneapolis",
    "Portland", "Nashville", "Charlotte", "Orlando",
}


def _get_coords(city: str) -> Optional[Tuple[float, float]]:
    if city in CITY_COORDS:
        return CITY_COORDS[city]
    # Geocoding через Open-Meteo
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
# 1. NOAA/NWS (США — найточніше)
# ─────────────────────────────────────────────

def fetch_noaa_forecast(city: str) -> Optional[WeatherForecast]:
    if city not in US_CITIES:
        return None
    coords = _get_coords(city)
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
            headers={"User-Agent": "PolymarketWeatherBot/2026"}
        )
        r.raise_for_status()
        forecast_url = r.json()["properties"]["forecast"]

        r2 = requests.get(forecast_url, timeout=10,
                           headers={"User-Agent": "PolymarketWeatherBot/2026"})
        r2.raise_for_status()
        periods = r2.json()["properties"]["periods"][:2]

        # BUG-8 FIX: NOAA повертає °F — конвертуємо в °C!
        high_f = max((p.get("temperature", 60) for p in periods
                      if p.get("temperature")), default=60)
        low_f  = min((p.get("temperature", 50) for p in periods
                      if p.get("temperature")), default=50)
        high_c = (high_f - 32) * 5 / 9
        low_c  = (low_f  - 32) * 5 / 9

        rain_prob = 0.0
        for p in periods:
            pp = p.get("probabilityOfPrecipitation", {})
            if pp and pp.get("value") is not None:
                rain_prob = max(rain_prob, float(pp["value"]) / 100.0)

        fc = WeatherForecast(
            city=city, timestamp=datetime.utcnow(),
            temp_high_c=round(high_c, 1), temp_low_c=round(low_c, 1),
            prob_rain=rain_prob, prob_snow=0.0,
            sources_used=["NOAA"]
        )
        _cache_set(key, fc)
        logger.debug(f"NOAA {city}: {high_c:.1f}°C ({high_f:.0f}°F)")
        return fc
    except Exception as e:
        logger.debug(f"NOAA error {city}: {e}")
        return None


# ─────────────────────────────────────────────
# 2. NASA POWER (глобально, безкоштовно)
# ─────────────────────────────────────────────

def fetch_nasa_power(city: str) -> Optional[WeatherForecast]:
    coords = _get_coords(city)
    if not coords:
        return None

    key = f"nasa_{city}"
    cached = _cache_get(key)
    if cached:
        return cached

    lat, lon = coords
    try:
        # BUG-5 FIX: динамічні дати (не хардкод "20260417")
        end_dt   = datetime.utcnow()
        start_dt = end_dt - timedelta(days=10)
        end_str   = end_dt.strftime("%Y%m%d")
        start_str = start_dt.strftime("%Y%m%d")

        r = requests.get(
            "https://power.larc.nasa.gov/api/temporal/daily/point",
            params={
                "parameters": "T2M_MAX,T2M_MIN,PRECTOTCORR",
                "community":  "RE",
                "longitude":  lon,
                "latitude":   lat,
                "start":      start_str,
                "end":        end_str,
                "format":     "JSON",
            },
            timeout=15
        )
        r.raise_for_status()
        data = r.json()["properties"]["parameter"]

        # Фільтруємо невалідні значення (-999)
        tmax_vals = [v for v in data["T2M_MAX"].values() if v and v > -900]
        tmin_vals = [v for v in data["T2M_MIN"].values() if v and v > -900]
        prec_vals = [v for v in data["PRECTOTCORR"].values() if v and v >= 0]

        if not tmax_vals:
            return None

        high_c = sum(tmax_vals[-5:]) / len(tmax_vals[-5:])  # останні 5 днів
        low_c  = sum(tmin_vals[-5:]) / len(tmin_vals[-5:]) if tmin_vals else high_c - 8
        avg_prec = sum(prec_vals[-5:]) / len(prec_vals[-5:]) if prec_vals else 0

        fc = WeatherForecast(
            city=city, timestamp=datetime.utcnow(),
            temp_high_c=round(high_c, 1), temp_low_c=round(low_c, 1),
            prob_rain=min(0.90, avg_prec / 8.0),
            prob_snow=0.0,
            sources_used=["NASA_POWER"]
        )
        _cache_set(key, fc)
        logger.debug(f"NASA POWER {city}: {high_c:.1f}°C")
        return fc
    except Exception as e:
        logger.debug(f"NASA POWER error {city}: {e}")
        return None


# ─────────────────────────────────────────────
# 3. Open-Meteo (GFS / ECMWF / forecast)
# ─────────────────────────────────────────────

def fetch_open_meteo(city: str, model: str = "forecast") -> Optional[WeatherForecast]:
    """
    BUG-7 FIX: fallback model="" → 404. Тепер default = "forecast".
    Підтримувані моделі: "forecast", "gfs", "ecmwf"
    """
    coords = _get_coords(city)
    if not coords:
        return None

    key = f"om_{model}_{city}"
    cached = _cache_get(key)
    if cached:
        return cached

    lat, lon = coords
    try:
        r = requests.get(
            f"https://api.open-meteo.com/v1/{model}",
            params={
                "latitude":   lat,
                "longitude":  lon,
                "daily":      ["temperature_2m_max", "temperature_2m_min",
                               "precipitation_probability_max",
                               "precipitation_sum"],
                "timezone":   "auto",
                "forecast_days": 3,
            },
            timeout=10
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        if not daily:
            return None

        high_c     = daily["temperature_2m_max"][0]
        low_c      = daily["temperature_2m_min"][0]
        rain_prob  = (daily.get("precipitation_probability_max", [0])[0] or 0) / 100.0
        precip_mm  = daily.get("precipitation_sum", [0])[0] or 0

        source_name = f"Open-Meteo_{model.upper()}"
        fc = WeatherForecast(
            city=city, timestamp=datetime.utcnow(),
            temp_high_c=round(high_c, 1), temp_low_c=round(low_c, 1),
            prob_rain=rain_prob, prob_snow=0.0,
            sources_used=[source_name]
        )
        _cache_set(key, fc)
        logger.debug(f"Open-Meteo/{model} {city}: {high_c:.1f}°C, rain={rain_prob:.0%}")
        return fc
    except Exception as e:
        logger.debug(f"Open-Meteo/{model} error {city}: {e}")
        return None


# ─────────────────────────────────────────────
# CONSENSUS (всі джерела → зважене середнє)
# ─────────────────────────────────────────────

def get_best_forecast(city: str) -> Optional[WeatherForecast]:
    """
    BUG-6 FIX: власний TTL кеш замість lru_cache без TTL.
    Якщо перший запит повернув None — наступний через 15 хвилин спробує знову.
    """
    key = f"consensus_{city}"
    cached = _cache_get(key)
    if cached:
        return cached

    forecasts_w = []  # (forecast, weight)

    # 1. NOAA для США (найвища вага)
    fc = fetch_noaa_forecast(city)
    if fc:
        forecasts_w.append((fc, 0.45))

    # 2. NASA POWER (знижено: кліматичні avg, не оперативний прогноз)
    fc = fetch_nasa_power(city)
    if fc:
        forecasts_w.append((fc, 0.10))

    # 3. GFS (підвищено: реальний NWP прогноз оновлюється 4 рази/добу)
    fc = fetch_open_meteo(city, "gfs")
    if fc:
        forecasts_w.append((fc, 0.30))

    # 4. ECMWF (підвищено: найточніший NWP для Європи)
    fc = fetch_open_meteo(city, "ecmwf")
    if fc:
        forecasts_w.append((fc, 0.25))

    # 5. Стандартний forecast як останній fallback
    if not forecasts_w:
        fc = fetch_open_meteo(city, "forecast")
        if fc:
            forecasts_w.append((fc, 1.0))

    if not forecasts_w:
        logger.warning(f"Немає прогнозу для {city}")
        return None

    total_w = sum(w for _, w in forecasts_w)

    def wavg(attr: str) -> float:
        return sum(getattr(f, attr) * w for f, w in forecasts_w) / total_w

    all_sources = [s for f, _ in forecasts_w for s in f.sources_used]

    result = WeatherForecast(
        city=city,
        timestamp=datetime.utcnow(),
        temp_high_c=round(wavg("temp_high_c"), 1),
        temp_low_c=round(wavg("temp_low_c"), 1),
        prob_rain=round(wavg("prob_rain"), 3),
        prob_snow=round(wavg("prob_snow"), 3),
        sources_used=all_sources,
    )

    _cache_set(key, result)
    logger.info(
        f"Forecast {city}: {result.temp_high_c:.1f}°C "
        f"(rain={result.prob_rain:.0%}) "
        f"| src={'+'.join(all_sources[:3])}"
    )
    return result


def get_multi_source_consensus(city: str) -> Optional[WeatherForecast]:
    """Alias для сумісності."""
    return get_best_forecast(city)
