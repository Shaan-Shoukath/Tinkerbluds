# Tinkerbluds — Cultivated Land Validation Platform

An **API-ready plot validation service** powered by **Google Earth Engine**, **Sentinel-2** optical imagery, **Sentinel-1 SAR** radar, **SRTM DEM** terrain data, **ESA WorldCover** land classification, **XGBoost ML** classifier, **Open-Meteo** weather & soil data, and **Supabase** for persistent farmer/plot storage.

Upload a KML polygon → get an instant validation score covering plot existence, agricultural land classification, crop plausibility, and supporting evidence layers — all through a REST API or interactive dashboard.

---

## Why This System Exists

Traditional crop insurance and agricultural subsidy programs face a core verification problem: **how do you confirm that a piece of land is actually being farmed, and that the claimed crop is plausible?**

Manual field inspections are expensive, slow, and don't scale. Satellite imagery offers a solution, but using a single data source (e.g. NDVI from optical satellites) creates false positives — **forests look just as green as farms**. Cloud cover during monsoon season blocks optical satellites for months at a time.

This platform solves these problems by **fusing four independent data sources** (optical, radar, terrain, land classification) through a **machine learning classifier** that produces a single, interpretable confidence score.

---

## How It Works — End-to-End Pipeline

```
                         ┌──────────────────────────────┐
                         │    KML Polygon Upload         │
                         └────────────┬─────────────────┘
                                      │
                         ┌────────────▼─────────────────┐
                         │  1. DOES THE PLOT EXIST?      │
                         │  KML → Sentinel-2 imagery     │
                         │  → RGB satellite thumbnail    │
                         └────────────┬─────────────────┘
                                      │
            ┌─────────────────────────┼──────────────────────────┐
            │                         │                          │
   ┌────────▼──────────┐   ┌─────────▼──────────┐   ┌──────────▼──────────┐
   │ Optical (S2)      │   │ Radar (S1 SAR)     │   │ Terrain (SRTM)      │
   │ • NDVI mean       │   │ • VH backscatter   │   │ • Elevation (m)     │
   │ • NDVI std dev    │   │ • VV backscatter   │   │ • Slope (degrees)   │
   │ • Active veg area │   │ • VH/VV ratio      │   │                     │
   └────────┬──────────┘   └─────────┬──────────┘   └──────────┬──────────┘
            │                         │                          │
            │              ┌──────────▼──────────┐               │
            │              │ ESA WorldCover      │               │
            │              │ Class 40 = Cropland │               │
            │              └──────────┬──────────┘               │
            │                         │                          │
            └─────────────────────────┼──────────────────────────┘
                                      │
                         ┌────────────▼─────────────────┐
                         │  2. IS IT AGRICULTURAL LAND?  │
                         │  8 features → XGBoost ML      │
                         │  → agricultural probability   │
                         │  → PASS / REVIEW / FAIL       │
                         └────────────┬─────────────────┘
                                      │
                         ┌────────────▼─────────────────┐
                         │  3. IS THE CROP PLAUSIBLE?    │
                         │  20-crop Kerala DB            │
                         │  × season-aware weather       │
                         │  → yield feasibility score    │
                         └────────────┬─────────────────┘
                                      │
                         ┌────────────▼─────────────────┐
                         │  4. EVIDENCE LAYERS           │
                         │  • Satellite RGB thumbnail    │
                         │  • NDVI gradient mask         │
                         │  • SAR radar backscatter      │
                         │  • Land class breakdown       │
                         │  • Weather comparison table   │
                         │  • Crop recommendations       │
                         └──────────────────────────────┘
```

---

## The Four Data Sources — Why Each Matters

### Why Not Just Use NDVI?

NDVI (Normalized Difference Vegetation Index) measures how green land is. The problem: **forests are also green**. Relying on NDVI alone produces false positives for any dense vegetation, whether it's a rice paddy or a rubber plantation with no agricultural value.

### Comparison: Single Source vs Multi-Source Fusion

| Scenario           | NDVI Only     | + WorldCover            | + SAR Radar        | + Terrain | Final Decision |
| ------------------ | ------------- | ----------------------- | ------------------ | --------- | -------------- |
| Active paddy field | ✅ Green      | ✅ Cropland             | ✅ Crop VH/VV      | ✅ Flat   | **PASS** ✅    |
| Dense forest       | ❌ Also green | ✅ Trees (not cropland) | ✅ Low VH/VV       | ✅ Sloped | **FAIL** ✅    |
| Fallow farmland    | ❌ Low NDVI   | ✅ Cropland             | ⚠️ Moderate        | ✅ Flat   | **REVIEW** ✅  |
| Construction site  | ✅ Low        | ✅ Built-up             | ✅ Very high VH/VV | —         | **FAIL** ✅    |
| Desert scrub       | ❌ Very low   | ✅ Bare/sparse          | ✅ Very low        | ✅ Flat   | **FAIL** ✅    |

Without multi-source fusion, a forest with NDVI=0.7 would score identically to a rice paddy with NDVI=0.7. SAR radar, terrain, and land classification break this ambiguity.

### Source 1: Sentinel-2 Optical (NDVI)

| What                                                       | Why                                                                                | How                                                             |
| ---------------------------------------------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Measures vegetation health using near-infrared reflectance | Chlorophyll absorbs red light but reflects NIR — the ratio indicates plant density | `NDVI = (B8 - B4) / (B8 + B4)` from cloud-free median composite |

- **NDVI temporal standard deviation** is the single most powerful feature (71.6% of ML model's decision weight)
- Crops change seasonally (planting → growth → harvest → fallow) → **high stddev**
- Forests stay green year-round → **low stddev**
- This one feature distinguishes farms from forests better than any other signal

### Source 2: Sentinel-1 SAR Radar

| What                                                             | Why                                                                                    | How                                                             |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Active microwave radar that penetrates clouds and works at night | Optical satellites are blind during monsoon season (months of cloud cover over Kerala) | C-band (5.4 GHz) VH/VV polarization ratio from median composite |

**How SAR distinguishes land types:**

| Surface            | VH/VV Ratio   | Why                                                    |
| ------------------ | ------------- | ------------------------------------------------------ |
| Crops (rice/wheat) | **0.4–0.65**  | Row crop canopy creates moderate volume scattering     |
| Dense forest       | **0.15–0.35** | Tree trunks cause strong co-polarized (VV) reflections |
| Urban/concrete     | **0.7–0.9**   | Hard corners cause multi-bounce scattering             |
| Water              | **0.1–0.2**   | Smooth surface reflects radar away from satellite      |

**SAR crop score** = 60% VH/VV score (peaks at 0.5) + 40% VH intensity score (peaks at -12 dB)

### Source 3: SRTM DEM Terrain

| What                                                             | Why                                                                                            | How                                  |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------ |
| Elevation and slope from NASA's Shuttle Radar Topography Mission | Steep slopes (>15°) are impractical for mechanized farming; high elevation limits crop options | `ee.Terrain.slope()` on SRTM 30m DEM |

### Source 4: ESA WorldCover

| What                                                 | Why                                                                                       | How                                                         |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Static 10m-resolution land classification map (2021) | Provides a baseline classification of what the land IS (trees, cropland, built-up, water) | `WorldCover == 40` identifies pixels classified as Cropland |

---

## ML Classifier — How Decisions Are Made

### The 8 Features

| #   | Feature         | Source                | Importance | Why It Matters                               |
| --- | --------------- | --------------------- | ---------- | -------------------------------------------- |
| 1   | `ndvi_stddev`   | Sentinel-2 (temporal) | **71.6%**  | Crops change seasonally, forests don't       |
| 2   | `vh_vv_ratio`   | Sentinel-1 SAR        | **14.5%**  | Distinguishes crop canopy from forests/urban |
| 3   | `ndvi_mean`     | Sentinel-2            | 5.2%       | How green the land is                        |
| 4   | `vh_mean_db`    | Sentinel-1 SAR        | 3.1%       | Radar backscatter intensity                  |
| 5   | `slope_deg`     | SRTM DEM              | 2.4%       | Steep = hard to farm                         |
| 6   | `elevation_m`   | SRTM DEM              | 1.8%       | High = fewer crop options                    |
| 7   | `rainfall_mm`   | Open-Meteo            | 0.9%       | Too dry = less viable                        |
| 8   | `soil_moisture` | Open-Meteo            | 0.5%       | Active farming needs moisture                |

### XGBoost Model

- **Why XGBoost?** Best-in-class for tabular data (8 features), handles missing values natively, ~1ms inference, ~50KB model file
- **Training:** `python scripts/train_classifier.py --samples 500` (bootstrapped synthetic data; retrain with real confirmed plots for improved accuracy)
- **Fallback:** When no trained model exists, uses a rule-based fused score: `0.7 × (0.7 × cultivated% + 0.3 × NDVI) + 0.3 × SAR_crop_score`

### Decision Thresholds

| Decision   | Condition           | Can Save to Supabase? | Meaning                                 |
| ---------- | ------------------- | --------------------- | --------------------------------------- |
| **PASS**   | probability > 0.7   | ✅ Yes                | High confidence this is active farmland |
| **REVIEW** | probability 0.4–0.7 | ✅ Yes                | Mixed signals — may need human review   |
| **FAIL**   | probability < 0.4   | ❌ Blocked            | Unlikely to be agricultural land        |

### Comparison: ML vs Previous Threshold System

| Approach                                              | Pros                                                                                       | Cons                                                                                              |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| **Old: Hard thresholds** (`cultivated% > 60% → PASS`) | Simple, predictable                                                                        | Ignores context (same threshold for Kerala plains and Western Ghats); forests with high NDVI pass |
| **New: XGBoost ML (8 features)**                      | Learns non-linear patterns; uses SAR+terrain to catch forests; provides feature importance | Needs training data; slightly less interpretable                                                  |

---

## Season-Aware Weather Comparison

### The Problem

Checking rice feasibility in February using the "last 90 days" of weather (Nov–Feb, dry season) shows 33mm rainfall — rice needs 1500–3000mm. The result: **0% rainfall score**, rice falsely flagged as "NOT RECOMMENDED."

### The Fix: Growing Season Lookup

Each crop now has a `season_start` and `season_end` month. The system automatically fetches weather from the **most recent completed growing season** instead of arbitrarily checking the last 90 days.

| Crop    | Growing Season    | Weather Period Checked             |
| ------- | ----------------- | ---------------------------------- |
| Rice    | Jun–Oct (Virippu) | Jun 1 – Oct 31 of most recent year |
| Maize   | Jun–Sep           | Jun 1 – Sep 30                     |
| Pepper  | Jun–Dec           | Jun 1 – Dec 31                     |
| Coconut | Year-round        | Last 90 days (no season)           |
| Banana  | Year-round        | Last 90 days                       |

### Before vs After

| Metric            | Before (last 90 days, dry) | After (Jun–Oct, monsoon) |
| ----------------- | -------------------------- | ------------------------ |
| **Period**        | Nov 23 – Feb 21            | **Jun 1 – Oct 31**       |
| **Rainfall**      | 33mm → **0%**              | 2329mm → **100%**        |
| **Humidity**      | 56% → **0%**               | 89% → **100%**           |
| **Soil Moisture** | 0.234 → **34%**            | 0.465 → **100%**         |
| **Overall**       | **33% (NOT RECOMMENDED)**  | **92.5% (HIGH)** ✅      |

---

## Required Deliverables — Status

### 1. A plot validation score indicating:

| Sub-requirement                           | Status | Implementation                                                                                                   |
| ----------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------- |
| **Whether the plot exists**               | ✅     | KML polygon → Sentinel-2 imagery → satellite thumbnail confirms physical land at coordinates                     |
| **Whether it is agricultural land**       | ✅     | WorldCover (Cropland) ∩ NDVI > 0.3 + SAR VH/VV ratio + terrain → 8-feature XGBoost classification                |
| **Whether the claimed crop is plausible** | ✅     | 20-crop Kerala DB × **season-aware** weather (temp, rain, humidity, soil moisture, NDVI); unsuitability warnings |
| **Supporting evidence layers**            | ✅     | Satellite RGB + NDVI gradient mask + SAR backscatter thumbnails, land class chart, weather table                 |

### 2. Clear pass/fail or confidence-based decision logic

| Component                  | Status | Implementation                                                                                           |
| -------------------------- | ------ | -------------------------------------------------------------------------------------------------------- |
| **Confidence score**       | ✅     | XGBoost ML classifier → agricultural probability (0.0–1.0); fused threshold fallback if no model trained |
| **Decision thresholds**    | ✅     | ML probability > 0.7 → PASS, 0.4–0.7 → REVIEW, < 0.4 → FAIL                                              |
| **Yield feasibility**      | ✅     | 5-parameter scoring (25% temp, 25% rain, 10% humidity, 15% soil, 25% NDVI) → season-aware                |
| **Unsuitability warnings** | ✅     | Below 40% overall → "🚫 Not Recommended"; any parameter ≤ 5% → "⚠️ Poor Yield"                           |
| **FAIL guard**             | ✅     | Non-cultivated (FAIL) plots blocked from Supabase; only PASS/REVIEW can save                             |

### 3. An API-ready validation service

| Component                 | Status | Implementation                                                                          |
| ------------------------- | ------ | --------------------------------------------------------------------------------------- |
| **REST API**              | ✅     | FastAPI with 3 endpoints: `/validate_plot`, `/confirm_plot`, `/admin/alerts` — all JSON |
| **Swagger docs**          | ✅     | Auto-generated at `http://localhost:8000/docs`                                          |
| **Farmer DB + storage**   | ✅     | Supabase stores farmers (by phone), plots (GeoJSON + KML), overlap alerts               |
| **Overlap detection**     | ✅     | Shapely geometric overlap check (≥ 5% threshold) with admin alerts                      |
| **Interactive dashboard** | ✅     | Single-page HTML dashboard with map, charts, crop recommendations, farmer registration  |

### 4. Documentation outlining validation rules and ML components

| Component                | Status | Implementation                                                                                     |
| ------------------------ | ------ | -------------------------------------------------------------------------------------------------- |
| **Validation rules**     | ✅     | Scoring formulas, decision thresholds, evidence layers in `04_validation_scoring.md` + this README |
| **ML / satellite**       | ✅     | S2, S1 SAR, DEM, WorldCover pipeline in `03_earth_engine_pipeline.md`                              |
| **ML classifier**        | ✅     | XGBoost model, 8 features, training, feature importance in `04_validation_scoring.md`              |
| **Yield & crop scoring** | ✅     | 20-crop DB, season-aware weather, Open-Meteo in `07_yield_service.md`                              |
| **System architecture**  | ✅     | End-to-end lifecycle, module deps in `01_architecture.md`                                          |
| **Frontend walkthrough** | ✅     | Dashboard UI, JS logic, CSS in `05_dashboard_frontend.md`                                          |
| **Supabase & overlap**   | ✅     | DB schema, overlap algorithm in `06_supabase_overlap.md`                                           |

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Authenticate Earth Engine
earthengine authenticate

# 3. Configure .env
EE_PROJECT_ID=your-gee-project-id
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key

# 4. Train ML model (optional — system works without it via threshold fallback)
python scripts/train_classifier.py --samples 500

# 5. Run the server
uvicorn main:app --reload
```

Open `http://localhost:8000` for the dashboard, or `http://localhost:8000/docs` for the Swagger API docs.

---

## Features

| Feature                    | Description                                                                                |
| -------------------------- | ------------------------------------------------------------------------------------------ |
| **KML Upload**             | Parse any KML polygon file                                                                 |
| **NDVI Analysis**          | Sentinel-2 vegetation index with configurable threshold (0.3)                              |
| **SAR Radar**              | Sentinel-1 C-band VH/VV backscatter for cloud-penetrating crop detection                   |
| **Terrain Analysis**       | SRTM DEM elevation + slope — steep slopes less likely to be farmed                         |
| **ML Classification**      | XGBoost classifier (8 features) with fused optical+SAR threshold fallback                  |
| **Crop Detection**         | ESA WorldCover cropland classification (class 40)                                          |
| **Land Class Breakdown**   | Per-class area chart (Trees, Cropland, Built-up, Water, etc.)                              |
| **Season-Aware Weather**   | Fetches weather from the crop's actual growing season, not just the last 90 days           |
| **Map Preview**            | Interactive Leaflet map with satellite tiles + polygon overlay                             |
| **Satellite Thumbnails**   | True-color satellite, NDVI gradient mask, and SAR radar previews (from EE)                 |
| **Yield Feasibility**      | Crop yield estimation using season-aware weather + soil moisture from Open-Meteo           |
| **Crop Recommendations**   | Top 5 crops ranked by suitability for the location's conditions                            |
| **Unsuitability Warnings** | Specific reasons when a crop won't grow (e.g. "Rainfall too low — needs 1500mm, got 80mm") |
| **PASS/REVIEW/FAIL**       | ML-based validation decision with confidence probability                                   |
| **Plot Confirmation**      | Farmer registration + save confirmed plots to Supabase                                     |
| **Overlap Detection**      | Shapely-based geometric overlap check (> 5% threshold triggers alert)                      |

---

## API Endpoints

### `POST /validate_plot` — Main Validation

| Parameter         | Type       | Default    | Description                          |
| ----------------- | ---------- | ---------- | ------------------------------------ |
| `file`            | KML upload | _required_ | Plot polygon KML file                |
| `year`            | int        | 2024       | Satellite imagery year (2015–2026)   |
| `start_month`     | int        | 1          | Start month (1–12)                   |
| `end_month`       | int        | 12         | End month (1–12)                     |
| `cloud_threshold` | int        | 20         | Max cloud cover %                    |
| `claimed_crop`    | string     | `""`       | Crop claimed by farmer (e.g. "rice") |

**Response:**

```json
{
  "plot_area_acres": 5.54,
  "cropland_area_acres": 3.88,
  "active_vegetation_area_acres": 4.24,
  "cultivated_percentage": 70.04,
  "decision": "PASS",
  "confidence_score": 0.925,
  "agricultural_probability": 0.925,
  "using_ml": true,
  "ml_feature_importance": { "ndvi_stddev": 0.716, "vh_vv_ratio": 0.145 },
  "sar_crop_score": 0.75,
  "vh_vv_ratio": 0.45,
  "mean_vh_db": -12.3,
  "mean_vv_db": -7.1,
  "elevation_m": 150.0,
  "slope_deg": 3.5,
  "ndvi_stddev": 0.18,
  "dominant_class": "Cropland",
  "land_classes": { "Cropland": 3.88, "Trees": 1.44, "Built-up": 0.22 },
  "polygon_coords": [[10.047, 76.328], "..."],
  "satellite_thumbnail": "<base64 PNG>",
  "green_mask_thumbnail": "<base64 PNG>",
  "sar_thumbnail": "<base64 PNG>",
  "green_area_acres": 4.24,
  "claimed_crop": "Rice",
  "estimated_yield_ton_per_hectare": 2.74,
  "total_estimated_yield_tons": 6.13,
  "yield_feasibility_score": 0.925,
  "yield_confidence": "HIGH",
  "is_unsuitable": false,
  "has_critical_failure": false,
  "yield_warning": "",
  "unsuitability_reasons": [],
  "weather_actual": {
    "avg_temp_c": 27.3,
    "total_rainfall_mm": 2329.1,
    "avg_humidity_pct": 89.0,
    "avg_soil_moisture": 0.465,
    "period": "2025-06-01 → 2025-10-31",
    "days_sampled": 153,
    "season_months": "6-10"
  },
  "crop_ideal": {
    "temp_range_c": "20–35",
    "rainfall_range_mm": "1500–3000",
    "humidity_range_pct": "70–90",
    "soil_moisture_range": "0.30–0.50"
  },
  "parameter_scores": {
    "temperature": 1.0,
    "rainfall": 1.0,
    "humidity": 1.0,
    "soil_moisture": 1.0,
    "vegetation": 0.625
  },
  "recommended_crops": [
    {
      "rank": 1,
      "crop": "Rice",
      "suitability_pct": 92,
      "baseline_yield": 2.96
    }
  ]
}
```

---

### `POST /confirm_plot` — Save to Supabase

Called when the user confirms "Yes, this is my plot." Only **PASS** or **REVIEW** plots can be saved — FAIL is rejected with HTTP 400.

**Request:**

```json
{
  "farmer_name": "Rajan Kumar",
  "farmer_phone": "+919876543210",
  "farmer_email": "rajan@email.com",
  "plot_label": "Paddy Field North",
  "polygon_geojson": {"type": "Polygon", "coordinates": [[[76.1, 10.2], ...]]},
  "kml_data": "<?xml ...>",
  "area_acres": 10.0,
  "cultivated_percentage": 70.0,
  "ndvi_mean": 0.72,
  "decision": "PASS",
  "confidence_score": 0.85
}
```

**Response:**

```json
{
  "success": true,
  "farmer_id": "uuid-...",
  "plot_id": "uuid-...",
  "message": "Plot saved successfully!",
  "has_overlap_warning": false,
  "overlaps": []
}
```

> **Area adjustment:** The stored area = `area_acres × cultivated_percentage / 100`. A 10-acre plot at 70% green stores as **7 acres**.

> **Overlap detection:** If the new plot overlaps any existing plot by > 5%, an alert is created and the response includes overlap details.

---

### `GET /admin/alerts?resolved=false` — Overlap Alerts

Returns all unresolved overlap alerts with linked plot/farmer data.

### `POST /admin/alerts/{alert_id}/resolve` — Resolve Alert

Marks an overlap alert as resolved.

---

## Scoring Formulas

| Metric                  | Formula                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| **Cultivated %**        | (Cropland ∩ Active Vegetation) / Total Plot Area × 100                                   |
| **Confidence Score**    | ML: XGBoost probability; Fallback: 0.7×(0.7×cultivated% + 0.3×NDVI) + 0.3×SAR_crop_score |
| **Overall (with crop)** | 0.6 × confidence + 0.4 × yield_feasibility                                               |
| **Crop Suitability**    | 25% temp + 25% rain + 10% humidity + 15% soil + 25% vegetation                           |
| **Estimated Yield**     | baseline_yield × overall_suitability                                                     |

---

## Yield Feasibility & Crop Recommendations

When a `claimed_crop` is provided, the system:

1. Looks up the crop's ideal growing conditions from a **Kerala-specific dataset** (20 crops)
2. Determines the crop's **growing season** (e.g. rice: Jun–Oct) and fetches weather from that period
3. Compares 5 parameters: temperature, rainfall, humidity, **soil moisture (0-7cm)**, and NDVI
4. Estimates yield as `baseline × overall_suitability`
5. Flags crops scoring below 40% as **unsuitable** with specific reasons

Crop recommendations are always generated — all 20 crops are scored against their respective growing seasons and the top 5 are returned.

### Supported Crops (Kerala Region)

| Category   | Crops                                                                | Key Seasons                      |
| ---------- | -------------------------------------------------------------------- | -------------------------------- |
| Food       | Rice, Tapioca, Banana, Maize                                         | Rice: Jun–Oct, Maize: Jun–Sep    |
| Plantation | Coconut, Rubber, Tea, Coffee, Arecanut, Cashew                       | Rubber: Jun–Nov, Coffee: Jun–Oct |
| Spices     | Pepper, Cardamom, Ginger, Turmeric, Nutmeg, Clove, Vanilla, Cinnamon | Pepper: Jun–Dec, Ginger: Apr–Dec |
| Others     | Sugarcane, Groundnut                                                 | Groundnut: Jun–Oct               |

### Crop Unsuitability

If a crop's overall suitability score falls below **40%**, it is flagged as **"Not Recommended"** with specific reasons:

- 🌡️ _"Temperature too hot for Cardamom — needs 15–25°C, got 32°C"_
- 🌧️ _"Rainfall too low for Rice — needs 1500–3000mm, got 80mm"_
- 🏜️ _"Soil too dry for Pepper — needs 0.25–0.45 m³/m³, got 0.12"_

If overall score is above 40% but **any single parameter is ≤ 5%**, a "critical failure" warning is shown:

- ⚠️ _"Tea will have POOR YIELD here — Rainfall, Humidity critically low"_

---

## Overlap Detection

When a plot is saved to Supabase:

1. Its GeoJSON polygon is compared **geometrically** (Shapely) against all existing saved plots
2. If intersection area / new plot area ≥ **5%**, an alert is created
3. The frontend shows an overlap warning banner + "Report to Admin" email button
4. Admin can view/resolve alerts via `GET /admin/alerts`

---

## Project Structure

```
Tinkerbluds/
├── main.py                           ← App entrypoint + EE init + dotenv
├── config.py                         ← Shared constants (SQ_M_PER_ACRE)
├── plot_validation/                  ← Core validation package
│   ├── __init__.py                   ← Package init + EE authentication
│   ├── router.py                     ← /validate_plot, /confirm_plot endpoints
│   ├── schemas.py                    ← Pydantic request/response models
│   ├── earth_engine_service.py       ← S2 + S1 + DEM + WorldCover + thumbnails
│   ├── ml_classifier.py              ← XGBoost classifier + threshold fallback
│   ├── geometry_utils.py             ← KML parsing + CRS conversion
│   ├── validation_logic.py           ← ML-based scoring + PASS/REVIEW/FAIL
│   ├── yield_service.py              ← Kerala crop DB + season-aware weather + yield
│   └── supabase_service.py           ← Farmer DB + overlap detection
├── scripts/
│   └── train_classifier.py           ← Bootstrap XGBoost training
├── data/                             ← Trained ML model (gitignored)
│   └── crop_classifier.json          ← XGBoost model (~50KB)
├── static/index.html                 ← Dashboard UI (single-file app)
├── developers_debug/                 ← Developer documentation (8 docs)
│   ├── 01_architecture.md            ← System diagram + request lifecycle
│   ├── 02_kml_geometry.md            ← KML parsing deep-dive
│   ├── 03_earth_engine_pipeline.md   ← S2 + S1 SAR + DEM + WorldCover
│   ├── 04_validation_scoring.md      ← ML classifier + scoring formulas
│   ├── 05_dashboard_frontend.md      ← Frontend JS/CSS walkthrough
│   ├── 06_supabase_overlap.md        ← Supabase + overlap detection
│   └── 07_yield_service.md           ← Yield, crop DB, soil moisture, warnings
├── requirements.txt
└── .env                              ← EE_PROJECT_ID + Supabase keys
```

---

## Environment Variables

| Variable               | Required | Description                    |
| ---------------------- | -------- | ------------------------------ |
| `EE_PROJECT_ID`        | ✅       | Google Earth Engine project ID |
| `SUPABASE_URL`         | ✅       | Supabase project URL           |
| `SUPABASE_SERVICE_KEY` | ✅       | Supabase service role key      |

---

## For Developers

See [`developers_debug/`](developers_debug/) for 8 detailed docs covering:

- System architecture and request lifecycle
- Earth Engine pipeline (Sentinel-2 + Sentinel-1 SAR + SRTM terrain)
- ML classifier (XGBoost, 8 features, training, fallback)
- Scoring formulas and decision logic
- Dashboard frontend walkthrough
- Supabase integration and overlap detection
- Yield service, crop database, and season-aware weather

Start with the [Developer README](developers_debug/README.md).
