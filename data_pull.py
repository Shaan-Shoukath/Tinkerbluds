import os
import math
import requests
import numpy as np
import geopandas as gpd
from PIL import Image, ImageDraw
from io import BytesIO
from dotenv import load_dotenv

# ---- Load API key from .env ----
load_dotenv()
api_key = os.getenv("GOOGLE_MAPS_API_KEY")

if not api_key or api_key == "YOUR_GOOGLE_API_KEY_HERE":
    print("ERROR: Set your Google Maps API key in the .env file")
    exit(1)

# ---- Read KML and extract polygon ----
gdf = gpd.read_file("plot.kml", driver="KML")
polygon = gdf.geometry.iloc[0]
minx, miny, maxx, maxy = polygon.bounds

# Get the actual polygon vertices (lon, lat pairs)
coords = list(polygon.exterior.coords)  # list of (lon, lat, z) tuples
print(f"Polygon vertices: {len(coords)-1} (excluding closing repeat)")

center_lat = (miny + maxy) / 2
center_lon = (minx + maxx) / 2
print(f"Bounds: {minx:.6f}, {miny:.6f} → {maxx:.6f}, {maxy:.6f}")
print(f"Center: {center_lat:.6f}, {center_lon:.6f}")

# ---- Mercator projection helpers ----
TILE_SIZE = 256

def lat_to_merc_y(lat_deg):
    lat_rad = math.radians(lat_deg)
    return math.log(math.tan(math.pi / 4 + lat_rad / 2))

def world_px(lat, lon, zoom):
    """Convert lat/lon to world pixel coordinates at given zoom."""
    scale = TILE_SIZE * (2 ** zoom)
    x = (lon + 180) / 360 * scale
    y = (1 - lat_to_merc_y(lat) / math.pi) / 2 * scale
    return x, y

def compute_zoom(minx, miny, maxx, maxy, img_size=640):
    for zoom in range(21, 0, -1):
        cx_px, cy_px = world_px(center_lat, center_lon, zoom)
        x_min, _ = world_px(miny, minx, zoom)
        x_max, _ = world_px(miny, maxx, zoom)
        _, y_min = world_px(maxy, minx, zoom)
        _, y_max = world_px(miny, minx, zoom)
        w_px = x_max - x_min
        h_px = y_max - y_min
        if w_px <= img_size * 0.85 and h_px <= img_size * 0.85:
            return zoom
    return 15

zoom = compute_zoom(minx, miny, maxx, maxy)
print(f"Using zoom level: {zoom}")

# ---- Download satellite image (scale=2 → actual 1280×1280) ----
IMG_SIZE = 640
SCALE = 2
url = (
    f"https://maps.googleapis.com/maps/api/staticmap"
    f"?center={center_lat},{center_lon}"
    f"&zoom={zoom}"
    f"&size={IMG_SIZE}x{IMG_SIZE}"
    f"&scale={SCALE}"
    f"&maptype=satellite"
    f"&key={api_key}"
)

print("Fetching satellite image...")
response = requests.get(url)
if response.status_code != 200:
    print(f"ERROR: API returned {response.status_code}: {response.text}")
    exit(1)

img = Image.open(BytesIO(response.content)).convert("RGBA")
img_w, img_h = img.size
print(f"Downloaded image size: {img_w}x{img_h}")

# ---- Convert each polygon vertex to image pixel coordinates ----
cx_px, cy_px = world_px(center_lat, center_lon, zoom)

def geo_to_img_px(lat, lon):
    wx, wy = world_px(lat, lon, zoom)
    px = (wx - cx_px) * SCALE + img_w / 2
    py = (wy - cy_px) * SCALE + img_h / 2
    return (px, py)

# Build pixel polygon from KML vertices (lon, lat, [z])
pixel_poly = [geo_to_img_px(c[1], c[0]) for c in coords[:-1]]  # drop closing repeat
print("Pixel polygon vertices:")
for i, pt in enumerate(pixel_poly):
    print(f"  {i}: ({pt[0]:.1f}, {pt[1]:.1f})")

# ---- Create polygon mask and apply to image ----
mask = Image.new("L", (img_w, img_h), 0)          # black = masked out
draw = ImageDraw.Draw(mask)
draw.polygon(pixel_poly, fill=255)                  # white = keep

# Apply mask: outside polygon → transparent
img.putalpha(mask)
result = img  # keep as RGBA with transparency

# ---- Crop tightly to the polygon bounding box ----
px_vals = [p[0] for p in pixel_poly]
py_vals = [p[1] for p in pixel_poly]
crop_left   = max(0, int(min(px_vals)))
crop_top    = max(0, int(min(py_vals)))
crop_right  = min(img_w, int(max(px_vals)) + 1)
crop_bottom = min(img_h, int(max(py_vals)) + 1)

result = result.crop((crop_left, crop_top, crop_right, crop_bottom))
print(f"Final image size: {result.width}x{result.height} px")

# ---- Save as image.png (RGBA — transparent outside polygon) ----
result.save("image.png")  # PNG preserves transparency
print("Saved image.png")