-- 201_procedure_approval_tiers.sql
--
-- Next free number: 200 (goal tool rankings) was highest before this file.
--
-- What this adds: a tier below `proposed` for procedures that have been submitted but not reviewed.
-- The ladder is now:
--
--     submitted   just arrived (the tool-card ways materialized by scripts/materialize_tool_procedures.py)
--     proposed    reviewed and proposed for approval
--     approved    routable / executable
--     rejected    refused
--
-- The router shows every non-rejected tier with its label; the procedure runner only executes
-- `approved`. New procedures default to `submitted`; the submission pipeline promotes them.
--
-- Idempotent.

ALTER TABLE procedures DROP CONSTRAINT IF EXISTS procedures_approval_status_check;
ALTER TABLE procedures ADD CONSTRAINT procedures_approval_status_check
    CHECK (approval_status IN ('submitted', 'proposed', 'approved', 'rejected'));

ALTER TABLE procedures ALTER COLUMN approval_status SET DEFAULT 'submitted';

-- The generated tool-card ways were inserted as `proposed` before this tier existed; they have not
-- been reviewed, so they belong at the bottom.
UPDATE procedures
SET approval_status = 'submitted', updated_at = now()
WHERE provenance = 'tool_card'
  AND t_invalid IS NULL
  AND approval_status = 'proposed';
