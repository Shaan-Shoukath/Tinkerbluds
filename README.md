# Tinkerbluds – Cultivated Land Validator

REST API that validates cultivated land inside a user-uploaded KML plot using **Google Earth Engine** (Sentinel-2 + ESA WorldCover).

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Authenticate Earth Engine

```bash
earthengine authenticate
```

### 3. Configure `.env`

```env
EE_PROJECT_ID=your-gee-project-id
GOOGLE_MAPS_API_KEY=your-google-maps-key   # only for data_pull.py
```

### 4. Run the server

```bash
uvicorn main:app --reload
```

Server starts at `http://localhost:8000` — docs at `http://localhost:8000/docs`.

---

## API

### `POST /validate_plot`

| Parameter         | Type       | Default    | Description                        |
| ----------------- | ---------- | ---------- | ---------------------------------- |
| `file`            | KML upload | _required_ | Plot polygon KML file              |
| `year`            | int        | 2024       | Satellite imagery year (2015–2025) |
| `cloud_threshold` | int        | 20         | Max cloud cover %                  |

**Example request:**

```bash
curl -X POST http://localhost:8000/validate_plot \
  -F "file=@plot.kml" \
  -F "year=2024" \
  -F "cloud_threshold=20"
```

**Example response:**

```json
{
  "plot_area_sq_m": 18543.21,
  "cropland_area_sq_m": 12050.88,
  "active_vegetation_area_sq_m": 15200.5,
  "cultivated_percentage": 64.99,
  "decision": "PASS",
  "confidence_score": 0.6149
}
```

---

## Project Structure

```
main.py                  FastAPI entrypoint
geometry_utils.py        KML → polygon → EE geometry
earth_engine_service.py  Sentinel-2, NDVI, WorldCover
validation_logic.py      Stage-1 scoring & decision
data_pull.py             Standalone satellite image puller
segmentation.py          Local HSV green mask (non-API)
```

## Decision Logic

- **cultivated_percentage** = cultivated area / plot area × 100
- **confidence_score** = 0.7 × cultivated% + 0.3 × mean NDVI
- **PASS** if cultivated% > 60%, else **REVIEW**
