"""
main.py — FastAPI entrypoint for the plot validation API.

Usage:
    uvicorn main:app --reload
"""

import logging
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from geometry_utils import (
    parse_kml, extract_polygon, validate_geometry, polygon_to_ee_geometry,
)
from earth_engine_service import init_ee, compute_cultivated_stats
from validation_logic import PlotValidatorStage1

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# FastAPI App
# ──────────────────────────────────────────────
app = FastAPI(
    title="Cultivated Land Validator",
    description=(
        "Upload a KML file to validate cultivated land presence "
        "using Sentinel-2 NDVI and ESA WorldCover."
    ),
    version="1.0.0",
)

# CORS — allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────
# Response schema
# ──────────────────────────────────────────────
class ValidationResponse(BaseModel):
    plot_area_acres: float
    cropland_area_acres: float
    active_vegetation_area_acres: float
    cultivated_percentage: float
    decision: str
    confidence_score: float
    dominant_class: str
    land_classes: dict
    polygon_coords: list

SQ_M_PER_ACRE = 4046.8564224


# ──────────────────────────────────────────────
# Initialize EE on startup
# ──────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    try:
        init_ee()
        logger.info("Earth Engine ready")
    except Exception as e:
        logger.error("EE init failed at startup: %s", e)


# ──────────────────────────────────────────────
# Static files + dashboard
# ──────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "cultivated-land-validator"}


# ──────────────────────────────────────────────
# POST /validate_plot
# ──────────────────────────────────────────────
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

@app.post("/validate_plot", response_model=ValidationResponse)
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
        logger.info("Starting EE processing (year=%d, months=%d-%d, cloud<%d%%)", year, start_month, end_month, cloud_threshold)
        area_stats = compute_cultivated_stats(ee_region, year, start_month, end_month, cloud_threshold)
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
    result["active_vegetation_area_acres"] = round(result.pop("active_vegetation_area_sq_m") / SQ_M_PER_ACRE, 4)

    # Convert land class areas to acres and find dominant class
    raw_classes = area_stats.get("land_classes_sq_m", {})
    land_classes_acres = {
        name: round(area / SQ_M_PER_ACRE, 4)
        for name, area in raw_classes.items()
    }
    result["land_classes"] = land_classes_acres
    result["dominant_class"] = max(raw_classes, key=raw_classes.get) if raw_classes else "Unknown"

    # Add polygon coords for map preview [lat, lon] pairs
    result["polygon_coords"] = [
        [c[1], c[0]] for c in polygon.exterior.coords
    ]

    logger.info("Validation result: %s", result)
    return result
