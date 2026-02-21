# Dashboard & Frontend

How the browser UI works — upload flow, API communication, result rendering, and map preview.

**File:** `static/index.html` (single-file HTML + CSS + JS)

---

## UI Components

```
┌─────────────────────────────────────────────────┐
│  Header: "Cultivated Land Validator"            │
├─────────────────────────────────────────────────┤
│  Upload Card                                    │
│  ┌───────────────────────────────────────────┐  │
│  │  Drag & drop zone (.kml files)            │  │
│  └───────────────────────────────────────────┘  │
│  [Year] [From month] [To month] [Cloud %]       │
│  [Validate Plot]                                │
├─────────────────────────────────────────────────┤
│  Processing Animation (5 steps with checkmarks) │
├─────────────────────────────────────────────────┤
│  Map Preview (Leaflet + Esri satellite tiles)   │
├─────────────────────────────────────────────────┤
│  Results: 4 stat cards + PASS/REVIEW badge      │
│  ┌─────────┬─────────┬─────────┬─────────┐     │
│  │Plot Area│Cropland │Veg Area │Cultiv.% │     │
│  └─────────┴─────────┴─────────┴─────────┘     │
│  Confidence bar                                 │
│  Land class breakdown (horizontal bar chart)    │
├─────────────────────────────────────────────────┤
│  🌾 Best Crops for This Location (top 5 cards)  │
├─────────────────────────────────────────────────┤
│  📌 Plot Confirmation                            │
│  "Is this your proposed plot?"                  │
│  [✅ Yes]  [❌ No, analysis only]                │
│  → Farmer form (name, phone, email, label)      │
│  → ✅ Saved! OR ⚠️ Overlap detected + report    │
├─────────────────────────────────────────────────┤
│  "How It Works" explanation section             │
└─────────────────────────────────────────────────┘
```

---

## JavaScript Logic

### File Upload

```javascript
// Drag & drop OR click to select
dropZone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
});

function setFile(file) {
  if (!file.name.toLowerCase().endsWith(".kml")) {
    alert("Please select a .kml file");
    return;
  }
  selectedFile = file;
  submitBtn.disabled = false; // enable the button
}
```

### API Call

```javascript
const formData = new FormData();
formData.append("file", selectedFile);

const year = document.getElementById("yearInput").value;
const sm = document.getElementById("startMonth").value;
const em = document.getElementById("endMonth").value;
const cloud = document.getElementById("cloudInput").value;

const res = await fetch(
  `/validate_plot?year=${year}&start_month=${sm}&end_month=${em}&cloud_threshold=${cloud}`,
  { method: "POST", body: formData },
);
```

**Why query params + form data?** FastAPI expects file uploads via `multipart/form-data` (the `FormData` object). Numeric parameters are cleaner as query params than form fields.

### Processing Animation

While waiting for the API (usually 10–30 seconds), we show a step-by-step animation:

```javascript
function animateSteps() {
  const steps = document.querySelectorAll("#stepList li");
  let i = 0;
  const interval = setInterval(() => {
    if (i > 0) steps[i - 1].classList.replace("active", "done");
    if (i < steps.length) {
      steps[i].classList.add("active");
      i++;
    } else clearInterval(interval);
  }, 2500); // 2.5s per step
  return interval;
}
```

Steps shown: Parsing KML → Fetching Sentinel-2 → Computing NDVI → Checking WorldCover → Calculating

---

## Map Preview

Uses **Leaflet.js** with Esri satellite tiles. Renders after results arrive.

```javascript
let mapInstance = null;

// Inside showResults():
if (data.polygon_coords && data.polygon_coords.length > 0) {
  document.getElementById("mapSection").style.display = "block";

  // Destroy previous map if re-submitting
  if (mapInstance) {
    mapInstance.remove();
  }

  mapInstance = L.map("map");

  // Esri satellite tiles (free, no API key needed)
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "© Esri", maxZoom: 19 },
  ).addTo(mapInstance);

  // Draw polygon outline
  const poly = L.polygon(data.polygon_coords, {
    color: "#63b3ed", // light blue border
    weight: 3,
    fillColor: "#63b3ed",
    fillOpacity: 0.15, // semi-transparent fill
  }).addTo(mapInstance);

  // Auto-zoom to fit polygon with padding
  mapInstance.fitBounds(poly.getBounds().pad(0.3));
}
```

**Why Esri tiles?** Free satellite imagery without API key. Alternatives like Google Maps or Mapbox require paid API keys.

**Why `pad(0.3)`?** Adds 30% padding around the polygon so it's not edge-to-edge — gives context about surrounding area.

**Why destroy/recreate?** Leaflet doesn't like re-initializing on the same DOM element. We `remove()` the old map instance before creating a new one.

---

## Land Class Chart

Renders a horizontal bar for each detected ESA WorldCover class:

```javascript
const CLASS_COLORS = {
  Trees: "#2d6a4f", // dark green
  Cropland: "#f4a261", // orange
  "Built-up": "#e76f51", // red
  Grassland: "#d4e09b", // lime
  // ...
};

const sorted = Object.entries(data.land_classes).sort((a, b) => b[1] - a[1]); // largest first

for (const [name, acres] of sorted) {
  const classPct = (acres / totalAcres) * 100;
  // Render bar with width proportional to percentage
}
```

**Why sort descending?** The most significant class should appear first. If 80% is Trees, that's the most important information.

---

## CSS Design System

| Element      | Style                                       | Reasoning                               |
| ------------ | ------------------------------------------- | --------------------------------------- |
| Background   | `#0b0f1a` (dark navy)                       | High contrast, professional look        |
| Cards        | `linear-gradient(145deg, #141a2e, #1a2040)` | Subtle depth without flat look          |
| Borders      | `rgba(99,179,237,0.08)`                     | Almost invisible but provides structure |
| Text         | `#e2e8f0` (light slate)                     | Easy on eyes, high readability          |
| Accent       | `#63b3ed` (sky blue)                        | Consistent brand color                  |
| PASS badge   | `#48bb78` (green)                           | Universal "success" color               |
| REVIEW badge | `#ed8936` (orange)                          | "Caution" without alarm                 |

---

## External Dependencies

| Library                  | CDN                  | Purpose                        |
| ------------------------ | -------------------- | ------------------------------ |
| **Leaflet 1.9.4**        | unpkg.com            | Map rendering, polygon drawing |
| **Esri World Imagery**   | arcgisonline.com     | Free satellite map tiles       |
| **Google Fonts (Inter)** | fonts.googleapis.com | Clean modern typography        |
