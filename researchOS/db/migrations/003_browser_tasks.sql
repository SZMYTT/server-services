-- Migration: Browser tasks table
-- Run: psql $DATABASE_URL -f db/migrations/003_browser_tasks.sql

SET search_path TO supply;

CREATE TABLE IF NOT EXISTS browser_tasks (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    url          TEXT NOT NULL,
    objective    TEXT NOT NULL,
    skus         JSONB,
    status       TEXT DEFAULT 'pending',
    actions_log  JSONB,
    result       JSONB,
    screenshot   TEXT,
    error        TEXT,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_browser_tasks_status ON browser_tasks(status);
