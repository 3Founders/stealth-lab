-- Registers the medical report extraction agent as a real row in the
-- Agent Store. It was built, shipped, and used in production well
-- before the Agent Store existed, and its endpoint
-- (POST /v1/agents/medical-report-extraction/run) never touched this
-- table -- it builds its own SkillRegistry inline. Without this insert,
-- the store has nothing real to search over.
--
-- source='internal': already trusted by construction, same reasoning as
-- AGENT_STORE_PLAN.md Section 4 -- an internal, already-shipped,
-- already-verified agent doesn't need to go through the
-- ingested -> under_review -> pending_human_approval flow as if newly
-- submitted. review_state and runnable are set directly.
--
-- skill_ref is a descriptive identifier here, not a literal
-- SkillRegistry lookup key -- this agent is actually served by its own
-- dedicated endpoint (composing two skills, extraction then Excel
-- generation), not the generic single-skill ExecutionHarness path a
-- true local_skill agent would use. Noted here so it isn't mistaken for
-- one later.
--
-- Idempotent: guarded by a WHERE NOT EXISTS check, since `agents` has
-- no unique constraint on name to ON CONFLICT against.

INSERT INTO agents (
    name, description, source, execution_mode, skill_ref,
    review_state, runnable, created_by
)
SELECT
    'Medical Report Extraction',
    'Upload one or more medical lab report PDFs and receive a combined '
    'Excel file with every field, its value, unit, and reference range, '
    'correctly typed as real numbers where applicable.',
    'internal', 'local_skill', 'medical_report_extraction_pipeline',
    'approved', TRUE, 'system_seed'
WHERE NOT EXISTS (
    SELECT 1 FROM agents
    WHERE name = 'Medical Report Extraction' AND t_invalid IS NULL
);
