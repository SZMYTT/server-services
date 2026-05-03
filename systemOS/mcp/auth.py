"""
Auth module — session-based login for any FastAPI project.

Drop this into any project and call setup_auth(app) in main.py.

Usage in main.py:
    from systemOS.mcp.auth import setup_auth, require_user, login_required
    setup_auth(app, secret_key=os.getenv("SECRET_KEY"), users_from_env=True)

Usage in routes:
    @app.get("/dashboard")
    def dashboard(request: Request, user=Depends(login_required)):
        return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})

Users from .env:
    ADMIN_USERS=daniel:password123,alice:secret
    (format: username:password,username2:password2)

Or pass users dict directly:
    setup_auth(app, users={"daniel": "hashed_pw_here"})

Login page at /login, logout at /logout.
Session cookie: httponly, samesite=lax, 8hr expiry.
"""
import hashlib, logging, os
from typing import Callable
import httpx
from fastapi import Request, Response, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

logger = logging.getLogger(__name__)

_serializer: URLSafeTimedSerializer | None = None
_users: dict[str, str] = {}   # username → hashed password
_SESSION_COOKIE = "session"
_SESSION_MAX_AGE = 8 * 3600   # 8 hours


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def setup_auth(
    app,
    secret_key: str | None = None,
    users: dict | None = None,
    users_from_env: bool = True,
    login_template: str | None = None,
) -> None:
    """
    Register /login and /logout routes on the FastAPI app.

    Args:
        app:            FastAPI application instance
        secret_key:     Session signing key (use SECRET_KEY env if None)
        users:          Dict of {username: plaintext_password} or {username: sha256_hex}
        users_from_env: Read ADMIN_USERS=user:pass,user2:pass2 from environment
        login_template: Custom Jinja2 template name (default: built-in HTML)
    """
    global _serializer, _users
    key = secret_key or os.getenv("SECRET_KEY", "change-me-in-production")
    _serializer = URLSafeTimedSerializer(key)

    if users:
        for username, pw in users.items():
            _users[username] = _hash(pw) if len(pw) != 64 else pw

    if users_from_env:
        raw = os.getenv("ADMIN_USERS", "")
        for pair in raw.split(","):
            if ":" in pair:
                u, p = pair.strip().split(":", 1)
                _users[u.strip()] = _hash(p.strip())

    if not _users:
        logger.warning("[AUTH] No users configured — set ADMIN_USERS=user:pass in .env")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: str = ""):
        return _login_html(error=error)

    @app.post("/login")
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        stored = _users.get(username)
        if not stored or stored != _hash(password):
            return RedirectResponse("/login?error=Invalid+username+or+password", status_code=302)
        token = _serializer.dumps({"username": username})
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            _SESSION_COOKIE, token,
            max_age=_SESSION_MAX_AGE, httponly=True, samesite="lax"
        )
        logger.info("[AUTH] Login: %s", username)
        return response

    @app.get("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie(_SESSION_COOKIE)
        return response


def get_user(request: Request) -> dict | None:
    """Return current user dict or None if not logged in."""
    token = request.cookies.get(_SESSION_COOKIE)
    if not token or _serializer is None:
        return None
    try:
        data = _serializer.loads(token, max_age=_SESSION_MAX_AGE)
        return {"username": data["username"]}
    except (BadSignature, SignatureExpired, KeyError):
        return None


def login_required(request: Request) -> dict:
    """FastAPI Depends — redirects to /login if not authenticated."""
    user = get_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def require_user(request: Request) -> dict | None:
    """Soft check — returns user or None (no redirect). Use for optional auth."""
    return get_user(request)


def _login_html(error: str = "") -> str:
    err_block = f'<div class="error">{error}</div>' if error else ""
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'DM Sans',Arial,sans-serif;background:#F2EBD9;display:flex;
     align-items:center;justify-content:center;min-height:100vh;}}
.card{{background:#FAF5EC;border:1px solid #E2D9C4;border-radius:12px;
       padding:40px 36px;width:360px;box-shadow:0 4px 24px rgba(22,41,32,.10)}}
h1{{font-family:Georgia,serif;font-size:22px;color:#162920;margin-bottom:4px}}
.sub{{color:#6B8578;font-size:13px;margin-bottom:28px}}
label{{display:block;font-size:11px;font-weight:600;text-transform:uppercase;
       letter-spacing:.7px;color:#6B8578;margin-bottom:5px}}
input{{width:100%;padding:9px 12px;border:1px solid #E2D9C4;border-radius:6px;
       background:#EDE6D3;font-size:14px;color:#162920;margin-bottom:16px}}
input:focus{{outline:none;border-color:#BFA880}}
button{{width:100%;padding:11px;background:#1F3B2D;color:#F2EBD9;border:none;
        border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;margin-top:4px}}
button:hover{{background:#2D5243}}
.error{{background:#fdecea;border:1px solid #e57373;color:#8B3A2A;border-radius:6px;
        padding:10px 14px;margin-bottom:16px;font-size:13px}}
</style></head><body>
<div class="card">
  <h1>Sign in</h1>
  <p class="sub">SystemOS operator access</p>
  {err_block}
  <form method="post" action="/login">
    <label>Username</label>
    <input type="text" name="username" autocomplete="username" autofocus required>
    <label>Password</label>
    <input type="password" name="password" autocomplete="current-password" required>
    <button type="submit">Sign in →</button>
  </form>
</div></body></html>"""
