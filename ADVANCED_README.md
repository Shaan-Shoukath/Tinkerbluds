# Advanced Technical Documentation

Deep-dive into the backend architecture and classification pipeline.

---

## Architecture Overview

```
┌──────────┐    ┌─────────────────┐    ┌──────────────────┐
│ KML File │───→│ geometry_utils  │───→│  Shapely Polygon │
└──────────┘    │  parse_kml()    │    │  + EE Geometry   │
                └─────────────────┘    └────────┬─────────┘
                                                │
                    ┌───────────────────────────┘
                    ▼
         ┌──────────────────────┐
         │  earth_engine_service│     ☁️ Google Cloud
         │                      │    ┌─────────────────┐
         │  Sentinel-2 filter ──────→│ Filter 500+ imgs│
         │  NDVI compute    ────────→│ Per-pixel math  │
         │  WorldCover mask ────────→│ Lookup class 40 │
         │  Area stats      ────────→│ Sum pixel areas │
         │                  ←────────│ Return numbers  │
         └──────────┬───────────┘    └─────────────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │  validation_logic    │
         │                      │
         │  cultivated% → score │
         │  confidence  → score │
         │  decision  → PASS/REV│
         └──────────┬───────────┘
                    │
                    ▼
              JSON Response
```

The system is a **thin orchestration layer** around Google Earth Engine — your server parses the KML, sends a computation graph to Google, and formats the results. All satellite processing happens on Google's cloud.

---

## Module Breakdown

### 1. `main.py` — FastAPI Entry Point

When a request hits `POST /validate_plot`:

```python
# Validate the uploaded file
if not file.filename.endswith(".kml"):   # reject non-KML
    raise HTTPException(400)
content = await file.read()              # read raw bytes
if len(content) > 5MB:                   # size check
    raise HTTPException(400)
```

Then orchestrates 3 modules in sequence:

```
content → geometry_utils → earth_engine_service → validation_logic → JSON
```

---

### 2. `geometry_utils.py` — KML → Polygon

**Input:** Raw KML bytes &nbsp;|&nbsp; **Output:** Shapely polygon + EE geometry

```python
# geopandas needs a file path for KML, so we write to a temp file
tmp.write(file_bytes)
gdf = gpd.read_file(tmp.name, driver="KML")  # → GeoDataFrame
```

**GeoDataFrame** = pandas DataFrame with a `geometry` column. The KML's `<coordinates>` become a Shapely Polygon object.

```python
geom = gdf.geometry.iloc[0]  # first feature's geometry
# → Polygon with vertices like (76.328, 10.047), (76.329, 10.047), ...
```

**Area computation** reprojects to EPSG:6933 (equal-area CRS):

```python
gdf_proj = gdf.to_crs("EPSG:6933")  # lat/lon → metres
area = gdf_proj.geometry.iloc[0].area  # accurate m²
# Why? 1° longitude ≈ 111km at equator but ≈ 85km at Kerala's latitude
```

**EE conversion** strips Z (altitude) and wraps for Earth Engine:

```python
coords_2d = [[c[0], c[1]] for c in polygon.exterior.coords]
ee.Geometry.Polygon([coords_2d])  # server-side geometry object
```

> **Key:** `ee.Geometry` is NOT a local Python object. It's a _description_ sent to Google's servers. No computation happens locally.

---

### 3. `earth_engine_service.py` — Satellite Processing (All on Google Cloud)

This is where the heavy lifting happens. **Nothing runs locally** — you send instructions to Earth Engine, which processes terabytes of satellite data on their distributed infrastructure.

#### Step A: Initialize EE

```python
ee.Initialize(project="your-project-id")
# Authenticates with Google Cloud, opens a session
```

#### Step B: Sentinel-2 Composite

```python
collection = (
    ee.ImageCollection("COPERNICUS/S2_SR")  # ESA's satellite constellation
    .filterBounds(region)         # only images covering your polygon
    .filterDate("2024-01-01", "2024-12-31")  # only this year
    .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))  # skip cloudy days
    .select(["B2", "B3", "B4", "B8"])  # Blue, Green, Red, NIR bands
)
composite = collection.median()  # pixel-wise median of all images
```

**Why median?** If a pixel had a cloud on March 5 but was clear on March 10 and 15, the median picks the clear value. Robust cloud removal without complex algorithms.

**What are the bands?**

| Band       | Wavelength | What it sees                                    |
| ---------- | ---------- | ----------------------------------------------- |
| B2 (Blue)  | 490nm      | Water, atmosphere                               |
| B3 (Green) | 560nm      | Vegetation reflectance                          |
| B4 (Red)   | 665nm      | Plants **absorb** this → dark for vegetation    |
| B8 (NIR)   | 842nm      | Plants **reflect** this → bright for vegetation |

#### Step C: NDVI Computation

```python
ndvi = composite.normalizedDifference(["B8", "B4"])
# = (NIR - Red) / (NIR + Red)
```

Computed **per pixel** on Google's servers:

| Surface       | B8 (NIR) | B4 (Red) | NDVI      | Meaning           |
| ------------- | -------- | -------- | --------- | ----------------- |
| Healthy crops | 3000     | 500      | **0.71**  | ✅ Active farming |
| Bare soil     | 1200     | 1000     | **0.09**  | ❌ No vegetation  |
| Water         | 100      | 400      | **-0.60** | ❌ Not land       |

#### Step D: ESA WorldCover

```python
worldcover = ee.Image("ESA/WorldCover/v200/2021")  # pre-made global map
cropland = worldcover.eq(40)  # binary: 1=cropland, 0=everything else
```

A **static dataset** — ESA trained ML models on satellite data to classify every 10m pixel on Earth:

| Class        | Value  | Description               |
| ------------ | ------ | ------------------------- |
| Trees        | 10     | Forest canopy             |
| Shrubland    | 20     | Bushes, scrub             |
| Grassland    | 30     | Grass fields              |
| **Cropland** | **40** | **Agricultural farmland** |
| Built-up     | 50     | Buildings, roads          |
| Bare         | 60     | Rock, sand, desert        |
| Water        | 80     | Lakes, rivers             |

#### Step E: Area Statistics

```python
pixel_area = ee.Image.pixelArea()  # each pixel knows its area in m²

# Total = sum all pixel areas inside polygon
total = pixel_area.reduceRegion(reducer=ee.Reducer.sum(), geometry=region, scale=10)

# Cropland = sum WHERE cropland mask == 1
crop = pixel_area.updateMask(cropland).reduceRegion(...)

# Active veg = sum WHERE NDVI > 0.3
veg = pixel_area.updateMask(ndvi.gt(0.3)).reduceRegion(...)

# Cultivated = sum WHERE (cropland==1 AND ndvi>0.3)
cult = pixel_area.updateMask(cropland.And(active_veg)).reduceRegion(...)
```

> **Key:** `updateMask()` works like a SQL `WHERE` clause. It hides pixels that don't match, so `sum()` only counts qualifying pixels.

#### Step F: The `.getInfo()` Call

```python
results = ee.Dictionary({...}).getInfo()  # ← Network call happens HERE
```

Everything before this was building a **computation graph** (lazy evaluation). `.getInfo()` sends the entire graph to Google, which executes it across distributed servers, then returns the numbers. One call, all results.

---

### 4. `validation_logic.py` — Scoring & Decision

**Input:** Area stats from EE &nbsp;|&nbsp; **Output:** Decision JSON

```python
cultivated_pct = cultivated_area / plot_area
# e.g. 3735 m² / 22428 m² = 0.166 → 16.6%

confidence = 0.7 * cultivated_pct + 0.3 * mean_ndvi
# e.g. 0.7 × 0.166 + 0.3 × 0.325 = 0.214

decision = "PASS" if cultivated_pct > 0.6 else "REVIEW"
```

| Metric       | Formula                             | Threshold      |
| ------------ | ----------------------------------- | -------------- |
| Cultivated % | cultivated_area / plot_area × 100   | > 60% for PASS |
| Confidence   | 0.7 × cultivated% + 0.3 × mean_ndvi | 0–1 scale      |

---

## Supporting Modules

### `data_pull.py` — Standalone Satellite Image Downloader

Fetches a high-resolution satellite image from Google Maps Static API for a given KML. Used for visual reference only — not part of the validation pipeline.

### `segmentation.py` — Local HSV Green Masking

Pixel-level HSV colour masking to detect green areas in downloaded images. Uses OpenCV locally (no Earth Engine). This was the original approach before the EE pipeline was built.

---

## Key Concepts

### Lazy Evaluation in Earth Engine

EE uses **deferred computation**. When you write:

```python
ndvi = composite.normalizedDifference(["B8", "B4"])
```

No computation happens. You're building a graph: _"take this composite, compute normalized difference of bands B8 and B4."_ The actual processing only triggers on `.getInfo()`, `.export()`, or map visualisation.

### Why Two Signals? (WorldCover + NDVI)

Using either alone has false positives:

- **WorldCover only:** Would flag fallow farmland (no crops growing) as "cultivated"
- **NDVI only:** Would flag dense forests (high NDVI) as "cultivated"

The **intersection** ensures: _"This land IS structurally farmland AND something is actively growing"_ = confirmed cultivation.

### NDVI Threshold: 0.3 vs 0.5

- **0.5** is textbook for "dense healthy vegetation" but fails for Indian agriculture where median composite NDVI across seasons averages ~0.3–0.4
- **0.3** captures active cropland while still excluding bare soil (NDVI < 0.1) and water (NDVI < 0)
