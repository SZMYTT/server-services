# vendorOS — Phase Plan

**Project:** vendorOS — NNL Vendor Intelligence & Procurement Strategy
**Primary user:** Daniel (Inventory Manager, NNL)
**Works alongside:** nnlos (data layer), researchOS (research layer)
**Last updated:** 2026-04-25

---

## Phase Status Overview

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

## Phase 0 — Vendor Audit & Cleanup

**STATUS:** `planned`

**Goal:** Get a clean, categorised vendor list as the foundation for everything else. Purge vendors that haven't been used in 12+ months and have no open POs. Tag every remaining vendor with a category so later phases can work by supplier type.

**Inputs required:**
- MRP Easy VENDORS export (current)
- MRP Easy ITEMS export (to map which items belong to which vendor)
- MRP Easy PO export (to find last order date per vendor)

**Builds:**

### Google Sheets: VENDOR_AUDIT tab
New tab in NNL_INVENTORY spreadsheet. Columns:
- `Vendor No` / `Vendor Name` / `Category` / `Email` / `URL`
- `Last PO Date` — pulled from PO sheet: max order date per vendor
- `Active Items` — count of ITEMS assigned to this vendor
- `Status` — Active / Inactive / Purge
- `Notes`

### Categorisation groups
Tag every vendor into one of these groups (add to VENDORS sheet, column after Notes):
| Category | Examples |
|----------|---------|
| `raw-fragrance` | Fragrance oil suppliers, aroma compounds |
| `raw-wax` | Wax suppliers |
| `raw-wick` | Wick suppliers |
| `raw-dye` | Colour/dye suppliers |
| `raw-ingredient` | All other raw materials |
| `packaging-glass` | Glass vessels (jars, bottles, votives) |
| `packaging-closure` | Lids, caps, pumps, corks |
| `packaging-carton` | Boxes, cartonage, gift packaging |
| `labels` | Label printers (Carvansons etc.) |
| `third-party-finished` | Third-party finished goods (not manufactured in-house) |
| `sundry` | Tape, bags, tissue, tissue, misc |
| `services` | Fulfilment, print, contract work |

### Apps Script: `vendorAudit.gs`
- `generateVendorAudit()` — reads VENDORS, ITEMS, PO sheets. Calculates last PO date and active item count per vendor. Writes to VENDOR_AUDIT tab
- `flagInactiveVendors()` — marks vendors with no PO in last 365 days AND no active items as `Purge`

**What to do with Purge vendors:**
- Do NOT delete from MRP Easy immediately — export to a `VENDORS_ARCHIVE` tab first
- Review manually: some may be dormant but needed for seasonal items
- Delete from MRP Easy only after 2 weeks of review

**Done when:**
- Every active vendor has a category tag
- Inactive vendors are flagged and reviewed
- VENDOR_AUDIT tab generates in one click

---

## Phase 1 — Sample Request Automation

**STATUS:** `planned`

**Goal:** Contact every active supplier and request samples of the products we currently buy from them. This does two things: (1) confirms we know exactly what we're getting from each supplier, (2) starts identifying whether backup suppliers would actually deliver the same product.

**The problem this solves:** Some backup suppliers offer "the same product" but it arrives different. We need samples before we commit to using them as alternatives.

**Inputs required:**
- Phase 0 complete (clean vendor list with categories)
- ITEMS export: which parts are assigned to which vendor

**Builds:**

### Google Sheets: SAMPLE_REQUESTS tab
Columns:
- `Vendor No` / `Vendor Name` / `Vendor Email` / `Category`
- `Part No` / `Part Description`
- `Priority` — calculated: single-source items are HIGH, multi-source are MEDIUM
- `Email Drafted` — Y/N
- `Email Sent Date`
- `Response Received` — Y/N, date
- `Sample Received` — Y/N, date
- `Evaluation Status` — Pending / Matches Current / Different / Acceptable Replacement / Reject
- `Evaluation Notes` — free text (texture, scent throw, dimensions, etc.)
- `Outcome` — Approved Backup / Not Suitable / Primary Confirmed

### Apps Script: `sampleRequests.gs`
`generateSampleRequestEmails()`:
- Reads SAMPLE_REQUESTS tab, finds rows where `Email Drafted = N`
- For each row: creates a Gmail draft using the template below
- Marks `Email Drafted = Y` after draft created
- Never sends automatically — you review and send each draft

**Email template (one per vendor, listing all products in one email, not one per item):**
```
Subject: Sample Request — [Vendor Name]

Hi [Contact Name / "there" if unknown],

I hope you're well. We're currently reviewing our supplier portfolio at NNL 
and would like to request samples of the following products we source from you:

[List of Part Description + Vendor SKU for each item]

Could you please send samples to our warehouse address below? We'd also appreciate 
any current product specification sheets if available.

[NNL Warehouse Address]

Many thanks,
Daniel
NNL
```

**Single-supplier items — additional note in email:**
For parts with only one vendor assigned in ITEMS, add to the email:
> "We're also exploring backup sourcing options for this product category. 
> If you're able to suggest any comparable alternatives or have additional product lines, 
> we'd be grateful to know."

**Done when:**
- SAMPLE_REQUESTS tab auto-populates from VENDORS + ITEMS in one click
- Gmail drafts generated for all active vendors
- Evaluation tracking columns update as samples arrive
- You can filter by `Evaluation Status = Different` to identify substitution risks

---

## Phase 2 — Single-Supplier Dependency Strategy

**STATUS:** `planned`

**Goal:** For every item that has only one supplier, build a documented strategy so we're not caught out if that supplier fails, raises prices, or goes out of business. Some items are fine with no backup (low risk). Others are critical and need a plan.

**The two critical categories to prioritise:**
- **Carvansons (fragrances)** — extremely high dependency, custom blends, hard to replicate
- **Label supplier** — everything depends on labels being correct; lead time is often on the critical path

**Inputs required:**
- Phase 0 complete
- Phase 1 sample evaluations started (to know if prospective backups are actually viable)
- researchOS research: "fragrance supplier alternatives UK" (queue these topics in researchOS)

**Builds:**

### Google Sheets: DEPENDENCY_RISK tab
Columns:
- `Part No` / `Part Description` / `Category` / `Current Vendor` / `Current Vendor Lead Time`
- `Supplier Count` — number of vendors assigned in ITEMS (1 = single source)
- `30D Usage` — from COMPONENTS/SALES velocity data
- `Risk Score` — calculated: `(usage × lead_time × single_source_flag) / ROP` — higher = more critical
- `Risk Band` — Critical / High / Medium / Low
- `Strategy` — one of: `Find Alternative` / `Stock Holding Agreement` / `Strategic Safety Stock` / `Acceptable Risk`
- `Alternative Found` — Y/N (linked to Phase 5 scraping results)
- `Alternative Vendor` / `Alternative Part No` / `Alternative Lead Time`
- `Strategy Notes` — specific plan text

### Risk Band definitions
| Band | Criteria |
|------|---------|
| Critical | Single source + 30D usage > 200 units + lead time > 14 days |
| High | Single source + 30D usage > 50 units OR lead time > 21 days |
| Medium | Single source + low usage OR multi-source but both from same region |
| Low | Multi-source, at least one backup confirmed via samples |

### Strategy playbook per category

**Labels (Critical)**
- Immediately request samples from 2-3 alternative UK label printers
- Spec sheet needed: material, adhesive type, finish, dimensions for each SKU
- Stock holding: negotiate 4-week label buffer held at supplier
- Emergency plan: digital-print labels in-house (specify printer/paper requirements)

**Carvansons / Fragrance (Critical)**
- Document exact IFRA grade and blend spec for each fragrance in MRP Easy notes
- Request CAS numbers and raw material breakdown from Carvansons
- Research: Fragrance houses that can replicate or source comparable blends (researchOS topic)
- Safety stock: maintain minimum 8-week stock for top 10 fragrances by usage
- Identify: which fragrances are Carvansons exclusives vs standard industry formulas

**Packaging — glass (High)**
- Suppliers often share the same Chinese manufacturers — identify if your "two suppliers" both source from the same factory
- MOQ considerations: alternative supplier may have different MOQs — check compatibility with production runs

**Done when:**
- All single-source items have a Risk Band
- All Critical items have a written Strategy + named responsible action
- Action items are trackable (date assigned, target date, status)

---

## Phase 3 — Lead Time Verification

**STATUS:** `planned`

**Goal:** Find out which suppliers are consistently late vs on-time. MRP Easy tracks this (On Time %, Average Delay columns in VENDORS) but we don't use it. This phase surfaces that data and adds a rolling trend so we know if things are getting worse.

**The operational problem:** If a supplier's actual lead time is 21 days but we're planning on 14, we'll run out. The ROP calculation depends on accurate lead times. Wrong lead times = wrong ROPs = stockouts.

**Inputs required:**
- MRP Easy PO export with actual arrival dates and expected dates (columns X and Y in PO sheet)
- MRP Easy VENDORS export (On Time %, Average Delay columns H and I)

**Builds:**

### Services: `services/lead_time.py`
```
analyse_lead_times(po_data, vendor_data) → dict
```
For each vendor:
- Count POs in last 90 days
- Calculate: mean actual lead time, stated lead time (from VENDORS), delta
- Flag: consistently_late (actual > stated + 2 days on 50%+ of POs)
- Flag: improving / worsening trend (compare 0-45 days vs 45-90 days)

### Google Sheets: VENDOR_PERFORMANCE tab
Columns:
- `Vendor No` / `Vendor Name` / `Category`
- `Stated Lead Time` — from VENDORS sheet
- `Actual Lead Time (90D avg)` — calculated from PO data
- `Delta` — actual minus stated (positive = later than promised)
- `On Time %` — from MRP Easy VENDORS (or recalculated)
- `POs analysed` — sample size (don't trust a score based on 1 PO)
- `Trend` — Improving / Stable / Worsening
- `Recommended Lead Time Adjustment` — if delta > 3 days: "Update MRP Easy lead time to X"
- `Last Updated`

### Apps Script: `vendorPerformance.gs`
`generateVendorPerformance()`:
- Reads PO sheet, calculates per-vendor lead time stats
- Writes to VENDOR_PERFORMANCE tab
- Highlights rows where Delta > 3 (supplier materially late)
- Shows a summary: "X vendors have understated lead times — review recommended"

**Done when:**
- Lead time delta calculated for all vendors with 3+ POs in last 90 days
- Vendors with understated lead times flagged with recommended update values
- Script runs in one click and updates in under 30 seconds

---

## Phase 4 — Multi-Supplier Consolidation

**STATUS:** `planned`

**Goal:** Identify items where we're buying from multiple suppliers and decide: consolidate to one (better price/relationship) or maintain spread (risk mitigation). For those we consolidate, chase the better deal actively.

**Inputs required:**
- Phase 0 complete (categorised vendors)
- Phase 1 sample evaluations (to know if alternatives are actually equivalent)
- Phase 3 lead time data (reliability comparison)

**Builds:**

### Google Sheets: MULTI_SUPPLIER tab
Columns:
- `Part No` / `Part Description` / `Category`
- `Vendor 1 Name` / `Vendor 1 Price` / `Vendor 1 Lead Time` / `Vendor 1 On-Time %`
- `Vendor 2 Name` / `Vendor 2 Price` / `Vendor 2 Lead Time` / `Vendor 2 On-Time %`
- `Vendor 3 Name` / `Vendor 3 Price` / `Vendor 3 Lead Time` / `Vendor 3 On-Time %` (if any)
- `Recommended Vendor` — calculated: lowest total cost × best reliability
- `Price Difference` — best vs worst vendor price as %
- `Decision` — Consolidate / Maintain Spread / Negotiate
- `Action` — free text: "negotiate with V2 to match V1 price", etc.
- `Annual Spend` — estimated from PO history (30D usage × cost × 12)

### Decision rules
| Situation | Decision |
|-----------|---------|
| All vendors confirmed equivalent (samples passed) + price diff > 10% | Consolidate to cheapest |
| All vendors confirmed equivalent + price diff < 5% | Maintain spread (risk hedge) |
| Vendors NOT confirmed equivalent | Keep buying from current primary, chase sample from alternative |
| One vendor significantly faster lead time | Factor into decision — faster may be worth paying more for |
| Both from same manufacturer/region | Treat as effectively single-source for risk purposes |

**Done when:**
- All multi-vendor items have a Recommended Vendor with reasoning
- Items worth negotiating are flagged with a target price (V1 price, ask V2 to match)

---

## Phase 5 — Supplier Discovery Scraper

**STATUS:** `planned`

**Goal:** Find new suppliers for each product category. Specifically: identify alternatives for single-source critical items, find lower-cost options for consolidated buys, and build a live prospect list we can revisit quarterly.

**Architecture:** Python scraper (runs on Mac or Lenovo server). Uses Playwright for JS-heavy sites, requests/BeautifulSoup for static sites, Claude for AI analysis of scraped content.

**Inputs:**
- Phase 0 category list (what product types to search for)
- Phase 2 critical dependency list (what to prioritise)
- SearXNG on port 8080 for initial discovery

**Builds:**

### `scrapers/supplier_scout.py`
```
search_category(category: str, region: str = "UK") → list[SupplierProspect]
```
Workflow per category:
1. Generate 3-5 search queries via Claude (`"{category} supplier UK wholesale"`, `"buy {category} bulk UK B2B"`, etc.)
2. Search via SearXNG, collect top 10 URLs per query (deduplicated)
3. For each URL: scrape the page with Playwright
4. Pass scraped content to Claude for structured extraction:

**Claude extraction prompt per scraped page:**
```
Extract supplier intelligence from this webpage. Output JSON:
{
  "company_name": "",
  "url": "",
  "is_wholesaler": true/false,
  "is_dropshipper": true/false,  // obvious signals: "reseller", "no stock held", generic product images
  "appears_to_be_manufacturer": true/false,
  "uk_based": true/false,
  "products": ["list of relevant products"],
  "min_order_value": "",
  "min_order_qty": "",
  "lead_time": "",
  "price_tiers": [{"qty": X, "price": Y}],  // if visible
  "delivery_info": "",
  "contact_email": "",
  "potential_upstream_supplier": "",  // if they mention their own source
  "comparable_alternatives": [],  // other companies mentioned
  "confidence": "high/medium/low",
  "notes": ""
}
```

### `scrapers/scrape_report.py`
- Generates a markdown report per category: `reports/scout_{category}_{date}.md`
- Sections: Overview, Top Prospects, Price Comparison, Risks, Recommended Next Steps
- Highlights: any supplier that might be upstream of a current supplier (potential direct-buy)

### Google Sheets: SUPPLIER_PROSPECTS tab
Columns:
- `Category` / `Company Name` / `URL` / `Is Wholesaler` / `Is Dropshipper` / `UK Based`
- `Products` / `MOQ Value` / `MOQ Qty` / `Lead Time` / `Price Tier Info`
- `Contact Email` / `Potential Upstream`
- `Status` — Prospect / Sample Requested / Evaluated / Rejected / Approved
- `Date Found` / `Date of Last Update`
- `Score` — AI confidence score (1-10): based on UK presence, non-dropshipper, has actual pricing, has MOQ info

**How to run:**
```bash
source venv/bin/activate
python3 scrapers/supplier_scout.py --category "fragrance oil" --limit 20
python3 scrapers/supplier_scout.py --category "glass jar" --limit 20
# etc.
```

**Done when:**
- Scraper runs for all Phase 0 categories without crashing
- Output is a populated SUPPLIER_PROSPECTS tab + markdown reports per category
- Reports identify at least 3 credible alternatives for each Critical dependency item from Phase 2

---

## Phase 6 — Disposal Item Tracking

**STATUS:** `planned`

**Goal:** Track consumables that fall outside MRP Easy (printer paper, pens, cleaning supplies, tape, packaging sundries). These don't have part numbers but running out of them is a real problem. Build a system to check regularly, forecast reorder points, and never be caught short.

**The core problem:** These items vary in usage based on order volume, season, and activity level. A simple "check every 2 weeks" isn't enough — you need to know approximately how much you're using so you can buy in bulk at the right time.

**Builds:**

### Google Sheets: DISPOSALS tab
Columns:
- `Item Name` — plain English (e.g. "A4 Printer Paper - White 80gsm")
- `Category` — Office / Cleaning / Packaging / Warehouse / Kitchen
- `Current Stock Level` — manual entry: count at last check (units or approx units)
- `Unit` — Reams / Boxes / Bottles / Rolls / Items
- `Last Checked Date` — updated on each check
- `Check Frequency` — Weekly / Fortnightly / Monthly
- `Reorder Trigger` — level at which to reorder (e.g. last 2 reams = reorder)
- `Reorder Qty` — standard qty to order each time
- `Avg Usage Per Week` — manual estimate initially, can be calculated over time
- `Weeks of Stock Remaining` — = current stock / avg weekly usage
- `Next Check Due` — = last checked + check frequency
- `Overdue?` — flag: today > next check due
- `Primary Supplier` / `Primary Supplier URL` / `Primary Price`
- `Backup Supplier` / `Backup Supplier URL` / `Backup Price`
- `In Bulk?` — Y/N: are we buying this in bulk already?
- `Bulk Saving` — estimated % saving vs ad-hoc buying
- `Notes`

### Starting inventory list to track
| Category | Items |
|---------|-------|
| Office | A4 white paper, A4 colour paper, pens (black/blue/red), staples, stapler, scissors, tape (clear), envelopes, sticky notes, folders/binders |
| Printing | Label roll stock (Dymo / Zebra compatible), ink cartridges, toner cartridges |
| Packaging warehouse | Packing tape, tissue paper, void fill, bubble wrap, cardboard boxes (various sizes) |
| Cleaning | General surface spray, floor cleaner, bin bags (various sizes), cloths/mops, hand soap, washing-up liquid, paper towels |
| Kitchen | Coffee/tea/milk, washing up equipment |
| Warehouse sundry | Gloves, cable ties, markers (permanent), box cutters |

### Apps Script: `disposalTracker.gs`
`checkDisposalsDue()`:
- Checks DISPOSALS tab for rows where `Overdue? = TRUE`
- Generates a summary: "X items overdue for stock check"
- Can be triggered as part of the Monday weekly check workflow

`updateWeeklyUsage()`:
- Manual trigger: you enter current stock for each item
- Script calculates usage since last check, updates `Avg Usage Per Week` (rolling average)

`generateDisposalReorderList()`:
- Filters: `Weeks of Stock Remaining < 3`
- Outputs a reorder list: item, qty needed, primary supplier link, estimated cost
- Shows total estimated cost for the reorder run

### Bulk-buying strategy
For items where `In Bulk? = N` and usage is predictable:
- Flag items where bulk price saving > 15%
- Calculate break-even: "buying 12 months upfront costs £X more than 12× monthly orders"
- Decision: if storage is available and cash flow allows, buy 6-month supply for: A4 paper, bin bags, tape, tissue paper (these are the highest-volume predictable ones)

### Integration with Monday workflow
Add a `DISPOSALS check` reminder to the weekly Monday checklist. Takes ~10 minutes — walk the office/warehouse, update stock levels in the DISPOSALS tab, system flags reorders needed.

**Done when:**
- DISPOSALS tab fully populated with starting inventory
- Overdue check flag working correctly
- Reorder list generates in one click
- At least the office paper / printer supplies have backup suppliers identified

---

## Phase 7 — MRP Easy Import Workflow

**STATUS:** `planned`

**Goal:** Reduce the friction of importing new items into MRP Easy — specifically the label/bulk-import scenario where you have to count items, create an import sheet, import them, then add to BOM individually. Find what MRP Easy's API/import tools actually support and build helper scripts where they help.

**Current pain point (labels example):**
1. Count incoming labels
2. Create import sheet manually (Part No, Description, Qty)
3. Import into MRP Easy stock
4. Add each label variant to BOM individually — very tedious at scale

**Investigations needed before building:**
- Does MRP Easy have a BOM import feature? (Check documentation / test environment)
- What format does MRP Easy accept for batch BOM creation?
- Can you import a BOM CSV that adds components to existing BOMs (not just creates new ones)?
- What happens if you import a component that's already in a BOM — does it update qty or duplicate?

**Builds (pending investigation):**

### `services/mrp_easy_helpers.py`
Utility functions for generating MRP Easy-compatible CSV files:

`generate_items_import(items: list) → csv_string`:
- Input: list of (Part No, Description, Group, Cost, Lead Time, Vendor No, Qty)
- Output: CSV formatted for MRP Easy articles import
- Validates required columns before generating

`generate_stock_adjustment_import(adjustments: list) → csv_string`:
- Input: list of (Part No, Qty, Site, Lot)
- Output: CSV formatted for stock adjustment import

`generate_bom_import(bom_lines: list) → csv_string` (if MRP Easy supports it):
- Input: list of (BOM Number, Product Part No, Component Part No, Qty)
- Output: CSV formatted for BOM import

### `sheets/mrpEasyImportHelper.gs`
`generateLabelImportSheet()`:
- When you do a batch label delivery:
  1. Scan or enter Part Numbers + Quantities received
  2. Script generates the MRP Easy import CSV format automatically
  3. Exports to Drive for import
  4. Also generates a BOM addition report: "Remember to add these to BOMs: [list]"

`bulkBOMUpdate()` (if BOM import is confirmed supported):
- Input: a table of Part No → Components + Quantities
- Generates the import CSV for batch BOM addition
- Includes a validation check: warns if component Part No doesn't exist in ITEMS

**Done when:**
- Investigation complete: BOM import capability confirmed or ruled out
- At minimum: items import CSV generator works and reduces the label-count workflow by removing the manual formatting step
- If BOM import is possible: batch BOM addition from a spreadsheet range works

---

## Principles

1. **MRP Easy is the source of record.** vendorOS reads from MRP Easy exports. It never writes back to MRP Easy directly.
2. **Google Sheets stays as the front end.** The Apps Script layer is where daily work happens. Python services provide the analysis and AI output; Sheets is the display.
3. **Human in the loop.** No emails are sent automatically. Gmail drafts only.
4. **Don't duplicate nnlos work.** If nnlos already calculates something (velocity, ROPs, procurement wall), pull from nnlos — don't recalculate.
5. **Start with the highest risk.** Phase 2 (single-source dependencies) and Phase 1 (samples) are the most operationally important. Phases 5 and 7 are enhancements.
6. **Practical over perfect.** A working Sheets tab that you actually update beats a perfect automated system that gets ignored.
