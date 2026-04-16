"""
data_fetcher.py — Polymarket Weather Bot 2026
Джерела прогнозів (всі безкоштовні, без ключів):
  1. NOAA/NWS  — api.weather.gov (США, найточніше)
  2. NASA POWER — power.larc.nasa.gov (глобально, NASA дані)
  3. Open-Meteo GFS    — api.open-meteo.com/v1/gfs
  4. Open-Meteo ECMWF  — api.open-meteo.com/v1/ecmwf
  5. Open-Meteo стандарт — fallback

ВИПРАВЛЕНО: 
  - синтаксична помилка відступу в prob_above_temp_f
  - імпорт math
  - prob ніколи не повертає 0.00 чи 1.00
  - NASA POWER додано
  - правильна конвертація C→F при порівнянні
"""

import math
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)

_weather_cache: Dict[str, Tuple[float, Any]] = {}


@dataclass
class WeatherForecast:
    """Структурований прогноз погоди."""
    city: str
    source: str
    forecast_time: datetime
    temp_high_f: Optional[float] = None
    temp_low_f: Optional[float] = None
    temp_high_c: Optional[float] = None
    temp_low_c: Optional[float] = None
    precip_mm: Optional[float] = None
    precip_prob: Optional[float] = None
    snow_mm: Optional[float] = None
    wind_kmh: Optional[float] = None
    raw_data: Any = None

    def prob_above_temp_c(self, threshold_c: float) -> float:
        """
        Ймовірність що max температура БУДЕ ВИЩЕ порогу (°C).
        Використовує нормальний розподіл з σ = 2.5°C.
        Ніколи не повертає 0.00 або 1.00 (межі 0.01-0.99).
        """
        if self.temp_high_c is None:
            return 0.50
        diff = self.temp_high_c - threshold_c
        sigma = 2.5  # стандартна похибка прогнозу
        prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))
        return max(0.01, min(0.99, round(prob, 4)))

    def prob_above_temp_f(self, threshold_f: float) -> float:
        """
        Ймовірність що max температура БУДЕ ВИЩЕ порогу (°F).
        Конвертуємо в °C для єдиної логіки.
        """
        threshold_c = (threshold_f - 32) * 5 / 9
        return self.prob_above_temp_c(threshold_c)

    def prob_exact_temp_c(self, target_c: float, sigma: float = 1.5) -> float:
        """
        Ймовірність що температура БУДЕ РІВНО target_c (±0.5°C).
        Для categorical ринків типу 'Will it be exactly 32°C?'
        """
        if self.temp_high_c is None:
            return 0.10
        from scipy.stats import norm
        try:
            p = norm.cdf(target_c + 0.5, self.temp_high_c, sigma) - \
                norm.cdf(target_c - 0.5, self.temp_high_c, sigma)
        except Exception:
            diff = abs(self.temp_high_c - target_c)
            p = max(0.01, 0.40 * math.exp(-0.5 * (diff / sigma) ** 2))
        return max(0.01, min(0.99, round(p, 4)))

    def prob_rain(self) -> Optional[float]:
        if self.precip_prob is not None:
            return max(0.01, min(0.99, self.precip_prob))
        return None

    def prob_snow(self) -> Optional[float]:
        if self.snow_mm is not None and self.snow_mm > 1.0:
            return min(0.95, (self.precip_prob or 0.5))
        if self.precip_prob and self.temp_high_c is not None and self.temp_high_c < 2:
            return max(0.01, min(0.99, self.precip_prob * 0.85))
        return None


# ════════════════════════════════════════════════════════════
# КООРДИНАТИ МІСТ
# ════════════════════════════════════════════════════════════

CITY_COORDS_EXTENDED: Dict[str, Tuple[float, float]] = {
    # США (NOAA покриває)
    "NYC": (40.7128, -74.0060), "New York": (40.7128, -74.0060),
    "Chicago": (41.8781, -87.6298), "Seattle": (47.6062, -122.3321),
    "Atlanta": (33.7490, -84.3880), "Dallas": (32.7767, -96.7970),
    "Miami": (25.7617, -80.1918), "Los Angeles": (34.0522, -118.2437),
    "San Francisco": (37.7749, -122.4194), "Boston": (42.3601, -71.0589),
    "Houston": (29.7604, -95.3698), "Denver": (39.7392, -104.9903),
    "Phoenix": (33.4484, -112.0740), "Las Vegas": (36.1699, -115.1398),
    "Minneapolis": (44.9778, -93.2650), "Portland": (45.5051, -122.6750),
    "Nashville": (36.1627, -86.7816), "Charlotte": (35.2271, -80.8431),
    "Orlando": (28.5383, -81.3792), "Austin": (30.2672, -97.7431),
    # Європа
    "London": (51.5074, -0.1278), "Paris": (48.8566, 2.3522),
    "Berlin": (52.5200, 13.4050), "Madrid": (40.4168, -3.7038),
    "Rome": (41.9028, 12.4964), "Amsterdam": (52.3676, 4.9041),
    "Vienna": (48.2082, 16.3738), "Prague": (50.0755, 14.4378),
    "Warsaw": (52.2297, 21.0122), "Brussels": (50.8503, 4.3517),
    "Dublin": (53.3498, -6.2603), "Edinburgh": (55.9533, -3.1883),
    "Istanbul": (41.0082, 28.9784), "Moscow": (55.7558, 37.6173),
    "Helsinki": (60.1699, 24.9384),
    # Азія
    "Tokyo": (35.6762, 139.6503), "Seoul": (37.5665, 126.9780),
    "Beijing": (39.9042, 116.4074), "Shanghai": (31.2304, 121.4737),
    "Hong Kong": (22.3193, 114.1694), "Singapore": (1.3521, 103.8198),
    "Bangkok": (13.7563, 100.5018), "Taipei": (25.0330, 121.5654),
    "Dubai": (25.2048, 55.2708), "Mumbai": (19.0760, 72.8777),
    "Delhi": (28.7041, 77.1025), "Jakarta": (6.2088, 106.8456),
    "Kuala Lumpur": (-3.1390, 101.6869),
    "Busan": (35.1796, 129.0756), "Osaka": (34.6937, 135.5023),
    "Wuhan": (30.5928, 114.3055), "Chengdu": (30.5728, 104.0668),
    "Shenzhen": (22.5431, 114.0579), "Chongqing": (29.5630, 106.5516),
    "Taipei": (25.0330, 121.5654),
    # Інші
    "Sydney": (-33.8688, 151.2093), "Melbourne": (-37.8136, 144.9631),
    "Toronto": (43.6532, -79.3832), "Vancouver": (49.2827, -123.1207),
    "Montreal": (45.5017, -73.5673), "Ankara": (39.9334, 32.8597),
    "Cape Town": (-33.9249, 18.4241), "Lagos": (6.5244, 3.3792),
    "Nairobi": (-1.2921, 36.8219), "Cairo": (30.0444, 31.2357),
    "Wellington": (-41.2865, 174.7762), "Auckland": (-36.8509, 174.7645),
    "Buenos Aires": (-34.6037, -58.3816), "Jeddah": (21.2854, 39.2376),
    "Lucknow": (26.8467, 80.9462), "Karachi": (24.8607, 67.0011),
    "Hanoi": (21.0285, 105.8542),
}


def _get_coords(city: str) -> Optional[Tuple[float, float]]:
    """Отримати координати міста."""
    # Спочатку з розширеної таблиці
    coords = CITY_COORDS_EXTENDED.get(city)
    if coords:
        return coords
    # Потім з config
    coords = getattr(config, 'CITY_COORDS', {}).get(city)
    if coords:
        return coords
    # Geocoding через Open-Meteo
    return _geocode_city(city)


def _geocode_city(city: str) -> Optional[Tuple[float, float]]:
    cache_key = f"geo_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < 86400 * 7:
            return data
    try:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en"},
            timeout=10
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        coords = (results[0]["latitude"], results[0]["longitude"])
        _weather_cache[cache_key] = (time.time(), coords)
        return coords
    except Exception as e:
        logger.debug(f"Geocoding error {city}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# 1. NOAA/NWS (США — найточніше для US ринків)
# ════════════════════════════════════════════════════════════

NOAA_CITIES = {
    "NYC", "New York", "Chicago", "Seattle", "Atlanta", "Dallas",
    "Miami", "Los Angeles", "San Francisco", "Boston", "Houston",
    "Denver", "Phoenix", "Las Vegas", "Minneapolis", "Portland",
    "Nashville", "Charlotte", "Orlando", "Austin",
}


def fetch_noaa_forecast(city: str) -> Optional[WeatherForecast]:
    """NOAA/NWS для США — офіційний, безкоштовний."""
    if city not in NOAA_CITIES:
        return None

    cache_key = f"noaa_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < 900:
            return data

    coords = _get_coords(city)
    if not coords:
        return None
    lat, lon = coords

    try:
        r = requests.get(
            f"https://api.weather.gov/points/{lat},{lon}",
            timeout=10,
            headers={"User-Agent": "PolymarketWeatherBot/2.0 (contact@example.com)"}
        )
        r.raise_for_status()
        forecast_url = r.json()["properties"]["forecast"]

        r2 = requests.get(forecast_url, timeout=10,
                          headers={"User-Agent": "PolymarketWeatherBot/2.0"})
        r2.raise_for_status()
        periods = r2.json()["properties"]["periods"]

        day = next((p for p in periods if p["isDaytime"]), periods[0])
        night = next((p for p in periods if not p["isDaytime"]), None)

        temp_high_f = float(day.get("temperature", 70))
        temp_low_f = float(night["temperature"]) if night else temp_high_f - 15
        temp_high_c = (temp_high_f - 32) * 5 / 9
        temp_low_c = (temp_low_f - 32) * 5 / 9

        precip_prob = None
        pp = day.get("probabilityOfPrecipitation", {})
        if pp and pp.get("value") is not None:
            precip_prob = float(pp["value"]) / 100.0

        fc = WeatherForecast(
            city=city, source="noaa",
            forecast_time=datetime.now(),
            temp_high_f=temp_high_f, temp_low_f=temp_low_f,
            temp_high_c=temp_high_c, temp_low_c=temp_low_c,
            precip_prob=precip_prob, raw_data=day
        )
        _weather_cache[cache_key] = (time.time(), fc)
        logger.debug(f"NOAA {city}: {temp_high_c:.1f}°C ({temp_high_f:.1f}°F)")
        return fc
    except Exception as e:
        logger.debug(f"NOAA error {city}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# 2. NASA POWER API (глобально — безкоштовно!)
# ════════════════════════════════════════════════════════════

def fetch_nasa_power_forecast(city: str) -> Optional[WeatherForecast]:
    """
    NASA POWER (Prediction Of Worldwide Energy Resources).
    Безкоштовний глобальний API NASA для температури та опадів.
    https://power.larc.nasa.gov/api/
    """
    cache_key = f"nasa_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < 3600:  # Кеш 1 година
            return data

    coords = _get_coords(city)
    if not coords:
        return None
    lat, lon = coords

    try:
        today = datetime.now()
        # NASA POWER дає дані з затримкою ~2 дні, тому беремо недавні
        end_date = (today - timedelta(days=2)).strftime("%Y%m%d")
        start_date = (today - timedelta(days=9)).strftime("%Y%m%d")

        r = requests.get(
            "https://power.larc.nasa.gov/api/temporal/daily/point",
            params={
                "parameters": "T2M_MAX,T2M_MIN,PRECTOTCORR",
                "community": "RE",
                "longitude": lon,
                "latitude": lat,
                "start": start_date,
                "end": end_date,
                "format": "JSON",
            },
            timeout=15
        )
        r.raise_for_status()
        data = r.json()

        props = data.get("properties", {}).get("parameter", {})
        t_max = props.get("T2M_MAX", {})
        t_min = props.get("T2M_MIN", {})
        precip = props.get("PRECTOTCORR", {})

        if not t_max:
            return None

        # Беремо середнє за останні 7 днів як кліматичний прогноз
        valid_temps_max = [v for v in t_max.values() if v and v > -900]
        valid_temps_min = [v for v in t_min.values() if v and v > -900]
        valid_precip = [v for v in precip.values() if v and v >= 0]

        if not valid_temps_max:
            return None

        avg_high_c = sum(valid_temps_max) / len(valid_temps_max)
        avg_low_c = sum(valid_temps_min) / len(valid_temps_min) if valid_temps_min else avg_high_c - 8
        avg_precip = sum(valid_precip) / len(valid_precip) if valid_precip else 0

        fc = WeatherForecast(
            city=city, source="nasa_power",
            forecast_time=datetime.now(),
            temp_high_c=round(avg_high_c, 1),
            temp_low_c=round(avg_low_c, 1),
            temp_high_f=round(avg_high_c * 9 / 5 + 32, 1),
            temp_low_f=round(avg_low_c * 9 / 5 + 32, 1),
            precip_mm=round(avg_precip, 2),
            precip_prob=min(0.85, avg_precip / 10.0) if avg_precip > 0 else 0.1,
            raw_data={"t_max": avg_high_c, "t_min": avg_low_c, "precip": avg_precip}
        )
        _weather_cache[cache_key] = (time.time(), fc)
        logger.debug(f"NASA POWER {city}: {avg_high_c:.1f}°C avg high (7-day)")
        return fc
    except Exception as e:
        logger.debug(f"NASA POWER error {city}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# 3. Open-Meteo GFS (NOAA Global Forecast System)
# ════════════════════════════════════════════════════════════

def fetch_gfs_forecast(city: str) -> Optional[WeatherForecast]:
    """Open-Meteo GFS — NOAA Global Forecast System."""
    cache_key = f"gfs_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < 900:
            return data

    coords = _get_coords(city)
    if not coords:
        return None
    lat, lon = coords

    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/gfs",
            params={
                "latitude": lat, "longitude": lon,
                "daily": ["temperature_2m_max", "temperature_2m_min",
                          "precipitation_sum", "precipitation_probability_max",
                          "snowfall_sum"],
                "temperature_unit": "celsius",
                "precipitation_unit": "mm",
                "timezone": "auto", "forecast_days": 3
            },
            timeout=10
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        if not daily:
            return None

        thc = daily["temperature_2m_max"][0]
        tlc = daily["temperature_2m_min"][0]
        pp = (daily.get("precipitation_probability_max", [0])[0] or 0) / 100.0
        precip = daily.get("precipitation_sum", [0])[0] or 0
        snow = daily.get("snowfall_sum", [None])[0]

        fc = WeatherForecast(
            city=city, source="gfs_open_meteo",
            forecast_time=datetime.now(),
            temp_high_c=thc, temp_low_c=tlc,
            temp_high_f=thc * 9 / 5 + 32 if thc else None,
            temp_low_f=tlc * 9 / 5 + 32 if tlc else None,
            precip_mm=precip, precip_prob=pp, snow_mm=snow, raw_data=daily
        )
        _weather_cache[cache_key] = (time.time(), fc)
        logger.debug(f"GFS {city}: {thc:.1f}°C high, precip={pp:.0%}")
        return fc
    except Exception as e:
        logger.debug(f"GFS error {city}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# 4. Open-Meteo ECMWF (Найточніше для Європи)
# ════════════════════════════════════════════════════════════

def fetch_ecmwf_forecast(city: str) -> Optional[WeatherForecast]:
    """ECMWF через Open-Meteo — найточніша модель для Європи."""
    cache_key = f"ecmwf_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < 900:
            return data

    coords = _get_coords(city)
    if not coords:
        return None
    lat, lon = coords

    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/ecmwf",
            params={
                "latitude": lat, "longitude": lon,
                "daily": ["temperature_2m_max", "temperature_2m_min",
                          "precipitation_sum", "precipitation_probability_max",
                          "snowfall_sum"],
                "temperature_unit": "celsius",
                "precipitation_unit": "mm",
                "timezone": "auto", "forecast_days": 3
            },
            timeout=10
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        if not daily:
            return None

        thc = daily["temperature_2m_max"][0]
        tlc = daily["temperature_2m_min"][0]
        pp = (daily.get("precipitation_probability_max", [0])[0] or 0) / 100.0
        precip = daily.get("precipitation_sum", [0])[0] or 0
        snow = daily.get("snowfall_sum", [None])[0]

        fc = WeatherForecast(
            city=city, source="ecmwf_open_meteo",
            forecast_time=datetime.now(),
            temp_high_c=thc, temp_low_c=tlc,
            temp_high_f=thc * 9 / 5 + 32 if thc else None,
            temp_low_f=tlc * 9 / 5 + 32 if tlc else None,
            precip_mm=precip, precip_prob=pp, snow_mm=snow, raw_data=daily
        )
        _weather_cache[cache_key] = (time.time(), fc)
        logger.debug(f"ECMWF {city}: {thc:.1f}°C high, precip={pp:.0%}")
        return fc
    except Exception as e:
        logger.debug(f"ECMWF error {city}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# 5. Open-Meteo стандарт (fallback)
# ════════════════════════════════════════════════════════════

def fetch_open_meteo_forecast(city: str) -> Optional[WeatherForecast]:
    """Open-Meteo стандартна модель — глобальний fallback."""
    cache_key = f"openmeteo_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < 900:
            return data

    coords = _get_coords(city)
    if not coords:
        return None
    lat, lon = coords

    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "daily": ["temperature_2m_max", "temperature_2m_min",
                          "precipitation_sum", "precipitation_probability_max",
                          "snowfall_sum", "windspeed_10m_max"],
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "timezone": "auto", "forecast_days": 3
            },
            timeout=10
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        if not daily:
            return None

        thc = daily["temperature_2m_max"][0]
        tlc = daily["temperature_2m_min"][0]
        pp = (daily.get("precipitation_probability_max", [0])[0] or 0) / 100.0
        precip = daily.get("precipitation_sum", [0])[0] or 0
        snow = daily.get("snowfall_sum", [None])[0]
        wind = daily.get("windspeed_10m_max", [None])[0]

        fc = WeatherForecast(
            city=city, source="open_meteo",
            forecast_time=datetime.now(),
            temp_high_c=thc, temp_low_c=tlc,
            temp_high_f=thc * 9 / 5 + 32 if thc else None,
            temp_low_f=tlc * 9 / 5 + 32 if tlc else None,
            precip_mm=precip, precip_prob=pp, snow_mm=snow,
            wind_kmh=wind, raw_data=daily
        )
        _weather_cache[cache_key] = (time.time(), fc)
        logger.debug(f"Open-Meteo {city}: {thc:.1f}°C, precip={pp:.0%}")
        return fc
    except Exception as e:
        logger.debug(f"Open-Meteo error {city}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# КОНСЕНСУС: всі джерела → зважене середнє
# ════════════════════════════════════════════════════════════

SOURCE_WEIGHTS = {
    "noaa":          0.95,
    "ecmwf_open_meteo": 0.90,
    "gfs_open_meteo":   0.88,
    "nasa_power":    0.75,
    "open_meteo":    0.80,
}


def get_multi_source_consensus(city: str) -> Optional[WeatherForecast]:
    """
    Отримати консенсус з усіх доступних джерел.
    Зважене середнє за точністю джерела.
    """
    forecasts = []

    # Отримуємо всі доступні прогнози
    for fn in [fetch_noaa_forecast, fetch_ecmwf_forecast,
               fetch_gfs_forecast, fetch_nasa_power_forecast,
               fetch_open_meteo_forecast]:
        try:
            fc = fn(city)
            if fc:
                forecasts.append(fc)
        except Exception:
            pass

    if not forecasts:
        return None
    if len(forecasts) == 1:
        return forecasts[0]

    # Зважене середнє
    def wavg(attr: str) -> Optional[float]:
        vals = [(getattr(fc, attr), SOURCE_WEIGHTS.get(fc.source, 0.7))
                for fc in forecasts if getattr(fc, attr) is not None]
        if not vals:
            return None
        total_w = sum(w for _, w in vals)
        return sum(v * w for v, w in vals) / total_w if total_w > 0 else None

    sources = "+".join(fc.source.split("_")[0] for fc in forecasts[:3])
    return WeatherForecast(
        city=city,
        source=f"consensus_{len(forecasts)}src_{sources}",
        forecast_time=datetime.now(),
        temp_high_c=wavg("temp_high_c"),
        temp_low_c=wavg("temp_low_c"),
        temp_high_f=wavg("temp_high_f"),
        temp_low_f=wavg("temp_low_f"),
        precip_mm=wavg("precip_mm"),
        precip_prob=wavg("precip_prob"),
        snow_mm=wavg("snow_mm"),
        wind_kmh=wavg("wind_kmh"),
    )


def get_best_forecast(city: str) -> Optional[WeatherForecast]:
    """Найкращий прогноз: пробуємо джерела від кращого до гіршого."""
    for fn in [fetch_noaa_forecast, fetch_ecmwf_forecast,
               fetch_gfs_forecast, fetch_open_meteo_forecast,
               fetch_nasa_power_forecast]:
        try:
            fc = fn(city)
            if fc:
                return fc
        except Exception:
            pass
    return None
