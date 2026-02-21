"""
yield_service.py — Yield Feasibility Module.

Compares *actual* weather at the plot location (from Open-Meteo API)
against ideal growing conditions for the claimed crop.
The comparison is parameter-by-parameter: temperature, rainfall, humidity.

Data sources:
    - Crop dataset: curated from ICAR / FAO guidelines
    - Weather:      Open-Meteo Historical Weather API (free, no key)
    - Vegetation:   Mean NDVI from Sentinel-2 (passed in from EE pipeline)
"""

import logging
import requests as http_requests
from datetime import date, timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# STEP 1 — Crop ideal-conditions dataset
# Each crop has its optimal growing range for key parameters.
# Sources: ICAR handbooks, FAO Ecocrop, state agri-university data.
# ──────────────────────────────────────────────────────────────

@dataclass
class CropProfile:
    """Ideal growing conditions for a crop."""
    name: str
    baseline_yield: float       # tons / hectare (national median)
    temp_min_c: float           # minimum optimal temperature
    temp_max_c: float           # maximum optimal temperature
    rainfall_min_mm: float      # minimum 3-month rainfall (mm)
    rainfall_max_mm: float      # maximum 3-month rainfall (mm)
    humidity_min_pct: float     # minimum relative humidity %
    humidity_max_pct: float     # maximum relative humidity %


CROP_DATABASE: dict[str, CropProfile] = {
    "rice":       CropProfile("Rice",       3.5,  22, 32, 300, 700, 60, 90),
    "wheat":      CropProfile("Wheat",      3.2,  12, 25, 100, 300, 40, 70),
    "maize":      CropProfile("Maize",      2.8,  18, 30, 200, 500, 50, 80),
    "sugarcane":  CropProfile("Sugarcane",  70.0,  24, 38, 350, 700, 60, 85),
    "cotton":     CropProfile("Cotton",      1.8,  21, 35, 150, 400, 40, 70),
    "coconut":    CropProfile("Coconut",     6.0,  20, 32, 300, 600, 60, 90),
    "rubber":     CropProfile("Rubber",      1.5,  25, 35, 400, 750, 70, 95),
    "tea":        CropProfile("Tea",         2.0,  13, 28, 300, 600, 70, 90),
    "coffee":     CropProfile("Coffee",      1.0,  15, 28, 250, 500, 60, 85),
    "banana":     CropProfile("Banana",     30.0,  20, 35, 300, 600, 60, 90),
    "potato":     CropProfile("Potato",     22.0,  10, 24, 100, 350, 50, 80),
    "soybean":    CropProfile("Soybean",     1.2,  20, 30, 200, 500, 50, 80),
    "groundnut":  CropProfile("Groundnut",   1.5,  22, 33, 150, 400, 50, 75),
    "mustard":    CropProfile("Mustard",     1.1,  10, 25,  80, 250, 30, 65),
    "onion":      CropProfile("Onion",      17.0,  13, 30, 100, 350, 40, 70),
    "tomato":     CropProfile("Tomato",     25.0,  15, 30, 100, 350, 50, 75),
    "pepper":     CropProfile("Pepper",      0.5,  20, 30, 350, 700, 65, 95),
    "cardamom":   CropProfile("Cardamom",    0.2,  10, 25, 400, 800, 70, 95),
    "turmeric":   CropProfile("Turmeric",    5.0,  20, 35, 350, 600, 60, 85),
    "ginger":     CropProfile("Ginger",      4.0,  19, 30, 350, 600, 65, 90),
}

DEFAULT_PROFILE = CropProfile("Unknown", 2.5, 18, 32, 200, 500, 50, 80)


# ──────────────────────────────────────────────────────────────
# STEP 2 — Pull actual weather from Open-Meteo (free, no key)
# ──────────────────────────────────────────────────────────────

def fetch_weather_last_3_months(lat: float, lon: float) -> dict:
    """
    Fetch daily weather for the last 3 months from Open-Meteo.

    Returns:
        {
            "avg_temp_c": float,
            "total_rainfall_mm": float,
            "avg_humidity_pct": float,
            "days_sampled": int,
        }
    """
    end = date.today() - timedelta(days=1)   # yesterday (latest available)
    start = end - timedelta(days=90)          # ~3 months back

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean",
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

    weather = {
        "avg_temp_c": round(sum(temps) / len(temps), 1) if temps else 0.0,
        "total_rainfall_mm": round(sum(rains), 1) if rains else 0.0,
        "avg_humidity_pct": round(sum(humids) / len(humids), 1) if humids else 0.0,
        "days_sampled": len(temps),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }
    logger.info("Weather data: %s", weather)
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


def compare_conditions(
    profile: CropProfile,
    weather: dict,
    mean_ndvi: float,
) -> dict:
    """
    Compare actual weather + NDVI against the crop's ideal conditions.

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
    veg_score = _vegetation_score(mean_ndvi)

    # Weighted overall: temp 30%, rain 30%, humidity 15%, vegetation 25%
    overall = (
        0.30 * temp_score
        + 0.30 * rain_score
        + 0.15 * humidity_score
        + 0.25 * veg_score
    )

    return {
        "temp_score": round(temp_score, 2),
        "rain_score": round(rain_score, 2),
        "humidity_score": round(humidity_score, 2),
        "vegetation_score": round(veg_score, 2),
        "overall_score": round(min(1.0, max(0.0, overall)), 4),
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
) -> dict:
    """
    Full yield feasibility pipeline:
      1. Look up crop ideal conditions
      2. Fetch actual weather from Open-Meteo (last 3 months)
      3. Compare actual vs ideal per parameter
      4. Estimate yield based on suitability

    Returns a dict ready for the API response.
    """
    crop_key = claimed_crop.strip().lower()
    profile = CROP_DATABASE.get(crop_key, DEFAULT_PROFILE)

    # Fetch real weather
    weather = fetch_weather_last_3_months(lat, lon)

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

    return {
        "claimed_crop": profile.name,
        "baseline_yield": profile.baseline_yield,
        "estimated_yield_ton_per_hectare": estimated_yield,
        "total_estimated_yield_tons": total_yield,
        "yield_feasibility_score": scores["overall_score"],
        "yield_confidence": confidence,
        # Per-parameter comparison
        "weather_actual": {
            "avg_temp_c": weather["avg_temp_c"],
            "total_rainfall_mm": weather["total_rainfall_mm"],
            "avg_humidity_pct": weather["avg_humidity_pct"],
            "period": f"{weather['period_start']} → {weather['period_end']}",
            "days_sampled": weather["days_sampled"],
        },
        "crop_ideal": {
            "temp_range_c": f"{profile.temp_min_c}–{profile.temp_max_c}",
            "rainfall_range_mm": f"{profile.rainfall_min_mm}–{profile.rainfall_max_mm}",
            "humidity_range_pct": f"{profile.humidity_min_pct}–{profile.humidity_max_pct}",
        },
        "parameter_scores": {
            "temperature": scores["temp_score"],
            "rainfall": scores["rain_score"],
            "humidity": scores["humidity_score"],
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
