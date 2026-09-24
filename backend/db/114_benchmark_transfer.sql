-- Migration 114. Next free number: 115.

ALTER TABLE benchmark_submissions
    ADD COLUMN IF NOT EXISTS benchmark_transfer_key TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'benchmark_submissions'::regclass
          AND conname = 'benchmark_submissions_goal_id_fkey'
    ) THEN
        ALTER TABLE benchmark_submissions DROP CONSTRAINT benchmark_submissions_goal_id_fkey;
    END IF;
END $$;

DROP TRIGGER IF EXISTS tg_ref_benchmark_submissions_goal_id ON benchmark_submissions;
CREATE TRIGGER tg_ref_benchmark_submissions_goal_id
    BEFORE INSERT OR UPDATE OF goal_id ON benchmark_submissions
    FOR EACH ROW EXECUTE FUNCTION sl_check_ref('goal_id', 'goal', '');

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'benchmarks'::regclass
          AND conname = 'benchmarks_goal_id_fkey'
    ) THEN
        ALTER TABLE benchmarks DROP CONSTRAINT benchmarks_goal_id_fkey;
    END IF;
END $$;

DROP TRIGGER IF EXISTS tg_ref_benchmarks_goal_id ON benchmarks;
CREATE TRIGGER tg_ref_benchmarks_goal_id
    BEFORE INSERT OR UPDATE OF goal_id ON benchmarks
    FOR EACH ROW EXECUTE FUNCTION sl_check_ref('goal_id', 'goal', '');

CREATE UNIQUE INDEX IF NOT EXISTS idx_benchmark_submissions_transfer_key
    ON benchmark_submissions(benchmark_transfer_key)
    WHERE benchmark_transfer_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS benchmark_transfer_decisions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transfer_key             TEXT NOT NULL UNIQUE,
    source_benchmark_id      UUID NOT NULL REFERENCES benchmarks(id),
    source_goal_id           UUID NOT NULL REFERENCES goals(id),
    target_goal_id           UUID NOT NULL REFERENCES goals(id),
    direction                TEXT NOT NULL CHECK (direction IN ('specific_to_abstract', 'abstract_to_specific')),
    transfer_direction       TEXT NOT NULL DEFAULT 'source_to_target' CHECK (transfer_direction = 'source_to_target'),
    decision                 TEXT NOT NULL CHECK (decision IN ('transferable', 'partial', 'uncertain', 'not_transferable')),
    confidence               REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reason                   TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    provenance               JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_fingerprint       TEXT NOT NULL,
    target_fingerprint       TEXT NOT NULL,
    relation_fingerprint     TEXT NOT NULL,
    policy_version           TEXT NOT NULL,
    judge_chain              TEXT,
    judge_provider           TEXT,
    judge_model              TEXT,
    prompt_version           TEXT NOT NULL,
    job_id                   BIGINT,
    target_benchmark_id      UUID REFERENCES benchmarks(id),
    benchmark_submission_id  UUID REFERENCES benchmark_submissions(id),
    scope_type               TEXT NOT NULL CHECK (scope_type IN (
        'global', 'organization', 'team', 'project', 'repository',
        'branch', 'user', 'session', 'task', 'entity'
    )),
    scope_entity_id          TEXT,
    owner_id                 TEXT,
    visibility               TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    tenant_id                UUID NOT NULL REFERENCES organizations(id),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((decision IN ('transferable', 'partial')
            AND target_benchmark_id IS NOT NULL
            AND benchmark_submission_id IS NOT NULL)
        OR (decision IN ('uncertain', 'not_transferable')
            AND target_benchmark_id IS NULL
            AND benchmark_submission_id IS NULL))
);

ALTER TABLE benchmark_transfer_decisions
    ADD COLUMN IF NOT EXISTS relation_fingerprint TEXT,
    ADD COLUMN IF NOT EXISTS policy_version TEXT,
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES organizations(id);

DO $$
DECLARE constraint_name TEXT;
BEGIN
    FOREACH constraint_name IN ARRAY ARRAY[
        'benchmark_transfer_decisions_source_benchmark_id_fkey',
        'benchmark_transfer_decisions_source_goal_id_fkey',
        'benchmark_transfer_decisions_target_goal_id_fkey',
        'benchmark_transfer_decisions_target_benchmark_id_fkey',
        'benchmark_transfer_decisions_benchmark_submission_id_fkey'
    ] LOOP
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'benchmark_transfer_decisions'::regclass
              AND conname = constraint_name
        ) THEN
            EXECUTE format('ALTER TABLE benchmark_transfer_decisions DROP CONSTRAINT %I', constraint_name);
        END IF;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION sl_benchmark_transfer_ref_exists(
    reference_kind TEXT,
    reference_id UUID,
    fallback_goal_id UUID
) RETURNS BOOLEAN AS $$
DECLARE ok BOOLEAN;
BEGIN
    IF reference_id IS NULL THEN RETURN TRUE; END IF;
    IF reference_kind = 'goal' THEN
        SELECT EXISTS (SELECT 1 FROM goals WHERE id = reference_id) INTO ok;
        IF ok THEN RETURN TRUE; END IF;
        RETURN EXISTS (
            SELECT 1 FROM object_routes
            WHERE object_type = 'goal' AND object_id = reference_id AND home_shard_id <> 'K000'
        );
    END IF;
    IF reference_kind IN ('benchmark', 'submission') THEN
        IF reference_kind = 'benchmark' THEN
            SELECT EXISTS (SELECT 1 FROM benchmarks WHERE id = reference_id) INTO ok;
        ELSE
            SELECT EXISTS (SELECT 1 FROM benchmark_submissions WHERE id = reference_id) INTO ok;
        END IF;
        IF ok THEN RETURN TRUE; END IF;
    END IF;
    RETURN fallback_goal_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM object_routes
        WHERE object_type = 'goal' AND object_id = fallback_goal_id AND home_shard_id <> 'K000'
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION sl_check_benchmark_transfer_ref() RETURNS trigger AS $$
DECLARE reference_id UUID;
BEGIN
    reference_id := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
    IF NOT sl_benchmark_transfer_ref_exists(
        TG_ARGV[1], reference_id,
        CASE WHEN TG_ARGV[0] = 'source_benchmark_id' THEN NEW.source_goal_id::uuid
             WHEN TG_ARGV[0] IN ('target_benchmark_id', 'benchmark_submission_id') THEN NEW.target_goal_id::uuid
             ELSE NULL END
    ) THEN
        RAISE EXCEPTION 'benchmark transfer reference % = % is unavailable', TG_ARGV[0], reference_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE spec RECORD;
BEGIN
    FOR spec IN SELECT * FROM (VALUES
        ('source_benchmark_id', 'benchmark'),
        ('source_goal_id', 'goal'),
        ('target_goal_id', 'goal'),
        ('target_benchmark_id', 'benchmark'),
        ('benchmark_submission_id', 'submission')
    ) AS t(column_name, reference_kind) LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS tg_benchmark_transfer_ref_%s ON benchmark_transfer_decisions', spec.column_name);
        EXECUTE format(
            'CREATE TRIGGER tg_benchmark_transfer_ref_%s BEFORE INSERT OR UPDATE OF %I ON benchmark_transfer_decisions FOR EACH ROW EXECUTE FUNCTION sl_check_benchmark_transfer_ref(%L, %L)',
            spec.column_name, spec.column_name, spec.column_name, spec.reference_kind
        );
    END LOOP;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'benchmark_transfer_decisions'::regclass
          AND conname = 'benchmark_transfer_identity_chk'
    ) THEN
        ALTER TABLE benchmark_transfer_decisions
            ADD CONSTRAINT benchmark_transfer_identity_chk CHECK (
                length(trim(source_fingerprint)) > 0
                AND length(trim(target_fingerprint)) > 0
                AND length(trim(relation_fingerprint)) > 0
                AND length(trim(policy_version)) > 0
                AND tenant_id IS NOT NULL
            ) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'benchmark_transfer_decisions'::regclass
          AND conname = 'benchmark_transfer_visibility_v1_chk'
    ) THEN
        ALTER TABLE benchmark_transfer_decisions
            ADD CONSTRAINT benchmark_transfer_visibility_v1_chk CHECK (
                visibility IN ('public', 'private')
                AND (visibility <> 'private' OR NULLIF(BTRIM(owner_id), '') IS NOT NULL)
            ) NOT VALID;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_benchmark_transfer_decisions_key
    ON benchmark_transfer_decisions(transfer_key);

CREATE INDEX IF NOT EXISTS idx_benchmark_transfer_source
    ON benchmark_transfer_decisions(source_benchmark_id, target_goal_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_benchmark_transfer_target
    ON benchmark_transfer_decisions(target_goal_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_benchmark_transfer_tenant_source
    ON benchmark_transfer_decisions(tenant_id, source_benchmark_id, target_goal_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_benchmark_transfer_identity
    ON benchmark_transfer_decisions(tenant_id, relation_fingerprint, policy_version);

CREATE OR REPLACE FUNCTION benchmark_transfer_decisions_append_only()
RETURNS trigger AS $$
BEGIN
    IF current_setting('app.benchmark_transfer_compensation', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'benchmark_transfer_decisions is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_benchmark_transfer_decisions_append_only ON benchmark_transfer_decisions;
CREATE TRIGGER trg_benchmark_transfer_decisions_append_only
    BEFORE UPDATE OR DELETE ON benchmark_transfer_decisions
    FOR EACH ROW EXECUTE FUNCTION benchmark_transfer_decisions_append_only();
