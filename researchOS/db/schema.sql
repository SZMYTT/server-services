CREATE SCHEMA IF NOT EXISTS supply;
SET search_path TO supply;

CREATE TABLE IF NOT EXISTS research_projects (
    id          BIGSERIAL PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT,
    icon        TEXT DEFAULT '📚',
    sort_order  INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS research_topics (
    id           BIGSERIAL PRIMARY KEY,
    project_id   BIGINT REFERENCES research_projects(id) ON DELETE SET NULL,
    topic        TEXT NOT NULL,
    category     TEXT DEFAULT 'general',
    sop_hint     TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending, running, done, error
    sort_order   INTEGER DEFAULT 0,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (project_id, topic)
);

CREATE TABLE IF NOT EXISTS research_findings (
    id          BIGSERIAL PRIMARY KEY,
    topic_id    BIGINT NOT NULL REFERENCES research_topics(id) ON DELETE CASCADE,
    report      TEXT NOT NULL,
    report_html TEXT,
    model       TEXT,
    sources     JSONB,
    queries     JSONB,
    output_file TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_topics_status     ON research_topics(status);
CREATE INDEX IF NOT EXISTS idx_topics_project    ON research_topics(project_id);
CREATE INDEX IF NOT EXISTS idx_findings_topic    ON research_findings(topic_id);
