# Validation & Scoring Logic

How we turn raw area numbers into a PASS/REVIEW decision.

**File:** `plot_validation/validation_logic.py`

---

## Data Flow

```
EE Stats (m²)
    │
    ▼
PlotValidatorStage1
    ├── cultivated_pct = cultivated_area / plot_area
    ├── confidence = 0.7 × cultivated_pct + 0.3 × mean_ndvi
    └── decision = "PASS" if cultivated_pct > 0.6 else "REVIEW"
    │
    ▼
Result dict (m²) → router converts to acres
```

---

## Class: `PlotValidatorStage1`

```python
class PlotValidatorStage1:
    def __init__(self, stats: dict):
        self.stats = stats

    def validate(self) -> dict:
        plot_area = self.stats["plot_area_sq_m"]
        cultivated_area = self.stats["cultivated_area_sq_m"]
        mean_ndvi = self.stats["mean_ndvi"]

        cultivated_pct = cultivated_area / plot_area
        confidence_score = 0.7 * cultivated_pct + 0.3 * max(0.0, mean_ndvi)

        decision = "PASS" if cultivated_pct > 0.6 else "REVIEW"

        return {
            "plot_area_sq_m": plot_area,
            "cropland_area_sq_m": self.stats["cropland_area_sq_m"],
            "active_vegetation_area_sq_m": self.stats["active_vegetation_area_sq_m"],
            "cultivated_percentage": round(cultivated_pct * 100, 2),
            "decision": decision,
            "confidence_score": round(confidence_score, 4),
        }
```

---

## Scoring Formula Explained

### Cultivated Percentage

```
cultivated_pct = cultivated_area / plot_area
```

- `cultivated_area` = pixels where (WorldCover == Cropland) AND (NDVI > 0.3)
- `plot_area` = total polygon area
- Expressed as 0–100% in the response

### Confidence Score

```
confidence = 0.7 × cultivated_pct + 0.3 × mean_ndvi
```

| Component    | Weight    | Range   | Reasoning                                               |
| ------------ | --------- | ------- | ------------------------------------------------------- |
| Cultivated % | 0.7 (70%) | 0.0–1.0 | Primary signal: how much of the plot is actively farmed |
| Mean NDVI    | 0.3 (30%) | 0.0–1.0 | Secondary signal: how healthy the vegetation is         |

**Why this weighting?**

- Coverage matters more than health. A plot where 80% is farmed but crops are slightly stressed is more clearly "cultivated" than a plot with 10% farmed but very healthy crops.
- Mean NDVI adds nuance: two plots with 65% cultivation but different vegetation health should score differently.
- `max(0.0, mean_ndvi)` clamps negative NDVI (water bodies) to zero.

### Decision Thresholds

| Cultivated % | Decision   | Reasoning                                                  |
| ------------ | ---------- | ---------------------------------------------------------- |
| > 60%        | **PASS**   | More than half the plot is confirmed active farmland       |
| ≤ 60%        | **REVIEW** | Could be forest, fallow, mixed-use, or partial cultivation |

**Why 60%?**

- Not 50% because small patches of cropland inside a forest shouldn't pass
- Not 80% because many farms have paths, buildings, ponds taking up ~20-30%
- 60% gives a balanced threshold

---

## Example Calculations

### Case 1: Active Paddy Field (Palakkad)

```
Plot area:       22,000 m²
Cropland:        18,000 m²  (WorldCover says it's farmland)
Active veg:      16,000 m²  (NDVI > 0.3)
Cultivated:      15,000 m²  (intersection)
Mean NDVI:       0.45

cultivated_pct = 15000 / 22000 = 0.682 → 68.2%
confidence     = 0.7 × 0.682 + 0.3 × 0.45 = 0.612
decision       = PASS  (68.2% > 60%)
```

### Case 2: Forest Plot (Western Ghats)

```
Plot area:       22,428 m²
Cropland:        0 m²       (WorldCover says Trees, not Cropland)
Active veg:      17,150 m²  (forest is green, NDVI > 0.3)
Cultivated:      0 m²       (0 AND 17150 = 0)
Mean NDVI:       0.376

cultivated_pct = 0 / 22428 = 0.0 → 0.0%
confidence     = 0.7 × 0.0 + 0.3 × 0.376 = 0.113
decision       = REVIEW  (0% ≤ 60%)
```

---

## Unit Conversion (in `router.py`)

The EE pipeline returns everything in **square metres**. The router converts to **acres** before responding:

```python
SQ_M_PER_ACRE = 4046.8564224

result["plot_area_acres"] = round(result.pop("plot_area_sq_m") / SQ_M_PER_ACRE, 4)
```

The conversion constant `4046.8564224` is the exact value defined by the International Yard and Pound Agreement (1959).

---

## Dominant Class Detection (in `router.py`)

```python
raw_classes = area_stats.get("land_classes_sq_m", {})
result["dominant_class"] = max(raw_classes, key=raw_classes.get)
```

Python's `max()` with `key=dict.get` finds the class name with the largest area value. This tells the user what the land primarily is (e.g. "Trees", "Cropland").

---

## Yield Feasibility Integration (in `router.py`)

When `claimed_crop` is provided, the yield score is integrated into the overall confidence:

```python
def integrate_yield_score(base_confidence, yield_feasibility_score):
    """Combined score = 60% land validation + 40% crop feasibility"""
    return round(0.6 * base_confidence + 0.4 * yield_feasibility_score, 4)
```

| Source            | Weight | What it measures                 |
| ----------------- | ------ | -------------------------------- |
| Land validation   | 60%    | Is this actually cultivated land |
| Yield feasibility | 40%    | Can the claimed crop grow here   |

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
