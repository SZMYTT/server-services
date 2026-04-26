# NNLOS — AI Context

This file is the system context for any AI session working on the NNLOS project.
Read this first. Read PHASES.md and ARCHITECTURE.md before writing any code.

---

## What This Project Is

NNLOS is NNL's procurement and inventory intelligence layer. It mirrors data from MRP Easy (the ERP), runs analytics, and provides tools for the daily procurement workflow. It is being migrated from a Google Sheets + Apps Script system into a proper server-based application.

NNL is a candle/fragrance/homeware brand with physical shops across the UK and wholesale/online channels. The primary user is Daniel (the Inventory Manager). Shop managers interact with the outputs (replenishment lists). Cover staff may use Manual Mode task instructions.

---

## Always Read Before Coding

1. [PHASES.md](PHASES.md) — current phase status and what is/isn't built yet
2. [ARCHITECTURE.md](ARCHITECTURE.md) — system layers, DB schema, integration points
3. `db/schema.sql` — current Postgres table definitions

---

## Hard Rules

### Never do these
- Do not modify MRP Easy data directly. NNLOS reads MRP Easy exports — it never writes back to MRP Easy
- Do not send emails or Slack messages automatically. Always create a draft or staged message for human approval
- Do not assume Google Sheets is the source of truth. Postgres is. Sheets is a display layer
- Do not implement website form-filling or browser automation for purchasing. Removed from scope
- Do not skip the manual fallback. Every automated feature must have a documented manual equivalent

### Python rules (follow systemOS patterns)
- Use `venv` — scripts assume virtual environment. Include `requirements.txt`
- All config via environment variables using `python-dotenv`. No hardcoded secrets
- All scripts have `if __name__ == "__main__":` entry points
- Use `logging` module, not `print()`, for diagnostic output
- All file I/O uses `pathlib.Path`
- Wrap all external calls in `try/except` with logged stack traces
- Follow systemOS import pattern: `from systemOS.services.queue import add_task`

### Database rules
- All NNLOS tables live in the `nnlos` schema within the `systemos` database
- Use upserts (INSERT ... ON CONFLICT DO UPDATE), never truncate + reinsert
- Never run raw string-concatenated SQL. Use parameterised queries

### Documentation rule
- Every new phase feature gets a PHASES.md entry with STATUS, GOAL, BUILDS, DONE_WHEN
- Update phase STATUS when work starts (`in-progress`) and completes (`done`)
- Non-obvious decisions go in ARCHITECTURE.md

---

## Key Domain Knowledge

### Part numbers
- `NNL` prefix = finished products / assemblies
- `A0` prefix = raw materials / components
- Third-party = identified via vendor in `items` table

### Procurement types (on items/vendors)
- `forward_order` — committed quantity months ahead. Tracks calloff dates
- `stock_holding` — supplier holds reserved stock on NNL's behalf
- `email_order` — reactive, ordered by email when stock hits ROP
- `website_order` — reactive, purchased manually from supplier website when stock hits ROP

### ROP (Reorder Point)
Pre-calculated and stored in `items.reorder_point`. Formula: `(avg daily usage × lead time days) + 14 days safety stock`. Do not recalculate — read from items.

### On Radar threshold
Dynamic per item: `(current_available / daily_usage_30d) < (lead_time_days + 14)`. Not a flat 30-day window.

### MRP Easy import limits
- Transfer order CSV import: maximum 100 rows. NNLOS auto-splits at 100 rows
- Part number is extracted from the first word of the description field: `LEFT(desc, SEARCH(" ", desc) - 1)`

### Monday shipment flow (manual steps that stay manual)
NNLOS prepares files and flags problems. The actual MRP Easy steps (create transfer order, drag-drop import, delete customer order, Pick All) remain manual — too many lot/stock edge cases to automate safely.

---

## Phase Status (update when progressing)

See PHASES.md for full detail. Quick reference:

| Phase | Name | Status |
|-------|------|--------|
| 0 | Foundation & Rename | `in-progress` |
| 1 | Data Ingestion | `planned` |
| 2 | Analytics Engine | `planned` |
| 3 | Shop & Shipment Flows | `planned` |
| 4 | Procurement Intelligence | `planned` |
| 5 | AI & Communication | `planned` |
| 6 | Web Dashboard | `planned` |

---

## Google Sheets Reference

The current live system is `NNL_INVENTORY` Google Spreadsheet.
Full column mappings for every sheet are in `CONTEXT.md` (attached to sessions when working on Sheets features).
Protected GAS functions (do not modify): `generateDataSALES`, `generateDataCOMPONENTS`, `syncRawData`, `syncOtherData`, `runWeeklyShopExport`, `processTargetShops`, `appendWeeklySales`, `updateShopStats`, `generateEmailDrafts`.

---

## Folder Structure

```
nnlos/
├── CLAUDE.md           ← you are here
├── PHASES.md           ← phase plan and status
├── ARCHITECTURE.md     ← system design
├── README.md           ← setup and running
├── db/
│   └── schema.sql      ← Postgres table definitions
├── services/           ← NNLOS business logic
│   ├── ingestion.py    ← CSV watcher and Postgres upserts
│   ├── analytics.py    ← sales velocity, component usage, forecasting
│   ├── shop_flow.py    ← Monday replenishment generation
│   ├── shipment.py     ← XLSX parsing and transfer order prep
│   ├── procurement.py  ← procurement wall, PO staging, task engine
│   └── comms.py        ← email drafting, Slack posting
├── agents/             ← AI agents (email, task prioritisation)
├── mcp/                ← tool integrations (Drive, Sheets, Gmail, Slack)
├── web/                ← FastAPI app + templates
├── .env.example
└── requirements.txt
```
