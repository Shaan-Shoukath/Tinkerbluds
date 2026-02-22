# Developer Documentation — Index

This folder contains detailed technical documentation for every module in the Tinkerbluds platform. Each file is a deep-dive into one layer of the system, written for developers who need to understand, debug, or extend the codebase.

---

## 📄 File Guide

| File                                                       | What It Covers                                                                                                                                                                                                                                                            |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [01_architecture.md](01_architecture.md)                   | **System Architecture** — High-level flow diagram, request lifecycle (KML → Geometry → EE → Score → Supabase → Response), module dependency map, and library table                                                                                                        |
| [02_kml_geometry.md](02_kml_geometry.md)                   | **KML Parsing & Geometry** — How `geometry_utils.py` reads KML files, extracts polygons, handles CRS conversion (EPSG:4326 → 6933 for accurate area), and strips Z-coordinates for EE compatibility                                                                       |
| [03_earth_engine_pipeline.md](03_earth_engine_pipeline.md) | **Earth Engine Pipeline** — Sentinel-2 compositing, cloud masking, NDVI calculation (NIR−Red)/(NIR+Red), WorldCover land classification, NDVI gradient mask thumbnail generation, lazy evaluation model, and `reduceRegion` area statistics                               |
| [04_validation_scoring.md](04_validation_scoring.md)       | **Validation & Scoring** — `PlotValidatorStage1` logic, cultivated percentage formula, confidence score weighting, PASS/REVIEW threshold, yield feasibility integration, worked examples                                                                                  |
| [05_dashboard_frontend.md](05_dashboard_frontend.md)       | **Dashboard Frontend** — Single-file `index.html` architecture, glassmorphism CSS, JavaScript state management, Leaflet map, yield feasibility section, warning banners (critical failures / unsuitability), crop recommendation cards, PASS/REVIEW/FAIL handling         |
| [06_supabase_overlap.md](06_supabase_overlap.md)           | **Supabase Integration & Overlap Detection** — Farmer DB (CRUD by phone), plot storage (GeoJSON + KML), area adjustment (cultivated %), Shapely-based overlap detection (5% threshold), FAIL guard, admin alerts, confirmation UI flow, SQL schema, API endpoints         |
| [07_yield_service.md](07_yield_service.md)                 | **Yield Service & Crop Recommendations** — 20-crop Kerala database with soil moisture ranges, Open-Meteo weather API (temp, rain, humidity, soil moisture), 5-parameter scoring, unsuitability warnings, critical failure detection, `estimate_yield` & `recommend_crops` |

---

## 🚀 Reading Order

If you're new to the codebase, read the docs in order:

1. **Architecture** (01) — understand the big picture
2. **KML Geometry** (02) — how polygons enter the system
3. **Earth Engine** (03) — the cloud processing brain
4. **Scoring** (04) — how decisions are made
5. **Dashboard** (05) — how results are displayed
6. **Supabase** (06) — farmer DB, plot storage, overlap detection
7. **Yield Service** (07) — crop feasibility, soil moisture, warnings

---

## 🔗 Quick Links

- Root README → [`../README.md`](../README.md)
- API Swagger docs → `http://localhost:8000/docs` (when server is running)
- Source code → [`../plot_validation/`](../plot_validation/)
