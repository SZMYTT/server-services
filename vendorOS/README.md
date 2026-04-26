# vendorOS

Vendor intelligence, procurement strategy, and supplier operations layer for NNL.

This project sits between the raw MRP Easy data (mirrored by nnlos) and the daily procurement decisions. It handles everything that nnlos doesn't: vendor auditing, sample tracking, dependency risk, supplier discovery, and disposal management.

## What this is NOT

- Not a replacement for nnlos (which handles MRP Easy ingestion, procurement wall, PO staging)
- Not a replacement for researchOS (which handles deep supply chain research)
- Not a replacement for MRP Easy (source of record for all vendor/item data)

## What this IS

- The tactical layer: who are our suppliers, are they any good, what happens if they fail us
- The sample tracking system: contact every active vendor, get samples, evaluate them
- The scraping layer: find new suppliers, compare pricing tiers, build prospect lists
- The disposal tracker: consumables that fall outside MRP Easy (paper, pens, cleaning supplies)
- The Google Sheets automation layer: Apps Script for sample emails, vendor reports, disposal checks

## Phases

See [PHASES.md](PHASES.md).

## How it fits into the stack

```
MRP Easy (ERP source of record)
    ↓ CSV exports
nnlos (ingestion, analytics, procurement wall, PO staging)
    ↓ vendor / item data
vendorOS (vendor audit, sample tracking, dependency risk, supplier scouting)
    ↓ research topics
researchOS (deep research reports)
```

## Data sources

| Source | How accessed | What we use |
|--------|-------------|-------------|
| MRP Easy VENDORS export | Google Drive CSV (via nnlos) | Vendor list, contact info, lead times, on-time performance |
| MRP Easy ITEMS export | Google Drive CSV (via nnlos) | Part numbers, vendor assignments, lead times, ROPs |
| MRP Easy PO export | Google Drive CSV (via nnlos) | Actual arrival dates vs expected (lead time verification) |
| Supplier websites | Python scrapers (`scrapers/`) | Price tiers, MOQs, delivery info |
| Google Sheets (NNL_INVENTORY) | Apps Script + Drive API | Current live data display + email automation |

## Structure

```
vendorOS/
├── sheets/          # Google Apps Script (.gs files) for NNL_INVENTORY
├── scrapers/        # Python scrapers for supplier websites
├── services/        # Backend logic (vendor scoring, dependency analysis)
├── reports/         # Generated markdown/CSV reports
├── PHASES.md        # Development phases and status
├── CLAUDE.md        # AI context
├── README.md        # This file
├── .env.example
└── requirements.txt
```

## Quick start

```bash
cd /home/szmyt/server-services/vendorOS
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set ANTHROPIC_API_KEY and DATABASE_URL
```

## Prerequisites

- nnlos Phase 1 complete (VENDORS, ITEMS, PO data in Postgres `nnlos` schema)
- Python 3.11+
- Playwright for scrapers: `playwright install chromium`
- SearXNG on port 8080 (already running)
- PostgreSQL on port 5433 (shared systemos DB)
