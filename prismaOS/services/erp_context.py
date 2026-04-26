"""
erp_context.py
Build a structured ERP snapshot for a given workspace, injected into
the agent's system prompt so it has live business data when reasoning.
"""

import os
import sys
import logging
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("prisma.erp_context")


def _get_conn():
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv()
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        dbname=os.getenv("POSTGRES_DB", "systemos"),
        user=os.getenv("POSTGRES_USER", "daniel"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=5,
    )


def _q(query: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT and return list of dicts."""
    import psycopg2.extras
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("ERP context query error: %s", exc)
        return []


def _q1(query: str, params: tuple = ()) -> dict:
    rows = _q(query, params)
    return rows[0] if rows else {}


# ── Per-workspace context builders ───────────────────────────────────────────

def _candles_context() -> dict:
    stats = _q1("""
        SELECT
            COUNT(*) FILTER (WHERE status='open') as open_orders,
            COUNT(*) FILTER (WHERE status='processing') as processing_orders,
            COALESCE(SUM(total) FILTER (WHERE date_trunc('month', order_date) = date_trunc('month', CURRENT_DATE)), 0) as revenue_mtd
        FROM candles_orders
    """)
    low_stock = _q("""
        SELECT name, quantity, reorder_level, unit
        FROM candles_inventory
        WHERE quantity <= reorder_level
        ORDER BY (quantity::float / NULLIF(reorder_level, 0)) ASC
    """)
    upcoming_content = _q("""
        SELECT platform, title, publish_date, status
        FROM candles_content
        WHERE status IN ('draft','scheduled') AND (publish_date IS NULL OR publish_date >= CURRENT_DATE)
        ORDER BY publish_date ASC NULLS LAST
        LIMIT 5
    """)
    return {
        "open_orders": int(stats.get("open_orders") or 0),
        "processing_orders": int(stats.get("processing_orders") or 0),
        "revenue_mtd": float(stats.get("revenue_mtd") or 0),
        "low_stock_items": [
            f"{r['name']} ({r['quantity']} {r['unit']}, reorder at {r['reorder_level']})"
            for r in low_stock
        ],
        "upcoming_content": [
            f"{r['platform']}: {r['title']} [{r['status']}] — {r['publish_date'] or 'unscheduled'}"
            for r in upcoming_content
        ],
    }


def _cars_context() -> dict:
    stats = _q1("""
        SELECT
            COUNT(*) FILTER (WHERE status='sourced') as sourced,
            COUNT(*) FILTER (WHERE status='prepping') as prepping,
            COUNT(*) FILTER (WHERE status='listed') as listed,
            COUNT(*) FILTER (WHERE status='sold') as sold,
            COALESCE(SUM(sell_price - buy_price - repair_costs) FILTER (WHERE status='sold'), 0) as total_profit
        FROM cars_vehicles
    """)
    pending_auctions = _q("""
        SELECT source, title, asking_price, auction_date
        FROM cars_auctions
        WHERE status IN ('new','reviewed')
        ORDER BY auction_date ASC NULLS LAST
        LIMIT 5
    """)
    return {
        "inventory_sourced": int(stats.get("sourced") or 0),
        "inventory_prepping": int(stats.get("prepping") or 0),
        "inventory_listed": int(stats.get("listed") or 0),
        "sold_total": int(stats.get("sold") or 0),
        "total_profit": float(stats.get("total_profit") or 0),
        "pending_auction_alerts": [
            f"[{r['source'].upper()}] {r['title']} — £{r['asking_price']:,.0f} on {r['auction_date'] or 'TBD'}"
            for r in pending_auctions
        ],
    }


def _property_context() -> dict:
    stats = _q1("""
        SELECT
            COUNT(*) FILTER (WHERE status NOT IN ('completed','lost')) as active_deals,
            COUNT(*) FILTER (WHERE status='completed') as completed,
            COUNT(*) FILTER (WHERE status='lost') as lost
        FROM property_deals
    """)
    pipeline = _q("""
        SELECT address, status, asking_price, offer_price
        FROM property_deals
        WHERE status NOT IN ('completed','lost')
        ORDER BY updated_at DESC
        LIMIT 5
    """)
    watching = _q("""
        SELECT address, source, asking_price, status
        FROM property_watchlist
        WHERE status IN ('watching','contacted','viewing_booked')
        ORDER BY created_at DESC
        LIMIT 5
    """)
    return {
        "active_deals": int(stats.get("active_deals") or 0),
        "completed_deals": int(stats.get("completed") or 0),
        "pipeline": [
            f"{r['address']} [{r['status']}] ask £{r['asking_price']:,.0f}"
            if r.get("asking_price") else f"{r['address']} [{r['status']}]"
            for r in pipeline
        ],
        "watchlist_active": [
            f"{r['address']} [{r['source']}] £{r['asking_price']:,.0f} — {r['status']}"
            if r.get("asking_price") else f"{r['address']} [{r['source']}] — {r['status']}"
            for r in watching
        ],
    }


def _nursing_context() -> dict:
    stats = _q1("""
        SELECT
            COUNT(*) FILTER (WHERE booking_date >= CURRENT_DATE AND status='confirmed') as upcoming,
            COALESCE(SUM(amount) FILTER (WHERE date_trunc('month', booking_date) = date_trunc('month', CURRENT_DATE) AND status='completed'), 0) as revenue_mtd,
            COUNT(DISTINCT client_id) as total_clients
        FROM nursing_bookings
    """)
    next_bookings = _q("""
        SELECT client_name, service_name, booking_date, booking_time
        FROM nursing_bookings
        WHERE booking_date >= CURRENT_DATE AND status='confirmed'
        ORDER BY booking_date, booking_time
        LIMIT 5
    """)
    return {
        "upcoming_bookings": int(stats.get("upcoming") or 0),
        "revenue_mtd": float(stats.get("revenue_mtd") or 0),
        "total_clients": int(stats.get("total_clients") or 0),
        "next_appointments": [
            f"{r['client_name']} — {r['service_name']} on {r['booking_date']} at {r['booking_time']}"
            for r in next_bookings
        ],
    }


def _food_context() -> dict:
    stats = _q1("""
        SELECT
            COUNT(*) FILTER (WHERE status='published') as published,
            COUNT(*) FILTER (WHERE status IN ('draft','scheduled')) as pending,
            COALESCE(SUM(likes) FILTER (WHERE status='published'), 0) as total_likes,
            COALESCE(SUM(views) FILTER (WHERE status='published'), 0) as total_views,
            COUNT(*) FILTER (WHERE status='active') as active_partnerships
        FROM food_content
        LEFT JOIN food_partnerships ON false
    """)
    # Simpler separate queries
    content_stats = _q1("""
        SELECT
            COUNT(*) FILTER (WHERE status='published') as published,
            COUNT(*) FILTER (WHERE status IN ('draft','scheduled')) as pending,
            COALESCE(SUM(likes) FILTER (WHERE status='published'), 0) as total_likes
        FROM food_content
    """)
    partnerships = _q1("""
        SELECT COUNT(*) FILTER (WHERE status='active') as active FROM food_partnerships
    """)
    top_ideas = _q("""
        SELECT title, category, priority FROM food_ideas
        WHERE status='pending'
        ORDER BY priority DESC, created_at DESC
        LIMIT 5
    """)
    return {
        "published_posts": int(content_stats.get("published") or 0),
        "pending_content": int(content_stats.get("pending") or 0),
        "total_likes": int(content_stats.get("total_likes") or 0),
        "active_partnerships": int(partnerships.get("active") or 0),
        "top_ideas": [
            f"[P{r['priority']}] {r['title']} ({r['category']})"
            for r in top_ideas
        ],
    }


def _finance_context(workspace: str) -> dict:
    summary = _q1("""
        SELECT
            COALESCE(SUM(amount) FILTER (WHERE type='income'), 0) as total_income,
            COALESCE(SUM(amount) FILTER (WHERE type='expense'), 0) as total_expenses,
            COALESCE(SUM(amount) FILTER (WHERE type='income' AND date >= date_trunc('month', CURRENT_DATE)), 0) as income_mtd,
            COALESCE(SUM(amount) FILTER (WHERE type='expense' AND date >= date_trunc('month', CURRENT_DATE)), 0) as expenses_mtd
        FROM finance_transactions WHERE workspace=%s
    """, (workspace,))
    total_income = float(summary.get("total_income") or 0)
    total_expenses = float(summary.get("total_expenses") or 0)
    return {
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net": round(total_income - total_expenses, 2),
        "income_mtd": float(summary.get("income_mtd") or 0),
        "expenses_mtd": float(summary.get("expenses_mtd") or 0),
    }


# ── Public API ────────────────────────────────────────────────────────────────

WORKSPACE_BUILDERS = {
    "candles": _candles_context,
    "cars": _cars_context,
    "property": _property_context,
    "nursing_massage": _nursing_context,
    "food_brand": _food_context,
}


def build_erp_context(workspace: str) -> dict:
    """
    Return a structured ERP snapshot for the given workspace.
    Suitable for injecting into an agent system prompt as JSON or YAML.
    """
    builder = WORKSPACE_BUILDERS.get(workspace)
    if not builder:
        return {"workspace": workspace, "error": "No ERP context builder for this workspace"}

    try:
        data = builder()
    except Exception as exc:
        logger.error("build_erp_context(%s) error: %s", workspace, exc)
        data = {"error": str(exc)}

    finance = _finance_context(workspace)
    return {
        "workspace": workspace,
        "as_of": date.today().isoformat(),
        **data,
        "finance": finance,
    }


def format_erp_context_for_prompt(workspace: str) -> str:
    """
    Return a human-readable ERP snapshot string ready to embed in a system prompt.
    """
    ctx = build_erp_context(workspace)
    lines = [f"=== ERP Snapshot: {ctx['workspace']} (as of {ctx.get('as_of', 'today')}) ==="]

    for key, val in ctx.items():
        if key in ("workspace", "as_of", "finance", "error"):
            continue
        label = key.replace("_", " ").title()
        if isinstance(val, list):
            if val:
                lines.append(f"\n{label}:")
                lines.extend(f"  • {item}" for item in val)
        elif isinstance(val, float):
            lines.append(f"{label}: £{val:,.2f}")
        else:
            lines.append(f"{label}: {val}")

    fin = ctx.get("finance", {})
    if fin:
        lines.append(f"\nFinance:")
        lines.append(f"  • Net P&L: £{fin.get('net', 0):,.2f}")
        lines.append(f"  • Income MTD: £{fin.get('income_mtd', 0):,.2f}")
        lines.append(f"  • Expenses MTD: £{fin.get('expenses_mtd', 0):,.2f}")

    if "error" in ctx:
        lines.append(f"\nError: {ctx['error']}")

    return "\n".join(lines)
