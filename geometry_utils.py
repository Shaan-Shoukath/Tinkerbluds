"""
geometry_utils.py — KML parsing, polygon extraction, validation, and EE conversion.
"""

import io
import tempfile
import os
import math
import geopandas as gpd
import ee
from shapely.geometry import mapping


def parse_kml(file_bytes: bytes) -> gpd.GeoDataFrame:
    """
    Read raw KML bytes into a GeoDataFrame.
    Writes to a temp file because GeoPandas/Fiona needs a file path for KML.
    """
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".kml")
    try:
        tmp.write(file_bytes)
        tmp.close()
        gdf = gpd.read_file(tmp.name, driver="KML")
    finally:
        os.unlink(tmp.name)

    if gdf.empty:
        raise ValueError("KML file contains no features")

    return gdf


def extract_polygon(gdf: gpd.GeoDataFrame):
    """
    Extract the first polygon geometry from a GeoDataFrame.
    Handles Polygon and MultiPolygon types.
    """
    geom = gdf.geometry.iloc[0]

    if geom.geom_type == "MultiPolygon":
        # Take the largest polygon from the collection
        geom = max(geom.geoms, key=lambda g: g.area)
    elif geom.geom_type != "Polygon":
        raise ValueError(f"Expected Polygon geometry, got {geom.geom_type}")

    if geom.is_empty or not geom.is_valid:
        raise ValueError("Polygon geometry is empty or invalid")

    return geom


def compute_area_sq_m(polygon) -> float:
    """
    Compute geodesic area of a polygon in square metres.
    Uses the Haversine-based approximation for WGS84 coordinates.
    """
    # Re-project to an equal-area CRS for accurate area calculation
    gdf = gpd.GeoDataFrame(geometry=[polygon], crs="EPSG:4326")
    gdf_proj = gdf.to_crs("EPSG:6933")  # World Cylindrical Equal Area
    return float(gdf_proj.geometry.iloc[0].area)


def validate_geometry(polygon) -> None:
    """
    Run safety checks on the polygon before sending to Earth Engine.
    """
    if polygon.is_empty:
        raise ValueError("Polygon is empty")

    if not polygon.is_valid:
        raise ValueError("Polygon geometry is not valid")

    area_sq_m = compute_area_sq_m(polygon)
    area_sq_km = area_sq_m / 1_000_000

    if area_sq_km > 500:
        raise ValueError(
            f"Polygon area is {area_sq_km:.1f} km² — exceeds 500 km² limit"
        )

    if area_sq_km < 0.0001:
        raise ValueError(
            f"Polygon area is {area_sq_km:.6f} km² — too small to process"
        )


def polygon_to_ee_geometry(polygon) -> ee.Geometry.Polygon:
    """
    Convert a Shapely polygon to an Earth Engine Geometry.
    Strips Z coordinates (KML often has 3D) and formats for EE.
    """
    # Extract exterior ring as 2D [lon, lat] pairs (drop Z if present)
    coords_2d = [[c[0], c[1]] for c in polygon.exterior.coords]
    return ee.Geometry.Polygon([coords_2d])
