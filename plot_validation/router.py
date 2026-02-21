"""
router.py — FastAPI router for the /validate_plot endpoint.
"""

import logging
from fastapi import APIRouter, UploadFile, File, Query, HTTPException

from config import SQ_M_PER_ACRE, MAX_FILE_SIZE
from plot_validation.schemas import ValidationResponse
from plot_validation.geometry_utils import (
    parse_kml, extract_polygon, validate_geometry, polygon_to_ee_geometry,
)
from plot_validation.earth_engine_service import compute_cultivated_stats, generate_thumbnails
from plot_validation.validation_logic import PlotValidatorStage1

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Plot Validation"])


@router.post("/validate_plot", response_model=ValidationResponse)
async def validate_plot(
    file: UploadFile = File(..., description="KML file to validate"),
    year: int = Query(2024, ge=2015, le=2026, description="Year for satellite imagery"),
    start_month: int = Query(1, ge=1, le=12, description="Start month (1-12)"),
    end_month: int = Query(12, ge=1, le=12, description="End month (1-12)"),
    cloud_threshold: int = Query(20, ge=1, le=100, description="Max cloud cover %"),
):
    """
    Upload a KML file containing a plot polygon.
    Returns cultivated land validation results using Sentinel-2 + ESA WorldCover.
    """

    # ── 1. Validate file type ──
    if not file.filename or not file.filename.lower().endswith(".kml"):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only .kml files are accepted.",
        )

    # ── 2. Read & validate file size ──
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(content)} bytes). Max is {MAX_FILE_SIZE} bytes.",
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # ── 3. Parse KML → polygon ──
    try:
        gdf = parse_kml(content)
        polygon = extract_polygon(gdf)
        validate_geometry(polygon)
        logger.info("Parsed polygon bounds: %s", polygon.bounds)
        logger.info("Polygon vertices: %s", list(polygon.exterior.coords))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("KML parsing error")
        raise HTTPException(status_code=400, detail=f"Failed to parse KML: {e}")

    # ── 4. Convert to EE geometry ──
    try:
        ee_region = polygon_to_ee_geometry(polygon)
    except Exception as e:
        logger.exception("EE geometry conversion error")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert geometry to Earth Engine format: {e}",
        )

    # ── 5. Run Earth Engine processing pipeline ──
    try:
        logger.info(
            "Starting EE processing (year=%d, months=%d-%d, cloud<%d%%)",
            year, start_month, end_month, cloud_threshold,
        )
        area_stats = compute_cultivated_stats(
            ee_region, year, start_month, end_month, cloud_threshold,
        )
        logger.info("EE stats: %s", area_stats)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Earth Engine processing error")
        raise HTTPException(
            status_code=500,
            detail=f"Earth Engine processing failed: {e}",
        )

    # ── 6. Stage-1 validation ──
    validator = PlotValidatorStage1(area_stats)
    result = validator.validate()

    # Convert m² to acres for response
    result["plot_area_acres"] = round(result.pop("plot_area_sq_m") / SQ_M_PER_ACRE, 4)
    result["cropland_area_acres"] = round(result.pop("cropland_area_sq_m") / SQ_M_PER_ACRE, 4)
    result["active_vegetation_area_acres"] = round(
        result.pop("active_vegetation_area_sq_m") / SQ_M_PER_ACRE, 4,
    )

    # Convert land class areas to acres and find dominant class
    raw_classes = area_stats.get("land_classes_sq_m", {})
    land_classes_acres = {
        name: round(area / SQ_M_PER_ACRE, 4)
        for name, area in raw_classes.items()
    }
    result["land_classes"] = land_classes_acres
    result["dominant_class"] = (
        max(raw_classes, key=raw_classes.get) if raw_classes else "Unknown"
    )

    # Add polygon coords for map preview [lat, lon] pairs
    result["polygon_coords"] = [
        [c[1], c[0]] for c in polygon.exterior.coords
    ]

    # ── 7. Generate satellite + green mask thumbnails ──
    try:
        thumbs = generate_thumbnails(
            ee_region,
            year=year,
            start_month=start_month,
            end_month=end_month,
            cloud_threshold=cloud_threshold,
        )
        result["satellite_thumbnail"] = thumbs["satellite_b64"]
        result["green_mask_thumbnail"] = thumbs["green_mask_b64"]
        result["green_area_acres"] = round(thumbs["green_area_sq_m"] / SQ_M_PER_ACRE, 4)
    except Exception as e:
        logger.warning("Thumbnail generation failed (non-fatal): %s", e)
        result["satellite_thumbnail"] = ""
        result["green_mask_thumbnail"] = ""
        result["green_area_acres"] = 0.0

    logger.info("Validation result: decision=%s", result["decision"])
    return result
