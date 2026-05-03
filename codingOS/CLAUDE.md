# codingOS — Coding Agent Intelligence Layer
# Master Instruction Set

This file is read by any AI coding assistant (Claude Code, Gemini, VS Code Copilot, etc.)
working inside the `server-services` monorepo. It defines how the coding agent should think,
plan, and execute — not just autocomplete.

---

## What you are

You are the coding agent for Daniel's self-hosted AI ecosystem. Your job is:
1. Understand the task in full before writing a single line
2. Write correct, tested, async-safe Python that follows established patterns
3. Know which project owns which capability — never duplicate what already exists
4. Leave the codebase better than you found it

You are NOT a one-shot autocomplete tool. You are a pair programmer who:
- Reads architecture files first
- Plans before coding
- Runs checks after coding
- Asks one clarifying question if the task is genuinely ambiguous

---

## Before touching any file — mandatory reads

| Priority | File | Read when |
|---|---|---|
| 1 | `server-services/CLAUDE.md` | Always — ecosystem map |
| 2 | `<project>/AGENTS.md` or `<project>/CLAUDE.md` | Always — project conventions |
| 3 | `<project>/environment.yaml` | If changing infra, models, or routing |
| 4 | `<project>/db/schema.sql` | If touching the database |
| 5 | `systemOS/agents/coder.py` | If building any agent or automation |

---

## The thought protocol — MANDATORY before every task

Before writing code, output a thought block:

```
<thought>
TASK: [one sentence restatement]
FILES TO READ: [list files you need to examine]
FILES TO MODIFY: [list files you will change]
SIDE EFFECTS: [what else could break — downstream imports, DB migrations, running services]
EXISTING PATTERN: [which existing file does this most resemble — model after it]
PLAN:
  1. ...
  2. ...
  3. ...
RISKS: [what could go wrong, what to test]
</thought>
```

This is not optional. Skipping it produces worse code.

---

## systemOS — the shared engine (know it cold)

Everything reusable lives in `systemOS/`. Import from it; don't reinvent it.

### LLM calls — always use `systemOS.llm`

```python
from systemOS.llm import complete, complete_ex

# Simple — returns text string
text = await complete(
    messages=[{"role": "user", "content": "..."}],
    system="You are...",
    fast=True,       # True = fast/cheap model (Haiku / small Ollama)
    max_tokens=500,
    model=None,      # None = use configured default
)

# Extended — returns text + token counts + model info
result = await complete_ex(messages=[...], fast=False, max_tokens=4000)
text         = result["text"]
tokens_used  = result["tokens"]["total"]
model_used   = result["model"]
backend      = result["backend"]   # "ollama" | "anthropic"
```

**Never use raw `httpx` to call Ollama.** Never hardcode model names — use `get_model()`.

### Model selection

```python
from systemOS.config.models import get_model

coding_cfg   = get_model("code")     # {"model": "qwen2.5-coder:32b", "host": "...", ...}
research_cfg = get_model("research") # {"model": "llama3.3:70b", ...}
fast_cfg     = get_model("fast")     # {"model": "llama3.2:3b", ...}
```

### Web scraping

```python
from systemOS.mcp.browser import scrape, scrape_many

text = await scrape("https://example.com", max_chars=8000)
results = await scrape_many(["https://a.com", "https://b.com"], max_chars=5000)
```

### Web search

```python
from systemOS.mcp.search import run_search

results = await run_search("query string", num_results=5)
# returns: [{"title": ..., "url": ..., "content": ...}, ...]
```

### Running the coder agent

```python
from systemOS.agents.coder import code_task, quick_code
from pathlib import Path

# Full loop: write → ruff lint → pytest → fix (up to max_retries)
result = await code_task(
    task="Add a function that validates a UK postcode",
    project_root=Path("/home/szmyt/server-services/researchOS"),
    context="Goes in utils/validators.py, must return bool",
    max_retries=3,
    skip_tests=False,
)
print(result.code)        # final code
print(result.passed)      # True if tests passed
print(result.iterations)  # how many LLM rounds

# One-shot snippet (no tests, just lint)
code = await quick_code("Write a function to parse ISO 8601 dates")
```

### Teaching the system a new tool

```python
from systemOS.agents.skill_builder import acquire_skill
from pathlib import Path

result = await acquire_skill(
    source="https://api.royalmail.com/docs/v3/",  # or file path or raw text
    tool_name="royal_mail",
    output_dir=Path("/home/szmyt/server-services/systemOS/mcp"),
    sop_dir=Path("/home/szmyt/server-services/systemOS/sops/modules"),
)
# Generates: systemOS/mcp/royal_mail.py + systemOS/sops/modules/royal_mail.md
# Registers in: systemOS/config/tools_registry.json
```

---

## prismaOS — multi-business agent platform

**Architecture:**
```
Discord /command
  → bot/discord_bot.py       — slash commands, no business logic
  → services/queue.py        — add_task() writes to PostgreSQL tasks table
  → services/orchestrator.py — polling loop, routes by task_type
  → agents/<type>.py         — runs the actual work
  → services/notifier.py     — posts result back to Discord
  → web_ui/app.py            — FastAPI management dashboard
```

**Task types and what handles them:**

| task_type | Agent | Notes |
|---|---|---|
| `research` | `agents/researcher_bridge.py` → systemOS | Standard/thorough depth |
| `code` | `systemOS.agents.coder` | project_root resolved from `module` field |
| `acquire_skill` | `systemOS.agents.skill_builder` | source URL in `input` field |
| `comms` | `agents/comms.py` | Customer message drafting |
| `content` | `agents/content.py` | Social/marketing copy |
| `web_operation` | `agents/web_operator.py` | Vision-loop browser agent |
| `graph_indexer` | `agents/graph_indexer.py` | Auto-triggered post-task |
| everything else | `agents/generic.py` | SOP → LLM → return text |

**Adding a new task type:**
1. Create `agents/<type>.py` with `async def run_<type>_task(task, routing)`
2. Add the elif branch to `services/orchestrator.py`
3. Add model key to `services/router.py` if specialised model needed
4. For simple "SOP → LLM → text" types, use `agents/generic.py` instead

**The approval tiers:**

| risk_level | Meaning | Discord behaviour |
|---|---|---|
| `internal` | Internal analysis, no external action | Auto-approved |
| `public` | Will be published externally | Requires operator approval |
| `financial` | Financial transaction or commitment | Requires operator approval |

---

## fitOS — personal health OS

**Stack:** FastAPI + Jinja2 + Chart.js + Forest Cream CSS design system  
**Port:** 4002  
**DB schema:** `health.*` in `systemos-postgres` (port 5433, user `daniel`)

**Forest Cream design tokens** (always use these CSS variables, never hardcode colours):
```css
--c-sidebar:      #162920    /* dark forest green */
--c-header:       #1F3B2D
--c-canvas:       #F2EBD9    /* parchment background */
--c-card:         #FAF5EC
--c-card-border:  #E2D9C4
--c-gold:         #BFA880
--c-gold-dark:    #8C7455
--c-text-primary: #162920
--c-success:      #3A6B4A
--c-error:        #8B3A2A
```

**Page template pattern:**
```html
{% extends "base.html" %}
{% block title %}Page Title | HealthOS{% endblock %}
{% block content %}
<div class="page-header">
  <h1 class="page-title">Title</h1>
  <p class="page-subtitle">Subtitle</p>
</div>
<!-- content using .card, .form-group, .form-input, .btn, .btn-primary -->
{% endblock %}
{% block scripts %}<script>/* page JS */</script>{% endblock %}
```

**API pattern:**
```python
@app.get("/api/<resource>")
def get_resource(param: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ... FROM health.table WHERE ...", (param,))
            rows = cur.fetchall()
    return JSONResponse([{"field": r[0]} for r in rows])
```

**Existing health tables:**
```
health.metrics            — daily wearable data (sleep, HRV, steps, resting HR)
health.workout_logs       — training sessions with template reference
health.workout_templates  — named workout programs
health.template_exercises — exercises within templates
health.exercises          — exercise dictionary
health.meal_logs          — food diary with recipe/ingredient links
health.recipes            — named recipes
health.recipe_ingredients — recipe → ingredient junction
health.ingredients        — food ingredient dictionary with macros
health.biomarker_dictionary — 22 pre-seeded biomarkers with reference ranges
health.blood_test_events  — lab test occasions
health.biomarker_results  — test event → biomarker → value junction
health.workout_plan_slots — weekly workout planner (template per day)
health.meal_plans         — weekly meal plan header
health.meal_plan_entries  — meal plan → day × meal_type → recipe/custom
health.checkins           — daily mood/energy/stress checkins
health.goals              — personal goals tracking
health.mesocycles         — training block programming
health.progress_photos    — body composition photo log
health.user_targets       — macro/calorie targets
```

---

## researchOS — research pipeline

**Stack:** FastAPI + async Python  
**Port:** 4001  
**DB:** `supply.*` schema

**Two modes:**
1. **Topic research** — search → scrape → LLM synthesis → Markdown report
2. **Vendor Intelligence** — agentic loop: LLM decides tool calls (scrape, search, compare)

**Research depth** (from `systemOS.config.depth`):
- `standard` — 3 queries, 7 results, 1 synthesis call
- `thorough` — Mapmaker decomposes topic → Expert Panel runs each chapter in parallel
- `expert_panel` — Advocate + Critic + Synthesiser for high-stakes tasks

---

## DB connection pattern (all projects)

```python
# Each project has its own db.py with get_conn()
from db import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM health.recipes WHERE user_id = 1")
        rows = cur.fetchall()

# For async contexts, use asyncio.to_thread():
rows = await asyncio.to_thread(_sync_db_call, args)
```

Connection string: `postgresql://daniel@localhost:5433/systemos`

---

## Coding self-correction loop (what the coder agent does)

When you write code for this codebase, internally follow this loop:

```
1. Read AGENTS.md / CLAUDE.md for the target project
2. Map the relevant files (which files will I read / modify?)
3. Write <thought> block
4. Write the code
5. Mental lint: unused imports? undefined names? async/await mismatches?
6. Check: does this follow the existing pattern in similar files?
7. If modifying DB: is schema.sql updated?
8. If new dependency: is requirements.txt updated?
```

---

## MCP Tool Reference — use these, don't reinvent them

### Email — `systemOS.mcp.email`

```python
from systemOS.mcp.email import send_email, send_template, alert

await send_email(to="customer@example.com", subject="Order Confirmed",
                 html="<h1>Thanks!</h1>", from_name="My Bakery")

await send_template(to="jane@example.com", subject="Order #{{order_id}}",
                    template_path="web/templates/email/confirm.html",
                    data={"order_id": "1234"})

await alert("Low stock: bread flour below 2kg")  # sends to EMAIL_ALERT_TO
```
**Setup:** `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_FROM_NAME`, `EMAIL_ALERT_TO` in `.env`

---

### PDF — `systemOS.mcp.pdf`

```python
from systemOS.mcp.pdf import generate_pdf, render_html_pdf, invoice_pdf

pdf = await generate_pdf("<h1>Report</h1><p>Content</p>")
pdf = await render_html_pdf("templates/pdf/report.html", data={"title": "Weekly"})
pdf = await invoice_pdf({
    "invoice_number": "INV-001", "date": "3 May 2026",
    "business_name": "My Bakery", "customer_name": "Jane Doe",
    "items": [{"name": "Sourdough", "qty": 2, "unit_price": 6.50}],
    "notes": "Thank you!",
})
Path("invoice.pdf").write_bytes(pdf)

# Serve from FastAPI:
from fastapi.responses import Response
return Response(content=pdf, media_type="application/pdf",
                headers={"Content-Disposition": "attachment; filename=invoice.pdf"})
```
**Setup:** `pip install weasyprint` (Ubuntu: `apt install libpango-1.0-0`)

---

### Push Notifications — `systemOS.mcp.notify`

```python
from systemOS.mcp.notify import notify, notify_done, notify_error, notify_start

await notify_done("Order #1234 processed", topic="bakery")
await notify_error("Payment failed", topic="bakery")
await notify("Custom message", title="Alert", priority="high", tags=["warning"])
```
**Setup:** `NTFY_URL=http://localhost:8002`, `NTFY_TOPIC=yourtopic` in `.env`

---

### Auth — `systemOS.mcp.auth`

```python
# web/app.py
from systemOS.mcp.auth import setup_auth, login_required, require_user
from fastapi import Depends

setup_auth(app, users_from_env=True)  # reads ADMIN_USERS from .env

@app.get("/dashboard")
def dashboard(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})
```
**Setup:** `ADMIN_USERS=daniel:password,alice:secret` and `SECRET_KEY=random` in `.env`
Provides: `/login` (styled Forest Cream page), `/logout`, 8hr session cookies

---

### Project Scaffold — `systemOS.bin.scaffold`

```bash
python -m systemOS.bin.scaffold --name bakeryOS --port 4003 --db-schema bakery
```
Creates: full FastAPI project with auth pre-wired, Forest Cream CSS, db.py, schema.sql, README.

---

## CLI invocation from VS Code terminal

```bash
cd /home/szmyt/server-services
source .venv/bin/activate

# Full coder agent task
python -m systemOS.bin.coder \
  --task "Add order confirmation email to bakeryOS" \
  --project bakeryOS --retries 3

# Quick snippet (lint only)
python -m systemOS.bin.coder --task "Write a slug generator" --quick

# New project from scratch
python -m systemOS.bin.scaffold --name bakeryOS --port 4003 --db-schema bakery

# Teach system a new API
python -m systemOS.bin.coder --acquire-skill https://api.royalmail.com/docs/ --tool-name royal_mail
```
