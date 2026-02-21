# Developer Documentation — Index

This folder contains detailed technical documentation for every module in the Tinkerbluds platform. Each file is a deep-dive into one layer of the system, written for developers who need to understand, debug, or extend the codebase.

---

## 📄 File Guide

| File                                                       | What It Covers                                                                                                                                                                                                                                                  |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [01_architecture.md](01_architecture.md)                   | **System Architecture** — High-level flow diagram, request lifecycle (KML → Geometry → EE → Score → Response), module dependency map, and library table                                                                                                         |
| [02_kml_geometry.md](02_kml_geometry.md)                   | **KML Parsing & Geometry** — How `geometry_utils.py` reads KML files, extracts polygons, handles CRS conversion (EPSG:4326 → 6933 for accurate area), and strips Z-coordinates for EE compatibility                                                             |
| [03_earth_engine_pipeline.md](03_earth_engine_pipeline.md) | **Earth Engine Pipeline** — Sentinel-2 compositing, cloud masking, NDVI calculation (NIR−Red)/(NIR+Red), WorldCover land classification, green mask thumbnail generation, lazy evaluation model, and `reduceRegion` area statistics                             |
| [04_validation_scoring.md](04_validation_scoring.md)       | **Validation & Scoring** — `PlotValidatorStage1` logic, cultivated percentage formula, confidence score weighting (0.7 × cultivated% + 0.3 × NDVI), PASS/REVIEW threshold, worked examples with real numbers                                                    |
| [05_dashboard_frontend.md](05_dashboard_frontend.md)       | **Dashboard Frontend** — Single-file `index.html` architecture, glassmorphism CSS design system, JavaScript state management (upload → processing → results), Leaflet map integration, base64 thumbnail rendering, land class bar chart, yield comparison table |

---

## 🧩 Modules Not Yet Documented

The following modules were added after the initial documentation sprint. Their logic is documented via inline comments and docstrings in the source code:

| Module                   | Location                                            | What It Does                                                                                                                                                                                      |
| ------------------------ | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Yield Service**        | `plot_validation/yield_service.py`                  | 20-crop ideal-conditions dataset, Open-Meteo weather API integration (last 90 days), parameter-by-parameter comparison (temp, rainfall, humidity, NDVI), yield estimation and feasibility scoring |
| **Green Mask**           | `earth_engine_service.py` → `generate_thumbnails()` | Binary vegetation mask — solid green for NDVI > 0.3 pixels, dark greyscale for everything else, clipped to polygon boundary                                                                       |
| **Satellite Thumbnails** | `earth_engine_service.py` → `generate_thumbnails()` | True-color RGB thumbnail from Sentinel-2 B4/B3/B2 bands via EE's `getThumbURL()`                                                                                                                  |

---

## 🚀 Reading Order

If you're new to the codebase, read the docs in order:

1. **Architecture** (01) — understand the big picture
2. **KML Geometry** (02) — how polygons enter the system
3. **Earth Engine** (03) — the cloud processing brain
4. **Scoring** (04) — how decisions are made
5. **Dashboard** (05) — how results are displayed

Then refer to `yield_service.py` source code for the crop yield feasibility module.

---

## 🔗 Quick Links

- Root README → [`../README.md`](../README.md)
- API Swagger docs → `http://localhost:8000/docs` (when server is running)
- Source code → [`../plot_validation/`](../plot_validation/)
