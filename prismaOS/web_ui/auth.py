import os
import yaml
import bcrypt
import logging
import psycopg2
import psycopg2.extras
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException, status
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("prisma.auth")

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE_HOURS", "12")) * 3600

_signer = URLSafeTimedSerializer(SECRET_KEY, salt="prisma-session")


# ── DB connection ─────────────────────────────────────────────

def _get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        dbname=os.getenv("POSTGRES_DB", "systemos"),
        user=os.getenv("POSTGRES_USER", "daniel"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=5,
    )


# ── Environment (roles + access) ─────────────────────────────

def load_users():
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "environment.yaml")
    try:
        with open(env_path) as f:
            data = yaml.safe_load(f)
        return data.get("team", {})
    except Exception as exc:
        logger.error("Failed to load environment.yaml: %s", exc)
        return {}

_ENV_USERS: dict | None = None

def get_user_db() -> dict:
    global _ENV_USERS
    if _ENV_USERS is None:
        _ENV_USERS = load_users()
    return _ENV_USERS


# ── Password management ───────────────────────────────────────

def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(username: str, plaintext: str) -> bool:
    """Check plaintext against stored bcrypt hash in DB. Returns True on match."""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT bcrypt_hash, active FROM users WHERE username = %s", (username,)
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            return False
        stored_hash, active = row
        if not active:
            return False
        return bcrypt.checkpw(plaintext.encode(), stored_hash.encode())
    except Exception as exc:
        logger.error("verify_password error: %s", exc)
        return False


def update_last_login(username: str) -> None:
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET last_login = NOW() WHERE username = %s", (username,)
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("update_last_login error: %s", exc)


def user_exists_in_db(username: str) -> bool:
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE username = %s AND active = true", (username,))
            exists = cur.fetchone() is not None
        conn.close()
        return exists
    except Exception:
        return False


# ── Session cookies ───────────────────────────────────────────

def create_session_token(username: str) -> str:
    """Create a signed, tamper-proof session cookie value."""
    return _signer.dumps({"u": username})


def decode_session_token(token: str) -> str | None:
    """Verify signature and expiry. Returns username or None."""
    try:
        payload = _signer.loads(token, max_age=SESSION_MAX_AGE)
        return payload.get("u")
    except SignatureExpired:
        logger.info("Session token expired")
        return None
    except BadSignature:
        logger.warning("Invalid session token signature")
        return None
    except Exception as exc:
        logger.warning("Session decode error: %s", exc)
        return None


# ── User resolution ───────────────────────────────────────────

def _build_user(username: str, env_users: dict) -> dict | None:
    if username not in env_users:
        return None
    info = env_users[username]
    access = info.get("businesses") or info.get("access") or []
    return {
        "username": username,
        "role": info.get("role", "workspace_user"),
        "access": access,
    }


def get_current_user(request: Request) -> dict | None:
    token = request.cookies.get("session_token")
    if not token:
        return None
    username = decode_session_token(token)
    if not username:
        return None
    env_users = get_user_db()
    return _build_user(username, env_users)


def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"},
        )
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user.get("role") != "operator":
        raise HTTPException(status_code=403, detail="Operator access required")
    return user


def require_workspace_access(user: dict, workspace: str) -> None:
    """Raise 403 if user cannot access the given workspace."""
    if user.get("role") == "operator":
        return
    access = user.get("access", [])
    if "all_workspaces" in access or workspace in access:
        return
    raise HTTPException(status_code=403, detail=f"No access to workspace: {workspace}")
