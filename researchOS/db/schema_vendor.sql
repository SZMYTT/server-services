-- vendorOS extension to supplyOS
-- Run after db/schema.sql: psql -U postgres -d systemos -f db/schema_vendor.sql
SET search_path TO supply;

CREATE TABLE IF NOT EXISTS vendor_scrape_jobs (
    id           BIGSERIAL PRIMARY KEY,
    vendor_name  TEXT,
    vendor_url   TEXT NOT NULL,
    skus         JSONB DEFAULT '[]',     -- optional list of SKU strings to find on site
    category     TEXT,                   -- vendorOS category (raw-fragrance, packaging-glass, etc.)
    status       TEXT DEFAULT 'pending', -- pending, running, done, error
    error_msg    TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS vendor_profiles (
    id                     BIGSERIAL PRIMARY KEY,
    job_id                 BIGINT REFERENCES vendor_scrape_jobs(id) ON DELETE CASCADE,
    vendor_name            TEXT,
    vendor_url             TEXT NOT NULL,
    category               TEXT,
    scraped_at             TIMESTAMPTZ DEFAULT NOW(),

    -- Company intelligence
    company_type           TEXT,      -- manufacturer / distributor / wholesaler / dropshipper / retailer
    uk_based               BOOLEAN,
    about                  TEXT,
    address                TEXT,
    contact_email          TEXT,
    contact_phone          TEXT,
    certifications         JSONB DEFAULT '[]',

    -- Commercial terms
    min_order_value        TEXT,
    min_order_qty          TEXT,
    lead_time              TEXT,
    wholesale_available    BOOLEAN,
    trade_account_required BOOLEAN,
    payment_terms          TEXT,
    delivery_info          TEXT,

    -- Products found on site (array of {sku, name, url, price, price_tiers, in_stock, description})
    products               JSONB DEFAULT '[]',

    -- Intelligence
    potential_upstream     TEXT,
    alternatives           JSONB DEFAULT '[]',  -- [{name, url, notes}]
    risk_flags             JSONB DEFAULT '[]',  -- ["appears to be dropshipper", ...]
    confidence_score       INTEGER,             -- 1-10

    -- Full report
    raw_report             TEXT,
    raw_report_html        TEXT,
    pages_scraped          JSONB DEFAULT '[]'   -- list of URLs scraped during job
);

CREATE INDEX IF NOT EXISTS idx_vendor_jobs_status  ON vendor_scrape_jobs(status);
CREATE INDEX IF NOT EXISTS idx_vendor_profiles_job ON vendor_profiles(job_id);
