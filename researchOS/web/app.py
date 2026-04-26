"""researchOS v2 web app — library of research projects."""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import markdown as md
from fastapi import FastAPI, Form, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from db import get_conn
from web.auth import get_session_user, verify_password, create_session, login_redirect

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

# ── Global run state ─────────────────────────────────────────────────────────
_run_state: dict = {
    "running": False,
    "current_topic": None,
    "current_topic_id": None,
    "stage": None,
    "progress": 0,
    "total": 0,
    "logs": [],
    # v2 additions
    "current_queries": [],
    "generated_files": [],
    "source_count": 0,
    "scrape_progress": "",   # e.g. "2/3"
    "elapsed_seconds": 0,
    "started_at": None,
    "error_msg": None,
}

STAGE_LABELS = {
    "queries":      "Generating search queries",
    "searching":    "Running web searches",
    "scraping":     "Deep-scraping pages",
    "synthesising": "Writing report with LLM",
    "saving":       "Saving to library",
}


def _run_log(level: str, msg: str):
    entry = {
        "type": "log",
        "level": level,
        "msg": msg,
        "t": datetime.now().strftime("%H:%M:%S"),
    }
    _run_state["logs"].append(entry)
    if len(_run_state["logs"]) > 600:
        _run_state["logs"] = _run_state["logs"][-500:]


def _make_emit():
    def emit(level: str, msg: str):
        if level == "stage":
            _run_state["stage"] = msg
            _run_log("stage", STAGE_LABELS.get(msg, msg))
        elif level == "query":
            _run_state["current_queries"].append(msg.strip(" ›").strip())
            _run_log("query", msg)
        elif level == "file":
            _run_state["generated_files"].append(msg)
            _run_log("done", f"File saved: {msg}")
        elif level == "source_count":
            try:
                _run_state["source_count"] = int(msg)
            except ValueError:
                pass
            _run_log("info", f"Sources found: {msg}")
        elif level == "scrape_progress":
            _run_state["scrape_progress"] = msg
            _run_log("info", f"Scraping page {msg}…")
        else:
            _run_log(level, msg)
    return emit


app = FastAPI(title="researchOS", version="2.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _all_projects() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.slug, p.name, p.description, p.icon,
                       COUNT(t.id) AS total_topics,
                       COUNT(t.id) FILTER (WHERE t.status = 'done') AS done_count,
                       COUNT(t.id) FILTER (WHERE t.status = 'pending') AS pending_count
                FROM supply.research_projects p
                LEFT JOIN supply.research_topics t ON t.project_id = p.id
                GROUP BY p.id
                ORDER BY p.sort_order, p.name
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def _get_project(slug: str) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, slug, name, description, icon FROM supply.research_projects WHERE slug=%s",
                (slug,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return dict(zip([d[0] for d in cur.description], row))


def _library_stats() -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                  COUNT(*) FILTER (WHERE status='done') AS total_reports,
                  COUNT(*) FILTER (WHERE status='pending') AS pending,
                  COUNT(*) FILTER (WHERE status='done' AND completed_at >= NOW() - INTERVAL '7 days') AS done_week
                FROM supply.research_topics
            """)
            row = cur.fetchone()
            return {"total_reports": row[0], "pending": row[1], "done_week": row[2]}


def _project_topics(project_id: int) -> list[dict]:
    """Return topics with at most one finding_id per topic (latest)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.id, t.topic, t.category, t.sop_hint, t.status, t.sort_order,
                       t.created_at, t.completed_at,
                       (SELECT f.id FROM supply.research_findings f
                        WHERE f.topic_id = t.id
                        ORDER BY f.created_at DESC LIMIT 1) AS finding_id
                FROM supply.research_topics t
                WHERE t.project_id = %s
                ORDER BY t.sort_order, t.id
            """, (project_id,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def _project_stats(project_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                  COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE status='done') AS done,
                  COUNT(*) FILTER (WHERE status='pending') AS pending,
                  COUNT(*) FILTER (WHERE status='error') AS error,
                  COUNT(*) FILTER (WHERE status='running') AS running
                FROM supply.research_topics WHERE project_id=%s
            """, (project_id,))
            row = cur.fetchone()
            return {"total": row[0], "done": row[1], "pending": row[2], "error": row[3], "running": row[4]}


def _render_ctx(request: Request, extra: dict = None) -> dict:
    from systemOS.config.depth import choices as depth_choices
    user = get_session_user(request)
    ctx = {
        "request": request,
        "user": user,
        "projects": _all_projects(),
        "active_route": "library",
        "active_project": None,
        "llm_model": os.getenv("OLLAMA_MODEL", "") or "Claude",
        "categories": ["general", "procurement", "inventory", "forecasting",
                       "logistics", "automation", "tools"],
        "depth_choices": depth_choices(),  # [(key, label, est_minutes), ...]
    }
    if extra:
        ctx.update(extra)
    return ctx


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _queue_topic(project_id: int, topic: str, category: str, sop_hint: str | None = None) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(sort_order), 0) FROM supply.research_topics WHERE project_id=%s",
                (project_id,),
            )
            max_order = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO supply.research_topics (project_id, topic, category, sop_hint, status, sort_order)
                   VALUES (%s, %s, %s, %s, 'pending', %s)
                   ON CONFLICT (project_id, topic) DO UPDATE SET status='pending', created_at=NOW()
                   RETURNING id""",
                (project_id, topic, category, sop_hint or None, max_order + 10),
            )
            return cur.fetchone()[0]


async def _execute_run(rows: list, emit_fn):
    from agents.researcher import research as do_research

    _run_state["running"] = True
    _run_state["total"] = len(rows)
    _run_state["progress"] = 0
    _run_state["logs"].clear()
    _run_state["started_at"] = time.time()
    _run_state["error_msg"] = None
    _run_state["current_queries"] = []
    _run_state["generated_files"] = []

    # Parallelism logic
    concurrency = int(os.environ.get("RESEARCH_PARALLEL_WORKERS", "2"))
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_topic(row):
        async with semaphore:
            tid, topic, category, sop_hint = row[0], row[1], row[2], row[3]
            depth = row[4] if len(row) > 4 else "standard"
            
            # Simple global state update (frontend will just show latest active task)
            _run_state["current_topic"] = f"Parallel: {topic}"
            _run_state["current_topic_id"] = tid
            _run_state["stage"] = None
            _run_state["source_count"] = 0
            _run_state["scrape_progress"] = ""
            _run_state["elapsed_seconds"] = 0
            
            topic_start = time.time()
            _run_log("info", f"Starting: {topic[:70]}")
            
            try:
                from db import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE supply.research_topics SET status='running' WHERE id=%s", (tid,)
                        )
                
                await do_research(
                    topic=topic,
                    category=category or "general",
                    sop_hint=sop_hint,
                    topic_id=tid,
                    depth=depth or "standard",
                    emit=emit_fn,
                )
                
                _run_state["elapsed_seconds"] = round(time.time() - topic_start)
                _run_state["progress"] += 1
                _run_log("done", f"Completed: {topic[:60]}")
                logger.info("[RUN] Done %d/%d: %s", _run_state["progress"], _run_state["total"], topic[:60])
                
            except Exception as exc:
                logger.error("[RUN] Failed topic %d: %s", tid, exc)
                _run_state["error_msg"] = str(exc)[:200]
                _run_log("error", f"Failed: {exc}")
                from db import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE supply.research_topics SET status='error' WHERE id=%s", (tid,)
                        )
                _run_state["progress"] += 1

    tasks = [_run_topic(row) for row in rows]
    await asyncio.gather(*tasks)

    _run_state["running"] = False
    _run_state["current_topic"] = None
    _run_state["current_topic_id"] = None
    _run_state["stage"] = None
    _run_log("info", "All done.")
    # emit a final 'done' marker so frontend knows without reloading
    _run_state["logs"].append({"type": "done", "t": datetime.now().strftime("%H:%M:%S")})



# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse("/library", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/library"):
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": None})


@app.post("/login")
def login_post(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/library")):
    if verify_password(username, password):
        resp = RedirectResponse(next or "/library", status_code=303)
        resp.set_cookie("supply_session", create_session(username), max_age=86400, httponly=True, samesite="lax")
        return resp
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": "Invalid username or password."}, status_code=401)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("supply_session")
    return resp


# ── Library ───────────────────────────────────────────────────────────────────

@app.get("/library", response_class=HTMLResponse)
def library(request: Request):
    user = get_session_user(request)
    if not user:
        return login_redirect("/library")
    stats = _library_stats()
    ctx = _render_ctx(request, {"active_route": "library", "stats": stats, "flash": None})
    return templates.TemplateResponse("library.html", ctx)


@app.post("/library/new")
def create_project(request: Request, name: str = Form(""), description: str = Form(""), icon: str = Form("📚")):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    slug = _slugify(name)
    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO supply.research_projects (slug, name, description, icon) VALUES (%s, %s, %s, %s)",
                    (slug, name.strip(), description.strip() or None, icon.strip() or "📚"),
                )
            except Exception:
                pass
    return RedirectResponse(f"/p/{slug}", status_code=303)


# ── Project page ──────────────────────────────────────────────────────────────

@app.get("/p/{slug}", response_class=HTMLResponse)
def project_page(request: Request, slug: str):
    user = get_session_user(request)
    if not user:
        return login_redirect(f"/p/{slug}")

    project = _get_project(slug)
    if not project:
        return RedirectResponse("/library", status_code=303)

    topics = _project_topics(project["id"])
    ctx = _render_ctx(request, {
        "active_project": slug,
        "active_route": "project",
        "project": project,
        "topics": topics,
        "total_count": len(topics),
        "done_count": sum(1 for t in topics if t["status"] == "done"),
        "pending_count": sum(1 for t in topics if t["status"] == "pending"),
        "error_count": sum(1 for t in topics if t["status"] == "error"),
        "running_count": sum(1 for t in topics if t["status"] == "running"),
        "flash": None,
    })
    return templates.TemplateResponse("project.html", ctx)


@app.post("/p/{slug}/queue")
def queue_research(request: Request, slug: str, topic: str = Form(""), category: str = Form("general"), sop_hint: str = Form("")):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    project = _get_project(slug)
    if not project or not topic.strip():
        return RedirectResponse(f"/p/{slug}", status_code=303)
    _queue_topic(project["id"], topic.strip(), category, sop_hint.strip() or None)
    return RedirectResponse(f"/p/{slug}", status_code=303)


@app.post("/p/{slug}/run")
async def run_project(request: Request, slug: str, background_tasks: BackgroundTasks):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    project = _get_project(slug)
    if not project:
        return RedirectResponse("/library", status_code=303)
    if _run_state["running"]:
        return RedirectResponse(f"/p/{slug}", status_code=303)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, topic, category, sop_hint, depth FROM supply.research_topics WHERE project_id=%s AND status='pending' ORDER BY sort_order, id",
                (project["id"],),
            )
            rows = cur.fetchall()

    if rows:
        background_tasks.add_task(_execute_run, rows, _make_emit())
    return RedirectResponse(f"/p/{slug}", status_code=303)


# ── Per-topic actions ─────────────────────────────────────────────────────────

@app.post("/p/{slug}/topic/{tid}/run")
async def run_single_topic(request: Request, slug: str, tid: int, background_tasks: BackgroundTasks):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    if _run_state["running"]:
        return JSONResponse({"error": "already_running"}, status_code=409)
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Accept pending OR error topics (retry in one action)
            cur.execute(
                "UPDATE supply.research_topics SET status='pending' WHERE id=%s AND status='error' RETURNING id",
                (tid,),
            )
            cur.execute(
                "SELECT id, topic, category, sop_hint, depth FROM supply.research_topics WHERE id=%s AND status='pending'",
                (tid,),
            )
            row = cur.fetchone()
    if row:
        background_tasks.add_task(_execute_run, [row], _make_emit())
        # Return JSON so JS can react without page reload
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"started": True, "topic_id": tid})
    return RedirectResponse(f"/p/{slug}", status_code=303)


@app.post("/p/{slug}/topic/{tid}/retry")
def retry_topic(request: Request, slug: str, tid: int):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE supply.research_topics SET status='pending' WHERE id=%s AND status='error'", (tid,)
            )
    return RedirectResponse(f"/p/{slug}", status_code=303)


@app.post("/p/{slug}/topic/{tid}/edit")
def edit_topic(request: Request, slug: str, tid: int, topic: str = Form(""), category: str = Form("general"), sop_hint: str = Form("")):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    if topic.strip():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE supply.research_topics SET topic=%s, category=%s, sop_hint=%s WHERE id=%s AND status='pending'",
                    (topic.strip(), category, sop_hint.strip() or None, tid),
                )
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True})
    return RedirectResponse(f"/p/{slug}", status_code=303)


@app.post("/p/{slug}/topic/{tid}/delete")
def delete_topic(request: Request, slug: str, tid: int):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM supply.research_topics WHERE id=%s AND status='pending'", (tid,)
            )
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True})
    return RedirectResponse(f"/p/{slug}", status_code=303)


@app.post("/p/{slug}/topic/{tid}/move")
def move_topic(request: Request, slug: str, tid: int, direction: str = Form("")):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    project = _get_project(slug)
    if not project:
        return RedirectResponse("/library", status_code=303)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM supply.research_topics WHERE project_id=%s AND status='pending' ORDER BY sort_order, id",
                (project["id"],),
            )
            ids = [r[0] for r in cur.fetchall()]

        if tid not in ids:
            return RedirectResponse(f"/p/{slug}", status_code=303)

        idx = ids.index(tid)
        if direction == "up" and idx > 0:
            ids[idx], ids[idx - 1] = ids[idx - 1], ids[idx]
        elif direction == "down" and idx < len(ids) - 1:
            ids[idx], ids[idx + 1] = ids[idx + 1], ids[idx]
        else:
            return RedirectResponse(f"/p/{slug}", status_code=303)

        with conn.cursor() as cur:
            for i, row_id in enumerate(ids):
                cur.execute(
                    "UPDATE supply.research_topics SET sort_order=%s WHERE id=%s",
                    ((i + 1) * 10, row_id),
                )

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True, "ids": ids})
    return RedirectResponse(f"/p/{slug}", status_code=303)


# ── Batch reorder (drag-and-drop) ─────────────────────────────────────────────

@app.post("/api/reorder")
async def reorder_topics(request: Request):
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    try:
        body = await request.json()
        topic_ids: list[int] = body["topic_ids"]
    except Exception:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    with get_conn() as conn:
        with conn.cursor() as cur:
            for i, tid in enumerate(topic_ids):
                cur.execute(
                    "UPDATE supply.research_topics SET sort_order=%s WHERE id=%s AND status='pending'",
                    ((i + 1) * 10, tid),
                )
    return JSONResponse({"ok": True})


# ── Project stats (live poll) ─────────────────────────────────────────────────

@app.get("/api/project/{slug}/stats")
def project_stats_api(request: Request, slug: str):
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    project = _get_project(slug)
    if not project:
        return JSONResponse({"error": "not_found"}, status_code=404)
    stats = _project_stats(project["id"])
    return JSONResponse(stats)


# ── Global run-all ────────────────────────────────────────────────────────────

@app.post("/api/run-all")
async def run_all(request: Request, background_tasks: BackgroundTasks):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    if _run_state["running"]:
        return RedirectResponse("/library", status_code=303)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, topic, category, sop_hint FROM supply.research_topics WHERE status='pending' ORDER BY sort_order, id"
            )
            rows = cur.fetchall()
    if rows:
        background_tasks.add_task(_execute_run, rows, _make_emit())
    return RedirectResponse("/library", status_code=303)


# ── SSE stream ────────────────────────────────────────────────────────────────

@app.get("/api/run-stream")
async def run_stream(request: Request):
    async def generator():
        sent_count = 0
        tick = 0
        while True:
            if await request.is_disconnected():
                break
            state_evt = {
                "type": "state",
                "running": _run_state["running"],
                "stage": _run_state.get("stage"),
                "topic": _run_state.get("current_topic"),
                "topic_id": _run_state.get("current_topic_id"),
                "progress": _run_state["progress"],
                "total": _run_state["total"],
                "current_queries": _run_state.get("current_queries", []),
                "generated_files": _run_state.get("generated_files", []),
                "source_count": _run_state.get("source_count", 0),
                "scrape_progress": _run_state.get("scrape_progress", ""),
                "elapsed_seconds": _run_state.get("elapsed_seconds", 0),
                "error_msg": _run_state.get("error_msg"),
            }
            yield f"data: {json.dumps(state_evt)}\n\n"
            logs = _run_state["logs"]
            for entry in logs[sent_count:]:
                yield f"data: {json.dumps(entry)}\n\n"
            sent_count = len(logs)
            tick += 1
            if tick % 20 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.8)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/run-status")
def run_status():
    return JSONResponse(_run_state)


# ── Report viewer ──────────────────────────────────────────────────────────────

@app.get("/report/{finding_id}", response_class=HTMLResponse)
def view_report(request: Request, finding_id: int):
    user = get_session_user(request)
    if not user:
        return login_redirect(f"/report/{finding_id}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT f.report, f.report_html, f.model, f.sources, f.queries, f.output_file,
                       f.created_at, t.topic, t.project_id
                FROM supply.research_findings f
                JOIN supply.research_topics t ON t.id = f.topic_id
                WHERE f.id = %s
            """, (finding_id,))
            row = cur.fetchone()

    if not row:
        return RedirectResponse("/library", status_code=303)

    report_raw, report_html, model, sources, queries, output_file, created_at, topic, project_id = row
    if not report_html:
        report_html = md.markdown(report_raw, extensions=["extra", "toc"])

    project = None
    if project_id:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, slug, name, description, icon FROM supply.research_projects WHERE id=%s",
                    (project_id,),
                )
                r = cur.fetchone()
                if r:
                    project = dict(zip([d[0] for d in cur.description], r))

    ctx = _render_ctx(request, {
        "active_project": project["slug"] if project else None,
        "topic": topic,
        "report_html": report_html,
        "model": model,
        "sources": sources or [],
        "queries": queries or [],
        "output_file": output_file,
        "created_at": created_at,
        "project": project,
        "finding_id": finding_id,
    })
    return templates.TemplateResponse("report.html", ctx)


# ── LLM + health ───────────────────────────────────────────────────────────────

@app.get("/api/llm-status")
async def llm_status():
    ollama_url = os.getenv("OLLAMA_URL", "")
    model = os.getenv("OLLAMA_MODEL", "llama3.3")
    if not ollama_url:
        return {"ok": True, "model": "Claude API"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{ollama_url}/api/tags")
            if r.status_code == 200:
                return {"ok": True, "model": model}
    except Exception:
        pass
    return {"ok": False, "model": model}


@app.get("/health")
def health():
    return {"status": "ok", "service": "researchOS", "version": "2.0.0"}


# ── Vendor Intelligence ────────────────────────────────────────────────────────

def _vendor_api_key_ok(request: Request) -> bool:
    expected = os.getenv("VENDOR_API_KEY", "")
    if not expected:
        return True  # no key configured = open (set one in .env for security)
    return request.headers.get("X-API-Key") == expected


def _all_vendor_jobs() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT j.id, j.vendor_name, j.vendor_url, j.skus, j.category, j.depth, j.status,
                       j.created_at, j.completed_at,
                       (SELECT p.id FROM supply.vendor_profiles p WHERE p.job_id = j.id LIMIT 1) AS profile_id
                FROM supply.vendor_scrape_jobs j
                ORDER BY j.created_at DESC
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def _get_vendor_profile(profile_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.*, j.skus AS requested_skus, j.category AS job_category
                FROM supply.vendor_profiles p
                JOIN supply.vendor_scrape_jobs j ON j.id = p.job_id
                WHERE p.id = %s
            """, (profile_id,))
            row = cur.fetchone()
            if not row:
                return None
            return dict(zip([d[0] for d in cur.description], row))


@app.get("/vendors", response_class=HTMLResponse)
def vendors_list(request: Request):
    user = get_session_user(request)
    if not user:
        return login_redirect("/vendors")
    jobs = _all_vendor_jobs()
    ctx = _render_ctx(request, {
        "active_route": "vendors",
        "jobs": jobs,
        "pending_count": sum(1 for j in jobs if j["status"] == "pending"),
        "done_count": sum(1 for j in jobs if j["status"] == "done"),
    })
    return templates.TemplateResponse("vendors.html", ctx)


@app.post("/vendors/queue")
def queue_vendor_job(
    request: Request,
    vendor_name: str = Form(""),
    vendor_url: str = Form(""),
    skus: str = Form(""),
    category: str = Form(""),
    depth: str = Form("standard"),
):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    if not vendor_url.strip():
        return RedirectResponse("/vendors", status_code=303)

    sku_list = [s.strip() for s in skus.split(",") if s.strip()]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO supply.vendor_scrape_jobs (vendor_name, vendor_url, skus, category, depth, status)
                   VALUES (%s, %s, %s, %s, %s, 'pending')""",
                (vendor_name.strip() or None, vendor_url.strip(),
                 json.dumps(sku_list), category.strip() or None, depth),
            )
    return RedirectResponse("/vendors", status_code=303)


@app.get("/vendor/{profile_id}", response_class=HTMLResponse)
def vendor_profile(request: Request, profile_id: int):
    user = get_session_user(request)
    if not user:
        return login_redirect(f"/vendor/{profile_id}")
    profile = _get_vendor_profile(profile_id)
    if not profile:
        return RedirectResponse("/vendors", status_code=303)

    import markdown as md
    if profile.get("raw_report") and not profile.get("raw_report_html"):
        profile["raw_report_html"] = md.markdown(
            profile["raw_report"], extensions=["extra", "toc"]
        )

    ctx = _render_ctx(request, {
        "active_route": "vendors",
        "profile": profile,
    })
    return templates.TemplateResponse("vendor_detail.html", ctx)


@app.post("/vendors/{job_id}/run")
async def run_vendor_job(request: Request, job_id: int, background_tasks: BackgroundTasks):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    if _run_state["running"]:
        return JSONResponse({"error": "research_run_in_progress"}, status_code=409)

    # Check job exists and is pending/error
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM supply.vendor_scrape_jobs WHERE id=%s", (job_id,)
            )
            row = cur.fetchone()
    if not row:
        return RedirectResponse("/vendors", status_code=303)
    if row[0] not in ("pending", "error"):
        return RedirectResponse("/vendors", status_code=303)

    # Reset to pending if it was an error
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE supply.vendor_scrape_jobs SET status='pending', error_msg=NULL WHERE id=%s AND status='error'",
                (job_id,),
            )

    from agents.vendor_agent import run_vendor_agent
    background_tasks.add_task(run_vendor_agent, job_id, _make_emit())
    return RedirectResponse("/vendors", status_code=303)


@app.post("/vendor/{profile_id}/delete")
def delete_vendor_profile(request: Request, profile_id: int):
    user = get_session_user(request)
    if not user:
        return login_redirect()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM supply.vendor_scrape_jobs WHERE id = (SELECT job_id FROM supply.vendor_profiles WHERE id=%s)",
                (profile_id,),
            )
    return RedirectResponse("/vendors", status_code=303)


# ── Vendor API (called from Mac scraper) ──────────────────────────────────────

@app.get("/api/vendor-jobs")
def api_vendor_jobs(request: Request, status: str = "pending"):
    if not _vendor_api_key_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, vendor_name, vendor_url, skus, category FROM supply.vendor_scrape_jobs WHERE status=%s ORDER BY created_at",
                (status,),
            )
            cols = [d[0] for d in cur.description]
            jobs = [dict(zip(cols, r)) for r in cur.fetchall()]
    return JSONResponse({"jobs": jobs})


@app.post("/api/vendor-jobs/{job_id}/start")
def api_vendor_job_start(request: Request, job_id: int):
    if not _vendor_api_key_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE supply.vendor_scrape_jobs SET status='running' WHERE id=%s AND status='pending'",
                (job_id,),
            )
    return JSONResponse({"ok": True})


@app.post("/api/vendor-jobs/{job_id}/result")
async def api_vendor_job_result(request: Request, job_id: int):
    if not _vendor_api_key_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()
    error = body.get("error")
    profile_data = body.get("profile", {})
    raw_report = body.get("raw_report", "")

    if error:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE supply.vendor_scrape_jobs SET status='error', error_msg=%s, completed_at=NOW() WHERE id=%s",
                    (error[:500], job_id),
                )
        return JSONResponse({"ok": True, "status": "error"})

    try:
        import markdown as md
        report_html = md.markdown(raw_report, extensions=["extra", "toc"]) if raw_report else None
    except Exception:
        report_html = None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO supply.vendor_profiles
                   (job_id, vendor_name, vendor_url, category,
                    company_type, uk_based, about, address, contact_email, contact_phone,
                    certifications, min_order_value, min_order_qty, lead_time,
                    wholesale_available, trade_account_required, payment_terms, delivery_info,
                    products, potential_upstream, alternatives, risk_flags, confidence_score,
                    raw_report, raw_report_html, pages_scraped)
                   VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s)""",
                (
                    job_id,
                    profile_data.get("vendor_name"),
                    profile_data.get("vendor_url"),
                    profile_data.get("category"),
                    profile_data.get("company_type"),
                    profile_data.get("uk_based"),
                    profile_data.get("about"),
                    profile_data.get("address"),
                    profile_data.get("contact_email"),
                    profile_data.get("contact_phone"),
                    json.dumps(profile_data.get("certifications") or []),
                    profile_data.get("min_order_value"),
                    profile_data.get("min_order_qty"),
                    profile_data.get("lead_time"),
                    profile_data.get("wholesale_available"),
                    profile_data.get("trade_account_required"),
                    profile_data.get("payment_terms"),
                    profile_data.get("delivery_info"),
                    json.dumps(profile_data.get("products") or []),
                    profile_data.get("potential_upstream_supplier"),
                    json.dumps(profile_data.get("alternatives") or []),
                    json.dumps(profile_data.get("risk_flags") or []),
                    profile_data.get("confidence_score"),
                    raw_report,
                    report_html,
                    json.dumps(profile_data.get("pages_scraped") or []),
                ),
            )
            cur.execute(
                "UPDATE supply.vendor_scrape_jobs SET status='done', completed_at=NOW() WHERE id=%s",
                (job_id,),
            )

    return JSONResponse({"ok": True, "status": "done"})

