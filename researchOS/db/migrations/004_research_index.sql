-- Migration 004: research_index — Knowledge Ledger
-- Tracks completed research with executive summary, ChromaDB collection pointer,
-- and optional Google Drive link. Enables fast recall without loading full reports.
-- Run once: psql -U postgres -d systemos -f db/migrations/004_research_index.sql

SET search_path TO supply;

CREATE TABLE IF NOT EXISTS research_index (
    id                BIGSERIAL PRIMARY KEY,
    topic_id          BIGINT REFERENCES research_topics(id) ON DELETE CASCADE,
    topic             TEXT NOT NULL,
    project_slug      TEXT,
    category          TEXT,
    executive_summary TEXT,                   -- 3-5 sentence human-readable summary
    section_count     INTEGER DEFAULT 0,      -- number of sections stored in ChromaDB
    chroma_collection TEXT,                   -- ChromaDB collection name
    drive_url         TEXT,                   -- Google Drive view link (optional)
    output_file       TEXT,                   -- local .md file path
    model             TEXT,
    depth             TEXT,
    tokens_used       INTEGER DEFAULT 0,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_research_index_topic_id ON research_index(topic_id);
CREATE INDEX IF NOT EXISTS idx_research_index_project  ON research_index(project_slug);
CREATE INDEX IF NOT EXISTS idx_research_index_created  ON research_index(created_at DESC);
