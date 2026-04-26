# vendorOS — AI Context

This file is the system context for any AI session working on vendorOS.
Read PHASES.md before touching any file.

---

## What This Project Is

vendorOS is NNL's vendor intelligence and procurement strategy layer. It covers:
- Vendor audit & cleanup (which suppliers are actually active, tagged by category)
- Sample request automation (contact all active suppliers, request samples, track results)
- Single-supplier dependency risk (what happens if a critical supplier fails)
- Lead time verification (are suppliers actually delivering when they say they will)
- Multi-supplier consolidation (rationalise where we buy the same thing from multiple vendors)
- Supplier discovery (scraping to find new/alternative suppliers)
- Disposal item tracking (consumables outside MRP Easy — paper, cleaning, packaging sundries)
- MRP Easy import workflow helpers (reduce friction of bulk imports)

---

## System Position

```
MRP Easy (ERP — source of record)
    ↓ CSV exports to Google Drive
nnlos (Postgres mirror + analytics + procurement wall)
    ↓ vendor/item/PO data
vendorOS (tactical layer — who are our vendors, are they reliable, what's our backup plan)
    ↓ research topics
researchOS (deep research reports)
```

---

## Hard Rules

- **Never send emails.** Create Gmail drafts only. Never auto-send.
- **Never modify MRP Easy data.** Read exports only.
- **Never call nnlos analytics functions directly.** Read from `nnlos` Postgres schema if needed, but don't import nnlos services.
- **No web form filling for purchasing.** Removed from all scopes.
- **Apps Script code lives in `sheets/` as `.gs` files.** It must be copied into the Google Sheets Script Editor manually — there is no automated deployment.

---

## NNL Domain Knowledge

### Key dependencies to treat as highest priority
- **Carvansons** — fragrance supplier. Custom blends. Extremely hard to replace. Single source for most fragrances.
- **Label supplier** — labels are on the critical path for every production run. Lead time often dictates schedule.
- **Glass vessel suppliers** — high MOQs mean switching costs are real. Verify if "two suppliers" are actually the same Chinese factory.

### MRP Easy data schema (relevant columns)
**VENDORS sheet:** A=Vendor No, B=Name, C=Phone, D=Teams, E=Email, F=URL, G=Address, H=On Time %, I=Average Delay, J=Currency, K=Default Lead Time, L=Total Cost, M=Supplier Type, N=Order Notes, O=Payment Period, P=Payment Period Type

**ITEMS sheet:** A=Part No, B=Description, C=Group No, D=Group Name, E=In Stock, N=Reorder Point, P=Cost, X=Lead Time, Y=Vendor No, Z=Vendor Name, AA=Vendor Part No

**PO sheet:** A=PO Number, B=Part No, H=Quantity, T=Status, W=Created, X=Expected Date, Y=Arrival Date, Z=Order ID, AA=Order Date, AF=Delay, AG=Vendor No, AH=Vendor Name

### Part number conventions
- `NNL` prefix = finished products
- `A0` prefix = raw materials / components
- Third-party finished goods = have a vendor assigned but are NOT manufactured in-house (check Group Name)

### Category tags for vendors (Phase 0)
`raw-fragrance`, `raw-wax`, `raw-wick`, `raw-dye`, `raw-ingredient`, `packaging-glass`, `packaging-closure`, `packaging-carton`, `labels`, `third-party-finished`, `sundry`, `services`

---

## Code Conventions

- Python: virtualenv, python-dotenv, pathlib, logging (not print), try/except on all external calls
- Apps Script: all functions in `sheets/` as `.gs` files — single purpose, triggered by menu items
- Scrapers: Playwright for JS-heavy sites, requests+BeautifulSoup for static. Always respect robots.txt
- Reports: markdown files saved to `reports/{category}_{date}.md`
- Never hardcode credentials — `.env` only

---

## Database

- Postgres: `systemos` database, port 5433, `nnlos` schema
- vendorOS reads from `nnlos.*` tables (items, vendors, purchase_orders, raw_movements)
- vendorOS writes to `vendor.*` schema (its own tables — don't pollute nnlos schema)
- Schema: see `db/schema.sql` when created

---

## Phase Status

See PHASES.md for full detail.

| Phase | Name | Status |
|-------|------|--------|
| 0 | Vendor Audit & Cleanup | `planned` |
| 1 | Sample Request Automation | `planned` |
| 2 | Single-Supplier Dependency Strategy | `planned` |
| 3 | Lead Time Verification | `planned` |
| 4 | Multi-Supplier Consolidation | `planned` |
| 5 | Supplier Discovery Scraper | `planned` |
| 6 | Disposal Item Tracking | `planned` |
| 7 | MRP Easy Import Workflow | `planned` |

---

## Related Projects

- **nnlos** — `/home/szmyt/server-services/nnlos/` — MRP Easy data layer, procurement wall, PO staging
- **researchOS** — `/home/szmyt/server-services/researchOS/` — deep supply chain research via SearXNG + Claude
- **systemOS** — `/home/szmyt/server-services/systemOS/` — shared runtime (queue, orchestrator, agents)
