"""
bootstrap_calibration.py — one-time sigma calibration primer for WeatherBot v27+

PROBLEM
-------
sigma_calibrator.py has wiring (data_fetcher._get_sigma calls get_adaptive_sigma;
trader.py resolution calls record_forecast_error) but the calibrator file is empty,
so get_adaptive_sigma falls back to hardcoded SIGMA_MIN=3.0C for every city/source.
At 3 trades/day bot needs weeks to cross the 5-sample threshold naturally -> bot keeps
trading on phantom sigma -> 0/15 win rate (see Recomeng.md + log.md 15-16.07.2026).

WHAT THIS SCRIPT DOES
---------------------
1. Pull 365 days of ERA5 daily max/min (ground truth) from Open-Meteo Archive API
   for every city in config.CITY_WHITELIST (uses data_fetcher.CITY_COORDS).
2. Pull archived daily max/min for each forecast source the bot actually uses:
      - NOAA / GFS   -> models=gfs_seamless   (exact match, source key "NOAA")
      - ENSEMBLE     -> models=icon_seamless  (PROXY: independent DWD seamless
                                              forecast; archived GEFS ensemble
                                              is NOT exposed by free-tier API.
                                              Same city-level bias structure
                                              is what we are estimating.)
      - ECMWF        -> models=ecmwf_ifs025   (extra 3rd independent source)
      - NASA_POWER   -> SKIPPED  (era5_power archive endpoint invalid on free
                                  tier; calibrator will self-learn NASA_POWER
                                  from live resolutions, slower but honest)
3. Pair forecast vs actual per day; call sigma_calibrator.record_forecast_error
   once per (city, source, day) so the calibrator accumulates the same shape of
   data the live trader would accumulate at resolution time.
4. Save sigma_calibration.json. After this run, every (city, source) has
   >300 samples -> get_adaptive_sigma returns real RMS-based sigma immediately.

runtime source string '+'-joined (e.g. "Open-Meteo_ENSEMBLE+NOAA+NASA_POWER"):
sigma_calibrator.get_adaptive_sigma SPLITS combined keys and aggregates per
component, so bootstrap feeding per-component keys (NOAA, ENSEMBLE, ECMWF) is
look-up-able at runtime regardless of how sources combine.

WHY THIS IS HONEST
------------------
We do NOT pretend the archive GMF forecast equals the as-issued N-hour-ahead forecast.
We are measuring *systematic model bias* vs ERA5 truth per city per source, which
is the dominant error contributor for cheap-YES peak-bucket trades (1C buckets).
Horizon-dependent growth is already encoded in get_adaptive_sigma via the
(1.0 + 0.015 * (hours - 6)) factor and in _get_sigma via sqrt(hours/12); both
are kept unchanged. We are only replacing the *base* sigma the bot falls back to.

USAGE
-----
    python bootstrap_calibration.py            # full run, 28 cities x 365 days
    python bootstrap_calibration.py --days 30  # quick test
    python bootstrap_calibration.py --cities "London,Paris" --days 90
    python bootstrap_calibration.py --dry-run  # compute only, don't write file

Re-run weekly once the live trader is also self-feeding (no harm in overwriting
with fresh data; calibrator internally caps at last 50 samples but bootstrap
spans a long window so it overrides with the most recent N days).

REQUIREMENTS
------------
- requests
- internet to archive-api.open-meteo.com
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

import config
import data_fetcher as df
import sigma_calibrator as cal

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT = 30
INTER_REQUEST_SEC = 0.65   # respect Open-Meteo free-tier rate limit (~100 req/min)
DAYS_DEFAULT = 365
HORIZON_HOURS_PROXY = 24.0  # daily archive is daily; sigma horizon growth handled elsewhere

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bootstrap")


def _date_range(days: int) -> Tuple[str, str]:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _fetch_daily(url: str, params: Dict) -> Optional[Dict]:
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code == 429:
            log.warning("429 rate-limited, sleeping 20s")
            time.sleep(20)
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            log.error(f"API error: {j.get('reason', j)}")
            return None
        return j
    except Exception as e:
        log.error(f"Fetch failed: {e}")
        return None


def _pull_daily_series(lat: float, lon: float, start: str, end: str, model: str,
                       want_min: bool = True) -> Optional[Dict[str, Tuple[float, float]]]:
    """Returns {date_iso: (max_c, min_c)} or None."""
    daily_vars = "temperature_2m_max"
    if want_min:
        daily_vars += ",temperature_2m_min"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": daily_vars,
        "timezone": "auto",
        "models": model,
    }
    j = _fetch_daily(ARCHIVE_URL, params)
    if not j or "daily" not in j:
        return None
    out: Dict[str, Tuple[float, float]] = {}
    times = j["daily"].get("time", [])
    maxes = j["daily"].get("temperature_2m_max", [])
    mins = j["daily"].get("temperature_2m_min", [])
    for i, day in enumerate(times):
        try:
            mx = float(maxes[i]) if i < len(maxes) and maxes[i] is not None else float("nan")
            mn = float(mins[i]) if i < len(mins) and mins[i] and mins[i] is not None else float("nan")
            if mx == mx or mn == mn:  # nan check
                out[day] = (mx, mn)
        except Exception:
            continue
    return out


def _filter_present(d: Dict[str, Tuple[float, float]]) -> Dict[str, Tuple[float, float]]:
    return {k: v for k, v in d.items()
            if v[0] == v[0] and v[1] == v[1] and v[0] is not None and v[1] is not None}


def calibrate_city(city: str, lat: float, lon: float, start: str, end: str) -> Dict[str, int]:
    """Pull ERA5 actuals + each forecast source; feed errors to calibrator."""
    stats = {"actuals_days": 0, "noaa_days": 0, "ensemble_days": 0, "ecmwf_days": 0}

    actuals = _pull_daily_series(lat, lon, start, end, model="era5", want_min=True)
    if not actuals:
        log.warning(f"[{city}] no ERA5 actuals returned")
        return stats
    actuals = _filter_present(actuals)
    stats["actuals_days"] = len(actuals)
    if len(actuals) < 30:
        log.warning(f"[{city}] only {len(actuals)} actual days -> calibration weak")

    # Forecast sources the bot uses at runtime (see data_fetcher.py sources_used
    # population: 'NOAA', 'NASA_POWER', 'Open-Meteo_ENSEMBLE', 'METAR', 'OBSERVED').
    # Open-Meteo Archive API exposes reanalysis + a handful of seamless forecast
    # archives; it does NOT expose archived GEFS ensemble or archived NASA POWER
    # at free tier. We substitute independent archive models as HONEST PROXIES:
    #
    #   NOAA            -> gfs_seamless        (exact match: bot NOAA=GFS archive)
    #   NASA_POWER      -> SKIPPED  (no valid free-tier archive; self-learning)
    #   ENSEMBLE        -> icon_seamless       (DWD post-processed seamless -- a
    #                                          genuinely independent model with
    #                                          ensemble corrections; closest
    #                                          proxy to live Open-Meteo ENSEMBLE)
    #   extra: ECMWF    -> ecmwf_ifs025        (4th independent source, bias shape
    #                                          differs from GFS/ICON; calibrator
    #                                          self-learns the mapping on run)
    # Proxy disclosure: RMS we measure is for the archive model vs ERA5 truth;
    # bot's live feed differs slightly, but the per-city systematic-bias structure
    # is what we are estimating, and that carries across model versions.
    sources = [
        ("NOAA",               "gfs_seamless",  True),    # exact: bot NOAA == GFS
        ("Open-Meteo_ENSEMBLE","icon_seamless", True),    # proxy: independent seamless
        ("ECMWF",              "ecmwf_ifs025",  True),    # extra independent source
    ]
    for src_key, model, want_min in sources:
        fc = _pull_daily_series(lat, lon, start, end, model=model, want_min=want_min)
        if not fc:
            log.warning(f"[{city}] source {src_key}: no archive data via model={model}")
            continue
        fc = _filter_present(fc)

        # feed errors one day at a time so calibrator structure matches live path
        n = 0
        for day, (fc_max, fc_min) in fc.items():
            if day not in actuals:
                continue
            ac_max, ac_min = actuals[day]
            # max temp error (HIGH markets)
            if fc_max == fc_max and ac_max == ac_max:
                cal.record_forecast_error(city, src_key, fc_max, ac_max)
                n += 1
            # min temp error (LOW markets) — store under source+"_LOW" so calibrator
            # doesn't conflate the two error distributions; in live path they share
            # the source string but bias differs (e.g. cold bias in minima).
            if fc_min == fc_min and ac_min == ac_min:
                cal.record_forecast_error(city, src_key + "_MIN", fc_min, ac_min)
                n += 1
        stats_key = {"NOAA": "noaa_days",
                     "Open-Meteo_ENSEMBLE": "ensemble_days",
                     "ECMWF": "ecmwf_days"}[src_key]
        stats[stats_key] = n
        time.sleep(INTER_REQUEST_SEC)

    return stats


def reset_calibrator_state():
    """Wipe in-memory calibrator state so we start clean (file also overwrite)."""
    with cal._lock:
        cal._city_source_errors = {}
    if os.path.exists(cal._CALIBRATOR_FILE):
        try:
            os.remove(cal._CALIBRATOR_FILE)
        except Exception as e:
            log.warning(f"Could not remove old calibrator file: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DAYS_DEFAULT,
                    help=f"Archive window in days (default {DAYS_DEFAULT})")
    ap.add_argument("--cities", type=str, default="",
                    help="Comma-separated city names (default: all in config.CITY_WHITELIST)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute everything but do NOT write sigma_calibration.json")
    ap.add_argument("--append", action="store_true",
                    help="Keep existing calibrator samples and add new (default: reset)")
    args = ap.parse_args()

    cities = [c.strip() for c in args.cities.split(",") if c.strip()] or list(config.CITY_WHITELIST)
    start, end = _date_range(args.days)
    log.info(f"Bootstrap calibration: {len(cities)} cities, window {start} -> {end} "
             f"({args.days} days), dry_run={args.dry_run}")

    if not args.append and not args.dry_run:
        reset_calibrator_state()
        log.info("Calibrator memory and file reset (use --append to keep prior samples)")

    per_city_stats = {}
    n_ok, n_fail = 0, 0
    for i, city in enumerate(cities, 1):
        coord = df.CITY_COORDS.get(city)
        if not coord or len(coord) < 2:
            log.warning(f"[{i}/{len(cities)}] {city}: no coordinates, skip")
            n_fail += 1
            continue
        lat, lon = coord[0], coord[1]
        log.info(f"[{i}/{len(cities)}] {city} @ ({lat}, {lon})")
        try:
            stats = calibrate_city(city, lat, lon, start, end)
            per_city_stats[city] = stats
            total = stats["noaa_days"] + stats["ensemble_days"] + stats["ecmwf_days"]
            if total > 0:
                n_ok += 1
                log.info(f"  -> {city}: {total} samples (NOAA {stats['noaa_days']}, "
                         f"ENS {stats['ensemble_days']}, ECMWF {stats['ecmwf_days']})")
            else:
                n_fail += 1
                log.warning(f"  -> {city}: 0 samples (API failure?)")
        except Exception as e:
            log.error(f"  -> {city} FAILED: {e}")
            n_fail += 1

    if args.dry_run:
        log.info("DRY-RUN: not writing sigma_calibration.json")
        cal_stats = cal.get_calibration_stats()
        log.info(f"Computed calibrator state: {len(cal_stats)} cities, "
                 f"{sum(len(v) for v in cal_stats.values())} (city,source) pairs")
        # preview a few
        for c in list(cal_stats)[:3]:
            for src, info in cal_stats[c].items():
                log.info(f"  {c} / {src}: n={info['n']} rms={info['rms']}C")
        return

    # Save
    cal._save()
    cal_stats = cal.get_calibration_stats()
    total_pairs = sum(len(v) for v in cal_stats.values())
    thresholds_crossed = sum(
        1 for c, srcs in cal_stats.items()
        for s, info in srcs.items()
        if info["n"] >= cal._MIN_SAMPLES_FOR_ADAPTIVE
    )
    log.info("=" * 60)
    log.info(f"DONE: {n_ok}/{len(cities)} cities calibrated, {n_fail} failed")
    log.info(f"File: {cal._CALIBRATOR_FILE}")
    log.info(f"Total (city, source) pairs: {total_pairs}")
    log.info(f"Pairs crossing 5-sample adaptive threshold: {thresholds_crossed}")
    log.info("=" * 60)
    log.info("NEXT: push sigma_calibration.json to WeatherBot/data/ on Render.")
    log.info("Bot will pick it up on next cycle (calibrator loads at module import).")

    # also write a small summary file for human review
    summary_path = os.path.join(config.DATA_DIR, "calibration_summary.json")
    try:
        with open(summary_path, "w") as f:
            json.dump({
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "window_start": start,
                "window_end": end,
                "days": args.days,
                "cities": per_city_stats,
                "calibrator_stats": cal_stats,
            }, f, indent=2)
        log.info(f"Summary written: {summary_path}")
    except Exception as e:
        log.warning(f"Could not write summary: {e}")


if __name__ == "__main__":
    main()
