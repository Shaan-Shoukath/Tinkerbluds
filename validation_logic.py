"""
validation_logic.py — Stage-1 plot validation scoring and decision.
"""


class PlotValidatorStage1:
    """
    Validates whether a plot contains cultivated (actively farmed) land.

    Scoring:
        cultivated_pct = cultivated_area / plot_area
        confidence     = 0.7 * cultivated_pct + 0.3 * mean_ndvi
        decision       = PASS if cultivated_pct > 0.6 else REVIEW
    """

    def __init__(self, area_stats: dict):
        """
        Args:
            area_stats: dict from earth_engine_service.compute_cultivated_stats()
                - plot_area_sq_m
                - cropland_area_sq_m
                - active_vegetation_area_sq_m
                - cultivated_area_sq_m
                - mean_ndvi
        """
        self.stats = area_stats

    def validate(self) -> dict:
        plot_area = self.stats["plot_area_sq_m"]
        cultivated_area = self.stats["cultivated_area_sq_m"]
        mean_ndvi = self.stats["mean_ndvi"]

        if plot_area <= 0:
            return {
                "plot_area_sq_m": 0.0,
                "cropland_area_sq_m": 0.0,
                "active_vegetation_area_sq_m": 0.0,
                "cultivated_percentage": 0.0,
                "decision": "REVIEW",
                "confidence_score": 0.0,
            }

        cultivated_pct = cultivated_area / plot_area

        # Confidence: weighted blend of cultivation ratio and vegetation health
        confidence_score = 0.7 * cultivated_pct + 0.3 * max(0.0, mean_ndvi)

        # Clamp to [0, 1]
        confidence_score = min(1.0, max(0.0, confidence_score))

        decision = "PASS" if cultivated_pct > 0.6 else "REVIEW"

        return {
            "plot_area_sq_m":              round(self.stats["plot_area_sq_m"], 2),
            "cropland_area_sq_m":          round(self.stats["cropland_area_sq_m"], 2),
            "active_vegetation_area_sq_m": round(self.stats["active_vegetation_area_sq_m"], 2),
            "cultivated_percentage":       round(cultivated_pct * 100, 2),
            "decision":                    decision,
            "confidence_score":            round(confidence_score, 4),
        }
