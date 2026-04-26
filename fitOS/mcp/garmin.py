"""
fitOS — Garmin Connect MCP
Stub. Wire up when Garmin API credentials are available.
"""

import logging

logger = logging.getLogger("fitos.mcp.garmin")


async def fetch_daily_stats(date: str | None = None) -> dict:
    """Fetch daily stats from Garmin Connect (steps, HR, sleep, stress)."""
    logger.warning("Garmin MCP not yet configured — returning empty stats")
    return {}


async def fetch_recent_activities(limit: int = 10) -> list[dict]:
    """Fetch recent workout activities from Garmin Connect."""
    logger.warning("Garmin MCP not yet configured — returning empty list")
    return []


async def fetch_sleep(date: str | None = None) -> dict:
    """Fetch sleep data for a given date."""
    logger.warning("Garmin MCP not yet configured — returning empty sleep data")
    return {}
