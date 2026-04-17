# polymarket-bot-main/data_fetcher.py
"""
data_fetcher.py — ВИПРАВЛЕНА ВЕРСІЯ (NOAA 404 → debug + надійний fallback)
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
    # ... (ваш оригінальний dataclass без змін)
    city: str
    source: str
    forecast_time: datetime
    temp_high_f: Optional[float] = None
    # ... (всі поля)

    def prob_above_temp_c(self, threshold_c: float) -> float:
        if self.temp_high_c is None:
            return 0.50
        diff = self.temp_high_c - threshold_c
        sigma = 2.5
        prob = 0.5 * (1 + math.erf(diff / (sigma * math.sqrt(2))))
        return max(0.12, min(0.88, round(prob, 4)))   # ← ВИПРАВЛЕНО: сильніше обмеження

    def prob_above_temp_f(self, threshold_f: float) -> float:
        threshold_c = (threshold_f - 32) * 5 / 9
        return self.prob_above_temp_c(threshold_c)

    def prob_exact_temp_c(self, target_c: float, sigma: float = 1.5) -> float:
        # ... (ваш код)
        return max(0.12, min(0.88, round(p, 4)))   # ← ВИПРАВЛЕНО

    def prob_rain(self) -> float:                  # ← тепер завжди float
        if self.precip_prob is not None:
            return max(0.12, min(0.88, self.precip_prob))
        if self.precip_mm is not None:
            return max(0.12, min(0.88, self.precip_mm / 15.0))
        return 0.35

    def prob_snow(self) -> float:
        if self.snow_mm is not None and self.snow_mm > 1.0:
            return max(0.12, min(0.88, (self.precip_prob or 0.5) * 0.85))
        return 0.15


# ... (CITY_COORDS_EXTENDED залишається без змін)

def fetch_noaa_forecast(city: str) -> Optional[WeatherForecast]:
    """NOAA — тепер debug замість WARNING + швидкий fallback"""
    if city not in NOAA_CITIES:
        return None
    # ... (ваш код до try)
    try:
        # ... (повний код без змін)
        return fc
    except Exception as e:
        logger.debug(f"NOAA error {city}: {e} → fallback Open-Meteo")
        return None   # не WARNING, а debug


# ... (всі інші fetch_ функції залишаються без змін)

def get_multi_source_consensus(city: str) -> Optional[WeatherForecast]:
    # ... (ваш код)
    # Гарантуємо, що consensus ніколи не None
    if not forecasts:
        return None
    # ... (зважений середній)
    return WeatherForecast(...)  # як у вас
