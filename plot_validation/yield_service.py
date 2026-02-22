"""
yield_service.py — Yield Feasibility Module.

Compares *actual* weather at the plot location (from Open-Meteo API)
against ideal growing conditions for the claimed crop.
The comparison is parameter-by-parameter: temperature, rainfall, humidity,
soil moisture.

Also provides crop *recommendations* by scoring all known crops against
actual weather conditions at the plot location.

Includes unsuitability detection — if a crop is a poor fit, specific
reasons are provided (e.g. "Rainfall too low for Rice").

Data sources:
    - Crop dataset: curated from ICAR / FAO / KAU guidelines
    - Weather:      Open-Meteo Historical Weather API (free, no key)
    - Soil moisture: Open-Meteo (soil_moisture_0_to_7cm, m³/m³)
    - Vegetation:   Mean NDVI from Sentinel-2 (passed in from EE pipeline)
"""

import logging
import requests as http_requests
from datetime import date, timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# CONFIGURABLE — Change this to adjust weather history window.
# 90 = last 3 months. Use 30 for 1 month, 180 for 6 months, etc.
# ──────────────────────────────────────────────────────────────
WEATHER_LOOKBACK_DAYS = 90


# ──────────────────────────────────────────────────────────────
# STEP 1 — Crop ideal-conditions dataset (KERALA REGION)
#
# Sources:
#   - Kerala Dept. of Agriculture statistics (2023-24)
#   - Kerala Agricultural University (KAU) recommendations
#   - CPCRI / Spices Board of India / ICAR-IISR guidelines
#   - FAO Ecocrop database
#
# Rainfall ranges are PER GROWING SEASON (season_start → season_end).
# The system fetches weather from the most recent occurrence of that
# season, not just the last 90 days.
# ──────────────────────────────────────────────────────────────

@dataclass
class CropProfile:
    """Ideal growing conditions for a crop."""
    name: str
    baseline_yield: float       # tons / hectare (Kerala state avg)
    temp_min_c: float           # minimum optimal temperature
    temp_max_c: float           # maximum optimal temperature
    rainfall_min_mm: float      # minimum season rainfall (mm)
    rainfall_max_mm: float      # maximum season rainfall (mm)
    humidity_min_pct: float     # minimum relative humidity %
    humidity_max_pct: float     # maximum relative humidity %
    soil_min: float = 0.15      # min ideal soil moisture (m³/m³)
    soil_max: float = 0.40      # max ideal soil moisture (m³/m³)
    season_start: int = 1       # growing season start month (1=Jan)
    season_end: int = 12        # growing season end month (12=Dec)


# Unsuitability threshold — below this overall score, crop is "Not Recommended"
UNSUITABILITY_THRESHOLD = 0.40


# fmt: off
CROP_DATABASE: dict[str, CropProfile] = {
    # ─── FOOD CROPS ──────────────────────────────────────────
    # Rice: Kerala avg 2.96 t/ha (2023-24), Virippu season (Jun-Oct, monsoon)
    #       Paddy needs waterlogged soil → high soil moisture (0.30–0.50)
    #       Rainfall ranges are PER GROWING SEASON (not annual)
    "rice":       CropProfile("Rice",        2.96, 20, 35,  1500, 3000,  70, 90, 0.30, 0.50, 6, 10),
    # Tapioca (Cassava): Major staple in Kerala, ~25 t/ha
    #       Tolerates moderate moisture, dislikes waterlogging
    "tapioca":    CropProfile("Tapioca",    25.0,  25, 30,  1000, 2000,  60, 85, 0.15, 0.35, 4, 10),
    # Banana: Nendran, Palayamkodan, Robusta varieties, ~18 t/ha in Kerala
    "banana":     CropProfile("Banana",     18.0,  15, 35,  1200, 2500,  65, 90, 0.20, 0.40, 1, 12),
    # Maize: limited but grown in Palakkad belt
    "maize":      CropProfile("Maize",       2.5,  18, 27,   500, 1000,  55, 80, 0.15, 0.35, 6, 9),

    # ─── PLANTATION CROPS ────────────────────────────────────
    # Coconut: Kerala ~7,215 nuts/ha, ~6 t copra/ha
    "coconut":    CropProfile("Coconut",     6.0,  27, 32,  1500, 2500,  80, 90, 0.20, 0.40, 1, 12),
    # Rubber: Kerala avg ~1.63 t/ha (dry rubber)
    "rubber":     CropProfile("Rubber",      1.63, 25, 34,  2000, 4000,  75, 95, 0.20, 0.45, 6, 11),
    # Tea: Munnar/Wayanad belt, ~2 t/ha made tea
    "tea":        CropProfile("Tea",         2.0,  13, 30,  1500, 3000,  70, 90, 0.25, 0.45, 1, 12),
    # Coffee: Wayanad/Idukki, robusta dominant, ~1.05 t/ha
    "coffee":     CropProfile("Coffee",      1.05, 20, 30,  1500, 2500,  70, 90, 0.20, 0.40, 6, 10),
    # Arecanut: ~1.5 t dry kernel/ha
    "arecanut":   CropProfile("Arecanut",    1.5,  14, 36,  1500, 5000,  70, 90, 0.20, 0.40, 1, 12),
    # Cashew: Kerala avg, ~0.8 t/ha
    "cashew":     CropProfile("Cashew",      0.8,  20, 35,  1000, 2000,  60, 80, 0.10, 0.30, 1, 12),

    # ─── SPICE CROPS (Kerala = Spice Garden of India) ────────
    # Black Pepper: ~0.4 t/ha (dried), Panniyur varieties
    "pepper":     CropProfile("Pepper",      0.40, 20, 30,  2000, 3000,  75, 90, 0.25, 0.45, 6, 12),
    # Cardamom: Idukki/Wayanad hills, ~0.2 t/ha
    "cardamom":   CropProfile("Cardamom",    0.20, 15, 25,  1500, 4000,  75, 90, 0.30, 0.50, 6, 11),
    # Ginger: Wayanad/Idukki, ~20 t/ha fresh (~4 t/ha dry)
    "ginger":     CropProfile("Ginger",     20.0,  19, 30,  1500, 3000,  70, 90, 0.25, 0.40, 4, 12),
    # Turmeric: ~25 t/ha fresh (~5 t/ha dry)
    "turmeric":   CropProfile("Turmeric",   25.0,  20, 35,  1500, 2500,  70, 90, 0.25, 0.40, 5, 12),
    # Nutmeg: Kerala = largest producer in India, ~0.35 t/ha dried
    "nutmeg":     CropProfile("Nutmeg",      0.35, 20, 30,  1500, 2500,  75, 90, 0.25, 0.45, 1, 12),
    # Clove: Nilambur/Kottayam belt, ~0.25 t/ha dried
    "clove":      CropProfile("Clove",       0.25, 20, 30,  1500, 2500,  75, 90, 0.25, 0.45, 1, 12),
    # Vanilla: ~0.3 t/ha cured beans (inter-cropped)
    "vanilla":    CropProfile("Vanilla",     0.30, 21, 32,  1500, 3000,  75, 90, 0.25, 0.40, 1, 12),
    # Cinnamon: Kerala/Karnataka, ~0.4 t/ha dried bark
    "cinnamon":   CropProfile("Cinnamon",    0.40, 20, 30,  1500, 2500,  75, 90, 0.25, 0.40, 1, 12),

    # ─── VEGETABLES & OTHERS ─────────────────────────────────
    # Sugarcane: limited in Kerala (Palakkad), ~55 t/ha
    "sugarcane":  CropProfile("Sugarcane",  55.0,  20, 35,  1500, 2500,  70, 85, 0.25, 0.45, 1, 12),
    # Groundnut: Palakkad dry belt
    "groundnut":  CropProfile("Groundnut",   1.3,  25, 30,   500, 1000,  50, 70, 0.10, 0.25, 6, 10),
}
# fmt: on

DEFAULT_PROFILE = CropProfile("Unknown", 2.5, 22, 32, 250, 575, 60, 85)


# ──────────────────────────────────────────────────────────────
# STEP 2 — Pull actual weather from Open-Meteo (free, no key)
# ──────────────────────────────────────────────────────────────

def fetch_weather_last_3_months(lat: float, lon: float) -> dict:
    """
    Fetch daily weather + soil moisture for the last 3 months from Open-Meteo.

    Returns:
        {
            "avg_temp_c": float,
            "total_rainfall_mm": float,
            "avg_humidity_pct": float,
            "avg_soil_moisture": float,  # m³/m³ (volumetric)
            "days_sampled": int,
        }
    """
    end = date.today() - timedelta(days=1)          # yesterday (latest available)
    start = end - timedelta(days=WEATHER_LOOKBACK_DAYS)  # configurable lookback

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean,soil_moisture_0_to_7cm_mean",
        "timezone": "auto",
    }

    logger.info("Fetching weather: %s → %s for (%.4f, %.4f)", start, end, lat, lon)
    resp = http_requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    temps = [t for t in (daily.get("temperature_2m_mean") or []) if t is not None]
    rains = [r for r in (daily.get("precipitation_sum") or []) if r is not None]
    humids = [h for h in (daily.get("relative_humidity_2m_mean") or []) if h is not None]
    soils = [s for s in (daily.get("soil_moisture_0_to_7cm_mean") or []) if s is not None]

    weather = {
        "avg_temp_c": round(sum(temps) / len(temps), 1) if temps else 0.0,
        "total_rainfall_mm": round(sum(rains), 1) if rains else 0.0,
        "avg_humidity_pct": round(sum(humids) / len(humids), 1) if humids else 0.0,
        "avg_soil_moisture": round(sum(soils) / len(soils), 4) if soils else 0.0,
        "days_sampled": len(temps),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }
    logger.info("Weather data: %s", weather)
    return weather


def fetch_weather_for_period(
    lat: float, lon: float,
    start_year: int, start_month: int,
    end_year: int, end_month: int,
) -> dict:
    """
    Fetch weather for the exact user-specified timeline (start_year/month → end_year/month).
    This is used when the user has explicitly chosen a date range in the UI.
    """
    import calendar
    period_start = date(start_year, start_month, 1)
    last_day = calendar.monthrange(end_year, end_month)[1]
    period_end = date(end_year, end_month, last_day)

    # Clamp end to yesterday (Open-Meteo archive has up to yesterday)
    yesterday = date.today() - timedelta(days=1)
    if period_end > yesterday:
        period_end = yesterday
    if period_start > period_end:
        # Fallback to last 90 days if the range is entirely in the future
        return fetch_weather_last_3_months(lat, lon)

    logger.info(
        "User-timeline weather fetch: %s → %s for (%.4f, %.4f)",
        period_start, period_end, lat, lon,
    )

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": period_start.isoformat(),
        "end_date": period_end.isoformat(),
        "daily": "temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean,soil_moisture_0_to_7cm_mean",
        "timezone": "auto",
    }

    resp = http_requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    temps = [t for t in (daily.get("temperature_2m_mean") or []) if t is not None]
    rains = [r for r in (daily.get("precipitation_sum") or []) if r is not None]
    humids = [h for h in (daily.get("relative_humidity_2m_mean") or []) if h is not None]
    soils = [s for s in (daily.get("soil_moisture_0_to_7cm_mean") or []) if s is not None]

    weather = {
        "avg_temp_c": round(sum(temps) / len(temps), 1) if temps else 0.0,
        "total_rainfall_mm": round(sum(rains), 1) if rains else 0.0,
        "avg_humidity_pct": round(sum(humids) / len(humids), 1) if humids else 0.0,
        "avg_soil_moisture": round(sum(soils) / len(soils), 4) if soils else 0.0,
        "days_sampled": len(temps),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "season_months": f"{start_month}-{end_month}",
    }
    logger.info("User-timeline weather: %s", weather)
    return weather


def fetch_weather_for_season(lat: float, lon: float, profile: "CropProfile") -> dict:
    """
    Fetch weather for a crop's growing season rather than simply the last 90 days.

    For seasonal crops (e.g. Rice: Jun-Oct), this function determines the most
    recent completed occurrence of that growing season and fetches weather data
    for exactly those months.  This ensures we don't evaluate monsoon-season crops
    against dry-season weather.

    For year-round crops (season_start=1, season_end=12) it falls back to the
    standard last-90-days fetch.
    """
    # Year-round crops → use normal lookback
    if profile.season_start == 1 and profile.season_end == 12:
        return fetch_weather_last_3_months(lat, lon)

    today = date.today()
    year = today.year

    # Build candidate season end date
    # If the season hasn't ended yet this year, use the previous year's season
    import calendar
    last_day = calendar.monthrange(year, profile.season_end)[1]
    season_end_date = date(year, profile.season_end, last_day)
    season_start_date = date(year, profile.season_start, 1)

    # Handle seasons that wrap across year boundary (e.g. Nov-Feb)
    if profile.season_start > profile.season_end:
        # Wrapping season (e.g. start=11, end=2)
        season_end_date = date(year, profile.season_end, last_day)
        season_start_date = date(year - 1, profile.season_start, 1)
        if today < season_end_date:
            # Current wrap hasn't completed, use previous year's
            season_end_date = date(year - 1, profile.season_end,
                                   calendar.monthrange(year - 1, profile.season_end)[1])
            season_start_date = date(year - 2, profile.season_start, 1)
    else:
        # Normal season (start <= end)
        if today < season_end_date:
            # This year's season hasn't finished, use last year's
            season_start_date = date(year - 1, profile.season_start, 1)
            last_day_prev = calendar.monthrange(year - 1, profile.season_end)[1]
            season_end_date = date(year - 1, profile.season_end, last_day_prev)

    logger.info(
        "Season-aware fetch for %s: %s → %s (season months %d-%d)",
        profile.name, season_start_date, season_end_date,
        profile.season_start, profile.season_end,
    )

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": season_start_date.isoformat(),
        "end_date": season_end_date.isoformat(),
        "daily": "temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean,soil_moisture_0_to_7cm_mean",
        "timezone": "auto",
    }

    resp = http_requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    temps = [t for t in (daily.get("temperature_2m_mean") or []) if t is not None]
    rains = [r for r in (daily.get("precipitation_sum") or []) if r is not None]
    humids = [h for h in (daily.get("relative_humidity_2m_mean") or []) if h is not None]
    soils = [s for s in (daily.get("soil_moisture_0_to_7cm_mean") or []) if s is not None]

    weather = {
        "avg_temp_c": round(sum(temps) / len(temps), 1) if temps else 0.0,
        "total_rainfall_mm": round(sum(rains), 1) if rains else 0.0,
        "avg_humidity_pct": round(sum(humids) / len(humids), 1) if humids else 0.0,
        "avg_soil_moisture": round(sum(soils) / len(soils), 4) if soils else 0.0,
        "days_sampled": len(temps),
        "period_start": season_start_date.isoformat(),
        "period_end": season_end_date.isoformat(),
        "season_months": f"{profile.season_start}-{profile.season_end}",
    }
    logger.info("Season weather for %s: %s", profile.name, weather)
    return weather


# ──────────────────────────────────────────────────────────────
# STEP 3 — Compare actual vs ideal (parameter-by-parameter)
# ──────────────────────────────────────────────────────────────

def _range_score(actual: float, ideal_min: float, ideal_max: float) -> float:
    """
    Score how well `actual` fits within [ideal_min, ideal_max].

    Returns:
        1.0  → perfectly inside the range
        0.0  → very far outside the range
        Linear degradation outside the range (50% of range width = 0 score).
    """
    if ideal_min <= actual <= ideal_max:
        return 1.0

    range_width = ideal_max - ideal_min
    # Allow graceful degradation: 50% beyond the range = score 0
    margin = max(range_width * 0.5, 5.0)

    if actual < ideal_min:
        return max(0.0, 1.0 - (ideal_min - actual) / margin)
    else:
        return max(0.0, 1.0 - (actual - ideal_max) / margin)


def _vegetation_score(mean_ndvi: float) -> float:
    """Map mean NDVI to a 0–1 vegetation health score."""
    if mean_ndvi >= 0.65:
        return 1.0
    elif mean_ndvi >= 0.5:
        return 0.7
    elif mean_ndvi >= 0.3:
        return 0.4
    else:
        return 0.1


def _soil_score(actual: float, ideal_min: float, ideal_max: float) -> float:
    """
    Score soil moisture fit. Same logic as _range_score but with tighter
    margins since soil moisture values are in a narrow 0–0.5 range.
    """
    if actual == 0.0:
        return 0.5   # No data — neutral score
    if ideal_min <= actual <= ideal_max:
        return 1.0
    range_width = ideal_max - ideal_min
    margin = max(range_width * 0.5, 0.05)
    if actual < ideal_min:
        return max(0.0, 1.0 - (ideal_min - actual) / margin)
    else:
        return max(0.0, 1.0 - (actual - ideal_max) / margin)


def compare_conditions(
    profile: CropProfile,
    weather: dict,
    mean_ndvi: float,
) -> dict:
    """
    Compare actual weather + NDVI + soil moisture against the crop's ideal conditions.

    Returns per-parameter scores and an overall suitability score.
    """
    temp_score = _range_score(
        weather["avg_temp_c"], profile.temp_min_c, profile.temp_max_c,
    )
    rain_score = _range_score(
        weather["total_rainfall_mm"], profile.rainfall_min_mm, profile.rainfall_max_mm,
    )
    humidity_score = _range_score(
        weather["avg_humidity_pct"], profile.humidity_min_pct, profile.humidity_max_pct,
    )
    soil_sc = _soil_score(
        weather.get("avg_soil_moisture", 0.0), profile.soil_min, profile.soil_max,
    )
    veg_score = _vegetation_score(mean_ndvi)

    # Weighted overall: temp 25%, rain 25%, humidity 10%, soil 15%, vegetation 25%
    overall = (
        0.25 * temp_score
        + 0.25 * rain_score
        + 0.10 * humidity_score
        + 0.15 * soil_sc
        + 0.25 * veg_score
    )

    return {
        "temp_score": round(temp_score, 2),
        "rain_score": round(rain_score, 2),
        "humidity_score": round(humidity_score, 2),
        "soil_score": round(soil_sc, 2),
        "vegetation_score": round(veg_score, 2),
        "overall_score": round(min(1.0, max(0.0, overall)), 4),
    }


# ──────────────────────────────────────────────────────────────
# STEP 4 — Unsuitability reason generation
# ──────────────────────────────────────────────────────────────

def _generate_unsuitability_reasons(
    profile: CropProfile,
    weather: dict,
    scores: dict,
) -> list[dict]:
    """
    Generate human-readable reasons why a crop is poorly suited.

    Returns list of {"param": str, "icon": str, "reason": str, "score": float}
    Only includes parameters scoring below 0.5.
    """
    reasons = []

    if scores["temp_score"] < 0.5:
        actual = weather["avg_temp_c"]
        ideal = f"{profile.temp_min_c}–{profile.temp_max_c}°C"
        direction = "too cold" if actual < profile.temp_min_c else "too hot"
        reasons.append({
            "param": "Temperature",
            "icon": "🌡️",
            "reason": f"Temperature {direction} for {profile.name} — needs {ideal}, got {actual}°C",
            "score": scores["temp_score"],
        })

    if scores["rain_score"] < 0.5:
        actual = weather["total_rainfall_mm"]
        ideal = f"{profile.rainfall_min_mm}–{profile.rainfall_max_mm}mm"
        direction = "too low" if actual < profile.rainfall_min_mm else "too high"
        reasons.append({
            "param": "Rainfall",
            "icon": "🌧️",
            "reason": f"Rainfall {direction} for {profile.name} — needs {ideal}, got {actual:.0f}mm",
            "score": scores["rain_score"],
        })

    if scores["humidity_score"] < 0.5:
        actual = weather["avg_humidity_pct"]
        ideal = f"{profile.humidity_min_pct}–{profile.humidity_max_pct}%"
        direction = "too dry" if actual < profile.humidity_min_pct else "too humid"
        reasons.append({
            "param": "Humidity",
            "icon": "💧",
            "reason": f"Humidity {direction} for {profile.name} — needs {ideal}, got {actual:.0f}%",
            "score": scores["humidity_score"],
        })

    if scores["soil_score"] < 0.5 and weather.get("avg_soil_moisture", 0) > 0:
        actual = weather["avg_soil_moisture"]
        ideal = f"{profile.soil_min}–{profile.soil_max} m³/m³"
        direction = "too dry" if actual < profile.soil_min else "too wet"
        reasons.append({
            "param": "Soil Moisture",
            "icon": "🏜️",
            "reason": f"Soil {direction} for {profile.name} — needs {ideal}, got {actual:.3f} m³/m³",
            "score": scores["soil_score"],
        })

    if scores["vegetation_score"] < 0.4:
        reasons.append({
            "param": "Vegetation",
            "icon": "🌿",
            "reason": f"Low vegetation health — NDVI indicates poor growing conditions",
            "score": scores["vegetation_score"],
        })

    return reasons


def _build_yield_warning(scores: dict, reasons: list[dict], profile_name: str) -> dict:
    """
    Determine the yield warning level based on scores.

    Returns:
        {
            "is_unsuitable": bool,         # overall < 40%
            "has_critical_failure": bool,   # ANY param at 0% or near-zero
            "yield_warning": str,           # human-readable warning message
        }
    """
    overall = scores["overall_score"]

    # Check if any individual parameter is critically low (≤ 5%)
    critical_params = []
    param_labels = {
        "temp_score": "Temperature",
        "rain_score": "Rainfall",
        "humidity_score": "Humidity",
        "soil_score": "Soil Moisture",
    }
    for key, label in param_labels.items():
        if scores.get(key, 1.0) <= 0.05:
            critical_params.append(label)

    has_critical = len(critical_params) > 0
    is_unsuitable = overall < UNSUITABILITY_THRESHOLD

    if is_unsuitable:
        warning = f"🚫 {profile_name} is NOT RECOMMENDED for this region — overall suitability only {overall*100:.0f}%"
    elif has_critical:
        warning = f"⚠️ {profile_name} will have POOR YIELD here — {', '.join(critical_params)} critically low"
    else:
        warning = ""

    return {
        "is_unsuitable": is_unsuitable,
        "has_critical_failure": has_critical,
        "yield_warning": warning,
    }


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────

def estimate_yield(
    claimed_crop: str,
    mean_ndvi: float,
    lat: float,
    lon: float,
    plot_area_hectares: float,
    start_year: int | None = None,
    start_month: int | None = None,
    end_year: int | None = None,
    end_month: int | None = None,
) -> dict:
    """
    Full yield feasibility pipeline:
      1. Look up crop ideal conditions
      2. Fetch actual weather from Open-Meteo for the user's chosen timeline
         (falls back to crop-season / last-90-days if no timeline given)
      3. Compare actual vs ideal per parameter
      4. Estimate yield based on suitability

    Returns a dict ready for the API response.
    """
    crop_key = claimed_crop.strip().lower()
    profile = CROP_DATABASE.get(crop_key, DEFAULT_PROFILE)

    # Fetch real weather — use user's chosen timeline if provided,
    # otherwise fall back to season-aware fetch
    if start_year and start_month and end_year and end_month:
        weather = fetch_weather_for_period(
            lat, lon, start_year, start_month, end_year, end_month,
        )
    else:
        weather = fetch_weather_for_season(lat, lon, profile)

    # Compare
    scores = compare_conditions(profile, weather, mean_ndvi)

    # Estimated yield = baseline × overall suitability
    estimated_yield = round(profile.baseline_yield * scores["overall_score"], 2)
    total_yield = round(estimated_yield * plot_area_hectares, 2)

    # Confidence label
    overall = scores["overall_score"]
    if overall >= 0.7:
        confidence = "HIGH"
    elif overall >= 0.4:
        confidence = "MODERATE"
    else:
        confidence = "LOW"

    logger.info(
        "Yield: crop=%s overall=%.2f yield=%.2f t/ha confidence=%s",
        crop_key, overall, estimated_yield, confidence,
    )

    # Unsuitability & critical-failure warnings
    warn = _build_yield_warning(scores, _generate_unsuitability_reasons(profile, weather, scores), profile.name)

    return {
        "claimed_crop": profile.name,
        "baseline_yield": profile.baseline_yield,
        "estimated_yield_ton_per_hectare": estimated_yield,
        "total_estimated_yield_tons": total_yield,
        "yield_feasibility_score": scores["overall_score"],
        "yield_confidence": confidence,
        # Unsuitability warnings
        "is_unsuitable": warn["is_unsuitable"],
        "has_critical_failure": warn["has_critical_failure"],
        "yield_warning": warn["yield_warning"],
        "unsuitability_reasons": _generate_unsuitability_reasons(profile, weather, scores),
        # Per-parameter comparison
        "weather_actual": {
            "avg_temp_c": weather["avg_temp_c"],
            "total_rainfall_mm": weather["total_rainfall_mm"],
            "avg_humidity_pct": weather["avg_humidity_pct"],
            "avg_soil_moisture": weather.get("avg_soil_moisture", 0.0),
            "period": f"{weather['period_start']} → {weather['period_end']}",
            "days_sampled": weather["days_sampled"],
            "season_months": weather.get("season_months", ""),
        },
        "crop_ideal": {
            "temp_range_c": f"{profile.temp_min_c}–{profile.temp_max_c}",
            "rainfall_range_mm": f"{profile.rainfall_min_mm}–{profile.rainfall_max_mm}",
            "humidity_range_pct": f"{profile.humidity_min_pct}–{profile.humidity_max_pct}",
            "soil_moisture_range": f"{profile.soil_min}–{profile.soil_max}",
        },
        "parameter_scores": {
            "temperature": scores["temp_score"],
            "rainfall": scores["rain_score"],
            "humidity": scores["humidity_score"],
            "soil_moisture": scores["soil_score"],
            "vegetation": scores["vegetation_score"],
        },
    }


def integrate_yield_score(previous_score: float, yield_feasibility_score: float) -> float:
    """
    Blend yield feasibility into the overall confidence score.
    overall = 0.8 * previous_score + 0.2 * yield_feasibility_score
    """
    overall = 0.8 * previous_score + 0.2 * yield_feasibility_score
    return round(min(1.0, max(0.0, overall)), 4)


# ──────────────────────────────────────────────────────────────
# Crop Recommendation (ranks ALL crops against actual weather)
# ──────────────────────────────────────────────────────────────

def recommend_crops(
    lat: float,
    lon: float,
    mean_ndvi: float,
    top_n: int = 5,
) -> list[dict]:
    """
    Rank all crops by suitability for this location's recent weather.

    1. Fetches weather for the last WEATHER_LOOKBACK_DAYS days.
    2. Scores every crop in CROP_DATABASE using the same _range_score logic.
    3. Returns the top_n crops sorted by overall suitability (descending).

    Each item in the returned list:
        {
            "rank": 1,
            "crop": "Rice",
            "suitability_pct": 82,
            "temp_score": 1.0,
            "rain_score": 0.6,
            "humidity_score": 0.9,
            "vegetation_score": 1.0,
            "baseline_yield": 3.5,
        }
    """
    weather = fetch_weather_last_3_months(lat, lon)

    # ── Fetch one full year of daily data for season-slicing ──
    # Instead of 20 API calls (one per crop), we fetch 365 days once
    # and slice locally by each crop's season months.
    full_year_daily = None
    try:
        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=365)
        url = "https://archive-api.open-meteo.com/v1/archive"
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": "temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean,soil_moisture_0_to_7cm_mean",
            "timezone": "auto",
        }
        resp = http_requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        year_data = resp.json().get("daily", {})
        dates = year_data.get("time", [])
        full_year_daily = {
            "dates": dates,
            "temps": year_data.get("temperature_2m_mean", []),
            "rains": year_data.get("precipitation_sum", []),
            "humids": year_data.get("relative_humidity_2m_mean", []),
            "soils": year_data.get("soil_moisture_0_to_7cm_mean", []),
        }
        logger.info("Fetched %d days of full-year data for recommendations", len(dates))
    except Exception as e:
        logger.warning("Full-year fetch failed, using last-90-days for all: %s", e)

    def _slice_season(profile: CropProfile) -> dict:
        """Slice the full-year daily data to this crop's growing season."""
        if full_year_daily is None:
            return weather  # fallback

        # Year-round crops → use all data (same as last 90 days)
        if profile.season_start == 1 and profile.season_end == 12:
            return weather

        dates = full_year_daily["dates"]
        temps, rains, humids, soils = [], [], [], []

        for i, d_str in enumerate(dates):
            try:
                month = int(d_str[5:7])  # "2025-06-15" → 6
            except (ValueError, IndexError):
                continue

            # Check if this day falls within the crop's season
            if profile.season_start <= profile.season_end:
                in_season = profile.season_start <= month <= profile.season_end
            else:
                # Wrapping season (e.g. Nov-Feb)
                in_season = month >= profile.season_start or month <= profile.season_end

            if in_season:
                t = full_year_daily["temps"][i] if i < len(full_year_daily["temps"]) else None
                r = full_year_daily["rains"][i] if i < len(full_year_daily["rains"]) else None
                h = full_year_daily["humids"][i] if i < len(full_year_daily["humids"]) else None
                s = full_year_daily["soils"][i] if i < len(full_year_daily["soils"]) else None
                if t is not None: temps.append(t)
                if r is not None: rains.append(r)
                if h is not None: humids.append(h)
                if s is not None: soils.append(s)

        if not temps:
            return weather  # no data for this season

        return {
            "avg_temp_c": round(sum(temps) / len(temps), 1),
            "total_rainfall_mm": round(sum(rains), 1),
            "avg_humidity_pct": round(sum(humids) / len(humids), 1),
            "avg_soil_moisture": round(sum(soils) / len(soils), 4) if soils else 0.0,
            "days_sampled": len(temps),
            "period_start": "season",
            "period_end": "season",
        }

    scored: list[tuple[str, dict, float]] = []
    for _key, profile in CROP_DATABASE.items():
        crop_weather = _slice_season(profile)
        scores = compare_conditions(profile, crop_weather, mean_ndvi)
        reasons = _generate_unsuitability_reasons(profile, crop_weather, scores)
        scored.append((profile.name, profile, scores, reasons))

    # Sort descending by overall_score
    scored.sort(key=lambda x: x[2]["overall_score"], reverse=True)

    recommendations = []
    for rank, (name, profile, scores, reasons) in enumerate(scored[:top_n], start=1):
        warn = _build_yield_warning(scores, reasons, name)
        recommendations.append({
            "rank": rank,
            "crop": name,
            "suitability_pct": round(scores["overall_score"] * 100),
            "temp_score": scores["temp_score"],
            "rain_score": scores["rain_score"],
            "humidity_score": scores["humidity_score"],
            "soil_score": scores["soil_score"],
            "vegetation_score": scores["vegetation_score"],
            "baseline_yield": profile.baseline_yield,
            "is_unsuitable": warn["is_unsuitable"],
            "has_critical_failure": warn["has_critical_failure"],
            "yield_warning": warn["yield_warning"],
            "unsuitability_reasons": reasons,
        })

    logger.info(
        "Crop recommendations: %s",
        [(r["rank"], r["crop"], r["suitability_pct"]) for r in recommendations],
    )
    return recommendations
