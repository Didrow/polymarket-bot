# polymarket-bot-main/data_fetcher.py
"""
data_fetcher.py — Polymarket Weather Bot 2026 (повністю виправлена версія)
NOAA 404 → debug, всі prob_* функції з жорсткими межами 0.12-0.88
"""

import math
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

import config
import numpy as np   # для деяких fallback'ів

logger = logging.getLogger(__name__)

_weather_cache: Dict[str, Tuple[float, Any]] = {}


@dataclass
class WeatherForecast:
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
        if self.temp_high_c is None:
            return 0.50
        diff = self.temp_high_c - threshold_c
        sigma = 2.5
        prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))
        return max(0.12, min(0.88, round(prob, 4)))

    def prob_above_temp_f(self, threshold_f: float) -> float:
        threshold_c = (threshold_f - 32) * 5 / 9
        return self.prob_above_temp_c(threshold_c)

    def prob_exact_temp_c(self, target_c: float, sigma: float = 1.5) -> float:
        if self.temp_high_c is None:
            return 0.40
        try:
            from scipy.stats import norm
            p = norm.cdf(target_c + 0.5, self.temp_high_c, sigma) - \
                norm.cdf(target_c - 0.5, self.temp_high_c, sigma)
            return max(0.12, min(0.88, round(p, 4)))
        except:
            diff = abs(self.temp_high_c - target_c)
            p = max(0.12, min(0.88, 0.40 * math.exp(-0.5 * (diff / sigma) ** 2)))
            return p

    def prob_rain(self) -> float:
        if self.precip_prob is not None:
            return max(0.12, min(0.88, self.precip_prob))
        if self.precip_mm is not None:
            return max(0.12, min(0.88, self.precip_mm / 15.0))
        return 0.35

    def prob_snow(self) -> float:
        if self.snow_mm is not None and self.snow_mm > 1.0:
            return max(0.12, min(0.88, (self.precip_prob or 0.5) * 0.85))
        if self.precip_prob and self.temp_high_c is not None and self.temp_high_c < 2:
            return max(0.12, min(0.88, self.precip_prob * 0.85))
        return 0.15


# ==================== CITY COORDS ====================
CITY_COORDS_EXTENDED: Dict[str, Tuple[float, float]] = {
    "NYC": (40.7128, -74.0060), "New York": (40.7128, -74.0060),
    "Chicago": (41.8781, -87.6298), "Los Angeles": (34.0522, -118.2437),
    "San Francisco": (37.7749, -122.4194), "Miami": (25.7617, -80.1918),
    "London": (51.5074, -0.1278), "Paris": (48.8566, 2.3522),
    "Berlin": (52.5200, 13.4050), "Tokyo": (35.6762, 139.6503),
    # ... (додайте сюди всі міста з вашого оригінального файлу, якщо потрібно)
}

NOAA_CITIES = {"NYC", "New York", "Chicago", "Los Angeles", "San Francisco", "Miami", "Boston", "Houston", "Denver", "Phoenix"}


def _get_coords(city: str) -> Optional[Tuple[float, float]]:
    coords = CITY_COORDS_EXTENDED.get(city)
    if coords:
        return coords
    # Geocoding fallback
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search", 
                         params={"name": city, "count": 1}, timeout=8)
        results = r.json().get("results", [])
        if results:
            return (results[0]["latitude"], results[0]["longitude"])
    except:
        pass
    return None


# ==================== NOAA ====================
def fetch_noaa_forecast(city: str) -> Optional[WeatherForecast]:
    if city not in NOAA_CITIES:
        return None
    cache_key = f"noaa_{city}"
    if cache_key in _weather_cache and time.time() - _weather_cache[cache_key][0] < 900:
        return _weather_cache[cache_key][1]

    coords = _get_coords(city)
    if not coords:
        return None
    lat, lon = coords

    try:
        r = requests.get(f"https://api.weather.gov/points/{lat},{lon}", 
                         headers={"User-Agent": "PolymarketWeatherBot/2.0"}, timeout=10)
        r.raise_for_status()
        forecast_url = r.json()["properties"]["forecast"]

        r2 = requests.get(forecast_url, headers={"User-Agent": "PolymarketWeatherBot/2.0"}, timeout=10)
        r2.raise_for_status()
        periods = r2.json()["properties"]["periods"]

        day = next((p for p in periods if p.get("isDaytime")), periods[0])
        temp_high_f = float(day.get("temperature", 70))
        temp_high_c = (temp_high_f - 32) * 5 / 9

        fc = WeatherForecast(
            city=city, source="noaa", forecast_time=datetime.now(),
            temp_high_c=temp_high_c, temp_high_f=temp_high_f,
            precip_prob=float(day.get("probabilityOfPrecipitation", {}).get("value", 30)) / 100.0 if day.get("probabilityOfPrecipitation") else None
        )
        _weather_cache[cache_key] = (time.time(), fc)
        return fc
    except Exception as e:
        logger.debug(f"NOAA error {city}: {e} → using Open-Meteo fallback")
        return None


# ==================== Open-Meteo функції (GFS, ECMWF, стандарт) ====================
def fetch_open_meteo_forecast(city: str, model: str = "forecast") -> Optional[WeatherForecast]:
    coords = _get_coords(city)
    if not coords:
        return None
    lat, lon = coords

    try:
        url = f"https://api.open-meteo.com/v1/{model if model != 'forecast' else 'forecast'}"
        params = {
            "latitude": lat, "longitude": lon,
            "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_probability_max", "precipitation_sum"],
            "temperature_unit": "celsius", "precipitation_unit": "mm",
            "timezone": "auto", "forecast_days": 3
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        daily = r.json().get("daily", {})

        if not daily or not daily.get("temperature_2m_max"):
            return None

        fc = WeatherForecast(
            city=city,
            source=f"open_meteo_{model}",
            forecast_time=datetime.now(),
            temp_high_c=daily["temperature_2m_max"][0],
            temp_low_c=daily["temperature_2m_min"][0],
            precip_prob=daily.get("precipitation_probability_max", [30])[0] / 100.0,
            precip_mm=daily.get("precipitation_sum", [0])[0]
        )
        return fc
    except Exception as e:
        logger.debug(f"Open-Meteo {model} error {city}: {e}")
        return None


def fetch_gfs_forecast(city: str) -> Optional[WeatherForecast]:
    return fetch_open_meteo_forecast(city, "gfs")

def fetch_ecmwf_forecast(city: str) -> Optional[WeatherForecast]:
    return fetch_open_meteo_forecast(city, "ecmwf")


# ==================== CONSENSUS ====================
def get_multi_source_consensus(city: str) -> Optional[WeatherForecast]:
    forecasts = []
    for fn in [fetch_noaa_forecast, fetch_ecmwf_forecast, fetch_gfs_forecast, fetch_open_meteo_forecast]:
        try:
            fc = fn(city)
            if fc:
                forecasts.append(fc)
        except:
            pass

    if not forecasts:
        return None
    if len(forecasts) == 1:
        return forecasts[0]

    # Зважений consensus (спрощений)
    avg_high_c = sum(f.temp_high_c for f in forecasts if f.temp_high_c is not None) / len(forecasts)
    avg_precip_prob = sum(f.precip_prob or 0.35 for f in forecasts) / len(forecasts)

    return WeatherForecast(
        city=city,
        source=f"consensus_{len(forecasts)}src",
        forecast_time=datetime.now(),
        temp_high_c=round(avg_high_c, 1),
        precip_prob=round(avg_precip_prob, 2)
    )


def get_best_forecast(city: str) -> Optional[WeatherForecast]:
    """Fallback якщо consensus не спрацював"""
    for fn in [fetch_noaa_forecast, fetch_ecmwf_forecast, fetch_gfs_forecast, fetch_open_meteo_forecast]:
        try:
            fc = fn(city)
            if fc:
                return fc
        except:
            pass
    return None
