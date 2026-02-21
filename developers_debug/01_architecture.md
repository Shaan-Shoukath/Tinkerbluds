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
 │  └─────────────────────────────────────────────┘
 │       │
 │  JSON response
 └───────┘
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
   b. Computes median composite
   c. Calculates NDVI per pixel
   d. Loads WorldCover, creates cropland mask
   e. Intersects: cultivated = cropland AND NDVI > 0.3
   f. Computes area stats via reduceRegion
   g. Computes per-class area breakdown
   h. Sends entire graph to Google via .getInfo() ← ONLY network call
5. validation_logic.py calculates cultivated %, confidence, decision
6. Router converts m² to acres, extracts polygon coords
7. JSON response returned to browser
```

**Key insight:** Steps 4a–4g build a _computation graph_ locally (no data moves). Only step 4h triggers actual satellite processing on Google's servers. This is called **lazy evaluation**.

---

## Module Dependencies

```
main.py
  └── plot_validation/router.py
        ├── plot_validation/schemas.py        (Pydantic models)
        ├── plot_validation/geometry_utils.py  (KML → Shapely → EE)
        ├── plot_validation/earth_engine_service.py  (EE pipeline)
        ├── plot_validation/validation_logic.py (scoring)
        └── config.py  (constants)
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
| **Shapely**              | Geometry objects | Polygon creation, validation, coordinate extraction              |
| **Fiona**                | File I/O         | Backend for geopandas KML reading                                |
| **python-dotenv**        | Config           | Load `.env` variables without hardcoding secrets                 |
| **Leaflet.js**           | Map rendering    | Lightweight, open-source map library for the dashboard           |
