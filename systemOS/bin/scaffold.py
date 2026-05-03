"""
Project Scaffolder — stamp out a complete FastAPI project in seconds.

Usage:
    python -m systemOS.bin.scaffold --name bakeryOS --port 4003 --db-schema bakery

What it creates:
    bakeryOS/
    ├── main.py              — FastAPI entry point + systemOS path bootstrap
    ├── db.py                — psycopg2 connection pool
    ├── .env                 — environment template (edit before running)
    ├── requirements.txt     — pinned deps
    ├── db/schema.sql        — empty schema with example table
    ├── web/
    │   ├── app.py           — FastAPI router (pages + API)
    │   ├── templates/
    │   │   ├── base.html    — Forest Cream sidebar layout
    │   │   └── dashboard.html
    │   └── static/
    │       ├── css/app.css  — Forest Cream design tokens
    │       └── js/main.js   — toast notifications + sidebar toggle
    └── README.md
"""
import argparse
import os
import sys
import textwrap
from pathlib import Path

_SERVER_ROOT = Path(__file__).parent.parent.parent  # server-services/


# ── Templates ──────────────────────────────────────────────────────────────────

def _main_py(name: str, port: int) -> str:
    return textwrap.dedent(f"""\
    \"\"\"
    {name} — FastAPI application entry point.
    Run: uvicorn main:app --reload --port {port}
    \"\"\"
    import sys
    from pathlib import Path

    # Make systemOS importable
    sys.path.insert(0, str(Path(__file__).parent))
    sys.path.insert(0, str(Path(__file__).parent.parent))

    import uvicorn
    from web.app import app  # noqa: F401

    if __name__ == "__main__":
        uvicorn.run("web.app:app", host="0.0.0.0", port={port}, reload=True)
    """)


def _db_py(schema: str) -> str:
    return textwrap.dedent(f"""\
    \"\"\"DB connection — psycopg2 pool for {schema} schema.\"\"\"
    import os
    import psycopg2
    from psycopg2 import pool
    from contextlib import contextmanager
    from dotenv import load_dotenv

    load_dotenv()

    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "postgresql://daniel@localhost:5433/systemos"
    )

    _pool = psycopg2.pool.SimpleConnectionPool(1, 10, DATABASE_URL)


    @contextmanager
    def get_conn():
        conn = _pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _pool.putconn(conn)
    """)


def _env_template(name: str, port: int, schema: str) -> str:
    return textwrap.dedent(f"""\
    # {name} environment — copy to .env and fill in values
    DATABASE_URL=postgresql://daniel@localhost:5433/systemos
    SECRET_KEY=change-me-to-a-random-string

    # Auth (admin login)
    ADMIN_USERS=daniel:your-password-here

    # Email (Resend)
    RESEND_API_KEY=
    EMAIL_FROM=noreply@yourdomain.com
    EMAIL_FROM_NAME={name}
    EMAIL_ALERT_TO=your@email.com

    # Push notifications (ntfy — already running on server)
    NTFY_URL=http://localhost:8002
    NTFY_TOPIC={name.lower()}

    # Ollama (for LLM features)
    OLLAMA_URL=http://100.76.139.41:11434
    OLLAMA_MODEL=gemma4:26b
    """)


def _requirements_txt() -> str:
    return textwrap.dedent("""\
    fastapi==0.115.0
    uvicorn[standard]==0.30.0
    python-multipart==0.0.9
    psycopg2-binary==2.9.9
    python-dotenv==1.0.1
    httpx==0.27.2
    itsdangerous==2.2.0
    jinja2==3.1.4
    weasyprint>=62.0        # PDF generation — pip install weasyprint
    """)


def _schema_sql(schema: str, name: str) -> str:
    return textwrap.dedent(f"""\
    -- {name} database schema
    -- Apply: docker exec systemos-postgres psql -U daniel -d systemos -f /tmp/schema.sql

    CREATE SCHEMA IF NOT EXISTS {schema};

    -- Example table — rename/replace with your actual tables
    CREATE TABLE IF NOT EXISTS {schema}.items (
        id          SERIAL       PRIMARY KEY,
        name        VARCHAR(200) NOT NULL,
        description TEXT,
        created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );
    """)


def _web_app_py(name: str, schema: str) -> str:
    return textwrap.dedent(f"""\
    \"\"\"
    {name} — FastAPI router (pages + API).
    \"\"\"
    import os
    import sys
    from pathlib import Path

    from dotenv import load_dotenv
    from fastapi import FastAPI, Request, Depends
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates

    load_dotenv()
    sys.path.insert(0, str(Path(__file__).parent.parent))
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from db import get_conn
    from systemOS.mcp.auth import setup_auth, login_required, require_user

    app = FastAPI(title="{name}", docs_url="/api/docs")

    _HERE = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
    templates = Jinja2Templates(directory=_HERE / "templates")

    # ── Auth ──────────────────────────────────────────────────
    setup_auth(app, users_from_env=True)

    # ── Pages ─────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, user=Depends(login_required)):
        return templates.TemplateResponse(
            "dashboard.html", {{"request": request, "page": "dashboard", "user": user}}
        )

    # ── API ───────────────────────────────────────────────────

    @app.get("/api/health")
    def health():
        return {{"status": "ok", "service": "{name}"}}

    @app.get("/api/items")
    def list_items(user=Depends(login_required)):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, description FROM {schema}.items ORDER BY created_at DESC")
                rows = cur.fetchall()
        return JSONResponse([{{"id": r[0], "name": r[1], "description": r[2]}} for r in rows])
    """)


def _base_html(name: str) -> str:
    return textwrap.dedent("""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width,initial-scale=1.0">
      <title>{% block title %}""" + name + """{% endblock %}</title>
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600;700&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&family=JetBrains+Mono:wght@400;500&display=swap" media="print" onload="this.media='all'">
      <link rel="stylesheet" href="/static/css/app.css">
      {% block head %}{% endblock %}
    </head>
    <body>
    <div class="app-shell">
      <header class="app-header">
        <a href="/" class="header-logo">
          <svg class="header-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18"/>
          </svg>
          """ + name + """
        </a>
        <div class="header-right">
          {% if user %}<a href="/logout" class="header-signout">Sign out</a>{% endif %}
        </div>
      </header>
      <div class="app-body">
        <aside class="app-sidebar" id="sidebar">
          <nav class="sidebar-nav">
            <div class="sidebar-section-label">Navigation</div>
            <a href="/" class="sidebar-nav-item {% if page=='dashboard' %}active{% endif %}">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/>
              </svg>
              Dashboard
            </a>
            {% block sidebar_links %}{% endblock %}
          </nav>
        </aside>
        <main class="app-main">
          <div class="page-content">
            {% block content %}{% endblock %}
          </div>
        </main>
      </div>
    </div>
    <div id="toast-container"></div>
    <script src="/static/js/main.js"></script>
    {% block scripts %}{% endblock %}
    </body>
    </html>
    """)


def _dashboard_html(name: str) -> str:
    return textwrap.dedent(f"""\
    {{% extends "base.html" %}}
    {{% block title %}}Dashboard | {name}{{% endblock %}}
    {{% block content %}}
    <div class="page-header">
      <h1 class="page-title">Dashboard</h1>
      <p class="page-subtitle">Welcome to {name}</p>
    </div>
    <div class="card">
      <div class="card-title">Getting Started</div>
      <p style="color:var(--c-text-muted);font-size:13px;">
        Your project is running. Edit <code>web/app.py</code> to add routes,
        and <code>db/schema.sql</code> to define your database structure.
      </p>
    </div>
    {{% endblock %}}
    """)


def _app_css() -> str:
    return textwrap.dedent("""\
    /* ── Design Tokens (Forest Cream) ───────────────────────── */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    a { text-decoration: none; color: inherit; }
    button { cursor: pointer; border: none; background: none; font-family: inherit; }
    input, select, textarea { font-family: inherit; }

    :root {
      --c-sidebar:      #162920;
      --c-header:       #1F3B2D;
      --c-hover-dark:   #2D5243;
      --c-active-dark:  #4E6F57;
      --c-canvas:       #F2EBD9;
      --c-card:         #FAF5EC;
      --c-card-border:  #E2D9C4;
      --c-input-bg:     #EDE6D3;
      --c-gold:         #BFA880;
      --c-gold-dark:    #8C7455;
      --c-text-primary: #162920;
      --c-text-secondary:#3A5244;
      --c-text-muted:   #6B8578;
      --c-text-on-dark: #F2EBD9;
      --c-success:      #3A6B4A;
      --c-warning:      #9B7A2A;
      --c-error:        #8B3A2A;
      --shadow-card:    0 1px 3px rgba(22,41,32,.06);
    }

    html, body { height: 100%; }
    body { font-family:'DM Sans',sans-serif; font-size:14px; line-height:1.6;
           color:var(--c-text-secondary); background:var(--c-canvas); -webkit-font-smoothing:antialiased; }
    h1,h2,h3,h4 { font-family:'Playfair Display',serif; color:var(--c-text-primary); line-height:1.2; }

    /* ── App Shell ────────────────────────────────────────── */
    .app-shell { display:flex; flex-direction:column; height:100vh; overflow:hidden; }
    .app-header { height:56px; background:var(--c-header); display:flex; align-items:center;
                  justify-content:space-between; padding:0 20px; flex-shrink:0; }
    .header-logo { display:flex; align-items:center; gap:10px; font-family:'Playfair Display',serif;
                   font-size:18px; font-weight:600; color:var(--c-text-on-dark); }
    .header-logo-icon { width:22px; height:22px; }
    .header-signout { color:var(--c-text-on-dark); opacity:.6; font-size:13px; }
    .header-signout:hover { opacity:1; }
    .app-body { display:flex; flex:1; overflow:hidden; }
    .app-sidebar { width:220px; background:var(--c-sidebar); flex-shrink:0;
                   overflow-y:auto; display:flex; flex-direction:column; padding:16px 0; }
    .app-main { flex:1; overflow-y:auto; padding:28px 32px; }

    /* ── Sidebar ──────────────────────────────────────────── */
    .sidebar-nav { display:flex; flex-direction:column; gap:2px; padding:0 10px; }
    .sidebar-section-label { font-size:10px; font-weight:600; text-transform:uppercase;
                              letter-spacing:.8px; color:rgba(242,235,217,.4); padding:12px 8px 6px; }
    .sidebar-nav-item { display:flex; align-items:center; gap:10px; padding:8px 10px;
                        border-radius:6px; color:rgba(242,235,217,.7); font-size:13px;
                        font-weight:500; transition:all .15s; }
    .sidebar-nav-item svg { width:16px; height:16px; flex-shrink:0; }
    .sidebar-nav-item:hover { background:var(--c-hover-dark); color:var(--c-text-on-dark); }
    .sidebar-nav-item.active { background:var(--c-active-dark); color:var(--c-text-on-dark); }

    /* ── Page ─────────────────────────────────────────────── */
    .page-content { max-width:1200px; }
    .page-header { margin-bottom:24px; }
    .page-title { font-size:24px; font-weight:700; }
    .page-subtitle { color:var(--c-text-muted); font-size:13.5px; margin-top:4px; }

    /* ── Card ─────────────────────────────────────────────── */
    .card { background:var(--c-card); border:1px solid var(--c-card-border);
            border-radius:10px; padding:20px 24px; box-shadow:var(--shadow-card); margin-bottom:16px; }
    .card-title { font-family:'Playfair Display',serif; font-size:15px; font-weight:600;
                  color:var(--c-text-primary); margin-bottom:14px; }

    /* ── Forms ────────────────────────────────────────────── */
    .form-group { margin-bottom:14px; }
    .form-label { display:block; font-size:11px; font-weight:600; text-transform:uppercase;
                  letter-spacing:.7px; color:var(--c-text-muted); margin-bottom:5px; }
    .form-input, .form-select, .form-textarea {
      width:100%; padding:9px 12px; border:1px solid var(--c-card-border);
      border-radius:6px; background:var(--c-input-bg); font-size:13.5px;
      color:var(--c-text-primary); transition:border-color .15s; }
    .form-input:focus, .form-select:focus, .form-textarea:focus {
      outline:none; border-color:var(--c-gold); }

    /* ── Buttons ──────────────────────────────────────────── */
    .btn { display:inline-flex; align-items:center; gap:7px; padding:9px 18px;
           border-radius:7px; font-size:13.5px; font-weight:500; transition:all .15s; }
    .btn-primary { background:var(--c-header); color:var(--c-text-on-dark); }
    .btn-primary:hover { background:var(--c-hover-dark); }
    .btn-secondary { background:var(--c-input-bg); color:var(--c-text-secondary);
                     border:1px solid var(--c-card-border); }
    .btn-secondary:hover { border-color:var(--c-gold); }
    .btn-full { width:100%; justify-content:center; }

    /* ── Toast ────────────────────────────────────────────── */
    #toast-container { position:fixed; bottom:24px; right:24px; z-index:9999;
                       display:flex; flex-direction:column; gap:8px; }
    .toast { padding:12px 18px; border-radius:8px; font-size:13px; font-weight:500;
             box-shadow:0 4px 16px rgba(22,41,32,.15); animation:slideIn .2s ease; max-width:320px; }
    .toast-success { background:#1F3B2D; color:#F2EBD9; }
    .toast-error   { background:#8B3A2A; color:#FFF; }
    @keyframes slideIn { from { transform:translateX(20px); opacity:0; } to { transform:none; opacity:1; } }
    """)


def _main_js() -> str:
    return textwrap.dedent("""\
    // Shared JS — toast notifications
    function showToast(msg, type='success') {
      const c = document.getElementById('toast-container');
      if (!c) return;
      const t = document.createElement('div');
      t.className = 'toast toast-' + type;
      t.textContent = msg;
      c.appendChild(t);
      setTimeout(() => t.remove(), 3500);
    }
    """)


def _readme(name: str, port: int, schema: str) -> str:
    return textwrap.dedent(f"""\
    # {name}

    FastAPI project scaffolded by systemOS.

    ## Setup

    ```bash
    # 1. Create venv
    python3 -m venv venv && source venv/bin/activate

    # 2. Install deps
    pip install -r requirements.txt

    # 3. Configure environment
    cp .env.example .env
    # Edit .env with your values

    # 4. Apply DB schema
    docker cp db/schema.sql systemos-postgres:/tmp/{schema}_schema.sql
    docker exec systemos-postgres psql -U daniel -d systemos -f /tmp/{schema}_schema.sql

    # 5. Run
    python main.py
    # or: uvicorn web.app:app --reload --port {port}
    ```

    ## Structure

    ```
    main.py          — entry point
    db.py            — database connection pool
    .env             — environment variables (never commit)
    db/schema.sql    — database schema (source of truth)
    web/
      app.py         — FastAPI routes (pages + API)
      templates/     — Jinja2 HTML templates
      static/        — CSS, JS, images
    ```

    ## Coding conventions

    See `/home/szmyt/server-services/codingOS/CLAUDE.md` for full agent instructions.

    - All DB changes → update `db/schema.sql` first
    - All new routes → add to `web/app.py`
    - New dependencies → add to `requirements.txt`
    - Use `systemOS.mcp.*` for email, PDF, notifications, auth
    """)


# ── Scaffold runner ────────────────────────────────────────────────────────────

def scaffold(name: str, port: int, schema: str, output_dir: Path) -> Path:
    """Create the full project structure. Returns the project root path."""
    root = output_dir / name
    if root.exists():
        print(f"⚠  Directory already exists: {root}")
        return root

    dirs = [
        root / "web" / "templates",
        root / "web" / "static" / "css",
        root / "web" / "static" / "js",
        root / "db",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    files = {
        root / "main.py":                          _main_py(name, port),
        root / "db.py":                            _db_py(schema),
        root / ".env.example":                     _env_template(name, port, schema),
        root / "requirements.txt":                 _requirements_txt(),
        root / "README.md":                        _readme(name, port, schema),
        root / "db" / "schema.sql":                _schema_sql(schema, name),
        root / "web" / "__init__.py":              "",
        root / "web" / "app.py":                   _web_app_py(name, schema),
        root / "web" / "templates" / "base.html":  _base_html(name),
        root / "web" / "templates" / "dashboard.html": _dashboard_html(name),
        root / "web" / "static" / "css" / "app.css":   _app_css(),
        root / "web" / "static" / "js" / "main.js":    _main_js(),
    }

    for path, content in files.items():
        path.write_text(content, encoding="utf-8")

    return root


def main():
    p = argparse.ArgumentParser(
        prog="python -m systemOS.bin.scaffold",
        description="Scaffold a new FastAPI project with Forest Cream design system",
    )
    p.add_argument("--name",      "-n", required=True, help="Project name (e.g. bakeryOS)")
    p.add_argument("--port",      "-p", type=int, default=4010, help="Dev server port (default 4010)")
    p.add_argument("--db-schema", "-s", default=None, help="PostgreSQL schema name (default: name.lower())")
    p.add_argument("--output",    "-o", default=str(_SERVER_ROOT), help="Parent directory for the project")
    args = p.parse_args()

    schema     = args.db_schema or args.name.lower().replace("-", "_").replace("os", "").rstrip("_") or args.name.lower()
    output_dir = Path(args.output)
    root       = scaffold(args.name, args.port, schema, output_dir)

    print(f"\n✅  Project scaffolded: {root}")
    print(f"\nNext steps:")
    print(f"  cd {root}")
    print(f"  python3 -m venv venv && source venv/bin/activate")
    print(f"  pip install -r requirements.txt")
    print(f"  cp .env.example .env  # fill in your values")
    print(f"  python main.py        # starts on port {args.port}")
    print(f"\nDB schema:")
    print(f"  docker cp db/schema.sql systemos-postgres:/tmp/schema.sql")
    print(f"  docker exec systemos-postgres psql -U daniel -d systemos -f /tmp/schema.sql")


if __name__ == "__main__":
    main()
