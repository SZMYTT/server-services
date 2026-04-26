"""Session auth — bcrypt passwords, itsdangerous signed cookies, users from config/users.yaml."""

import os
import logging
from pathlib import Path

import bcrypt
import yaml
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-supply-os")
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE_HOURS", "24")) * 3600
_signer = URLSafeTimedSerializer(SECRET_KEY, salt="supply-session")

_CONFIG_DIR = Path(__file__).parent.parent / "config"
_USERS_FILE = _CONFIG_DIR / "users.yaml"
_HASHES_FILE = _CONFIG_DIR / "user_hashes.yaml"


def _load_users() -> dict:
    if not _USERS_FILE.exists():
        return {}
    return yaml.safe_load(_USERS_FILE.read_text()).get("users", {})


def _load_hashes() -> dict:
    if not _HASHES_FILE.exists():
        return {}
    return yaml.safe_load(_HASHES_FILE.read_text()) or {}


def verify_password(username: str, plaintext: str) -> bool:
    hashes = _load_hashes()
    stored = hashes.get(username)
    if not stored:
        return False
    return bcrypt.checkpw(plaintext.encode(), stored.encode())


def set_password(username: str, plaintext: str):
    """Hash and store a password. Called from setup script."""
    hashes = _load_hashes()
    hashes[username] = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=12)).decode()
    _HASHES_FILE.write_text(yaml.dump(hashes))
    logger.info("Password set for %s", username)


def create_session(username: str) -> str:
    return _signer.dumps({"u": username})


def get_session_user(request: Request) -> dict | None:
    token = request.cookies.get("supply_session")
    if not token:
        return None
    try:
        data = _signer.loads(token, max_age=SESSION_MAX_AGE)
        username = data.get("u")
        users = _load_users()
        if username not in users:
            return None
        return {"username": username, **users[username]}
    except (BadSignature, SignatureExpired):
        return None


def require_auth(request: Request) -> dict | None:
    """Returns user dict or None. Use in route handlers to gate access."""
    return get_session_user(request)


def login_redirect(next_url: str = "/library") -> RedirectResponse:
    return RedirectResponse(f"/login?next={next_url}", status_code=303)
