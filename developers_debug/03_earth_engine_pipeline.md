# Earth Engine Processing Pipeline

The core satellite data pipeline — all processing runs on Google's servers.

**File:** `plot_validation/earth_engine_service.py`

---

## Data Flow

```
Optical (Sentinel-2)              SAR (Sentinel-1)              Terrain (SRTM)
    │                                    │                          │
get_sentinel2_composite()      get_sentinel1_composite()     get_terrain_stats()
    │                                    │                          │
compute_ndvi()                 compute_sar_stats()                  │
    │                           VH, VV, VH/VV ratio                 │
    ▼                                    │                          │
NDVI > 0.3 (active_veg)                 │                          │
  [health indicator only]               │                          │
    │                                    │                          │
    ├── get_cropland_mask()              │                          │
    │   WorldCover == 40                 │                          │
    │                                    │                          │
    └──── cultivated = cropland          │                          │
         (ESA trusted directly)          │                          │
               │                         │                          │
               ▼                         ▼                          ▼
         ┌─────────────────────────────────────────────────────────────┐
         │         ee.Dictionary(batch).getInfo()                     │
         │         SINGLE batched network call for ALL features       │
         └─────────────────────────────────────────────────────────────┘
```

### Why Cultivated = ESA Cropland Directly?

The previous logic was `cultivated = cropland AND (NDVI > 0.3)`, but this
failed catastrophically when optical imagery was limited or cloudy:

| Scenario                               | NDVI | Old Result       | New Result   |
| -------------------------------------- | ---- | ---------------- | ------------ |
| 1 cloudy S2 image, ESA says 100% crops | 0.08 | **0% cultiv.**   | 100% cultiv. |
| Monsoon season, few clear images       | 0.15 | **2–5% cultiv.** | 100% cultiv. |
| Clear sky, active crops visible        | 0.55 | 100% cultiv.     | 100% cultiv. |
| Forest area (ESA = Trees)              | 0.72 | 0% (correct)     | 0% (correct) |

ESA WorldCover v200 is a 10m ML classification trained on **multi-year**
Sentinel-1 + Sentinel-2 temporal stacks. It already incorporates SAR,
optical, and temporal NDVI in its training pipeline. Our single-image
NDVI threshold was redundant and broke when imagery was limited.

**Active Vegetation (NDVI > 0.3)** is now kept as a **separate health
indicator** — shown in the dashboard for reference but NOT used as a gate
for cultivated land.

---

## Why We Use Three Data Sources

NDVI alone can't distinguish farms from forests (both are green). Adding SAR radar and terrain data solves this:

| Scenario           | NDVI alone    | + WorldCover            | + SAR Radar        | + Terrain | Correct?    |
| ------------------ | ------------- | ----------------------- | ------------------ | --------- | ----------- |
| Active paddy field | ✅ Green      | ✅ Cropland             | ✅ High VH/VV      | ✅ Flat   | ✅          |
| Dense forest       | ❌ Also green | ✅ Trees (not cropland) | ✅ Low VH/VV       | ✅ Sloped | ✅          |
| Fallow farmland    | ❌ Low NDVI   | ✅ Cropland             | ⚠️ Moderate        | ✅ Flat   | ✅ (REVIEW) |
| Construction site  | ✅ Low        | ✅ Built-up             | ✅ Very high VH/VV | -         | ✅          |

---

## Optical: Sentinel-2 (NDVI)

### `get_sentinel2_composite(region, year, start_month, end_month, cloud_threshold)`

Creates a cloud-free median composite from Sentinel-2 optical imagery.

```python
collection = (
    ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    .filterBounds(region)          # ① only tiles covering our polygon
    .filterDate(start, end)        # ② only our time window
    .filter(ee.Filter.lt(          # ③ reject cloudy scenes
        "CLOUDY_PIXEL_PERCENTAGE", cloud_threshold
    ))
    .select(["B2", "B3", "B4", "B8"])  # ④ only RGB + NIR bands
)
composite = collection.median()    # ⑤ pixel-wise median removes clouds
```

**Why median compositing?** If 20 images were captured, some will have clouds. The median of each pixel naturally picks the cloud-free value because clouds (very bright) are outliers.

### `compute_ndvi(composite)`

```
NDVI = (NIR - Red) / (NIR + Red) = (B8 - B4) / (B8 + B4)
```

| Surface      | NDVI      | Interpretation              |
| ------------ | --------- | --------------------------- |
| Dense crops  | **0.71**  | Healthy active farming      |
| Sparse crops | **0.33**  | Growing but not peak season |
| Bare soil    | **0.09**  | No vegetation               |
| Water        | **-0.60** | Not land                    |

**NDVI threshold (0.3):** Indian crops (especially paddy between seasons) have median NDVI of 0.3–0.4. This captures active cropland while excluding bare soil and water.

---

## SAR Radar: Sentinel-1

### What is SAR?

**Synthetic Aperture Radar** is an active sensor — the satellite sends out microwave pulses and measures the reflected signal. Unlike optical imagery (cameras), SAR:

- ☁️ **Works through clouds** — microwaves penetrate cloud cover completely
- 🌙 **Works at night** — doesn't need sunlight (active illumination)
- 🌿 **Senses canopy structure** — different vegetation types scatter radar differently

### How the Data is Used

**Sentinel-1** is a C-band SAR satellite (5.4 GHz) that captures images in two polarizations:

| Polarization             | Name                                  | What It Measures                        | Typical Range |
| ------------------------ | ------------------------------------- | --------------------------------------- | ------------- |
| **VH** (cross-polarized) | Vertical transmit, Horizontal receive | Volume scattering in vegetation canopy  | -20 to -8 dB  |
| **VV** (co-polarized)    | Vertical transmit, Vertical receive   | Surface scattering (bare ground, water) | -15 to -5 dB  |

**The VH/VV ratio** is the key discriminator:

| Surface             | VH (dB) | VV (dB) | VH/VV Ratio   | Why                                         |
| ------------------- | ------- | ------- | ------------- | ------------------------------------------- |
| Crops (paddy/wheat) | -12     | -7      | **0.4–0.65**  | Crop rows create moderate volume scattering |
| Dense forest        | -10     | -6      | **0.15–0.35** | Tree trunks cause strong co-pol reflections |
| Urban/concrete      | -5      | -3      | **0.7–0.9**   | Hard corners cause strong multi-bounce      |
| Water               | -25     | -20     | **0.1–0.2**   | Smooth surface reflects away from satellite |

### Why SAR for Crop Detection?

The fundamental problem: **forests and farms both look green to optical satellites** (both have high NDVI). SAR solves this because:

1. **Crop canopy structure** — row crops (rice, wheat) have a distinct scattering pattern (moderate VH, moderate VH/VV ~0.5) that's different from random forest canopy or smooth urban surfaces
2. **Temporal consistency** — SAR data is available regardless of cloud cover, solving the tropical India problem where monsoon clouds block Sentinel-2 for months
3. **Penetration depth** — C-band SAR partially penetrates crop canopy, revealing the soil-vegetation interface that optical can't see

### `get_sentinel1_composite(region, start_year, start_month, end_year, end_month)`

```python
collection = (
    ee.ImageCollection("COPERNICUS/S1_GRD")
    .filterBounds(region)
    .filterDate(start, end)
    .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
    .filter(ee.Filter.eq("instrumentMode", "IW"))  # Interferometric Wide swath
    .select(["VH", "VV"])
)
composite = collection.median()
```

**S1_GRD** = Sentinel-1 Ground Range Detected (backscatter intensity in dB). **IW** mode = Interferometric Wide swath (250km width, 5×20m resolution) — the standard mode over land.

### `compute_sar_stats(region, s1_composite)`

Extracts mean VH and VV values using `reduceRegion(mean)`:

```python
mean_vh = s1_composite.select("VH").reduceRegion(mean, region, scale=10)
mean_vv = s1_composite.select("VV").reduceRegion(mean, region, scale=10)
vh_vv_ratio = 10^((mean_vh - mean_vv) / 10)  # convert dB difference to linear ratio
```

### `compute_sar_crop_score(vh_db, vh_vv_ratio)`

Rule-based scoring (0.0–1.0):

```
VH/VV score: peaks at 0.5 (crop canopy sweet spot), 60% weight
VH intensity score: peaks at -12 dB (moderate scattering), 40% weight

sar_crop_score = 0.6 × vh_vv_score + 0.4 × vh_score
```

---

## Terrain: SRTM DEM

### `get_terrain_stats(region)`

```python
dem = ee.Image("USGS/SRTMGL1_003").clip(region)
elevation = dem.select("elevation")
slope = ee.Terrain.slope(dem)
```

Returns `elevation_m` and `slope_deg` via `reduceRegion(mean)`.

**Why terrain?**

- Steep slopes (>15°) are difficult to farm mechanically
- High elevation (>1500m) limits crop options significantly
- Mountain forests often get misclassified as cropland by NDVI alone
- Flat, low-elevation areas are more likely to be agricultural

---

## NDVI Temporal Standard Deviation

The **most important ML feature** (71.6% of model's decision weight).

```python
ndvi_collection = (
    ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    .map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI"))
)
ndvi_stddev = ndvi_collection.reduce(ee.Reducer.stdDev())
```

**Why this matters:**

| Land Type        | NDVI Mean | NDVI StdDev   | Pattern                              |
| ---------------- | --------- | ------------- | ------------------------------------ |
| Active farm      | 0.5–0.7   | **0.10–0.25** | Changes: planting → growth → harvest |
| Evergreen forest | 0.6–0.8   | **0.01–0.05** | Always green, minimal change         |
| Fallow field     | 0.1–0.3   | **0.05–0.10** | Some seasonal weed growth            |

Forests stay green year-round (low variability). Crops cycle through seasons (high variability). This single feature is the strongest signal for distinguishing farms from forests.

---

## WorldCover Land Classification

### `get_cropland_mask(region)`

```python
worldcover = ee.Image("ESA/WorldCover/v200/2021").clip(region)
cropland = worldcover.eq(40).rename("cropland")
```

**ESA WorldCover** is a static 10m-resolution land classification map (2021 data, ML-classified):

| Class  | Label           | Description                      |
| ------ | --------------- | -------------------------------- |
| 10     | Trees           | Forest canopy > 5m               |
| 20     | Shrubland       | Woody plants < 5m                |
| 30     | Grassland       | Herbaceous cover                 |
| **40** | **Cropland**    | **Agricultural farmland**        |
| 50     | Built-up        | Buildings, roads, infrastructure |
| 60     | Bare            | Rock, sand, desert               |
| 80     | Permanent Water | Lakes, rivers, reservoirs        |

---

## `compute_cultivated_stats()` — The Orchestrator

Collects ALL features in a **single batched** `.getInfo()` call:

```python
batch = {
    "total_area":      total_area_stat,      # pixel areas summed
    "cropland_area":   cropland_area_stat,   # WorldCover == 40 (= cultivated)
    "active_veg_area": active_veg_stat,      # NDVI > 0.3 (health indicator)
    "cultivated_area": cultivated_stat,      # = cropland (ESA trusted directly)
    "mean_ndvi":       mean_ndvi_stat,       # NDVI mean
    "ndvi_stddev":     ndvi_stddev_stat,     # temporal NDVI std deviation
    "mean_vh":         sar_vh_stat,          # Sentinel-1 VH
    "mean_vv":         sar_vv_stat,          # Sentinel-1 VV
    "elevation":       elevation_stat,       # SRTM elevation
    "slope":           slope_stat,           # SRTM slope
    # ... + per WorldCover class areas
}
results = ee.Dictionary(batch).getInfo()     # ← ONE network call
```

> **Design Note:** `cultivated_area` now equals `cropland_area` since cultivated
> is directly mapped from ESA WorldCover. Active vegetation (NDVI > 0.3) is
> tracked separately as a health metric. The `cultivated_percentage` in the
> validation response is calculated as `cropland_area / total_area`.

**Why batch?** Each `.getInfo()` call takes 3–8 seconds (network latency + cloud computation). By batching all 15+ statistics into one call, we avoid making 15 separate round trips. Total time ≈ 5–10 seconds instead of 45–120 seconds.

---

## Thumbnails

### `generate_thumbnails(region, ...)`

Generates 2 optical thumbnails via `getThumbURL()`:

1. **Satellite RGB** — true-color view (B4/B3/B2)
2. **NDVI gradient mask** — green = dense vegetation, brown = bare soil

### `generate_sar_thumbnail(region, s1_composite)`

Generates a SAR radar backscatter thumbnail with blue-to-orange gradient:

- **Blue** = low backscatter (water, smooth surfaces)
- **Orange** = high backscatter (rough surfaces, dense vegetation)

---

## Lazy Evaluation — The Most Important Concept

**Nothing in this file processes data locally.** Every line builds a _computation graph_:

```python
# This does NOT download satellite images:
collection = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region)

# This does NOT compute NDVI:
ndvi = composite.normalizedDifference(["B8", "B4"])

# THIS is when everything actually runs:
results = ee.Dictionary(batch).getInfo()  # ← sends entire graph to Google
```

**Why lazy?** Earth Engine hosts petabytes of data. If every line triggered a network call, analysis would take minutes. Instead, we build the entire computation graph locally, send it once, and Google's distributed infrastructure executes it efficiently — often processing terabytes in under 10 seconds.
