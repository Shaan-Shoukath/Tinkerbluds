"""
earth_engine_service.py — Google Earth Engine initialization, Sentinel-2
composites, NDVI computation, ESA WorldCover cropland masking, and area stats.
"""

import os
import logging
import base64
import ee
import requests as http_requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_ee_initialized = False


def init_ee() -> None:
    """
    Initialize the Earth Engine API exactly once.
    Uses the project ID from .env (EE_PROJECT_ID).
    """
    global _ee_initialized
    if _ee_initialized:
        return

    load_dotenv()
    project_id = os.getenv("EE_PROJECT_ID")

    try:
        if project_id:
            ee.Initialize(project=project_id)
            logger.info("Earth Engine initialized with project: %s", project_id)
        else:
            ee.Initialize()
            logger.info("Earth Engine initialized (default project)")
        _ee_initialized = True
    except Exception as exc:
        logger.error("Failed to initialize Earth Engine: %s", exc)
        raise RuntimeError(
            "Could not initialize Earth Engine. "
            "Have you run 'earthengine authenticate'?"
        ) from exc


def get_sentinel2_composite(
    region: ee.Geometry,
    start_year: int = 2024,
    start_month: int = 1,
    end_year: int = 2024,
    end_month: int = 12,
    cloud_threshold: int = 20,
) -> ee.Image:
    """
    Build a cloud-filtered median composite from Sentinel-2 Surface Reflectance.

    Supports cross-year ranges (e.g. Nov 2025 → Feb 2026).
    Bands selected: B2 (Blue), B3 (Green), B4 (Red), B8 (NIR)
    """
    start_date = f"{start_year}-{start_month:02d}-01"
    # End on the last day of end_month
    if end_month == 12:
        end_date = f"{end_year}-12-31"
    else:
        end_date = f"{end_year}-{end_month + 1:02d}-01"

    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_threshold))
        .select(["B2", "B3", "B4", "B8"])
    )

    # Check if any images were found
    count = collection.size().getInfo()
    if count == 0:
        raise ValueError(
            f"No Sentinel-2 images found for {start_year}-{start_month:02d} to "
            f"{end_year}-{end_month:02d} with cloud threshold < {cloud_threshold}%. "
            "Try increasing the cloud threshold or changing the date range."
        )

    logger.info("Found %d Sentinel-2 images for %d-%02d to %d-%02d", count, start_year, start_month, end_year, end_month)

    # Median composite reduces cloud/shadow artefacts
    composite = collection.median().clip(region)
    return composite


def compute_ndvi(composite: ee.Image) -> ee.Image:
    """
    Compute Normalised Difference Vegetation Index.
    NDVI = (B8 - B4) / (B8 + B4)
    Range: -1 (water/bare) to +1 (dense vegetation)
    """
    ndvi = composite.normalizedDifference(["B8", "B4"]).rename("NDVI")
    return ndvi


def get_cropland_mask(region: ee.Geometry) -> ee.Image:
    """
    Get the ESA WorldCover v200 cropland mask for the region.
    Cropland class value = 40.
    Returns a binary image: 1 where cropland, 0 elsewhere.
    """
    worldcover = ee.Image("ESA/WorldCover/v200/2021").clip(region)
    cropland = worldcover.eq(40).rename("cropland")
    return cropland


# ──────────────────────────────────────────────────────────────
# Sentinel-1 SAR (Radar) — penetrates clouds, detects crop structure
# ──────────────────────────────────────────────────────────────

def get_sentinel1_composite(
    region: ee.Geometry,
    start_year: int = 2024,
    start_month: int = 1,
    end_year: int = 2024,
    end_month: int = 12,
) -> ee.Image:
    """
    Build a median composite from Sentinel-1 GRD (Ground Range Detected).

    Uses IW (Interferometric Wide) mode with VH + VV polarization.
    Returns median composite clipped to region at 10m resolution.
    """
    start_date = f"{start_year}-{start_month:02d}-01"
    if end_month == 12:
        end_date = f"{end_year}-12-31"
    else:
        end_date = f"{end_year}-{end_month + 1:02d}-01"

    collection = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select(["VH", "VV"])
    )

    count = collection.size().getInfo()
    logger.info("Found %d Sentinel-1 images for %d-%02d to %d-%02d", count, start_year, start_month, end_year, end_month)

    if count == 0:
        logger.warning("No Sentinel-1 images found — SAR data will be unavailable")
        return None

    return collection.median().clip(region)


def compute_sar_stats(region: ee.Geometry, s1_composite: ee.Image) -> dict:
    """
    Compute SAR statistics over the polygon using reduceRegion.

    Returns ee.Dictionary entries (lazy — not yet fetched):
        mean_vh_db:   mean VH backscatter (dB)
        mean_vv_db:   mean VV backscatter (dB)
        vh_vv_ratio:  VH/VV ratio (linear, higher = more likely crop)
    """
    if s1_composite is None:
        return {}

    # VH and VV are in dB; compute ratio in linear space
    vh_linear = ee.Image(10).pow(s1_composite.select("VH").divide(10))
    vv_linear = ee.Image(10).pow(s1_composite.select("VV").divide(10))
    ratio = vh_linear.divide(vv_linear).rename("vh_vv_ratio")

    stack = s1_composite.addBands(ratio)

    result = stack.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    )

    return {
        "mean_vh_db": result.get("VH"),
        "mean_vv_db": result.get("VV"),
        "vh_vv_ratio": result.get("vh_vv_ratio"),
    }


def compute_sar_crop_score(vh_vv_ratio: float, mean_vh_db: float) -> float:
    """
    Score how likely the radar signature indicates cropland (0.0–1.0).

    Cropland: higher VH/VV ratio (> 0.5), VH > -12 dB (rough surface)
    Forest:   lower VH/VV ratio (< 0.3), more uniform backscatter
    Water:    very low VH (< -20 dB)
    """
    if vh_vv_ratio is None or mean_vh_db is None:
        return 0.5  # neutral when no SAR data

    score = 0.0

    # VH/VV ratio component (0–0.6)
    if vh_vv_ratio > 0.5:
        score += 0.6
    elif vh_vv_ratio > 0.3:
        score += 0.3 + 0.3 * ((vh_vv_ratio - 0.3) / 0.2)
    else:
        score += max(0.0, vh_vv_ratio)

    # VH intensity component (0–0.4)
    if mean_vh_db > -12:
        score += 0.4  # rough/vegetated surface
    elif mean_vh_db > -18:
        score += 0.2 + 0.2 * ((mean_vh_db + 18) / 6)
    else:
        score += max(0.0, 0.1 + 0.1 * ((mean_vh_db + 20) / 2))

    return round(min(1.0, max(0.0, score)), 4)


# ──────────────────────────────────────────────────────────────
# Terrain statistics (SRTM DEM)
# ──────────────────────────────────────────────────────────────

def get_terrain_stats(region: ee.Geometry) -> dict:
    """
    Get elevation and slope from SRTM 30m DEM.
    Returns ee.Dictionary entries (lazy):
        elevation_m: mean elevation in meters
        slope_deg:   mean slope in degrees
    """
    dem = ee.Image("USGS/SRTMGL1_003")
    slope = ee.Terrain.slope(dem)

    stack = dem.rename("elevation").addBands(slope.rename("slope"))

    result = stack.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=30,
        maxPixels=1e9,
    )

    return {
        "elevation_m": result.get("elevation"),
        "slope_deg": result.get("slope"),
    }


def compute_cultivated_stats(
    region: ee.Geometry,
    start_year: int = 2024,
    start_month: int = 1,
    end_year: int = 2024,
    end_month: int = 12,
    cloud_threshold: int = 20,
    ndvi_threshold: float = 0.3,
) -> dict:
    """
    Full processing pipeline:
      1. Sentinel-2 median composite → NDVI (mean + stddev)
      2. Sentinel-1 GRD composite → SAR stats (VH, VV, VH/VV ratio)
      3. SRTM DEM → elevation, slope
      4. WorldCover cropland mask
      5. Cultivated = cropland AND (NDVI > threshold)
      6. Area statistics via reduceRegion (single batched getInfo)

    Returns dict with area values in m², NDVI, SAR, and terrain stats.
    """
    init_ee()

    # --- Optical: Sentinel-2 ---
    composite = get_sentinel2_composite(region, start_year, start_month, end_year, end_month, cloud_threshold)
    ndvi = compute_ndvi(composite)
    cropland_mask = get_cropland_mask(region)

    # --- SAR: Sentinel-1 ---
    s1_composite = get_sentinel1_composite(region, start_year, start_month, end_year, end_month)
    sar_stats = compute_sar_stats(region, s1_composite)

    # --- Active vegetation mask (NDVI + SAR) ---
    # This is a real-time health indicator, NOT the cultivated land gate.
    # NDVI and SAR can both miss vegetation due to cloud contamination,
    # limited imagery, or seasonal spectral dips.
    active_veg = ndvi.gt(ndvi_threshold).rename("active_vegetation")

    # --- Cultivated = ESA WorldCover cropland (directly) ---
    # ESA WorldCover v200 is a 10m ML classification trained on multi-year
    # Sentinel-1 + Sentinel-2 temporal stacks.  It already incorporates
    # SAR + optical + temporal NDVI in its training pipeline, so adding
    # our own single-image threshold on top is redundant and fails on
    # cloudy/limited imagery.  We trust the ESA classification for
    # "cultivated land" and use active_veg as a separate health metric.
    cultivated = cropland_mask.rename("cultivated")

    pixel_area = ee.Image.pixelArea()

    # Area statistics (lazy)
    total_area = pixel_area.reduceRegion(
        reducer=ee.Reducer.sum(), geometry=region, scale=10, maxPixels=1e9,
    ).get("area")

    cropland_area = pixel_area.updateMask(cropland_mask).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=region, scale=10, maxPixels=1e9,
    ).get("area")

    active_veg_area = pixel_area.updateMask(active_veg).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=region, scale=10, maxPixels=1e9,
    ).get("area")

    cultivated_area = pixel_area.updateMask(cultivated).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=region, scale=10, maxPixels=1e9,
    ).get("area")

    mean_ndvi = ndvi.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=10, maxPixels=1e9,
    ).get("NDVI")

    # NDVI standard deviation (temporal variability — crops fluctuate, forests don't)
    ndvi_collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(
            f"{start_year}-{start_month:02d}-01",
            f"{end_year}-12-31" if end_month == 12 else f"{end_year}-{end_month + 1:02d}-01",
        )
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_threshold))
        .map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI"))
    )
    ndvi_stddev = ndvi_collection.reduce(ee.Reducer.stdDev()).rename("NDVI_stddev")
    mean_ndvi_stddev = ndvi_stddev.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=10, maxPixels=1e9,
    ).get("NDVI_stddev")

    # --- WorldCover per-class breakdown ---
    WORLDCOVER_CLASSES = {
        10: "Trees", 20: "Shrubland", 30: "Grassland", 40: "Cropland",
        50: "Built-up", 60: "Bare / Sparse Vegetation",
        80: "Permanent Water", 90: "Herbaceous Wetland",
        95: "Mangroves", 100: "Moss and Lichen",
    }

    worldcover_raw = ee.Image("ESA/WorldCover/v200/2021").clip(region)
    class_areas = {}
    for class_val, class_name in WORLDCOVER_CLASSES.items():
        mask = worldcover_raw.eq(class_val)
        class_areas[class_name] = pixel_area.updateMask(mask).reduceRegion(
            reducer=ee.Reducer.sum(), geometry=region, scale=10, maxPixels=1e9,
        ).get("area")

    # --- Terrain: SRTM ---
    terrain_stats = get_terrain_stats(region)

    # --- Single batched getInfo() call ---
    batch = {
        "total_area": total_area,
        "cropland_area": cropland_area,
        "active_veg_area": active_veg_area,
        "cultivated_area": cultivated_area,
        "mean_ndvi": mean_ndvi,
        "ndvi_stddev": mean_ndvi_stddev,
    }
    batch.update({f"class_{name}": val for name, val in class_areas.items()})
    batch.update(sar_stats)     # mean_vh_db, mean_vv_db, vh_vv_ratio
    batch.update(terrain_stats)  # elevation_m, slope_deg

    results = ee.Dictionary(batch).getInfo()
    logger.info("EE stats (optical+SAR+terrain): %s", results)

    # Build class breakdown
    land_classes = {}
    for class_name in WORLDCOVER_CLASSES.values():
        area = results.get(f"class_{class_name}") or 0.0
        if area > 0:
            land_classes[class_name] = round(area, 2)

    # SAR crop score
    vh_vv = results.get("vh_vv_ratio")
    vh_db = results.get("mean_vh_db")
    sar_score = compute_sar_crop_score(vh_vv, vh_db)

    return {
        "plot_area_sq_m":              results.get("total_area") or 0.0,
        "cropland_area_sq_m":          results.get("cropland_area") or 0.0,
        "active_vegetation_area_sq_m": results.get("active_veg_area") or 0.0,
        "cultivated_area_sq_m":        results.get("cultivated_area") or 0.0,
        "mean_ndvi":                   results.get("mean_ndvi") or 0.0,
        "ndvi_stddev":                 results.get("ndvi_stddev") or 0.0,
        "land_classes_sq_m":           land_classes,
        # SAR
        "mean_vh_db":                  round(vh_db, 2) if vh_db else None,
        "mean_vv_db":                  round(results.get("mean_vv_db") or 0, 2) if results.get("mean_vv_db") else None,
        "vh_vv_ratio":                 round(vh_vv, 4) if vh_vv else None,
        "sar_crop_score":              sar_score,
        # Terrain
        "elevation_m":                 round(results.get("elevation_m") or 0, 1),
        "slope_deg":                   round(results.get("slope_deg") or 0, 1),
        # S1 composite reference for thumbnail
        "_s1_composite":               s1_composite,
    }


def generate_thumbnails(
    region: ee.Geometry,
    composite: ee.Image = None,
    ndvi: ee.Image = None,
    start_year: int = 2024,
    start_month: int = 1,
    end_year: int = 2024,
    end_month: int = 12,
    cloud_threshold: int = 20,
    ndvi_threshold: float = 0.3,
    thumb_width: int = 512,
) -> dict:
    """
    Generate satellite + green mask thumbnails for the polygon.

    Returns dict with:
      - satellite_b64: base64 PNG of true-color satellite image
      - green_mask_b64: base64 PNG of green vegetation overlay
      - green_area_sq_m: area of NDVI > threshold in m²
    """
    init_ee()

    # Build composite/NDVI if not provided
    if composite is None:
        composite = get_sentinel2_composite(region, start_year, start_month, end_year, end_month, cloud_threshold)
    if ndvi is None:
        ndvi = compute_ndvi(composite)

    # ── True-color thumbnail (B4=Red, B3=Green, B2=Blue) ──
    rgb_vis = {
        "bands": ["B4", "B3", "B2"],
        "min": 0,
        "max": 3000,
        "dimensions": thumb_width,
        "region": region,
        "format": "png",
    }
    rgb_url = composite.getThumbURL(rgb_vis)
    logger.info("Fetching satellite thumbnail: %s", rgb_url)
    rgb_response = http_requests.get(rgb_url, timeout=60)
    satellite_b64 = base64.b64encode(rgb_response.content).decode("utf-8")

    # ── Green mask thumbnail ──
    # Gradient visualization: maps NDVI to green intensity.
    # - NDVI < 0 (water/bare): dark
    # - NDVI 0–0.3 (sparse):   very dim green
    # - NDVI 0.3–0.6:          moderate green
    # - NDVI 0.6–0.9:          bright green
    # This is far more informative than a binary on/off mask.

    # Clamp NDVI to [0, 1] range for visualization
    ndvi_clamped = ndvi.clamp(0, 1)

    # Build gradient: green channel scales with NDVI, red/blue stay low
    green_channel = ndvi_clamped.multiply(4000)       # 0 → 0, 1.0 → 4000
    red_channel   = ndvi_clamped.multiply(400)        # slight warm tint
    blue_channel  = ee.Image.constant(0)

    # For pixels below threshold: show dark greyscale base
    grey = composite.select(["B4", "B3", "B2"]).reduce(ee.Reducer.mean())
    dark_base = ee.Image.cat(
        grey.multiply(0.2), grey.multiply(0.2), grey.multiply(0.2),
    ).rename(["vis-red", "vis-green", "vis-blue"])

    # Green gradient overlay
    green_gradient = ee.Image.cat(
        red_channel, green_channel, blue_channel,
    ).rename(["vis-red", "vis-green", "vis-blue"])

    # NDVI-based vegetation highlight
    active_veg = ndvi.gt(ndvi_threshold)

    # Where NDVI > threshold → gradient green; otherwise → dark greyscale
    highlighted = dark_base.where(active_veg, green_gradient).clip(region)

    mask_vis = {
        "bands": ["vis-red", "vis-green", "vis-blue"],
        "min": 0,
        "max": 4000,
        "dimensions": thumb_width,
        "region": region,
        "format": "png",
    }
    mask_url = highlighted.getThumbURL(mask_vis)
    logger.info("Fetching green mask thumbnail: %s", mask_url)
    mask_response = http_requests.get(mask_url, timeout=60)
    green_mask_b64 = base64.b64encode(mask_response.content).decode("utf-8")

    # ── Green area calculation ──
    pixel_area = ee.Image.pixelArea()
    green_area = pixel_area.updateMask(active_veg).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    ).get("area")

    green_area_val = ee.Number(green_area).getInfo() or 0.0

    return {
        "satellite_b64": satellite_b64,
        "green_mask_b64": green_mask_b64,
        "green_area_sq_m": round(green_area_val, 2),
    }


def generate_sar_thumbnail(
    region: ee.Geometry,
    s1_composite: ee.Image,
    thumb_width: int = 512,
) -> str:
    """
    Generate a SAR (Sentinel-1 VH) thumbnail with blue-orange gradient.

    Blue = low backscatter (water/smooth), Orange = high (rough/vegetated).
    Returns base64-encoded PNG string.
    """
    if s1_composite is None:
        return ""

    init_ee()

    # VH band, typical range: -25 to -5 dB
    vh = s1_composite.select("VH")

    # Normalize to [0, 1] range
    vh_norm = vh.subtract(-25).divide(20).clamp(0, 1)

    # Blue-to-orange gradient
    red_channel = vh_norm.multiply(255)
    green_channel = vh_norm.multiply(140)
    blue_channel = ee.Image.constant(255).subtract(vh_norm.multiply(255))

    rgb = ee.Image.cat(
        red_channel, green_channel, blue_channel,
    ).rename(["vis-red", "vis-green", "vis-blue"]).clip(region)

    vis = {
        "bands": ["vis-red", "vis-green", "vis-blue"],
        "min": 0,
        "max": 255,
        "dimensions": thumb_width,
        "region": region,
        "format": "png",
    }
    url = rgb.getThumbURL(vis)
    logger.info("Fetching SAR thumbnail: %s", url)
    response = http_requests.get(url, timeout=60)
    return base64.b64encode(response.content).decode("utf-8")


# ──────────────────────────────────────────────────────────────
# Vegetation type breakdown (WorldCover)
# ──────────────────────────────────────────────────────────────

WORLDCOVER_CLASSES = {
    10: "Tree Cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare / Sparse Vegetation",
    80: "Permanent Water Bodies",
    90: "Herbaceous Wetland",
    95: "Mangroves",
}


def get_vegetation_breakdown(region: ee.Geometry) -> dict:
    """
    Get pixel-count breakdown of ESA WorldCover classes inside the region.
    Returns dict like: {"Cropland": 45.2, "Tree Cover": 30.1, ...} (percentages).
    """
    init_ee()
    worldcover = ee.Image("ESA/WorldCover/v200/2021").clip(region)

    # Count pixels per class using a frequency histogram
    hist = worldcover.reduceRegion(
        reducer=ee.Reducer.frequencyHistogram(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    ).get("Map").getInfo()

    if not hist:
        return {}

    # Convert pixel counts to percentages
    total_pixels = sum(hist.values())
    if total_pixels == 0:
        return {}

    breakdown = {}
    for class_val_str, count in hist.items():
        class_val = int(class_val_str)
        class_name = WORLDCOVER_CLASSES.get(class_val, f"Unknown ({class_val})")
        pct = round(count / total_pixels * 100, 1)
        if pct > 0:
            breakdown[class_name] = pct

    # Sort by percentage descending
    breakdown = dict(sorted(breakdown.items(), key=lambda x: x[1], reverse=True))
    logger.info("Vegetation breakdown: %s", breakdown)
    return breakdown


# ──────────────────────────────────────────────────────────────
# Soil statistics (OpenLandMap)
# ──────────────────────────────────────────────────────────────

def get_soil_stats(region: ee.Geometry) -> dict:
    """
    Get mean soil properties for the region using OpenLandMap datasets.
    Returns: sand_pct, clay_pct, ph
    All values are surface level (0cm depth band: b0).
    """
    init_ee()

    sand = ee.Image("OpenLandMap/SOL/SOL_SAND-WFRACTION_USDA-3A1A1A_M/v02").select("b0")
    clay = ee.Image("OpenLandMap/SOL/SOL_CLAY-WFRACTION_USDA-3A1A1A_M/v02").select("b0")
    ph   = ee.Image("OpenLandMap/SOL/SOL_PH-H2O_USDA-4C1A2A_M/v02").select("b0")

    stack = sand.rename("sand").addBands(clay.rename("clay")).addBands(ph.rename("ph"))

    results = stack.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=250,       # OpenLandMap resolution
        maxPixels=1e9,
    ).getInfo()

    # pH is stored as pH × 10 in the dataset
    raw_ph = results.get("ph") or 0
    soil = {
        "sand_pct": round(results.get("sand") or 0, 1),
        "clay_pct": round(results.get("clay") or 0, 1),
        "ph":       round(raw_ph / 10.0, 1),
    }
    logger.info("Soil stats: %s", soil)
    return soil


# ──────────────────────────────────────────────────────────────
# Climate statistics (TerraClimate)
# ──────────────────────────────────────────────────────────────

def get_climate_stats(region: ee.Geometry, year: int = 2024) -> dict:
    """
    Get annual mean temperature and total rainfall from TerraClimate.
    Returns: temp_c (°C, annual mean), rainfall_mm (mm, annual total)
    """
    init_ee()

    # TerraClimate is monthly — filter to the requested year
    tc = (
        ee.ImageCollection("IDAHO_EPSCOR/TERRACLIMATE")
        .filterDate(f"{year}-01-01", f"{year}-12-31")
    )

    # Mean temperature across all months (tmmx + tmmn / 2, stored as °C × 10)
    mean_temp = tc.select("tmmx").mean()  # mean of monthly max temps
    total_precip = tc.select("pr").sum()  # sum of monthly precipitation

    stack = mean_temp.rename("temp").addBands(total_precip.rename("precip"))

    results = stack.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=4000,      # TerraClimate resolution ~4km
        maxPixels=1e9,
    ).getInfo()

    # tmmx is stored as °C × 10
    raw_temp = results.get("temp") or 0
    climate = {
        "temp_c":       round(raw_temp / 10.0, 1),
        "rainfall_mm":  round(results.get("precip") or 0, 0),
    }
    logger.info("Climate stats: %s", climate)
    return climate

