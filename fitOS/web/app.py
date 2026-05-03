"""fitOS web app — Phase 1 + 2 + 3."""

import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn
from services.openfoodfacts import lookup_barcode
from services import fitbit as fitbit_svc
from services import notifications as notif_svc
from services.sync import sync_vitals_today
from services.shopping import generate_weekly_shopping_list
from mcp.context_generator import generate_context_last_7_days

sys.path.insert(0, '/home/szmyt/server-services')
from systemOS.services.queue import add_task

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


# ── APScheduler setup ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = AsyncIOScheduler()
    scheduler.add_job(sync_vitals_today, CronTrigger(hour=4, minute=0),
                      id="daily_vitals_sync", replace_existing=True)
    scheduler.add_job(notif_svc.check_meal_nudge, CronTrigger(hour=13, minute=5),
                      id="meal_nudge", replace_existing=True)
    scheduler.start()
    logger.info("[SCHEDULER] Started — daily sync at 04:00, meal nudge at 13:05")
    yield
    scheduler.shutdown()

app = FastAPI(title="fitOS", version="3.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _latest_weight() -> dict | None:
    """Return the most recent weight entry."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT value, unit, created_at
                FROM health.metrics
                WHERE metric_type = 'weight'
                ORDER BY created_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()
    if not row:
        return None
    return {"value": float(row[0]), "unit": row[1], "created_at": row[2]}


def _weight_stats(days: int = 30) -> dict:
    """Min, max, avg weight over the last N days."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    MIN(value)::float,
                    MAX(value)::float,
                    ROUND(AVG(value)::numeric, 2)::float,
                    COUNT(*)
                FROM health.metrics
                WHERE metric_type = 'weight'
                  AND created_at >= NOW() - INTERVAL '1 day' * %s
            """, (days,))
            row = cur.fetchone()
    return {
        "min": row[0], "max": row[1],
        "avg": row[2], "count": row[3],
    }


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    latest = _latest_weight()
    stats = _weight_stats(30)
    return templates.TemplateResponse("dashboard.html", {
        "request":  request,
        "latest":   latest,
        "stats":    stats,
        "page":     "vitals",
    })


# ── API ───────────────────────────────────────────────────────────────────────

@app.post("/api/metrics", status_code=201)
async def add_metric(request: Request):
    """
    Log a single metric entry.

    Body: { metric_type, value, unit, note? }
    Returns: { id, created_at }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    metric_type = (body.get("metric_type") or "").strip()
    unit        = (body.get("unit") or "").strip()
    note        = (body.get("note") or "").strip() or None

    try:
        value = float(body.get("value"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "value must be a number"}, status_code=422)

    if not metric_type or not unit:
        return JSONResponse({"error": "metric_type and unit are required"}, status_code=422)

    if value <= 0 or value > 999:
        return JSONResponse({"error": "value out of plausible range"}, status_code=422)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO health.metrics (metric_type, value, unit, note)
                   VALUES (%s, %s, %s, %s) RETURNING id, created_at""",
                (metric_type, value, unit, note),
            )
            row = cur.fetchone()

    logger.info("[METRICS] logged %s=%.2f %s", metric_type, value, unit)
    return JSONResponse(
        {"id": row[0], "created_at": row[1].isoformat()},
        status_code=201,
    )


@app.get("/api/metrics/history")
def metric_history(metric_type: str = "weight", days: int = 30):
    """
    Return metric history ordered ASC (oldest first) for Chart.js.

    Query params:
        metric_type  default "weight"
        days         default 30, max 365
    """
    days = min(max(days, 1), 365)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, value::float, unit, created_at
                FROM health.metrics
                WHERE metric_type = %s
                  AND created_at >= NOW() - INTERVAL '1 day' * %s
                ORDER BY created_at ASC
            """, (metric_type, days))
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

    return JSONResponse([
        {
            "id":         r[0],
            "value":      r[1],
            "unit":       r[2],
            "created_at": r[3].isoformat(),
        }
        for r in rows
    ])


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "fitOS", "version": "3.0.0"}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 pages
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, notes FROM health.workout_templates
                WHERE user_id = 1 ORDER BY created_at DESC
            """)
            tmpl_list = [{"id": r[0], "name": r[1], "notes": r[2]} for r in cur.fetchall()]

            cur.execute("""
                SELECT id, name, muscle_group FROM health.exercises ORDER BY name
            """)
            exercises = [{"id": r[0], "name": r[1], "muscle_group": r[2]} for r in cur.fetchall()]

    return templates.TemplateResponse("activity.html", {
        "request":   request,
        "page":      "activity",
        "templates": tmpl_list,
        "exercises": exercises,
    })


@app.get("/nutrition", response_class=HTMLResponse)
def nutrition_page(request: Request):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, notes FROM health.recipes
                WHERE user_id = 1 ORDER BY created_at DESC
            """)
            recipes = [{"id": r[0], "name": r[1], "notes": r[2]} for r in cur.fetchall()]

    return templates.TemplateResponse("nutrition.html", {
        "request": request,
        "page":    "nutrition",
        "recipes": recipes,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Exercises API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/exercises")
def list_exercises():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, muscle_group, equipment, notes
                FROM health.exercises ORDER BY name
            """)
            rows = cur.fetchall()
    return JSONResponse([
        {"id": r[0], "name": r[1], "muscle_group": r[2], "equipment": r[3], "notes": r[4]}
        for r in rows
    ])


@app.post("/api/exercises", status_code=201)
async def create_exercise(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=422)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO health.exercises (name, muscle_group, equipment, notes)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (name, body.get("muscle_group"), body.get("equipment"), body.get("notes")),
            )
            eid = cur.fetchone()[0]
    return JSONResponse({"id": eid, "name": name}, status_code=201)


@app.get("/api/exercises/{exercise_id}/history")
def exercise_history(exercise_id: int, limit: int = 5):
    """Return last N sets for ghost-value display in the workout logger."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ws.set_number, ws.weight, ws.reps, ws.rir, ws.logged_at
                FROM health.workout_sets ws
                JOIN health.workout_logs wl ON wl.id = ws.log_id
                WHERE ws.exercise_id = %s
                ORDER BY ws.logged_at DESC
                LIMIT %s
            """, (exercise_id, limit))
            rows = cur.fetchall()
    return JSONResponse([
        {"set_number": r[0], "weight": float(r[1]) if r[1] else None,
         "reps": r[2], "rir": r[3], "logged_at": r[4].isoformat()}
        for r in rows
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Workout Templates API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/templates")
def list_templates():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT wt.id, wt.name, wt.notes, COUNT(te.id) AS exercise_count
                FROM health.workout_templates wt
                LEFT JOIN health.template_exercises te ON te.template_id = wt.id
                WHERE wt.user_id = 1
                GROUP BY wt.id ORDER BY wt.created_at DESC
            """)
            rows = cur.fetchall()
    return JSONResponse([
        {"id": r[0], "name": r[1], "notes": r[2], "exercise_count": r[3]}
        for r in rows
    ])


@app.post("/api/templates", status_code=201)
async def create_template(request: Request):
    """
    Body: { name, notes?, exercises: [{exercise_id, order_index, target_sets, target_reps, target_weight}] }
    """
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=422)

    exercises = body.get("exercises", [])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health.workout_templates (user_id, name, notes) VALUES (1, %s, %s) RETURNING id",
                (name, body.get("notes")),
            )
            tid = cur.fetchone()[0]
            for i, ex in enumerate(exercises):
                cur.execute("""
                    INSERT INTO health.template_exercises
                        (template_id, exercise_id, order_index, target_sets, target_reps, target_weight)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (tid, ex["exercise_id"], ex.get("order_index", i),
                      ex.get("target_sets"), ex.get("target_reps"), ex.get("target_weight")))
    return JSONResponse({"id": tid, "name": name}, status_code=201)


@app.get("/api/templates/{template_id}")
def get_template(template_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT wt.id, wt.name, wt.notes FROM health.workout_templates wt
                WHERE wt.id = %s AND wt.user_id = 1
            """, (template_id,))
            row = cur.fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)
            template = {"id": row[0], "name": row[1], "notes": row[2], "exercises": []}

            cur.execute("""
                SELECT te.id, te.order_index, te.target_sets, te.target_reps, te.target_weight,
                       e.id, e.name, e.muscle_group
                FROM health.template_exercises te
                JOIN health.exercises e ON e.id = te.exercise_id
                WHERE te.template_id = %s
                ORDER BY te.order_index
            """, (template_id,))
            for r in cur.fetchall():
                template["exercises"].append({
                    "te_id": r[0], "order_index": r[1],
                    "target_sets": r[2], "target_reps": r[3],
                    "target_weight": float(r[4]) if r[4] else None,
                    "exercise_id": r[5], "exercise_name": r[6], "muscle_group": r[7],
                })
    return JSONResponse(template)


# ─────────────────────────────────────────────────────────────────────────────
# Workout Logs API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/workouts")
def list_workouts(limit: int = 20):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT wl.id, wl.name, wl.started_at, wl.finished_at,
                       COALESCE(SUM(ws.volume), 0)::float AS total_volume,
                       COUNT(ws.id) AS set_count
                FROM health.workout_logs wl
                LEFT JOIN health.workout_sets ws ON ws.log_id = wl.id
                WHERE wl.user_id = 1
                GROUP BY wl.id ORDER BY wl.started_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
    return JSONResponse([
        {"id": r[0], "name": r[1], "started_at": r[2].isoformat(),
         "finished_at": r[3].isoformat() if r[3] else None,
         "total_volume": r[4], "set_count": r[5]}
        for r in rows
    ])


@app.post("/api/workouts/start", status_code=201)
async def start_workout(request: Request):
    """Body: { template_id?, name? }  Returns { log_id }"""
    body = await request.json()
    template_id = body.get("template_id")
    name = (body.get("name") or "").strip() or None

    if not name and template_id:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM health.workout_templates WHERE id = %s", (template_id,))
                row = cur.fetchone()
                if row:
                    name = row[0]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health.workout_logs (user_id, template_id, name) VALUES (1, %s, %s) RETURNING id",
                (template_id, name),
            )
            log_id = cur.fetchone()[0]
    return JSONResponse({"log_id": log_id}, status_code=201)


@app.post("/api/workouts/{log_id}/sets", status_code=201)
async def log_set(log_id: int, request: Request):
    """Body: { exercise_id, set_number, weight?, reps?, rir? }"""
    body = await request.json()
    exercise_id = body.get("exercise_id")
    set_number  = body.get("set_number", 1)
    weight = body.get("weight")
    reps   = body.get("reps")
    rir    = body.get("rir")

    if not exercise_id:
        return JSONResponse({"error": "exercise_id required"}, status_code=422)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO health.workout_sets
                       (log_id, exercise_id, set_number, weight, reps, rir)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, volume""",
                (log_id, exercise_id, set_number,
                 weight if weight is not None else None,
                 reps if reps is not None else None,
                 rir if rir is not None else None),
            )
            row = cur.fetchone()
    return JSONResponse(
        {"id": row[0], "volume": float(row[1]) if row[1] else None},
        status_code=201,
    )


@app.put("/api/workouts/{log_id}/finish")
async def finish_workout(log_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE health.workout_logs SET finished_at = NOW() WHERE id = %s AND user_id = 1",
                (log_id,),
            )
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# Ingredients API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/ingredients/search")
def search_ingredients(q: str = ""):
    q = q.strip()
    if not q:
        return JSONResponse([])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, brand, serving_size_g, kcal, protein_g, carbs_g, fat_g, fibre_g, source
                FROM health.ingredients
                WHERE name ILIKE %s OR brand ILIKE %s
                ORDER BY name LIMIT 20
            """, (f"%{q}%", f"%{q}%"))
            rows = cur.fetchall()
    return JSONResponse([_ingredient_row(r) for r in rows])


@app.get("/api/ingredients/barcode/{barcode}")
async def ingredient_by_barcode(barcode: str):
    """Look up in local DB first, then Open Food Facts. Auto-saves if found via OFF."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, brand, serving_size_g, kcal, protein_g, carbs_g, fat_g, fibre_g, source
                FROM health.ingredients WHERE barcode = %s
            """, (barcode,))
            row = cur.fetchone()
    if row:
        return JSONResponse(_ingredient_row(row))

    data = await lookup_barcode(barcode)
    if not data:
        return JSONResponse({"error": "barcode not found"}, status_code=404)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO health.ingredients
                    (name, barcode, brand, serving_size_g, kcal, protein_g, carbs_g, fat_g, fibre_g, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, name, brand, serving_size_g, kcal, protein_g, carbs_g, fat_g, fibre_g, source
            """, (data["name"], data["barcode"], data["brand"], data["serving_size_g"],
                  data["kcal"], data["protein_g"], data["carbs_g"], data["fat_g"],
                  data["fibre_g"], data["source"]))
            row = cur.fetchone()
    return JSONResponse(_ingredient_row(row))


@app.post("/api/ingredients", status_code=201)
async def create_ingredient(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=422)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO health.ingredients
                    (name, barcode, brand, serving_size_g, kcal, protein_g, carbs_g, fat_g, fibre_g, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'manual')
                RETURNING id
            """, (name, body.get("barcode") or None, body.get("brand") or None,
                  body.get("serving_size_g", 100), body.get("kcal"),
                  body.get("protein_g"), body.get("carbs_g"), body.get("fat_g"),
                  body.get("fibre_g")))
            iid = cur.fetchone()[0]
    return JSONResponse({"id": iid, "name": name}, status_code=201)


def _ingredient_row(r) -> dict:
    return {
        "id": r[0], "name": r[1], "brand": r[2],
        "serving_size_g": float(r[3]) if r[3] else 100,
        "kcal":      float(r[4]) if r[4] else None,
        "protein_g": float(r[5]) if r[5] else None,
        "carbs_g":   float(r[6]) if r[6] else None,
        "fat_g":     float(r[7]) if r[7] else None,
        "fibre_g":   float(r[8]) if r[8] else None,
        "source":    r[9],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Recipes API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/recipes")
def list_recipes():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.id, r.name, r.notes,
                       COALESCE(SUM(ri.quantity_g), 0)::float AS total_g
                FROM health.recipes r
                LEFT JOIN health.recipe_ingredients ri ON ri.recipe_id = r.id
                WHERE r.user_id = 1
                GROUP BY r.id ORDER BY r.created_at DESC
            """)
            rows = cur.fetchall()
    return JSONResponse([
        {"id": r[0], "name": r[1], "notes": r[2], "total_g": r[3]}
        for r in rows
    ])


@app.post("/api/recipes", status_code=201)
async def create_recipe(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=422)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health.recipes (user_id, name, notes) VALUES (1, %s, %s) RETURNING id",
                (name, body.get("notes")),
            )
            rid = cur.fetchone()[0]
    return JSONResponse({"id": rid, "name": name}, status_code=201)


@app.get("/api/recipes/{recipe_id}")
def get_recipe(recipe_id: int):
    """Returns recipe with ingredients and aggregated macro totals."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, notes FROM health.recipes WHERE id = %s AND user_id = 1",
                (recipe_id,),
            )
            row = cur.fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)

            recipe = {"id": row[0], "name": row[1], "notes": row[2], "ingredients": []}

            cur.execute("""
                SELECT ri.id, ri.quantity_g,
                       i.id, i.name, i.brand, i.kcal, i.protein_g, i.carbs_g, i.fat_g, i.fibre_g
                FROM health.recipe_ingredients ri
                JOIN health.ingredients i ON i.id = ri.ingredient_id
                WHERE ri.recipe_id = %s
            """, (recipe_id,))

            totals = {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0, "fibre_g": 0.0}

            for r in cur.fetchall():
                qty = float(r[1])
                factor = qty / 100.0

                def scaled(val):
                    return round(float(val) * factor, 2) if val else 0.0

                item = {
                    "item_id":      r[0],
                    "quantity_g":   qty,
                    "ingredient_id": r[2],
                    "name":         r[3],
                    "brand":        r[4],
                    "kcal":         scaled(r[5]),
                    "protein_g":    scaled(r[6]),
                    "carbs_g":      scaled(r[7]),
                    "fat_g":        scaled(r[8]),
                    "fibre_g":      scaled(r[9]),
                }
                recipe["ingredients"].append(item)
                for key in totals:
                    totals[key] += item[key]

    recipe["totals"] = {k: round(v, 1) for k, v in totals.items()}
    return JSONResponse(recipe)


@app.post("/api/recipes/{recipe_id}/ingredients", status_code=201)
async def add_recipe_ingredient(recipe_id: int, request: Request):
    """Body: { ingredient_id, quantity_g }"""
    body = await request.json()
    ingredient_id = body.get("ingredient_id")
    quantity_g = body.get("quantity_g")
    if not ingredient_id or quantity_g is None:
        return JSONResponse({"error": "ingredient_id and quantity_g required"}, status_code=422)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health.recipe_ingredients (recipe_id, ingredient_id, quantity_g) VALUES (%s, %s, %s) RETURNING id",
                (recipe_id, ingredient_id, quantity_g),
            )
            item_id = cur.fetchone()[0]
    return JSONResponse({"item_id": item_id}, status_code=201)


@app.delete("/api/recipes/{recipe_id}/ingredients/{item_id}")
def remove_recipe_ingredient(recipe_id: int, item_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM health.recipe_ingredients WHERE id = %s AND recipe_id = %s",
                (item_id, recipe_id),
            )
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 pages
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/integrations", response_class=HTMLResponse)
def integrations_page(request: Request):
    fitbit_connected = False
    last_sync = None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT updated_at FROM health.oauth_tokens
                WHERE user_id = 1 AND provider = 'fitbit'
            """)
            row = cur.fetchone()
            if row:
                fitbit_connected = True
                last_sync = row[0].isoformat()

    return templates.TemplateResponse("integrations.html", {
        "request":          request,
        "page":             "integrations",
        "fitbit_connected": fitbit_connected,
        "fitbit_configured": fitbit_svc.is_configured(),
        "last_sync":        last_sync,
        "fcm_configured":   notif_svc.is_configured(),
    })


@app.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request):
    return templates.TemplateResponse("insights.html", {
        "request": request,
        "page":    "insights",
    })

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 pages
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "page":    "calendar",
    })

@app.get("/report", response_class=HTMLResponse)
def report_page(request: Request):
    # Fetch weekly KPI data
    days_ago = datetime.now() - timedelta(days=7)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(wl.id), COALESCE(SUM(ws.volume), 0), COALESCE(AVG(ws.weight), 0)
                FROM health.workout_logs wl
                LEFT JOIN health.workout_sets ws ON ws.log_id = wl.id
                WHERE wl.started_at >= %s
            """, (days_ago,))
            kpi_row = cur.fetchone()
            kpis = {
                "workouts": kpi_row[0],
                "volume": float(kpi_row[1]),
                "avg_weight": float(kpi_row[2])
            }
            
            cur.execute("""
                SELECT wl.started_at, wl.name, COUNT(ws.id)
                FROM health.workout_logs wl
                LEFT JOIN health.workout_sets ws ON ws.log_id = wl.id
                WHERE wl.started_at >= %s
                GROUP BY wl.id ORDER BY wl.started_at DESC
            """, (days_ago,))
            workouts = [{"date": r[0].isoformat()[:10], "name": r[1], "sets": r[2]} for r in cur.fetchall()]
            
            cur.execute("""
                SELECT ml.consumed_at, ml.meal_type, ml.meal_name, r.name as rname
                FROM health.meal_logs ml
                LEFT JOIN health.recipes r ON r.id = ml.recipe_id
                WHERE ml.consumed_at >= %s ORDER BY ml.consumed_at DESC
            """, (days_ago,))
            meals = [{"date": r[0].isoformat()[:10], "type": r[1], "name": r[3] or r[2]} for r in cur.fetchall()]

    return templates.TemplateResponse("report.html", {
        "request": request,
        "page":    "report",
        "week_start": days_ago.strftime("%b %d, %Y"),
        "week_end": datetime.now().strftime("%b %d, %Y"),
        "kpis": kpis,
        "workouts": workouts,
        "meals": meals
    })

@app.get("/shopping", response_class=HTMLResponse)
def shopping_page(request: Request):
    s_list = generate_weekly_shopping_list(7)
    return templates.TemplateResponse("shopping.html", {
        "request": request,
        "page":    "shopping",
        "shopping_list": s_list,
    })

@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "page":    "chat",
    })

@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    msg = body.get("message")
    
    # We enqueue a task to SystemOS comms.py
    try:
        task_id = await add_task(
            workspace="fitos",
            user="daniel",
            task_type="comms",
            risk_level="none",
            module="coach",
            input=msg,
            trigger_type="manual",
            queue_lane="fast"
        )
        
        # Approve task so runner picks it up
        from systemOS.services.queue import approve_task, get_task
        await approve_task(task_id, "daniel")
        
        # Poll for completion (max 30s)
        import asyncio
        for _ in range(30):
            await asyncio.sleep(1)
            t = await get_task(task_id)
            if t and t.get("status") == "done":
                return JSONResponse({"reply": t.get("output", "No response.")})
            elif t and t.get("status") == "failed":
                return JSONResponse({"reply": "Agent failed to process your request."})
                
        return JSONResponse({"reply": "Request timed out waiting for agent."})
    except Exception as e:
        logger.error(f"[CHAT] Error: {e}")
        return JSONResponse({"reply": "Sorry, I'm having trouble connecting right now."})

@app.get("/api/calendar/events")
def calendar_events(start: str = None, end: str = None):
    # Return workout logs for FullCalendar
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, started_at, finished_at
                FROM health.workout_logs
                WHERE started_at >= NOW() - INTERVAL '30 days'
            """)
            events = []
            for r in cur.fetchall():
                events.append({
                    "id": r[0],
                    "title": r[1],
                    "start": r[2].isoformat(),
                    "end": r[3].isoformat() if r[3] else None,
                    "className": "completed"
                })
    return JSONResponse(events)





# ─────────────────────────────────────────────────────────────────────────────
# Planner — Workout, Meal Plan, Meal Prep
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/planner", response_class=HTMLResponse)
def planner_page(request: Request):
    return templates.TemplateResponse("planner.html", {"request": request, "page": "planner"})


# ── Workout Week Plan ──────────────────────────────────────────────────────────

@app.get("/api/planner/workout")
def get_workout_plan(week_start: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.day_of_week, s.label, s.template_id, t.name AS template_name
                FROM health.workout_plan_slots s
                LEFT JOIN health.workout_templates t ON t.id = s.template_id
                WHERE s.user_id = 1 AND s.week_start = %s
                ORDER BY s.day_of_week
            """, (week_start,))
            rows = cur.fetchall()
    return JSONResponse([{
        "day": r[0], "label": r[1], "template_id": r[2], "template_name": r[3]
    } for r in rows])


@app.post("/api/planner/workout")
async def save_workout_slot(request: Request):
    body = await request.json()
    week_start  = body["week_start"]
    day_of_week = body["day_of_week"]
    template_id = body.get("template_id")
    label       = body.get("label", "")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO health.workout_plan_slots (week_start, day_of_week, template_id, label)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, week_start, day_of_week)
                DO UPDATE SET template_id = EXCLUDED.template_id, label = EXCLUDED.label
            """, (week_start, day_of_week, template_id or None, label or None))
    return JSONResponse({"ok": True})


@app.delete("/api/planner/workout")
async def clear_workout_slot(request: Request):
    body = await request.json()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM health.workout_plan_slots
                WHERE user_id = 1 AND week_start = %s AND day_of_week = %s
            """, (body["week_start"], body["day_of_week"]))
    return JSONResponse({"ok": True})


# ── Meal Plan ──────────────────────────────────────────────────────────────────

@app.get("/api/planner/meal")
def get_meal_plan(week_start: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Upsert plan shell
            cur.execute("""
                INSERT INTO health.meal_plans (week_start) VALUES (%s)
                ON CONFLICT (user_id, week_start) DO NOTHING
                RETURNING id
            """, (week_start,))
            row = cur.fetchone()
            if row:
                plan_id = row[0]
            else:
                cur.execute("SELECT id FROM health.meal_plans WHERE user_id=1 AND week_start=%s", (week_start,))
                plan_id = cur.fetchone()[0]

            cur.execute("""
                SELECT e.id, e.day_of_week, e.meal_type, e.recipe_id, r.name AS recipe_name,
                       e.custom_name, e.servings, e.kcal, e.protein_g, e.carbs_g, e.fat_g
                FROM health.meal_plan_entries e
                LEFT JOIN health.recipes r ON r.id = e.recipe_id
                WHERE e.plan_id = %s ORDER BY e.day_of_week, e.meal_type
            """, (plan_id,))
            entries = cur.fetchall()

    return JSONResponse({
        "plan_id": plan_id,
        "entries": [{
            "id": e[0], "day": e[1], "meal_type": e[2],
            "recipe_id": e[3], "recipe_name": e[4],
            "custom_name": e[5], "servings": float(e[6]),
            "kcal": e[7], "protein_g": float(e[8]) if e[8] else None,
            "carbs_g": float(e[9]) if e[9] else None,
            "fat_g": float(e[10]) if e[10] else None,
        } for e in entries]
    })


@app.post("/api/planner/meal/entry")
async def add_meal_plan_entry(request: Request):
    body = await request.json()
    plan_id     = body["plan_id"]
    day         = body["day_of_week"]
    meal_type   = body["meal_type"]
    recipe_id   = body.get("recipe_id")
    custom_name = body.get("custom_name", "")
    servings    = float(body.get("servings", 1))
    kcal        = body.get("kcal")
    protein_g   = body.get("protein_g")
    carbs_g     = body.get("carbs_g")
    fat_g       = body.get("fat_g")

    # If recipe_id given, calculate macros from recipe_ingredients × servings
    if recipe_id:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        ROUND(SUM(ri.quantity_g * i.kcal / 100.0 * %s))::int,
                        ROUND(SUM(ri.quantity_g * i.protein_g / 100.0 * %s)::numeric, 1),
                        ROUND(SUM(ri.quantity_g * i.carbs_g / 100.0 * %s)::numeric, 1),
                        ROUND(SUM(ri.quantity_g * i.fat_g / 100.0 * %s)::numeric, 1)
                    FROM health.recipe_ingredients ri
                    JOIN health.ingredients i ON i.id = ri.ingredient_id
                    WHERE ri.recipe_id = %s
                """, (servings, servings, servings, servings, recipe_id))
                row = cur.fetchone()
                if row and row[0]:
                    kcal, protein_g, carbs_g, fat_g = row

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO health.meal_plan_entries
                    (plan_id, day_of_week, meal_type, recipe_id, custom_name, servings, kcal, protein_g, carbs_g, fat_g)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (plan_id, day, meal_type, recipe_id or None, custom_name or None,
                  servings, kcal, protein_g, carbs_g, fat_g))
            new_id = cur.fetchone()[0]

    return JSONResponse({"id": new_id, "kcal": kcal, "protein_g": protein_g,
                         "carbs_g": carbs_g, "fat_g": fat_g}, status_code=201)


@app.delete("/api/planner/meal/entry/{entry_id}")
def delete_meal_plan_entry(entry_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM health.meal_plan_entries WHERE id = %s", (entry_id,))
    return JSONResponse({"ok": True})


# ── Meal Prep — shopping list aggregation ──────────────────────────────────────

@app.get("/api/planner/prep")
def meal_prep_list(week_start: str):
    """
    Aggregate all recipe ingredients from the week's meal plan into a
    consolidated shopping list, accounting for servings multiplier.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM health.meal_plans WHERE user_id=1 AND week_start=%s", (week_start,))
            row = cur.fetchone()
            if not row:
                return JSONResponse({"items": [], "days": []})
            plan_id = row[0]

            # Aggregated ingredients from recipes
            cur.execute("""
                SELECT i.name, i.unit, SUM(ri.quantity_g * e.servings)::float AS total_g,
                       i.kcal, i.protein_g, i.carbs_g, i.fat_g
                FROM health.meal_plan_entries e
                JOIN health.recipe_ingredients ri ON ri.recipe_id = e.recipe_id
                JOIN health.ingredients i ON i.id = ri.ingredient_id
                WHERE e.plan_id = %s AND e.recipe_id IS NOT NULL
                GROUP BY i.id, i.name, i.unit, i.kcal, i.protein_g, i.carbs_g, i.fat_g
                ORDER BY i.name
            """, (plan_id,))
            items = cur.fetchall()

            # Per-day macro totals
            cur.execute("""
                SELECT day_of_week,
                       SUM(kcal)::int, SUM(protein_g)::float,
                       SUM(carbs_g)::float, SUM(fat_g)::float
                FROM health.meal_plan_entries
                WHERE plan_id = %s
                GROUP BY day_of_week ORDER BY day_of_week
            """, (plan_id,))
            days = cur.fetchall()

    return JSONResponse({
        "items": [{"name": r[0], "unit": r[1], "total_g": round(r[2], 1),
                   "kcal_per100": r[3], "protein_per100": float(r[4]) if r[4] else None,
                   "carbs_per100": float(r[5]) if r[5] else None,
                   "fat_per100": float(r[6]) if r[6] else None}
                  for r in items],
        "days": [{"day": r[0], "kcal": r[1], "protein_g": round(r[2],1) if r[2] else 0,
                  "carbs_g": round(r[3],1) if r[3] else 0, "fat_g": round(r[4],1) if r[4] else 0}
                 for r in days],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Biomarker & Blood Test Vault
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/biomarkers", response_class=HTMLResponse)
def biomarkers_page(request: Request):
    return templates.TemplateResponse("biomarkers.html", {"request": request, "page": "biomarkers"})


@app.get("/api/biomarkers")
def get_biomarkers():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, unit, range_min, range_max, category
                FROM health.biomarker_dictionary ORDER BY category, name
            """)
            rows = cur.fetchall()
    result = {}
    for row in rows:
        cat = row[5] or "Other"
        result.setdefault(cat, []).append({
            "id": row[0], "name": row[1], "unit": row[2],
            "range_min": float(row[3]) if row[3] is not None else None,
            "range_max": float(row[4]) if row[4] is not None else None,
        })
    return JSONResponse(result)


@app.post("/api/blood-tests")
async def create_blood_test(request: Request):
    body      = await request.json()
    test_date = body.get("test_date")
    lab_name  = body.get("lab_name", "")
    notes     = body.get("notes", "")
    results   = body.get("results", [])
    if not test_date or not results:
        return JSONResponse({"error": "test_date and results are required"}, status_code=400)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO health.blood_test_events (test_date, lab_name, notes)
                VALUES (%s, %s, %s) RETURNING id
            """, (test_date, lab_name or None, notes or None))
            event_id = cur.fetchone()[0]
            for r in results:
                if r.get("biomarker_id") is None or r.get("value") is None:
                    continue
                cur.execute("""
                    INSERT INTO health.biomarker_results (event_id, biomarker_id, value)
                    VALUES (%s, %s, %s)
                """, (event_id, int(r["biomarker_id"]), float(r["value"])))
    return JSONResponse({"event_id": event_id, "inserted": len(results)}, status_code=201)


@app.get("/api/blood-tests")
def list_blood_tests():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.id, e.test_date, e.lab_name, e.notes, COUNT(r.id)
                FROM health.blood_test_events e
                LEFT JOIN health.biomarker_results r ON r.event_id = e.id
                WHERE e.user_id = 1
                GROUP BY e.id ORDER BY e.test_date DESC
            """)
            rows = cur.fetchall()
    return JSONResponse([{"id":r[0],"test_date":str(r[1]),"lab_name":r[2],"notes":r[3],"result_count":r[4]} for r in rows])


@app.get("/api/biomarkers/{biomarker_id}/history")
def biomarker_history(biomarker_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.test_date, r.value::float, d.name, d.unit,
                       d.range_min::float, d.range_max::float
                FROM health.biomarker_results r
                JOIN health.blood_test_events e ON e.id = r.event_id
                JOIN health.biomarker_dictionary d ON d.id = r.biomarker_id
                WHERE r.biomarker_id = %s AND e.user_id = 1
                ORDER BY e.test_date ASC
            """, (biomarker_id,))
            rows = cur.fetchall()
    if not rows:
        return JSONResponse({"history": [], "meta": {}})
    meta = {"name": rows[0][2], "unit": rows[0][3],
            "range_min": rows[0][4], "range_max": rows[0][5]}
    return JSONResponse({"history": [{"date": str(r[0]), "value": r[1]} for r in rows], "meta": meta})


@app.get("/api/biomarkers/{biomarker_id}/correlate")
def biomarker_correlate(biomarker_id: int, metric: str = "sleep_hrs"):
    if metric not in {"sleep_hrs", "resting_hr", "steps", "hrv_ms"}:
        return JSONResponse({"error": "Invalid metric"}, status_code=400)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.test_date, r.value::float, d.name, d.unit,
                       d.range_min::float, d.range_max::float
                FROM health.biomarker_results r
                JOIN health.blood_test_events e ON e.id = r.event_id
                JOIN health.biomarker_dictionary d ON d.id = r.biomarker_id
                WHERE r.biomarker_id = %s AND e.user_id = 1
                ORDER BY e.test_date ASC
            """, (biomarker_id,))
            test_rows = cur.fetchall()
            corr = []
            for row in test_rows:
                window_start = row[0] - timedelta(days=30)
                cur.execute("""
                    SELECT AVG(value)::float FROM health.metrics
                    WHERE metric_type = %s AND user_id = 1
                      AND created_at::date BETWEEN %s AND %s
                """, (metric, window_start, row[0]))
                avg = cur.fetchone()
                corr.append({"date": str(row[0]), "biomarker": row[1],
                             "metric_avg": round(avg[0], 2) if avg and avg[0] else None})
    meta = {"biomarker_name": test_rows[0][2] if test_rows else "",
            "biomarker_unit": test_rows[0][3] if test_rows else "",
            "range_min": test_rows[0][4] if test_rows else None,
            "range_max": test_rows[0][5] if test_rows else None,
            "metric": metric}
    return JSONResponse({"data": corr, "meta": meta})


# ─────────────────────────────────────────────────────────────────────────────
# PWA companion app
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/app", response_class=HTMLResponse)
def pwa_home(request: Request):
    return templates.TemplateResponse("app.html", {"request": request})


@app.get("/sw.js")
def service_worker():
    from fastapi.responses import FileResponse
    return FileResponse(str(BASE_DIR / "static" / "sw.js"),
                        media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})


# ─────────────────────────────────────────────────────────────────────────────
# Recovery API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/recovery", response_class=HTMLResponse)
def recovery_page(request: Request):
    return templates.TemplateResponse("recovery.html", {
        "request": request,
        "page":    "recovery",
    })


@app.get("/api/recovery/today")
def recovery_today():
    """Compute today's readiness score from last known sleep, HRV, resting HR."""
    today = date.today()
    seven_days_ago = today - timedelta(days=7)

    def latest(conn, metric_type: str):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT value::float FROM health.metrics
                WHERE metric_type = %s AND user_id = 1
                ORDER BY created_at DESC LIMIT 1
            """, (metric_type,))
            row = cur.fetchone()
        return float(row[0]) if row else None

    def avg_7day(conn, metric_type: str):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT AVG(value)::float FROM health.metrics
                WHERE metric_type = %s AND user_id = 1
                  AND created_at >= %s
            """, (metric_type, seven_days_ago))
            row = cur.fetchone()
        return float(row[0]) if row and row[0] else None

    with get_conn() as conn:
        sleep_hrs  = latest(conn, 'sleep_hrs')
        resting_hr = latest(conn, 'resting_hr')
        hrv_ms     = latest(conn, 'hrv_ms')
        steps      = latest(conn, 'steps')
        hrv_7d_avg = avg_7day(conn, 'hrv_ms')
        hr_7d_avg  = avg_7day(conn, 'resting_hr')
        sleep_7d   = avg_7day(conn, 'sleep_hrs')

    # ── Readiness score (0–100) ──────────────────────────────────────────
    score = 50.0  # baseline when no data
    components = 0

    if sleep_hrs is not None:
        # 8h = 100, 6h = 60, <5h = 20
        sleep_score = max(20, min(100, (sleep_hrs / 8.0) * 100))
        score = (score * components + sleep_score) / (components + 1)
        components += 1

    if resting_hr is not None and hr_7d_avg is not None:
        # Lower than avg = better recovery
        hr_delta = hr_7d_avg - resting_hr  # positive = better than avg
        hr_score = min(100, max(0, 70 + hr_delta * 3))
        score = (score * components + hr_score) / (components + 1)
        components += 1

    if hrv_ms is not None and hrv_7d_avg is not None:
        # Higher than avg = better recovery
        hrv_ratio = hrv_ms / hrv_7d_avg if hrv_7d_avg > 0 else 1.0
        hrv_score = min(100, max(0, hrv_ratio * 70))
        score = (score * components + hrv_score) / (components + 1)
        components += 1

    # CNS Fatigue: HRV > 15% below 7-day average
    cns_fatigue = False
    if hrv_ms is not None and hrv_7d_avg is not None and hrv_7d_avg > 0:
        cns_fatigue = hrv_ms < (hrv_7d_avg * 0.85)

    return JSONResponse({
        "readiness_score": round(score, 1),
        "sleep_hrs":       sleep_hrs,
        "resting_hr":      resting_hr,
        "hrv_ms":          hrv_ms,
        "steps":           steps,
        "hrv_7d_avg":      hrv_7d_avg,
        "cns_fatigue":     cns_fatigue,
    })


@app.get("/api/recovery/trends")
def recovery_trends(days: int = 14):
    """Return per-day trend arrays for sleep, HRV, resting HR."""
    days = min(max(days, 7), 30)

    def daily_series(conn, metric_type: str) -> list:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT created_at::date AS day, AVG(value)::float
                FROM health.metrics
                WHERE metric_type = %s AND user_id = 1
                  AND created_at >= NOW() - INTERVAL '1 day' * %s
                GROUP BY day ORDER BY day ASC
            """, (metric_type, days))
            rows = {str(r[0]): round(r[1], 2) for r in cur.fetchall()}

        # Build full array with None for missing days
        result = []
        for i in range(days):
            d = (date.today() - timedelta(days=days - 1 - i))
            result.append(rows.get(str(d)))
        return result

    with get_conn() as conn:
        sleep_series  = daily_series(conn, 'sleep_hrs')
        hrv_series    = daily_series(conn, 'hrv_ms')
        hr_series     = daily_series(conn, 'resting_hr')

    return JSONResponse({
        "sleep":      sleep_series,
        "hrv":        hrv_series,
        "resting_hr": hr_series,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Diary & Targets API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/diary", response_class=HTMLResponse)
def diary_page(request: Request):
    return templates.TemplateResponse("diary.html", {
        "request": request,
        "page":    "diary",
    })


@app.get("/api/diary/today")
def diary_today():
    """Aggregate today's macro intake from meal_logs → recipes → ingredients."""
    today = date.today()
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get today's meal logs with their recipe macros
            cur.execute("""
                SELECT
                    ml.id, ml.meal_type, ml.consumed_at,
                    COALESCE(r.name, ml.meal_name) AS name,
                    COALESCE(SUM(ri.quantity_g / 100.0 * i.kcal), 0)::float      AS kcal,
                    COALESCE(SUM(ri.quantity_g / 100.0 * i.protein_g), 0)::float AS protein_g,
                    COALESCE(SUM(ri.quantity_g / 100.0 * i.carbs_g), 0)::float   AS carbs_g,
                    COALESCE(SUM(ri.quantity_g / 100.0 * i.fat_g), 0)::float     AS fat_g
                FROM health.meal_logs ml
                LEFT JOIN health.recipes r ON r.id = ml.recipe_id
                LEFT JOIN health.recipe_ingredients ri ON ri.recipe_id = r.id
                LEFT JOIN health.ingredients i ON i.id = ri.ingredient_id
                WHERE ml.user_id = 1 AND ml.consumed_at::date = %s
                GROUP BY ml.id, ml.meal_type, ml.consumed_at, r.name, ml.meal_name
                ORDER BY ml.consumed_at ASC
            """, (today,))
            meals = [
                {
                    "id": r[0], "meal_type": r[1],
                    "consumed_at": r[2].isoformat(), "name": r[3],
                    "kcal": round(r[4], 1), "protein_g": round(r[5], 1),
                    "carbs_g": round(r[6], 1), "fat_g": round(r[7], 1),
                }
                for r in cur.fetchall()
            ]

            # Totals
            totals = {
                "kcal":      round(sum(m["kcal"] for m in meals), 1),
                "protein_g": round(sum(m["protein_g"] for m in meals), 1),
                "carbs_g":   round(sum(m["carbs_g"] for m in meals), 1),
                "fat_g":     round(sum(m["fat_g"] for m in meals), 1),
            }

            # Targets
            cur.execute("""
                SELECT kcal, protein_g, carbs_g, fat_g, water_ml
                FROM health.user_targets
                WHERE user_id = 1
                ORDER BY effective_from DESC LIMIT 1
            """)
            t = cur.fetchone()
            targets = {
                "kcal":      t[0] if t else 2500,
                "protein_g": float(t[1]) if t else 180,
                "carbs_g":   float(t[2]) if t else 250,
                "fat_g":     float(t[3]) if t else 80,
                "water_ml":  t[4] if t else 2500,
            }

            # Water today
            cur.execute("""
                SELECT COALESCE(SUM(value), 0)::float FROM health.metrics
                WHERE metric_type = 'water_ml' AND created_at::date = %s AND user_id = 1
            """, (today,))
            water_ml = cur.fetchone()[0]

    return JSONResponse({
        "meals": meals,
        "totals": totals,
        "targets": targets,
        "water_ml": water_ml,
    })


@app.get("/api/targets")
def get_targets():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT kcal, protein_g, carbs_g, fat_g, water_ml, effective_from
                FROM health.user_targets WHERE user_id = 1
                ORDER BY effective_from DESC LIMIT 1
            """)
            row = cur.fetchone()
    if not row:
        return JSONResponse({"kcal": 2500, "protein_g": 180, "carbs_g": 250, "fat_g": 80, "water_ml": 2500})
    return JSONResponse({"kcal": row[0], "protein_g": float(row[1]), "carbs_g": float(row[2]),
                         "fat_g": float(row[3]), "water_ml": row[4], "effective_from": str(row[5])})


@app.post("/api/targets", status_code=201)
async def set_targets(request: Request):
    body = await request.json()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO health.user_targets (user_id, kcal, protein_g, carbs_g, fat_g, water_ml)
                VALUES (1, %s, %s, %s, %s, %s) RETURNING id
            """, (body.get("kcal", 2500), body.get("protein_g", 180),
                  body.get("carbs_g", 250), body.get("fat_g", 80),
                  body.get("water_ml", 2500)))
            row = cur.fetchone()
    return JSONResponse({"id": row[0]}, status_code=201)


@app.post("/api/quick-log/food", status_code=201)
async def quick_log_food(request: Request):
    """Log a single ingredient directly without a recipe. Body: {ingredient_id, quantity_g, meal_type?}"""
    body = await request.json()
    ingredient_id = body.get("ingredient_id")
    quantity_g = body.get("quantity_g", 100)
    meal_type = body.get("meal_type", "meal")
    if not ingredient_id:
        return JSONResponse({"error": "ingredient_id required"}, status_code=422)
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get ingredient name for the log
            cur.execute("SELECT name FROM health.ingredients WHERE id = %s", (ingredient_id,))
            row = cur.fetchone()
            if not row:
                return JSONResponse({"error": "ingredient not found"}, status_code=404)
            name = row[0]
            # Log as meal with no recipe — create a temporary single-ingredient recipe inline
            cur.execute(
                "INSERT INTO health.meal_logs (user_id, meal_name, meal_type) VALUES (1, %s, %s) RETURNING id, consumed_at",
                (f"{name} ({quantity_g}g)", meal_type)
            )
            r = cur.fetchone()
    return JSONResponse({"id": r[0], "consumed_at": r[1].isoformat()}, status_code=201)


# ─────────────────────────────────────────────────────────────────────────────
# Fitbit OAuth API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/fitbit/connect")
def fitbit_connect():
    if not fitbit_svc.is_configured():
        return JSONResponse({"error": "FITBIT_CLIENT_ID / FITBIT_CLIENT_SECRET not set"}, status_code=400)
    return RedirectResponse(fitbit_svc.auth_url(), status_code=302)


@app.get("/api/fitbit/callback")
async def fitbit_callback(code: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/integrations?error=fitbit_denied", status_code=302)
    try:
        token_data = await fitbit_svc.exchange_code(code)
        with get_conn() as conn:
            fitbit_svc._save_tokens(conn, token_data)
        return RedirectResponse("/integrations?connected=1", status_code=302)
    except Exception as exc:
        logger.error("[FITBIT] Callback error: %s", exc)
        return RedirectResponse("/integrations?error=token_exchange", status_code=302)


@app.delete("/api/fitbit/disconnect")
def fitbit_disconnect():
    with get_conn() as conn:
        fitbit_svc._delete_tokens(conn)
    return JSONResponse({"ok": True})


@app.get("/api/fitbit/status")
def fitbit_status():
    with get_conn() as conn:
        tokens = fitbit_svc._load_tokens(conn)
    if not tokens:
        return JSONResponse({"connected": False})
    expires_at = tokens["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return JSONResponse({
        "connected":   True,
        "expires_at":  expires_at.isoformat(),
        "scope":       tokens["scope"],
    })


@app.post("/api/sync/fitbit")
async def manual_sync():
    result = await sync_vitals_today()
    return JSONResponse(result)


# ─────────────────────────────────────────────────────────────────────────────
# Meal Logs API
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/meal-logs", status_code=201)
async def log_meal(request: Request):
    """Body: { recipe_id?, meal_name?, meal_type? }"""
    body = await request.json()
    recipe_id = body.get("recipe_id")
    meal_name = (body.get("meal_name") or "").strip() or None
    meal_type = (body.get("meal_type") or "meal").strip()

    if not recipe_id and not meal_name:
        return JSONResponse({"error": "recipe_id or meal_name required"}, status_code=422)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO health.meal_logs (user_id, recipe_id, meal_name, meal_type)
                   VALUES (1, %s, %s, %s) RETURNING id, consumed_at""",
                (recipe_id, meal_name, meal_type),
            )
            row = cur.fetchone()
    return JSONResponse({"id": row[0], "consumed_at": row[1].isoformat()}, status_code=201)


@app.get("/api/meal-logs/today")
def todays_meals():
    today = date.today()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ml.id, ml.meal_type, ml.meal_name, ml.consumed_at,
                       r.name AS recipe_name
                FROM health.meal_logs ml
                LEFT JOIN health.recipes r ON r.id = ml.recipe_id
                WHERE ml.user_id = 1
                  AND ml.consumed_at::date = %s
                ORDER BY ml.consumed_at ASC
            """, (today,))
            rows = cur.fetchall()
    return JSONResponse([
        {"id": r[0], "meal_type": r[1], "meal_name": r[4] or r[2],
         "consumed_at": r[3].isoformat()}
        for r in rows
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Quick-log water
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/quick-log/water", status_code=201)
async def quick_log_water(request: Request):
    """Body: { ml: number }  Wraps POST /api/metrics."""
    body = await request.json()
    ml = body.get("ml", 250)
    try:
        ml = float(ml)
    except (TypeError, ValueError):
        return JSONResponse({"error": "ml must be a number"}, status_code=422)
    if ml <= 0 or ml > 5000:
        return JSONResponse({"error": "ml out of range"}, status_code=422)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health.metrics (metric_type, value, unit) VALUES ('water_ml', %s, 'ml') RETURNING id, created_at",
                (ml,),
            )
            row = cur.fetchone()
    return JSONResponse({"id": row[0], "ml": ml, "created_at": row[1].isoformat()}, status_code=201)


@app.get("/api/quick-log/water/today")
def water_today():
    today = date.today()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(value), 0)::float
                FROM health.metrics
                WHERE metric_type = 'water_ml'
                  AND created_at::date = %s AND user_id = 1
            """, (today,))
            total = cur.fetchone()[0]
    return JSONResponse({"total_ml": total, "total_L": round(total / 1000, 2)})


# ─────────────────────────────────────────────────────────────────────────────
# Insights data API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/insights/chart")
def insights_chart(days: int = 30):
    """Return per-day sleep_hrs and workout volume for the dual-axis chart."""
    days = min(max(days, 7), 90)
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Sleep per day
            cur.execute("""
                SELECT created_at::date AS day, AVG(value)::float
                FROM health.metrics
                WHERE metric_type = 'sleep_hrs'
                  AND created_at >= NOW() - INTERVAL '1 day' * %s
                GROUP BY day ORDER BY day ASC
            """, (days,))
            sleep_rows = {str(r[0]): round(r[1], 2) for r in cur.fetchall()}

            # Workout volume per day
            cur.execute("""
                SELECT wl.started_at::date AS day,
                       COALESCE(SUM(ws.volume), 0)::float AS vol
                FROM health.workout_logs wl
                LEFT JOIN health.workout_sets ws ON ws.log_id = wl.id
                WHERE wl.started_at >= NOW() - INTERVAL '1 day' * %s
                  AND wl.user_id = 1
                GROUP BY day ORDER BY day ASC
            """, (days,))
            volume_rows = {str(r[0]): round(r[1], 1) for r in cur.fetchall()}

            # Steps + resting HR summary
            cur.execute("""
                SELECT metric_type, AVG(value)::float
                FROM health.metrics
                WHERE metric_type IN ('steps', 'resting_hr', 'sleep_hrs')
                  AND created_at >= NOW() - INTERVAL '7 days'
                GROUP BY metric_type
            """)
            summary_raw = {r[0]: round(r[1], 1) for r in cur.fetchall()}

    # Build merged date list
    all_days = sorted(set(sleep_rows) | set(volume_rows))
    chart = [
        {
            "date":   d,
            "sleep":  sleep_rows.get(d),
            "volume": volume_rows.get(d, 0),
        }
        for d in all_days
    ]
    return JSONResponse({"chart": chart, "summary": summary_raw})


# ─────────────────────────────────────────────────────────────────────────────
# FCM test notification
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/notifications/test")
async def test_notification():
    ok = await notif_svc.send_fcm(
        title="HealthOS",
        body="Test notification — your setup is working.",
    )
    return JSONResponse({"sent": ok})
