"""
data_fetcher.py — Multi-source weather consensus для Polymarket Weather Bot 2026
Джерела: NOAA + NASA POWER + Open-Meteo GFS + ECMWF + fallback
"""

import requests
import json
import time
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

@dataclass
class WeatherForecast:
    city: str
    timestamp: datetime
    temp_high_c: float = 0.0
    temp_low_c: float = 0.0
    prob_rain: float = 0.0
    prob_snow: float = 0.0
    sources_used: List[str] = None

    def __post_init__(self):
        if self.sources_used is None:
            self.sources_used = []

    # Жорсткі clamps щоб уникнути 0.00 / 1.00
    def prob_above_temp_c(self, threshold_c: float) -> float:
        if self.temp_high_c == 0.0:
            return 0.50
        raw = 1.0 - (1.0 / (1.0 + ((self.temp_high_c - threshold_c) / 3.5) ** 2))  # sigmoid-like
        return max(0.12, min(0.88, round(raw, 4)))

    def prob_below_temp_c(self, threshold_c: float) -> float:
        if self.temp_low_c == 0.0:
            return 0.50
        raw = 1.0 / (1.0 + ((self.temp_low_c - threshold_c) / 3.5) ** 2)
        return max(0.12, min(0.88, round(raw, 4)))

    def prob_rain_or_snow(self) -> float:
        raw = max(self.prob_rain, self.prob_snow)
        return max(0.12, min(0.88, round(raw, 4)))

# ─────────────────────────────────────────────
# API FETCHERS
# ─────────────────────────────────────────────

def fetch_noaa_forecast(city: str, coords: Tuple[float, float]) -> Optional[WeatherForecast]:
    try:
        lat, lon = coords
        # Points endpoint
        point_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
        r = requests.get(point_url, timeout=10, headers={"User-Agent": "PolymarketBot/2026"})
        r.raise_for_status()
        data = r.json()
        forecast_url = data["properties"]["forecast"]

        r2 = requests.get(forecast_url, timeout=10, headers={"User-Agent": "PolymarketBot/2026"})
        r2.raise_for_status()
        periods = r2.json()["properties"]["periods"][:2]

        high = max(p.get("temperature", 15) for p in periods if p.get("temperature"))
        low = min(p.get("temperature", 10) for p in periods if p.get("temperature"))
        rain_prob = max(p.get("probabilityOfPrecipitation", {}).get("value", 0) for p in periods) / 100.0

        return WeatherForecast(
            city=city,
            timestamp=datetime.utcnow(),
            temp_high_c=high,
            temp_low_c=low,
            prob_rain=rain_prob,
            prob_snow=0.0,
            sources_used=["NOAA"]
        )
    except Exception as e:
        logger.debug(f"NOAA forecast error для {city}: {e}")
        return None


def fetch_nasa_power(city: str, coords: Tuple[float, float]) -> Optional[WeatherForecast]:
    try:
        lat, lon = coords
        end_date = (datetime.utcnow() + timedelta(days=2)).strftime("%Y%m%d")
        url = f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M_MAX,T2M_MIN,PRECTOTCORR&community=RE&longitude={lon}&latitude={lat}&start=20260417&end={end_date}&format=JSON"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()["properties"]["parameter"]

        tmax = list(data["T2M_MAX"].values())[0]
        tmin = list(data["T2M_MIN"].values())[0]
        precip = list(data["PRECTOTCORR"].values())[0]

        return WeatherForecast(
            city=city,
            timestamp=datetime.utcnow(),
            temp_high_c=tmax,
            temp_low_c=tmin,
            prob_rain=min(1.0, precip / 10.0),
            prob_snow=0.0,
            sources_used=["NASA_POWER"]
        )
    except Exception as e:
        logger.debug(f"NASA POWER error для {city}: {e}")
        return None


def fetch_open_meteo(city: str, coords: Tuple[float, float], model: str = "gfs") -> Optional[WeatherForecast]:
    try:
        lat, lon = coords
        url = f"https://api.open-meteo.com/v1/{model}?latitude={lat}&longitude={lon}&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto&forecast_days=3"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()["daily"]

        high = data["temperature_2m_max"][0]
        low = data["temperature_2m_min"][0]
        rain_prob = data["precipitation_probability_max"][0] / 100.0

        return WeatherForecast(
            city=city,
            timestamp=datetime.utcnow(),
            temp_high_c=high,
            temp_low_c=low,
            prob_rain=rain_prob,
            prob_snow=0.0,
            sources_used=[f"Open-Meteo_{model.upper()}"]
        )
    except Exception as e:
        logger.debug(f"Open-Meteo {model} error для {city}: {e}")
        return None


# ─────────────────────────────────────────────
# CONSENSUS
# ─────────────────────────────────────────────

CITY_COORDS: Dict[str, Tuple[float, float]] = {
    "NYC": (40.7128, -74.0060),
    "New York": (40.7128, -74.0060),
    "Chicago": (41.8781, -87.6298),
    "Los Angeles": (34.0522, -118.2437),
    "London": (51.5074, -0.1278),
    "Paris": (48.8566, 2.3522),
    "Berlin": (52.5200, 13.4049),
    "Tokyo": (35.6895, 139.6917),
    # додай інші міста за потребою
}

@lru_cache(maxsize=50)
def get_multi_source_consensus(city: str) -> Optional[WeatherForecast]:
    coords = CITY_COORDS.get(city)
    if not coords:
        logger.warning(f"Невідомі координати для {city}")
        return None

    forecasts = []

    # 1. NOAA (найвища вага для США)
    noaa = fetch_noaa_forecast(city, coords)
    if noaa:
        forecasts.append((noaa, 0.45))

    # 2. NASA POWER
    nasa = fetch_nasa_power(city, coords)
    if nasa:
        forecasts.append((nasa, 0.25))

    # 3. Open-Meteo GFS
    gfs = fetch_open_meteo(city, coords, "gfs")
    if gfs:
        forecasts.append((gfs, 0.15))

    # 4. Open-Meteo ECMWF
    ecmwf = fetch_open_meteo(city, coords, "ecmwf")
    if ecmwf:
        forecasts.append((ecmwf, 0.10))

    # 5. Стандартний Open-Meteo як fallback
    if not forecasts:
        std = fetch_open_meteo(city, coords, "")
        if std:
            forecasts.append((std, 1.0))

    if not forecasts:
        return None

    # Weighted consensus
    total_weight = sum(w for _, w in forecasts)
    consensus = WeatherForecast(city=city, timestamp=datetime.utcnow())

    consensus.temp_high_c = sum(f.temp_high_c * w for f, w in forecasts) / total_weight
    consensus.temp_low_c = sum(f.temp_low_c * w for f, w in forecasts) / total_weight
    consensus.prob_rain = sum(f.prob_rain * w for f, w in forecasts) / total_weight
    consensus.prob_snow = sum(f.prob_snow * w for f, w in forecasts) / total_weight
    consensus.sources_used = [s for f, _ in forecasts for s in f.sources_used]

    return consensus


def get_best_forecast(city: str) -> Optional[WeatherForecast]:
    """Повертає найкращий прогноз з кешем"""
    return get_multi_source_consensus(city)
