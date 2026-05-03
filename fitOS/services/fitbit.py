"""Fitbit OAuth2 + Vitals API service."""

import logging
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

_AUTH_URL     = "https://www.fitbit.com/oauth2/authorize"
_TOKEN_URL    = "https://api.fitbit.com/oauth2/token"
_API_BASE     = "https://api.fitbit.com/1/user/-"
_REDIRECT_URI = os.getenv("FITBIT_REDIRECT_URI", "http://localhost:4002/api/fitbit/callback")
_SCOPES       = "sleep heartrate activity profile"


def client_id() -> str:
    return os.getenv("FITBIT_CLIENT_ID", "")


def client_secret() -> str:
    return os.getenv("FITBIT_CLIENT_SECRET", "")


def is_configured() -> bool:
    return bool(client_id() and client_secret())


def auth_url(state: str = "fitbit") -> str:
    params = {
        "response_type": "code",
        "client_id":     client_id(),
        "redirect_uri":  _REDIRECT_URI,
        "scope":         _SCOPES,
        "state":         state,
        "expires_in":    "604800",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Trade auth code for access + refresh tokens. Returns token dict."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "grant_type":   "authorization_code",
                "code":         code,
                "redirect_uri": _REDIRECT_URI,
            },
            auth=(client_id(), client_secret()),
        )
    resp.raise_for_status()
    return resp.json()


async def refresh_tokens(refresh_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(client_id(), client_secret()),
        )
    resp.raise_for_status()
    return resp.json()


def _expires_at(token_data: dict) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=int(token_data.get("expires_in", 28800)))


# ── DB helpers ────────────────────────────────────────────────────────────────

def _save_tokens(conn, token_data: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO health.oauth_tokens
                (user_id, provider, access_token, refresh_token, expires_at, scope, updated_at)
            VALUES (1, 'fitbit', %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, provider) DO UPDATE SET
                access_token  = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at    = EXCLUDED.expires_at,
                scope         = EXCLUDED.scope,
                updated_at    = NOW()
        """, (
            token_data["access_token"],
            token_data["refresh_token"],
            _expires_at(token_data),
            token_data.get("scope"),
        ))


def _load_tokens(conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT access_token, refresh_token, expires_at, scope
            FROM health.oauth_tokens
            WHERE user_id = 1 AND provider = 'fitbit'
        """)
        row = cur.fetchone()
    if not row:
        return None
    return {"access_token": row[0], "refresh_token": row[1],
            "expires_at": row[2], "scope": row[3]}


def _delete_tokens(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM health.oauth_tokens WHERE user_id = 1 AND provider = 'fitbit'")


# ── Ensure fresh access token ──────────────────────────────────────────────────

async def _get_valid_access_token(conn) -> str | None:
    """Return a valid access token, auto-refreshing if expired (spec 3.1)."""
    tokens = _load_tokens(conn)
    if not tokens:
        return None

    now = datetime.now(timezone.utc)
    expires_at = tokens["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now >= expires_at - timedelta(minutes=5):
        logger.info("[FITBIT] Token expired — refreshing")
        try:
            new_tokens = await refresh_tokens(tokens["refresh_token"])
            _save_tokens(conn, new_tokens)
            return new_tokens["access_token"]
        except Exception as exc:
            logger.error("[FITBIT] Token refresh failed: %s", exc)
            return None

    return tokens["access_token"]


# ── Vitals fetch ──────────────────────────────────────────────────────────────

async def get_vitals(conn, date: str = "today") -> dict | None:
    """
    Fetch sleep duration, resting heart rate, and steps for a given date.
    Returns normalised dict or None if not connected / fetch fails.
    """
    access_token = await _get_valid_access_token(conn)
    if not access_token:
        return None

    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        sleep_r, hr_r, steps_r = await _gather(client, headers, date)

    result: dict = {}

    try:
        summary = sleep_r.get("summary", {})
        total_ms = summary.get("totalMinutesAsleep", 0)
        result["sleep_hrs"] = round(total_ms / 60, 2) if total_ms else None
        result["sleep_score"] = sleep_r.get("sleep", [{}])[0].get("efficiency") if sleep_r.get("sleep") else None
    except Exception:
        result["sleep_hrs"] = None

    try:
        rhr = hr_r.get("activities-heart", [{}])[0].get("value", {}).get("restingHeartRate")
        result["resting_hr"] = rhr
    except Exception:
        result["resting_hr"] = None

    try:
        steps = steps_r.get("activities-steps", [{}])[0].get("value")
        result["steps"] = int(steps) if steps else None
    except Exception:
        result["steps"] = None

    return result


async def _gather(client, headers, date):
    import asyncio
    responses = await asyncio.gather(
        _get(client, f"{_API_BASE}/sleep/date/{date}.json", headers),
        _get(client, f"{_API_BASE}/activities/heart/date/{date}/1d.json", headers),
        _get(client, f"{_API_BASE}/activities/steps/date/{date}/1d.json", headers),
        return_exceptions=True,
    )
    results = []
    for r in responses:
        if isinstance(r, Exception):
            logger.warning("[FITBIT] API call failed: %s", r)
            results.append({})
        else:
            results.append(r)
    return results


async def _get(client, url, headers) -> dict:
    resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()
