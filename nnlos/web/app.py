"""NNLOS web service — FastAPI app."""

import logging
import os
from datetime import datetime

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="NNLOS", version="0.1.0", docs_url="/api/docs")

from fastapi import Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "nnlos", "time": datetime.utcnow().isoformat()}


# ── Sync endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/sync")
def trigger_full_sync(background_tasks: BackgroundTasks):
    """Trigger a full sync of all MRP Easy CSV types."""
    background_tasks.add_task(_run_sync, None)
    return {"status": "started", "message": "Full sync queued"}


@app.post("/api/sync/{data_type}")
def trigger_sync_type(data_type: str, background_tasks: BackgroundTasks):
    """Trigger sync for a single data type (e.g. raw_movements, items)."""
    from services.ingestion import INGEST_CONFIGS
    if data_type not in INGEST_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown type: {data_type}")
    background_tasks.add_task(_run_sync, [data_type])
    return {"status": "started", "type": data_type}


@app.get("/api/sync/status")
def sync_status():
    """Return last sync result per data type."""
    from db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (sync_type)
                    sync_type, status, rows_processed, error_message,
                    started_at, completed_at
                FROM nnlos.sync_log
                ORDER BY sync_type, started_at DESC
            """)
            rows = cur.fetchall()
    return [
        {
            "type": r[0], "status": r[1], "rows": r[2],
            "error": r[3], "started": str(r[4]), "completed": str(r[5]),
        }
        for r in rows
    ]


# ── Dashboard (simple HTML status page) ───────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    from db import get_conn
    rows = []
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (sync_type)
                        sync_type, status, rows_processed, started_at
                    FROM nnlos.sync_log
                    ORDER BY sync_type, started_at DESC
                """)
                for r in cur.fetchall():
                    rows.append({
                        "type": r[0], "status": r[1], "rows": r[2], "started": str(r[3])
                    })
    except Exception as exc:
        logger.warning("Dashboard DB query failed: %s", exc)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "rows": rows,
        "active_route": "dashboard"
    })


# ── Background task helper ─────────────────────────────────────────────────────

def _run_sync(types):
    from services.ingestion import run
    try:
        results = run(types)
        logger.info("Sync complete: %s", results)
    except Exception as exc:
        logger.exception("Sync failed: %s", exc)
