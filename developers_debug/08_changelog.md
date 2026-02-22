# Changelog

Chronological log of significant design changes, bug fixes, and architectural decisions.

---

## 2025-06 — Cultivated Land & Yield Timeline Overhaul

### 1. Cultivated Land = ESA WorldCover (Directly Trusted)

**Problem:**
Cultivated land was computed as `ESA cropland AND (NDVI > 0.3)`. During cloudy monsoon seasons, Sentinel-2 imagery was limited (sometimes only 1 image with severe cloud cover), producing very low NDVI values (e.g. 0.079). This caused cultivated land to report 0–2.4% even when ESA WorldCover correctly classified 100% of the plot as cropland.

An intermediate fix attempted to add SAR-backed vegetation (`NDVI > 0.3 OR SAR above threshold`), but real-world SAR values for the test plot were also below thresholds (VH = −19.48 dB, VH/VV ratio = 0.158), so the issue persisted.

**Root Cause:**
Requiring single-image thresholds on top of ESA WorldCover was redundant. ESA WorldCover v200 is trained on multi-year temporal stacks of both Sentinel-1 (radar) and Sentinel-2 (optical) data using a Random Forest classifier at 10m resolution. It already incorporates the signal that a single-scene NDVI check was trying to add — but more robustly.

**Fix:**

```python
# Before (broken in cloudy conditions)
cultivated = cropland_mask.And(active_veg)   # active_veg = NDVI > 0.3

# After (robust)
cultivated = cropland_mask   # ESA WorldCover class 40 directly
```

**Files changed:**

- `plot_validation/earth_engine_service.py` — `cultivated = cropland_mask.rename("cultivated")`
- `plot_validation/validation_logic.py` — `cultivated_pct = cropland_area_sq_m / plot_area`
- `static/index.html` — Card labels updated

**Active Vegetation repurposed:**
NDVI (and SAR stats) are now displayed as a **health indicator** only — they show how green/active the farmland is right now, but no longer gate the cultivated classification.

---

### 2. Yield Feasibility Now Uses User's Chosen Timeline

**Problem:**
`estimate_yield()` always fetched weather for either:

- The crop's growing season (hardcoded months), OR
- The last 90 days from today

This ignored the timeline the user selected in the UI (year, from-month, to-month), producing misleading feasibility results for historical or future-season analysis.

**Fix:**
Added `fetch_weather_for_period(lat, lon, start_year, start_month, end_year, end_month)` which fetches Open-Meteo historical weather for the exact user-specified date range. Updated `estimate_yield()` to accept optional timeline parameters and use a fallback chain:

1. **User timeline** → `fetch_weather_for_period()` (preferred)
2. **Crop growing season** → `fetch_weather_for_season()` (if no user timeline)
3. **Last 90 days** → `fetch_weather_last_3_months()` (final fallback)

**Files changed:**

- `plot_validation/yield_service.py` — New function + updated signature
- `plot_validation/router.py` — Passes `start_year`, `start_month`, `end_year`, `end_month` to `estimate_yield()`

---

### 3. Confidence Integration Weights

The yield feasibility integration formula in `router.py`:

```python
combined = 0.8 * base_confidence + 0.2 * yield_feasibility_score
```

This gives 80% weight to land validation (EE pipeline + ML/fused score) and 20% to crop feasibility. Previously documented as 60/40 — the docs have been corrected.

---

### 4. Frontend Label Updates

| Card            | Old Label                                   | New Label                                                               |
| --------------- | ------------------------------------------- | ----------------------------------------------------------------------- |
| Active Veg.     | "Sentinel-2 near-infrared (NDVI > 0.3)"     | "Sentinel-2 NDVI and Sentinel-1 SAR radar backscatter"                  |
| Cultivated Land | (generic)                                   | "ESA WorldCover ML-classified farmland — trained on multi-year S1 & S2" |
| Yield Section   | "ACTUAL VS IDEAL CONDITIONS (LAST 90 DAYS)" | Shows actual user-chosen period                                         |

---

## Summary of Design Principles

| Principle                  | Implementation                                                       |
| -------------------------- | -------------------------------------------------------------------- |
| Trust ML over single-scene | ESA WorldCover (multi-year RF) > single-image NDVI threshold         |
| User intent first          | Weather data uses user's chosen timeline, not hardcoded seasons      |
| Separate concerns          | Cultivated = ESA classification. Health = live NDVI. Don't conflate. |
| Graceful degradation       | 3-level weather fallback: user timeline → crop season → last 90 days |
