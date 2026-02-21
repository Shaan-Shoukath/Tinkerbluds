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
    cloud_threshold: int = 20,
) -> ee.Image:
    """
    Build a cloud-filtered median composite from Sentinel-2 Surface Reflectance.

    Bands selected: B2 (Blue), B3 (Green), B4 (Red), B8 (NIR)
    """
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"

    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR")
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
    composite = get_sentinel2_composite(region, year, cloud_threshold)
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
        scale=10,       # Sentinel-2 resolution
        maxPixels=1e9,
    ).get("area")

    # Cropland area (WorldCover class 40)
    cropland_area = pixel_area.updateMask(cropland_mask).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    ).get("area")

    # Active vegetation area (NDVI > 0.5)
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

    # --- Fetch all results from EE in one batch ---
    results = ee.Dictionary({
        "total_area": total_area,
        "cropland_area": cropland_area,
        "active_veg_area": active_veg_area,
        "cultivated_area": cultivated_area,
        "mean_ndvi": mean_ndvi,
    }).getInfo()

    logger.info("EE area stats: %s", results)

    return {
        "plot_area_sq_m":              results.get("total_area") or 0.0,
        "cropland_area_sq_m":          results.get("cropland_area") or 0.0,
        "active_vegetation_area_sq_m": results.get("active_veg_area") or 0.0,
        "cultivated_area_sq_m":        results.get("cultivated_area") or 0.0,
        "mean_ndvi":                   results.get("mean_ndvi") or 0.0,
    }
