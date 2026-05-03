# AGENTS.md
# PrismaOS — AI Development Guide

This file gives any AI assistant (Claude, Gemini, GPT, etc.) working in this
repository the context, conventions, and rules it needs to contribute correctly.
Read this before touching any file.

---

## What PrismaOS Is

PrismaOS is a self-hosted, multi-tenant AI operations platform for a small group
of businesses. It connects a Discord bot (input layer) to an AI agent pipeline
(orchestrator + agents) backed by a PostgreSQL database, with a FastAPI web
dashboard as the management UI.

**The five businesses it serves:**
| Workspace | Owner | Type |
|---|---|---|
| `candles` | Alice | Etsy handmade candle shop |
| `cars` | Eddie | Auction car flipping |
| `property` | Eddie + Daniel | Property investment |
| `nursing_massage` | Asta | Medical massage clinic |
| `food_brand` | Alicja | Healthy eating brand |

**Daniel (szmyt)** is the operator — he approves all tasks and has full access.

---

## Source of Truth Files

Before making ANY change, read these files first:

| File | Purpose |
|---|---|
| `environment.yaml` | Master config: hardware, models, workspaces, team, routing |
| `README.md` | Roadmap, decisions log, open questions |
| `db/schema.sql` | Database schema — single source of truth for all tables |
| `FUTURE_IDEAS.md` | Backlog of unscheduled ideas — check before adding new features |

> **Rule:** If something is in `environment.yaml`, do not hardcode it elsewhere.
> Read the file. If a fact about the system changes, update `environment.yaml` first.

---

## Architecture at a Glance

```
Discord (/command)
    ↓
bot/discord_bot.py          — receives user input, queues task
    ↓
services/queue.py           — writes task to PostgreSQL
    ↓
services/orchestrator.py    — polling loop, picks up approved tasks
    ↓
services/router.py          — selects model + host from task metadata
    ↓
agents/<type>.py            — runs the actual LLM inference
    ↓
services/checkpointer.py    — checkpoints each step to task_steps table
    ↓
services/notifier.py        — posts result back to Discord channel
    ↓
web_ui/app.py               — FastAPI dashboard for viewing/approving tasks
```

---

## Inference Rules

**There is ONE inference host: the MacBook Pro.**
The gaming PC is a development workstation only (VS Code / SSH). It does NOT
run Ollama in production. Never route tasks to `gaming-pc` or
`OLLAMA_GAMING_URL` in production code.

Current model catalogue (`services/router.py`):
```
orchestrator / researcher  → llama3.3:70b   @ macbook-pro
coder                      → qwen2.5-coder:32b @ macbook-pro
content                    → mistral:22b    @ macbook-pro
finance / documents        → phi4:14b       @ macbook-pro
fast / comms / routing     → llama3.2:3b    @ macbook-pro
```

Fallback on Mac unreachable: queue the task and wait. Do NOT silently drop it.

---

## Coding Conventions

### Python
- All agent and service functions must be **async** — this is an asyncio system
- All database calls go through `services/queue.py` — never raw SQL elsewhere
- Always use `logger = logging.getLogger("prisma.<module>")` for logging
- Log format: `[MODULE_NAME] message` — e.g. `[RESEARCHER] Starting step 2`
- Type hints are encouraged but not mandatory
- No `print()` in production code — use logger

### Logging levels
- `logger.info` — normal operations (task started, step completed)
- `logger.warning` — recoverable issues (fallback triggered, retry attempt)
- `logger.error` — failures that need operator attention

### Error handling
- Every agent must wrap its full body in a `try/except`
- On exception: call `await set_task_status(task_id, "failed", output=str(e))`
- Never leave a task permanently in `running` state

### Database
- Schema is in `db/schema.sql` — update it when adding columns or tables
- Always add columns with `DEFAULT` values so old rows aren't broken
- Never `DROP` a column without a migration path
- Task status flow: `queued → pending_approval → approved → running ↔ awaiting_children/ready_to_synthesize → done/failed/declined`

---

## How to Add a New Agent

1. Create `agents/<type>.py` with a single async entry point: `run_<type>_task(task, routing)`
2. Follow the step checkpoint pattern:

```python
step1_id = await start_step(task_id, 1, "step_name", {"input": ...})
# ... do work ...
await complete_step(task_id, 1, {"output": ...})
```

3. Wrap everything in try/except and call `set_task_status("failed")` on error
4. Update `services/orchestrator.py` to route the new `task_type` to your agent
5. Add a model key in `services/router.py` if the task needs a specialised model

**For new module types that are simple (run SOP → LLM → return text):**  
Do NOT create a custom agent. Use `agents/generic.py` instead. The generic agent
handles any task type that doesn't need custom tool calls.

---

## How to Add a New Workspace SOP Module

Modules live in `sops/modules/<name>.md`. They are Layer 2 of 3 in the
SOP assembly. The assembler (`services/sop_assembler.py`) injects them
automatically based on `task.module`.

When writing a module SOP:
- Start with: `# PrismaOS SOP Module: <Name>`
- Section: `## Purpose` — one paragraph
- Section: `## Methodology` — numbered steps
- Section: `## Output Format` — exact structure the LLM must produce
- Section: `## Rules & Boundaries` — hard limits (e.g. never claim medical cure)
- Target length: 600–1000 tokens (not more — context is precious)

---

## How to Add a New Workspace Profile

Profiles live in `sops/workspaces/<workspace>/profile.md`. They are Layer 3 of 3.
They define brand voice, target audience, and absolute rules for a specific business.

Always cross-check with `environment.yaml → workspaces` for existing metadata.

---

## Discord Bot Conventions

- All user-facing strings live in `bot/responses.py` — never hardcode strings in `discord_bot.py`
- All text must support both `en` and `lt` (Lithuanian) via the `r(key, lang)` helper
- Embed colours are standardised:
  - `0x95a5a6` Grey — informational / internal
  - `0x3498db` Blue — running / in progress
  - `0x2ecc71` Green — success / done
  - `0xf39c12` Amber — public risk / warning / retry
  - `0xe74c3c` Red — financial risk / declined / error
- Operator-only commands must check `is_operator(interaction.user.name)` at the top
- New slash commands must be registered in the `/setup` command's `structure` dict so they appear in the right channel category

---

## Web UI Conventions

- Templates extend `base.html` via Jinja2 `{% extends %}`
- All badge colours are CSS classes in `style.css` — never use inline Jinja `style="..."` tags
- All routes in `web_ui/app.py` must use `user = require_user(request)` for authentication
- Pass `active_route=request.url.path` to every template so the sidebar highlights correctly
- Mobile responsiveness is mandatory — test with `@media (max-width: 768px)`

---

## What NOT to Do

- ❌ Do not add new Python dependencies without adding them to `requirements.txt`
- ❌ Do not hardcode any credentials, tokens, or passwords — they live in `.env` only
- ❌ Do not bypass the approval system — all tasks must enter the queue
- ❌ Do not run blocking code (file I/O, heavy computation) on the asyncio event loop — use `asyncio.to_thread()`
- ❌ Do not change the database schema without updating `db/schema.sql`
- ❌ Do not add features to `FUTURE_IDEAS.md` scope without a plan — just add them to the backlog

---

## File Ownership Quick Reference

| Directory | What lives here |
|---|---|
| `agents/` | Task runner scripts (one per task type or generic) |
| `bot/` | Discord bot, responses, permissions, embed builders |
| `db/` | SQL schema and migration scripts |
| `deploy/` | Systemd service files |
| `mcp/` | External API integrations (stubs + real) |
| `modules/` | Future: orchestrator sub-modules |
| `scripts/` | One-off maintenance scripts (archiver, health checks) |
| `services/` | Core shared services (queue, router, orchestrator, SOP assembler) |
| `sops/` | All SOP markdown files (system, modules, workspaces) |
| `web_ui/` | FastAPI app, Jinja2 templates, static CSS/JS |
| `workspaces/` | Workspace-specific data or overrides |

---

## Before You Finish Any Work

- [ ] Did you update `environment.yaml` if hardware, models, or workspace config changed?
- [ ] Did you update `db/schema.sql` if you changed the database?
- [ ] Did you add strings to `bot/responses.py` in both `en` and `lt`?
- [ ] Did you add the new dependency to `requirements.txt`?
- [ ] Is all new code async-safe?
- [ ] Does the feature respect the approval tier system (internal / public / financial)?
- [ ] Could this belong in `FUTURE_IDEAS.md` instead of being built right now?
