-- 152_local_check_evidence.sql
--
-- Next free number: 151 was highest before this file.
--
-- What this adds: the server side of the client's check evidence (docs/evidence_upload_design.md). The client
-- (`stealthlab-mcp status --run`, dispatch's accepted answers) records one EVIDENCE line per check run in
-- .stealth/evidence.md; with upload switched on it reports the line through the MCP tool `report_check_evidence`, and the
-- line lands here. The proposal's server-side Goal status (docs/verification_changes_prop.md) reads this table.
--
-- WHY A SIBLING TABLE AND NOT AN EXTENSION OF verification_results (migration 55). verification_results is keyed to an
-- execution run (`execution_run_id NOT NULL`) and keeps ONE current row per (run, criterion): a re-evaluation UPDATEs it.
-- Local evidence is a HISTORY (the latest pass, the latest fail, a pass and a fail on the same code) and has no execution
-- run. Making that table append-only with a nullable run would change what every run-scoped reader assumes and weaken the
-- foreign key its tenant isolation rides on. Migration 55's core invariant is kept, though: callers never pass a state.
-- `record_local_check` (app/services/verification.py) computes it from (method, result); the table's CHECK refuses any
-- pairing the service could not produce.
--
-- WHAT A ROW IS, AND IS NOT. It is a client-REPORTED observation (provenance 'company_ingested', scope 'repository'): the
-- server did not run the check, so a pass is stored as `checked`, one rung below the ladder's `verified`, and a fail as
-- `failed_verification` (compare execution/durable_run.py: provider-reported results stay `unverified`). `verified` is not
-- a stored state: it is derived by the view `local_check_effective` when at least TWO independent groups (distinct
-- accounts) have a current pass on the same check and the same files (see that view). Nothing here lets a caller choose a
-- state, and `provenance` can only say where the row came from.
--
-- WHAT NEVER ARRIVES. No column holds a command, a path, an output, a review text or a repository name: the check is a
-- keyed HMAC (`check_hash`), each file is `{path_hmac, sha}`, the repository is the hashed identity the client already sends
-- (`repo_ref`). The key lives only on the client (packaging/npm/lib/evidence_key.mjs). Because no free-text column exists on
-- this path there is nothing for trace_redaction to guard; the tool refuses any field outside its whitelist instead.
--
-- ISOLATION. Strict row-level security with NO fallback (as 150/151): a row is visible only to the account that reported it
-- (`app.owner_subject`, migration 150's sl_owner_scope_allows: unset sees nothing) AND inside the tenant it was reported
-- under (`app.tenant_id`, sl_tenant_scope_allows), or under the explicit system scope. FORCE ROW LEVEL SECURITY: even the
-- table owner is subject to it. (A SUPERUSER bypasses RLS whatever is declared; the DB-backed tests therefore connect as a
-- plain role.) The cross-account count that makes `verified` possible runs under the system scope and returns counts only.
--
-- APPEND-ONLY. Like `evidence` (migration 24): DELETE is refused, and the only UPDATE is the retraction tombstone
-- (t_invalid). Corrections are new rows. The columns are enumerated in the trigger so a column added later fails loudly.
--
-- Idempotent: safe to re-run. Fresh-start: additive DDL only, no backfill, no data writes.

CREATE TABLE IF NOT EXISTS local_check_evidence (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- the client's id for this evidence line; the outbox may send a line twice, so (owner_id, client_event_id) is unique
    client_event_id     UUID NOT NULL,

    tenant_id           UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001'::uuid,
    -- the repository, as the hashed identity the survey already sends ("r:" / "c:" / "p:" + 16 hex); never a name or URL
    repo_ref            TEXT NOT NULL,

    target_kind         TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    -- server ids, present only when the client's library.md already holds them. goal_id has a real key; procedure_id is the
    -- LOGICAL procedure id (one per version family, so not a foreign key)
    goal_id             UUID REFERENCES goals(id),
    procedure_id        UUID,

    method              TEXT NOT NULL,
    result              TEXT NOT NULL,
    -- computed by the service from (method, result); see the header
    state               TEXT NOT NULL,
    -- who counts as an independent source of this evidence: the reporting account
    independence_group  TEXT NOT NULL,

    observed_at         TIMESTAMPTZ NOT NULL,
    commit_sha          TEXT,
    tree_id             TEXT,
    -- [{"path_hmac": <64 hex>, "sha": <4-64 hex>}, ...]
    hashes              JSONB NOT NULL DEFAULT '[]',
    where_ran           TEXT,
    check_hash          TEXT NOT NULL,

    -- independent_agent only: both model names, which must differ (the reviewer is not the executor)
    reviewer_model      TEXT,
    executor_model      TEXT,
    client_version      TEXT,

    -- V0 gate fields: nullable at the DB layer on purpose, required by app/services/v0_gate.py at the write boundary
    provenance          TEXT,
    extractor_version   TEXT,
    created_by          TEXT,
    visibility          visibility_level NOT NULL DEFAULT 'private',
    owner_id            TEXT NOT NULL,
    scope_type          TEXT,
    scope_entity_id     TEXT,

    -- bi-temporal trio, as `evidence`: t_valid / t_invalid = when true in the world (t_invalid = retraction tombstone),
    -- t_created = when the system learned it
    t_valid             TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid           TIMESTAMPTZ,
    t_created           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A hashes value is an array of objects with exactly the keys path_hmac (64 hex) and sha (4-64 hex).
CREATE OR REPLACE FUNCTION sl_local_check_hashes_ok(h JSONB) RETURNS BOOLEAN
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE e JSONB;
BEGIN
    IF h IS NULL OR jsonb_typeof(h) <> 'array' OR jsonb_array_length(h) > 500 THEN RETURN FALSE; END IF;
    FOR e IN SELECT * FROM jsonb_array_elements(h) LOOP
        IF jsonb_typeof(e) <> 'object' THEN RETURN FALSE; END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(e)) <> 2 THEN RETURN FALSE; END IF;
        IF NOT (e ? 'path_hmac' AND e ? 'sha') THEN RETURN FALSE; END IF;
        IF jsonb_typeof(e -> 'path_hmac') <> 'string' OR jsonb_typeof(e -> 'sha') <> 'string' THEN RETURN FALSE; END IF;
        IF (e ->> 'path_hmac') !~ '^[0-9a-f]{64}$' OR (e ->> 'sha') !~ '^[0-9a-f]{4,64}$' THEN RETURN FALSE; END IF;
    END LOOP;
    RETURN TRUE;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_repo_ref_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_repo_ref_chk
            CHECK (repo_ref ~ '^[a-z]:[0-9a-f]{16}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_target_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_target_chk
            CHECK (target_kind IN ('library_entry', 'plan_node') AND target_id ~ '^[NL]-[A-Za-z0-9_.-]{1,40}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_method_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_method_chk
            CHECK (method IN ('deterministic_check', 'independent_agent'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_result_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_result_chk
            CHECK (result IN ('pass', 'fail'));
    END IF;
    -- the only (result, state) pairings the service can produce: a client-reported pass is at most `checked`
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_state_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_state_chk
            CHECK ((result = 'pass' AND state = 'checked') OR (result = 'fail' AND state = 'failed_verification'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_group_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_group_chk
            CHECK (length(btrim(independence_group)) > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_ids_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_ids_chk
            CHECK ((commit_sha IS NULL OR commit_sha ~ '^[0-9a-f]{40}$') AND (tree_id IS NULL OR tree_id ~ '^[0-9a-f]{40}$')
                   AND check_hash ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_where_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_where_chk
            CHECK (where_ran IS NULL OR where_ran IN ('worktree', 'in-place'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_hashes_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_hashes_chk
            CHECK (sl_local_check_hashes_ok(hashes));
    END IF;
    -- independence of the reviewer: an independent_agent row names both models and they differ; a deterministic row names neither
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_check_evidence_reviewer_chk') THEN
        ALTER TABLE local_check_evidence ADD CONSTRAINT local_check_evidence_reviewer_chk
            CHECK ((method = 'independent_agent'
                    AND reviewer_model IS NOT NULL AND executor_model IS NOT NULL
                    AND lower(btrim(reviewer_model)) <> lower(btrim(executor_model))
                    AND length(btrim(reviewer_model)) > 0 AND length(btrim(executor_model)) > 0)
                   OR (method = 'deterministic_check' AND reviewer_model IS NULL AND executor_model IS NULL));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_local_check_evidence_event
    ON local_check_evidence (owner_id, client_event_id);
CREATE INDEX IF NOT EXISTS idx_local_check_evidence_target
    ON local_check_evidence (tenant_id, repo_ref, target_id, check_hash, observed_at DESC) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_local_check_evidence_goal
    ON local_check_evidence (goal_id) WHERE goal_id IS NOT NULL AND t_invalid IS NULL;

-- ============================================================
-- Append-only: DELETE refused; the only UPDATE is the retraction tombstone (t_invalid), as `evidence` (migration 24).
-- ============================================================
CREATE OR REPLACE FUNCTION sl_local_check_only_tombstone() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '%.% is append-only: DELETE rejected -- retract via the t_invalid tombstone or append a new row',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;
    IF OLD.t_invalid IS NOT NULL THEN
        RAISE EXCEPTION 'local_check_evidence % is already retracted -- retraction is final, append a new row instead', OLD.id;
    END IF;
    -- Every column except t_invalid must be untouched. Enumerated (not a whole-row compare) so that adding a column later
    -- fails THIS function loudly instead of silently widening what a tombstone may edit.
    IF ROW(
            NEW.id, NEW.client_event_id, NEW.tenant_id, NEW.repo_ref, NEW.target_kind, NEW.target_id, NEW.goal_id,
            NEW.procedure_id, NEW.method, NEW.result, NEW.state, NEW.independence_group, NEW.observed_at, NEW.commit_sha,
            NEW.tree_id, NEW.hashes, NEW.where_ran, NEW.check_hash, NEW.reviewer_model, NEW.executor_model,
            NEW.client_version, NEW.provenance, NEW.extractor_version, NEW.created_by, NEW.visibility, NEW.owner_id,
            NEW.scope_type, NEW.scope_entity_id, NEW.t_valid, NEW.t_created
        ) IS DISTINCT FROM
       ROW(
            OLD.id, OLD.client_event_id, OLD.tenant_id, OLD.repo_ref, OLD.target_kind, OLD.target_id, OLD.goal_id,
            OLD.procedure_id, OLD.method, OLD.result, OLD.state, OLD.independence_group, OLD.observed_at, OLD.commit_sha,
            OLD.tree_id, OLD.hashes, OLD.where_ran, OLD.check_hash, OLD.reviewer_model, OLD.executor_model,
            OLD.client_version, OLD.provenance, OLD.extractor_version, OLD.created_by, OLD.visibility, OLD.owner_id,
            OLD.scope_type, OLD.scope_entity_id, OLD.t_valid, OLD.t_created
       ) THEN
        RAISE EXCEPTION 'local_check_evidence is append-only: only the t_invalid retraction tombstone may change -- append a new row instead';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_local_check_evidence_append_only') THEN
        CREATE TRIGGER tg_local_check_evidence_append_only
            BEFORE UPDATE OR DELETE ON local_check_evidence
            FOR EACH ROW EXECUTE FUNCTION sl_local_check_only_tombstone();
    END IF;
END $$;

-- ============================================================
-- Strict row-level security, no fallback.
-- ============================================================
ALTER TABLE local_check_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE local_check_evidence FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'local_check_evidence' AND policyname = 'local_check_strict') THEN
        CREATE POLICY local_check_strict ON local_check_evidence FOR ALL
            USING (sl_owner_scope_allows(owner_id) AND sl_tenant_scope_allows(tenant_id))
            WITH CHECK (sl_owner_scope_allows(owner_id) AND sl_tenant_scope_allows(tenant_id));
    END IF;
END $$;

-- ============================================================
-- Views. security_invoker: they run with the CALLER's row-level security, not the view owner's.
-- ============================================================

-- The latest live row per (account, repository, target, method, check): what each account currently says.
CREATE OR REPLACE VIEW local_check_current WITH (security_invoker = true) AS
SELECT DISTINCT ON (owner_id, tenant_id, repo_ref, target_id, method, check_hash) *
FROM local_check_evidence
WHERE t_invalid IS NULL
ORDER BY owner_id, tenant_id, repo_ref, target_id, method, check_hash, observed_at DESC, t_created DESC, id;

-- The effective state of a check on a target, with corroboration. `verified` is DERIVED here and never stored. Per check, look
-- at the newest file-signature reported (`sig`: the sorted path_hmac:sha pairs, which match across accounts only when they
-- share the evidence key) and count the independent groups (distinct accounts) whose CURRENT row says pass, and fail, on that
-- same signature. A pass counts only at the newest signature: evidence on older files is superseded.
--   fail and pass on the same files -> contested      fail only -> failed_verification
--   two or more passing groups     -> verified        one passing group -> checked
-- Read it under the system scope: it counts across accounts, and it returns counts, never another account's rows.
CREATE OR REPLACE VIEW local_check_effective WITH (security_invoker = true) AS
WITH cur AS (
    SELECT c.tenant_id, c.repo_ref, c.target_id, c.check_hash, c.independence_group, c.result, c.observed_at,
           COALESCE((SELECT md5(string_agg((e ->> 'path_hmac') || ':' || (e ->> 'sha'), ',' ORDER BY e ->> 'path_hmac'))
                       FROM jsonb_array_elements(c.hashes) e), '') AS sig
    FROM local_check_current c
), newest AS (
    SELECT DISTINCT ON (tenant_id, repo_ref, target_id, check_hash) tenant_id, repo_ref, target_id, check_hash, sig
    FROM cur
    ORDER BY tenant_id, repo_ref, target_id, check_hash, observed_at DESC
), counted AS (
    SELECT cur.tenant_id, cur.repo_ref, cur.target_id, cur.check_hash, newest.sig,
           count(DISTINCT cur.independence_group) FILTER (WHERE cur.result = 'pass') AS passing_groups,
           count(DISTINCT cur.independence_group) FILTER (WHERE cur.result = 'fail') AS failing_groups,
           max(cur.observed_at) AS last_observed_at
    FROM cur JOIN newest USING (tenant_id, repo_ref, target_id, check_hash)
    WHERE cur.sig = newest.sig
    GROUP BY cur.tenant_id, cur.repo_ref, cur.target_id, cur.check_hash, newest.sig
)
SELECT tenant_id, repo_ref, target_id, check_hash, sig, passing_groups, failing_groups, last_observed_at,
       CASE WHEN failing_groups > 0 AND passing_groups > 0 THEN 'contested'
            WHEN failing_groups > 0 THEN 'failed_verification'
            WHEN passing_groups >= 2 THEN 'verified'
            ELSE 'checked' END AS effective_state
FROM counted;
