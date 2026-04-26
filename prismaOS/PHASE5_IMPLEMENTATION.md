# Phase 5 Implementation Guide
## PrismaOS ERP + Integrations

All sub-phases build on the ERP tables and routes delivered in Phase 5.0–5.4.
Work through these in order — each phase has dependencies on the one before it.

---

## Phase 5.5 — ntfy ERP Notifications ✅
**Goal:** Push real-time alerts to ntfy whenever an ERP action occurs.

### Tasks
- [x] Create `services/erp_notifier.py`
  - `notify(title, body, topic, priority, tags)` — base httpx POST to ntfy
  - `notify_new_order(order)` — candles new Etsy order
  - `notify_new_booking(booking)` — nursing new booking
  - `notify_low_stock(item)` — candles inventory below reorder level
  - `notify_new_auction(auction)` — cars new auction alert
  - `notify_auction_won(auction)` — cars auction won → auto-inventory
  - `notify_watchlist_update(listing)` — property watchlist status change
- [x] Wire into `web_ui/app.py` ERP POST endpoints:
  - `POST /api/erp/candles/orders` → `notify_new_order()`
  - `POST /api/erp/nursing/bookings` → `notify_new_booking()`
  - `POST /api/erp/candles/inventory` (low stock check) → `notify_low_stock()`
  - `POST /api/erp/cars/auctions` → `notify_new_auction()`
  - `POST /api/erp/cars/auctions/{id}/won` → `notify_auction_won()`
  - `PATCH /api/erp/property/watchlist/{id}` → `notify_watchlist_update()`

**ntfy config:**
- Server: `http://ntfy:80` (Docker internal) or `http://localhost:8002` (host)
- Default topic: `prisma-erp`
- Topic format: `prisma-{workspace}` e.g. `prisma-candles`, `prisma-cars`

---

## Phase 5.6 — Etsy API Integration ✅
**Goal:** Replace the stub `mcp/etsy.py` with real Etsy API v3 calls.

### Tasks
- [x] Rewrite `mcp/etsy.py` with httpx async client
  - `fetch_unread_messages(shop_id)` → `GET /shops/{shop_id}/conversations` (with `unread` filter)
  - `send_reply(conversation_id, message)` → `POST /shops/{shop_id}/conversations/{id}/messages`
  - `fetch_open_orders(shop_id)` → `GET /shops/{shop_id}/receipts?status=open`
  - `sync_orders_to_db()` → fetch open orders → upsert `candles_orders` table
  - `fetch_listing_inventory(listing_id)` → `GET /listings/{id}/inventory`
  - Graceful degradation: if `ETSY_API_KEY` / `ETSY_ACCESS_TOKEN` / `ETSY_SHOP_ID` not set → return `[]` with warning log
- [x] Add `.env` vars: `ETSY_API_KEY`, `ETSY_ACCESS_TOKEN`, `ETSY_SHOP_ID`
- [ ] Wire `sync_orders_to_db()` into scheduler (Phase 5.9)

**Etsy API base:** `https://openapi.etsy.com/v3/application`
**Auth headers:** `x-api-key: {ETSY_API_KEY}` + `Authorization: Bearer {ETSY_ACCESS_TOKEN}`

---

## Phase 5.7 — Gmail API Integration ✅
**Goal:** Replace stub `mcp/gmail.py` with real Gmail API calls.

### Tasks
- [x] Rewrite `mcp/gmail.py` with `google-api-python-client`
  - `fetch_unread_messages(max_results)` → list + get inbox messages
  - `send_email(to, subject, body)` → create + send MIME message
  - `send_booking_confirmation(client_email, client_name, service, date_time)` → nursing confirmations
  - Token refresh: load `credentials.json` → OAuth2 flow → save `token.json`
  - Graceful degradation: if `GOOGLE_CREDENTIALS_PATH` not set or token invalid → return `[]`
- [ ] Wire booking confirmations into nursing booking POST endpoint (Phase 5.9)

**Gmail API scopes:** `gmail.readonly`, `gmail.send`, `gmail.modify`
**Credentials:** `GOOGLE_CREDENTIALS_PATH` in `.env` pointing to OAuth2 `credentials.json`

---

## Phase 5.8 — Facebook / Instagram API ✅
**Goal:** Replace stub `mcp/facebook.py` with real Graph API calls.

### Tasks
- [x] Rewrite `mcp/facebook.py` with httpx async client
  - `fetch_page_mentions(page_id)` → `GET /{page_id}/tagged`
  - `publish_post(page_id, message, link)` → `POST /{page_id}/feed`
  - `publish_to_instagram(ig_account_id, image_url, caption)` → two-step container create → publish
  - `fetch_post_insights(post_id, metrics)` → `GET /{post_id}/insights`
  - Graceful degradation: if `FACEBOOK_PAGE_TOKEN` not set → return `[]`
- [ ] Wire `publish_post()` into content calendar publish workflow (Phase 5.9)

**Facebook Graph API base:** `https://graph.facebook.com/v19.0`
**Auth:** `FACEBOOK_PAGE_TOKEN` in `.env` (Page Access Token)
**Instagram:** `INSTAGRAM_ACCOUNT_ID` in `.env`

---

## Phase 5.9 — ERP Intelligence ✅
**Goal:** Connect ERP data to the AI agent pipeline.

### Tasks
- [x] Create `services/erp_context.py`
  - `build_erp_context(workspace)` → summary dict injected into agent system prompt
  - `format_erp_context_for_prompt(workspace)` → human-readable string for system prompts
  - Covers all 5 workspaces + finance summary
- [x] Auto-transactions from completed bookings (nursing)
  - Trigger: `PATCH /api/erp/nursing/bookings/{id}` status → `completed`
  - Action: INSERT into `finance_transactions` with service price
- [x] Auto-inventory from auction won (cars)
  - Trigger: `PATCH /api/erp/cars/auctions/{id}` status → `won`
  - Action: INSERT into `cars_vehicles` with make/model/price from auction row
- [x] Low-stock agent alert
  - When stock adjusted below `reorder_level`: `notify_low_stock()` + queue `research` task
- [x] Rightmove scraper in `mcp/browser.py`
  - Playwright-based: `scrape_rightmove(postcode, max_price, min_beds, max_beds)` → list of listings
  - `sync_rightmove_to_watchlist(postcode, max_price)` → upsert into `property_watchlist`
  - Graceful if `playwright` not installed
  - Added `UNIQUE` constraint on `property_watchlist.listing_url` in schema.sql
  - Schema migration included (DO $$ block for existing databases)

---

## Phase 5.10 — Analytics Charts ✅
**Goal:** Add Chart.js visualisations to ERP pages.

### Tasks
- [x] Add `GET /api/erp/finance/{workspace}/chart` → 12-month income/expense bar chart data
- [x] Add `GET /api/erp/finance/overview/chart` → 6-month per-workspace net P&L grouped bar
- [x] Add `GET /api/erp/candles/orders/chart` → 12-month order count + revenue line/bar combo
- [x] `finance.html` — Monthly P&L bar chart (income vs expenses per month)
- [x] `finance_overview.html` — 6-month cross-workspace grouped bar chart
- [x] `cars_inventory.html` — Pipeline horizontal funnel (sourced → prepping → listed → sold)
  - Fixed `fetch_cars_vehicle_stats()` to include `prepping` count
- [x] `content_calendar.html` (food_brand) — Top 10 posts engagement bar chart (views/likes/comments)
  - Expanded analytics stats row from 2 → 4 stats (added comments + shares)
- [x] `candles_orders.html` — Monthly orders bar + revenue line combo chart (dual Y axes)
- All charts use forest-cream palette, DM Sans font, Chart.js 4.4.0 (already in base.html)

---

## Phase 5.11 — Authentication Hardening ✅
**Goal:** Replace cookie username with hashed passwords and per-user permissions.

### Tasks
- [x] Add `users` table (`username PK`, `bcrypt_hash`, `active`, `last_login`) to `db/schema.sql` + migrated live
- [x] Add `audit_log` table (`username`, `action`, `resource`, `resource_id`, `workspace`, `ip_address`, `details JSONB`) + indexes
- [x] Rewrote `web_ui/auth.py`:
  - bcrypt password verify (`verify_password`)
  - signed session cookies via `itsdangerous.URLSafeTimedSerializer` (`create_session_token` / `decode_session_token`)
  - 12-hour session expiry (`SESSION_MAX_AGE_HOURS` in `.env`)
  - `require_workspace_access(user, workspace)` — raises 403 for out-of-scope workspaces
  - `update_last_login()` on successful auth
- [x] Updated `POST /login` — verifies bcrypt hash, sets signed `httponly samesite=lax` cookie, logs LOGIN/LOGIN_FAILED to `audit_log`
- [x] Updated `GET /logout` — logs LOGOUT to `audit_log`, clears cookie
- [x] `GET /login` redirects to `/` if already authenticated
- [x] Updated `login.html` — added password field, updated copy
- [x] Added `audit()` helper in `app.py` — wired into: candles orders, cars auctions, nursing bookings, property deals, finance create/delete
- [x] Created `scripts/seed_passwords.py` — interactive bcrypt hash setup for all team members
- [x] Added `bcrypt==5.0.0` + `itsdangerous==2.2.0` to `requirements.txt`
- [x] Added `SECRET_KEY` + `SESSION_MAX_AGE_HOURS` to `.env`
- [x] Seeded password for `daniel` (operator)

**To set passwords for other users:**
```bash
python scripts/seed_passwords.py alice eddie asta alicja eddies_brother
```

---

## Phase 5.12 — Scheduler Integration ✅
**Goal:** Automate recurring ERP tasks via the scheduler service.

### Tasks
- [x] Rewrote `services/scheduler.py` with two parallel job types:
  - **DB-backed agent tasks** — reads `schedules` table, queues via `add_task()`
  - **Integration jobs** — async functions called directly, tracked in `integration_job_runs` table
- [x] `workspace_window_to_cron()` — converts `environment.yaml` scheduling windows to cron expressions
- [x] Integration jobs wired:
  - `etsy_sync` — `sync_orders_to_db()` at candles window (Mon 07:00)
  - `rightmove_sync` — `sync_rightmove_to_watchlist()` at property window (Sun 09:00); reads postcode + budget from env.yaml
  - `content_publish_check` — queries `*_content` tables for due scheduled posts, queues publish tasks (daily 07:00)
- [x] Seeded `schedules` table with per-workspace weekly digest agent tasks:
  - Candles Weekly Digest — `0 7 * * 1`
  - Cars Daily Check — `0 6 * * *`
  - Nursing Weekly Summary — `0 8 * * 1`
  - Food Brand Weekly Digest — `0 8 * * 5`
  - Property Weekly Research — `0 9 * * 0`
- [x] Added `integration_job_runs` table to `db/schema.sql` + migrated live
- [x] Fixed `input=` parameter name in `add_task()` calls (was `input_text=`)
- [x] Added `/audit` operator page + sidebar nav entry — shows last 500 audit events with action filters

### Audit Log URL
`http://<server>:3000/audit` — operator only

---

## Dependency Map

```
5.5 (ntfy)
  └─ no deps

5.6 (Etsy)
  └─ no deps (graceful without key)

5.7 (Gmail)
  └─ no deps (graceful without credentials)

5.8 (Facebook)
  └─ no deps (graceful without token)

5.9 (ERP Intelligence)
  ├─ requires 5.5 (notifications)
  ├─ requires 5.6 (Etsy sync for candles context)
  └─ requires 5.7 (Gmail for booking confirmations)

5.10 (Analytics)
  └─ requires Phase 5.0 ERP tables to be populated

5.11 (Auth)
  └─ independent (can do any time)

5.12 (Scheduler)
  ├─ requires 5.6 (Etsy sync)
  ├─ requires 5.8 (social publishing)
  └─ requires 5.9 (ERP intelligence context)
```

---

## Environment Variables Checklist

Add these to `.env` as you get each credential:

```env
# Phase 5.5 — ntfy
NTFY_URL=http://localhost:8002
NTFY_TOPIC=prisma-erp

# Phase 5.6 — Etsy
ETSY_API_KEY=
ETSY_ACCESS_TOKEN=
ETSY_SHOP_ID=

# Phase 5.7 — Gmail
GOOGLE_CREDENTIALS_PATH=/home/szmyt/server-services/prismaOS/credentials/google_credentials.json
GOOGLE_TOKEN_PATH=/home/szmyt/server-services/prismaOS/credentials/google_token.json

# Phase 5.8 — Facebook / Instagram
FACEBOOK_PAGE_TOKEN=
FACEBOOK_PAGE_ID=
INSTAGRAM_ACCOUNT_ID=
```
