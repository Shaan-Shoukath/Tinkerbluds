# Yield Service & Crop Recommendations

How yield feasibility, crop suitability, and unsuitability warnings work.

**File:** `plot_validation/yield_service.py`

---

## Overview

The yield service answers two questions for every plot:

1. **"Will the claimed crop grow here?"** → `estimate_yield()`
2. **"What crops would grow best here?"** → `recommend_crops()`

Both use a 20-crop Kerala-specific database and live weather data from Open-Meteo.

---

## Data Flow

```
User claims "Cardamom" on plot at (10.05°N, 76.32°E)
User chose timeline: start_year=2024, start_month=6, end_year=2024, end_month=9
                    │
                    ▼
┌──────────────────────────────────────────┐
│  Weather Fetch — Fallback Chain               │
│                                              │
│  1️⃣ User timeline provided?                    │
│     → fetch_weather_for_period(2024-06, 2024-09)│
│  2️⃣ Crop growing season known?                 │
│     → fetch_weather_for_season(lat, lon, crop) │
│  3️⃣ Fallback: last 90 days                     │
│     → fetch_weather_last_3_months(lat, lon)    │
│                                              │
│  Result:                                     │
│  → avg_temp_c: 27.3°C                        │
│  → total_rainfall_mm: 1450mm                 │
│  → avg_humidity_pct: 78.2%                   │
│  → avg_soil_moisture: 0.312 m³/m³            │
└──────────────────┬───────────────────────┘
                   │
┌──────────────────▼───────────────────────┐
│  Compare vs Cardamom's ideal ranges      │
│  Temp:     15–25°C     → score: 0.64    │
│  Rain:     1500–4000mm → score: 0.00    │
│  Humidity: 75–90%      → score: 0.00    │
│  Soil:     0.30–0.50   → score: 0.16    │
│  NDVI:     ≥ 0.3       → score: 1.00    │
└──────────────────┬───────────────────────┘
                   │
┌──────────────────▼───────────────────────┐
│  Weighted Overall Score                   │
│  25% temp + 25% rain + 10% humidity      │
│  + 15% soil + 25% vegetation             │
│  = 0.43 (43%) → MODERATE                 │
│                                          │
│  Critical failures: Rain ≤ 5%, Humidity  │
│  → has_critical_failure = true           │
│  → "⚠️ Cardamom will have POOR YIELD    │
│     here — Rainfall, Humidity critically │
│     low"                                 │
└──────────────────────────────────────────┘
```

---

## Crop Database

`CROP_DATABASE` contains 20 Kerala-specific crops, each a `CropProfile` dataclass:

```python
@dataclass
class CropProfile:
    name:           str    # Display name
    baseline_yield: float  # tonnes/hectare (Kerala average)
    temp_min:       float  # °C ideal range
    temp_max:       float
    rain_min:       float  # mm per 3 months ideal
    rain_max:       float
    humidity_min:   float  # % ideal range
    humidity_max:   float
    soil_min:       float  # m³/m³ soil moisture ideal
    soil_max:       float
```

### Full Crop Table

| Category   | Crop      | Yield (t/ha) | Temp (°C) | Rain (mm) | Humidity (%) | Soil Moisture |
| ---------- | --------- | ------------ | --------- | --------- | ------------ | ------------- |
| **Food**   | Rice      | 2.96         | 20–35     | 1500–3000 | 70–90        | 0.30–0.50     |
|            | Tapioca   | 25.0         | 25–30     | 1000–2000 | 60–85        | 0.15–0.35     |
|            | Banana    | 20.0         | 20–35     | 1200–2500 | 60–90        | 0.20–0.40     |
| **Spices** | Pepper    | 0.35         | 20–30     | 1500–3000 | 70–90        | 0.25–0.45     |
|            | Cardamom  | 0.20         | 15–25     | 1500–4000 | 75–90        | 0.30–0.50     |
|            | Ginger    | 5.50         | 20–30     | 1500–3000 | 70–90        | 0.25–0.40     |
|            | Turmeric  | 5.00         | 20–30     | 1500–2500 | 65–85        | 0.20–0.40     |
|            | Nutmeg    | 0.10         | 20–30     | 1500–3000 | 70–90        | 0.25–0.45     |
|            | Clove     | 0.05         | 20–30     | 1500–3000 | 70–90        | 0.25–0.45     |
|            | Cinnamon  | 0.50         | 25–32     | 1250–2500 | 65–85        | 0.20–0.40     |
|            | Vanilla   | 0.30         | 20–30     | 1500–3000 | 70–90        | 0.25–0.45     |
| **Cash**   | Coconut   | 10000 nuts   | 22–32     | 1000–2500 | 60–90        | 0.15–0.35     |
|            | Rubber    | 1.60         | 22–32     | 1500–3000 | 70–90        | 0.20–0.40     |
|            | Tea       | 1.80         | 13–30     | 1500–3000 | 70–90        | 0.25–0.45     |
|            | Coffee    | 0.80         | 15–28     | 1000–2500 | 60–80        | 0.20–0.40     |
|            | Cashew    | 0.75         | 24–35     | 600–2000  | 50–80        | 0.10–0.30     |
|            | Arecanut  | 1.50         | 22–32     | 1500–3000 | 70–90        | 0.25–0.45     |
| **Fruits** | Pineapple | 20.0         | 22–32     | 1000–2000 | 60–85        | 0.15–0.35     |
|            | Jackfruit | 15.0         | 22–35     | 1000–2500 | 60–90        | 0.15–0.35     |
|            | Mango     | 8.0          | 24–35     | 500–2000  | 50–80        | 0.10–0.30     |

---

## Scoring Functions

### `_range_score(actual, ideal_min, ideal_max)`

Scores how well an actual value fits the ideal range (0.0 – 1.0):

```
Score = 1.0  if  ideal_min ≤ actual ≤ ideal_max
Score degrades linearly towards 0.0 as actual moves away from the range
Margin = max(range_width × 0.5, 5.0)
```

### `_soil_score(actual, ideal_min, ideal_max)`

Same logic but with tighter margins for soil moisture (values are 0–0.5 range):

```
Score = 0.5  if actual is 0.0 (no data → neutral)
Margin = max(range_width × 0.5, 0.05)
```

### `_veg_score(mean_ndvi)`

Simple threshold check for vegetation health:

```
Score = min(mean_ndvi / 0.3, 1.0)
NDVI ≥ 0.3 → 1.0 (healthy vegetation)
NDVI = 0.15 → 0.5 (sparse)
NDVI = 0.0 → 0.0 (bare)
```

---

## Overall Suitability Weighting

```python
compare_conditions() returns:
    overall = (
        0.25 × temp_score
      + 0.25 × rain_score
      + 0.10 × humidity_score
      + 0.15 × soil_score
      + 0.25 × vegetation_score
    )
```

| Parameter   | Weight | Reasoning                                  |
| ----------- | ------ | ------------------------------------------ |
| Temperature | 25%    | Primary growth driver                      |
| Rainfall    | 25%    | Critical for Kerala's rain-fed agriculture |
| Humidity    | 10%    | Secondary; often correlates with rainfall  |
| Soil Moist. | 15%    | Direct root-zone water availability        |
| Vegetation  | 25%    | NDVI: actual crop health from satellite    |

---

## Unsuitability & Warning System

Two levels of warnings:

### Level 1: Overall Unsuitability (≥ 40% threshold)

```python
UNSUITABILITY_THRESHOLD = 0.40

if overall < 0.40:
    is_unsuitable = True
    → "🚫 Cardamom is NOT RECOMMENDED for this region — overall suitability only 36%"
```

### Level 2: Critical Parameter Failures (any ≤ 5%)

Even if overall is above 40%, individual parameters at near-zero indicate the crop won't yield:

```python
if any_param_score <= 0.05:   # e.g. rainfall = 0%
    has_critical_failure = True
    → "⚠️ Tea will have POOR YIELD here — Rainfall, Humidity critically low"
```

### `_generate_unsuitability_reasons(profile, weather, scores)`

Generates human-readable warnings for every parameter scoring below 50%:

```python
# Example output:
[
    {"icon": "🌧️", "reason": "Rainfall too low for Tea — needs 1500–3000mm, got 33mm", "score": 0.0},
    {"icon": "💧", "reason": "Humidity too low for Tea — needs 70–90%, got 55.9%", "score": 0.0},
]
```

### `_build_yield_warning(scores, reasons, profile_name)`

Combines both checks into a single response:

```python
{
    "is_unsuitable": False,           # overall ≥ 40%
    "has_critical_failure": True,     # rain=0%, humidity=0%
    "yield_warning": "⚠️ Tea will have POOR YIELD here — Rainfall, Humidity critically low",
}
```

---

## Confidence Levels

| Score Range | Label    |
| ----------- | -------- |
| ≥ 75%       | HIGH     |
| 50–74%      | MODERATE |
| < 50%       | LOW      |

---

## Weather Data Source

The yield service fetches weather from the **Open-Meteo Historical Weather API** using a three-level fallback chain:

### 1. `fetch_weather_for_period(lat, lon, start_year, start_month, end_year, end_month)`

Used when the user supplies an explicit timeline in the UI (year, from-month, to-month). Builds the exact date range from the user's selection:

```python
start_date = f"{start_year}-{start_month:02d}-01"
end_date   = last day of end_year/end_month
```

### 2. `fetch_weather_for_season(lat, lon, crop_name)`

Used when no user timeline is provided but the claimed crop has a known growing season (e.g. Rice → June–October). Currently not populated for most crops, so this step is skipped in practice.

### 3. `fetch_weather_last_3_months(lat, lon)` **(original fallback)**

Used when neither a user timeline nor a crop season is available. Fetches the last 90 days from today.

### Common API Call

All three functions call the same endpoint:

```
https://archive-api.open-meteo.com/v1/archive
  ?latitude=10.05
  &longitude=76.32
  &start_date=2024-06-01
  &end_date=2024-09-30
  &daily=temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean,soil_moisture_0_to_7cm_mean
  &timezone=Asia/Kolkata
```

Returns aggregated values:

| Field               | Aggregation | Unit  |
| ------------------- | ----------- | ----- |
| `avg_temp_c`        | Mean        | °C    |
| `total_rainfall_mm` | Sum         | mm    |
| `avg_humidity_pct`  | Mean        | %     |
| `avg_soil_moisture` | Mean        | m³/m³ |

### Fallback Priority in `estimate_yield()`

```python
def estimate_yield(crop_name, mean_ndvi, lat, lon, area_hectares,
                   start_year=None, start_month=None,
                   end_year=None, end_month=None):
    # 1. User timeline (if all 4 params provided)
    if start_year and start_month and end_year and end_month:
        weather = fetch_weather_for_period(lat, lon, start_year, start_month, end_year, end_month)
    # 2. Crop growing season
    elif crop has known season:
        weather = fetch_weather_for_season(lat, lon, crop_name)
    # 3. Last 90 days
    else:
        weather = fetch_weather_last_3_months(lat, lon)
```

> **Design note:** The user-chosen timeline is the most meaningful
> comparison — it covers the exact period they intend to farm,
> so weather data for that window produces the most accurate
> feasibility assessment.

---

## Response Fields (from `estimate_yield`)

```json
{
  "claimed_crop": "Tea",
  "baseline_yield": 1.80,
  "estimated_yield_ton_per_hectare": 1.10,
  "total_estimated_yield_tons": 0.71,
  "yield_feasibility_score": 0.55,
  "yield_confidence": "MODERATE",
  "is_unsuitable": false,
  "has_critical_failure": true,
  "yield_warning": "⚠️ Tea will have POOR YIELD here — Rainfall, Humidity critically low",
  "unsuitability_reasons": [
    {"icon": "🌧️", "reason": "Rainfall too low for Tea — needs 1500–3000mm, got 33mm", "score": 0.0},
    {"icon": "💧", "reason": "Humidity too low for Tea — needs 70–90%, got 55.9%", "score": 0.0}
  ],
  "weather_actual": { "avg_temp_c": 27.3, "total_rainfall_mm": 33.3, "avg_humidity_pct": 55.9, "avg_soil_moisture": 0.234 },
  "crop_ideal": { "temp_range": "13–30°C", "rain_range": "1500–3000mm", ... },
  "parameter_scores": { "temperature": 1.0, "rainfall": 0.0, "humidity": 0.0, "soil_moisture": 0.84, "vegetation": 0.70 }
}
```

---

## Crop Recommendations (`recommend_crops`)

Runs **all 20 crops** through `compare_conditions()` and returns the top N (default 5) by overall suitability, each including:

```json
{
  "rank": 1,
  "crop": "cashew",
  "suitability_pct": 78,
  "is_unsuitable": false,
  "has_critical_failure": false,
  "yield_warning": "",
  "unsuitability_reasons": [],
  "temp_score": 0.92,
  "rain_score": 0.55,
  "humidity_score": 0.72,
  "soil_score": 0.8,
  "vegetation_score": 0.95,
  "baseline_yield": 0.75
}
```

Crops with `has_critical_failure: true` display a ⚠️ warning badge in the dashboard.
