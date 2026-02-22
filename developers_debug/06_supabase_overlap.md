# Supabase Integration, Farmer DB & Plot Overlap Detection

How plots are saved, farmers are registered, and overlapping claims are caught.

**Files:**

- `plot_validation/supabase_service.py` — DB operations + overlap logic
- `plot_validation/router.py` — `/confirm_plot`, `/admin/alerts` endpoints
- `plot_validation/schemas.py` — `ConfirmPlotRequest`, `ConfirmPlotResponse`, `OverlapInfo`

---

## System Flow

```
User uploads KML → Validates plot → Sees results
                                       │
                              "Is this your plot?"
                              ┌────────┴────────┐
                              │                 │
                           [YES]              [NO]
                              │              (done)
                              ▼
                     ┌─────────────────┐
                     │  Farmer Form    │
                     │  Name, Phone,   │
                     │  Email, Label   │
                     └────────┬────────┘
                              │
                    POST /confirm_plot
                              │
                ┌─────────────┼─────────────┐
                │             │             │
           1. Upsert     2. Save       3. Overlap
              Farmer        Plot          Check
              (phone)     (GeoJSON)    (all saved plots)
                │             │             │
                └─────────────┼─────────────┘
                              │
                     ┌────────┴────────┐
                     │                 │
                  No overlap     Overlap > 30%
                     │                 │
                 ✅ "Saved!"    ⚠️ Alert created
                                 + email button
                                   → admin@email
```

---

## Database Schema (Supabase/PostgreSQL)

### `farmers` table

| Column       | Type        | Constraints           | Notes               |
| ------------ | ----------- | --------------------- | ------------------- |
| `id`         | UUID        | Primary key, auto-gen | `gen_random_uuid()` |
| `name`       | TEXT        | NOT NULL              | Farmer's full name  |
| `phone`      | TEXT        | NOT NULL, UNIQUE      | Used as lookup key  |
| `email`      | TEXT        | Optional              |                     |
| `created_at` | TIMESTAMPTZ | Default `now()`       |                     |

### `plots` table

| Column             | Type        | Constraints                          | Notes                            |
| ------------------ | ----------- | ------------------------------------ | -------------------------------- |
| `id`               | UUID        | Primary key, auto-gen                |                                  |
| `farmer_id`        | UUID        | FK → `farmers(id)` ON DELETE CASCADE |                                  |
| `label`            | TEXT        | Optional                             | e.g. "Paddy Field North"         |
| `polygon_geojson`  | JSONB       | NOT NULL                             | GeoJSON Polygon for overlap math |
| `kml_data`         | TEXT        | Optional                             | Raw KML string for re-download   |
| `area_acres`       | FLOAT       |                                      | From validation result           |
| `ndvi_mean`        | FLOAT       |                                      | Mean NDVI at time of save        |
| `decision`         | TEXT        |                                      | PASS / REVIEW / FAIL             |
| `confidence_score` | FLOAT       |                                      | 0.0 – 1.0                        |
| `created_at`       | TIMESTAMPTZ | Default `now()`                      |                                  |

### `overlap_alerts` table

| Column             | Type        | Constraints                        | Notes                      |
| ------------------ | ----------- | ---------------------------------- | -------------------------- |
| `id`               | UUID        | Primary key, auto-gen              |                            |
| `new_plot_id`      | UUID        | FK → `plots(id)` ON DELETE CASCADE |                            |
| `existing_plot_id` | UUID        | FK → `plots(id)` ON DELETE CASCADE |                            |
| `overlap_pct`      | FLOAT       | NOT NULL                           | As decimal (0.36 = 36%)    |
| `resolved`         | BOOLEAN     | Default `false`                    | Admin can mark as resolved |
| `created_at`       | TIMESTAMPTZ | Default `now()`                    |                            |

---

## Key Functions

### `upsert_farmer(name, phone, email)`

```python
# Lookup by phone (unique key)
existing = sb.table("farmers").select("*").eq("phone", phone).execute()
if existing.data:
    return existing.data[0]  # Return existing farmer

# Otherwise create new
return sb.table("farmers").insert({...}).execute().data[0]
```

**Why upsert by phone?** Farmers may submit multiple plots. The phone number uniquely identifies a farmer, so the same person's plots are linked.

---

### `save_plot(farmer_id, polygon_geojson, kml_data, ...)`

Stores the polygon as a **JSONB GeoJSON** object. The raw KML string is also saved for potential re-download.

```python
row = {
    "farmer_id": farmer_id,
    "polygon_geojson": json.dumps(polygon_geojson),  # GeoJSON → string for JSONB
    "kml_data": kml_data,      # raw KML content
    "area_acres": area_acres,  # from validation
    ...
}
sb.table("plots").insert(row).execute()
```

---

### `check_overlap(new_polygon_geojson, new_plot_id)` ⭐ Core Algorithm

This is the most important function. It compares a new plot against **every** saved plot using Shapely geometry.

```python
def check_overlap(new_polygon_geojson, new_plot_id=None):
    new_shape = shape(new_polygon_geojson)  # GeoJSON → Shapely Polygon
    new_area = new_shape.area

    # Fetch ALL existing plots from Supabase
    existing_plots = sb.table("plots").select("id, farmer_id, polygon_geojson, label").execute()

    overlaps = []
    for existing in existing_plots:
        existing_shape = shape(json.loads(existing["polygon_geojson"]))

        # Compute geometric intersection
        intersection = new_shape.intersection(existing_shape)
        overlap_pct = intersection.area / new_area

        if overlap_pct >= OVERLAP_THRESHOLD:  # Default: 0.05 (5%)
            # Create alert in overlap_alerts table
            sb.table("overlap_alerts").insert({
                "new_plot_id": new_plot_id,
                "existing_plot_id": existing["id"],
                "overlap_pct": overlap_pct,
            }).execute()

            overlaps.append({...})  # Include farmer name/phone for display

    return overlaps
```

#### How overlap is calculated:

```
                    ┌──────────────┐
                    │  New Plot    │
                    │    (A)       │
              ┌─────┤──────┐      │
              │     │██████│      │
              │     │██████│←── Intersection (I)
              │     │██████│      │
              │     └──────┤──────┘
              │  Existing  │
              │  Plot (B)  │
              └────────────┘

    overlap_pct = area(I) / area(A)

    If overlap_pct ≥ 0.05 → ⚠️ ALERT
```

**Why overlap_pct = I/A (not I/B)?** We measure "what fraction of the NEW plot overlaps with existing claims." This catches cases where a small plot is entirely inside a larger one (100% overlap), even though the large plot only loses a small percentage.

---

### Overlap Threshold

```python
OVERLAP_THRESHOLD = 0.05  # 5%
```

Defined at top of `supabase_service.py`. Adjustable — lower values catch more overlaps but may produce false positives for neighboring plots that share boundaries.

| Threshold | Catches                                      |
| --------- | -------------------------------------------- |
| **0.05**  | **Even small boundary overlaps (current)**   |
| 0.10      | Most overlaps without excessive false alarms |
| 0.30      | Significant overlaps only                    |
| 0.50      | Only catches major overlaps                  |
| 0.80      | Only near-complete duplicates                |

---

## API Endpoints

### `POST /confirm_plot`

**Request body** (`ConfirmPlotRequest`):

```json
{
  "farmer_name": "Rajan Kumar",
  "farmer_phone": "+919876543210",
  "farmer_email": "rajan@email.com",
  "plot_label": "Paddy Field North",
  "polygon_geojson": {"type": "Polygon", "coordinates": [[[76.1, 10.2], ...]]},
  "kml_data": "<?xml version=\"1.0\"?>...",
  "area_acres": 10.0,
  "cultivated_percentage": 70.0,
  "ndvi_mean": 0.72,
  "decision": "PASS",
  "confidence_score": 0.85
}
```

> **FAIL Guard:** Plots with `decision: "FAIL"` are rejected with HTTP 400. Only PASS/REVIEW plots can be saved.
>
> **Area Adjustment:** The stored area = `area_acres × cultivated_percentage / 100`. A 10-acre plot at 70% green → **7 acres** stored in Supabase.

**Response** (`ConfirmPlotResponse`):

```json
{
  "success": true,
  "farmer_id": "uuid-...",
  "plot_id": "uuid-...",
  "message": "Plot saved — ⚠️ 1 overlap(s) detected!",
  "has_overlap_warning": true,
  "overlaps": [
    {
      "existing_plot_id": "uuid-...",
      "existing_plot_label": "My Maize Field",
      "existing_farmer_name": "Shaan Shoukath",
      "existing_farmer_phone": "1234567890",
      "overlap_pct": 0.364
    }
  ]
}
```

### `GET /admin/alerts?resolved=false`

Returns all unresolved overlap alerts with linked plot/farmer data.

### `POST /admin/alerts/{alert_id}/resolve`

Marks an alert as resolved (admin action).

---

## Dashboard UI Flow

### 1. Confirmation Prompt

After validation results are shown, a card appears:

```
┌──────────────────────────────────────────┐
│  📌 PLOT CONFIRMATION                    │
│                                          │
│  Is this your proposed plot?             │
│                                          │
│  [✅ Yes, this is my plot]  [❌ No]      │
└──────────────────────────────────────────┘
```

### 2. Farmer Registration Form (shown on "Yes")

```
┌──────────────────────────────────────────┐
│  👤 FARMER REGISTRATION                  │
│                                          │
│  Full Name *     │  Phone Number *       │
│  ┌─────────────┐ │  ┌─────────────────┐  │
│  │             │ │  │ +91 9876543210  │  │
│  └─────────────┘ │  └─────────────────┘  │
│  Email (opt)     │  Plot Label (opt)     │
│  ┌─────────────┐ │  ┌─────────────────┐  │
│  │             │ │  │ Paddy Field N   │  │
│  └─────────────┘ │  └─────────────────┘  │
│                                          │
│  [💾 Save & Register Plot]               │
└──────────────────────────────────────────┘
```

### 3. Result Display

**Success (no overlap):**

```
         ✅
  Plot Saved Successfully!
  Plot ID: abc-123-def
```

**Overlap detected:**

```
         ⚠️
  Plot Saved — Overlap Detected!

  ┌──────────────────────────────┐
  │ ⚠️ Overlap: 36.4%           │
  │ Overlaps with "My Maize     │
  │ Field" by Shaan (1234567890)│
  └──────────────────────────────┘

  [📧 Report to Admin]
```

The **Report to Admin** button opens a pre-filled `mailto:shaanshoukath44@gmail.com` with the overlap details.

---

## Environment Variables

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=eyJ...service-role-key...
```

Both loaded via `python-dotenv` in `main.py` before any module imports.

---

## Security Notes

- **Service role key** is used (not anon key) for full DB access without RLS restrictions
- **`.env` is gitignored** — keys never go to source control
- **Row Level Security** is enabled on all tables with service-role bypass policies
- **Phone uniqueness** prevents duplicate farmer records
