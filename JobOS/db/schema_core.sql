-- systemOS/db/schema_core.sql
-- Core engine tables shared by all projects.
-- Run with: psql -U daniel -d systemos -h localhost -p 5433 -f schema_core.sql

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
    timing_breakdown JSONB,
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
    step_name       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    input           JSONB,
    output          JSONB,
    error           TEXT,
    duration_ms     INTEGER,
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

-- Module duration estimates (updated after every task run)
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

-- Integration job run tracker
CREATE TABLE IF NOT EXISTS integration_job_runs (
    job_name    TEXT PRIMARY KEY,
    last_run    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auth: users
CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    bcrypt_hash TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    last_login  TIMESTAMPTZ
);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username    TEXT NOT NULL,
    action      TEXT NOT NULL,
    resource    TEXT NOT NULL,
    resource_id TEXT,
    workspace   TEXT,
    ip_address  TEXT,
    details     JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tasks_workspace        ON tasks(workspace);
CREATE INDEX IF NOT EXISTS idx_tasks_status           ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created          ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_workspace_status ON tasks(workspace, status);
CREATE INDEX IF NOT EXISTS idx_task_steps_task_id     ON task_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_schedules_workspace    ON schedules(workspace);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run     ON schedules(next_run) WHERE active = true;
CREATE INDEX IF NOT EXISTS idx_audit_username         ON audit_log(username);
CREATE INDEX IF NOT EXISTS idx_audit_created          ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource         ON audit_log(resource);

-- ── Seed module estimates ─────────────────────────────────────
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

-- ── Migrations (safe to re-run) ───────────────────────────────
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS timing_breakdown JSONB;
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS duration_ms INTEGER;
