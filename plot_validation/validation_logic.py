"""
validation_logic.py — Stage-1 plot validation scoring and decision.

Uses ML classifier when available, falls back to threshold-based scoring.
"""

import logging
from plot_validation.ml_classifier import (
    classifier, extract_features, MLResult,
)

logger = logging.getLogger(__name__)


class PlotValidatorStage1:
    """
    Validates whether a plot contains cultivated (actively farmed) land.

    ML Mode (when model is trained):
        Features: NDVI, SAR, terrain, weather → XGBoost → agricultural probability
        Decision: PASS if prob > 0.7, REVIEW if 0.4–0.7, FAIL if < 0.4

    Fallback Mode (no trained model):
        optical_score = 0.7 × cultivated_pct + 0.3 × mean_ndvi
        fused_score   = 0.7 × optical_score + 0.3 × sar_crop_score
        decision      = PASS if fused > 0.7, REVIEW if 0.4–0.7, FAIL if < 0.4
    """

    def __init__(self, area_stats: dict, weather: dict = None):
        """
        Args:
            area_stats: dict from earth_engine_service.compute_cultivated_stats()
            weather:    dict from yield_service with rainfall + soil moisture (optional)
        """
        self.stats = area_stats
        self.weather = weather

    def validate(self) -> dict:
        plot_area = self.stats.get("plot_area_sq_m", 0.0)
        cultivated_area = self.stats.get("cultivated_area_sq_m", 0.0)

        if plot_area <= 0:
            return {
                "plot_area_sq_m": 0.0,
                "cropland_area_sq_m": 0.0,
                "active_vegetation_area_sq_m": 0.0,
                "cultivated_percentage": 0.0,
                "decision": "REVIEW",
                "confidence_score": 0.0,
                "agricultural_probability": 0.0,
                "ml_feature_importance": {},
                "using_ml": False,
            }

        # Cultivated = ESA cropland area (trusted ML classification)
        cultivated_pct = self.stats.get("cropland_area_sq_m", 0.0) / plot_area

        # --- ML Classification ---
        features = extract_features(self.stats, self.weather)
        ml_result: MLResult = classifier.predict(features, area_stats=self.stats)

        logger.info(
            "Classification: prob=%.4f decision=%s using_ml=%s",
            ml_result.agricultural_probability, ml_result.decision, ml_result.using_ml,
        )

        return {
            "plot_area_sq_m":              round(self.stats["plot_area_sq_m"], 2),
            "cropland_area_sq_m":          round(self.stats["cropland_area_sq_m"], 2),
            "active_vegetation_area_sq_m": round(self.stats["active_vegetation_area_sq_m"], 2),
            "cultivated_percentage":       round(cultivated_pct * 100, 2),
            "decision":                    ml_result.decision,
            "confidence_score":            ml_result.agricultural_probability,
            "agricultural_probability":    ml_result.agricultural_probability,
            "ml_feature_importance":       ml_result.feature_importance,
            "using_ml":                    ml_result.using_ml,
        }
