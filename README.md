# Tinkerbluds

Cultivated land validation platform powered by **Google Earth Engine**, **Sentinel-2** satellite imagery, and **ESA WorldCover** land classification.

Upload a KML polygon → get an instant analysis of cropland presence, vegetation health, and land class breakdown — with an interactive map preview.

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

Open `http://localhost:8000` for the dashboard, or `http://localhost:8000/docs` for the API docs.

---

## Features

| Feature                  | Description                                                    |
| ------------------------ | -------------------------------------------------------------- |
| **KML Upload**           | Parse any KML polygon file                                     |
| **NDVI Analysis**        | Sentinel-2 vegetation index with configurable threshold (0.3)  |
| **Crop Detection**       | ESA WorldCover cropland classification (class 40)              |
| **Land Class Breakdown** | Per-class area chart (Trees, Cropland, Built-up, etc.)         |
| **Month Range Filter**   | Analyze specific growing seasons (e.g. Jan–Mar)                |
| **Map Preview**          | Interactive Leaflet map with satellite tiles + polygon overlay |
| **PASS/REVIEW Decision** | Automated validation based on cultivated percentage            |

---

## API

### `POST /validate_plot`

| Parameter         | Type       | Default    | Description                        |
| ----------------- | ---------- | ---------- | ---------------------------------- |
| `file`            | KML upload | _required_ | Plot polygon KML file              |
| `year`            | int        | 2024       | Satellite imagery year (2015–2026) |
| `start_month`     | int        | 1          | Start month (1–12)                 |
| `end_month`       | int        | 12         | End month (1–12)                   |
| `cloud_threshold` | int        | 20         | Max cloud cover %                  |

**Response:**

```json
{
  "plot_area_acres": 5.54,
  "cropland_area_acres": 0.00,
  "active_vegetation_area_acres": 4.24,
  "cultivated_percentage": 0.0,
  "decision": "REVIEW",
  "confidence_score": 0.11,
  "dominant_class": "Trees",
  "land_classes": { "Trees": 4.44, "Built-up": 1.08, "Grassland": 0.02 },
  "polygon_coords": [[10.047, 76.328], ...]
}
```

---

## Project Structure

```
Tinkerbluds/
├── main.py                           ← App entrypoint + router include
├── config.py                         ← Shared constants
├── plot_validation/                  ← Plot validation package
│   ├── router.py                     ← /validate_plot endpoint
│   ├── schemas.py                    ← Pydantic response models
│   ├── earth_engine_service.py       ← EE processing pipeline
│   ├── geometry_utils.py             ← KML parsing + geometry
│   └── validation_logic.py           ← Scoring + decision
├── static/index.html                 ← Dashboard UI
├── developers_debug/                 ← Developer documentation
│   ├── 01_architecture.md
│   ├── 02_kml_geometry.md
│   ├── 03_earth_engine_pipeline.md
│   ├── 04_validation_scoring.md
│   └── 05_dashboard_frontend.md
├── requirements.txt
└── .env
```

---

## Decision Logic

- **Cultivated %** = (Cropland ∩ Active Vegetation) / Plot Area × 100
- **Confidence** = 0.7 × cultivated% + 0.3 × mean NDVI
- **PASS** if cultivated% > 60%, else **REVIEW**

---

## For Developers

See [`developers_debug/`](developers_debug/) for detailed documentation on each module, including data flow diagrams, function-by-function explanations, library comparisons, and reasoning behind every design decision.
