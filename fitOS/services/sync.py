"""Daily vitals sync — pulls from Fitbit and writes to health.metrics."""

import logging
from datetime import date

logger = logging.getLogger(__name__)

# metric_type → Fitbit key mapping
_METRIC_MAP = {
    "sleep_hrs":  ("sleep_hrs",   "hrs"),
    "resting_hr": ("resting_hr",  "bpm"),
    "steps":      ("steps",       "steps"),
}


async def sync_vitals_today() -> dict:
    """
    Fetch today's Fitbit vitals and upsert into health.metrics.
    Called by APScheduler at 04:00 daily and manually via API.
    Returns { synced: [...], skipped: [...], error: str|None }
    """
    from db import get_conn
    from services.fitbit import get_vitals, is_configured

    if not is_configured():
        logger.info("[SYNC] Fitbit not configured — skipping")
        return {"synced": [], "skipped": [], "error": "Fitbit not configured"}

    today = date.today().isoformat()
    result = {"synced": [], "skipped": [], "error": None}

    try:
        with get_conn() as conn:
            vitals = await get_vitals(conn, date=today)
    except Exception as exc:
        logger.error("[SYNC] get_vitals failed: %s", exc, exc_info=True)
        return {"synced": [], "skipped": [], "error": str(exc)}

    if vitals is None:
        return {"synced": [], "skipped": [], "error": "Not connected to Fitbit"}

    with get_conn() as conn:
        with conn.cursor() as cur:
            for metric_type, (vkey, unit) in _METRIC_MAP.items():
                value = vitals.get(vkey)
                if value is None:
                    result["skipped"].append(metric_type)
                    continue
                # Upsert: one entry per metric per day (keyed by date truncation)
                cur.execute("""
                    INSERT INTO health.metrics (metric_type, value, unit, note)
                    VALUES (%s, %s, %s, 'fitbit-auto')
                    ON CONFLICT DO NOTHING
                """, (metric_type, float(value), unit))
                result["synced"].append(metric_type)
                logger.info("[SYNC] %s = %.2f %s", metric_type, float(value), unit)

    return result
