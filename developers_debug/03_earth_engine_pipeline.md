# Earth Engine Processing Pipeline

The core satellite data pipeline — all processing runs on Google's servers.

**File:** `plot_validation/earth_engine_service.py`

---

## Data Flow

```
init_ee()
    │
    ▼
get_sentinel2_composite()         get_cropland_mask()
    │                                    │
    ▼                                    ▼
compute_ndvi()               WorldCover == 40
    │                                    │
    ▼                                    │
NDVI > 0.3 (active_veg)                 │
    │                                    │
    └──────────┬─────────────────────────┘
               ▼
        cultivated = active_veg AND cropland
               │
               ▼
        reduceRegion (area stats)
               │
               ▼
        per-class breakdown
               │
               ▼
        .getInfo()  ← single network call
```

---

## Functions

### `init_ee()`

```python
def init_ee():
    project = os.getenv("EE_PROJECT_ID")
    ee.Initialize(
        credentials=ee.ServiceAccountCredentials(...) or default,
        project=project,
    )
```

Authenticates with Google Cloud. Uses Application Default Credentials (from `earthengine authenticate`). Called once at server startup.

---

### `get_sentinel2_composite(region, year, start_month, end_month, cloud_threshold)`

**What it does:** Creates a cloud-free median composite from the Sentinel-2 satellite constellation.

```python
collection = (
    ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    .filterBounds(region)          # ① only tiles covering our polygon
    .filterDate(start, end)        # ② only our time window
    .filter(ee.Filter.lt(          # ③ reject cloudy scenes
        "CLOUDY_PIXEL_PERCENTAGE", cloud_threshold
    ))
    .select(["B2", "B3", "B4", "B8"])  # ④ only the bands we need
)
composite = collection.median()    # ⑤ pixel-wise median
```

**Step-by-step reasoning:**

| Step           | What               | Why                                                                           |
| -------------- | ------------------ | ----------------------------------------------------------------------------- |
| ① filterBounds | Spatial filter     | Sentinel-2 has 290km-wide tiles. Filter to only tiles overlapping our polygon |
| ② filterDate   | Temporal filter    | Limit to the user's chosen month range                                        |
| ③ cloud filter | Quality filter     | `CLOUDY_PIXEL_PERCENTAGE` is metadata attached to each scene by ESA           |
| ④ select       | Band filter        | We only need RGB + NIR. Dropping other bands reduces memory                   |
| ⑤ median       | Temporal composite | Merges all qualifying images into one clean image                             |

**Why median compositing?**

If Sentinel-2 captured 20 images of your plot over a year, some will have clouds. The median of each pixel across all images naturally picks the cloud-free value, because clouds (bright white) are outliers that the median ignores.

```
Image 1 pixel: 2500  (clear)
Image 2 pixel: 9000  (cloud - very bright)
Image 3 pixel: 2800  (clear)
Median = 2800 ← cloud automatically excluded
```

---

### `compute_ndvi(composite)`

```python
def compute_ndvi(composite):
    ndvi = composite.normalizedDifference(["B8", "B4"]).rename("NDVI")
    return ndvi
```

**Formula:** `NDVI = (NIR - Red) / (NIR + Red) = (B8 - B4) / (B8 + B4)`

**The science:** Chlorophyll in leaves absorbs red light (for photosynthesis) but strongly reflects near-infrared. So healthy vegetation has: high NIR, low Red → high NDVI.

| Surface      | B8 (NIR) | B4 (Red) | NDVI      | Interpretation              |
| ------------ | -------- | -------- | --------- | --------------------------- |
| Dense crops  | 3000     | 500      | **0.71**  | Healthy active farming      |
| Sparse crops | 2000     | 1000     | **0.33**  | Growing but not peak season |
| Bare soil    | 1200     | 1000     | **0.09**  | No vegetation               |
| Water        | 100      | 400      | **-0.60** | Not land                    |
| Clouds       | 8000     | 8000     | **0.00**  | Bright in both bands        |

**NDVI threshold (0.3):**

- Textbook threshold is 0.5, but that's for peak-season temperate agriculture
- Indian crops (especially paddy after harvest or between seasons) have median NDVI of 0.3–0.4
- 0.3 captures active cropland while excluding bare soil (< 0.1) and water (< 0)

---

### `get_cropland_mask(region)`

```python
def get_cropland_mask(region):
    worldcover = ee.Image("ESA/WorldCover/v200/2021").clip(region)
    cropland = worldcover.eq(40).rename("cropland")
    return cropland
```

**ESA WorldCover** is a static global land classification map at 10m resolution. ESA trained ML models on 2021 satellite data to classify every pixel on Earth.

| Class Value | Label              | Description                      |
| ----------- | ------------------ | -------------------------------- |
| 10          | Trees              | Forest canopy > 5m               |
| 20          | Shrubland          | Woody plants < 5m                |
| 30          | Grassland          | Herbaceous cover                 |
| **40**      | **Cropland**       | **Agricultural farmland**        |
| 50          | Built-up           | Buildings, roads, infrastructure |
| 60          | Bare               | Rock, sand, desert               |
| 80          | Permanent Water    | Lakes, rivers, reservoirs        |
| 90          | Herbaceous Wetland | Marshes, swamps                  |
| 95          | Mangroves          | Coastal tidal forests            |
| 100         | Moss and Lichen    | Arctic/alpine ground cover       |

**Why `.eq(40)`?** Creates a binary mask: 1 where the pixel IS cropland, 0 everywhere else. This lets us use it as a filter.

---

### `compute_cultivated_stats(region, year, start_month, end_month, cloud_threshold, ndvi_threshold)`

This is the orchestrator function. It:

1. Calls `get_sentinel2_composite()` → median image
2. Calls `compute_ndvi()` → NDVI layer
3. Calls `get_cropland_mask()` → binary cropland mask
4. Creates `active_veg = ndvi.gt(threshold)` → binary vegetation mask
5. Creates `cultivated = cropland.And(active_veg)` → intersection
6. Uses `ee.Image.pixelArea()` with `reduceRegion()` to sum areas
7. Computes per-class breakdown for all 10 WorldCover classes
8. Sends everything to Google in ONE `.getInfo()` call

**Per-class breakdown logic:**

```python
for class_val, class_name in WORLDCOVER_CLASSES.items():
    mask = worldcover_raw.eq(class_val)
    class_areas[class_name] = pixel_area.updateMask(mask).reduceRegion(...)
```

This loops through all 10 classes, creating a binary mask for each and summing the area of matching pixels within the polygon.

---

## Lazy Evaluation — The Most Important Concept

**Nothing in this file actually processes data locally.** Every line builds a _computation graph_:

```python
# This does NOT download satellite images:
collection = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region)

# This does NOT compute NDVI:
ndvi = composite.normalizedDifference(["B8", "B4"])

# This does NOT calculate areas:
total_area = pixel_area.reduceRegion(reducer=ee.Reducer.sum(), ...)

# THIS is when everything actually runs:
results = ee.Dictionary(batch).getInfo()  # ← sends graph to Google
```

**Why lazy?** Earth Engine processes petabytes of data. If every line triggered a network call, a simple analysis would take minutes. Instead, we build the entire computation as a graph, send it once, and Google's distributed infrastructure executes it efficiently.

---

## Why Two Signals? (WorldCover + NDVI)

Using either alone produces false positives:

| Scenario           | WorldCover  | NDVI > 0.3 | Intersection       | Correct? |
| ------------------ | ----------- | ---------- | ------------------ | -------- |
| Active paddy field | Cropland ✅ | High ✅    | **Cultivated** ✅  | ✅ Yes   |
| Dense forest       | Trees ❌    | High ✅    | **Not cultivated** | ✅ Yes   |
| Fallow farmland    | Cropland ✅ | Low ❌     | **Not cultivated** | ✅ Yes   |
| Construction site  | Built-up ❌ | Low ❌     | **Not cultivated** | ✅ Yes   |

The intersection catches the one scenario that matters: land that IS classified as farmland AND has something actively growing.
