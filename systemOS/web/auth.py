"""
Shared session auth for all systemOS web projects.

bcrypt password hashing, itsdangerous signed cookies, users from a YAML file.

Import from any project:
    from systemOS.web.auth import get_session_user, verify_password, create_session, login_redirect

Each project configures via env vars:
    SECRET_KEY        — signing key (required, set in .env)
    SESSION_COOKIE    — cookie name (default: "os_session")
    SESSION_SALT      — itsdangerous salt (default: "os-session")
    SESSION_MAX_HOURS — session lifetime in hours (default: 24)

Users are loaded from a YAML file at:
    <project_root>/config/users.yaml
    <project_root>/config/user_hashes.yaml

users.yaml format:
    users:
      daniel:
        display_name: Daniel
        role: admin

user_hashes.yaml format:
    daniel: $2b$12$...bcrypt hash...

To set a password from a project setup script:
    from systemOS.web.auth import set_password
    set_password("daniel", "mypassword", config_dir=Path("config"))
"""

import logging
import os
from pathlib import Path

import bcrypt
import yaml
from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

_SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-env")
_COOKIE_NAME = os.getenv("SESSION_COOKIE", "os_session")
_SALT = os.getenv("SESSION_SALT", "os-session")
_MAX_AGE = int(os.getenv("SESSION_MAX_HOURS", "24")) * 3600

_signer = URLSafeTimedSerializer(_SECRET_KEY, salt=_SALT)


def _users_file(config_dir: Path) -> Path:
    return config_dir / "users.yaml"


def _hashes_file(config_dir: Path) -> Path:
    return config_dir / "user_hashes.yaml"


def _load_users(config_dir: Path) -> dict:
    f = _users_file(config_dir)
    if not f.exists():
        return {}
    return yaml.safe_load(f.read_text()).get("users", {})


def _load_hashes(config_dir: Path) -> dict:
    f = _hashes_file(config_dir)
    if not f.exists():
        return {}
    return yaml.safe_load(f.read_text()) or {}


def verify_password(username: str, plaintext: str, config_dir: Path) -> bool:
    hashes = _load_hashes(config_dir)
    stored = hashes.get(username)
    if not stored:
        return False
    return bcrypt.checkpw(plaintext.encode(), stored.encode())


def set_password(username: str, plaintext: str, config_dir: Path):
    """Hash and store a password. Call from a project setup script."""
    hashes = _load_hashes(config_dir)
    hashes[username] = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=12)).decode()
    _hashes_file(config_dir).write_text(yaml.dump(hashes))
    logger.info("Password set for %s", username)


def create_session(username: str) -> str:
    return _signer.dumps({"u": username})


def get_session_user(request: Request, config_dir: Path) -> dict | None:
    """
    Read the signed session cookie and return the user dict, or None if
    not logged in / session expired.
    """
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return None
    try:
        data = _signer.loads(token, max_age=_MAX_AGE)
        username = data.get("u")
        users = _load_users(config_dir)
        if username not in users:
            return None
        return {"username": username, **users[username]}
    except (BadSignature, SignatureExpired):
        return None


def set_session_cookie(response, username: str):
    """Attach a signed session cookie to a response."""
    token = create_session(username)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=_MAX_AGE,
    )


def clear_session_cookie(response):
    response.delete_cookie(_COOKIE_NAME)


def login_redirect(next_url: str = "/") -> RedirectResponse:
    return RedirectResponse(f"/login?next={next_url}", status_code=303)
