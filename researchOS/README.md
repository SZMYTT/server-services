# researchOS

Supply chain and procurement research assistant for NNL. Takes a topic, runs web searches, scrapes sources, and synthesises a structured report using Claude.

## Prerequisites

- Python 3.11+
- SearXNG running on port 8080 (already set up on this server)
- PostgreSQL running on port 5433 (shared `systemos` database)
- Anthropic API key

## Setup

```bash
cd /home/szmyt/server-services/researchOS

# Create virtualenv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY

# Init database schema
python3 -c "from db import init_schema; init_schema()"

# Seed initial research topics
python3 -c "from services.research import seed_initial_topics; seed_initial_topics()"
```

## Usage

### Research a topic directly

```bash
source venv/bin/activate
python3 agents/researcher.py "supplier lead time management for fragrance manufacturers"
```

### Queue a topic and run it

```bash
python3 services/research.py "reorder point calculation methods for seasonal products"
```

### Run the web UI

```bash
uvicorn web.app:app --host 0.0.0.0 --port 4001
# Open http://localhost:4001
```

### Process all pending topics

```bash
python3 -c "
import asyncio
from services.research import run_pending
asyncio.run(run_pending())
"
```

## SOPs

Pre-written research guides are in `sops/`. Each defines a topic and a structured prompt hint. Run a SOP like this:

```bash
python3 - <<'EOF'
import asyncio
from sops.procurement_kpis import TOPIC, HINT
from agents.researcher import research
result = asyncio.run(research(TOPIC, sop_hint=HINT))
print(f"Saved: {result['output_file']}")
EOF
```

Available SOPs:
- `sops/procurement_kpis.py` — 10 KPIs Daniel should track, dashboard design
- `sops/reorder_points.py` — ROP and safety stock formulas with worked examples
- `sops/vendor_management.py` — supplier scorecards and performance tracking
- `sops/demand_forecasting.py` — forecasting methods for seasonal products
- `sops/shop_replenishment.py` — Monday replenishment cycle optimisation
- `sops/automation_opportunities.py` — AI/automation opportunities for procurement

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Claude API key |
| `DATABASE_URL` | required | PostgreSQL connection string |
| `SEARXNG_URL` | `http://localhost:8080` | SearXNG instance |
| `PORT` | `4001` | Web UI port |
| `RESEARCH_OUTPUT_DIR` | `research` | Where .md files are saved |
| `QUERIES_PER_TOPIC` | `4` | Search queries generated per topic |
| `SEARCH_RESULTS_PER_QUERY` | `5` | Results fetched per query |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## Structure

```
researchOS/
├── agents/
│   └── researcher.py       # Core research agent
├── mcp/
│   ├── search.py           # SearXNG client
│   └── browser.py          # Page scraper
├── services/
│   └── research.py         # Queue management
├── sops/                   # Pre-written research SOPs
├── web/
│   └── app.py              # FastAPI web UI (port 4001)
├── db/
│   └── schema.sql          # supply.* schema
├── db.py                   # DB connection helper
├── research/               # Generated reports (created at runtime)
├── PHASES.md               # Development phases
├── CLAUDE.md               # AI context
└── README.md
```
