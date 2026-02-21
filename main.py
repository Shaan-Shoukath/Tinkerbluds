"""
main.py — FastAPI entrypoint for Tinkerbluds.

Usage:
    uvicorn main:app --reload
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from plot_validation.earth_engine_service import init_ee
from plot_validation.router import router as plot_validation_router


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Prevent browsers from serving stale cached responses."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# FastAPI App
# ──────────────────────────────────────────────
app = FastAPI(
    title="Tinkerbluds",
    description="Cultivated land validation & crop suitability platform.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(NoCacheMiddleware)

# ──────────────────────────────────────────────
# Routers
# ──────────────────────────────────────────────
app.include_router(plot_validation_router)

# ──────────────────────────────────────────────
# Startup
# ──────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    try:
        init_ee()
        logger.info("Earth Engine ready")
    except Exception as e:
        logger.error("EE init failed at startup: %s", e)

# ──────────────────────────────────────────────
# Static files + dashboard
# ──────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "tinkerbluds"}
