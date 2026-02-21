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
