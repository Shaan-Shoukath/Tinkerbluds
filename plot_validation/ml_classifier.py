"""
ml_classifier.py — XGBoost-based agricultural land classifier.

Replaces hard-coded NDVI threshold logic with a trained ML model
that uses multi-source features (optical, SAR, terrain, weather).

When no trained model exists, falls back to threshold-based scoring.
"""

import os
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Try to import xgboost; if not installed, ML mode is disabled
try:
    import xgboost as xgb
    _XGB_AVAILABLE = True
except ImportError:
    _XGB_AVAILABLE = False
    logger.info("xgboost not installed — ML classifier disabled, using threshold fallback")


# Feature names in the order expected by the model
FEATURE_NAMES = [
    "ndvi_mean",
    "ndvi_stddev",
    "vh_mean_db",
    "vh_vv_ratio",
    "elevation_m",
    "slope_deg",
    "rainfall_mm",
    "soil_moisture",
]


@dataclass
class MLResult:
    """Result from the ML classifier."""
    agricultural_probability: float  # 0.0–1.0
    decision: str                    # PASS / REVIEW / FAIL
    feature_importance: dict         # feature → importance weight
    using_ml: bool                   # True if ML model was used, False if fallback


def extract_features(area_stats: dict, weather: dict = None) -> dict:
    """
    Build the 8-feature vector from pipeline outputs.

    Args:
        area_stats: dict from compute_cultivated_stats()
        weather:    dict from yield_service.fetch_weather_last_3_months()
                    (may be None if no crop was claimed)

    Returns:
        dict with 8 feature values (some may be None if unavailable)
    """
    features = {
        "ndvi_mean":      area_stats.get("mean_ndvi", 0.0),
        "ndvi_stddev":    area_stats.get("ndvi_stddev", 0.0),
        "vh_mean_db":     area_stats.get("mean_vh_db"),
        "vh_vv_ratio":    area_stats.get("vh_vv_ratio"),
        "elevation_m":    area_stats.get("elevation_m", 0.0),
        "slope_deg":      area_stats.get("slope_deg", 0.0),
        "rainfall_mm":    weather.get("total_rainfall_mm", 0.0) if weather else 0.0,
        "soil_moisture":  weather.get("avg_soil_moisture", 0.0) if weather else 0.0,
    }
    return features


def _threshold_fallback(area_stats: dict) -> MLResult:
    """
    Original threshold-based scoring as fallback when ML model is unavailable.

    confidence = 0.7 * cultivated_pct + 0.3 * mean_ndvi
    Fused with SAR: 70% optical + 30% SAR crop score
    """
    plot_area = area_stats.get("plot_area_sq_m", 0.0)
    cultivated_area = area_stats.get("cultivated_area_sq_m", 0.0)
    mean_ndvi = area_stats.get("mean_ndvi", 0.0)
    sar_score = area_stats.get("sar_crop_score", 0.5)

    if plot_area <= 0:
        return MLResult(
            agricultural_probability=0.0,
            decision="REVIEW",
            feature_importance={},
            using_ml=False,
        )

    cultivated_pct = cultivated_area / plot_area

    # Optical confidence
    optical_score = 0.7 * cultivated_pct + 0.3 * max(0.0, mean_ndvi)

    # Fused: 70% optical + 30% SAR
    fused = 0.7 * optical_score + 0.3 * sar_score
    fused = min(1.0, max(0.0, fused))

    # Decision
    if fused > 0.7:
        decision = "PASS"
    elif fused > 0.4:
        decision = "REVIEW"
    else:
        decision = "FAIL"

    return MLResult(
        agricultural_probability=round(fused, 4),
        decision=decision,
        feature_importance={
            "cultivated_pct": 0.35,
            "mean_ndvi": 0.15,
            "sar_crop_score": 0.30,
            "elevation": 0.10,
            "slope": 0.10,
        },
        using_ml=False,
    )


class CropClassifier:
    """
    XGBoost-based agricultural land classifier.

    Loads a pre-trained model from disk.
    Falls back to threshold-based scoring if model is missing or xgboost
    is not installed.
    """

    def __init__(self, model_path: str = None):
        if model_path is None:
            model_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "data", "crop_classifier.json",
            )
        self.model_path = model_path
        self.model = None
        self._load_model()

    def _load_model(self):
        """Attempt to load the XGBoost model."""
        if not _XGB_AVAILABLE:
            logger.info("XGBoost not available — using threshold fallback")
            return

        if not os.path.exists(self.model_path):
            logger.info(
                "Model file not found at %s — using threshold fallback. "
                "Run scripts/train_classifier.py to train a model.",
                self.model_path,
            )
            return

        try:
            self.model = xgb.Booster()
            self.model.load_model(self.model_path)
            logger.info("XGBoost model loaded from %s", self.model_path)
        except Exception as e:
            logger.warning("Failed to load XGBoost model: %s — using fallback", e)
            self.model = None

    def predict(self, features: dict, area_stats: dict = None) -> MLResult:
        """
        Predict agricultural probability.

        Args:
            features: dict with 8 feature values from extract_features()
            area_stats: raw area_stats for fallback scoring

        Returns:
            MLResult with probability, decision, and feature importance
        """
        # Fallback if model not loaded
        if self.model is None:
            if area_stats is not None:
                return _threshold_fallback(area_stats)
            return MLResult(
                agricultural_probability=0.5,
                decision="REVIEW",
                feature_importance={},
                using_ml=False,
            )

        try:
            import numpy as np

            # Build feature array in correct order
            feature_values = [features.get(name, 0.0) or 0.0 for name in FEATURE_NAMES]
            dmatrix = xgb.DMatrix(
                data=np.array([feature_values]),
                feature_names=FEATURE_NAMES,
            )

            # Predict
            prob = float(self.model.predict(dmatrix)[0])
            prob = min(1.0, max(0.0, prob))

            # Decision
            if prob > 0.7:
                decision = "PASS"
            elif prob > 0.4:
                decision = "REVIEW"
            else:
                decision = "FAIL"

            # Feature importance
            importance = self.model.get_score(importance_type="gain")
            total = sum(importance.values()) or 1.0
            normalized = {k: round(v / total, 3) for k, v in importance.items()}

            return MLResult(
                agricultural_probability=round(prob, 4),
                decision=decision,
                feature_importance=normalized,
                using_ml=True,
            )
        except Exception as e:
            logger.warning("ML prediction failed: %s — using fallback", e)
            if area_stats is not None:
                return _threshold_fallback(area_stats)
            return MLResult(
                agricultural_probability=0.5,
                decision="REVIEW",
                feature_importance={},
                using_ml=False,
            )


# Global singleton classifier (loaded once at import time)
classifier = CropClassifier()
