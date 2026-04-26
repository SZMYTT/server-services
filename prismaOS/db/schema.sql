-- db/schema.sql
-- PrismaOS PostgreSQL schema
-- Run with: psql -U szmyt -d prismaos -h localhost -p 5433 -f schema.sql

-- Create database (run as postgres user if needed)
-- CREATE DATABASE prismaos OWNER szmyt;

-- Master task record
CREATE TABLE IF NOT EXISTS tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace       TEXT NOT NULL,
    user_name       TEXT NOT NULL,
    trigger_type    TEXT NOT NULL,   -- discord, scheduler, webui
    task_type       TEXT NOT NULL,   -- research, content, comms, finance, action
    risk_level      TEXT NOT NULL,   -- internal, public, financial
    module          TEXT,
    model           TEXT,
    input           TEXT,
    output          TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
                                     -- queued, pending_approval, approved,
                                     -- running, pending_publish, done,
                                     -- failed, declined
    queue_lane      TEXT,            -- urgent, fast, standard, batch
    priority_score  INTEGER DEFAULT 50,
    approval_by     TEXT,
    approval_at     TIMESTAMPTZ,
    decline_reason  TEXT,
    tools_called    JSONB,
    tokens_used     INTEGER,
    duration_ms     INTEGER,
    timing_breakdown JSONB,           -- per-step timing: {sop_ms, inference_ms, tools_ms, queue_wait_ms, steps: [...]}
    retry_count     INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    notified_at     TIMESTAMPTZ
);

-- Step-level checkpoints
CREATE TABLE IF NOT EXISTS task_steps (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_number     INTEGER NOT NULL,
    step_name       TEXT NOT NULL,   -- web_search, scrape, summarise, write, post
    status          TEXT NOT NULL DEFAULT 'queued',
    input           JSONB,
    output          JSONB,
    error           TEXT,
    duration_ms     INTEGER,         -- how long this step took
    started_at      TIMESTAMPTZ,
    done_at         TIMESTAMPTZ
);

-- Scheduled task definitions
CREATE TABLE IF NOT EXISTS schedules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace       TEXT NOT NULL,
    name            TEXT NOT NULL,
    task_type       TEXT NOT NULL,
    module          TEXT NOT NULL,
    input           TEXT,
    cron_expression TEXT NOT NULL,
    time_window     TEXT,
    priority        INTEGER DEFAULT 5,
    active          BOOLEAN DEFAULT true,
    last_run        TIMESTAMPTZ,
    next_run        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Module duration estimates (updated after every task)
CREATE TABLE IF NOT EXISTS module_estimates (
    module          TEXT PRIMARY KEY,
    estimated_mins  FLOAT NOT NULL DEFAULT 10,
    sample_count    INTEGER DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Weekly analytics rollup per workspace
CREATE TABLE IF NOT EXISTS workspace_analytics (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace           TEXT NOT NULL,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    tasks_requested     INTEGER DEFAULT 0,
    tasks_approved      INTEGER DEFAULT 0,
    tasks_declined      INTEGER DEFAULT 0,
    tasks_failed        INTEGER DEFAULT 0,
    by_type             JSONB,
    by_module           JSONB,
    most_used_model     TEXT,
    avg_duration_ms     INTEGER,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace, period_start)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_tasks_workspace
    ON tasks(workspace);
CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created
    ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_workspace_status
    ON tasks(workspace, status);
CREATE INDEX IF NOT EXISTS idx_task_steps_task_id
    ON task_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_schedules_workspace
    ON schedules(workspace);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run
    ON schedules(next_run) WHERE active = true;

-- Seed module duration estimates
INSERT INTO module_estimates (module, estimated_mins) VALUES
    ('research',          15),
    ('content',           10),
    ('finance',            5),
    ('customer_comms',     3),
    ('inventory',          2),
    ('auction_sourcing',   8),
    ('document_analyser', 12),
    ('coder',             10)
ON CONFLICT (module) DO NOTHING;

-- Seed schedules
INSERT INTO schedules
    (workspace, name, task_type, module, input, cron_expression, time_window)
VALUES
    ('candles', 'Weekly sales summary',
     'finance', 'finance',
     'Generate weekly sales summary with trends and top products',
     '0 7 * * 1', 'morning'),

    ('cars', 'Daily auction scan',
     'research', 'auction_sourcing',
     'Scan BCA and Manheim listings for target criteria',
     '0 6 * * *', 'morning'),

    ('nursing_massage', 'Weekly bookings summary',
     'finance', 'finance',
     'Generate weekly bookings and revenue summary',
     '0 8 * * 1', 'morning'),

    ('food_brand', 'Weekly analytics report',
     'research', 'analytics',
     'Pull Instagram and TikTok performance for the week',
     '0 8 * * 5', 'morning'),

    ('property', 'Weekly property market scan',
     'research', 'research',
     'Scan Rightmove and Zoopla for new listings matching criteria',
     '0 9 * * 0', 'morning')
ON CONFLICT DO NOTHING;

-- ============================================================
-- MIGRATIONS (safe to re-run on existing databases)
-- ============================================================
-- Add timing_breakdown if upgrading from an older schema:
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS timing_breakdown JSONB;
-- Add step duration if upgrading:
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS duration_ms INTEGER;

-- ============================================================
-- PHASE 5 — ERP TABLES
-- ============================================================

-- ── Candles ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS candles_orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    etsy_order_id   TEXT UNIQUE,
    customer_name   TEXT NOT NULL,
    customer_email  TEXT,
    items           JSONB,
    subtotal        NUMERIC(10,2) DEFAULT 0,
    shipping        NUMERIC(10,2) DEFAULT 0,
    total           NUMERIC(10,2) DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open',
    -- open, processing, shipped, completed, cancelled, refunded
    shipping_address TEXT,
    notes           TEXT,
    order_date      TIMESTAMPTZ DEFAULT NOW(),
    shipped_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS candles_products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    sku             TEXT UNIQUE,
    etsy_listing_id TEXT,
    price           NUMERIC(10,2) DEFAULT 0,
    cost            NUMERIC(10,2) DEFAULT 0,
    active          BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS candles_inventory (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'raw',
    -- raw, finished
    sku             TEXT,
    quantity        NUMERIC(12,3) DEFAULT 0,
    unit            TEXT DEFAULT 'units',
    reorder_level   NUMERIC(12,3) DEFAULT 0,
    cost_per_unit   NUMERIC(10,4) DEFAULT 0,
    supplier        TEXT,
    notes           TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS candles_content (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        TEXT NOT NULL,
    -- etsy, instagram, tiktok, pinterest
    title           TEXT NOT NULL,
    caption         TEXT,
    media_url       TEXT,
    publish_date    DATE,
    status          TEXT NOT NULL DEFAULT 'draft',
    -- draft, scheduled, published, archived
    post_url        TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Cars ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cars_vehicles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    make            TEXT NOT NULL,
    model           TEXT NOT NULL,
    year            INTEGER,
    vin             TEXT,
    colour          TEXT,
    mileage         INTEGER,
    buy_price       NUMERIC(12,2) DEFAULT 0,
    repair_costs    NUMERIC(12,2) DEFAULT 0,
    sell_price      NUMERIC(12,2),
    status          TEXT NOT NULL DEFAULT 'sourced',
    -- sourced, prepping, listed, sold
    source          TEXT,
    listing_url     TEXT,
    notes           TEXT,
    purchased_at    DATE,
    listed_at       DATE,
    sold_at         DATE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cars_auctions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT NOT NULL,
    -- bca, manheim, facebook, autotrader
    listing_url     TEXT,
    title           TEXT NOT NULL,
    make            TEXT,
    model           TEXT,
    year            INTEGER,
    mileage         INTEGER,
    asking_price    NUMERIC(12,2),
    auction_date    DATE,
    status          TEXT NOT NULL DEFAULT 'new',
    -- new, reviewed, bid, won, passed
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cars_documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id      UUID REFERENCES cars_vehicles(id) ON DELETE SET NULL,
    doc_type        TEXT NOT NULL,
    -- v5c, mot, service_history, receipt, insurance
    description     TEXT,
    file_path       TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Property ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS property_deals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    address         TEXT NOT NULL,
    postcode        TEXT,
    deal_type       TEXT NOT NULL DEFAULT 'buy',
    -- buy, flip, rental, commercial
    asking_price    NUMERIC(12,2),
    offer_price     NUMERIC(12,2),
    agreed_price    NUMERIC(12,2),
    estimated_costs NUMERIC(12,2) DEFAULT 0,
    estimated_value NUMERIC(12,2),
    status          TEXT NOT NULL DEFAULT 'prospect',
    -- prospect, offer_made, under_offer, due_diligence, exchanged, completed, lost
    agent           TEXT,
    solicitor       TEXT,
    viewing_date    DATE,
    offer_date      DATE,
    exchange_date   DATE,
    completion_date DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS property_watchlist (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    address         TEXT NOT NULL,
    postcode        TEXT,
    listing_url     TEXT UNIQUE,
    source          TEXT,
    -- rightmove, zoopla, onthemarket, auction, private
    asking_price    NUMERIC(12,2),
    property_type   TEXT,
    -- terraced, semi, detached, flat, commercial
    bedrooms        INTEGER,
    notes           TEXT,
    status          TEXT NOT NULL DEFAULT 'watching',
    -- watching, contacted, viewing_booked, archived
    found_at        DATE DEFAULT CURRENT_DATE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Add unique constraint if upgrading from a pre-5.9 schema
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'property_watchlist_listing_url_key'
    ) THEN
        ALTER TABLE property_watchlist ADD CONSTRAINT property_watchlist_listing_url_key UNIQUE (listing_url);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS property_documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id         UUID REFERENCES property_deals(id) ON DELETE SET NULL,
    doc_type        TEXT NOT NULL,
    -- offer_letter, survey, contract, title_deeds, mortgage, insurance
    description     TEXT,
    file_path       TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Nursing / Massage ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nursing_clients (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    phone           TEXT,
    email           TEXT,
    date_of_birth   DATE,
    address         TEXT,
    medical_notes   TEXT,
    active          BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nursing_services (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    category        TEXT,
    -- nursing, massage, consultation
    duration_mins   INTEGER DEFAULT 60,
    price           NUMERIC(10,2) DEFAULT 0,
    active          BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS nursing_bookings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID REFERENCES nursing_clients(id) ON DELETE SET NULL,
    client_name     TEXT,
    service_id      UUID REFERENCES nursing_services(id) ON DELETE SET NULL,
    service_name    TEXT,
    booking_date    DATE NOT NULL,
    booking_time    TIME NOT NULL,
    duration_mins   INTEGER DEFAULT 60,
    amount          NUMERIC(10,2) DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'confirmed',
    -- confirmed, completed, cancelled, no_show
    payment_status  TEXT DEFAULT 'unpaid',
    -- unpaid, paid, refunded
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nursing_content (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        TEXT NOT NULL,
    title           TEXT NOT NULL,
    caption         TEXT,
    publish_date    DATE,
    status          TEXT NOT NULL DEFAULT 'draft',
    post_url        TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Food Brand ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS food_content (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        TEXT NOT NULL,
    -- instagram, tiktok, youtube, blog
    title           TEXT NOT NULL,
    caption         TEXT,
    media_url       TEXT,
    publish_date    DATE,
    status          TEXT NOT NULL DEFAULT 'draft',
    -- draft, scheduled, published, archived
    post_url        TEXT,
    views           INTEGER DEFAULT 0,
    likes           INTEGER DEFAULT 0,
    comments        INTEGER DEFAULT 0,
    shares          INTEGER DEFAULT 0,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS food_partnerships (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand           TEXT NOT NULL,
    contact_name    TEXT,
    contact_email   TEXT,
    deal_value      NUMERIC(10,2),
    deliverables    TEXT,
    due_date        DATE,
    status          TEXT NOT NULL DEFAULT 'prospect',
    -- prospect, negotiating, active, completed, declined
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS food_ideas (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,
    description     TEXT,
    category        TEXT,
    -- recipe, trend, product, collab
    platform        TEXT,
    status          TEXT NOT NULL DEFAULT 'idea',
    -- idea, planned, in_progress, published, archived
    priority        INTEGER DEFAULT 5,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Shared Finance ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS finance_transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace       TEXT NOT NULL,
    type            TEXT NOT NULL,
    -- income, expense
    category        TEXT NOT NULL,
    -- sales, materials, shipping, marketing, salary, tax, fees, other
    description     TEXT NOT NULL,
    amount          NUMERIC(12,2) NOT NULL,
    reference_id    UUID,
    reference_type  TEXT,
    -- candles_order, nursing_booking, cars_vehicle, property_deal
    date            DATE NOT NULL DEFAULT CURRENT_DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── ERP Indexes ───────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_candles_orders_status   ON candles_orders(status);
CREATE INDEX IF NOT EXISTS idx_candles_orders_date     ON candles_orders(order_date DESC);
CREATE INDEX IF NOT EXISTS idx_candles_inventory_cat   ON candles_inventory(category);
CREATE INDEX IF NOT EXISTS idx_cars_vehicles_status    ON cars_vehicles(status);
CREATE INDEX IF NOT EXISTS idx_cars_auctions_status    ON cars_auctions(status);
CREATE INDEX IF NOT EXISTS idx_property_deals_status   ON property_deals(status);
CREATE INDEX IF NOT EXISTS idx_property_watch_status   ON property_watchlist(status);
CREATE INDEX IF NOT EXISTS idx_nursing_bookings_date   ON nursing_bookings(booking_date DESC);
CREATE INDEX IF NOT EXISTS idx_nursing_bookings_status ON nursing_bookings(status);
CREATE INDEX IF NOT EXISTS idx_food_content_date       ON food_content(publish_date DESC);
CREATE INDEX IF NOT EXISTS idx_finance_ws              ON finance_transactions(workspace);
CREATE INDEX IF NOT EXISTS idx_finance_date            ON finance_transactions(date DESC);

-- ── Integration job run tracker (Phase 5.12) ─────────────────
CREATE TABLE IF NOT EXISTS integration_job_runs (
    job_name    TEXT PRIMARY KEY,
    last_run    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Auth: Users ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    bcrypt_hash TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    last_login  TIMESTAMPTZ
);

-- ── Audit Log ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username    TEXT NOT NULL,
    action      TEXT NOT NULL,
    -- LOGIN, LOGOUT, CREATE, UPDATE, DELETE
    resource    TEXT NOT NULL,
    resource_id TEXT,
    workspace   TEXT,
    ip_address  TEXT,
    details     JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_log(username);
CREATE INDEX IF NOT EXISTS idx_audit_created  ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource);

-- ── Workspace module configuration ───────────────────────────
CREATE TABLE IF NOT EXISTS workspace_modules (
    workspace    TEXT NOT NULL,
    module       TEXT NOT NULL,
    enabled      BOOLEAN DEFAULT true,
    sort_order   INTEGER DEFAULT 99,
    settings     JSONB DEFAULT '{}',
    PRIMARY KEY (workspace, module)
);

CREATE INDEX IF NOT EXISTS idx_ws_modules_workspace ON workspace_modules(workspace);

-- ── Seed nursing services ─────────────────────────────────────
INSERT INTO nursing_services (name, category, duration_mins, price) VALUES
    ('Swedish Massage',       'massage',      60,  65.00),
    ('Deep Tissue Massage',   'massage',      60,  75.00),
    ('Hot Stone Massage',     'massage',      90,  90.00),
    ('Nursing Consultation',  'nursing',      30,  50.00),
    ('Home Visit Nursing',    'nursing',      60, 120.00),
    ('Pregnancy Massage',     'massage',      60,  80.00)
ON CONFLICT DO NOTHING;
