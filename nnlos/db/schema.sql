-- nnlos/db/schema.sql
-- NNL procurement and inventory intelligence tables.
-- All tables live in the 'nnlos' schema within the shared systemos database.

CREATE SCHEMA IF NOT EXISTS nnlos;
SET search_path TO nnlos;

-- ─── Source data tables (mirrored from MRP Easy CSV exports) ─────────────────

CREATE TABLE IF NOT EXISTS items (
    part_no             TEXT PRIMARY KEY,
    description         TEXT,
    group_number        TEXT,
    group_name          TEXT,
    in_stock            NUMERIC(12,3) DEFAULT 0,
    packaged            NUMERIC(12,3) DEFAULT 0,
    available           NUMERIC(12,3) DEFAULT 0,
    booked              NUMERIC(12,3) DEFAULT 0,
    expected_total      NUMERIC(12,3) DEFAULT 0,
    expected_available  NUMERIC(12,3) DEFAULT 0,
    work_in_progress    NUMERIC(12,3) DEFAULT 0,
    reorder_point       NUMERIC(12,3) DEFAULT 0,
    min_qty_mfg         NUMERIC(12,3) DEFAULT 0,
    cost                NUMERIC(12,4) DEFAULT 0,
    selling_price       NUMERIC(12,4) DEFAULT 0,
    uom                 TEXT,
    lead_time_days      INTEGER DEFAULT 0,
    vendor_number       TEXT,
    vendor_name         TEXT,
    vendor_part_no      TEXT,
    procurement_type    TEXT,   -- forward_order, stock_holding, email_order, website_order
    stock_type          TEXT,   -- Production Stock, Component Stock, Retail Stock, etc.
    is_procured         BOOLEAN DEFAULT false,
    is_inventory        BOOLEAN DEFAULT true,
    synced_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_movements (
    id              BIGSERIAL PRIMARY KEY,
    created_date    DATE NOT NULL,
    lot             TEXT NOT NULL DEFAULT '',  -- '' not NULL so UNIQUE constraint works
    site            TEXT,
    part_no         TEXT NOT NULL,
    description     TEXT,
    group_number    TEXT,
    group_name      TEXT,
    quantity        NUMERIC(12,3) NOT NULL,   -- negative = outgoing, positive = incoming
    cost            NUMERIC(12,4),
    source          TEXT NOT NULL DEFAULT '', -- '' not NULL so UNIQUE constraint works
    stock_type      TEXT,
    synced_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (created_date, lot, part_no, quantity, source)
);

CREATE TABLE IF NOT EXISTS boms (
    id              BIGSERIAL PRIMARY KEY,
    bom_number      TEXT,
    bom_name        TEXT,
    product_no      TEXT NOT NULL,
    product_name    TEXT,
    group_number    TEXT,
    group_name      TEXT,
    part_no         TEXT NOT NULL,
    part_description TEXT,
    uom             TEXT,
    quantity        NUMERIC(12,4) NOT NULL,
    approx_cost     NUMERIC(12,4),
    notes           TEXT,
    bom_type        TEXT,
    synced_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (bom_number, part_no)
);

CREATE TABLE IF NOT EXISTS vendors (
    vendor_number   TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    phone           TEXT,
    email           TEXT,
    url             TEXT,
    address         TEXT,
    on_time_pct     NUMERIC(5,2),
    avg_delay_days  NUMERIC(6,2),
    currency        TEXT DEFAULT 'GBP',
    default_lead_time_days INTEGER,
    total_cost      NUMERIC(14,2),
    supplier_type   TEXT,   -- forward_order, stock_holding, email_order, website_order
    order_notes     TEXT,
    payment_period  INTEGER,
    payment_period_type TEXT,
    synced_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id              BIGSERIAL PRIMARY KEY,
    po_number       TEXT NOT NULL,
    part_no         TEXT NOT NULL,
    part_description TEXT,
    vendor_part_no  TEXT,
    group_number    TEXT,
    group_name      TEXT,
    quantity        NUMERIC(12,3),
    lot             TEXT,
    site            TEXT,
    total           NUMERIC(12,2),
    unit_cost       NUMERIC(12,4),
    currency        TEXT DEFAULT 'GBP',
    status          TEXT,
    product_status  TEXT,
    created_by      TEXT,
    created_date    DATE,
    expected_date   DATE,
    arrival_date    DATE,
    order_id        TEXT,
    order_date      DATE,
    invoice_id      TEXT,
    due_date        DATE,
    shipped_on      DATE,
    delay_days      INTEGER,
    vendor_number   TEXT,
    vendor_name     TEXT,
    supplier_type   TEXT,
    order_notes     TEXT,
    stock_type      TEXT,
    synced_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (po_number, part_no)
);

CREATE TABLE IF NOT EXISTS inventory_snapshot (
    part_no         TEXT NOT NULL,
    description     TEXT,
    group_number    TEXT,
    group_name      TEXT,
    quantity        NUMERIC(12,3) DEFAULT 0,
    uom             TEXT,
    cost            NUMERIC(12,4),
    avg_cost        NUMERIC(12,4),
    wip_quantity    NUMERIC(12,3) DEFAULT 0,
    stock_type      TEXT,
    snapshot_date   DATE NOT NULL DEFAULT CURRENT_DATE,
    synced_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (part_no, snapshot_date)
);

CREATE TABLE IF NOT EXISTS criticall (
    part_no         TEXT NOT NULL,
    description     TEXT,
    group_number    TEXT,
    group_name      TEXT,
    site            TEXT NOT NULL,
    in_stock        NUMERIC(12,3) DEFAULT 0,
    available       NUMERIC(12,3) DEFAULT 0,
    expected_available NUMERIC(12,3) DEFAULT 0,
    reorder_point   NUMERIC(12,3) DEFAULT 0,
    stock_type      TEXT,
    synced_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (part_no, site)
);

CREATE TABLE IF NOT EXISTS shop_orders (
    id              BIGSERIAL PRIMARY KEY,
    order_number    TEXT,
    customer_name   TEXT,
    email           TEXT,
    status          TEXT,
    total           NUMERIC(12,2),
    created_date    DATE,
    delivery_date   DATE,
    part_no         TEXT,
    part_description TEXT,
    group_number    TEXT,
    group_name      TEXT,
    quantity        NUMERIC(12,3),
    shipped         NUMERIC(12,3),
    order_type      TEXT,   -- 'shop' or 'post'
    synced_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ─── Calculated / derived tables (written by NNLOS analytics engine) ──────────

CREATE TABLE IF NOT EXISTS sales_velocity (
    part_no         TEXT PRIMARY KEY,
    description     TEXT,
    unit_price      NUMERIC(12,4),
    qty_30d         NUMERIC(12,3) DEFAULT 0,
    qty_60d         NUMERIC(12,3) DEFAULT 0,
    qty_90d         NUMERIC(12,3) DEFAULT 0,
    qty_120d        NUMERIC(12,3) DEFAULT 0,
    qty_180d        NUMERIC(12,3) DEFAULT 0,
    qty_365d        NUMERIC(12,3) DEFAULT 0,
    income_30d      NUMERIC(14,2) DEFAULT 0,
    income_60d      NUMERIC(14,2) DEFAULT 0,
    income_90d      NUMERIC(14,2) DEFAULT 0,
    income_120d     NUMERIC(14,2) DEFAULT 0,
    income_180d     NUMERIC(14,2) DEFAULT 0,
    income_365d     NUMERIC(14,2) DEFAULT 0,
    avg_daily_30d   NUMERIC(10,4) DEFAULT 0,
    avg_daily_60d   NUMERIC(10,4) DEFAULT 0,
    avg_daily_90d   NUMERIC(10,4) DEFAULT 0,
    calculated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS component_usage (
    part_no         TEXT PRIMARY KEY,
    description     TEXT,
    qty_30d         NUMERIC(12,3) DEFAULT 0,
    qty_60d         NUMERIC(12,3) DEFAULT 0,
    qty_90d         NUMERIC(12,3) DEFAULT 0,
    qty_120d        NUMERIC(12,3) DEFAULT 0,
    qty_180d        NUMERIC(12,3) DEFAULT 0,
    qty_365d        NUMERIC(12,3) DEFAULT 0,
    avg_daily_30d   NUMERIC(10,4) DEFAULT 0,
    avg_daily_60d   NUMERIC(10,4) DEFAULT 0,
    reorder_point   NUMERIC(12,3),
    cost            NUMERIC(12,4),
    lead_time_days  INTEGER,
    vendor_name     TEXT,
    calculated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS production_forecast (
    part_no             TEXT PRIMARY KEY,
    description         TEXT,
    current_stock       NUMERIC(12,3) DEFAULT 0,
    daily_usage         NUMERIC(10,4) DEFAULT 0,
    days_remaining      NUMERIC(8,1),
    shortage_date       DATE,
    lead_time_days      INTEGER,
    recommended_order_date DATE,
    reorder_point       NUMERIC(12,3),
    status              TEXT,   -- ok, on_radar, urgent
    calculated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ─── Operational tables (written by NNLOS services) ───────────────────────────

CREATE TABLE IF NOT EXISTS shipments (
    id              BIGSERIAL PRIMARY KEY,
    mrp_order_id    TEXT,
    shipment_date   DATE,
    status          TEXT DEFAULT 'draft',   -- draft, reviewed, split, exported, complete
    raw_filename    TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS shipment_lines (
    id              BIGSERIAL PRIMARY KEY,
    shipment_id     BIGINT NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    part_no         TEXT NOT NULL,
    description     TEXT,
    quantity        NUMERIC(12,3) NOT NULL,
    lot             TEXT,
    status          TEXT DEFAULT 'ok',   -- ok, edited, removed, flagged
    notes           TEXT,
    split_chunk     INTEGER,   -- 1 or 2 (for MRP Easy 100-row split)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS shop_replenishment (
    id              BIGSERIAL PRIMARY KEY,
    week_date       DATE NOT NULL,
    site            TEXT NOT NULL,
    part_no         TEXT NOT NULL,
    description     TEXT,
    in_stock        NUMERIC(12,3),
    reorder_point   NUMERIC(12,3),
    quantity_needed NUMERIC(12,3),
    group_name      TEXT,
    status          TEXT DEFAULT 'pending',   -- pending, sent, exported
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (week_date, site, part_no)
);

CREATE TABLE IF NOT EXISTS discrepancies (
    id              BIGSERIAL PRIMARY KEY,
    week_date       DATE NOT NULL,
    site            TEXT,
    part_no         TEXT,
    description     TEXT,
    reported_qty    NUMERIC(12,3),
    system_qty      NUMERIC(12,3),
    source          TEXT,   -- slack, email, manual
    raw_message     TEXT,
    status          TEXT DEFAULT 'open',   -- open, resolved, ignored
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS po_staging (
    id              BIGSERIAL PRIMARY KEY,
    part_no         TEXT NOT NULL,
    description     TEXT,
    vendor_number   TEXT,
    vendor_name     TEXT,
    vendor_part_no  TEXT,
    quantity        NUMERIC(12,3),
    unit_cost       NUMERIC(12,4),
    currency        TEXT DEFAULT 'GBP',
    expected_date   DATE,
    procurement_type TEXT,
    notes           TEXT,
    status          TEXT DEFAULT 'new',   -- new, staged, ordered
    po_number       TEXT,   -- filled in once created in MRP Easy
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS daily_tasks (
    id              BIGSERIAL PRIMARY KEY,
    task_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    priority        TEXT NOT NULL,   -- urgent, this_week, on_radar
    task_type       TEXT NOT NULL,   -- stock_out, overdue_po, calloff_due, approaching_rop, etc.
    part_no         TEXT,
    description     TEXT,
    vendor_name     TEXT,
    detail          TEXT,
    manual_instruction TEXT,
    status          TEXT DEFAULT 'open',   -- open, actioned, snoozed
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sync_log (
    id              BIGSERIAL PRIMARY KEY,
    sync_type       TEXT NOT NULL,   -- raw_movements, items, boms, vendors, purchase_orders, etc.
    filename        TEXT,
    rows_processed  INTEGER,
    status          TEXT NOT NULL,   -- success, failed, partial
    error_message   TEXT,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- ─── Indexes ──────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_raw_part_date      ON raw_movements(part_no, created_date DESC);
CREATE INDEX IF NOT EXISTS idx_raw_date           ON raw_movements(created_date DESC);
CREATE INDEX IF NOT EXISTS idx_boms_product       ON boms(product_no);
CREATE INDEX IF NOT EXISTS idx_boms_part          ON boms(part_no);
CREATE INDEX IF NOT EXISTS idx_po_vendor          ON purchase_orders(vendor_number);
CREATE INDEX IF NOT EXISTS idx_po_part            ON purchase_orders(part_no);
CREATE INDEX IF NOT EXISTS idx_po_status          ON purchase_orders(status);
CREATE INDEX IF NOT EXISTS idx_po_expected        ON purchase_orders(expected_date);
CREATE INDEX IF NOT EXISTS idx_criticall_site     ON criticall(site);
CREATE INDEX IF NOT EXISTS idx_forecast_status    ON production_forecast(status);
CREATE INDEX IF NOT EXISTS idx_forecast_shortage  ON production_forecast(shortage_date ASC);
CREATE INDEX IF NOT EXISTS idx_tasks_date         ON daily_tasks(task_date DESC, priority);
CREATE INDEX IF NOT EXISTS idx_replen_week        ON shop_replenishment(week_date DESC, site);
CREATE INDEX IF NOT EXISTS idx_staging_status     ON po_staging(status);
