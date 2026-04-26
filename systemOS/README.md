# systemOS

**Code library only. No UI, no users, no running process.**

systemOS is the shared foundation. It provides generic tools (scraping, search, LLM, memory, notifications, Drive), the task runtime (queue, orchestrator, scheduler), and infrastructure config. Any project imports what it needs — no copying.

OS services are grouped by **capability** (research, procurement, content), not by NNL business domain. A new NNL domain is a project/tag inside the right existing service — not a reason to create a new OS. See the root `CLAUDE.md` for the full mental model.

---

## Projects on this platform

| Project | Description | Status |
|---------|-------------|--------|
| [prismaOS](../prismaOS/) | Business AI centre (candles, cars, nursing, property, food brand) | Live |
| [researchOS](../researchOS/) | Research module — topic research + vendor intelligence (port 4001) | Active |
| [nnlos](../nnlos/) | NNL procurement & inventory intelligence | In progress |
| [vendorOS](../vendorOS/) | NNL vendor strategy, sample tracking, dependency risk | Planned |
| [fitOS](../fitOS/) | Fitness & life coach — Android/web, Garmin/Strava | Planned |

---

## Shared tools — import these in any project

### Core

```python
from systemOS.llm import complete, complete_ex         # Ollama/Anthropic — returns text or full result with tokens
from systemOS.mcp.browser import scrape, scrape_many   # Crawl4AI web scraper
from systemOS.mcp.search import run_search             # SearXNG search
```

### Config

```python
from systemOS.config.depth import get as get_depth     # Research depth presets: quick/standard/deep/thorough
from systemOS.config.models import get_model, MODELS   # Model routing: task_type → Ollama model config
```

### Notifications

```python
from systemOS.mcp.notify import notify, notify_done, notify_error, notify_start
# Push notifications via Ntfy (localhost:8002) to phone/desktop
```

### Memory

```python
from systemOS.mcp.memory import upsert, search, delete, collection_info
# ChromaDB vector memory (localhost:8001) — semantic search over stored text
```

### Google Drive

```python
from systemOS.mcp.drive import read_file, read_csv, list_files, create_file, find_file
# Drive API v3 — read MRP Easy exports, save reports
```

### Web (for projects building a web UI)

```python
# Templates: extend systemOS/web/templates/base.html
# CSS/JS:    serve from systemOS/web/static/css/base.css + js/main.js
from systemOS.web.auth import get_session_user, verify_password, create_session, login_redirect
```

---

## Structure

```
systemOS/
├── llm.py                  # LLM abstraction — complete() + complete_ex() with token counts
├── mcp/                    # Shared tool wrappers
│   ├── browser.py          # Crawl4AI scraper: scrape(url) + scrape_many(urls)
│   ├── search.py           # SearXNG client: run_search(query)
│   ├── notify.py           # Ntfy push: notify(), notify_done(), notify_error()
│   ├── memory.py           # ChromaDB vector store: upsert(), search(), delete()
│   ├── drive.py            # Google Drive: read_file(), read_csv(), create_file()
│   └── web_agent.py        # Browser automation agent
├── config/
│   ├── depth.py            # Research depth presets: quick/standard/deep/thorough
│   └── models.py           # Model catalogue + task→model routing
├── web/                    # Shared web layer for projects with a UI
│   ├── auth.py             # Session auth: bcrypt + itsdangerous cookies
│   ├── templates/
│   │   └── base.html       # Base HTML shell — projects override sidebar/logo blocks
│   └── static/
│       ├── css/base.css    # Full UI stylesheet
│       └── js/main.js      # UI helpers
├── services/               # Task runtime (used by prismaOS)
│   ├── queue.py            # PostgreSQL-backed task queue with priority scoring
│   ├── orchestrator.py     # Event loop — routes tasks to agents
│   ├── scheduler.py        # APScheduler-based cron triggers
│   ├── checkpointer.py     # Step-level task checkpointing
│   ├── retry.py            # Retry with backoff
│   ├── router.py           # Model routing with host reachability check
│   └── sop_assembler.py    # 3-layer SOP injection (system + module + workspace)
├── agents/                 # Base agent implementations
│   ├── generic.py          # Universal agent: SOP → LLM → output
│   ├── researcher.py       # Research pipeline
│   ├── content.py          # Content agent
│   └── comms.py            # Communications agent
├── sops/
│   ├── system/core.md      # Core system identity prompt
│   └── modules/            # Module-level SOP files (research, content, finance, …)
├── db/
│   └── schema_core.sql     # tasks, task_steps, schedules, users, audit_log
├── environment.yaml        # Hardware, inference hosts, models, services
└── requirements.txt
```

---

## How to use systemOS in a new project

**Step 1 — Bootstrap `main.py`:**
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))           # project root
sys.path.insert(0, str(Path(__file__).parent.parent))    # server-services/ → systemOS importable
```

**Step 2 — Install deps in project venv:**
```bash
pip install crawl4ai anthropic httpx python-dotenv chromadb google-api-python-client
pip install bcrypt itsdangerous pyyaml jinja2 fastapi uvicorn  # if building a web UI
```

**Step 3 — Import and use:**
```python
from systemOS.mcp.browser import scrape, scrape_many
from systemOS.mcp.search import run_search
from systemOS.llm import complete
from systemOS.config.depth import get as get_depth
from systemOS.config.models import get_model
from systemOS.mcp.notify import notify_done, notify_error
from systemOS.mcp.memory import upsert, search
from systemOS.mcp.drive import read_csv
```

**What stays project-specific (never in systemOS):**
- `db.py` — each project owns its DB schema and connection
- `agents/*.py` — domain-specific prompt logic
- `web/app.py` + project templates — FastAPI app, project-specific nav/pages
- `.env` and all credentials

---

## Token tracking

`complete()` returns just the text string (backwards-compatible).
`complete_ex()` returns a `LLMResult` dict with text + token counts + model info:

```python
from systemOS.llm import complete_ex
result = await complete_ex(messages=[...])
print(result["text"])                        # response
print(result["tokens"])                      # {"prompt": 120, "completion": 340, "total": 460}
print(result["model"], result["backend"])    # "llama3.3:70b", "ollama"
```

---

## Notifications

Subscribe to topics on the Ntfy app (iOS/Android) or ntfy.sh web UI.
Ntfy runs at `http://localhost:8002`. Default topic: `systemos`.
Projects should use their own topic (e.g. `researchos`, `nnlos`).

```python
from systemOS.mcp.notify import notify_done, notify_error, notify_start
await notify_start("Vendor scrape started for Carvansons", topic="researchos")
await notify_done("Vendor profile ready: Carvansons", topic="researchos")
```

---

## Google Drive auth (one-time setup)

Service account (recommended for server automation):
```bash
# 1. Create service account in Google Cloud Console
# 2. Enable Drive API
# 3. Download JSON key → save to project/config/google_service_account.json
# 4. Share Drive folder with the service account email
# 5. Add to project .env:
GOOGLE_SERVICE_ACCOUNT_FILE=config/google_service_account.json
```

---

## Database

Shared PostgreSQL: `systemos` db, port 5433, container `systemos-postgres`.

Core schema (`db/schema_core.sql`): tasks, task_steps, schedules, module_estimates, workspace_analytics, users, audit_log.

Each project has its own schema: `supply.*` (researchOS), `nnlos.*`, `vendor.*` etc.

---

## Running systemd services (prismaOS)

```bash
sudo systemctl restart prisma-web
sudo systemctl restart prisma-orchestrator
sudo systemctl restart prisma-bot
```
