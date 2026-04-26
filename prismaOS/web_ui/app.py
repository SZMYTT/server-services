import sys
import os
import json
import datetime
from pathlib import Path
from fastapi import FastAPI, Request, Form, Response, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from web_ui.auth import (
    get_current_user, require_user, require_admin, get_user_db,
    verify_password, create_session_token, update_last_login,
    user_exists_in_db, require_workspace_access,
)
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.queue import get_full_queue, approve_task, decline_task, _get_conn, add_task
from services.erp_notifier import (
    notify_new_order, notify_new_booking, notify_low_stock,
    notify_new_auction, notify_auction_won, notify_watchlist_update,
)

app = FastAPI(title="PrismaOS Web UI")

BASE_DIR = Path(__file__).resolve().parent

# Ensure static and templates dirs exist
os.makedirs(BASE_DIR / "static" / "css", exist_ok=True)
os.makedirs(BASE_DIR / "static" / "js", exist_ok=True)
os.makedirs(BASE_DIR / "templates", exist_ok=True)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# --- Helpers ---

def fetch_schedules(workspaces: list[str] = None):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT id, workspace, name, task_type, module, cron_expression, active,
                       last_run, next_run
                FROM schedules
            """
            params = []
            if workspaces and "all_workspaces" not in workspaces:
                query += " WHERE workspace = ANY(%s) "
                params.append((workspaces,))
            query += " ORDER BY next_run ASC NULLS LAST "
            cur.execute(query, tuple(params))
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    except Exception as e:
        print("Error fetching schedules", e)
        return []
    finally:
        conn.close()

def fetch_recent_tasks(workspaces: list = None, limit: int = 10):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            ws_filter = ""
            params = []
            if workspaces and "all_workspaces" not in workspaces:
                ws_filter = "WHERE workspace = ANY(%s)"
                params.append(workspaces)
            cur.execute(
                f"SELECT id, workspace, user_name, task_type, module, status, risk_level, "
                f"input, output, created_at, completed_at FROM tasks "
                f"{ws_filter} ORDER BY created_at DESC LIMIT %s",
                (*params, limit)
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        print("Error fetching recent tasks", e)
        return []
    finally:
        conn.close()


def fetch_workspace_stats(workspace: str) -> dict:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            today = datetime.date.today()
            week_ago = today - datetime.timedelta(days=7)
            cur.execute("""
                SELECT
                  (SELECT count(*) FROM tasks WHERE workspace=%s AND created_at::date = %s) as today,
                  (SELECT count(*) FROM tasks WHERE workspace=%s AND status IN ('queued','pending_approval','running')) as pending,
                  (SELECT count(*) FROM tasks WHERE workspace=%s AND status='done' AND created_at >= %s) as done_week,
                  (SELECT count(*) FROM tasks WHERE workspace=%s AND status='failed' AND created_at >= %s) as failed
            """, (workspace, today, workspace, workspace, week_ago, workspace, week_ago))
            row = cur.fetchone()
            return {"tasks_today": row[0], "pending": row[1], "done_week": row[2], "failed": row[3]}
    except Exception as e:
        print("Error fetching workspace stats", e)
        return {"tasks_today": 0, "pending": 0, "done_week": 0, "failed": 0}
    finally:
        conn.close()

def get_dashboard_metrics(workspaces: list[str] = None):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            ws_filter = ""
            params = []
            if workspaces and "all_workspaces" not in workspaces:
                ws_filter = " AND workspace = ANY(%s) "
                params = [(workspaces,), (workspaces,), (workspaces,), (workspaces,)]

            query = f"""
                SELECT
                    (SELECT count(*) FROM tasks WHERE status IN ('queued', 'running', 'pending_approval') {ws_filter}) as active_tasks,
                    (SELECT count(*) FROM tasks WHERE status = 'done' {ws_filter}) as completed_tasks,
                    (SELECT count(*) FROM tasks WHERE status = 'pending_approval' {ws_filter}) as pending_approvals,
                    (SELECT count(*) FROM schedules WHERE active=true {ws_filter.replace('workspace', 'workspace')}) as active_schedules
            """
            cur.execute(query, tuple(params))
            row = cur.fetchone()
            if row:
                return {
                    "active_tasks": row[0],
                    "completed_tasks": row[1],
                    "pending_approvals": row[2],
                    "active_schedules": row[3]
                }
    except Exception as e:
        print("Error fetching metrics", e)
    finally:
        conn.close()
    return {"active_tasks": 0, "completed_tasks": 0, "pending_approvals": 0, "active_schedules": 0}

# --- ERP Helpers ---

def _fetch_rows(query: str, params: tuple = ()) -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        print("DB error:", e)
        return []
    finally:
        conn.close()

def _exec(query: str, params: tuple = ()):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
        conn.commit()
        return True
    except Exception as e:
        print("DB write error:", e)
        conn.rollback()
        return False
    finally:
        conn.close()

def _insert_return_id(query: str, params: tuple = ()):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
        conn.commit()
        return str(row[0]) if row else None
    except Exception as e:
        print("DB insert error:", e)
        conn.rollback()
        return None
    finally:
        conn.close()


def audit(username: str, action: str, resource: str, resource_id: str = None, workspace: str = None, ip: str = None, details: dict = None):
    """Fire-and-forget audit log entry."""
    import json as _json
    try:
        _exec(
            "INSERT INTO audit_log (username, action, resource, resource_id, workspace, ip_address, details) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (username, action, resource, resource_id, workspace, ip,
             _json.dumps(details) if details else None),
        )
    except Exception as exc:
        print("audit log error:", exc)

# Candles
def fetch_candles_orders(limit: int = 100) -> list[dict]:
    return _fetch_rows(
        "SELECT * FROM candles_orders ORDER BY order_date DESC LIMIT %s", (limit,)
    )

def fetch_candles_inventory() -> list[dict]:
    return _fetch_rows("SELECT * FROM candles_inventory ORDER BY category, name")

def fetch_candles_content(limit: int = 50) -> list[dict]:
    return _fetch_rows(
        "SELECT * FROM candles_content ORDER BY publish_date DESC NULLS LAST, created_at DESC LIMIT %s", (limit,)
    )

def fetch_candles_order_stats() -> dict:
    rows = _fetch_rows("""
        SELECT
            COUNT(*) FILTER (WHERE status NOT IN ('cancelled','refunded')) as total,
            COUNT(*) FILTER (WHERE status='open') as open_orders,
            COUNT(*) FILTER (WHERE status='processing') as processing,
            COALESCE(SUM(total) FILTER (WHERE status NOT IN ('cancelled','refunded')), 0) as revenue
        FROM candles_orders
    """)
    return rows[0] if rows else {"total": 0, "open_orders": 0, "processing": 0, "revenue": 0}

# Cars
def fetch_cars_auctions(limit: int = 100) -> list[dict]:
    return _fetch_rows(
        "SELECT * FROM cars_auctions ORDER BY created_at DESC LIMIT %s", (limit,)
    )

def fetch_cars_vehicles() -> list[dict]:
    rows = _fetch_rows("SELECT * FROM cars_vehicles ORDER BY created_at DESC")
    for v in rows:
        buy = float(v.get("buy_price") or 0)
        repair = float(v.get("repair_costs") or 0)
        sell = float(v.get("sell_price") or 0)
        v["profit"] = round(sell - buy - repair, 2) if v.get("sell_price") else None
        v["total_cost"] = round(buy + repair, 2)
    return rows

def fetch_cars_documents(vehicle_id: str = None) -> list[dict]:
    if vehicle_id:
        return _fetch_rows(
            "SELECT d.*, v.make, v.model, v.year FROM cars_documents d LEFT JOIN cars_vehicles v ON v.id=d.vehicle_id WHERE d.vehicle_id=%s ORDER BY d.created_at DESC",
            (vehicle_id,)
        )
    return _fetch_rows(
        "SELECT d.*, v.make, v.model, v.year FROM cars_documents d LEFT JOIN cars_vehicles v ON v.id=d.vehicle_id ORDER BY d.created_at DESC"
    )

def fetch_cars_vehicle_stats() -> dict:
    rows = _fetch_rows("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status='sourced')   as sourced,
            COUNT(*) FILTER (WHERE status='prepping')  as prepping,
            COUNT(*) FILTER (WHERE status='listed')    as listed,
            COUNT(*) FILTER (WHERE status='sold') as sold,
            COALESCE(SUM(sell_price - buy_price - repair_costs) FILTER (WHERE status='sold'), 0) as total_profit
        FROM cars_vehicles
    """)
    return rows[0] if rows else {"total": 0, "sourced": 0, "prepping": 0, "listed": 0, "sold": 0, "total_profit": 0}

# Property
def fetch_property_deals() -> list[dict]:
    return _fetch_rows("SELECT * FROM property_deals ORDER BY updated_at DESC")

def fetch_property_watchlist() -> list[dict]:
    return _fetch_rows(
        "SELECT * FROM property_watchlist WHERE status != 'archived' ORDER BY created_at DESC"
    )

def fetch_property_documents() -> list[dict]:
    return _fetch_rows(
        "SELECT d.*, p.address FROM property_documents d LEFT JOIN property_deals p ON p.id=d.deal_id ORDER BY d.created_at DESC"
    )

def fetch_property_deal_stats() -> dict:
    rows = _fetch_rows("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status NOT IN ('completed','lost')) as active,
            COUNT(*) FILTER (WHERE status='completed') as completed,
            COALESCE(SUM(agreed_price - estimated_costs) FILTER (WHERE status='completed'), 0) as realised_profit
        FROM property_deals
    """)
    return rows[0] if rows else {"total": 0, "active": 0, "completed": 0, "realised_profit": 0}

# Nursing
def fetch_nursing_bookings(limit: int = 100, upcoming_only: bool = False) -> list[dict]:
    if upcoming_only:
        return _fetch_rows(
            "SELECT * FROM nursing_bookings WHERE booking_date >= CURRENT_DATE ORDER BY booking_date, booking_time LIMIT %s",
            (limit,)
        )
    return _fetch_rows(
        "SELECT * FROM nursing_bookings ORDER BY booking_date DESC, booking_time DESC LIMIT %s", (limit,)
    )

def fetch_nursing_clients() -> list[dict]:
    return _fetch_rows("SELECT * FROM nursing_clients WHERE active=true ORDER BY name")

def fetch_nursing_services_list() -> list[dict]:
    return _fetch_rows("SELECT * FROM nursing_services WHERE active=true ORDER BY category, name")

def fetch_nursing_booking_stats() -> dict:
    rows = _fetch_rows("""
        SELECT
            COUNT(*) FILTER (WHERE booking_date >= CURRENT_DATE AND status='confirmed') as upcoming,
            COUNT(*) FILTER (WHERE booking_date >= date_trunc('week', CURRENT_DATE) AND status='completed') as done_week,
            COALESCE(SUM(amount) FILTER (WHERE booking_date >= date_trunc('month', CURRENT_DATE) AND status='completed'), 0) as revenue_month,
            COUNT(DISTINCT client_id) as total_clients
        FROM nursing_bookings
    """)
    return rows[0] if rows else {"upcoming": 0, "done_week": 0, "revenue_month": 0, "total_clients": 0}

def fetch_nursing_content(limit: int = 50) -> list[dict]:
    return _fetch_rows(
        "SELECT * FROM nursing_content ORDER BY publish_date DESC NULLS LAST, created_at DESC LIMIT %s", (limit,)
    )

# Food Brand
def fetch_food_content(limit: int = 50) -> list[dict]:
    return _fetch_rows(
        "SELECT * FROM food_content ORDER BY publish_date DESC NULLS LAST, created_at DESC LIMIT %s", (limit,)
    )

def fetch_food_partnerships(limit: int = 50) -> list[dict]:
    return _fetch_rows(
        "SELECT * FROM food_partnerships ORDER BY created_at DESC LIMIT %s", (limit,)
    )

def fetch_food_ideas(limit: int = 100) -> list[dict]:
    return _fetch_rows(
        "SELECT * FROM food_ideas WHERE status != 'archived' ORDER BY priority DESC, created_at DESC LIMIT %s",
        (limit,)
    )

def fetch_food_content_stats() -> dict:
    rows = _fetch_rows("""
        SELECT
            COUNT(*) FILTER (WHERE status='published') as published,
            COUNT(*) FILTER (WHERE status IN ('draft','scheduled')) as pending,
            COALESCE(SUM(likes) FILTER (WHERE status='published'), 0) as total_likes,
            COALESCE(SUM(views) FILTER (WHERE status='published'), 0) as total_views
        FROM food_content
    """)
    return rows[0] if rows else {"published": 0, "pending": 0, "total_likes": 0, "total_views": 0}

# Finance
def fetch_finance_transactions(workspace: str, limit: int = 100) -> list[dict]:
    return _fetch_rows(
        "SELECT * FROM finance_transactions WHERE workspace=%s ORDER BY date DESC, created_at DESC LIMIT %s",
        (workspace, limit)
    )

def fetch_finance_summary(workspace: str) -> dict:
    rows = _fetch_rows("""
        SELECT
            COALESCE(SUM(amount) FILTER (WHERE type='income'), 0) as total_income,
            COALESCE(SUM(amount) FILTER (WHERE type='expense'), 0) as total_expenses,
            COALESCE(SUM(amount) FILTER (WHERE type='income' AND date >= date_trunc('month', CURRENT_DATE)), 0) as income_this_month,
            COALESCE(SUM(amount) FILTER (WHERE type='expense' AND date >= date_trunc('month', CURRENT_DATE)), 0) as expenses_this_month
        FROM finance_transactions WHERE workspace=%s
    """, (workspace,))
    r = rows[0] if rows else {}
    r["net"] = float(r.get("total_income", 0)) - float(r.get("total_expenses", 0))
    r["net_this_month"] = float(r.get("income_this_month", 0)) - float(r.get("expenses_this_month", 0))
    return r

def fetch_all_finance_summary() -> list[dict]:
    workspaces = ["candles", "cars", "property", "nursing_massage", "food_brand"]
    result = []
    for ws in workspaces:
        s = fetch_finance_summary(ws)
        s["workspace"] = ws
        s["name"] = WORKSPACE_META.get(ws, {}).get("name", ws.title())
        result.append(s)
    return result

# --- HTML Routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="login.html", context={})

@app.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    env_users = get_user_db()
    # Username must exist in environment.yaml AND have a password row in DB
    if username in env_users and user_exists_in_db(username) and verify_password(username, password):
        update_last_login(username)
        _exec(
            "INSERT INTO audit_log (username, action, resource, ip_address) VALUES (%s,'LOGIN','session',%s)",
            (username, request.client.host if request.client else None),
        )
        token = create_session_token(username)
        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=43200,  # 12 hours
        )
        return response
    _exec(
        "INSERT INTO audit_log (username, action, resource, ip_address, details) VALUES (%s,'LOGIN_FAILED','session',%s,'{}') ON CONFLICT DO NOTHING",
        (username or "unknown", request.client.host if request.client else None),
    )
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": "Invalid username or password."},
    )

@app.get("/logout")
async def logout(request: Request):
    user = get_current_user(request)
    if user:
        _exec(
            "INSERT INTO audit_log (username, action, resource) VALUES (%s,'LOGOUT','session')",
            (user["username"],),
        )
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("session_token")
    return response

def get_user_primary_ws(user: dict) -> str:
    if user.get("role") == "operator":
        return "operator"
    access = user.get("access", [])
    if access and access[0] != "all_workspaces":
        return access[0]
    return "operator"

@app.get("/", response_class=HTMLResponse)
async def view_dashboard(request: Request, user: dict = Depends(require_user)):
    ws_id = get_user_primary_ws(user)
    if ws_id != "operator":
        return RedirectResponse(url=f"/{ws_id}/overview", status_code=status.HTTP_302_FOUND)
        
    metrics = get_dashboard_metrics(user.get("access"))
    recent_tasks = fetch_recent_tasks(user.get("access"), limit=8)
    now_hour = datetime.datetime.now().hour
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "metrics": metrics,
            "recent_tasks": recent_tasks,
            "now_hour": now_hour,
            "active_route": "overview",
            "active_ws": "operator",
            "ws_meta": WORKSPACE_META,
            "user": user,
        }
    )

@app.get("/queue", response_class=HTMLResponse)
async def view_queue(request: Request, user: dict = Depends(require_user)):
    if get_user_primary_ws(user) != "operator":
        return RedirectResponse(url="/")
    tasks = await get_full_queue(user.get("access"))
    return templates.TemplateResponse(
        request=request, 
        name="queue.html", 
        context={"tasks": tasks, "active_route": "queue", "active_ws": "operator", "ws_meta": WORKSPACE_META, "user": user}
    )

@app.get("/approvals", response_class=HTMLResponse)
async def view_approvals(request: Request, user: dict = Depends(require_user)):
    if get_user_primary_ws(user) != "operator":
        return RedirectResponse(url="/")
    tasks = await get_full_queue(user.get("access"))
    pending = [t for t in tasks if t["status"] in ("queued", "pending_approval", "pending_publish")]
    return templates.TemplateResponse(
        request=request, 
        name="approvals.html", 
        context={"tasks": pending, "active_route": "approvals", "active_ws": "operator", "ws_meta": WORKSPACE_META, "user": user}
    )

@app.get("/schedules", response_class=HTMLResponse)
async def view_schedules(request: Request, user: dict = Depends(require_user)):
    if get_user_primary_ws(user) != "operator":
        return RedirectResponse(url="/")
    scheds = fetch_schedules(user.get("access"))
    return templates.TemplateResponse(
        request=request, 
        name="schedules.html", 
        context={"schedules": scheds, "active_route": "schedules", "active_ws": "operator", "ws_meta": WORKSPACE_META, "user": user}
    )

# --- Workspace dashboard routes ---

# ── Module registry ───────────────────────────────────────────
# Source of truth for every module that can exist in a workspace.
# available_for: list of ws_ids OR ["all"]
# core: True = cannot be disabled
# settings_schema: list of editable fields shown in the settings panel
MODULE_REGISTRY = {
    "overview":     {"name": "Overview",        "icon": "📊", "core": True,  "available_for": ["all"], "settings_schema": []},
    "task":         {"name": "New Task",         "icon": "⚡", "core": True,  "available_for": ["all"], "settings_schema": []},
    "finance":      {"name": "Finance",          "icon": "💰", "core": False, "available_for": ["all"], "settings_schema": []},
    "content":      {"name": "Content Calendar", "icon": "✍️", "core": False,
                     "available_for": ["candles", "nursing_massage", "food_brand", "cars"],
                     "settings_schema": [
                         {"key": "default_platform", "label": "Default Platform", "type": "text", "placeholder": "e.g. instagram"},
                     ]},
    "messages":     {"name": "Messages",         "icon": "💬", "core": False, "available_for": ["candles", "nursing_massage"], "settings_schema": []},
    "orders":       {"name": "Orders",           "icon": "📦", "core": False, "available_for": ["candles"], "settings_schema": []},
    "stock":        {"name": "Inventory",        "icon": "🏷️",  "core": False, "available_for": ["candles"], "settings_schema": []},
    "bookings":     {"name": "Bookings",         "icon": "📅", "core": False, "available_for": ["nursing_massage"],
                     "settings_schema": [
                         {"key": "public_booking_url", "label": "Public Booking Page", "type": "readonly_link", "path": "/book/nursing"},
                     ]},
    "auctions":     {"name": "Auction Alerts",   "icon": "🔨", "core": False, "available_for": ["cars"], "settings_schema": []},
    "inventory":    {"name": "Vehicles",         "icon": "🚗", "core": False, "available_for": ["cars"], "settings_schema": []},
    "documents":    {"name": "Documents",        "icon": "📄", "core": False, "available_for": ["cars"], "settings_schema": []},
    "deals":        {"name": "Deal Pipeline",    "icon": "🤝", "core": False, "available_for": ["property"], "settings_schema": []},
    "research":     {"name": "Research",         "icon": "🔍", "core": False, "available_for": ["property"], "settings_schema": []},
    "legal":        {"name": "Legal",            "icon": "⚖️",  "core": False, "available_for": ["property"], "settings_schema": []},
    "ideas":        {"name": "Ideas Board",      "icon": "💡", "core": False, "available_for": ["food_brand"], "settings_schema": []},
    "partnerships": {"name": "Partnerships",     "icon": "🤝", "core": False, "available_for": ["food_brand"], "settings_schema": []},
    "analytics":    {"name": "Analytics",        "icon": "📈", "core": False, "available_for": ["food_brand"], "settings_schema": []},
    "brand_guide":  {"name": "Brand Guide",      "icon": "🎨", "core": False, "available_for": ["all"],
                     "settings_schema": [
                         {"key": "public_questionnaire_url", "label": "Public Questionnaire", "type": "readonly_link", "path_template": "/brand-guide/{ws_id}"},
                     ]},
}

WORKSPACE_META = {
    "operator": {
        "name": "Operator", "icon": "⚡", "owner": "Daniel", "platform": "PrismaOS", "rgb": "167, 139, 250",
        "default_modules": [],
    },
    "candles": {
        "name": "Candles", "icon": "🕯️", "owner": "Alice", "platform": "Etsy", "rgb": "245, 180, 80",
        "default_modules": ["overview", "task", "orders", "stock", "content", "messages", "finance"],
    },
    "cars": {
        "name": "Cars", "icon": "🚗", "owner": "Eddie", "platform": "Facebook", "rgb": "100, 149, 237",
        "default_modules": ["overview", "task", "auctions", "inventory", "documents", "finance"],
    },
    "property": {
        "name": "Property", "icon": "🏠", "owner": "Daniel", "platform": "Private", "rgb": "120, 160, 140",
        "default_modules": ["overview", "task", "deals", "research", "finance", "legal"],
    },
    "nursing_massage": {
        "name": "Nursing", "icon": "🩺", "owner": "Asta", "platform": "Facebook", "rgb": "230, 140, 160",
        "default_modules": ["overview", "task", "bookings", "content", "messages", "finance"],
    },
    "food_brand": {
        "name": "Food Brand", "icon": "🥗", "owner": "Alicja", "platform": "Instagram", "rgb": "170, 190, 100",
        "default_modules": ["overview", "task", "content", "ideas", "partnerships", "analytics", "finance"],
    },
}

# ── Module nav helpers ───────────────────────────────────────

def _seed_workspace_modules(ws_id: str) -> None:
    defaults = WORKSPACE_META.get(ws_id, {}).get("default_modules", [])
    if not defaults:
        return
    try:
        conn = _get_conn()
        cur = conn.cursor()
        for i, mod in enumerate(defaults):
            cur.execute(
                "INSERT INTO workspace_modules (workspace, module, enabled, sort_order) VALUES (%s,%s,true,%s) ON CONFLICT DO NOTHING",
                (ws_id, mod, i),
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        import logging; logging.getLogger("prisma.app").warning("seed_workspace_modules: %s", exc)


def get_workspace_nav(ws_id: str) -> list[dict]:
    """Returns ordered list of enabled module dicts for a workspace nav."""
    rows = _fetch_rows(
        "SELECT module, enabled, sort_order FROM workspace_modules WHERE workspace=%s ORDER BY sort_order ASC, module ASC",
        (ws_id,),
    )
    if not rows:
        _seed_workspace_modules(ws_id)
        defaults = WORKSPACE_META.get(ws_id, {}).get("default_modules", [])
        rows = [{"module": m, "enabled": True, "sort_order": i} for i, m in enumerate(defaults)]

    result = []
    for row in rows:
        if not row.get("enabled"):
            continue
        mod_key = row["module"]
        reg = MODULE_REGISTRY.get(mod_key, {})
        result.append({
            "module":     mod_key,
            "name":       reg.get("name", mod_key.replace("_", " ").title()),
            "icon":       reg.get("icon", ""),
            "sort_order": row["sort_order"],
            "core":       reg.get("core", False),
        })
    return result


def get_all_workspace_modules(ws_id: str) -> list[dict]:
    """Returns ALL modules available for a workspace (enabled + disabled) for the settings page."""
    db_rows = {r["module"]: r for r in _fetch_rows(
        "SELECT module, enabled, sort_order, settings FROM workspace_modules WHERE workspace=%s",
        (ws_id,),
    )}
    default_mods = WORKSPACE_META.get(ws_id, {}).get("default_modules", [])

    result = []
    for mod_key, reg in MODULE_REGISTRY.items():
        avail = reg.get("available_for", [])
        if "all" not in avail and ws_id not in avail:
            continue
        db_row = db_rows.get(mod_key, {})
        # Resolve readonly_link paths
        schema = []
        for field in reg.get("settings_schema", []):
            f = dict(field)
            if f.get("type") == "readonly_link":
                tmpl = f.pop("path_template", None)
                if tmpl:
                    f["path"] = tmpl.replace("{ws_id}", ws_id)
            schema.append(f)
        result.append({
            "module":          mod_key,
            "name":            reg["name"],
            "icon":            reg.get("icon", ""),
            "core":            reg.get("core", False),
            "enabled":         db_row.get("enabled", mod_key in default_mods),
            "sort_order":      db_row.get("sort_order", default_mods.index(mod_key) if mod_key in default_mods else 99),
            "settings":        db_row.get("settings") or {},
            "settings_schema": schema,
        })
    result.sort(key=lambda x: (x["sort_order"], x["module"]))
    return result


# ── Public booking page (no auth) ────────────────────────────
@app.get("/book/nursing", response_class=HTMLResponse)
async def public_nursing_booking_page(request: Request):
    services = fetch_nursing_services_list()
    return templates.TemplateResponse(
        request=request,
        name="public/booking_nursing.html",
        context={"services": services},
    )

class PublicNursingBookingPayload(BaseModel):
    client_name: str
    phone: str = ""
    email: str = ""
    service_id: str = None
    service_name: str = ""
    preferred_date: str
    preferred_time: str
    notes: str = ""

@app.post("/api/public/book/nursing")
async def api_public_nursing_booking(request: Request, p: PublicNursingBookingPayload):
    client_id = None
    if p.phone or p.email:
        existing = _fetch_rows(
            "SELECT id FROM nursing_clients WHERE phone=%s OR email=%s LIMIT 1",
            (p.phone or None, p.email or None),
        )
        if existing:
            client_id = str(existing[0]["id"])
        else:
            new_id = _insert_return_id(
                "INSERT INTO nursing_clients (name, phone, email) VALUES (%s,%s,%s) RETURNING id",
                (p.client_name, p.phone or None, p.email or None),
            )
            client_id = str(new_id) if new_id else None

    duration = 60
    amount = 0.0
    if p.service_id:
        svc = _fetch_rows(
            "SELECT duration_mins, price FROM nursing_services WHERE id=%s", (p.service_id,)
        )
        if svc:
            duration = svc[0]["duration_mins"]
            amount = float(svc[0]["price"])

    id_ = _insert_return_id(
        """INSERT INTO nursing_bookings
           (client_name, client_id, service_name, service_id, booking_date, booking_time,
            duration_mins, amount, status, notes)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (p.client_name, client_id, p.service_name or "To be discussed",
         p.service_id or None, p.preferred_date, p.preferred_time,
         duration, amount, "pending", p.notes),
    )
    if id_:
        await notify_new_booking({
            "client_name": p.client_name,
            "service_name": p.service_name or "To be discussed",
            "appointment_at": f"{p.preferred_date} {p.preferred_time}",
        })
    return {"success": bool(id_), "id": id_}


@app.get("/{ws_id}/overview", response_class=HTMLResponse)
async def view_workspace_overview(ws_id: str, request: Request, user: dict = Depends(require_user)):
    meta = WORKSPACE_META.get(ws_id)
    if not meta:
        return RedirectResponse(url="/")

    access = user.get("access", [])
    if "all_workspaces" not in access and ws_id not in access:
        return RedirectResponse(url="/")

    ws_modules   = get_workspace_nav(ws_id)
    stats        = fetch_workspace_stats(ws_id)
    recent_tasks = fetch_recent_tasks([ws_id], limit=5)
    ws_schedules = fetch_schedules([ws_id])
    pending_tasks = [t for t in fetch_recent_tasks([ws_id], limit=20) if t["status"] in ("queued", "pending_approval")]

    return templates.TemplateResponse(
        request=request,
        name="workspace.html",
        context={
            "ws_id":        ws_id,
            "ws_name":      meta["name"],
            "ws_icon":      meta["icon"],
            "ws_owner":     meta["owner"],
            "ws_platform":  meta["platform"],
            "stats":        stats,
            "recent_tasks": recent_tasks,
            "ws_schedules": ws_schedules,
            "pending_tasks":pending_tasks,
            "active_route": "overview",
            "active_ws":    ws_id,
            "ws_meta":      WORKSPACE_META,
            "ws_modules":   ws_modules,
            "user":         user,
        }
    )

@app.get("/{ws_id}/settings", response_class=HTMLResponse)
async def view_workspace_settings(ws_id: str, request: Request, user: dict = Depends(require_user)):
    meta = WORKSPACE_META.get(ws_id)
    if not meta:
        return RedirectResponse(url="/")
    access = user.get("access", [])
    if "all_workspaces" not in access and ws_id not in access and user.get("role") != "operator":
        return RedirectResponse(url="/")
    ws_modules  = get_workspace_nav(ws_id)
    all_modules = get_all_workspace_modules(ws_id)
    return templates.TemplateResponse(
        request=request,
        name="erp/ws_settings.html",
        context={
            "ws_id":       ws_id,
            "active_route":"settings",
            "active_ws":   ws_id,
            "ws_meta":     WORKSPACE_META,
            "ws_modules":  ws_modules,
            "all_modules": all_modules,
            "user":        user,
        },
    )


@app.get("/{ws_id}/{module}", response_class=HTMLResponse)
async def view_workspace_module(ws_id: str, module: str, request: Request, user: dict = Depends(require_user)):
    meta = WORKSPACE_META.get(ws_id)
    if not meta:
        return RedirectResponse(url="/")

    access = user.get("access", [])
    if "all_workspaces" not in access and ws_id not in access:
        return RedirectResponse(url="/")

    ws_modules = get_workspace_nav(ws_id)
    valid_modules = [m["module"] for m in ws_modules]
    if module not in valid_modules:
        return RedirectResponse(url=f"/{ws_id}/overview")

    base_ctx = {
        "ws_id":      ws_id,
        "active_route": module,
        "active_ws":  ws_id,
        "ws_meta":    WORKSPACE_META,
        "ws_modules": ws_modules,
        "user":       user,
    }

    if module == "task":
        return templates.TemplateResponse(request=request, name="task_input.html", context=base_ctx)

    # ── Candles ERP ──────────────────────────────────────────
    if ws_id == "candles" and module == "orders":
        return templates.TemplateResponse(request=request, name="erp/candles_orders.html", context={
            **base_ctx,
            "orders": fetch_candles_orders(),
            "stats": fetch_candles_order_stats(),
        })
    if ws_id == "candles" and module == "stock":
        inv = fetch_candles_inventory()
        return templates.TemplateResponse(request=request, name="erp/candles_stock.html", context={
            **base_ctx,
            "raw_materials": [i for i in inv if i["category"] == "raw"],
            "finished_goods": [i for i in inv if i["category"] == "finished"],
            "low_stock": [i for i in inv if float(i.get("quantity") or 0) <= float(i.get("reorder_level") or 0) and float(i.get("reorder_level") or 0) > 0],
        })
    if ws_id == "candles" and module == "content":
        return templates.TemplateResponse(request=request, name="erp/content_calendar.html", context={
            **base_ctx,
            "posts": fetch_candles_content(),
            "content_table": "candles_content",
            "platforms": ["etsy", "instagram", "tiktok", "pinterest"],
        })
    if ws_id == "candles" and module == "finance":
        return templates.TemplateResponse(request=request, name="erp/finance.html", context={
            **base_ctx,
            "transactions": fetch_finance_transactions(ws_id),
            "summary": fetch_finance_summary(ws_id),
        })

    # ── Cars ERP ─────────────────────────────────────────────
    if ws_id == "cars" and module == "auctions":
        return templates.TemplateResponse(request=request, name="erp/cars_auctions.html", context={
            **base_ctx,
            "auctions": fetch_cars_auctions(),
        })
    if ws_id == "cars" and module == "inventory":
        return templates.TemplateResponse(request=request, name="erp/cars_inventory.html", context={
            **base_ctx,
            "vehicles": fetch_cars_vehicles(),
            "stats": fetch_cars_vehicle_stats(),
        })
    if ws_id == "cars" and module == "documents":
        return templates.TemplateResponse(request=request, name="erp/cars_documents.html", context={
            **base_ctx,
            "documents": fetch_cars_documents(),
            "vehicles": fetch_cars_vehicles(),
        })
    if ws_id == "cars" and module == "finance":
        return templates.TemplateResponse(request=request, name="erp/finance.html", context={
            **base_ctx,
            "transactions": fetch_finance_transactions(ws_id),
            "summary": fetch_finance_summary(ws_id),
        })

    # ── Property ERP ─────────────────────────────────────────
    if ws_id == "property" and module == "deals":
        deals = fetch_property_deals()
        pipeline_order = ["prospect", "offer_made", "under_offer", "due_diligence", "exchanged", "completed", "lost"]
        return templates.TemplateResponse(request=request, name="erp/property_deals.html", context={
            **base_ctx,
            "deals": deals,
            "stats": fetch_property_deal_stats(),
            "pipeline_order": pipeline_order,
        })
    if ws_id == "property" and module == "research":
        return templates.TemplateResponse(request=request, name="erp/property_research.html", context={
            **base_ctx,
            "watchlist": fetch_property_watchlist(),
        })
    if ws_id == "property" and module == "finance":
        return templates.TemplateResponse(request=request, name="erp/finance.html", context={
            **base_ctx,
            "transactions": fetch_finance_transactions(ws_id),
            "summary": fetch_finance_summary(ws_id),
        })

    # ── Nursing / Massage ERP ─────────────────────────────────
    if ws_id == "nursing_massage" and module == "bookings":
        return templates.TemplateResponse(request=request, name="erp/bookings.html", context={
            **base_ctx,
            "bookings": fetch_nursing_bookings(upcoming_only=False),
            "upcoming": fetch_nursing_bookings(limit=10, upcoming_only=True),
            "clients": fetch_nursing_clients(),
            "services": fetch_nursing_services_list(),
            "stats": fetch_nursing_booking_stats(),
        })
    if ws_id == "nursing_massage" and module == "content":
        return templates.TemplateResponse(request=request, name="erp/content_calendar.html", context={
            **base_ctx,
            "posts": fetch_nursing_content(),
            "content_table": "nursing_content",
            "platforms": ["facebook", "instagram"],
        })
    if ws_id == "nursing_massage" and module == "finance":
        return templates.TemplateResponse(request=request, name="erp/finance.html", context={
            **base_ctx,
            "transactions": fetch_finance_transactions(ws_id),
            "summary": fetch_finance_summary(ws_id),
        })

    # ── Food Brand ERP ────────────────────────────────────────
    if ws_id == "food_brand" and module == "content":
        return templates.TemplateResponse(request=request, name="erp/content_calendar.html", context={
            **base_ctx,
            "posts": fetch_food_content(),
            "content_table": "food_content",
            "platforms": ["instagram", "tiktok", "youtube", "blog"],
            "show_analytics": True,
        })
    if ws_id == "food_brand" and module == "ideas":
        return templates.TemplateResponse(request=request, name="erp/food_ideas.html", context={
            **base_ctx,
            "ideas": fetch_food_ideas(),
        })
    if ws_id == "food_brand" and module == "partnerships":
        return templates.TemplateResponse(request=request, name="erp/food_partnerships.html", context={
            **base_ctx,
            "partnerships": fetch_food_partnerships(),
        })
    if ws_id == "food_brand" and module == "finance":
        return templates.TemplateResponse(request=request, name="erp/finance.html", context={
            **base_ctx,
            "transactions": fetch_finance_transactions(ws_id),
            "summary": fetch_finance_summary(ws_id),
        })

    # ── Fallback stub ─────────────────────────────────────────
    module_name = next((m["name"] for m in ws_modules if m["module"] == module), module.replace("_", " ").title())
    return templates.TemplateResponse(
        request=request,
        name="module_stub.html",
        context={
            **base_ctx,
            "module":      module,
            "module_name": module_name,
            "recent_tasks": fetch_recent_tasks([ws_id], limit=15),
        }
    )


@app.get("/finance_overview", response_class=HTMLResponse)
async def view_finance_overview(request: Request, user: dict = Depends(require_user)):
    if get_user_primary_ws(user) != "operator":
        return RedirectResponse(url="/")
    return templates.TemplateResponse(
        request=request,
        name="erp/finance_overview.html",
        context={
            "active_route": "finance_overview",
            "active_ws": "operator",
            "ws_meta": WORKSPACE_META,
            "user": user,
            "summaries": fetch_all_finance_summary(),
            "all_transactions": _fetch_rows(
                "SELECT * FROM finance_transactions ORDER BY date DESC, created_at DESC LIMIT 50"
            ),
        }
    )

@app.get("/analytics", response_class=HTMLResponse)
async def view_analytics(request: Request, user: dict = Depends(require_user)):
    if get_user_primary_ws(user) != "operator":
        return RedirectResponse(url="/")

    # Build per-workspace stats
    ws_stats = {}
    for ws_id in ["candles", "cars", "property", "nursing_massage", "food_brand"]:
        ws_stats[ws_id] = fetch_workspace_stats(ws_id)
    
    # Aggregate totals
    all_tasks = fetch_recent_tasks(None, limit=500)
    total_tasks = len(all_tasks)
    completed = len([t for t in all_tasks if t["status"] == "done"])
    failed = len([t for t in all_tasks if t["status"] == "failed"])
    declined = len([t for t in all_tasks if t["status"] == "declined"])

    # Chart data — last 7 days
    import datetime as dt
    today = dt.date.today()
    chart_labels = [(today - dt.timedelta(days=i)).strftime("%d %b") for i in range(6, -1, -1)]
    chart_data = [0] * 7
    for t in all_tasks:
        if t.get("created_at"):
            delta = (today - t["created_at"].date()).days
            if 0 <= delta < 7:
                chart_data[6 - delta] += 1

    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context={
            "active_route": "analytics",
            "active_ws": "operator",
            "ws_meta": WORKSPACE_META,
            "user": user,
            "ws_stats": ws_stats,
            "total_tasks": total_tasks,
            "completed": completed,
            "failed": failed,
            "declined": declined,
            "chart_labels": chart_labels,
            "chart_data": chart_data,
        }
    )

@app.get("/audit", response_class=HTMLResponse)
async def view_audit(request: Request, user: dict = Depends(require_admin)):
    entries = _fetch_rows(
        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 500"
    )
    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "active_route": "audit",
            "active_ws": "operator",
            "ws_meta": WORKSPACE_META,
            "user": user,
            "entries": entries,
        },
    )

@app.get("/logs", response_class=HTMLResponse)
async def view_logs(request: Request, user: dict = Depends(require_user)):
    if get_user_primary_ws(user) != "operator":
        return RedirectResponse(url="/")
    tasks = fetch_recent_tasks(None, limit=200)
    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context={
            "active_route": "logs",
            "active_ws": "operator",
            "ws_meta": WORKSPACE_META,
            "user": user,
            "tasks": tasks,
        }
    )

@app.get("/sops", response_class=HTMLResponse)
async def view_sops(request: Request, user: dict = Depends(require_user)):
    if get_user_primary_ws(user) != "operator":
        return RedirectResponse(url="/")
    import glob
    sop_files = glob.glob("/home/szmyt/server-services/prismaOS/sops/**/*.md", recursive=True)
    sops = [{"path": f, "name": f.split("/")[-1].replace(".md","").replace("_"," ").title()} for f in sop_files]
    return templates.TemplateResponse(
        request=request,
        name="sops.html",
        context={
            "active_route": "sops",
            "active_ws": "operator",
            "ws_meta": WORKSPACE_META,
            "user": user,
            "sops": sops,
        }
    )

# --- API Endpoints for Frontend actions ---

class TaskCreatePayload(BaseModel):
    task_type: str
    module: str
    input: str
    workspace: str
    user_name: str
    urgent: bool = False

# ── Workspace module settings API ─────────────────────────────

class ModuleSettingsPayload(BaseModel):
    settings: dict = {}

@app.post("/api/ws/{ws_id}/modules/{module}/toggle")
async def api_toggle_module(ws_id: str, module: str, enabled: bool, user: dict = Depends(require_user)):
    access = user.get("access", [])
    if "all_workspaces" not in access and ws_id not in access and user.get("role") != "operator":
        raise HTTPException(status_code=403, detail="Access denied")
    reg = MODULE_REGISTRY.get(module)
    if not reg:
        return {"success": False, "error": "Unknown module"}
    if reg.get("core"):
        return {"success": False, "error": "Core modules cannot be disabled"}
    ok = _exec(
        """INSERT INTO workspace_modules (workspace, module, enabled)
           VALUES (%s,%s,%s)
           ON CONFLICT (workspace, module) DO UPDATE SET enabled=%s""",
        (ws_id, module, enabled, enabled),
    )
    return {"success": ok}

@app.post("/api/ws/{ws_id}/modules/{module}/settings")
async def api_save_module_settings(ws_id: str, module: str, p: ModuleSettingsPayload, user: dict = Depends(require_user)):
    access = user.get("access", [])
    if "all_workspaces" not in access and ws_id not in access and user.get("role") != "operator":
        raise HTTPException(status_code=403, detail="Access denied")
    settings_json = json.dumps(p.settings)
    ok = _exec(
        """INSERT INTO workspace_modules (workspace, module, settings)
           VALUES (%s,%s,%s::jsonb)
           ON CONFLICT (workspace, module) DO UPDATE SET settings=%s::jsonb""",
        (ws_id, module, settings_json, settings_json),
    )
    return {"success": ok}

@app.get("/api/ws/{ws_id}/modules")
async def api_get_workspace_modules(ws_id: str, user: dict = Depends(require_user)):
    access = user.get("access", [])
    if "all_workspaces" not in access and ws_id not in access and user.get("role") != "operator":
        raise HTTPException(status_code=403, detail="Access denied")
    return {"modules": get_all_workspace_modules(ws_id)}


@app.post("/api/tasks/create")
async def api_create_task(payload: TaskCreatePayload, user: dict = Depends(require_user)):
    try:
        # Determine risk level based on workspace (simulating basic rules router)
        risk_level = "internal"
        if payload.workspace == "operator":
            risk_level = "internal"
        elif payload.workspace in ["candles", "cars", "food_brand"]:
            risk_level = "public"
        elif payload.workspace in ["property", "nursing_massage"]:
            risk_level = "financial"

        # Operator can do anything, others can only interact with their workspace
        if user.get("role") != "operator":
            access = user.get("access", [])
            if payload.workspace not in access and "all_workspaces" not in access:
                return {"success": False, "error": "Unauthorized workspace access"}

        lane = "urgent" if payload.urgent else None
        
        task_id = await add_task(
            workspace=payload.workspace,
            user=user.get("username", "web_user"),
            task_type=payload.task_type,
            risk_level=risk_level,
            module=payload.module,
            input=payload.input,
            trigger_type="web",
            queue_lane=lane
        )
        return {"success": True, "task_id": task_id}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/tasks/{task_id}/approve")
async def api_approve_task(task_id: str):
    success = await approve_task(task_id, approved_by="daniel (web UI)")
    return {"success": success}

@app.post("/api/tasks/{task_id}/decline")
async def api_decline_task(task_id: str):
    success = await decline_task(task_id, reason="Declined via Web UI")
    return {"success": success}

# ── ERP API ──────────────────────────────────────────────────

# Candles Orders
class CandlesOrderPayload(BaseModel):
    customer_name: str
    customer_email: str = ""
    etsy_order_id: str = ""
    total: float = 0
    subtotal: float = 0
    shipping: float = 0
    shipping_address: str = ""
    notes: str = ""

@app.post("/api/erp/candles/orders")
async def api_candles_order_create(request: Request, p: CandlesOrderPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO candles_orders (customer_name, customer_email, etsy_order_id, total, subtotal, shipping, shipping_address, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.customer_name, p.customer_email, p.etsy_order_id or None, p.total, p.subtotal, p.shipping, p.shipping_address, p.notes)
    )
    if id_:
        await notify_new_order({"customer_name": p.customer_name, "total": p.total})
        audit(user["username"], "CREATE", "candles_orders", id_, "candles", request.client.host if request.client else None)
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/candles/orders/{id}")
async def api_candles_order_update(id: str, status: str, user: dict = Depends(require_user)):
    ok = _exec("UPDATE candles_orders SET status=%s, updated_at=NOW() WHERE id=%s", (status, id))
    return {"success": ok}

# Candles Inventory
class InventoryPayload(BaseModel):
    name: str
    category: str = "raw"
    quantity: float = 0
    unit: str = "units"
    reorder_level: float = 0
    cost_per_unit: float = 0
    supplier: str = ""
    notes: str = ""

@app.post("/api/erp/candles/inventory")
async def api_candles_inventory_create(p: InventoryPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO candles_inventory (name, category, quantity, unit, reorder_level, cost_per_unit, supplier, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.name, p.category, p.quantity, p.unit, p.reorder_level, p.cost_per_unit, p.supplier, p.notes)
    )
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/candles/inventory/{id}")
async def api_candles_inventory_update(id: str, quantity: float, user: dict = Depends(require_user)):
    ok = _exec("UPDATE candles_inventory SET quantity=%s, updated_at=NOW() WHERE id=%s", (quantity, id))
    if ok:
        row = _fetch_rows("SELECT name, quantity, reorder_level FROM candles_inventory WHERE id=%s", (id,))
        if row and row[0]["quantity"] <= row[0]["reorder_level"]:
            item = row[0]
            await notify_low_stock(item)
            # Queue a research task for restocking
            await add_task(
                workspace="candles",
                user="system",
                task_type="research",
                risk_level="standard",
                module="inventory",
                input=f"Low stock alert: {item['name']} has {item['quantity']} {item.get('unit','units')} remaining (reorder level: {item['reorder_level']}). Find best UK supplier options and pricing.",
            )
    return {"success": ok}

# Cars Auctions
class CarAuctionPayload(BaseModel):
    source: str
    title: str
    listing_url: str = ""
    make: str = ""
    model: str = ""
    year: int = None
    mileage: int = None
    asking_price: float = None
    auction_date: str = None
    notes: str = ""

@app.post("/api/erp/cars/auctions")
async def api_cars_auction_create(request: Request, p: CarAuctionPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO cars_auctions (source, title, listing_url, make, model, year, mileage, asking_price, auction_date, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.source, p.title, p.listing_url, p.make, p.model, p.year, p.mileage, p.asking_price, p.auction_date, p.notes)
    )
    if id_:
        await notify_new_auction({"title": p.title, "source": p.source, "asking_price": p.asking_price})
        audit(user["username"], "CREATE", "cars_auctions", id_, "cars", request.client.host if request.client else None)
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/cars/auctions/{id}")
async def api_cars_auction_update(request: Request, id: str, status: str, notes: str = "", user: dict = Depends(require_user)):
    ok = _exec("UPDATE cars_auctions SET status=%s, notes=%s WHERE id=%s", (status, notes, id))
    if ok:
        audit(user["username"], "UPDATE", "cars_auctions", id, "cars", request.client.host if request.client else None, {"status": status})
    if ok and status == "won":
        row = _fetch_rows("SELECT * FROM cars_auctions WHERE id=%s", (id,))
        if row:
            a = row[0]
            await notify_auction_won({"title": a["title"], "asking_price": a.get("asking_price")})
            # Auto-create vehicle in inventory
            _insert_return_id(
                "INSERT INTO cars_vehicles (make, model, year, mileage, buy_price, status, source, notes) VALUES (%s,%s,%s,%s,%s,'sourced',%s,%s) RETURNING id",
                (a.get("make") or "Unknown", a.get("model") or a["title"],
                 a.get("year"), a.get("mileage"), a.get("asking_price") or 0,
                 a.get("source") or "auction",
                 f"Auto-imported from auction alert {id}"),
            )
    return {"success": ok}

# Cars Vehicles
class CarVehiclePayload(BaseModel):
    make: str
    model: str
    year: int = None
    vin: str = ""
    colour: str = ""
    mileage: int = None
    buy_price: float = 0
    repair_costs: float = 0
    sell_price: float = None
    status: str = "sourced"
    source: str = ""
    listing_url: str = ""
    notes: str = ""
    purchased_at: str = None

@app.post("/api/erp/cars/vehicles")
async def api_cars_vehicle_create(p: CarVehiclePayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO cars_vehicles (make, model, year, vin, colour, mileage, buy_price, repair_costs, sell_price, status, source, listing_url, notes, purchased_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.make, p.model, p.year, p.vin or None, p.colour, p.mileage, p.buy_price, p.repair_costs, p.sell_price, p.status, p.source, p.listing_url, p.notes, p.purchased_at)
    )
    return {"success": bool(id_), "id": id_}

class CarDocPayload(BaseModel):
    doc_type: str
    vehicle_id: str = None
    description: str = ""
    notes: str = ""

@app.post("/api/erp/cars/documents")
async def api_cars_document_create(p: CarDocPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO cars_documents (doc_type, vehicle_id, description, notes) VALUES (%s,%s,%s,%s) RETURNING id",
        (p.doc_type, p.vehicle_id or None, p.description, p.notes)
    )
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/cars/vehicles/{id}")
async def api_cars_vehicle_update(id: str, status: str, sell_price: float = None, repair_costs: float = None, notes: str = None, user: dict = Depends(require_user)):
    parts, vals = ["status=%s"], [status]
    if sell_price is not None:
        parts.append("sell_price=%s"); vals.append(sell_price)
        if status == "sold":
            parts.append("sold_at=CURRENT_DATE")
    if repair_costs is not None:
        parts.append("repair_costs=%s"); vals.append(repair_costs)
    if notes is not None:
        parts.append("notes=%s"); vals.append(notes)
    vals.append(id)
    ok = _exec(f"UPDATE cars_vehicles SET {', '.join(parts)} WHERE id=%s", tuple(vals))
    return {"success": ok}

# Property Deals
class PropertyDealPayload(BaseModel):
    address: str
    postcode: str = ""
    deal_type: str = "buy"
    asking_price: float = None
    offer_price: float = None
    estimated_costs: float = 0
    estimated_value: float = None
    status: str = "prospect"
    agent: str = ""
    notes: str = ""

@app.post("/api/erp/property/deals")
async def api_property_deal_create(request: Request, p: PropertyDealPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO property_deals (address, postcode, deal_type, asking_price, offer_price, estimated_costs, estimated_value, status, agent, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.address, p.postcode, p.deal_type, p.asking_price, p.offer_price, p.estimated_costs, p.estimated_value, p.status, p.agent, p.notes)
    )
    if id_:
        audit(user["username"], "CREATE", "property_deals", id_, "property", request.client.host if request.client else None)
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/property/deals/{id}")
async def api_property_deal_update(id: str, status: str, notes: str = None, user: dict = Depends(require_user)):
    parts, vals = ["status=%s", "updated_at=NOW()"], [status]
    if notes is not None:
        parts.append("notes=%s"); vals.append(notes)
    vals.append(id)
    ok = _exec(f"UPDATE property_deals SET {', '.join(parts)} WHERE id=%s", tuple(vals))
    return {"success": ok}

# Property Watchlist
class WatchlistPayload(BaseModel):
    address: str
    postcode: str = ""
    listing_url: str = ""
    source: str = ""
    asking_price: float = None
    property_type: str = ""
    bedrooms: int = None
    notes: str = ""

@app.post("/api/erp/property/watchlist")
async def api_property_watchlist_create(p: WatchlistPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO property_watchlist (address, postcode, listing_url, source, asking_price, property_type, bedrooms, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.address, p.postcode, p.listing_url, p.source, p.asking_price, p.property_type, p.bedrooms, p.notes)
    )
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/property/watchlist/{id}")
async def api_property_watchlist_update(id: str, status: str, user: dict = Depends(require_user)):
    ok = _exec("UPDATE property_watchlist SET status=%s WHERE id=%s", (status, id))
    if ok:
        row = _fetch_rows("SELECT address, asking_price FROM property_watchlist WHERE id=%s", (id,))
        if row:
            await notify_watchlist_update({"address": row[0]["address"], "asking_price": row[0]["asking_price"], "status": status})
    return {"success": ok}

# Nursing Clients
class NursingClientPayload(BaseModel):
    name: str
    phone: str = ""
    email: str = ""
    address: str = ""
    medical_notes: str = ""

@app.post("/api/erp/nursing/clients")
async def api_nursing_client_create(p: NursingClientPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO nursing_clients (name, phone, email, address, medical_notes) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (p.name, p.phone, p.email, p.address, p.medical_notes)
    )
    return {"success": bool(id_), "id": id_}

# Nursing Bookings
class NursingBookingPayload(BaseModel):
    client_name: str
    client_id: str = None
    service_name: str
    service_id: str = None
    booking_date: str
    booking_time: str
    duration_mins: int = 60
    amount: float = 0
    notes: str = ""

@app.post("/api/erp/nursing/bookings")
async def api_nursing_booking_create(request: Request, p: NursingBookingPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO nursing_bookings (client_name, client_id, service_name, service_id, booking_date, booking_time, duration_mins, amount, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.client_name, p.client_id or None, p.service_name, p.service_id or None, p.booking_date, p.booking_time, p.duration_mins, p.amount, p.notes)
    )
    if id_:
        await notify_new_booking({
            "client_name": p.client_name,
            "service_name": p.service_name,
            "appointment_at": f"{p.booking_date} {p.booking_time}",
        })
        audit(user["username"], "CREATE", "nursing_bookings", id_, "nursing_massage", request.client.host if request.client else None)
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/nursing/bookings/{id}")
async def api_nursing_booking_update(id: str, status: str, payment_status: str = None, user: dict = Depends(require_user)):
    parts, vals = ["status=%s"], [status]
    if payment_status:
        parts.append("payment_status=%s"); vals.append(payment_status)
    vals.append(id)
    ok = _exec(f"UPDATE nursing_bookings SET {', '.join(parts)} WHERE id=%s", tuple(vals))
    # Auto-create finance transaction when booking is completed
    if ok and status == "completed":
        booking = _fetch_rows(
            "SELECT client_name, service_name, amount, booking_date FROM nursing_bookings WHERE id=%s", (id,)
        )
        if booking and float(booking[0].get("amount") or 0) > 0:
            b = booking[0]
            _exec(
                "INSERT INTO finance_transactions (workspace, type, category, description, amount, date, reference_id, reference_type) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                ("nursing_massage", "income", "booking", f"{b['service_name']} — {b['client_name']}",
                 float(b["amount"]), b["booking_date"], id, "nursing_booking"),
            )
    return {"success": ok}

# Content (generic for candles_content, nursing_content, food_content)
class ContentPayload(BaseModel):
    table: str
    platform: str
    title: str
    caption: str = ""
    publish_date: str = None
    status: str = "draft"
    notes: str = ""

@app.post("/api/erp/content")
async def api_content_create(p: ContentPayload, user: dict = Depends(require_user)):
    allowed_tables = {"candles_content", "nursing_content", "food_content"}
    if p.table not in allowed_tables:
        return {"success": False, "error": "Invalid table"}
    id_ = _insert_return_id(
        f"INSERT INTO {p.table} (platform, title, caption, publish_date, status, notes) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.platform, p.title, p.caption, p.publish_date or None, p.status, p.notes)
    )
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/content/{table}/{id}")
async def api_content_update(table: str, id: str, status: str, post_url: str = "", user: dict = Depends(require_user)):
    allowed_tables = {"candles_content", "nursing_content", "food_content"}
    if table not in allowed_tables:
        return {"success": False, "error": "Invalid table"}
    ok = _exec(f"UPDATE {table} SET status=%s, post_url=%s WHERE id=%s", (status, post_url, id))
    return {"success": ok}

# Food Ideas
class FoodIdeaPayload(BaseModel):
    title: str
    description: str = ""
    category: str = ""
    platform: str = ""
    priority: int = 5
    notes: str = ""

@app.post("/api/erp/food/ideas")
async def api_food_idea_create(p: FoodIdeaPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO food_ideas (title, description, category, platform, priority, notes) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.title, p.description, p.category, p.platform, p.priority, p.notes)
    )
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/food/ideas/{id}")
async def api_food_idea_update(id: str, status: str, user: dict = Depends(require_user)):
    ok = _exec("UPDATE food_ideas SET status=%s WHERE id=%s", (status, id))
    return {"success": ok}

# Food Partnerships
class PartnershipPayload(BaseModel):
    brand: str
    contact_name: str = ""
    contact_email: str = ""
    deal_value: float = None
    deliverables: str = ""
    due_date: str = None
    status: str = "prospect"
    notes: str = ""

@app.post("/api/erp/food/partnerships")
async def api_partnership_create(p: PartnershipPayload, user: dict = Depends(require_user)):
    id_ = _insert_return_id(
        "INSERT INTO food_partnerships (brand, contact_name, contact_email, deal_value, deliverables, due_date, status, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.brand, p.contact_name, p.contact_email, p.deal_value, p.deliverables, p.due_date or None, p.status, p.notes)
    )
    return {"success": bool(id_), "id": id_}

@app.patch("/api/erp/food/partnerships/{id}")
async def api_partnership_update(id: str, status: str, user: dict = Depends(require_user)):
    ok = _exec("UPDATE food_partnerships SET status=%s WHERE id=%s", (status, id))
    return {"success": ok}

# Finance Transactions
class FinanceTransactionPayload(BaseModel):
    workspace: str
    type: str
    category: str
    description: str
    amount: float
    date: str = None
    notes: str = ""

@app.post("/api/erp/finance/transactions")
async def api_finance_create(request: Request, p: FinanceTransactionPayload, user: dict = Depends(require_user)):
    import datetime as dt
    date = p.date or str(dt.date.today())
    id_ = _insert_return_id(
        "INSERT INTO finance_transactions (workspace, type, category, description, amount, date, notes) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (p.workspace, p.type, p.category, p.description, p.amount, date, p.notes)
    )
    if id_:
        audit(user["username"], "CREATE", "finance_transactions", id_, p.workspace, request.client.host if request.client else None, {"type": p.type, "amount": p.amount})
    return {"success": bool(id_), "id": id_}

@app.delete("/api/erp/finance/transactions/{id}")
async def api_finance_delete(request: Request, id: str, user: dict = Depends(require_user)):
    ok = _exec("DELETE FROM finance_transactions WHERE id=%s", (id,))
    if ok:
        audit(user["username"], "DELETE", "finance_transactions", id, None, request.client.host if request.client else None)
    return {"success": ok}

@app.get("/api/erp/finance/{workspace}/chart")
async def api_finance_chart(workspace: str, user: dict = Depends(require_user)):
    """12-month monthly income and expenses for Chart.js."""
    rows = _fetch_rows("""
        SELECT
            to_char(date_trunc('month', date), 'Mon YYYY') as month,
            date_trunc('month', date) as month_ts,
            COALESCE(SUM(amount) FILTER (WHERE type='income'), 0)  as income,
            COALESCE(SUM(amount) FILTER (WHERE type='expense'), 0) as expenses
        FROM finance_transactions
        WHERE workspace = %s
          AND date >= date_trunc('month', CURRENT_DATE) - INTERVAL '11 months'
        GROUP BY month_ts
        ORDER BY month_ts ASC
    """, (workspace,))
    return {
        "labels":   [r["month"] for r in rows],
        "income":   [float(r["income"]) for r in rows],
        "expenses": [float(r["expenses"]) for r in rows],
    }

@app.get("/api/erp/finance/overview/chart")
async def api_finance_overview_chart(user: dict = Depends(require_user)):
    """Last 6 months net P&L per workspace for the operator overview chart."""
    workspaces = ["candles", "cars", "property", "nursing_massage", "food_brand"]
    labels_set = _fetch_rows("""
        SELECT DISTINCT to_char(date_trunc('month', date), 'Mon YYYY') as month,
                        date_trunc('month', date) as month_ts
        FROM finance_transactions
        WHERE date >= date_trunc('month', CURRENT_DATE) - INTERVAL '5 months'
        ORDER BY month_ts ASC
    """)
    labels = [r["month"] for r in labels_set]

    datasets = []
    colours = {
        "candles":        "#BFA880",
        "cars":           "#3A6B4A",
        "property":       "#2A5B6B",
        "nursing_massage":"#7B5EA7",
        "food_brand":     "#8B3A2A",
    }
    ws_labels = {
        "candles": "Candles", "cars": "Cars", "property": "Property",
        "nursing_massage": "Nursing", "food_brand": "Food Brand",
    }
    for ws in workspaces:
        rows = _fetch_rows("""
            SELECT
                to_char(date_trunc('month', date), 'Mon YYYY') as month,
                COALESCE(SUM(amount) FILTER (WHERE type='income'), 0)
                - COALESCE(SUM(amount) FILTER (WHERE type='expense'), 0) as net
            FROM finance_transactions
            WHERE workspace = %s
              AND date >= date_trunc('month', CURRENT_DATE) - INTERVAL '5 months'
            GROUP BY date_trunc('month', date)
            ORDER BY date_trunc('month', date) ASC
        """, (ws,))
        by_month = {r["month"]: float(r["net"]) for r in rows}
        datasets.append({
            "label": ws_labels[ws],
            "data": [by_month.get(l, 0) for l in labels],
            "backgroundColor": colours[ws] + "CC",
            "borderColor": colours[ws],
            "borderWidth": 1,
        })
    return {"labels": labels, "datasets": datasets}

@app.get("/api/erp/candles/orders/chart")
async def api_candles_orders_chart(user: dict = Depends(require_user)):
    rows = _fetch_rows("""
        SELECT
            to_char(date_trunc('month', order_date), 'Mon YYYY') as month,
            date_trunc('month', order_date) as month_ts,
            COUNT(*) FILTER (WHERE status NOT IN ('cancelled','refunded')) as orders,
            COALESCE(SUM(total) FILTER (WHERE status NOT IN ('cancelled','refunded')), 0) as revenue
        FROM candles_orders
        WHERE order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '11 months'
        GROUP BY month_ts
        ORDER BY month_ts ASC
    """)
    return {
        "labels":  [r["month"] for r in rows],
        "orders":  [int(r["orders"]) for r in rows],
        "revenue": [float(r["revenue"]) for r in rows],
    }
