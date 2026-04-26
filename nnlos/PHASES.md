# NNLOS — Phase Plan

**Project:** NNLOS (NNL Operating System — Procurement & Inventory Intelligence)
**Migrating from:** NNL_INVENTORY Google Sheets + MRP Easy manual workflows
**Running on:** Lenovo home server, accessible via Cloudflare Tunnel
**Last updated:** 2026-04-21

---

## Phase Status Overview

| Phase | Name | Status |
|-------|------|--------|
| 0 | Foundation & Rename | `done` |
| 1 | Data Ingestion Layer | `in-progress` |
| 2 | Analytics Engine | `planned` |
| 3 | Shop & Shipment Flows | `planned` |
| 4 | Procurement Intelligence | `planned` |
| 5 | AI & Communication | `planned` |
| 6 | Web Dashboard | `planned` |

---

## Phase 0 — Foundation & Rename

**STATUS:** `done`

**Goal:** Rename procureOS → nnlos everywhere, establish the project structure, define the DB schema for NNL's actual domain (not generic procurement).

**Builds:**
- [x] Rename `procureOS/` → `nnlos/` directory
- [x] Update `systemOS/README.md` to reference nnlos
- [x] Rewrite `db/schema.sql` with NNL-specific tables (all source + derived + operational tables)
- [x] `db.py` — Postgres connection utility
- [x] `mcp/drive.py` — Google Drive client (list, download, archive)
- [x] `services/ingestion.py` — Phase 1 CSV ingestion pipeline
- [x] `services/worker.py` — Background scheduler (APScheduler)
- [x] `web/app.py` — FastAPI with dashboard, sync endpoints, health check
- [x] `deploy/nnlos-web.service` + `nnlos-worker.service` — systemd units
- [x] `.env.example` with all vars including Drive folder IDs
- [x] `requirements.txt`, `ARCHITECTURE.md`, `CLAUDE.md`, `PHASES.md`
- [x] No references to `procureOS` remain (historical mentions in Phase 0 text only)

**Note:** NNLOS runs as systemd services (matching prismaOS pattern), not as a Docker container. Shared `systemos-postgres` container on port 5433.

---

## Phase 1 — Data Ingestion Layer

**STATUS:** `in-progress`

**Goal:** Replace the manual MRP Easy CSV download + GAS import buttons with an automated pipeline that keeps NNLOS Postgres as a live mirror of MRP Easy data.

**Replaces:**
- `syncRawData()` GAS function
- `syncOtherData()` GAS function
- Manual 12-CSV download workflow for shop exports
- RAW sheet cell-limit problem (76% of Google Sheets limit)

**Builds:**
- [x] `mcp/drive.py` — Google Drive API client (list files, download, move to archive)
- [x] `services/ingestion.py` — config-driven pipeline. Parsers for all 9 CSV types. Three strategies: `append_dedup` (raw_movements), `upsert` (items/boms/vendors/POs/inventory), `replace` (criticall/shop/post orders)
- [x] `services/worker.py` — APScheduler runs `ingestion.run()` every N minutes (configurable)
- [x] `web/app.py` — `POST /api/sync` and `POST /api/sync/{type}` manual trigger endpoints. `GET /api/sync/status` shows last result per type. `GET /` dashboard page
- [x] `deploy/nnlos-web.service` + `nnlos-worker.service` — systemd units

**Still to wire up:**
- [ ] Set up GCP service account, enable Drive API, share source folder with service account email
- [ ] Copy `.env.example` → `.env`, fill in `GOOGLE_SERVICE_ACCOUNT_JSON` and `NNL_SPREADSHEET_ID`
- [ ] Run `db.init_schema()` to create nnlos schema in Postgres
- [ ] Install systemd services: `sudo cp deploy/*.service /etc/systemd/system/ && sudo systemctl enable nnlos-web nnlos-worker && sudo systemctl start nnlos-web nnlos-worker`
- [ ] Test: drop a stock_movement CSV into the Drive folder, watch `journalctl -u nnlos-worker -f`

**Key decisions:**
- Google Sheets GAS sync buttons can stay and call a NNLOS webhook instead, so the workflow feels identical to now while the data lands in Postgres
- RAW data: all history goes into Postgres. Google Sheets can keep a 13-month rolling window if needed for display, but NNLOS holds everything

**Done when:**
- All MRP Easy CSV types auto-ingest into correct Postgres tables
- Sync runs on schedule without manual intervention
- Data in NNLOS matches data in Sheets after a sync

---

## Phase 2 — Analytics Engine

**STATUS:** `planned`

**Goal:** Run all the calculations that currently happen in GAS and formula sheets (SALES velocity, COMPONENTS usage, CRITICALL logic, CODE/CODE2 chain) inside NNLOS against Postgres. Results are stored in DB and pushed back to Google Sheets so the existing display is unchanged.

**Replaces:**
- `generateDataSALES()` GAS function
- `generateDataCOMPONENTS()` GAS function
- CODE → CODE2 → PROCUREMENT_WALL formula chain (complex, slow, fragile)

**The display question:** Postgres is the calculation layer. Google Sheets stays as a verification display — NNLOS writes results back to the SALES, COMPONENTS, and PROCUREMENT_WALL sheets via the Drive/GAS webhook pattern. You can still see the data in Sheets to check it's correct, but the calculation is no longer happening in Sheets.

**Builds:**
- **Sales velocity calculator** — 30/60/90/120/180/365 day windows, BOM explosion for gift sets/bundles (same logic as `generateDataSALES()` but in Python/SQL, runs in seconds not minutes)
- **Component usage calculator** — mirrors `generateDataCOMPONENTS()` logic
- **CRITICALL engine** — items at/below ROP per site, with open order demand folded in
- **Production requirements** — CODE/CODE2 equivalent: product demand → BOM explosion → component demand totals
- **Results writer** — pushes calculated results back to specific Google Sheets ranges (SALES, COMPONENTS, PROCUREMENT_WALL) so display is unchanged
- **PRODUCTION_FORECAST table** — days of stock remaining per component, shortage dates, recommended MFG order dates (was TASK-007 in the GAS backlog — built properly here)

**Done when:**
- NNLOS analytics match GAS output values (verified side by side)
- SALES and COMPONENTS sheets update from NNLOS, not GAS
- `generateDataSALES()` and `generateDataCOMPONENTS()` are deprecated (kept in GAS as fallback, not called)

---

## Phase 3 — Shop & Shipment Flows

**STATUS:** `planned`

**Goal:** Automate the Monday shop replenishment workflow and the weekly shipment processing workflow. Two distinct sub-flows.

### Sub-flow A: Monday Replenishment

**Replaces:** `runWeeklyShopExport()`, `processTargetShops()`, manual 12-CSV download

**Builds:**
- Single CRITICALL export → NNLOS generates per-shop replenishment lists automatically
- Rules: Products sheet per shop, Third-Party sheet per shop, merge if ≤5 Third-Party items
- Naming: `[SHOP] {site}` / `[SHOP] {site} [3rd Party]` / `[SHOP] Combined [3rd Party combined]`
- CSV export to Drive (same structure as now — MRP Easy upload workflow unchanged)
- **Slack integration** — Monday morning: auto-post the replenishment report (stock levels, ROP, qty being sent) to the correct Slack channels. Format: one message per shop, or a summary digest
- **Email sending** — shops that receive via email instead of Slack
- **Discrepancy tracker** — when staff reply in Slack with corrections against physical stock levels, NNLOS logs the discrepancy. Flags items for manual stock adjustment. Dashboard view of open discrepancies

**Note:** Slack integration is a prototype — built to show the boss, not fully production-hardened yet. Manual fallback: existing CSV export still works if Slack breaks.

### Sub-flow B: Shipment Processing

**Current manual flow (to understand what we're improving):**
1. Customer orders made in MRP Easy → books stock
2. Fulfillment creates shipment → downloads as XLSX/PDF
3. Upload XLSX to Google Sheets
4. Review against paper copy (tick = correct, cross = stock wrong) → delete/edit rows
5. Split 1 / Split 2 sheets → paste filtered list → formula `=LEFT(A2, SEARCH(" ", A2) - 1)` extracts part numbers → split into ≤100 row chunks (MRP Easy import limit)
6. Download each chunk as CSV
7. In MRP Easy: open customer order → create transfer order → Import button → drag/drop file popup → map columns (uncheck Lot, set last column as Part No) → scroll to Import → submit
8. Delete customer order (releases booked stock)
9. Set From / To / Date on transfer order
10. "Pick All" → fails if any items are "planned" (not yet made) → must manually free lot allocations

**Problems:** Stock changes during the slow process, lot numbers wrong, allocated lots don't have enough qty, whole thing takes a long time

**Builds:**
- **XLSX parser** — accepts shipment XLSX upload, parses into a structured list (replaces Google Sheets import step)
- **Review interface** — simple web view showing parsed shipment lines. You mark items as wrong/correct, edit quantities, delete rows. Equivalent to the paper-copy review step but faster and on screen
- **Auto-split** — NNLOS automatically splits the reviewed list at 100 rows, extracts part numbers correctly, labels chunks (Split 1, Split 2...)
- **CSV export** — downloads each chunk formatted for MRP Easy drag/drop import (the manual drag/drop step remains — NNLOS just makes the file correct)
- **Lot conflict flagging** — before you start the MRP Easy import, NNLOS checks current stock levels and flags any items where the lot allocation may have changed since the shipment was created
- **"Planned" item alert** — flags items that are still "planned" (not yet manufactured) so you know before you hit "Pick All" that it will fail

**What stays manual (intentionally):** The actual MRP Easy transfer order creation, drag/drop import, and Pick All steps. Too many edge cases (lot issues, stock changes) to automate safely. NNLOS prepares the data and flags problems; you execute in MRP Easy.

**Done when:**
- XLSX upload → review → split → CSV export replaces the Google Sheets steps
- Monday Slack reports send automatically
- Discrepancy log captures replies

---

## Phase 4 — Procurement Intelligence

**STATUS:** `planned`

**Goal:** Replace the manual PROCUREMENT_WALL review with an intelligent procurement preparation system. Key use case: prepare everything at home the evening before a buying day, so when you arrive everything is staged and ready.

**Replaces:** Manual PROCUREMENT_WALL review process. Extends/improves on the existing Sheets wall.

**Builds:**

### Procurement Wall (improved)
- All the data currently on PROCUREMENT_WALL but split into tabs/sections by procurement type:
  - **Forward Order** — tracks calloff schedules, flags if a calloff date is approaching or overdue, checks if expected delivery is on track
  - **Stock Holding** — how much the supplier holds on your behalf, days of stock remaining at supplier, alert when threshold approaching
  - **Email Order** — stock at ROP, triggers to Phase 5 AI email draft
  - **Website Order** — stock at ROP, shows supplier URL + pre-calculated quantity, flagged for manual purchase
- Sortable by vendor (matching your current workflow — you've learned to navigate by vendor)
- Colour-coded urgency: red = order now, amber = this week, grey = on radar

### PO Preparation Mode ("Evening Before" workflow)
- Run this at home the evening before a procurement day, or the Monday before a busy week
- NNLOS generates a **"Today's Buying List"**: all items that need ordering, grouped by supplier, with quantities and costs
- Each PO is marked with a status: `new` (not yet created in MRP Easy), `staged` (created in MRP Easy, not yet ordered with supplier), `ordered`
- Custom notes field per PO — you can annotate which ones still need action ("waiting on quote", "check stock first", etc.)
- The list stays visible when you come in the next morning — nothing is lost, statuses persist

### PO Staging
- For each item needing a PO: NNLOS pre-fills all MRP Easy PO form fields (vendor, part number, qty, unit cost, expected date, currency, notes)
- You review the staged PO → copy the values into MRP Easy manually
- Removes the lookup/calculation work; you just confirm and paste

**What was removed:** Website form pre-filling / browser automation for purchasing. Removed at your request — too risky for work environment.

**Done when:**
- Procurement wall loads split by type with correct urgency colours
- "Today's Buying List" generates correctly
- PO statuses persist between sessions (new → staged → ordered)

---

## Phase 5 — AI & Communication

**STATUS:** `planned`

**Goal:** AI-assisted supplier communication and daily task prioritisation. Everything automated must have a plain manual equivalent — if the AI breaks, the work still gets done.

**Builds:**

### Supplier Chase Email Agent
- Reads PO data: order number, items, quantities, expected dates, vendor contact, delay info
- **Templates** (multiple variants per scenario):
  - Delivery due today — is it on route?
  - Didn't arrive yesterday — what's the update?
  - Arrived but wrong/incomplete — 2-3 variants
  - Generic update request
- AI personalises each email with the specific PO context — not just mail merge
- Output: Gmail draft, ready to review and send. AI never sends without your approval
- Manual fallback: templates are plain text files you can fill in manually if the AI agent is down

### Daily Task Engine
- **Urgent** — stock out, overdue delivery, missed calloff
- **This Week** — items approaching ROP, calloffs due within 7 days, POs approaching expected date
- **On Radar** — items where projected stock will hit ROP within `lead_time_days` of the item (dynamic per item, not a flat 30 days — your ROP already accounts for 2 weeks + lead time, so On Radar = "you need to act soon or the ROP buffer disappears")
- **Two modes:**
  - *Manual mode* — plain English, step-by-step. "Go to [supplier URL], order [qty] of [part], here's the email to send." Designed for cover staff or non-technical users. Works without any AI
  - *AI mode* — automated: drafts emails, pre-stages POs, surfaces only the decisions that need a human. Designed for your daily use

**Fallback principle (applies to all automation in this phase):**
Every automated action has a documented manual equivalent. The system works without the AI layer. Automated features are clearly labelled so anyone can understand and replace them if broken.

**Done when:**
- Daily task list generates with correct priority bands
- Chase email drafts appear in Gmail
- Manual mode produces plain-text instructions without AI calls
- AI mode works end-to-end for at least Email Order procurement type

---

## Phase 6 — Web Dashboard

**STATUS:** `planned`

**Goal:** A local web UI that reads from NNLOS Postgres (same DB as phases 1–5 — zero duplication). Replaces Google Sheets as the primary interface for procurement work. Sheets stays as fallback and CSV terminal.

**No duplication:** Phases 1–5 write everything to Postgres. The web UI is just a different renderer of the same data. Nothing is calculated twice.

**Setup:** Runs on Lenovo server, accessible via Cloudflare Tunnel. One screen: NNLOS dashboard. Other screen: MRP Easy. Background tabs: Gmail, supplier sites.

**Key views:**
- **Daily Task Dashboard** — landing page, the Phase 5 task engine output
- **Procurement Wall** — replaces the PROCUREMENT_WALL sheet. Live, sortable by vendor, split by procurement type
- **PO Tracker** — open POs with status, expected dates, delay flag, one-click chase email
- **Shop Replenishment** — per-shop view with export/approve actions
- **Production Forecast** — days of stock remaining per component, shortage timeline
- **Supplier Intelligence** — reliability scores, lead time trends, contact details
- **Inventory Analytics** — replaces EVAL-DASH and TOP 50 SALES

**Google Sheets after Phase 6:**
- CSV drop zone for MRP Easy exports (unchanged)
- Backup verification view (SALES, COMPONENTS still updated by NNLOS)
- Shop manager output (some prefer Sheets — keep as fallback)

**Deployment:** FastAPI backend + lightweight frontend (React or HTMX). Caddy routes `/nnlos` subdomain. DB backed up to cloud storage on a schedule.

**Done when:**
- All Phase 1–5 data is viewable in the web UI
- Procurement Wall and Daily Tasks are usable as primary interface
- Google Sheets is demoted to fallback/export role

---

## Principles (applies across all phases)

1. **MRP Easy is the system of record.** NNLOS mirrors and augments; it never replaces MRP Easy data.
2. **Manual fallback for everything.** If automation breaks, the work still gets done with documented manual steps.
3. **No duplication.** Postgres is the single calculation layer. Google Sheets and the web UI are both renderers of the same data.
4. **Human in the loop.** AI prepares; human approves. Emails, POs, and orders never send automatically.
5. **Visible automation.** Any script that runs locally should show its progress. Nothing happens silently in the background without a way to check what it did.
6. **Document everything.** Each phase has a PHASES.md entry. Each feature has inline docs. New AI context goes in CLAUDE.md.
