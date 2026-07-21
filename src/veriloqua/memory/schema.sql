-- Veriloqua memory schema. Purpose-built (NOT a fork of any conversation turn-log).
-- Reuses only sound engineering patterns: local SQLite, in-DB vector BLOBs,
-- an applications audit table, and deleted_at soft-delete.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS entries (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                  TEXT NOT NULL,
    src_lang              TEXT NOT NULL,
    tgt_lang              TEXT NOT NULL,
    source_text           TEXT NOT NULL,
    source_norm           TEXT NOT NULL,
    accepted_translation  TEXT NOT NULL,
    literal_gloss         TEXT NOT NULL DEFAULT '',
    rejected_translations TEXT NOT NULL DEFAULT '[]',   -- JSON array (the never-repeat payload)
    error_type            TEXT NOT NULL DEFAULT 'unspecified',
    context_tags          TEXT NOT NULL DEFAULT '{}',    -- JSON {domain,register,locale,audience,project_id}
    applies_when          TEXT NOT NULL DEFAULT '',
    rationale             TEXT NOT NULL DEFAULT '',
    examples              TEXT NOT NULL DEFAULT '[]',    -- JSON array
    provenance            TEXT NOT NULL DEFAULT 'user_correction',
    source_urls           TEXT NOT NULL DEFAULT '[]',    -- JSON array
    confidence            REAL NOT NULL DEFAULT 0.8,
    scope                 TEXT NOT NULL DEFAULT 'user',
    scope_id              TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'active',
    invariant             INTEGER NOT NULL DEFAULT 0,
    hit_count             INTEGER NOT NULL DEFAULT 0,
    apply_count           INTEGER NOT NULL DEFAULT 0,
    positive_signals      INTEGER NOT NULL DEFAULT 0,
    negative_signals      INTEGER NOT NULL DEFAULT 0,
    ttl_days              INTEGER,
    created_at            TEXT NOT NULL,
    last_verified_at      TEXT NOT NULL DEFAULT '',
    last_applied_at       TEXT NOT NULL DEFAULT '',
    superseded_by         INTEGER,
    deleted_at            TEXT
);

-- The guarantee's keystone: AT MOST ONE active row per key. Superseded / retired /
-- needs_review rows coexist for audit + rollback (a plain UNIQUE would reject the
-- superseding INSERT and break supersede-not-mutate).
CREATE UNIQUE INDEX IF NOT EXISTS ux_entries_active_key
    ON entries (source_norm, src_lang, tgt_lang, scope, scope_id)
    WHERE deleted_at IS NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS ix_entries_lookup
    ON entries (src_lang, tgt_lang, status, deleted_at);

-- Influence-tracing + one-command rollback: which entry touched which request.
CREATE TABLE IF NOT EXISTS applications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id   INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    tier       TEXT NOT NULL DEFAULT 'exact'
);
CREATE INDEX IF NOT EXISTS ix_applications_entry ON applications (entry_id);

-- Conflict-at-write: a differing accepted target for an existing key lands here as
-- needs_review instead of silently overwriting.
CREATE TABLE IF NOT EXISTS conflicts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    existing_id        INTEGER NOT NULL,
    proposed_accepted  TEXT NOT NULL,
    note               TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    resolved           INTEGER NOT NULL DEFAULT 0
);

-- Optional semantic-recall booster. Vectors are STAMPED with (backend, model, dim);
-- a query vector with a mismatched stamp triggers re-embed, never a noisy compare.
CREATE TABLE IF NOT EXISTS vectors (
    entry_id INTEGER PRIMARY KEY,
    backend  TEXT NOT NULL,
    model    TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vec      BLOB NOT NULL
);

-- PII-minimized ring buffer that powers correct-by-id without keeping every source
-- forever. Unfiled records age out; filing a correction promotes the payload durably.
CREATE TABLE IF NOT EXISTS request_log (
    request_id  TEXT PRIMARY KEY,
    src_lang    TEXT NOT NULL DEFAULT '',
    tgt_lang    TEXT NOT NULL DEFAULT '',
    source_text TEXT NOT NULL DEFAULT '',
    output_text TEXT NOT NULL DEFAULT '',
    mode        TEXT NOT NULL DEFAULT '',
    domain      TEXT NOT NULL DEFAULT '',
    register    TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reqlog_created ON request_log (created_at);
