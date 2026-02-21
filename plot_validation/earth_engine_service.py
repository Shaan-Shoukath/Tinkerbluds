"""
earth_engine_service.py — Google Earth Engine initialization, Sentinel-2
composites, NDVI computation, ESA WorldCover cropland masking, and area stats.
"""

import os
import logging
import ee
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
    year: int = 2024,
    start_month: int = 1,
    end_month: int = 12,
    cloud_threshold: int = 20,
) -> ee.Image:
    """
    Build a cloud-filtered median composite from Sentinel-2 Surface Reflectance.

    Bands selected: B2 (Blue), B3 (Green), B4 (Red), B8 (NIR)
    """
    start_date = f"{year}-{start_month:02d}-01"
    # End on the last day of end_month
    if end_month == 12:
        end_date = f"{year}-12-31"
    else:
        end_date = f"{year}-{end_month + 1:02d}-01"

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
            f"No Sentinel-2 images found for the region in {year} "
            f"with cloud threshold < {cloud_threshold}%. "
            "Try increasing the cloud threshold or changing the year."
        )

    logger.info("Found %d Sentinel-2 images for %d", count, year)

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


def compute_cultivated_stats(
    region: ee.Geometry,
    year: int = 2024,
    start_month: int = 1,
    end_month: int = 12,
    cloud_threshold: int = 20,
    ndvi_threshold: float = 0.3,
) -> dict:
    """
    Full processing pipeline:
      1. Sentinel-2 median composite
      2. NDVI
      3. WorldCover cropland mask
      4. Cultivated = cropland AND (NDVI > 0.5)
      5. Area statistics via reduceRegion

    Returns dict with area values in m² and mean NDVI.
    """
    init_ee()

    # --- Step 1–3: composites and masks ---
    composite = get_sentinel2_composite(region, year, start_month, end_month, cloud_threshold)
    ndvi = compute_ndvi(composite)
    cropland_mask = get_cropland_mask(region)

    # Active vegetation: NDVI > threshold (0.3 works well for Indian agriculture)
    active_veg = ndvi.gt(ndvi_threshold).rename("active_vegetation")

    # Cultivated = cropland AND active vegetation
    cultivated = cropland_mask.And(active_veg).rename("cultivated")

    # --- Step 4: area statistics ---
    # ee.Image.pixelArea() gives the area of each pixel in m²
    pixel_area = ee.Image.pixelArea()

    # Total plot area
    total_area = pixel_area.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    ).get("area")

    # Cropland area (WorldCover class 40)
    cropland_area = pixel_area.updateMask(cropland_mask).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    ).get("area")

    # Active vegetation area (NDVI > threshold)
    active_veg_area = pixel_area.updateMask(active_veg).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    ).get("area")

    # Cultivated area (cropland ∩ active veg)
    cultivated_area = pixel_area.updateMask(cultivated).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    ).get("area")

    # Mean NDVI inside the polygon
    mean_ndvi = ndvi.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    ).get("NDVI")

    # --- Step 5: WorldCover per-class breakdown ---
    WORLDCOVER_CLASSES = {
        10: "Trees",
        20: "Shrubland",
        30: "Grassland",
        40: "Cropland",
        50: "Built-up",
        60: "Bare / Sparse Vegetation",
        80: "Permanent Water",
        90: "Herbaceous Wetland",
        95: "Mangroves",
        100: "Moss and Lichen",
    }

    worldcover_raw = ee.Image("ESA/WorldCover/v200/2021").clip(region)
    class_areas = {}
    for class_val, class_name in WORLDCOVER_CLASSES.items():
        mask = worldcover_raw.eq(class_val)
        class_areas[class_name] = pixel_area.updateMask(mask).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=region,
            scale=10,
            maxPixels=1e9,
        ).get("area")

    # --- Fetch all results from EE in one batch ---
    batch = {
        "total_area": total_area,
        "cropland_area": cropland_area,
        "active_veg_area": active_veg_area,
        "cultivated_area": cultivated_area,
        "mean_ndvi": mean_ndvi,
    }
    batch.update({f"class_{name}": val for name, val in class_areas.items()})

    results = ee.Dictionary(batch).getInfo()
    logger.info("EE area stats: %s", results)

    # Build class breakdown (only include classes with > 0 area)
    land_classes = {}
    for class_name in WORLDCOVER_CLASSES.values():
        area = results.get(f"class_{class_name}") or 0.0
        if area > 0:
            land_classes[class_name] = round(area, 2)

    return {
        "plot_area_sq_m":              results.get("total_area") or 0.0,
        "cropland_area_sq_m":          results.get("cropland_area") or 0.0,
        "active_vegetation_area_sq_m": results.get("active_veg_area") or 0.0,
        "cultivated_area_sq_m":        results.get("cultivated_area") or 0.0,
        "mean_ndvi":                   results.get("mean_ndvi") or 0.0,
        "land_classes_sq_m":           land_classes,
    }


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

