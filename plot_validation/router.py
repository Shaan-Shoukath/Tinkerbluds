"""
router.py — FastAPI router for the /validate_plot endpoint.
"""

import logging
from fastapi import APIRouter, UploadFile, File, Query, HTTPException

from config import SQ_M_PER_ACRE, MAX_FILE_SIZE
from plot_validation.schemas import (
    ValidationResponse, ConfirmPlotRequest, ConfirmPlotResponse, OverlapInfo,
)
from plot_validation.geometry_utils import (
    parse_kml, extract_polygon, validate_geometry, polygon_to_ee_geometry,
)
from plot_validation.earth_engine_service import (
    compute_cultivated_stats, generate_thumbnails,
)
from plot_validation.validation_logic import PlotValidatorStage1
from plot_validation.yield_service import estimate_yield, integrate_yield_score, recommend_crops
from plot_validation.supabase_service import (
    upsert_farmer, save_plot, check_overlap, get_overlap_alerts, resolve_alert,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Plot Validation"])


@router.post("/validate_plot", response_model=ValidationResponse)
async def validate_plot(
    file: UploadFile = File(..., description="KML file to validate"),
    start_year: int = Query(2025, ge=2015, le=2026, description="Start year for satellite imagery"),
    start_month: int = Query(1, ge=1, le=12, description="Start month (1-12)"),
    end_year: int = Query(2025, ge=2015, le=2026, description="End year for satellite imagery"),
    end_month: int = Query(12, ge=1, le=12, description="End month (1-12)"),
    cloud_threshold: int = Query(20, ge=1, le=100, description="Max cloud cover %"),
    claimed_crop: str = Query("", description="Crop claimed by farmer (e.g. rice, wheat)"),
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
            "Starting EE processing (%d-%02d to %d-%02d, cloud<%d%%)",
            start_year, start_month, end_year, end_month, cloud_threshold,
        )
        area_stats = compute_cultivated_stats(
            ee_region, start_year, start_month, end_year, end_month, cloud_threshold,
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
            start_year=start_year,
            start_month=start_month,
            end_year=end_year,
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

    # ── 8. Yield Feasibility (only if crop is claimed) ──
    if claimed_crop.strip():
        try:
            centroid = polygon.centroid
            plot_area_hectares = result["plot_area_acres"] * 0.404686
            mean_ndvi = area_stats.get("mean_ndvi", 0.0)

            yield_result = estimate_yield(
                claimed_crop=claimed_crop,
                mean_ndvi=mean_ndvi,
                lat=centroid.y,
                lon=centroid.x,
                plot_area_hectares=plot_area_hectares,
            )

            result["claimed_crop"] = yield_result["claimed_crop"]
            result["estimated_yield_ton_per_hectare"] = yield_result["estimated_yield_ton_per_hectare"]
            result["total_estimated_yield_tons"] = yield_result["total_estimated_yield_tons"]
            result["yield_feasibility_score"] = yield_result["yield_feasibility_score"]
            result["yield_confidence"] = yield_result["yield_confidence"]
            result["weather_actual"] = yield_result["weather_actual"]
            result["crop_ideal"] = yield_result["crop_ideal"]
            result["parameter_scores"] = yield_result["parameter_scores"]
            result["is_unsuitable"] = yield_result.get("is_unsuitable", False)
            result["has_critical_failure"] = yield_result.get("has_critical_failure", False)
            result["yield_warning"] = yield_result.get("yield_warning", "")
            result["unsuitability_reasons"] = yield_result.get("unsuitability_reasons", [])

            # Integrate into overall confidence
            result["confidence_score"] = integrate_yield_score(
                result["confidence_score"], yield_result["yield_feasibility_score"],
            )

            logger.info("Yield estimate: %s", yield_result["yield_confidence"])
        except Exception as e:
            logger.warning("Yield estimation failed (non-fatal): %s", e)

    # ── 9. Crop Recommendations (always, independent of claimed_crop) ──
    try:
        centroid = polygon.centroid
        mean_ndvi = area_stats.get("mean_ndvi", 0.0)
        result["recommended_crops"] = recommend_crops(
            lat=centroid.y,
            lon=centroid.x,
            mean_ndvi=mean_ndvi,
            top_n=5,
        )
    except Exception as e:
        logger.warning("Crop recommendation failed (non-fatal): %s", e)
        result["recommended_crops"] = []

    logger.info("Validation result: decision=%s", result["decision"])
    return result


# ──────────────────────────────────────────────────────────────
# POST /confirm_plot — Save farmer + plot to Supabase + overlap check
# ──────────────────────────────────────────────────────────────

@router.post("/confirm_plot", response_model=ConfirmPlotResponse)
async def confirm_plot(req: ConfirmPlotRequest):
    """
    Called when the user confirms "Yes, this is my plot".

    1. Upserts the farmer (by phone number).
    2. Saves the plot polygon + KML to Supabase.
    3. Checks for overlaps with all existing plots.
    4. Creates admin alerts if overlap > threshold.
    """
    try:
        # Guard: reject non-cultivated plots
        if req.decision and req.decision.upper() == "FAIL":
            raise HTTPException(
                status_code=400,
                detail="This plot did not pass validation (not cultivated land). "
                       "Only PASS or REVIEW plots can be saved.",
            )

        # Step 1: Upsert farmer
        farmer = upsert_farmer(
            name=req.farmer_name,
            phone=req.farmer_phone,
            email=req.farmer_email,
        )

        # Step 2: Compute effective cultivated area
        #   e.g. 10-acre plot at 70% green → store 7 acres
        cultivated_pct = max(0.0, min(100.0, req.cultivated_percentage))
        effective_area = round(req.area_acres * (cultivated_pct / 100.0), 4)
        logger.info(
            "Area adjustment: %.2f acres × %.1f%% cultivated = %.4f effective acres",
            req.area_acres, cultivated_pct, effective_area,
        )

        # Step 3: Save the plot (with adjusted area)
        plot = save_plot(
            farmer_id=farmer["id"],
            polygon_geojson=req.polygon_geojson,
            kml_data=req.kml_data,
            label=req.plot_label,
            area_acres=effective_area,
            ndvi_mean=req.ndvi_mean,
            decision=req.decision,
            confidence_score=req.confidence_score,
        )

        # Step 3: Check for overlaps
        overlaps = check_overlap(
            new_polygon_geojson=req.polygon_geojson,
            new_plot_id=plot["id"],
        )

        overlap_list = [
            OverlapInfo(
                existing_plot_id=o["existing_plot_id"],
                existing_plot_label=o.get("existing_plot_label", ""),
                existing_farmer_name=o.get("existing_farmer_name", ""),
                existing_farmer_phone=o.get("existing_farmer_phone", ""),
                overlap_pct=o["overlap_pct"],
            )
            for o in overlaps
        ]

        msg = "Plot saved successfully!"
        if overlap_list:
            msg = f"Plot saved — ⚠️ {len(overlap_list)} overlap(s) detected! Admin has been alerted."

        return ConfirmPlotResponse(
            success=True,
            farmer_id=farmer["id"],
            plot_id=plot["id"],
            message=msg,
            overlaps=overlap_list,
            has_overlap_warning=len(overlap_list) > 0,
        )

    except Exception as e:
        logger.error("confirm_plot failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────
# GET /admin/alerts — List unresolved overlap alerts
# ──────────────────────────────────────────────────────────────

@router.get("/admin/alerts")
async def admin_alerts(resolved: bool = Query(False)):
    """List overlap alerts. Default: unresolved only."""
    try:
        alerts = get_overlap_alerts(resolved=resolved)
        return {"alerts": alerts, "count": len(alerts)}
    except Exception as e:
        logger.error("admin_alerts failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/alerts/{alert_id}/resolve")
async def resolve_overlap_alert(alert_id: str):
    """Mark an overlap alert as resolved."""
    try:
        result = resolve_alert(alert_id)
        return {"success": True, "alert": result}
    except Exception as e:
        logger.error("resolve_alert failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
