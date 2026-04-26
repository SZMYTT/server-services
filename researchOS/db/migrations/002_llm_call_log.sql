-- Migration: LLM call telemetry table
-- Run: psql $DATABASE_URL -f db/migrations/002_llm_call_log.sql

SET search_path TO supply;

CREATE TABLE IF NOT EXISTS llm_call_log (
    id            BIGSERIAL PRIMARY KEY,
    called_at     TIMESTAMPTZ DEFAULT NOW(),
    service       TEXT NOT NULL,          -- 'researchOS', 'vendorOS', etc.
    topic_id      BIGINT,                 -- FK to research_topics (nullable — not all calls are per-topic)
    call_type     TEXT NOT NULL,          -- 'queries', 'synthesis', 'vendor_profile', 'browser_action', etc.
    model         TEXT NOT NULL,
    backend       TEXT NOT NULL,          -- 'anthropic' or 'ollama'
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd      NUMERIC(12,8) DEFAULT 0,
    cost_gbp      NUMERIC(12,8) DEFAULT 0,
    duration_ms   INTEGER,
    fast          BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_llm_log_called_at  ON llm_call_log(called_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_log_topic_id   ON llm_call_log(topic_id);
CREATE INDEX IF NOT EXISTS idx_llm_log_service     ON llm_call_log(service);
CREATE INDEX IF NOT EXISTS idx_llm_log_model       ON llm_call_log(model);

-- Convenient view: cost aggregated per topic
CREATE OR REPLACE VIEW llm_cost_by_topic AS
SELECT
    l.topic_id,
    t.topic,
    t.status,
    SUM(l.total_tokens)  AS total_tokens,
    SUM(l.cost_usd)      AS total_cost_usd,
    SUM(l.cost_gbp)      AS total_cost_gbp,
    COUNT(*)             AS call_count,
    MAX(l.called_at)     AS last_call_at
FROM supply.llm_call_log l
LEFT JOIN supply.research_topics t ON t.id = l.topic_id
WHERE l.topic_id IS NOT NULL
GROUP BY l.topic_id, t.topic, t.status
ORDER BY last_call_at DESC;

-- Convenient view: daily cost summary
CREATE OR REPLACE VIEW llm_cost_daily AS
SELECT
    DATE(called_at AT TIME ZONE 'Europe/London') AS day,
    service,
    backend,
    SUM(total_tokens)  AS tokens,
    SUM(cost_usd)      AS cost_usd,
    SUM(cost_gbp)      AS cost_gbp,
    COUNT(*)           AS calls
FROM supply.llm_call_log
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 3 DESC;
