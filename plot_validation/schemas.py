"""
schemas.py — Pydantic response models for plot validation.
"""

from pydantic import BaseModel


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
