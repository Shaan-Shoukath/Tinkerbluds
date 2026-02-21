# Tinkerbluds

Cultivated land validation platform powered by **Google Earth Engine**, **Sentinel-2** satellite imagery, **ESA WorldCover** land classification, and **Open-Meteo** weather data.

Upload a KML polygon → get an instant analysis of cropland presence, vegetation health, land class breakdown, satellite previews, and crop yield feasibility — all through an interactive dashboard.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Authenticate Earth Engine
earthengine authenticate

# 3. Configure .env
echo "EE_PROJECT_ID=your-gee-project-id" > .env

# 4. Run the server
uvicorn main:app --reload
```

Open `http://localhost:8000` for the dashboard, or `http://localhost:8000/docs` for the Swagger API docs.

---

## Features

| Feature                  | Description                                                            |
| ------------------------ | ---------------------------------------------------------------------- |
| **KML Upload**           | Parse any KML polygon file                                             |
| **NDVI Analysis**        | Sentinel-2 vegetation index with configurable threshold (0.3)          |
| **Crop Detection**       | ESA WorldCover cropland classification (class 40)                      |
| **Land Class Breakdown** | Per-class area chart (Trees, Cropland, Built-up, etc.)                 |
| **Month Range Filter**   | Analyze specific growing seasons (e.g. Jan–Mar)                        |
| **Map Preview**          | Interactive Leaflet map with satellite tiles + polygon overlay         |
| **Satellite Thumbnails** | True-color satellite & green vegetation mask previews (from EE)        |
| **Green Mask**           | Binary NDVI mask — solid green for vegetation, dark greyscale for rest |
| **Yield Feasibility**    | Crop yield estimation using real weather data from Open-Meteo API      |
| **Weather Comparison**   | Parameter-by-parameter actual vs ideal (temp, rain, humidity)          |
| **PASS/REVIEW Decision** | Automated validation based on cultivated percentage                    |

---

## API

### `POST /validate_plot`

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
  "cropland_area_acres": 0.0,
  "active_vegetation_area_acres": 4.24,
  "cultivated_percentage": 0.0,
  "decision": "REVIEW",
  "confidence_score": 0.28,
  "dominant_class": "Trees",
  "land_classes": { "Trees": 4.44, "Built-up": 1.08, "Grassland": 0.02 },
  "polygon_coords": [[10.047, 76.328], "..."],
  "satellite_thumbnail": "<base64 PNG>",
  "green_mask_thumbnail": "<base64 PNG>",
  "green_area_acres": 1.59,
  "claimed_crop": "Rice",
  "estimated_yield_ton_per_hectare": 2.45,
  "total_estimated_yield_tons": 5.49,
  "yield_feasibility_score": 0.85,
  "yield_confidence": "HIGH",
  "weather_actual": {
    "avg_temp_c": 27.3,
    "total_rainfall_mm": 342.1,
    "avg_humidity_pct": 66.3,
    "period": "2024-11-22 → 2025-02-19",
    "days_sampled": 91
  },
  "crop_ideal": {
    "temp_range_c": "22–32",
    "rainfall_range_mm": "300–700",
    "humidity_range_pct": "60–90"
  },
  "parameter_scores": {
    "temperature": 1.0,
    "rainfall": 0.42,
    "humidity": 1.0,
    "vegetation": 1.0
  }
}
```

---

## Project Structure

```
Tinkerbluds/
├── main.py                           ← App entrypoint + EE init + router
├── config.py                         ← Shared constants (SQ_M_PER_ACRE)
├── plot_validation/                  ← Core validation package
│   ├── __init__.py                   ← Package init + EE authentication
│   ├── router.py                     ← /validate_plot endpoint
│   ├── schemas.py                    ← Pydantic response models
│   ├── earth_engine_service.py       ← EE pipeline + thumbnails
│   ├── geometry_utils.py             ← KML parsing + CRS conversion
│   ├── validation_logic.py           ← Scoring + PASS/REVIEW decision
│   └── yield_service.py              ← Crop dataset + Open-Meteo + yield
├── static/index.html                 ← Dashboard UI (single-file app)
├── developers_debug/                 ← Developer documentation
│   ├── README.md                     ← Index of all developer docs
│   ├── 01_architecture.md            ← System diagram + request lifecycle
│   ├── 02_kml_geometry.md            ← KML parsing deep-dive
│   ├── 03_earth_engine_pipeline.md   ← Sentinel-2 + NDVI + WorldCover
│   ├── 04_validation_scoring.md      ← Scoring formulas + examples
│   └── 05_dashboard_frontend.md      ← Frontend JS/CSS walkthrough
├── requirements.txt
└── .env                              ← EE_PROJECT_ID
```

---

## Decision Logic

- **Cultivated %** = (Cropland ∩ Active Vegetation) / Plot Area × 100
- **Confidence** = 0.7 × cultivated% + 0.3 × mean NDVI
- If yield is estimated: **Overall** = 0.8 × confidence + 0.2 × yield_feasibility
- **PASS** if cultivated% > 60%, else **REVIEW**

---

## Yield Feasibility

When a `claimed_crop` is provided, the system:

1. Looks up the crop's ideal growing conditions from a built-in dataset (20 Indian crops)
2. Fetches the **last 90 days of real weather** at the plot's location from the [Open-Meteo API](https://open-meteo.com/) (free, no key)
3. Compares actual vs ideal for temperature, rainfall, humidity, and NDVI
4. Estimates yield as `baseline × overall_suitability`

---

## For Developers

See [`developers_debug/`](developers_debug/) for detailed documentation on each module — architecture diagrams, function-level explanations, Earth Engine pipeline deep-dives, scoring formulas, and frontend walkthrough. Start with the [README](developers_debug/README.md) for a roadmap.
