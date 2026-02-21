"""
supabase_service.py — Farmer DB, Plot Storage & Overlap Detection.

Connects to Supabase for:
  - Farmer registration (name, phone, email)
  - Saving confirmed plots (GeoJSON polygon + raw KML)
  - Overlap detection between plots (Shapely-based)
  - Admin alerts for overlapping plots
"""

import os
import json
import logging
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
from supabase import create_client, Client

logger = logging.getLogger(__name__)

_supabase: Client | None = None

# ──────────────────────────────────────────────────────────────
# Overlap threshold — plots overlapping more than this trigger an alert.
# 0.30 = 30%.  Change as needed.
# ──────────────────────────────────────────────────────────────
OVERLAP_THRESHOLD = 0.30


def init_supabase() -> Client:
    """Initialise (or return cached) Supabase client."""
    global _supabase
    if _supabase is not None:
        return _supabase

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")  # service role for full access
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env"
        )

    _supabase = create_client(url, key)
    logger.info("Supabase client initialised (%s)", url)
    return _supabase


# ──────────────────────────────────────────────────────────────
# Farmer CRUD
# ──────────────────────────────────────────────────────────────

def upsert_farmer(name: str, phone: str, email: str = "") -> dict:
    """
    Create a farmer or return existing one (matched by phone).

    Returns the farmer row as a dict with 'id', 'name', 'phone', 'email'.
    """
    sb = init_supabase()

    # Check if farmer already exists by phone
    existing = sb.table("farmers").select("*").eq("phone", phone).execute()
    if existing.data:
        logger.info("Farmer already exists: %s (phone=%s)", existing.data[0]["name"], phone)
        return existing.data[0]

    # Create new farmer
    row = {"name": name, "phone": phone, "email": email}
    result = sb.table("farmers").insert(row).execute()
    farmer = result.data[0]
    logger.info("Created farmer: %s (id=%s)", farmer["name"], farmer["id"])
    return farmer


# ──────────────────────────────────────────────────────────────
# Plot Storage
# ──────────────────────────────────────────────────────────────

def save_plot(
    farmer_id: str,
    polygon_geojson: dict,
    kml_data: str = "",
    label: str = "",
    area_acres: float = 0.0,
    ndvi_mean: float = 0.0,
    decision: str = "",
    confidence_score: float = 0.0,
) -> dict:
    """
    Save a confirmed plot to Supabase.

    Args:
        farmer_id: UUID of the farmer
        polygon_geojson: GeoJSON dict of the polygon geometry
        kml_data: raw KML file content (for later download)
        label: user-given name for the plot
        area_acres: plot area in acres
        ndvi_mean: mean NDVI value
        decision: validation decision string
        confidence_score: 0–1 confidence score

    Returns the saved plot row.
    """
    sb = init_supabase()

    row = {
        "farmer_id": farmer_id,
        "polygon_geojson": json.dumps(polygon_geojson),
        "kml_data": kml_data,
        "label": label,
        "area_acres": area_acres,
        "ndvi_mean": ndvi_mean,
        "decision": decision,
        "confidence_score": confidence_score,
    }
    result = sb.table("plots").insert(row).execute()
    plot = result.data[0]
    logger.info("Saved plot: id=%s, farmer=%s, area=%.2f acres", plot["id"], farmer_id, area_acres)
    return plot


# ──────────────────────────────────────────────────────────────
# Overlap Detection
# ──────────────────────────────────────────────────────────────

def check_overlap(new_polygon_geojson: dict, new_plot_id: str = None) -> list[dict]:
    """
    Check if a new polygon overlaps with any existing saved plots.

    Uses Shapely for geometric intersection.
    Returns a list of overlaps that exceed OVERLAP_THRESHOLD.

    Each overlap dict:
        {
            "existing_plot_id": "uuid",
            "existing_farmer_id": "uuid",
            "existing_farmer_name": "...",
            "existing_farmer_phone": "...",
            "overlap_pct": 0.45,   # 45% of the new plot is overlapping
            "alert_created": True,
        }
    """
    sb = init_supabase()

    # Build Shapely geometry for the new plot
    new_shape = shape(new_polygon_geojson)
    new_area = new_shape.area

    if new_area == 0:
        return []

    # Fetch all existing plots
    result = sb.table("plots").select("id, farmer_id, polygon_geojson, label").execute()
    existing_plots = result.data or []

    overlaps = []
    for existing in existing_plots:
        # Skip self
        if new_plot_id and existing["id"] == new_plot_id:
            continue

        try:
            existing_geojson = existing["polygon_geojson"]
            if isinstance(existing_geojson, str):
                existing_geojson = json.loads(existing_geojson)
            existing_shape = shape(existing_geojson)

            intersection = new_shape.intersection(existing_shape)
            overlap_pct = intersection.area / new_area

            if overlap_pct >= OVERLAP_THRESHOLD:
                # Look up the farmer who owns the existing plot
                farmer_info = sb.table("farmers").select("name, phone").eq(
                    "id", existing["farmer_id"]
                ).execute()
                farmer = farmer_info.data[0] if farmer_info.data else {}

                # Create an alert record
                alert_created = False
                if new_plot_id:
                    try:
                        sb.table("overlap_alerts").insert({
                            "new_plot_id": new_plot_id,
                            "existing_plot_id": existing["id"],
                            "overlap_pct": round(overlap_pct, 4),
                        }).execute()
                        alert_created = True
                    except Exception as e:
                        logger.warning("Failed to create alert: %s", e)

                overlaps.append({
                    "existing_plot_id": existing["id"],
                    "existing_plot_label": existing.get("label", ""),
                    "existing_farmer_id": existing["farmer_id"],
                    "existing_farmer_name": farmer.get("name", ""),
                    "existing_farmer_phone": farmer.get("phone", ""),
                    "overlap_pct": round(overlap_pct, 4),
                    "alert_created": alert_created,
                })
        except Exception as e:
            logger.warning("Error checking overlap with plot %s: %s", existing["id"], e)

    if overlaps:
        logger.warning(
            "⚠️ OVERLAP DETECTED: %d plot(s) overlap > %.0f%%",
            len(overlaps), OVERLAP_THRESHOLD * 100,
        )
    return overlaps


# ──────────────────────────────────────────────────────────────
# Admin: Alerts
# ──────────────────────────────────────────────────────────────

def get_overlap_alerts(resolved: bool = False) -> list[dict]:
    """Fetch overlap alerts (default: unresolved only)."""
    sb = init_supabase()
    result = (
        sb.table("overlap_alerts")
        .select("*, new_plot:plots!new_plot_id(label, farmer_id), existing_plot:plots!existing_plot_id(label, farmer_id)")
        .eq("resolved", resolved)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def resolve_alert(alert_id: str) -> dict:
    """Mark an overlap alert as resolved."""
    sb = init_supabase()
    result = sb.table("overlap_alerts").update({"resolved": True}).eq("id", alert_id).execute()
    return result.data[0] if result.data else {}
