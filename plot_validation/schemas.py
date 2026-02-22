"""
schemas.py — Pydantic response models for plot validation.
"""

from pydantic import BaseModel
from typing import Optional


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
    satellite_thumbnail: str = ""
    green_mask_thumbnail: str = ""
    green_area_acres: float = 0.0
    # Yield feasibility
    claimed_crop: str = ""
    estimated_yield_ton_per_hectare: float = 0.0
    total_estimated_yield_tons: float = 0.0
    yield_feasibility_score: float = 0.0
    yield_confidence: str = ""
    weather_actual: dict = {}
    crop_ideal: dict = {}
    parameter_scores: dict = {}
    # Unsuitability warnings
    is_unsuitable: bool = False
    has_critical_failure: bool = False
    yield_warning: str = ""
    unsuitability_reasons: list = []
    # Crop recommendations
    recommended_crops: list = []


# ─── Plot Confirmation & Farmer Registration ──────────────────

class ConfirmPlotRequest(BaseModel):
    """Sent when user confirms 'Yes, this is my plot'."""
    farmer_name: str
    farmer_phone: str
    farmer_email: str = ""
    plot_label: str = ""
    # Polygon + KML passed from the previous validation result
    polygon_geojson: dict
    kml_data: str = ""
    # Validation stats (from the previous result)
    area_acres: float = 0.0
    cultivated_percentage: float = 100.0  # % of plot that is cultivated (green)
    ndvi_mean: float = 0.0
    decision: str = ""
    confidence_score: float = 0.0


class OverlapInfo(BaseModel):
    existing_plot_id: str
    existing_plot_label: str = ""
    existing_farmer_name: str = ""
    existing_farmer_phone: str = ""
    overlap_pct: float


class ConfirmPlotResponse(BaseModel):
    """Response after confirming and saving a plot."""
    success: bool
    farmer_id: str = ""
    plot_id: str = ""
    message: str = ""
    overlaps: list[OverlapInfo] = []
    has_overlap_warning: bool = False
