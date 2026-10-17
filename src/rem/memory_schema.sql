-- Beta-only memory tables. Names and Mem0 collection are intentionally separate
-- from the legacy Rem bot's users/raw_events/memories tables.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rem_beta_raw_events (
    id BIGSERIAL PRIMARY KEY,
    discord_message_id TEXT NOT NULL UNIQUE,
    discord_user_id TEXT NOT NULL,
    guild_id TEXT,
    channel_id TEXT NOT NULL,
    author_display_name TEXT NOT NULL,
    content TEXT NOT NULL,
    source_created_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rem_beta_memory_records (
    id BIGSERIAL PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('user', 'community')),
    scope_id TEXT NOT NULL,
    mem0_user_id TEXT NOT NULL,
    mem0_id UUID NOT NULL UNIQUE,
    fact_text TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    confidence_score DOUBLE PRECISION NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 1),
    importance_score DOUBLE PRECISION NOT NULL CHECK (importance_score >= 0 AND importance_score <= 1),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'deleted')),
    source_event_id BIGINT REFERENCES rem_beta_raw_events(id) ON DELETE SET NULL,
    superseded_by_id BIGINT REFERENCES rem_beta_memory_records(id),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rem_beta_memory_evidence (
    memory_id BIGINT NOT NULL REFERENCES rem_beta_memory_records(id) ON DELETE CASCADE,
    raw_event_id BIGINT NOT NULL REFERENCES rem_beta_raw_events(id) ON DELETE CASCADE,
    PRIMARY KEY (memory_id, raw_event_id)
);

CREATE TABLE IF NOT EXISTS rem_beta_memory_review_state (
    discord_user_id TEXT NOT NULL,
    guild_scope_id TEXT NOT NULL,
    last_reviewed_event_id BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (discord_user_id, guild_scope_id)
);

CREATE TABLE IF NOT EXISTS rem_beta_curator_runs (
    discord_message_id TEXT PRIMARY KEY,
    raw_event_id BIGINT NOT NULL REFERENCES rem_beta_raw_events(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'skipped')),
    outcome JSONB,
    error_type TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_rem_beta_events_user_time
    ON rem_beta_raw_events (discord_user_id, source_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rem_beta_events_guild_channel_time
    ON rem_beta_raw_events (guild_id, channel_id, source_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rem_beta_memories_scope_status
    ON rem_beta_memory_records (scope_type, scope_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_rem_beta_memories_mem0_id
    ON rem_beta_memory_records (mem0_id);
CREATE INDEX IF NOT EXISTS idx_rem_beta_evidence_event
    ON rem_beta_memory_evidence (raw_event_id);
