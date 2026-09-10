-- Migration 53 (Ingestion + Knowledge hardening, Plan A / G10 / audit B6):
-- promote `procedure_implementations` from a thin link (procedure_id,
-- implementation_id, resource_path) into the first-class
-- ProcedureImplementation relation V4-hardening §B23 specifies.
--
-- Next free migration number: 54.
--
-- WHY
--   The table (migration 39) carried exactly one payload column,
--   `resource_path`, and was written only for `skill_package` bundled
--   scripts. It cannot express role (primary/supporting/partial/
--   verification), which steps an implementation covers, its applicability,
--   the evidence that it realizes THIS procedure well, or a version
--   constraint. `implementation_tasks` (migration 33, implementation <->
--   task_node) is a separate disconnected mechanism.
--
-- WHAT THIS IS NOT
--   Not a second registry. `implementations` (migration 33) stays the one
--   registry. This is relation metadata on the M:N edge between a
--   Procedure and an Implementation. Numeric coverage/quality scores are
--   NOT introduced -- `evidence_refs` points at real recorded evaluation
--   instead (§B23: "Do not invent numeric coverage/quality scores unless
--   they come from recorded evaluation").
--
-- Fresh-start rule: additive columns, nullable, NO backfill. Existing rows
-- keep resource_path and get role='primary' by DEFAULT (they were bundled
-- primary executables), status='active'.
-- Idempotent.

ALTER TABLE procedure_implementations
    -- resource_path was NOT NULL (skill-package writer always set it); a
    -- non-package relation row has no bundled path. Relax to nullable.
    ALTER COLUMN resource_path DROP NOT NULL;

ALTER TABLE procedure_implementations
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'primary'
        CHECK (role IN ('primary', 'supporting', 'partial', 'verification')),
    -- exact version row, OR a family + constraint string ('>=1.2,<2').
    ADD COLUMN IF NOT EXISTS implementation_version   INTEGER,
    ADD COLUMN IF NOT EXISTS implementation_version_constraint TEXT,
    -- which procedure step ids / capability names this implementation
    -- realizes. Empty = the whole procedure.
    ADD COLUMN IF NOT EXISTS supported_steps          JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS supported_capabilities   JSONB NOT NULL DEFAULT '[]',
    -- hard constraints on when this binding is usable (env, platform,
    -- credentials present). Same shape as ApplicabilityRule.conditions.
    ADD COLUMN IF NOT EXISTS applicability            JSONB NOT NULL DEFAULT '{}',
    -- how the host actually calls it for this procedure (arg mapping,
    -- entrypoint override). Distinct from implementations.invocation,
    -- which is the implementation's own generic contract.
    ADD COLUMN IF NOT EXISTS interface_binding        JSONB NOT NULL DEFAULT '{}',
    -- pointers to `evidence` rows about how well THIS implementation
    -- realizes THIS procedure (evidence.target_type='implementation' with
    -- context_key naming the procedure). A list of evidence ids.
    ADD COLUMN IF NOT EXISTS evidence_refs            JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS status                   TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('candidate', 'active', 'deprecated', 'disabled', 'quarantined')),
    ADD COLUMN IF NOT EXISTS ingestion_context_id     UUID,
    -- Bi-temporal: a binding is closed when the procedure re-authors its
    -- implementation set or the implementation is retired for it.
    ADD COLUMN IF NOT EXISTS t_valid                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS t_invalid                TIMESTAMPTZ;

-- The old UNIQUE (procedure_id, implementation_id) forbade the same
-- implementation supporting one procedure in two roles (e.g. primary for
-- steps 1-3, verification for step 4). Widen it to include role.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
               WHERE conname = 'procedure_implementations_procedure_id_implementation_id_key') THEN
        ALTER TABLE procedure_implementations
            DROP CONSTRAINT procedure_implementations_procedure_id_implementation_id_key;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_procedure_implementations_identity
    ON procedure_implementations (procedure_id, implementation_id, role)
    WHERE t_invalid IS NULL;

-- "candidate implementations for this procedure, live, by role".
CREATE INDEX IF NOT EXISTS idx_procedure_implementations_by_role
    ON procedure_implementations (procedure_id, role) WHERE t_invalid IS NULL;
-- reverse: "every procedure this implementation supports" (one impl,
-- many procedures -- §B23's Graphify example).
CREATE INDEX IF NOT EXISTS idx_procedure_implementations_reverse
    ON procedure_implementations (implementation_id) WHERE t_invalid IS NULL;
