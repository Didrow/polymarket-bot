"""
sigma_calibrator.py — Polymarket Weather Bot (v14 ADAPTIVE SNIPER GRID)

Self-calibrating sigma per city per source based on resolved trade errors.
Learns from historical forecast errors which cities/sources are more/less accurate.
Replaces hardcoded sigma in WeatherForecast._get_sigma() with adaptive values.

Architecture:
- Stores (city, source, forecast_error_abs) for each resolved trade
- Computes rolling sigma = RMS(forecast_errors) over last N samples
- Falls back to hardcoded base sigma when insufficient data (<5 samples)
"""

import math
import json
import os
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import config

logger = logging.getLogger(__name__)

_MAX_SAMPLES = 50
_MIN_SAMPLES_FOR_ADAPTIVE = 5
_BLEND_WEIGHT = 0.6

_city_source_errors: Dict[str, Dict[str, List[float]]] = {}
_lock = threading.Lock()

_CALIBRATOR_FILE = os.path.join(config.DATA_DIR, "sigma_calibration.json")

_CALIBRATOR_CACHE_TTL = 300
_cache_ts: float = 0.0


def _load():
    global _city_source_errors, _cache_ts
    if not os.path.exists(_CALIBRATOR_FILE):
        return
    try:
        with open(_CALIBRATOR_FILE, "r") as f:
            data = json.load(f)
        with _lock:
            _city_source_errors = {}
            for city, sources in data.get("errors", {}).items():
                _city_source_errors[city] = {}
                for source, errors in sources.items():
                    _city_source_errors[city][source] = [float(e) for e in errors[-_MAX_SAMPLES:]]
        _cache_ts = time.time()
        total = sum(len(e) for s in _city_source_errors.values() for e in s.values())
        logger.info(f"📐 Sigma calibrator: завантажено {total} записів з {len(_city_source_errors)} міст")
    except Exception as e:
        logger.debug(f"Sigma calibrator load error: {e}")


def _save():
    try:
        os.makedirs(os.path.dirname(_CALIBRATOR_FILE), exist_ok=True)
        with _lock:
            data = {"errors": {}}
            for city, sources in _city_source_errors.items():
                data["errors"][city] = {}
                for source, errors in sources.items():
                    data["errors"][city][source] = errors[-_MAX_SAMPLES:]
        with open(_CALIBRATOR_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.debug(f"Sigma calibrator save error: {e}")


def record_forecast_error(city: str, source: str, forecast_c: float, actual_c: float):
    error = abs(forecast_c - actual_c)
    with _lock:
        if city not in _city_source_errors:
            _city_source_errors[city] = {}
        if source not in _city_source_errors[city]:
            _city_source_errors[city][source] = []
        _city_source_errors[city][source].append(error)
        _city_source_errors[city][source] = _city_source_errors[city][source][-_MAX_SAMPLES:]
    _save()
    logger.info(f"📐 Sigma calibrated: {city}/{source} error={error:.1f}°C")


def get_adaptive_sigma(city: str, source: str, base_sigma: float, hours: float = 24.0) -> float:
    with _lock:
        errors = _city_source_errors.get(city, {}).get(source, [])

    if len(errors) < _MIN_SAMPLES_FOR_ADAPTIVE:
        return base_sigma

    rms = math.sqrt(sum(e * e for e in errors) / len(errors))
    hour_factor = 1.0 + 0.015 * max(0, hours - 6)
    adaptive_sigma = rms * min(hour_factor, 1.5)

    blended = _BLEND_WEIGHT * adaptive_sigma + (1 - _BLEND_WEIGHT) * base_sigma

    logger.debug(
        f"📐 Adaptive sigma: {city}/{source} | rms={rms:.2f} | "
        f"adaptive={adaptive_sigma:.2f} | base={base_sigma:.2f} | blended={blended:.2f} | n={len(errors)}"
    )
    return round(blended, 3)


def get_calibration_stats() -> Dict:
    stats = {}
    with _lock:
        for city, sources in _city_source_errors.items():
            stats[city] = {}
            for source, errors in sources.items():
                if errors:
                    rms = math.sqrt(sum(e * e for e in errors) / len(errors))
                    stats[city][source] = {
                        "n": len(errors),
                        "rms": round(rms, 2),
                        "last_error": round(errors[-1], 2),
                    }
    return stats


_load()
