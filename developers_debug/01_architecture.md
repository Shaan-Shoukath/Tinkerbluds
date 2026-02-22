# Architecture Overview

How all the pieces fit together — from KML upload to JSON response.

---

## System Diagram

```
                          ┌──────────────────────────────┐
                          │        Google Cloud           │
                          │     (Earth Engine Servers)    │
                          │                              │
                          │  ┌──────────────────────┐    │
                          │  │ Sentinel-2 Catalogue │    │
                          │  │ (10TB+ imagery)      │    │
 ┌──────────┐             │  └──────┬───────────────┘    │
 │ Browser  │ ──POST──→   │         │                    │
 │ Dashboard│             │  ┌──────▼───────────────┐    │
 └──────────┘             │  │ Computation Graph    │    │
       │                  │  │  filter → composite  │    │
       │                  │  │  → NDVI → mask       │    │
  KML file                │  │  → reduceRegion      │    │
       │                  │  └──────┬───────────────┘    │
       ▼                  │         │ .getInfo()         │
 ┌───────────────┐        │  ┌──────▼───────────────┐    │
 │  main.py      │        │  │ ESA WorldCover       │    │
 │  (FastAPI)    │ ◄──────│  │ (pre-classified map) │    │
 │               │        │  └──────────────────────┘    │
 │  ┌─────────┐  │        └──────────────────────────────┘
 │  │ router  │  │
 │  └────┬────┘  │
 │       │       │                    Processing Flow
 │  ┌────▼────────────────────────────────────────┐
 │  │ 1. geometry_utils  →  parse KML to polygon  │
 │  │ 2. earth_engine    →  send graph to Google   │
 │  │ 3. validation      →  score + decide         │
 │  │ 4. thumbnails      →  satellite + green mask  │
 │  │ 5. yield_service   →  weather + crop yield    │
 │  │ 6. recommend_crops →  top-N crop ranking      │
 │  │ 7. supabase_svc    →  save + overlap check    │
 │  └─────────────────────────────────────────────┘
 │       │                               │
 │  JSON response             ┌──────────▼────────────┐
 └───────┘                    │  Supabase (Postgres)  │
                              │  farmers / plots /    │
                              │  overlap_alerts       │
                              └───────────────────────┘
```

---

## Request Lifecycle

When a user uploads `field.kml`:

```
1. Browser POSTs KML → main.py routes to plot_validation/router.py
2. Router validates file type + size
3. geometry_utils.py parses KML → Shapely polygon → EE geometry
4. earth_engine_service.py builds computation graph:
   a. Filters Sentinel-2 collection (year, cloud, region)
   b. Computes median composite → NDVI mean + temporal stddev
   c. Loads WorldCover, creates cropland mask
   d. Intersects: cultivated = cropland AND NDVI > 0.3
   e. Fetches Sentinel-1 SAR (VH, VV) → computes VH/VV ratio + SAR crop score
   f. Fetches SRTM DEM → elevation + slope
   g. Computes area stats + per-class breakdown via reduceRegion
   h. Sends entire graph to Google via .getInfo() ← SINGLE network call
5. Weather prefetch: Open-Meteo (temp, rain, humidity, soil moisture)
6. ml_classifier.py extracts 8 features (NDVI, SAR, terrain, weather)
   → XGBoost predicts agricultural probability
   → Falls back to fused optical+SAR scoring if no model trained
7. validation_logic.py decision: PASS (>0.7), REVIEW (0.4–0.7), FAIL (<0.4)
8. Router converts m² to acres, extracts polygon coords
9. Thumbnails: satellite RGB + NDVI mask + SAR radar (3 images)
10. If claimed_crop is provided:
    a. yield_service.py fetches last 90 days of weather + soil moisture
    b. Compares actual vs ideal conditions (5 params)
    c. Estimates yield, detects critical failures
    d. Integrates yield score into overall confidence
11. Crop recommendations generated (top 5 by suitability)
12. JSON response returned to browser
```

**Key insight:** Steps 4a–4g build a _computation graph_ locally (no data moves). Only step 4h triggers actual satellite processing on Google's servers. This is called **lazy evaluation**.

---

## Module Dependencies

```
main.py
  └── plot_validation/router.py
        ├── plot_validation/schemas.py              (Pydantic models)
        ├── plot_validation/geometry_utils.py        (KML → Shapely → EE)
        ├── plot_validation/earth_engine_service.py  (S2 + S1 + DEM + thumbnails)
        ├── plot_validation/ml_classifier.py         (XGBoost classifier + fallback)
        ├── plot_validation/validation_logic.py      (ML-based scoring)
        ├── plot_validation/yield_service.py         (Kerala crop DB + Open-Meteo)
        ├── plot_validation/supabase_service.py      (farmer DB + overlap detection)
        └── config.py  (constants)

scripts/
  └── train_classifier.py  (bootstrap XGBoost training)

data/
  └── crop_classifier.json  (trained model — gitignored)
```

---

## Libraries Used

| Library                  | Purpose          | Why this library                                                 |
| ------------------------ | ---------------- | ---------------------------------------------------------------- |
| **FastAPI**              | Web framework    | Async, auto-docs, Pydantic validation, faster than Flask         |
| **Uvicorn**              | ASGI server      | Production-grade async server for FastAPI                        |
| **Pydantic**             | Data validation  | Type-safe response models, auto JSON serialization               |
| **ee** (earthengine-api) | Satellite data   | Access to petabytes of satellite imagery, server-side processing |
| **geopandas**            | Geospatial data  | Read KML, manipulate GeoDataFrames, CRS transforms               |
| **Shapely**              | Geometry objects | Polygon creation, validation, coordinate extraction, overlap     |
| **Fiona**                | File I/O         | Backend for geopandas KML reading                                |
| **XGBoost**              | ML classifier    | Gradient-boosted trees for crop/non-crop classification          |
| **scikit-learn**         | ML utilities     | Training data splits, metrics for model evaluation               |
| **python-dotenv**        | Config           | Load `.env` variables without hardcoding secrets                 |
| **requests**             | HTTP client      | Fetch EE thumbnails + Open-Meteo weather API                     |
| **supabase-py**          | Database client  | Connect to Supabase PostgreSQL for farmer/plot/alert storage     |
| **Leaflet.js**           | Map rendering    | Lightweight, open-source map library for the dashboard           |
