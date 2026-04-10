"""
data_fetcher.py — Polymarket Weather Bot 2026
Отримання прогнозів погоди з NOAA (США) та Open-Meteo (Глобально).
Обидва API — безкоштовні, без ключів.
"""

import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)

# ─── Кеш для уникнення повторних запитів ───────────────────
_weather_cache: Dict[str, Tuple[float, Any]] = {}


@dataclass
class WeatherForecast:
    """Структурований прогноз погоди для ринку."""
    city: str
    source: str                          # "noaa" або "open_meteo"
    forecast_time: datetime
    temp_high_f: Optional[float] = None  # Максимальна температура (°F)
    temp_low_f: Optional[float] = None   # Мінімальна температура (°F)
    temp_high_c: Optional[float] = None  # Максимальна температура (°C)
    temp_low_c: Optional[float] = None   # Мінімальна температура (°C)
    precip_mm: Optional[float] = None    # Опади (мм)
    precip_prob: Optional[float] = None  # Ймовірність опадів (0–1)
    snow_mm: Optional[float] = None      # Сніг (мм)
    wind_kmh: Optional[float] = None     # Вітер (км/год)
    raw_data: Dict = None

    def prob_above_temp_f(self, threshold: float) -> Optional[float]:
        """
        Оцінює ймовірність того, що max temp буде вище порогу (°F).
        Використовує sigmoid-подібну апроксимацію навколо прогнозного значення.
        """
        if self.temp_high_f is None:
            return None
        diff = self.temp_high_f - threshold
        # Нормальний розподіл із σ ≈ 4°F (типова похибка прогнозу NWS)
        import math
        sigma = 4.0
        prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))
        return round(prob, 4)

    def prob_rain(self) -> Optional[float]:
        """Ймовірність дощу."""
        return self.precip_prob

    def prob_snow(self) -> Optional[float]:
        """Ймовірність снігу (грубо: є опади + низька temp)."""
        if self.snow_mm is not None:
            return 0.9 if self.snow_mm > 1.0 else 0.15
        if self.precip_prob and self.temp_high_f and self.temp_high_f < 34:
            return self.precip_prob * 0.85
        return None


# ═══════════════════════════════════════════════════════════
# NOAA / NWS (США — безкоштовно, без ключів)
# ═══════════════════════════════════════════════════════════

def _noaa_get_gridpoint(lat: float, lon: float) -> Optional[Dict]:
    """Отримати gridpoint NOAA для координат."""
    cache_key = f"noaa_grid_{lat}_{lon}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < 86400:  # Кеш gridpoint на 24 год
            return data

    url = f"{config.NOAA_BASE_URL}/points/{lat},{lon}"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "PolymarketWeatherBot/1.0"})
        r.raise_for_status()
        data = r.json()
        _weather_cache[cache_key] = (time.time(), data)
        return data
    except Exception as e:
        logger.warning(f"NOAA gridpoint error {lat},{lon}: {e}")
        return None


def fetch_noaa_forecast(city: str) -> Optional[WeatherForecast]:
    """
    Отримати прогноз погоди NOAA для міста (тільки США).
    Використовує api.weather.gov — офіційний, безкоштовний, без ключів.
    """
    cache_key = f"noaa_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < config.WEATHER_CACHE_SEC:
            return data

    coords = config.CITY_COORDS.get(city)
    if not coords:
        logger.warning(f"Немає координат для: {city}")
        return None

    lat, lon = coords
    grid = _noaa_get_gridpoint(lat, lon)
    if not grid:
        return None

    try:
        forecast_url = grid["properties"]["forecast"]
        r = requests.get(forecast_url, timeout=10,
                         headers={"User-Agent": "PolymarketWeatherBot/1.0"})
        r.raise_for_status()
        periods = r.json()["properties"]["periods"]

        # Беремо перший денний period (ближчий прогноз)
        day_period = next((p for p in periods if p["isDaytime"]), periods[0])
        night_period = next((p for p in periods if not p["isDaytime"]), None)

        temp_high = float(day_period.get("temperature", 0))
        temp_low = float(night_period["temperature"]) if night_period else temp_high - 10

        # Ймовірність опадів
        precip_prob = None
        if day_period.get("probabilityOfPrecipitation", {}).get("value") is not None:
            precip_prob = day_period["probabilityOfPrecipitation"]["value"] / 100.0

        forecast = WeatherForecast(
            city=city,
            source="noaa",
            forecast_time=datetime.now(),
            temp_high_f=temp_high,
            temp_low_f=temp_low,
            temp_high_c=(temp_high - 32) * 5 / 9,
            temp_low_c=(temp_low - 32) * 5 / 9,
            precip_prob=precip_prob,
            raw_data=day_period
        )

        _weather_cache[cache_key] = (time.time(), forecast)
        logger.debug(f"NOAA {city}: high={temp_high}°F, precip={precip_prob}")
        return forecast

    except Exception as e:
        logger.warning(f"NOAA forecast error для {city}: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# Open-Meteo (Глобально — безкоштовно, без ключів)
# ═══════════════════════════════════════════════════════════

def _geocode_city(city: str) -> Optional[Tuple[float, float]]:
    """Геокодування назви міста через Open-Meteo Geocoding API."""
    # Спочатку шукаємо в локальній таблиці
    if city in config.CITY_COORDS:
        return config.CITY_COORDS[city]

    cache_key = f"geo_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < 86400 * 7:
            return data

    try:
        r = requests.get(
            config.OPEN_METEO_GEO_URL,
            params={"name": city, "count": 1, "language": "en"},
            timeout=10
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        lat = results[0]["latitude"]
        lon = results[0]["longitude"]
        _weather_cache[cache_key] = (time.time(), (lat, lon))
        return lat, lon
    except Exception as e:
        logger.warning(f"Geocoding error для {city}: {e}")
        return None


def fetch_open_meteo_forecast(city: str) -> Optional[WeatherForecast]:
    """
    Отримати прогноз погоди Open-Meteo для будь-якого міста.
    Безкоштовно, без ключів, глобально.
    """
    cache_key = f"openmeteo_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < config.WEATHER_CACHE_SEC:
            return data

    coords = _geocode_city(city)
    if not coords:
        return None

    lat, lon = coords
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": [
                "temperature_2m_max", "temperature_2m_min",
                "precipitation_sum", "precipitation_probability_max",
                "snowfall_sum", "windspeed_10m_max"
            ],
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "timezone": "auto",
            "forecast_days": 3
        }
        r = requests.get(config.OPEN_METEO_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily", {})
        if not daily:
            return None

        temp_high_c = daily["temperature_2m_max"][0]
        temp_low_c = daily["temperature_2m_min"][0]
        precip_mm = daily["precipitation_sum"][0]
        precip_prob = (daily.get("precipitation_probability_max", [None])[0] or 0) / 100.0
        snow_mm = daily.get("snowfall_sum", [None])[0]
        wind_kmh = daily.get("windspeed_10m_max", [None])[0]

        forecast = WeatherForecast(
            city=city,
            source="open_meteo",
            forecast_time=datetime.now(),
            temp_high_c=temp_high_c,
            temp_low_c=temp_low_c,
            temp_high_f=temp_high_c * 9 / 5 + 32 if temp_high_c else None,
            temp_low_f=temp_low_c * 9 / 5 + 32 if temp_low_c else None,
            precip_mm=precip_mm,
            precip_prob=precip_prob,
            snow_mm=snow_mm,
            wind_kmh=wind_kmh,
            raw_data=daily
        )

        _weather_cache[cache_key] = (time.time(), forecast)
        logger.debug(f"Open-Meteo {city}: high={temp_high_c}°C, precip={precip_prob:.0%}")
        return forecast

    except Exception as e:
        logger.warning(f"Open-Meteo error для {city}: {e}")
        return None


def get_best_forecast(city: str) -> Optional[WeatherForecast]:
    """
    Найкращий прогноз: GFS → ECMWF → NOAA → Open-Meteo (fallback).
    """
    # 1. GFS (NOAA) — пріоритет
    forecast = fetch_gfs_forecast(city)
    if forecast:
        return forecast

    # 2. ECMWF — друге найкраще
    forecast = fetch_ecmwf_forecast(city)
    if forecast:
        return forecast

    # 3. Старий порядок для США
    is_us_city = city in config.NOAA_CITIES
    if is_us_city:
        forecast = fetch_noaa_forecast(city)
        if forecast:
            return forecast

    # 4. Open-Meteo як останній fallback
    return fetch_open_meteo_forecast(city)

def get_multi_source_consensus(city: str) -> Optional[WeatherForecast]:
    """
    Отримати прогноз з двох джерел і взяти середнє — підвищує точність edge.
    """
    f1 = fetch_noaa_forecast(city) if city in config.NOAA_CITIES else None
    f2 = fetch_open_meteo_forecast(city)

    if not f1 and not f2:
        return None
    if not f1:
        return f2
    if not f2:
        return f1

    # Усереднення двох прогнозів
    def avg(a, b):
        if a is None and b is None:
            return None
        if a is None:
            return b
        if b is None:
            return a
        return (a + b) / 2

    return WeatherForecast(
        city=city,
        source="consensus_noaa+open_meteo",
        forecast_time=datetime.now(),
        temp_high_f=avg(f1.temp_high_f, f2.temp_high_f),
        temp_low_f=avg(f1.temp_low_f, f2.temp_low_f),
        temp_high_c=avg(f1.temp_high_c, f2.temp_high_c),
        temp_low_c=avg(f1.temp_low_c, f2.temp_low_c),
        precip_mm=avg(f1.precip_mm, f2.precip_mm),
        precip_prob=avg(f1.precip_prob, f2.precip_prob),
        snow_mm=avg(f1.snow_mm, f2.snow_mm),
        wind_kmh=avg(f1.wind_kmh, f2.wind_kmh),
    )
# ─────────────────────────────────────────────────────────────
# GFS (Global Forecast System) — додано для топ-edge
# ─────────────────────────────────────────────────────────────
def fetch_gfs_forecast(city: str) -> Optional[WeatherForecast]:
    """
    Новий джерело: Open-Meteo GFS (включає ensemble probabilities).
    Доповнює NOAA + Open-Meteo.
    """
    cache_key = f"gfs_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < config.WEATHER_CACHE_SEC:
            return data

    coords = _geocode_city(city)
    if not coords:
        return None

    lat, lon = coords
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": ["temperature_2m_max", "temperature_2m_min",
                      "precipitation_sum", "precipitation_probability_max",
                      "snowfall_sum"],
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
            "timezone": "auto",
            "forecast_days": 3
        }
        r = requests.get("https://api.open-meteo.com/v1/gfs", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily", {})
        if not daily:
            return None

        temp_high_c = daily["temperature_2m_max"][0]
        temp_low_c = daily["temperature_2m_min"][0]
        precip_mm = daily["precipitation_sum"][0]
        precip_prob = (daily.get("precipitation_probability_max", [None])[0] or 0) / 100.0
        snow_mm = daily.get("snowfall_sum", [None])[0]

        forecast = WeatherForecast(
            city=city,
            source="gfs_open_meteo",
            forecast_time=datetime.now(),
            temp_high_c=temp_high_c,
            temp_low_c=temp_low_c,
            temp_high_f=temp_high_c * 9 / 5 + 32 if temp_high_c else None,
            temp_low_f=temp_low_c * 9 / 5 + 32 if temp_low_c else None,
            precip_mm=precip_mm,
            precip_prob=precip_prob,
            snow_mm=snow_mm,
            raw_data=daily
        )

        _weather_cache[cache_key] = (time.time(), forecast)
        logger.info(f"GFS {city}: high={temp_high_c}°C, precip_prob={precip_prob:.0%}")
        return forecast

    except Exception as e:
        logger.warning(f"GFS error для {city}: {e}")
        return None
# ─────────────────────────────────────────────────────────────
# ECMWF (Європейський центр) — ще одне точне джерело
# ─────────────────────────────────────────────────────────────
def fetch_ecmwf_forecast(city: str) -> Optional[WeatherForecast]:
    """
    Новий джерело: ECMWF через Open-Meteo (дуже точний для Європи та глобально).
    """
    cache_key = f"ecmwf_{city}"
    if cache_key in _weather_cache:
        ts, data = _weather_cache[cache_key]
        if time.time() - ts < config.WEATHER_CACHE_SEC:
            return data

    coords = _geocode_city(city)
    if not coords:
        return None

    lat, lon = coords
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": ["temperature_2m_max", "temperature_2m_min",
                      "precipitation_sum", "precipitation_probability_max",
                      "snowfall_sum"],
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
            "timezone": "auto",
            "forecast_days": 3
        }
        r = requests.get("https://api.open-meteo.com/v1/ecmwf", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily", {})
        if not daily:
            return None

        temp_high_c = daily["temperature_2m_max"][0]
        temp_low_c = daily["temperature_2m_min"][0]
        precip_mm = daily["precipitation_sum"][0]
        precip_prob = (daily.get("precipitation_probability_max", [None])[0] or 0) / 100.0
        snow_mm = daily.get("snowfall_sum", [None])[0]

        forecast = WeatherForecast(
            city=city,
            source="ecmwf_open_meteo",
            forecast_time=datetime.now(),
            temp_high_c=temp_high_c,
            temp_low_c=temp_low_c,
            temp_high_f=temp_high_c * 9 / 5 + 32 if temp_high_c else None,
            temp_low_f=temp_low_c * 9 / 5 + 32 if temp_low_c else None,
            precip_mm=precip_mm,
            precip_prob=precip_prob,
            snow_mm=snow_mm,
            raw_data=daily
        )

        _weather_cache[cache_key] = (time.time(), forecast)
        logger.info(f"ECMWF {city}: high={temp_high_c}°C, precip_prob={precip_prob:.0%}")
        return forecast

    except Exception as e:
        logger.warning(f"ECMWF error для {city}: {e}")
        return None
