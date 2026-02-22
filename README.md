# Tinkerbluds — Cultivated Land Validation Platform

An **API-ready plot validation service** powered by **Google Earth Engine**, **Sentinel-2** satellite imagery, **ESA WorldCover** land classification, **Open-Meteo** weather & soil data, and **Supabase** for persistent farmer/plot storage.

Upload a KML polygon → get an instant validation score covering plot existence, agricultural land classification, crop plausibility, and supporting evidence layers — all through a REST API or interactive dashboard.

---

## Required Deliverables

### 1. A plot validation score indicating:

| Sub-requirement                           | Status | Implementation                                                                                                                                     |
| ----------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Whether the plot exists**               | ✅     | KML polygon parsed → mapped to Sentinel-2 imagery → satellite thumbnail confirms physical land exists at the given coordinates                     |
| **Whether it is agricultural land**       | ✅     | ESA WorldCover (class 40 = Cropland) intersected with NDVI > 0.3 (active vegetation) → cultivated area percentage calculated                       |
| **Whether the claimed crop is plausible** | ✅     | 20-crop Kerala DB compared against actual weather + soil moisture (0–7cm) + NDVI; unsuitability warnings if overall < 40% or any parameter at 0%   |
| **Supporting evidence layers**            | ✅     | Historical satellite RGB thumbnail, NDVI gradient mask, land-class breakdown chart (Trees / Cropland / Built-up / Water), weather comparison table |

### 2. Clear pass/fail or confidence-based decision logic

| Component                  | Status | Implementation                                                                                                           |
| -------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------ |
| **Confidence score**       | ✅     | `confidence = 0.7 × cultivated% + 0.3 × mean_NDVI` — weighted formula combining land coverage and vegetation health      |
| **Decision thresholds**    | ✅     | Cultivated % > 60% → **PASS**, ≤ 60% → **REVIEW**, no cropland → **FAIL**                                                |
| **Yield feasibility**      | ✅     | 5-parameter scoring (temp, rain, humidity, soil moisture, NDVI) with 25/25/10/15/25% weights → overall suitability score |
| **Unsuitability warnings** | ✅     | Crops below 40% overall → "🚫 Not Recommended"; any parameter ≤ 5% → "⚠️ Poor Yield" with specific reasons               |
| **FAIL guard**             | ✅     | Non-cultivated (FAIL) plots are blocked from being saved to Supabase; only PASS/REVIEW can proceed                       |

### 3. An API-ready validation service

| Component                 | Status | Implementation                                                                                               |
| ------------------------- | ------ | ------------------------------------------------------------------------------------------------------------ |
| **REST API**              | ✅     | FastAPI with 3 endpoints: `/validate_plot`, `/confirm_plot`, `/admin/alerts` — all return JSON               |
| **Swagger docs**          | ✅     | Auto-generated at `http://localhost:8000/docs`                                                               |
| **Farmer DB + storage**   | ✅     | Supabase stores farmers (by phone), plots (GeoJSON + KML), and overlap alerts; cultivated area auto-adjusted |
| **Overlap detection**     | ✅     | Shapely-based geometric overlap check (≥ 5% threshold) with admin alert creation                             |
| **Interactive dashboard** | ✅     | Single-page HTML dashboard with map, charts, crop recommendations, and farmer registration                   |

### 4. Documentation outlining validation rules and ML components

| Component                     | Status | Implementation                                                                                               |
| ----------------------------- | ------ | ------------------------------------------------------------------------------------------------------------ |
| **Validation rules**          | ✅     | Scoring formulas, decision thresholds, and evidence layers documented in `04_validation_scoring.md` + README |
| **ML / satellite components** | ✅     | Earth Engine pipeline (NDVI, WorldCover, compositing) documented in `03_earth_engine_pipeline.md`            |
| **Yield & crop scoring**      | ✅     | 20-crop database, 5-parameter comparison, Open-Meteo integration documented in `07_yield_service.md`         |
| **System architecture**       | ✅     | End-to-end request lifecycle, module dependency map in `01_architecture.md`                                  |
| **Frontend walkthrough**      | ✅     | Dashboard UI components, JS logic, CSS design system in `05_dashboard_frontend.md`                           |
| **Supabase & overlap**        | ✅     | DB schema, overlap algorithm, API endpoints in `06_supabase_overlap.md`                                      |
| **Developer docs index**      | ✅     | 7 docs in `developers_debug/` with reading order guide                                                       |

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

# 4. Run the server
uvicorn main:app --reload
```

Open `http://localhost:8000` for the dashboard, or `http://localhost:8000/docs` for the Swagger API docs.

---

## Features

| Feature                    | Description                                                                                |
| -------------------------- | ------------------------------------------------------------------------------------------ |
| **KML Upload**             | Parse any KML polygon file                                                                 |
| **NDVI Analysis**          | Sentinel-2 vegetation index with configurable threshold (0.3)                              |
| **Crop Detection**         | ESA WorldCover cropland classification (class 40)                                          |
| **Land Class Breakdown**   | Per-class area chart (Trees, Cropland, Built-up, Water, etc.)                              |
| **Month Range Filter**     | Analyze specific growing seasons (e.g. Jan–Mar)                                            |
| **Map Preview**            | Interactive Leaflet map with satellite tiles + polygon overlay                             |
| **Satellite Thumbnails**   | True-color satellite & NDVI gradient mask previews (from EE)                               |
| **NDVI Gradient Mask**     | Vegetation intensity map — bright green = dense, dark = bare soil                          |
| **Yield Feasibility**      | Crop yield estimation using real weather + soil moisture from Open-Meteo                   |
| **Soil Moisture**          | Volumetric soil moisture (0–7cm) integrated into crop scoring                              |
| **Weather Comparison**     | 5-parameter comparison: temperature, rainfall, humidity, soil moisture, NDVI               |
| **Crop Recommendations**   | Top 5 crops ranked by suitability for the location's conditions                            |
| **Unsuitability Warnings** | Specific reasons when a crop won't grow (e.g. "Rainfall too low — needs 1500mm, got 80mm") |
| **PASS/REVIEW Decision**   | Automated validation based on cultivated percentage                                        |
| **Plot Confirmation**      | Farmer registration + save confirmed plots to Supabase                                     |
| **Overlap Detection**      | Shapely-based geometric overlap check (> 5% threshold triggers alert)                      |
| **Area Adjustment**        | Only cultivated portion saved (10 acres × 70% green = 7 acres in DB)                       |
| **Admin Alerts**           | Overlap alerts stored in DB + email report to admin                                        |

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
  "confidence_score": 0.72,
  "dominant_class": "Cropland",
  "land_classes": { "Cropland": 3.88, "Trees": 1.44, "Built-up": 0.22 },
  "polygon_coords": [[10.047, 76.328], "..."],
  "satellite_thumbnail": "<base64 PNG>",
  "green_mask_thumbnail": "<base64 PNG>",
  "green_area_acres": 4.24,
  "claimed_crop": "Rice",
  "estimated_yield_ton_per_hectare": 2.45,
  "total_estimated_yield_tons": 5.49,
  "yield_feasibility_score": 0.85,
  "yield_confidence": "HIGH",
  "is_unsuitable": false,
  "has_critical_failure": false,
  "yield_warning": "",
  "unsuitability_reasons": [],
  "weather_actual": {
    "avg_temp_c": 27.3,
    "total_rainfall_mm": 1842.1,
    "avg_humidity_pct": 82.3,
    "avg_soil_moisture": 0.312,
    "period": "2024-11-22 → 2025-02-19",
    "days_sampled": 91
  },
  "crop_ideal": {
    "temp_range_c": "20–35",
    "rainfall_range_mm": "1500–3000",
    "humidity_range_pct": "70–90",
    "soil_moisture_range": "0.30–0.50"
  },
  "parameter_scores": {
    "temperature": 1.0,
    "rainfall": 0.92,
    "humidity": 1.0,
    "soil_moisture": 0.88,
    "vegetation": 1.0
  },
  "recommended_crops": [
    {
      "rank": 1,
      "crop": "Rice",
      "suitability_pct": 85,
      "temp_score": 1.0,
      "rain_score": 0.92,
      "humidity_score": 1.0,
      "soil_score": 0.88,
      "vegetation_score": 1.0,
      "baseline_yield": 2.96,
      "is_unsuitable": false,
      "has_critical_failure": false,
      "yield_warning": "",
      "unsuitability_reasons": []
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

## Validation Rules & Decision Logic

### Scoring Pipeline

```
KML Polygon
    │
    ├── 1. PLOT EXISTS? ──── Sentinel-2 imagery confirms land at coordinates
    │
    ├── 2. IS IT AGRICULTURAL? ──── WorldCover class 40 (Cropland) area check
    │                                + NDVI > 0.3 (active vegetation)
    │
    ├── 3. IS THE CROP PLAUSIBLE? ── Compare claimed crop against:
    │                                 • Temperature (last 90 days)
    │                                 • Rainfall (last 90 days)
    │                                 • Humidity (last 90 days)
    │                                 • Soil moisture (0-7cm, last 90 days)
    │                                 • NDVI (vegetation health)
    │
    ├── 4. EVIDENCE LAYERS ────────── Satellite RGB thumbnail
    │                                 NDVI gradient mask
    │                                 Land class breakdown chart
    │                                 Weather comparison table
    │
    └── 5. DECISION ───────────────── PASS / REVIEW / FAIL
```

### Formulas

| Metric                  | Formula                                                        |
| ----------------------- | -------------------------------------------------------------- |
| **Cultivated %**        | (Cropland ∩ Active Vegetation) / Total Plot Area × 100         |
| **Confidence Score**    | 0.7 × cultivated% + 0.3 × mean NDVI                            |
| **Overall (with crop)** | 0.8 × confidence + 0.2 × yield_feasibility                     |
| **Crop Suitability**    | 25% temp + 25% rain + 10% humidity + 15% soil + 25% vegetation |
| **Estimated Yield**     | baseline_yield × overall_suitability                           |

### Decision Thresholds

| Decision   | Condition            | Can Save to Supabase? |
| ---------- | -------------------- | --------------------- |
| **PASS**   | cultivated% > 60%    | ✅ Yes                |
| **REVIEW** | cultivated% ≤ 60%    | ✅ Yes                |
| **FAIL**   | No cropland detected | ❌ Blocked            |

### Crop Unsuitability

If a crop's overall suitability score falls below **40%**, it is flagged as **"Not Recommended"** with specific reasons:

- 🌡️ _"Temperature too hot for Cardamom — needs 15–25°C, got 32°C"_
- 🌧️ _"Rainfall too low for Rice — needs 1500–3000mm, got 80mm"_
- 🏜️ _"Soil too dry for Pepper — needs 0.25–0.45 m³/m³, got 0.12"_

If overall score is above 40% but **any single parameter is ≤ 5%**, a "critical failure" warning is shown:

- ⚠️ _"Tea will have POOR YIELD here — Rainfall, Humidity critically low"_

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
│   ├── earth_engine_service.py       ← EE pipeline + thumbnails
│   ├── geometry_utils.py             ← KML parsing + CRS conversion
│   ├── validation_logic.py           ← Scoring + PASS/REVIEW decision
│   ├── yield_service.py              ← Kerala crop DB + Open-Meteo + yield
│   └── supabase_service.py           ← Farmer DB + overlap detection
├── static/index.html                 ← Dashboard UI (single-file app)
├── developers_debug/                 ← Developer documentation (7 docs)
│   ├── 01_architecture.md            ← System diagram + request lifecycle
│   ├── 02_kml_geometry.md            ← KML parsing deep-dive
│   ├── 03_earth_engine_pipeline.md   ← Sentinel-2 + NDVI + WorldCover
│   ├── 04_validation_scoring.md      ← Scoring formulas + examples
│   ├── 05_dashboard_frontend.md      ← Frontend JS/CSS walkthrough
│   ├── 06_supabase_overlap.md        ← Supabase + overlap detection
│   └── 07_yield_service.md           ← Yield, crop DB, soil moisture, warnings
├── requirements.txt
└── .env                              ← EE_PROJECT_ID + Supabase keys
```

---

## Yield Feasibility & Crop Recommendations

When a `claimed_crop` is provided, the system:

1. Looks up the crop's ideal growing conditions from a **Kerala-specific dataset** (20 crops)
2. Fetches the **last 90 days of real weather + soil moisture** from the [Open-Meteo API](https://open-meteo.com/)
3. Compares 5 parameters: temperature, rainfall, humidity, **soil moisture (0-7cm)**, and NDVI
4. Estimates yield as `baseline × overall_suitability`
5. Flags crops scoring below 40% as **unsuitable** with specific reasons

Crop recommendations are always generated (no `claimed_crop` needed) — all 20 crops are scored and the top 5 are returned.

### Supported Crops (Kerala Region)

| Category   | Crops                                                                |
| ---------- | -------------------------------------------------------------------- |
| Food       | Rice, Tapioca, Banana                                                |
| Plantation | Coconut, Rubber, Tea, Coffee, Arecanut, Cashew                       |
| Spices     | Pepper, Cardamom, Ginger, Turmeric, Nutmeg, Clove, Vanilla, Cinnamon |
| Fruits     | Pineapple, Jackfruit, Mango                                          |

---

## Overlap Detection

When a plot is saved to Supabase:

1. Its GeoJSON polygon is compared **geometrically** (Shapely) against all existing saved plots
2. If intersection area / new plot area ≥ **5%**, an alert is created
3. The frontend shows an overlap warning banner + "Report to Admin" email button
4. Admin can view/resolve alerts via `GET /admin/alerts`

---

## Environment Variables

| Variable               | Required | Description                    |
| ---------------------- | -------- | ------------------------------ |
| `EE_PROJECT_ID`        | ✅       | Google Earth Engine project ID |
| `SUPABASE_URL`         | ✅       | Supabase project URL           |
| `SUPABASE_SERVICE_KEY` | ✅       | Supabase service role key      |

---

## For Developers

See [`developers_debug/`](developers_debug/) for 7 detailed docs: architecture diagrams, function-level explanations, Earth Engine pipeline, scoring formulas, frontend walkthrough, Supabase integration, and yield service. Start with the [README](developers_debug/README.md).
