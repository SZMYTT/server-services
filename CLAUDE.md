# server-services — Ecosystem Architecture

This file is the top-level AI context for the entire `server-services` ecosystem.
Read this before touching any project in this directory.

---

## Mental model

**OS services are grouped by what they DO, not who they're for.**

NNL is the organisation that uses all of this. NNL's business domains (supply chain, retail, fragrance, website) are contexts that appear *inside* each service — they are not reasons to create separate services.

A new OS is justified when you need a new *capability* (research, procurement operations, content management). A new NNL business domain is just a project/tag inside the right existing OS.

---

## Vocabulary (use these consistently)

| Term | Meaning |
|------|---------|
| **OS service** | A running webapp or background service (`researchOS`, `nnlos`, `prismaOS`). Lives in its own directory, has its own venv, port, DB schema. |
| **module** | A feature within an OS (e.g. Vendor Intelligence inside researchOS). Not a standalone service. |
| **NNL domain** | A business area of NNL: supply chain, retail, fragrance, website. This is a tag/project *inside* a service, not a reason to create a new one. |
| **project (researchOS)** | A research folder inside researchOS for organising topics (e.g. "NNL Supply Chain", "NNL Retail"). |
| **systemOS** | A **code library only** — no UI, no users, no running process. Other services import from it. |

---

## Ecosystem map

```
systemOS/               ← Code library. Shared tools only. Never a UI.
  mcp/browser.py        ← Crawl4AI scraper: scrape(), scrape_many()
  mcp/search.py         ← SearXNG client: run_search()
  llm.py                ← Ollama/Anthropic abstraction: complete()
  config/depth.py       ← Research depth presets: get(), choices()
  services/             ← Task queue, orchestrator, scheduler (for prismaOS)
  agents/               ← Base agent classes

researchOS/  port 4001  ← Research for ALL NNL domains
  Mode 1: Topic research (search → scrape → LLM synthesis → report)
  Mode 2: Vendor Intelligence (agentic scraper — LLM drives tool calls)
  "Projects" inside = NNL business domains, not separate apps

nnlos/                  ← NNL procurement operations (MRP Easy mirror, POs, shop replenishment)
vendorOS/               ← NNL vendor strategy (audit, samples, dependency risk, disposal tracking)
prismaOS/               ← Live multi-business AI (candles, cars, nursing, property, food brand)

fitOS/                  ← (planned) Fitness/life coach — Android/web, Garmin/Strava
contentOS/              ← (future) Website building and editing with LLM
```

---

## Decision guide: where does new work go?

| What you're building | Where it goes |
|---------------------|---------------|
| Research on any NNL topic | researchOS — new project or topic |
| Vendor scraping / profiling | researchOS — Vendor Intelligence module |
| Procurement operations, PO tracking | nnlos |
| Vendor strategy, sample tracking, risk | vendorOS |
| Site generation or LLM content editing | contentOS (create when needed) |
| New NNL business domain to research | New project *inside* researchOS, not a new OS |
| Generic scraping/search/LLM tool | systemOS — add to shared tools |
| Shared data model or task queue feature | systemOS |

**When to create a new OS:** Only when you need a genuinely new capability that doesn't fit any existing service. Not when you have a new NNL domain to work on.

---

## Import pattern (all projects use this)

```python
# In any project's main.py — bootstrap systemOS on the path:
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))           # own project root
sys.path.insert(0, str(Path(__file__).parent.parent))    # server-services/ → systemOS importable

# Then use shared tools anywhere in the project:
from systemOS.mcp.browser import scrape, scrape_many
from systemOS.mcp.search import run_search
from systemOS.llm import complete
from systemOS.config.depth import get as get_depth
```

**What stays project-specific (never moves to systemOS):**
- `db.py` — each project owns its schema and DB connection
- `agents/*.py` — domain-specific prompt logic
- `web/` — FastAPI app, templates, static assets
- `.env` and credentials

---

## Infrastructure

| Resource | Detail |
|----------|--------|
| Postgres | `systemos` database, port 5433, container `systemos-postgres` |
| SearXNG | `http://localhost:8080` — must be running for any research/search |
| Ollama | `http://100.76.139.41:11434` — Mac M1 Max via Tailscale. Model: `gemma4:26b` |
| Anthropic | Via `ANTHROPIC_API_KEY` in each project's `.env` |
| researchOS | Port 4001 |
| prismaOS web | Port 4000, systemd: `prisma-web` |

---

## DB schema ownership

| Schema | Owner |
|--------|-------|
| `supply.*` | researchOS (research topics, findings, vendor jobs, profiles) |
| `nnlos.*` | nnlos |
| `vendor.*` | vendorOS |
| core tables (tasks, users, audit_log) | systemOS — `db/schema_core.sql` |
