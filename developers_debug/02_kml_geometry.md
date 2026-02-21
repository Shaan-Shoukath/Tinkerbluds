# KML Parsing & Geometry Pipeline

How we go from a `.kml` file to a Google Earth Engine geometry object.

**File:** `plot_validation/geometry_utils.py`

---

## Data Flow

```
Raw KML bytes → temp file → geopandas read → GeoDataFrame
                                                  │
                                        ┌─────────┴──────────┐
                                        │                    │
                                  Shapely Polygon    ee.Geometry.Polygon
                                  (local Python)     (server-side EE)
```

---

## Functions

### `parse_kml(file_bytes: bytes) → GeoDataFrame`

```python
def parse_kml(file_bytes: bytes) -> gpd.GeoDataFrame:
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp.flush()
    gdf = gpd.read_file(tmp.name, driver="KML")
    os.unlink(tmp.name)
    return gdf
```

**Why temp file?** Geopandas/Fiona can't read KML from bytes directly — they need a file path. We write the uploaded bytes to a temp file, read it, then delete it.

**Why `driver="KML"`?** Fiona supports multiple geospatial formats. Without specifying the driver, it might misidentify the format.

---

### `extract_polygon(gdf: GeoDataFrame) → Polygon`

```python
def extract_polygon(gdf):
    if gdf.empty:
        raise ValueError("No features in KML")
    geom = gdf.geometry.iloc[0]
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    if geom.geom_type != "Polygon":
        raise ValueError(f"Expected Polygon, got {geom.geom_type}")
    return geom
```

**MultiPolygon handling:** Some KML files contain multiple polygons. We take the largest one by area — this is usually the main plot boundary, not small slivers or islands.

---

### `validate_geometry(polygon)`

Checks:

1. `polygon.is_valid` — no self-intersections, proper ring closure
2. `polygon.area > 0` — not a degenerate point or line

---

### `compute_area_sq_m(polygon) → float`

```python
def compute_area_sq_m(polygon):
    gdf = gpd.GeoDataFrame(geometry=[polygon], crs="EPSG:4326")
    gdf_proj = gdf.to_crs("EPSG:6933")  # World Cylindrical Equal Area
    return float(gdf_proj.geometry.iloc[0].area)
```

**Why reproject?** GPS coordinates (EPSG:4326) use degrees, not metres. 1° longitude ≈ 111 km at the equator but ≈ 85 km at Kerala's latitude (10°N). Computing area directly in degrees gives wrong numbers. EPSG:6933 is a global equal-area projection that gives accurate m² values everywhere.

**Why not use EE for area?** We could, but this is faster (no network call) and gives us a local area value before the EE pipeline runs.

---

### `polygon_to_ee_geometry(polygon) → ee.Geometry`

```python
def polygon_to_ee_geometry(polygon):
    coords_2d = [[c[0], c[1]] for c in polygon.exterior.coords]
    return ee.Geometry.Polygon([coords_2d])
```

**Why strip Z?** KML stores 3D coordinates `(lon, lat, altitude)`. Earth Engine expects 2D `[lon, lat]` only. The `c[0], c[1]` extraction drops the altitude.

**Important:** `ee.Geometry.Polygon` does NOT create a polygon locally. It creates a _description_ of a polygon that will be sent to Google's servers. No geometry computation happens here.

---

## Coordinate Systems

| System                       | Code      | Units             | Used For               |
| ---------------------------- | --------- | ----------------- | ---------------------- |
| WGS84                        | EPSG:4326 | Degrees (lat/lon) | KML input, EE geometry |
| World Cylindrical Equal Area | EPSG:6933 | Metres            | Area calculation       |
| Web Mercator                 | EPSG:3857 | Metres            | Leaflet map display    |

---

## Common Issues

| Problem              | Cause                                     | Fix                              |
| -------------------- | ----------------------------------------- | -------------------------------- |
| "No features in KML" | KML has no `<Placemark>` elements         | Check KML structure              |
| "Expected Polygon"   | KML contains a LineString or Point        | Ensure KML uses `<Polygon>`      |
| `is_valid` fails     | Self-intersecting polygon                 | Simplify polygon in Google Earth |
| Zero area            | Degenerate polygon (all points collinear) | Re-draw polygon                  |
