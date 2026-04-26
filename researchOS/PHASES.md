# researchOS — Phase Plan

## Phase 0: Foundation ✅ DONE
**Goal:** Working research agent that can accept a topic and produce a report.

**Done when:**
- `agents/researcher.py` generates queries, runs SearXNG, scrapes pages, synthesises with Claude
- Reports save to `research/` as markdown files
- DB schema created, optional persistence to `supply.research_findings`

---

## Phase 1: Queue and Web UI ✅ DONE
**Goal:** Topic queue + simple web interface to manage research.

**Done when:**
- `services/research.py` manages pending/done/error state in DB
- Web UI on port 4001: submit topics, view status, read reports
- Initial topic queue pre-seeded with 12 NNL-relevant supply chain topics
- SOPs written for 6 key areas (procurement KPIs, reorder points, vendor management, demand forecasting, shop replenishment, automation)

---

## Phase 2: Scheduled Research Worker
**Goal:** Automatically process pending topics on a schedule.

**Builds on:** Phase 1

**Done when:**
- APScheduler worker runs `run_pending()` on a configurable interval
- Systemd unit files created: `supplyo-worker.service`
- Web UI shows last-run time and queue depth
- Failed topics auto-retry once before marking as error

---

## Phase 3: SOP-Driven Deep Research
**Goal:** Run all SOP modules to build a comprehensive NNL knowledge base.

**Builds on:** Phase 2

**Done when:**
- All 6 SOPs run and produce reports
- Web UI shows reports grouped by SOP category
- Each report links to related SOPs for follow-up research
- Reports refresh on a monthly schedule (stale after 30 days)

---

## Phase 4: nnlos Integration
**Goal:** researchOS research informs nnlos decisions.

**Builds on:** Phase 3 + nnlos Phase 2

**Done when:**
- When nnlos detects a new procurement pattern or gap, it queues a researchOS research topic
- researchOS reports can be referenced from nnlos procurement summaries
- Example: nnlos detects unusual lead time → queues "managing lead time variability for [supplier type]"

---

## Phase 5: Report Quality Improvements
**Goal:** Better, more targeted reports.

**Builds on:** Phase 3

**Done when:**
- Reports include a "confidence" note when search results were thin
- Follow-up question suggestions at end of each report
- Topic deduplication: similar topics merged rather than re-researched
- Source quality scoring: prefer authoritative domains over content farms

---

## Phase 6: Vendor Intelligence Scraper ✅ BUILT (2026-04-25)
**Goal:** Scrape specific supplier websites (known URLs + SKUs) to build structured vendor intelligence profiles. Different from research topics — these are targeted deep-dives into NNL's actual supplier sites.

**Builds on:** Phase 1 (web UI), existing MCP browser infrastructure

**Architecture:**
- Server: job queue in `supply.vendor_scrape_jobs`, profiles in `supply.vendor_profiles`
- Mac CLI: `bin/scrape_vendors.py` — Playwright scraper + Claude extraction — runs on Mac (not server)
- Why Mac: JS-heavy supplier sites need a real browser; the Mac (M1 Max) handles it better

**Built:**
- `db/schema_vendor.sql` — two new tables (`vendor_scrape_jobs`, `vendor_profiles`)
- `bin/scrape_vendors.py` — Mac CLI (Playwright multi-page scraper + Claude extraction + server POST)
- Web routes: `GET /vendors`, `POST /vendors/queue`, `GET /vendor/{id}`, `POST /vendor/{id}/delete`
- API: `GET /api/vendor-jobs`, `POST /api/vendor-jobs/{id}/start`, `POST /api/vendor-jobs/{id}/result`
- Templates: `vendors.html` (queue + list), `vendor_detail.html` (full profile view)
- Sidebar: Vendors link added under Intelligence section

**Mac setup:**
```bash
pip install playwright anthropic requests python-dotenv
playwright install chromium
# Set in researchOS .env: VENDOR_API_KEY=<something>
# Set on Mac: ANTHROPIC_API_KEY=..., SUPPLY_API_KEY=..., SUPPLY_SERVER_URL=http://100.119.217.120:4001
```

**Usage:**
```bash
# Queue from web UI at /vendors, then on Mac:
python3 bin/scrape_vendors.py poll

# Or direct:
python3 bin/scrape_vendors.py scrape --url https://carvansons.co.uk --name "Carvansons" --skus A01234,A01235

# Or batch:
python3 bin/scrape_vendors.py batch --file vendors.json
```

**What it extracts:**
- Company type (manufacturer / distributor / wholesaler / dropshipper)
- UK presence, address, contact email
- MOQ, lead times, delivery info, wholesale availability
- Per-SKU: price, price tiers, stock status, specs
- Upstream supplier intelligence, comparable alternatives
- Risk flags (dropshipper signals, no UK address, generic products)

**Done when:**
- Schema migrated: `psql -d systemos -f db/schema_vendor.sql`
- At least one vendor scraped end-to-end and visible at `/vendors`
- Profile shows price tiers and alternatives where available
