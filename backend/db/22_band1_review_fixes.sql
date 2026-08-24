-- Band 0 review fixes (BAND0_REVIEW.md): defense-in-depth + deduplication.
-- Idempotent; fresh-start compliant (no data backfills).

-- ============================================================
-- Review nit 6: CHECK constraints behind the V0 gate. The service
-- layer owns error messaging (v0_gate.py); the engine refusing
-- garbage costs nothing and protects direct-SQL paths (backfills,
-- maintenance tools) that bypass the service boundary.
-- ============================================================
DO $$
BEGIN
    -- One explicit block per table so constraint names are greppable
    -- and statically checkable (tests read this file as text).
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_knowledge_nodes'
    ) THEN
        ALTER TABLE knowledge_nodes ADD CONSTRAINT scope_type_chk_knowledge_nodes
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_task_nodes'
    ) THEN
        ALTER TABLE task_nodes ADD CONSTRAINT scope_type_chk_task_nodes
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_edges'
    ) THEN
        ALTER TABLE edges ADD CONSTRAINT scope_type_chk_edges
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_procedures'
    ) THEN
        ALTER TABLE procedures ADD CONSTRAINT scope_type_chk_procedures
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_observations'
    ) THEN
        ALTER TABLE observations ADD CONSTRAINT scope_type_chk_observations
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_episodes'
    ) THEN
        ALTER TABLE episodes ADD CONSTRAINT scope_type_chk_episodes
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_agent_traces'
    ) THEN
        ALTER TABLE agent_traces ADD CONSTRAINT scope_type_chk_agent_traces
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

-- ============================================================
-- Review nit 5: duplicate validity columns. t_valid / t_invalid on the
-- graph tables ARE the canonical worldly-validity window (bi-temporal
-- convention: t_valid/t_invalid = when true in the world,
-- t_created/t_expired = when the system learned it -- backend README).
-- valid_from / valid_until added in migration 21 duplicated that pair;
-- under the fresh-start ruling nothing references them yet. Claim
-- validity (spec §7) maps onto t_valid / t_invalid, full stop.
-- ============================================================
ALTER TABLE knowledge_nodes DROP COLUMN IF EXISTS valid_from;
ALTER TABLE knowledge_nodes DROP COLUMN IF EXISTS valid_until;
