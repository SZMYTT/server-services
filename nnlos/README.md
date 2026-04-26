# NNLOS

**NNL Operating System — Procurement & Inventory Intelligence**

NNLOS is the server-side intelligence layer for NNL's procurement and inventory operations. It mirrors MRP Easy data into Postgres, runs the analytics that were too slow or cell-hungry for Google Sheets, and provides tools for the daily procurement workflow.

**Status:** Phase 0 — scaffold and documentation. See [PHASES.md](PHASES.md) for what's been built.

## Read first

- [PHASES.md](PHASES.md) — what is planned and what is built
- [ARCHITECTURE.md](ARCHITECTURE.md) — how the system works
- [CLAUDE.md](CLAUDE.md) — AI context for future sessions

## Running locally

```bash
# From server-services root
cp nnlos/.env.example nnlos/.env
# Fill in .env values

# Start shared infrastructure (if not already running)
docker compose up -d postgres

# Install dependencies
cd nnlos
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run web service
uvicorn web.app:app --port 4000 --reload

# Run background worker
python -m services.worker
```

## Port

`4000` — routed via Caddy, accessible via Cloudflare Tunnel at `nnlos.[yourdomain]`

## Database

Shared `systemos-postgres` container (port 5433). All NNLOS tables are in the `nnlos` schema.

```bash
psql postgresql://daniel:@localhost:5433/systemos -c "SET search_path TO nnlos;"
```

## Environment variables

See `.env.example` for all required vars. Key ones:

| Var | Purpose |
|-----|---------|
| `DATABASE_URL` | Postgres connection string |
| `GOOGLE_DRIVE_FOLDER_ID` | Drive folder where MRP Easy CSVs land |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Path to GCP service account key |
| `GMAIL_CREDENTIALS_JSON` | Path to Gmail OAuth credentials |
| `SLACK_WEBHOOK_URL` | Slack webhook for Monday reports |
| `ANTHROPIC_API_KEY` | For AI email drafting (Phase 5) |

## Structure

```
nnlos/
├── CLAUDE.md           ← AI context for future sessions
├── PHASES.md           ← phase plan and status
├── ARCHITECTURE.md     ← system design
├── README.md           ← this file
├── db/
│   └── schema.sql
├── services/           ← business logic
├── agents/             ← AI agents
├── mcp/                ← integrations (Drive, Sheets, Gmail, Slack)
├── web/                ← FastAPI app + templates
├── .env.example
└── requirements.txt
```
