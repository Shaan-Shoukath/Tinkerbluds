# Validation & Scoring Logic

How we turn raw satellite features into a PASS/REVIEW/FAIL decision.

**Files:** `plot_validation/validation_logic.py`, `plot_validation/ml_classifier.py`

---

## Data Flow

```
Earth Engine Stats
    │
    ├── NDVI mean + stddev
    ├── SAR VH, VV, VH/VV ratio
    ├── Elevation, slope
    └── WorldCover class areas
         │
         ▼
  extract_features() → 8-feature vector
         │
         ▼
  ┌──────────────────────────────────────┐
  │  ML Model Trained?                   │
  │   YES → XGBoost prediction           │
  │   NO  → Fused optical+SAR fallback   │
  └──────────────────────────────────────┘
         │
         ▼
  agricultural_probability (0.0 → 1.0)
         │
    > 0.7 → PASS
    0.4–0.7 → REVIEW
    < 0.4 → FAIL
```

---

## ML Classifier: `ml_classifier.py`

### The 8 Features

| #   | Feature         | Source                | Why It Matters                                                   |
| --- | --------------- | --------------------- | ---------------------------------------------------------------- |
| 1   | `ndvi_mean`     | Sentinel-2            | How green the land is                                            |
| 2   | `ndvi_stddev`   | Sentinel-2 (temporal) | **Crops fluctuate seasonally, forests don't** (71.6% importance) |
| 3   | `vh_mean_db`    | Sentinel-1 SAR        | Radar backscatter intensity                                      |
| 4   | `vh_vv_ratio`   | Sentinel-1 SAR        | Distinguishes crop canopy from forests/urban                     |
| 5   | `elevation_m`   | SRTM DEM              | High elevation = less likely farmland                            |
| 6   | `slope_deg`     | SRTM DEM              | Steep slopes = hard to farm                                      |
| 7   | `rainfall_mm`   | Open-Meteo            | Too dry = less viable                                            |
| 8   | `soil_moisture` | Open-Meteo            | Active farming needs adequate moisture                           |

### Feature Extraction

```python
def extract_features(area_stats: dict, weather: dict = None) -> dict:
    return {
        "ndvi_mean":     area_stats.get("mean_ndvi", 0.0),
        "ndvi_stddev":   area_stats.get("ndvi_stddev", 0.0),
        "vh_mean_db":    area_stats.get("mean_vh_db"),
        "vh_vv_ratio":   area_stats.get("vh_vv_ratio"),
        "elevation_m":   area_stats.get("elevation_m", 0.0),
        "slope_deg":     area_stats.get("slope_deg", 0.0),
        "rainfall_mm":   weather.get("total_rainfall_mm", 0.0) if weather else 0.0,
        "soil_moisture": weather.get("avg_soil_moisture", 0.0) if weather else 0.0,
    }
```

### XGBoost Model

The model is an XGBoost gradient-boosted decision tree classifier, stored as `data/crop_classifier.json`.

**Why XGBoost?**

- Best-in-class for tabular data (8 columns)
- Handles missing features natively (if SAR is unavailable)
- Provides feature importance (explains decisions)
- ~1ms inference, no GPU, ~50KB model file

**Training:** `python scripts/train_classifier.py --samples 500`

Uses synthetic but science-backed bootstrap data (cropland/forest/urban/water distribution). Retrain with real user-confirmed plots for improved accuracy.

### Fused Threshold Fallback

When no trained model exists (`data/crop_classifier.json` missing), the system uses a rule-based fusion:

```
optical_score = 0.7 × cultivated_pct + 0.3 × max(0, mean_ndvi)
fused_score   = 0.7 × optical_score  + 0.3 × sar_crop_score

→ fused_score becomes the agricultural_probability
```

> **Note:** `cultivated_pct` is now `cropland_area / total_area` — derived
> directly from ESA WorldCover (class 40). It no longer requires NDVI > 0.3
> gating, so 100% ESA-classified cropland = 100% cultivated.

| Weight          | Component      | What It Measures                             |
| --------------- | -------------- | -------------------------------------------- |
| 49% (0.7 × 0.7) | Cultivated %   | ESA WorldCover cropland fraction of the plot |
| 21% (0.7 × 0.3) | Mean NDVI      | Vegetation health (real-time indicator)      |
| 30%             | SAR crop score | Radar-based crop structure                   |

---

## Validator: PlotValidatorStage1

```python
class PlotValidatorStage1:
    def __init__(self, stats: dict, weather: dict = None):
        self.stats = stats
        self.weather = weather

    def validate(self) -> dict:
        features = extract_features(self.stats, self.weather)
        ml_result = classifier.predict(features, area_stats=self.stats)

        return {
            "decision":                 ml_result.decision,
            "confidence_score":         ml_result.agricultural_probability,
            "agricultural_probability": ml_result.agricultural_probability,
            "ml_feature_importance":    ml_result.feature_importance,
            "using_ml":                 ml_result.using_ml,
            # ... plus area stats
        }
```

### Decision Thresholds

| Probability | Decision   | Reasoning                             | Can Save to Supabase? |
| ----------- | ---------- | ------------------------------------- | --------------------- |
| > 0.7       | **PASS**   | High confidence of active farmland    | ✅ Yes                |
| 0.4 – 0.7   | **REVIEW** | Mixed signals — may need human review | ✅ Yes                |
| < 0.4       | **FAIL**   | Unlikely to be agricultural land      | ❌ Blocked            |

---

## Example Calculations

### Case 1: Active Paddy Field (Palakkad) — Fused Fallback

```
NDVI mean:        0.55
Cultivated %:     68.2%
SAR crop score:   0.75

optical_score = 0.7 × 0.682 + 0.3 × 0.55 = 0.642
fused_score   = 0.7 × 0.642 + 0.3 × 0.75 = 0.674

Decision: REVIEW (0.674 < 0.7)
```

### Case 2: Dense Forest (Western Ghats) — Fused Fallback

```
NDVI mean:        0.72  (forest is very green)
Cultivated %:     0%    (WorldCover = Trees, not Cropland)
SAR crop score:   0.25  (low VH/VV ratio = forest canopy)

optical_score = 0.7 × 0.0 + 0.3 × 0.72 = 0.216
fused_score   = 0.7 × 0.216 + 0.3 × 0.25 = 0.226

Decision: FAIL (0.226 < 0.4)
```

Note: Without SAR, the forest's high NDVI (0.72) would have inflated the score. SAR's low VH/VV ratio correctly pulls it down.

### Case 3: ML Model Active

When the XGBoost model is trained, `using_ml = True`:

```
Features: [0.55, 0.18, -12.0, 0.45, 150, 3.5, 450, 0.32]

XGBoost prediction: 0.89 probability (cropland)
Feature importance: { ndvi_stddev: 71.6%, vh_vv_ratio: 14.5%, ... }

Decision: PASS (0.89 > 0.7)
```

The ML model uses all 8 features holistically, especially leveraging NDVI temporal variability which the threshold fallback cannot.

---

## Unit Conversion (in `router.py`)

EE returns everything in **square metres**. Router converts to **acres**:

```python
SQ_M_PER_ACRE = 4046.8564224  # International Yard and Pound Agreement (1959)
result["plot_area_acres"] = round(result.pop("plot_area_sq_m") / SQ_M_PER_ACRE, 4)
```

---

## Yield Feasibility Integration (in `router.py`)

When `claimed_crop` is provided, yield score integrates into confidence:

```python
def integrate_yield_score(base_confidence, yield_feasibility_score):
    """Combined score = 80% land validation + 20% crop feasibility"""
    return round(0.8 * base_confidence + 0.2 * yield_feasibility_score, 4)
```

### Yield Parameter Weights

```
overall_yield = 25% × temperature
             + 25% × rainfall
             + 10% × humidity
             + 15% × soil_moisture
             + 25% × vegetation (NDVI)
```

### Warning Levels

| Condition                   | Label               | UI Treatment       |
| --------------------------- | ------------------- | ------------------ |
| Overall < 40%               | **NOT RECOMMENDED** | 🚫 Red banner      |
| Any parameter ≤ 5%          | **POOR YIELD**      | ⚠️ Orange banner   |
| Overall 40–75%, no critical | **MODERATE**        | Yellow score badge |
| Overall ≥ 75%               | **HIGH**            | Green score badge  |

> For full details on the yield scoring, crop database, and warning system, see [07_yield_service.md](07_yield_service.md).
