# researchOS — AI Context

Read `/home/szmyt/server-services/CLAUDE.md` first for the ecosystem mental model.

---

## What researchOS is

ResearchOS is the **research capability** for all NNL domains. It is not limited to supply chain — any NNL business area (retail, fragrance, website, operations) creates a project here and queues research topics into it.

"Projects" inside researchOS represent NNL business domains. They are folders for organising topics — not separate apps or services.

It is a personal research tool, not a customer-facing product.

---

## What researchOS does

**Mode 1 — Topic Research**
1. User queues a topic into a project (e.g. "fragrance trend forecasting 2026" into the "NNL Fragrance" project)
2. LLM generates 4 targeted search queries
3. SearXNG searches the web
4. Crawl4AI scrapes the top results (5 pages in parallel)
5. LLM synthesises a structured markdown report with depth controlled by preset (quick/standard/deep/thorough)
6. Report saved to `research/` directory and to Postgres

**Mode 2 — Vendor Intelligence (Agentic)**
1. User queues a vendor URL + products to find at `/vendors`
2. Server-side agentic loop: LLM calls tools (`scrape_page`, `search_site`, `search_web`, `get_links`, `done`)
3. Up to 8–70 iterations depending on depth preset, wall-clock time budget enforced
4. Results appear at `/vendor/{id}` — company profile, pricing tiers, MOQs, lead times, alternatives

Vendor Intelligence is a **module within researchOS**, not a separate service.

---

## Hard rules

- **Never make purchasing decisions automatically** — reports are advisory only
- **Never send emails or take external actions** — output is always a file or DB row
- **SearXNG must be running** — no fallback; fail clearly if search returns nothing
- **All reports must be human-readable** — saved as markdown regardless of DB state

---

## NNL context (apply to all research output)

- NNL is a UK candle, fragrance, and homeware brand
- Uses MRP Easy as ERP/MRP system
- 10+ physical retail shops (Aldeburgh, Cambridge, Chelsea, Columbia Road, Southwold, Woodbridge, others)
- Weekly Monday shop replenishment cycle
- Small team — Daniel handles procurement, inventory management, and production planning
- Manages production (manufacturing finished goods from raw materials) and procurement

## Research framing (apply to all research output)

- Prioritise practical, actionable advice over theory
- Focus on tools that work at small-to-mid scale (not SAP-level solutions)
- UK-relevant context where applicable (UK suppliers, regulatory context)
- Realistic AI/automation opportunities with implementation effort estimates

---

## Infrastructure

| Item | Detail |
|------|--------|
| Port | 4001 |
| DB schema | `supply.*` in `systemos` Postgres, port 5433 |
| SearXNG | `http://localhost:8080` |
| Ollama model | `gemma4:26b` (set `OLLAMA_MODEL` in `.env`) |
| Vendor agent model | `VENDOR_SCRAPER_MODEL` in `.env` (defaults to `OLLAMA_MODEL`) |
| Research output | `research/` directory (markdown files) |

---

## Shared tools (imported from systemOS)

```python
from systemOS.mcp.browser import scrape, scrape_many   # Crawl4AI
from systemOS.mcp.search import run_search              # SearXNG
from systemOS.llm import complete                       # Ollama/Anthropic
from systemOS.config.depth import get as get_depth      # depth presets
```

Both project roots are on `sys.path` — see `main.py` bootstrap.

---

## Key files

| File | Purpose |
|------|---------|
| `web/app.py` | FastAPI app — all routes |
| `web/templates/base.html` | Shared HTML shell (all pages extend this) |
| `agents/researcher.py` | Topic research agent |
| `agents/vendor_agent.py` | Vendor intelligence agentic loop |
| `agents/vendor_scraper.py` | Vendor data extraction + LLM structuring |
| `db/schema_vendor.sql` | `supply.vendor_scrape_jobs` + `supply.vendor_profiles` |
| `sops/` | Pre-written research SOP modules (topic + hint pairs) |

---

## Depth presets

| Preset | Time budget | Max iterations |
|--------|-------------|----------------|
| quick | 5 min | 8 |
| standard | 15 min | 20 |
| deep | 30 min | 40 |
| thorough | 60 min | 70 |

Wall-clock budget is enforced — the agent stops early if time is up.

---

## Related services

- **systemOS** — shared tools (browser, search, LLM, depth) — import from here, don't copy
- **nnlos** — procurement operations layer; researchOS vendor findings inform nnlos procurement decisions
- **vendorOS** — vendor strategy (audit, samples, risk) — uses researchOS research as input
