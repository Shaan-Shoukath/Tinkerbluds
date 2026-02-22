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
# Rainfall ranges are PER 3-MONTH PERIOD (annual ÷ 4)
# because we fetch the last ~90 days of weather data.
# ──────────────────────────────────────────────────────────────

@dataclass
class CropProfile:
    """Ideal growing conditions for a crop."""
    name: str
    baseline_yield: float       # tons / hectare (Kerala state avg)
    temp_min_c: float           # minimum optimal temperature
    temp_max_c: float           # maximum optimal temperature
    rainfall_min_mm: float      # minimum 3-month rainfall (mm)
    rainfall_max_mm: float      # maximum 3-month rainfall (mm)
    humidity_min_pct: float     # minimum relative humidity %
    humidity_max_pct: float     # maximum relative humidity %
    soil_min: float = 0.15      # min ideal soil moisture (m³/m³)
    soil_max: float = 0.40      # max ideal soil moisture (m³/m³)


# Unsuitability threshold — below this overall score, crop is "Not Recommended"
UNSUITABILITY_THRESHOLD = 0.40


# fmt: off
CROP_DATABASE: dict[str, CropProfile] = {
    # ─── FOOD CROPS ──────────────────────────────────────────
    # Rice: Kerala avg 2.96 t/ha (2023-24), 3 seasons: Virippu/Mundakan/Puncha
    #       Paddy needs waterlogged soil → high soil moisture (0.30–0.50)
    "rice":       CropProfile("Rice",        2.96, 20, 35,  1500, 3000,  70, 90, 0.30, 0.50),
    # Tapioca (Cassava): Major staple in Kerala, ~25 t/ha
    #       Tolerates moderate moisture, dislikes waterlogging
    "tapioca":    CropProfile("Tapioca",    25.0,  25, 30,  1000, 2000,  60, 85, 0.15, 0.35),
    # Banana: Nendran, Palayamkodan, Robusta varieties, ~18 t/ha in Kerala
    "banana":     CropProfile("Banana",     18.0,  15, 35,  1200, 2500,  65, 90, 0.20, 0.40),
    # Maize: limited but grown in Palakkad belt
    "maize":      CropProfile("Maize",       2.5,  18, 27,   500, 1000,  55, 80, 0.15, 0.35),

    # ─── PLANTATION CROPS ────────────────────────────────────
    # Coconut: Kerala ~7,215 nuts/ha, ~6 t copra/ha
    "coconut":    CropProfile("Coconut",     6.0,  27, 32,  1500, 2500,  80, 90, 0.20, 0.40),
    # Rubber: Kerala avg ~1.63 t/ha (dry rubber)
    "rubber":     CropProfile("Rubber",      1.63, 25, 34,  2000, 4000,  75, 95, 0.20, 0.45),
    # Tea: Munnar/Wayanad belt, ~2 t/ha made tea
    "tea":        CropProfile("Tea",         2.0,  13, 30,  1500, 3000,  70, 90, 0.25, 0.45),
    # Coffee: Wayanad/Idukki, robusta dominant, ~1.05 t/ha
    "coffee":     CropProfile("Coffee",      1.05, 20, 30,  1500, 2500,  70, 90, 0.20, 0.40),
    # Arecanut: ~1.5 t dry kernel/ha
    "arecanut":   CropProfile("Arecanut",    1.5,  14, 36,  1500, 5000,  70, 90, 0.20, 0.40),
    # Cashew: Kerala avg, ~0.8 t/ha
    "cashew":     CropProfile("Cashew",      0.8,  20, 35,  1000, 2000,  60, 80, 0.10, 0.30),

    # ─── SPICE CROPS (Kerala = Spice Garden of India) ────────
    # Black Pepper: ~0.4 t/ha (dried), Panniyur varieties
    "pepper":     CropProfile("Pepper",      0.40, 20, 30,  2000, 3000,  75, 90, 0.25, 0.45),
    # Cardamom: Idukki/Wayanad hills, ~0.2 t/ha
    "cardamom":   CropProfile("Cardamom",    0.20, 15, 25,  1500, 4000,  75, 90, 0.30, 0.50),
    # Ginger: Wayanad/Idukki, ~20 t/ha fresh (~4 t/ha dry)
    "ginger":     CropProfile("Ginger",     20.0,  19, 30,  1500, 3000,  70, 90, 0.25, 0.40),
    # Turmeric: ~25 t/ha fresh (~5 t/ha dry)
    "turmeric":   CropProfile("Turmeric",   25.0,  20, 35,  1500, 2500,  70, 90, 0.25, 0.40),
    # Nutmeg: Kerala = largest producer in India, ~0.35 t/ha dried
    "nutmeg":     CropProfile("Nutmeg",      0.35, 20, 30,  1500, 2500,  75, 90, 0.25, 0.45),
    # Clove: Nilambur/Kottayam belt, ~0.25 t/ha dried
    "clove":      CropProfile("Clove",       0.25, 20, 30,  1500, 2500,  75, 90, 0.25, 0.45),
    # Vanilla: ~0.3 t/ha cured beans (inter-cropped)
    "vanilla":    CropProfile("Vanilla",     0.30, 21, 32,  1500, 3000,  75, 90, 0.25, 0.40),
    # Cinnamon: Kerala/Karnataka, ~0.4 t/ha dried bark
    "cinnamon":   CropProfile("Cinnamon",    0.40, 20, 30,  1500, 2500,  75, 90, 0.25, 0.40),

    # ─── VEGETABLES & OTHERS ─────────────────────────────────
    # Sugarcane: limited in Kerala (Palakkad), ~55 t/ha
    "sugarcane":  CropProfile("Sugarcane",  55.0,  20, 35,  1500, 2500,  70, 85, 0.25, 0.45),
    # Groundnut: Palakkad dry belt
    "groundnut":  CropProfile("Groundnut",   1.3,  25, 30,   500, 1000,  50, 70, 0.10, 0.25),
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

    scored: list[tuple[str, dict, float]] = []
    for _key, profile in CROP_DATABASE.items():
        scores = compare_conditions(profile, weather, mean_ndvi)
        reasons = _generate_unsuitability_reasons(profile, weather, scores)
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
