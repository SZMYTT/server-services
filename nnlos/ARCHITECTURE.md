# NNLOS — Architecture

**Last updated:** 2026-04-21

---

## What NNLOS Is

NNLOS is the server-side intelligence layer for NNL's procurement and inventory operations. It mirrors data from MRP Easy (the ERP/system of record), runs calculations that are too slow or cell-hungry for Google Sheets, and provides tools for the daily procurement workflow.

It does not replace MRP Easy. It does not replace Google Sheets entirely. It sits between them and makes both faster and smarter.

---

## System Layers

```
┌─────────────────────────────────────────────────────┐
│  MRP Easy (System of Record)                        │
│  CSV exports → Google Drive folder                  │
└────────────────────┬────────────────────────────────┘
                     │ CSV files land in Drive
                     ▼
┌─────────────────────────────────────────────────────┐
│  NNLOS — Ingestion Layer (Phase 1)                  │
│  Watches Drive → parses CSV → upserts to Postgres   │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  NNLOS Postgres DB                                  │
│  raw_movements, items, boms, vendors, POs,          │
│  inventory_snapshot, shop_orders, shipments, ...    │
└──────┬──────────────────────────┬───────────────────┘
       │                          │
       ▼                          ▼
┌──────────────────┐   ┌──────────────────────────────┐
│  Analytics       │   │  Google Sheets (display)     │
│  Engine          │   │  SALES, COMPONENTS,          │
│  (Phase 2)       │   │  PROCUREMENT_WALL pushed     │
│  Writes results  │──▶│  back for verification       │
│  to DB + Sheets  │   └──────────────────────────────┘
└──────┬───────────┘
       │
       ▼
┌─────────────────────────────────────────────────────┐
│  NNLOS Services (Phases 3–5)                        │
│  ├── Shop & Shipment Flows                          │
│  ├── Procurement Intelligence                       │
│  ├── PO Preparation Mode                           │
│  ├── Daily Task Engine                             │
│  └── AI Email Agent                               │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  NNLOS Web UI (Phase 6)                             │
│  Reads same Postgres DB — zero duplication          │
│  FastAPI + frontend, served via Caddy               │
│  Accessible via Cloudflare Tunnel                   │
└─────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Framework | FastAPI |
| Database | PostgreSQL 16 (shared `systemos-postgres` container) |
| Task queue | systemOS queue (PostgreSQL-backed) |
| Scheduling | systemOS scheduler |
| Google Drive | Google Drive API (service account) |
| Google Sheets | Sheets API (write-back for display) |
| Email drafts | Gmail API |
| Slack | Slack Webhooks / Bot API |
| Frontend | HTMX + Jinja2 templates (or React TBD) |
| Serving | Caddy (reverse proxy) |
| Remote access | Cloudflare Tunnel |
| Runtime | Lenovo home server, Docker |

---

## Database Schema (nnlos schema in Postgres)

All NNLOS tables live in the `nnlos` schema within the shared `systemos` database.

### Source data tables (mirrored from MRP Easy)
- `nnlos.raw_movements` — stock movements (MRP Easy: stock_movement CSV)
- `nnlos.items` — master catalogue, all products and components
- `nnlos.boms` — bill of materials / recipes
- `nnlos.vendors` — supplier master data
- `nnlos.purchase_orders` — PO headers
- `nnlos.po_line_items` — PO line items
- `nnlos.inventory_snapshot` — current stock levels per item/site
- `nnlos.shop_orders` — Shopify/retail customer orders
- `nnlos.post_orders` — wholesale/posted customer orders
- `nnlos.criticall` — per-site stock levels vs ROP (MRP Easy: critical_on_hand CSV)

### Calculated/derived tables (written by analytics engine)
- `nnlos.sales_velocity` — 30/60/90/120/180/365 day qty and income per product
- `nnlos.component_usage` — 30/60/90/120/180/365 day usage per component
- `nnlos.production_forecast` — days of stock remaining, shortage date, MFG order date per component
- `nnlos.procurement_wall` — aggregated procurement view per component

### Operational tables (written by NNLOS services)
- `nnlos.shipments` — shipment records (from XLSX import)
- `nnlos.shipment_lines` — individual line items per shipment
- `nnlos.shop_replenishment` — generated per-shop replenishment lists
- `nnlos.discrepancies` — stock discrepancies reported via Slack/email
- `nnlos.po_staging` — staged POs awaiting entry into MRP Easy
- `nnlos.daily_tasks` — prioritised task list (Urgent / This Week / On Radar)
- `nnlos.sync_log` — record of every data sync with status and timestamps

---

## Key Domain Concepts

### Part Number Conventions
- `NNL` prefix — finished products / assemblies
- `A0` prefix — raw materials / components
- Third-party items — identified via vendor association in `items`

### Procurement Types
Stored on each item/vendor. Determines what kind of action is triggered when stock hits ROP:

| Type | Logic |
|------|-------|
| Forward Order | Tracks calloff schedules. Flags if calloff date approaching or delivery overdue |
| Stock Holding | Supplier holds reserved stock. Monitors supplier qty, days remaining, calloff timing |
| Email Order | Stock hits ROP → AI drafts supplier email with PO context. Human approves + sends |
| Website Order | Stock hits ROP → flags for manual purchase. Shows supplier URL, pre-calculated qty |

### ROP Definition
`ROP = (average daily usage × lead time in days) + 2 weeks of safety stock`

This is pre-calculated and stored in `items.reorder_point`. NNLOS reads it directly.

### "On Radar" Threshold
Dynamic per item. An item is On Radar when:
`(current_available_stock / daily_usage_30d) < (lead_time_days + 14)`

i.e. stock will hit ROP within the item's own lead time window. Not a flat 30-day threshold.

### BOM Explosion
Finished product demand × component qty per BOM = component daily demand.
Used in analytics engine (sales velocity → component requirements) and production forecasting.

---

## Integration Points

### Google Drive (in)
- MRP Easy CSV exports land in a specific Drive folder
- NNLOS watches this folder for new files on a schedule
- Same folder structure as the current GAS sync

### Google Sheets (out)
- NNLOS writes calculated results back to specific sheet ranges after analytics run
- Sheets: SALES, COMPONENTS, PROCUREMENT_WALL
- This keeps Sheets as a valid verification display without running any GAS calculations

### Gmail (out)
- AI email agent creates Gmail drafts via Gmail API
- Never sends directly — always draft for human review

### Slack (out)
- Monday replenishment reports posted to configured channels
- Per-shop messages or summary digest
- Discrepancy replies from staff are logged

### MRP Easy (manual bridge)
- No direct API (MRP Easy doesn't have one we can use)
- NNLOS exports clean CSVs formatted for MRP Easy's drag-and-drop import
- PO staging pre-fills data for manual entry into MRP Easy forms

---

## Deployment

```
Lenovo server
├── Docker: systemos-postgres (port 5433)
├── Docker: systemos-caddy  (ports 80/443)
├── systemd: nnlos-web       (FastAPI, port 4000)
├── systemd: nnlos-worker    (background tasks, sync, analytics)
└── Cloudflare Tunnel → nnlos.yourdomain.com
```

NNLOS shares the `systemos-postgres` container. All NNLOS tables are in the `nnlos` schema.

---

## What Stays in Google Sheets

| What | Why |
|------|-----|
| CSV drop zone for MRP Easy exports | Files land in Drive, NNLOS reads from there |
| SALES / COMPONENTS display | NNLOS writes results back — verification display |
| PROCUREMENT_WALL display | Same — NNLOS writes, Sheets displays |
| Shop manager replenishment output | Some managers prefer Sheets — kept as fallback |
| Ad-hoc formula analysis | Sheets is still faster for one-off questions |

## What Moves to NNLOS

| What | Moved to |
|------|---------|
| All GAS calculation functions | NNLOS analytics engine (Phase 2) |
| Manual CSV sync buttons | Automated NNLOS watcher (Phase 1) |
| Shipment XLSX processing | NNLOS shipment flow (Phase 3) |
| Monday replenishment generation | NNLOS shop flow (Phase 3) |
| PROCUREMENT_WALL calculations | NNLOS procurement intelligence (Phase 4) |
| Email drafting | NNLOS AI email agent (Phase 5) |
| Daily task prioritisation | NNLOS task engine (Phase 5) |
| All views and dashboards | NNLOS web UI (Phase 6) |
