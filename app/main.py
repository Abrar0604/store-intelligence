"""
Store Intelligence API — FastAPI entrypoint.

A production-aware REST API that ingests behavioral events from the detection
pipeline, computes real-time store analytics, detects operational anomalies,
and serves a live dashboard.

Endpoints:
  POST  /events/ingest              — Batch event ingestion (idempotent)
  GET   /stores/{id}/metrics        — Real-time store metrics
  GET   /stores/{id}/funnel         — Conversion funnel
  GET   /stores/{id}/heatmap        — Zone visit heatmap
  GET   /stores/{id}/anomalies      — Active anomalies
  GET   /health                     — Service health
  GET   /dashboard                  — Live web dashboard
  GET   /events/stream              — SSE event stream for dashboard
"""

import os
import json
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.logging_config import setup_logging, RequestLoggingMiddleware
from app.database import get_db, close_db
from app.ingestion import router as ingestion_router
from app.metrics import router as metrics_router
from app.funnel import router as funnel_router
from app.heatmap import router as heatmap_router
from app.anomalies import router as anomalies_router
from app.health import router as health_router

# ── Setup ──────────────────────────────────────────────────────────────────
setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"

# SSE subscribers for live updates
_sse_subscribers = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — initialize DB on startup, close on shutdown."""
    logger.info("Starting Store Intelligence API...")
    await get_db()
    logger.info("Database initialized. API ready.")
    yield
    logger.info("Shutting down...")
    await close_db()


# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Store Intelligence API",
    description="Real-time retail store analytics from CCTV detection pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging middleware
app.add_middleware(RequestLoggingMiddleware)

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(ingestion_router, tags=["Ingestion"])
app.include_router(metrics_router, tags=["Analytics"])
app.include_router(funnel_router, tags=["Analytics"])
app.include_router(heatmap_router, tags=["Analytics"])
app.include_router(anomalies_router, tags=["Analytics"])
app.include_router(health_router, tags=["Operations"])

# ── Static files for dashboard ─────────────────────────────────────────────
if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")


# ── Dashboard ──────────────────────────────────────────────────────────────
@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard():
    """Serve the live dashboard."""
    index_path = DASHBOARD_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text())
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)


# ── SSE Stream ─────────────────────────────────────────────────────────────
@app.get("/events/stream", tags=["Dashboard"])
async def event_stream():
    """
    Server-Sent Events stream for live dashboard updates.
    Sends metrics updates every 2 seconds.
    """
    async def generate():
        try:
            while True:
                try:
                    db = await get_db()
                    
                    # Get latest metrics summary
                    cursor = await db.execute(
                        """SELECT 
                            COUNT(DISTINCT CASE WHEN event_type IN ('ENTRY','REENTRY') AND is_staff=0 THEN visitor_id END) as visitors,
                            COUNT(*) as total_events,
                            MAX(timestamp) as last_event
                           FROM events"""
                    )
                    row = await cursor.fetchone()
                    
                    data = {
                        "type": "metrics_update",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "visitors": row[0] if row else 0,
                        "total_events": row[1] if row else 0,
                        "last_event": row[2] if row else None,
                    }
                    
                    yield f"data: {json.dumps(data)}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Global exception handler ──────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions — return structured JSON, no raw stack traces."""
    trace_id = getattr(request.state, "trace_id", "unknown") if hasattr(request, "state") else "unknown"
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred. Please try again.",
            "trace_id": trace_id,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Ensure HTTP exceptions also return structured JSON."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)},
    )


# ── Root ───────────────────────────────────────────────────────────────────
@app.get("/", tags=["Info"])
async def root():
    return {
        "service": "Store Intelligence API",
        "version": "1.0.0",
        "store": "STORE_BLR_002",
        "endpoints": [
            "POST /events/ingest",
            "GET /stores/{store_id}/metrics",
            "GET /stores/{store_id}/funnel",
            "GET /stores/{store_id}/heatmap",
            "GET /stores/{store_id}/anomalies",
            "GET /health",
            "GET /dashboard",
            "GET /events/stream",
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=True,
    )
