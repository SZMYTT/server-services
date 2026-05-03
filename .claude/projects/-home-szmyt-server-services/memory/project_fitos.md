---
name: Project fitOS
description: HealthOS / fitOS — personal health tracking app, Phase 1 complete
type: project
---

fitOS is the fitness/health OS (CLAUDE.md: `fitOS/` planned slot). It runs on the same Python/FastAPI/Postgres stack as the rest of the ecosystem.

**Phase 1 (complete):** weight logging dashboard at port 4002.
- DB: `health.*` schema in `systemos` Postgres — tables: `metrics`, `workouts`, `checkins`, `goals`
- API: `POST /api/metrics`, `GET /api/metrics/history`
- Frontend: Jinja2 + Chart.js (same forest-green/gold/parchment design tokens)
- Entry: `fitOS/main.py`, venv at `fitOS/venv/`

**Why:** Uses existing Python/Postgres stack. The `health.*` schema sits in the shared `systemos` database so future agent/MCP layers can JOIN against researchOS findings.

**How to apply:** The spec PDF (HealthOS Phase 1 Foundation) uses PHP/MySQL terminology — translate to FastAPI routes and Postgres SQL when extending.

**Next phases:** Activity/workout logging (Phase 2), nutrition/macro tracking (Phase 3), Garmin/Fitbit sync, Android app, MCP agent layer.
