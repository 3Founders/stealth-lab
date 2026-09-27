-- Migration 124: a Procedure version may carry the verified solution it was extracted from.
--
-- docs/knowledge_side_improvements.md (changes 1-3): every measured knowledge gain came from verified
-- code shown as a worked example, which the Procedure (steps + APIs + pitfalls) deliberately drops.
-- Following migration 97's rule (no separate Implementation object; Procedure knowledge lives on the
-- procedure row), the example is a JSONB column on `procedures`, so it lives with its Procedure on the
-- Procedure's home shard and inherits its visibility/owner/tenant.
--
-- Shape (validated by app/services/verified_examples.py before any write):
--   {"task": str, "code": str, "language": str, "verified_by": str, "recorded_at": iso8601,
--    "redacted": [pattern names], "truncated": bool}
-- NULL = no verified example (every existing row). Written only when KNOWLEDGE_VERIFIED_EXAMPLES is on.
-- Next free number: 125. Idempotent.

ALTER TABLE procedures ADD COLUMN IF NOT EXISTS verified_example JSONB;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'procedures_verified_example_chk') THEN
        ALTER TABLE procedures ADD CONSTRAINT procedures_verified_example_chk
            CHECK (verified_example IS NULL OR (jsonb_typeof(verified_example) = 'object'
                                                 AND verified_example ? 'code' AND verified_example ? 'task'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_procedures_has_verified_example
    ON procedures (achieves_goal_id) WHERE verified_example IS NOT NULL AND t_invalid IS NULL;
