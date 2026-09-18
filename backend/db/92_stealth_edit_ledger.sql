-- Migration 92: stealth_edit_ledger -- a GitHub-style "who changed what
-- file, and when" audit trail for hand-edits made directly to
-- `.stealth/*.md` projection files (claims.md, procedures.md, goals.md,
-- run.md, ...) through a separate, simpler MCP frontend a teammate is
-- building on top of this server.
--
-- WHY A NEW TABLE, NOT A LOCAL JOURNAL / NOT local_sync.py
--   `.stealth/` is disposable and regenerated -- that invariant is NOT
--   changing here. This table is NOT a mechanism for promoting edited
--   content into canonical Claim/Procedure/Goal truth (that already
--   exists, untouched by this migration, as `preview_local_sync`/
--   `commit_local_sync` in `app.stealth.local_sync`). It is log-only: a
--   flat, append-only record of "actor X edited file Y at time T because
--   Z" -- evidence/audit history, never a second source of truth.
--
--   Same reasoning as migration 90's `run_collaboration_records` (see
--   that migration's own docstring): a workspace-LOCAL journal
--   (`app.stealth.journal`, `.stealth/events.jsonl`) cannot be read by a
--   different `repo_path`/checkout of the same workspace, and this ledger
--   needs to survive across machines/checkouts and be visible to any
--   agent (or the teammate's frontend) connecting later -- so it lives in
--   durable Postgres, projected one-way into a local `.stealth/ledger.md`
--   file, the same one-way pattern `run.md` already established.
--
--   It is a NEW TABLE rather than reusing `run_collaboration_records`
--   because an edit-ledger entry is not tied to an `execution_run_id` at
--   all (a `.stealth/` edit can happen with no run in progress, e.g. right
--   after `init_workspace` bootstraps an empty repo) -- it is scoped to a
--   WORKSPACE (`project_id`, the same derived identity
--   `app.execution.workspace_init._project_id_from_repo_path` already
--   uses, reused here rather than inventing a second workspace-identity
--   concept), not a run.
--
-- Deliberately NOT storing diffs, full file contents, or before/after
-- hashes -- this is a log, not source control (no revert mechanism, no
-- patch storage). `content_hash` is the one exception, and only because
-- it is essentially free: `hashlib.sha256` over the file's current text is
-- a one-line computation the caller (the `record_stealth_edit` MCP tool)
-- already has the file open to write elsewhere, not new hashing
-- infrastructure. It is OPTIONAL (nullable) -- a caller/file that cannot
-- be hashed still gets a valid ledger row.
--
-- Fresh-start rule: additive, NO in-migration backfill. Idempotent:
-- CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS stealth_edit_ledger (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    actor         TEXT NOT NULL,
    summary       TEXT NOT NULL,
    content_hash  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stealth_edit_ledger_project
    ON stealth_edit_ledger (project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_stealth_edit_ledger_project_file
    ON stealth_edit_ledger (project_id, file_path, created_at DESC);
