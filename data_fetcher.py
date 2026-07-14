"""
data_fetcher.py — Polymarket Weather Bot v22 (RATE-LIMITED)
Джерела: Open-Meteo ENSEMBLE (primary) + NOAA + NASA POWER + METAR (confirmation) + OBSERVED (physical floor)
v17: GFS/ECMWF видалено — 100% 429 на api.open-meteo.com. ENSEMBLE 31-member достатньо.
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
CACHE_TTL = 1800  # 30 хвилин (прогноз на день не змінюється швидше)

# Глобальний throttling для Open-Meteo (безкошвний tier ~60 req/min);
# 600ms = ~100 req/min — безпечно, залишає запас.
_last_request_time: float = 0.0
MIN_REQUEST_INTERVAL: float = 0.6  # 600ms між запитами


def _cache_set(key: str, val):
    _cache[key] = (time.time(), val)

def _cache_get(key: str):
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return val
    return None


def _throttle():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _request_with_retry(url, params=None, timeout=10, headers=None, retries=3, backoff=2):
    # 429 progressive backoff: 3s → 8s → 15s
    _429_WAIT = [3, 8, 15]
    for attempt in range(retries):
        _throttle()
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=headers)
            if r.status_code == 429:
                wait = _429_WAIT[min(attempt, len(_429_WAIT) - 1)]
                logger.debug(f"429 rate-limited, waiting {wait}s... ({url[:60]}...)")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            if attempt == retries - 1:
                raise e
            wait_time = backoff ** (attempt + 1)
            logger.warning(f"Request failed (attempt {attempt + 1}/{retries}). Retrying in {wait_time}s... Error: {e}")
            time.sleep(wait_time)
    return None


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
        """Повертає RAW (незміщені) члени ансамблю для підрахунку spread."""
        members = self.temp_low_members if is_low else self.temp_high_members
        if not members or len(members) < 5:
            return []
        return members

    def _get_base_sigma(self) -> float:
        return 3.5

    def _get_sigma(self, hours: float = 24.0) -> float:
        # sigma = max(ensemble_stdev * SIGMA_SPREAD_FACTOR, SIGMA_MIN)
        stdev = None
        for member_list in [self.temp_high_members, self.temp_low_members]:
            if member_list and len(member_list) >= 5:
                m_mean = sum(member_list) / len(member_list)
                m_var = sum((m - m_mean) ** 2 for m in member_list) / len(member_list)
                stdev = math.sqrt(m_var)
                break

        spread_factor = getattr(config, 'SIGMA_SPREAD_FACTOR', 1.30)
        sigma_min = getattr(config, 'SIGMA_MIN', 1.5)

        if stdev is not None and stdev > 0:
            sigma_value = max(sigma_min, stdev * spread_factor)
        else:
            sigma_value = max(sigma_min, self._get_base_sigma())

        # Адаптивне калібрування з історичних помилок
        try:
            from sigma_calibrator import get_adaptive_sigma
            source = "+".join(self.sources_used) if self.sources_used else "SINGLE"
            return get_adaptive_sigma(self.city, source, sigma_value, hours)
        except Exception:
            return round(sigma_value, 3)

    def raw_prob_above_temp_c(self, threshold_c: float, is_low: bool = False, hours: float = 24.0) -> float:
        # PURE GAUSSIAN: використовуємо тільки parametric (без empirical blending)
        sigma = self._get_sigma(hours)
        mean = self.temp_low_c if is_low else self.temp_high_c
        prob = 0.5 * (1 + math.erf((mean - threshold_c) / (sigma * math.sqrt(2))))
        return prob

    def prob_above_temp_c(self, threshold_c: float, is_low: bool = False, hours: float = 24.0) -> float:
        raw_p = self.raw_prob_above_temp_c(threshold_c, is_low, hours)
        biased = raw_p
        
        if hours <= 6.0:
            max_cap = getattr(config, 'CAP_SHORT', 0.85)
        elif hours <= 18.0:
            max_cap = getattr(config, 'CAP_MID', 0.75)
        else:
            max_cap = getattr(config, 'CAP_LONG', 0.65)

        return max(0.01, min(max_cap, round(biased, 4)))

    def prob_below_temp_c(self, threshold_c: float, is_low: bool = False, hours: float = 24.0) -> float:
        raw_p = 1.0 - self.raw_prob_above_temp_c(threshold_c, is_low, hours)
        biased = raw_p
        
        if hours <= 6.0:
            max_cap = getattr(config, 'CAP_SHORT', 0.85)
        elif hours <= 18.0:
            max_cap = getattr(config, 'CAP_MID', 0.75)
        else:
            max_cap = getattr(config, 'CAP_LONG', 0.65)

        return max(0.01, min(max_cap, round(biased, 4)))

    def prob_exact_temp_c(self, threshold_c: float, is_low: bool = False, half_width: float = 0.5, hours: float = 24.0) -> float:
        members = self._get_adjusted_members(is_low)

        # Кап з config.py (змінювати там)
        if hours <= 6.0:
            max_cap = getattr(config, 'CAP_SHORT', 0.85)
        elif hours <= 18.0:
            max_cap = getattr(config, 'CAP_MID', 0.75)
        else:
            max_cap = getattr(config, 'CAP_LONG', 0.65)

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
            
            # v13: реалістичні ймовірності для forecast ladder grid
            # Було 0.30/0.70*0.55 (discount 45%) — our_prob=11% для бакета при прогнозі.
            # Тепер 0.40/0.60*0.75 (discount 25%) — our_prob=22-30% для центрального бакета.
            # Це дозволяє сітці 16.8/16.9/17.0/17.1/17.2 мати реалістичні edge.
            if prob_empirical == 0.0:
                prob = prob_parametric * 0.50  # v13: 0.30→0.50 (нульова емпірика — не означає 0% шанс)
            else:
                prob = (prob_empirical * 0.40 + prob_parametric * 0.60) * 0.75  # v13: 0.30/0.70*0.55 → 0.40/0.60*0.75
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
    # ── USA ──
    "NYC":           (40.7769, -73.8740),   # KLGA — LaGuardia (NOT JFK)
    "New York":      (40.7769, -73.8740),   # KLGA — LaGuardia
    "Chicago":       (41.9742, -87.9073),   # KORD — O'Hare
    "Los Angeles":   (33.9425, -118.4081),  # KLAX — LAX
    "San Francisco": (37.6213, -122.3790),  # KSFO — SFO
    "Miami":         (25.7959, -80.2870),   # KMIA — Miami Intl
    "Dallas":        (32.8481, -96.8512),   # KDAL — Love Field (NOT DFW)
    "Seattle":       (47.4502, -122.3088),  # KSEA — Sea-Tac
    "Boston":        (42.3656, -71.0096),   # KBOS — Logan
    "Denver":        (39.7017, -104.7517),  # KBKF — Buckley SFB (NOT KDEN)
    "Atlanta":       (33.6407, -84.4277),   # KATL — Hartsfield-Jackson
    "Houston":       (29.6456, -95.2789),   # KHOU — Hobby (NOT KIAH)
    "Austin":        (30.1974, -97.6664),   # KAUS — Bergstrom
    "Phoenix":       (33.4342, -112.0117),  # KPHX — Sky Harbor
    "Las Vegas":     (36.0800, -115.1522),  # KLAS — Harry Reid
    "Minneapolis":   (44.8830, -93.2108),   # KMSP — MSP
    "Portland":      (45.5887, -122.5975),  # KPDX — Portland Intl
    "Nashville":     (36.1244, -86.6782),   # KBNA — Nashville Intl
    "Charlotte":     (35.2140, -80.9431),   # KCLT — Charlotte Douglas
    "Orlando":       (28.4294, -81.3089),   # KMCO — Orlando Intl
    # ── Europe ──
    "London":        (51.5053,   0.0554),   # EGLC — London City (NOT Heathrow)
    "Paris":         (48.9694,   2.4414),   # LFPB — Le Bourget (NOT CDG)
    "Berlin":        (52.3667,  13.5033),   # EDDB — Brandenburg
    "Munich":        (48.3537,  11.7750),   # EDDM — Munich Airport
    "Amsterdam":     (52.3086,   4.7639),   # EHAM — Schiphol
    "Rome":          (41.8003,  12.2389),   # LIRF — Fiumicino
    "Madrid":        (40.4936,  -3.5668),   # LEMD — Barajas
    "Dublin":        (53.4213,  -6.2701),   # EIDW — Dublin
    "Warsaw":        (52.1657,  20.9671),   # EPWA — Chopin
    "Vienna":        (48.1103,  16.5700),   # LOWW — Vienna Intl
    "Prague":        (50.1008,  14.2600),   # LKPR — Vaclav Havel
    "Moscow":        (55.5917,  37.2615),   # UUWW — Vnukovo (NOT Domodedovo)
    "Ankara":        (40.1281,  32.9951),   # LTAC — Esenboga
    # ── Asia ──
    "Tokyo":         (35.5494, 139.7798),   # RJTT — Haneda
    "Seoul":         (37.4602, 126.4407),   # RKSI — Incheon
    "Busan":         (35.1795, 128.9381),   # RKPK — Gimhae
    "Shanghai":      (31.1434, 121.8053),   # ZSPD — Pudong (NOT Hongqiao)
    "Beijing":       (40.0799, 116.6031),   # ZBAA — Capital
    "Chengdu":       (30.5785, 103.9471),   # ZUUU — Shuangliu
    "Hong Kong":     (22.3193, 114.1694),   # VHHH — HKG (HKO data)
    "Singapore":     ( 1.3644, 103.9915),   # WSSS — Changi
    "Bangkok":       (13.6811, 100.7472),   # VTBS — Suvarnabhumi
    "Dubai":         (25.2532,  55.3657),   # OMDB — Dubai Intl
    "Jeddah":        (21.6786,  39.1575),   # OEJN — King Abdulaziz
    "Lucknow":       (26.7606,  80.8893),   # VILK — Chaudhary Charan Singh
    "Karachi":       (24.9075,  67.1612),   # OPKC — Jinnah Intl
    # ── South America ──
    "Buenos Aires":  (-34.8222, -58.5358),  # SAEZ — Ezeiza
    "Sao Paulo":     (-23.4356, -46.4731),  # SBGR — Guarulhos
    "Santiago":      (-33.3930, -70.7858),  # SCEL — Arturo Benitez
    "Lima":          (-12.0231, -77.1081),  # SPJC — Jorge Chavez
    # ── Africa ──
    "Cape Town":     (-33.9715,  18.6021),  # FACT — Cape Town Intl
    "Lagos":         ( 6.5774,   3.3211),   # DNMM — Murtala Muhammed
    # ── Australia / NZ ──
    "Sydney":        (-33.9399, 151.1753),  # YSSY — Kingsford Smith
    "Melbourne":     (-37.6733, 144.8435),  # YMML — Tullamarine
    "Brisbane":      (-27.3842, 153.1175),  # YBBN — Brisbane
    "Perth":         (-31.9403, 115.9668),  # YPPH — Perth
    "Auckland":      (-37.0081, 174.7919),  # NZAA — Auckland
    "Wellington":    (-41.3278, 174.8083),  # NZWN — Wellington
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

CITY_SEASON_BIAS_C: Dict[str, Dict[str, float]] = {
    # ── US cities ───────────────────────────────────────────
    "Miami": {"summer": 1.7, "winter": 0.0, "shoulder": 0.5},
    "Dallas": {"summer": 1.2, "winter": 0.0, "shoulder": 0.3},
    "Chicago": {"summer": 0.8, "winter": 0.0, "shoulder": 0.2},
    "NYC": {"summer": 0.6, "winter": 0.0, "shoulder": 0.2},
    "New York": {"summer": 0.6, "winter": 0.0, "shoulder": 0.2},
    "Los Angeles": {"summer": 0.5, "winter": 0.0, "shoulder": 0.2},
    "Houston": {"summer": 1.0, "winter": 0.0, "shoulder": 0.3},
    "Atlanta": {"summer": 0.7, "winter": 0.0, "shoulder": 0.2},
    "Seattle": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Denver": {"summer": 0.4, "winter": 0.0, "shoulder": 0.1},
    "Boston": {"summer": 0.6, "winter": 0.0, "shoulder": 0.2},
    "Austin": {"summer": 1.0, "winter": 0.0, "shoulder": 0.3},
    "San Francisco": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Phoenix": {"summer": 1.2, "winter": 0.0, "shoulder": 0.3},
    "Las Vegas": {"summer": 1.0, "winter": 0.0, "shoulder": 0.3},
    "Minneapolis": {"summer": 0.6, "winter": 0.0, "shoulder": 0.2},
    "Portland": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Nashville": {"summer": 0.7, "winter": 0.0, "shoulder": 0.2},
    "Charlotte": {"summer": 0.7, "winter": 0.0, "shoulder": 0.2},
    "Orlando": {"summer": 1.5, "winter": 0.0, "shoulder": 0.4},
    # ── Europe ──────────────────────────────────────────────
    "London": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Paris": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Berlin": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Munich": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Amsterdam": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Rome": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Madrid": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Dublin": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Warsaw": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Vienna": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Prague": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Moscow": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Ankara": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    # ── Asia ────────────────────────────────────────────────
    "Tokyo": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Seoul": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Busan": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Singapore": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Hong Kong": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Shanghai": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Beijing": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Dubai": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Bangkok": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Lucknow": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Chengdu": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Karachi": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Jeddah": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    # ── South America ───────────────────────────────────────
    "Buenos Aires": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Sao Paulo": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Santiago": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Lima": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    # ── Africa ──────────────────────────────────────────────
    "Cape Town": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Lagos": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    # ── Oceania ─────────────────────────────────────────────
    "Sydney": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Melbourne": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Brisbane": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Perth": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Auckland": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
    "Wellington": {"summer": 0.3, "winter": 0.0, "shoulder": 0.1},
}


def _get_season_bias_c(city: str) -> float:
    # v27: SEASON BIAS NUKE — pure-forecast strategy (was inflating US hot cities
    # Miami +1.7°C, Orlando +1.5°C, Dallas +1.2°C → phantom peaks killed v26).
    # Ensemble+observed are accurate on their own; no manual adjustments.
    return 0.0


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
        r = _request_with_retry(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en"},
            timeout=8
        )
        if r:
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
        target_date_str = target_date.isoformat()
    else:
        target_dt = datetime.now(timezone.utc) + timedelta(hours=hours_to_resolution)
        target_date_str = target_dt.date().isoformat()

    key = f"ensemble_{city}_{target_date_str}"
    cached = _cache_get(key)
    if cached:
        return cached

    lat, lon = coords
    try:
        r = _request_with_retry(
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
        if not r:
            return None
        r.raise_for_status()
        daily = r.json().get("daily", {})

        # Resolve correct day_index from API's local-time indexed array
        daily_times = daily.get("time", [])
        day_index = 0
        if daily_times and target_date_str in daily_times:
            day_index = daily_times.index(target_date_str)

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
        logger.warning(f"Ensemble error {city}: {e}")
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
        r = _request_with_retry(
            f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}",
            timeout=10,
            headers={"User-Agent": "PolymarketWeatherBot/GridEdition"}
        )
        if not r:
            return None
        r.raise_for_status()
        forecast_url = r.json()["properties"]["forecast"]

        r2 = _request_with_retry(
            forecast_url,
            timeout=10,
            headers={"User-Agent": "PolymarketWeatherBot/GridEdition"}
        )
        if not r2:
            return None
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
        logger.warning(f"NOAA error {city}: {e}")
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
        
        r = _request_with_retry(
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
        logger.warning(f"NASA POWER error {city}: {e}")
        return None


    # NOTE: fetch_open_meteo() видалено в v17 — використовувалась тільки для gfs/ecmwf, які мали 100% 429.


# ─────────────────────────────────────────────
# 5. METAR (REAL-TIME AIRPORT OBSERVATION) — ПРІОРИТЕТ ДЛЯ БЛИЗЬКИХ РИНКІВ
# ─────────────────────────────────────────────

# Розширений словник CITY -> ICAO (ключові аеропорти для ColdMath)
CITY_TO_ICAO: Dict[str, str] = {
    # USA
    "NYC": "KLGA", "New York": "KLGA",
    "Chicago": "KORD",
    "Los Angeles": "KLAX", "LA": "KLAX", "Los Angeles CA": "KLAX",
    "San Francisco": "KSFO",
    "Miami": "KMIA",
    "Dallas": "KDAL",
    "Seattle": "KSEA",
    "Boston": "KBOS",
    "Denver": "KBKF",
    "Atlanta": "KATL",
    "Houston": "KHOU",
    "Phoenix": "KPHX",
    "Las Vegas": "KLAS",
    "Austin": "KAUS",
    "Minneapolis": "KMSP",
    "Portland": "KPDX",
    "Nashville": "KBNA",
    "Charlotte": "KCLT",
    "Orlando": "KMCO",
    # Europe
    "London": "EGLC", "London City": "EGLC",
    "Paris": "LFPB", "Paris Le Bourget": "LFPB",
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
    "Shanghai": "ZSPD", "Shanghai Pudong": "ZSPD",
    "Beijing": "ZBAA", "Beijing Capital": "ZBAA",
    "Chengdu": "ZUUU", "Chengdu Shuangliu": "ZUUU",
    "Hong Kong": "VHHH", "Hong Kong International": "VHHH",
    "Singapore": "WSSS", "Singapore Changi": "WSSS",
    "Bangkok": "VTBS", "Bangkok Suvarnabhumi": "VTBS",
    "Dubai": "OMDB", "Dubai International": "OMDB",
    "Jeddah": "OEJN", "Jeddah King Abdulaziz": "OEJN",
    "Ankara": "LTAC", "Ankara Esenboga": "LTAC",
    "Lucknow": "VILK", "Lucknow Amausi": "VILK",
    "Karachi": "OPKC", "Karachi Jinnah": "OPKC",
    "Moscow": "UUWW", "Moscow Vnukovo": "UUWW",
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
        r = _request_with_retry(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": icao, "format": "json", "hours": 1},
            timeout=8,
            headers={"User-Agent": "PolymarketWeatherBot/ColdMath"}
        )
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "json" not in ctype and not r.text.strip().startswith(("[", "{")):
            logger.debug(f"METAR {city} ({icao}): не-JSON відповідь (content-type={ctype[:30]})")
            return None
        try:
            data = r.json()
        except Exception:
            logger.debug(f"METAR {city} ({icao}): r.json() не зміг розпарсити")
            return None
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
        logger.debug(f"METAR {city} ({icao}) skip: {e}")
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

        today_str = times[-1][:10] if times else None
        if not today_str:
            return None

        today_temps = []
        for t_str, temp in zip(times, temps):
            try:
                if t_str[:10] == today_str and temp is not None:
                    today_temps.append(temp)
            except:
                pass

        if len(today_temps) < 2:
            return None

        result = (min(today_temps), max(today_temps))
        _cache_set(key, result)
        logger.debug(f"📊 Observed today {city}: min={result[0]:.1f}°C, max={result[1]:.1f}°C ({len(today_temps)} hours)")
        return result
    except Exception:
        logger.debug(f"Observed today error {city}: single-request failed (likely 429, expected on Render free tier)")
        return None


# ─────────────────────────────────────────────
# 6. CONSENSUS (ЗЛИТТЯ ПРОГНОЗІВ З ПРІОРИТЕТОМ METAR ДЛЯ БЛИЗЬКИХ ГОДИН)
# ─────────────────────────────────────────────

def test_all_apis() -> Dict[str, bool]:
    """Тестує всі API джерела при старті, повертає статус кожного."""
    test_city = "London"
    results = {}
    for name, func in [
        ("ENSEMBLE", lambda: fetch_open_meteo_ensemble(test_city, 12.0)),
        ("Open-Meteo/forecast", lambda: requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": 51.5, "longitude": -0.13, "daily": "temperature_2m_max", "timezone": "auto", "forecast_days": 1}, timeout=10)),
        ("METAR/aviationweather", lambda: _request_with_retry("https://aviationweather.gov/api/data/metar", params={"ids": "EGLC", "format": "json", "hours": 1})),
        ("Historical/archive", lambda: _request_with_retry("https://archive-api.open-meteo.com/v1/archive", params={"latitude": 51.5, "longitude": -0.13, "start_date": "2026-07-02", "end_date": "2026-07-02", "daily": "temperature_2m_max", "timezone": "auto"})),
    ]:
        try:
            r = func()
            if isinstance(r, WeatherForecast):
                results[name] = True
                logger.info(f"✅ API test {name}: OK (temp={r.temp_high_c:.1f}°C)")
            elif r is not None:
                results[name] = True
                logger.info(f"✅ API test {name}: OK (HTTP {r.status_code})")
            else:
                results[name] = False
                logger.warning(f"❌ API test {name}: повернув None")
        except Exception as e:
            results[name] = False
            logger.warning(f"❌ API test {name}: {e}")
    return results


def get_best_forecast(city: str, hours_to_resolution: float = 24.0, target_date: Optional[datetime.date] = None) -> Optional[WeatherForecast]:
    """
    Повертає консенсусний прогноз для заданого міста.
    v15: METAR = основа стратегії (60% вага ≤8h, 40% ≤12h).
    """
    # Бакет hours для кешу: ≤12, ≤24, >24
    _h_bucket = "short" if hours_to_resolution <= 12 else ("mid" if hours_to_resolution <= 24 else "long")
    key = f"consensus_{city}_{_h_bucket}_{target_date or ''}"
    cached = _cache_get(key)
    if cached:
        return cached

    forecasts_w = []

    # v16: METAR fetching for confirmation only (not for forecast average)
    # METAR temp_high_c is CURRENT hourly temp, not daily high.
    # Mixing it into the daily-high weighted average was pulling forecasts
    # down toward morning temps (e.g., 24°C → 21.4°C), destroying all edge.
    metar_fc = fetch_metar(city)

    fc_ensemble = fetch_open_meteo_ensemble(city, hours_to_resolution, target_date)
    fc_noaa = fetch_noaa_forecast(city, hours_to_resolution, target_date)
    fc_nasa = fetch_nasa_power(city, hours_to_resolution)

    # v17: Pure ENSEMBLE + NOAA — GFS/ECMWF removed (100% 429 on api.open-meteo.com)
    if hours_to_resolution <= 8.0:
        if fc_ensemble:
            forecasts_w.append((fc_ensemble, 0.75))
        if fc_noaa:
            forecasts_w.append((fc_noaa, 0.25))
    elif hours_to_resolution <= 12.0:
        if fc_ensemble:
            forecasts_w.append((fc_ensemble, 0.70))
        if fc_noaa:
            forecasts_w.append((fc_noaa, 0.30))
    else:
        if fc_ensemble:
            forecasts_w.append((fc_ensemble, 0.60))
        if fc_noaa:
            forecasts_w.append((fc_noaa, 0.35))
        if fc_nasa:
            forecasts_w.append((fc_nasa, 0.05))

    # Якщо після зважування немає жодного прогнозу — повертаємо ансамбль або None
    if not forecasts_w:
        if fc_ensemble:
            _cache_set(key, fc_ensemble)
            return fc_ensemble
        logger.debug(f"Немає прогнозу для {city}")
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
        temp_high_c=round(wavg("temp_high_c") + _get_season_bias_c(city), 1),
        temp_low_c=round(wavg("temp_low_c"), 1),
        prob_rain=round(wavg("prob_rain"), 3),
        prob_snow=round(wavg("prob_snow"), 3),
        sources_used=all_sources,
    )

    # v15: Ensemble members передаємо лише якщо METAR вага < 0.5
    _metar_weight = next(
        (w for f, w in forecasts_w if "METAR" in f.sources_used), 0.0
    )
    if fc_ensemble and _metar_weight < 0.5:
        result.temp_high_members = fc_ensemble.temp_high_members
        result.temp_low_members = fc_ensemble.temp_low_members

    # v15: observed = жорсткий floor/ceiling (фізичне обмеження)
    if hours_to_resolution <= 12.0:
        observed = _fetch_observed_daily_extremes(city)
        if observed:
            obs_low, obs_high = observed
            result.temp_high_c = max(result.temp_high_c, obs_high)
            result.temp_low_c = min(result.temp_low_c, obs_low)

            if "OBSERVED" not in result.sources_used:
                result.sources_used.append("OBSERVED")

        # v15: METAR вже в зваженому середній (вага 40-60%).
        # НЕ додаємо окрему 10% корекцію — це вже враховано у зважуванні вище.
        if metar_fc and "METAR" not in result.sources_used:
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
        r = _request_with_retry(
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
            # Fallback 1: forecast API з past_days (дає recent спостереження)
            from datetime import date as _date
            today = _date.today()
            try:
                target = _date.fromisoformat(date_str)
                days_ago = (today - target).days
            except (ValueError, TypeError):
                days_ago = 1
            if 0 < days_ago <= 5:
                r = _request_with_retry(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "past_days": days_ago,
                        "daily": "temperature_2m_max,temperature_2m_min",
                        "timezone": "auto",
                    },
                    timeout=10,
                )
            else:
                # Fallback 2: forecast API з start_date/end_date (короткий архів)
                r = _request_with_retry(
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
            else:
                logger.warning(f"📊 Historical {city} on {date_str}: API returned null values (t_max={t_max}, t_min={t_min})")
        else:
            logger.warning(f"📊 Historical {city} on {date_str}: API returned empty arrays (t_max={len(t_max_vals)}, t_min={len(t_min_vals)})")
    except Exception as e:
        logger.warning(f"Historical data error {city} on {date_str}: {e}")

    logger.debug(f"Historical data unavailable: {city} on {date_str}")
    return None
