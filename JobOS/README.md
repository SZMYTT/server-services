# SystemOS

**The engine that builds and runs AI systems.**

SystemOS is the core runtime layer. It provides the task queue, orchestrator, scheduler, agent runners, MCP tools, and SOP library that any project can build on top of.

## Projects using SystemOS

| Project | Description | Status |
|---------|-------------|--------|
| [prismaOS](../prismaOS/) | Business AI centre (candles, cars, nursing, property, food brand) | Live |
| [fitOS](../fitOS/) | Fitness & life coach — Android/web, Garmin/Strava | Planned |
| [nnlos](../nnlos/) | NNL procurement & inventory intelligence — local server | Planned |

## Structure

```
systemOS/
├── services/          # Core engine
│   ├── queue.py       # Task queue (PostgreSQL-backed)
│   ├── orchestrator.py
│   ├── scheduler.py
│   ├── checkpointer.py
│   ├── retry.py
│   ├── router.py
│   └── sop_assembler.py
├── agents/            # Base agent implementations
│   ├── generic.py
│   ├── researcher.py
│   ├── content.py
│   └── comms.py
├── mcp/               # Infrastructure MCP tools
│   ├── browser.py     # Playwright scraper
│   └── search.py      # SearXNG search
├── sops/
│   ├── system/        # Core system SOPs
│   └── modules/       # Reusable module SOPs
├── db/
│   └── schema_core.sql
├── config/
├── environment.yaml   # Hardware, inference, models, network
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Integration pattern

Projects import from systemOS by adding the parent directory to PYTHONPATH:

```
PYTHONPATH=/home/szmyt/server-services
```

Then in project code:
```python
from systemOS.services.queue import add_task
from systemOS.services.orchestrator import run_orchestrator
```

## Database

Shared PostgreSQL instance: `systemos` db, port 5433, container `systemos-postgres`.

The core schema (`db/schema_core.sql`) covers: tasks, task_steps, schedules, module_estimates, workspace_analytics, users, audit_log, integration_job_runs.

Each project manages its own ERP/domain tables in separate schema files.

## Running systemd services (prismaOS)

```bash
sudo systemctl restart prisma-web
sudo systemctl restart prisma-orchestrator
sudo systemctl restart prisma-bot
```
