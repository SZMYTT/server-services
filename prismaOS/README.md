# PrismaOS

A self-hosted AI operating system that builds and runs business automation
modules across multiple business workspaces. One platform, multiple businesses,
one operator.

**Operator:** szmyt (Daniel)
**Bot:** Prisma
**Version:** 0.1.0
**Last updated:** 2026-04-19

---

## What PrismaOS is

PrismaOS is a meta-system. It builds and configures the modules that do
business tasks, rather than doing those tasks directly. You describe what
a business needs, PrismaOS generates the SOP, configures the agent, and
adds it to the module library. Once built, a module runs independently
and can be reused across any workspace.

The system has three layers:

```
PrismaOS (VS Code / Gitea project)
    the code, SOPs, configs, and agents you build and maintain

Lenovo server (always-on coordinator)
    runs the queue, scheduler, bot, web UI, memory, MCP servers
    never runs models, only coordinates

MacBook M1 Max (always-on inference)
    runs Ollama only
    Lenovo calls it over Tailscale when a task needs LLM inference
```

---

## The people

| Person | Role | Access |
|---|---|---|
| Daniel (szmyt) | Operator | Full - all workspaces, web UI, Gitea |
| Eddie | Workspace user | Cars + Property |
| Eddie's brother | Workspace user | Cars |
| Alice | Workspace user | Candles |
| Asta | Workspace user | Nursing / Massage |
| Alicja | Workspace user | Food brand |

Daniel is the only person who touches PrismaOS directly. Everyone else
interacts through Discord via Prisma the bot. Results post back to their
channels automatically. Daniel approves all tasks before they run and all
outputs before they go public.

---

## Business workspaces

| Workspace | Person | Platform | Status |
|---|---|---|---|
| Candles | Alice | Etsy | Live |
| Nursing / Massage | Asta | Facebook | Live |
| Cars | Eddie + brother | Facebook Marketplace | Live |
| Property | Daniel + Eddie | N/A | Research phase |
| Food brand | Alicja | Instagram + TikTok | Live |

All businesses operate on a revenue share model. Daniel provides the AI
operations and marketing layer. The practitioners do the actual work.

---

## Hardware

### MacBook Pro M1 Max - inference server
- 64GB unified memory, 1TB storage
- Runs Ollama only
- Always on, Tailscale hostname: macbook-pro
- Capable of 70B models comfortably

### Lenovo Yoga - primary server
- Intel Core i7, 16GB RAM, 512GB SSD, x86_64
- Ubuntu 24.04
- Always on, Tailscale hostname: lenovo-server
- Hosts all services except inference

### Gaming PC - dev workstation + secondary inference
- AMD Ryzen 3700X, 16GB RAM, RTX 3060Ti
- 1TB SSD + 8TB HDD (backup target)
- Windows 11
- VS Code + Remote SSH into Lenovo for development

### Pixel 9 - mobile client
- Discord bot commands
- Tailscale access
- ntfy push notifications

### ASUS ProArt PZ13 - spare
- Snapdragon X Plus (ARM) - keep on Windows

---

## Network

All devices connected via Tailscale mesh VPN.
Nothing exposed to public internet.
Lenovo Tailscale IP: 100.119.217.120

---

## Models

| Agent | Model | Host | Use case |
|---|---|---|---|
| Orchestrator | llama3.3:70b | MacBook | Task decomposition, complex reasoning |
| Researcher | llama3.3:70b | MacBook | Web research, synthesis |
| Coder | qwen2.5-coder:32b | MacBook | Code generation, review |
| Content | mistral:22b | MacBook | Social copy, ad copy |
| Finance / Docs | phi4:14b | MacBook | Structured output, documents |
| Fast / Router | llama3.2:3b | Gaming PC | Instant replies, classification |
| Fast Coder | qwen2.5-coder:7b | Gaming PC | Quick code tasks |

---

## Task execution

### Task categories

Every task gets two labels at creation:

Type:
- research     finding information, analysing data
- content      writing posts, copy, creative material
- comms        customer messages, email replies
- finance      reports, forecasts, transaction analysis
- action       anything that executes in the real world

Risk level:
- internal     stays inside the system
- public       posts or publishes something visible
- financial    spends money or commits to something real

### Three-tier approval

Tier 1 - Internal (research, finance)
- User triggers in Discord
- Appears in Daniel's approval queue
- Daniel approves via Discord button or web UI
- Runs, result posted to user's channel as summary + web UI link

Tier 2 - Public (content, social posts)
- Daniel approves to run AND approves before publishing
- Nothing posts publicly without Daniel's sign-off

Tier 3 - Financial/action (ad spend, customer replies)
- Daniel pinged immediately in #operator-log
- Daniel approves to run AND approves the action itself
- Full audit log written to Postgres

### Queue priority

URGENT queue   - jumps everything, financial tasks + Daniel bumps
FAST queue     - Gaming PC 3B model, under 2 mins
STANDARD queue - Mac 70B, 2-15 mins
BATCH queue    - Mac 70B, overnight only, 15+ mins

Priority score per task = queue weight + workspace fairness boost
+ wait time boost + shorter task bonus

---

## Database schema

See db/schema.sql for full Postgres schema.

Key tables:
- tasks          master task record with full lifecycle
- task_steps     step-level checkpoints per task
- schedules      recurring task definitions
- workspace_analytics  weekly rollup per workspace

---

## SOP architecture

Three layers assembled at runtime per task:

Layer 1 - System layer (~500 tokens, always)
  Who Daniel is, PrismaOS core rules, output format

Layer 2 - Module layer (~1500 tokens, per agent type)
  Deep instructions for what this agent does

Layer 3 - Workspace layer (~800 tokens, per business)
  Business context, brand voice, audience, goals

Total per task: ~2800-3500 tokens

---

## Services on Lenovo

| Service | Port | Status |
|---|---|---|
| PostgreSQL | 5433 | Running |
| ChromaDB | 8001 | Running |
| SearXNG | 8080 | Running |
| ntfy | 8002 | Running |
| Gitea | 3001 | Running |
| Caddy | 80/443 | Running |
| Orchestrator | 8000 | To build |
| Prisma bot | - | To build |
| Web UI | 3000 | To build |

---

## Discord structure

```
PrismaOS Discord
|- #operator-log         Daniel only - approvals, all activity
|- #operator-analytics   Daniel only - weekly digest
|
|- candles/
|   |- #candles-summary
|   |- #candles-stock-alerts
|   |- #candles-messages
|   |- #candles-marketing
|   |- #candles-commands
|
|- nursing-massage/
|   |- #nursing-summary
|   |- #nursing-bookings
|   |- #nursing-messages
|   |- #nursing-commands
|
|- cars/
|   |- #cars-summary
|   |- #cars-auction-alerts
|   |- #cars-inventory
|   |- #cars-commands
|
|- property/
|   |- #property-research
|   |- #property-finance
|   |- #property-compliance
|   |- #property-commands
|
|- food-brand/
    |- #food-summary
    |- #food-content
    |- #food-analytics
    |- #food-commands
```

---

## Project structure

```
prismaOS/
|- README.md
|- environment.yaml
|- schedules.yaml
|- .env                    never commit
|- .gitignore
|- docker-compose.yml
|
|- sops/
|   |- system/core.md
|   |- modules/
|   |   |- research.md
|   |   |- content.md
|   |   |- comms.md
|   |   |- finance.md
|   |   |- action.md
|   |   |- coder.md
|   |- workspaces/
|       |- candles/
|       |- nursing_massage/
|       |- cars/
|       |- property/
|       |- food_brand/
|
|- agents/
|   |- base.py
|   |- orchestrator.py
|   |- researcher.py
|   |- content.py
|   |- comms.py
|   |- finance.py
|   |- coder.py
|
|- modules/
|   |- research/
|   |- content/
|   |- comms/
|   |- finance/
|   |- website_builder/
|   |- auction_sourcing/
|
|- workspaces/
|   |- candles/profile.md
|   |- nursing_massage/profile.md
|   |- cars/profile.md
|   |- property/profile.md
|   |- food_brand/profile.md
|
|- bot/
|   |- discord_bot.py
|   |- permissions.py
|   |- responses.py
|   |- commands/
|
|- services/
|   |- queue.py
|   |- scheduler.py
|   |- router.py
|   |- sop_assembler.py
|   |- checkpointer.py
|   |- memory.py
|   |- notifier.py
|   |- analytics.py
|
|- mcp/
|   |- search.py
|   |- browser.py
|   |- filesystem.py
|   |- etsy.py
|   |- facebook.py
|   |- gmail.py
|
|- db/
|   |- schema.sql
|   |- migrations/
|   |- queries.py
|
|- web_ui/
|   |- app.py
|   |- frontend/
|
|- scripts/
|   |- setup_lenovo.sh
|   |- backup.sh
|   |- prisma.service
|
|- config/
    |- caddy/Caddyfile
    |- searxng/settings.yml
    |- ntfy/
```

---

## Build phases

### Phase 1 - Infrastructure (COMPLETE)
- [x] Ubuntu 24.04 on Lenovo
- [x] Docker installed
- [x] PostgreSQL running (port 5433)
- [x] ChromaDB running (port 8001)
- [x] SearXNG running (port 8080)
- [x] ntfy running (port 8002)
- [x] Gitea running (port 3001)
- [x] Caddy running (port 80)
- [x] Tailscale on all devices
- [x] Ollama on MacBook + models
- [x] Ollama on Gaming PC + models

### Phase 2 - Core execution pipeline
- [x] db/schema.sql created and run
- [x] services/queue.py - task queue
- [x] services/router.py - fast vs heavy classification
- [x] services/sop_assembler.py - layered prompt builder
- [x] sops/system/core.md - Layer 1 SOP
- [x] sops/modules/research.md - Layer 2 SOP
- [x] sops/workspaces/property/ - Layer 3 SOP
- [x] agents/researcher.py - first agent
- [x] services/checkpointer.py - step checkpointing
- [x] End to end test via curl

### Phase 3 - Prisma Discord bot
- `[x]` Discord app created at discord.com/developers
- `[x]` Discord server set up with full channel structure
- `[x]` bot/discord_bot.py running as systemd service
- `[x]` Approval flow working end to end
- `[x]` Scheduler firing scheduled tasks
- `[x]` First real task: Alice triggers /research, Daniel approves, result posts

### Phase 4 - Web UI
- `[x]` Task queue view
- `[x]` Approvals inbox
- `[x]` Schedule management
- `[x]` Logs with step detail
- `[x]` Analytics dashboard

### Phase 5 - MCP integrations
- `[x]` SearXNG wired to research agent
- `[x]` Playwright browser scraping
- `[x]` Etsy API
- `[x]` Facebook Graph API
- `[x]` Gmail API

### Phase 6 - Module library
- `[x]` Content / social media module
- `[x]` Customer comms with approval flow
- `[x]` Finance and reporting module
- `[x]` Website builder module
- `[x]` Auction sourcing module
- `[x]` Document analyser
- `[x]` Legal compliance module

### Phase 7 - Client portal
- `[x]` Per workspace dashboards
- `[x]` Accounts for Alice, Asta, Eddie, Alicja
- `[x]` Mobile responsive

---

## Open questions

- `[x]` Frontend framework - Plain HTML and Jinja decided
- `[x]` Secrets management long term - Local .env + systemd deployed
- `[x]` Booking platform for Asta - public booking page at /book/nursing
- `[x]` Brand voice definition for Cars workspace
- `[x]` Target audience definitions for all workspaces

---

## Decisions log

| Date | Decision | Reason |
|---|---|---|
| 2026-04-18 | Lenovo as server not ASUS | x86 vs ARM |
| 2026-04-18 | Both Mac and Lenovo always-on | No single point of failure |
| 2026-04-18 | Inference on Mac only | Separate services from inference |
| 2026-04-18 | Discord as input layer | Everyone uses it, mobile-friendly |
| 2026-04-18 | Layered SOP assembly | Lean context, modular, fast |
| 2026-04-18 | Revenue share model | Aligned incentives |
| 2026-04-19 | PostgreSQL for task logs | Analytics queries |
| 2026-04-19 | Step checkpointing in Postgres | Replaces .sh files |
| 2026-04-19 | Daniel approves all tasks initially | Build usage patterns first |
| 2026-04-19 | Three-tier approval by risk | Different gates for different stakes |
| 2026-04-19 | No compute budgets | Local is free, Daniel is the filter |
| 2026-04-19 | Decline sends reason to user | Teaches better requests |
| 2026-04-19 | Weekly analytics digest | Patterns without noise |
| 2026-04-19 | Renamed SystemOS to PrismaOS | Stronger name, fits Prisma bot |
