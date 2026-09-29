-- Migration 125: `procedures.verifier_check` — a Procedure that carries a runnable, checkable outcome.
--
-- WHY A NEW COLUMN AND NOT `screening.CHECK_TYPES` (board question Q-STEP4-1):
--   `screening_decisions.check_type` is 1:1 with `check_type_chk_screening_decisions`
--   (db/68_screening_decisions.sql) and records *admission screening of untrusted source text* --
--   "we quarantined this document: prompt_injection / pii / license". A verifier verdict is a different
--   fact about a different subject (our own derived knowledge) at a different moment (verification, not
--   admission). Putting actionlint/zizmor/ast_grep there would make a screening row indistinguishable
--   from a verification verdict and would corrupt claim_publication.py's reporting over blocking findings.
--   It would also require a spec + schema change, and the spec is frozen.
--
-- WHY NOT `procedures.postconditions`:
--   `execution/verification.py:89-100` accepts only a string or `{statement, required}` and builds
--   Criterion objects from exactly those. A `verifier` key stored there would be SILENTLY DROPPED by
--   derive_criteria -- a silent-drop hazard on the one field that decides verification.
--
-- WHY NOT `routing.CHECK_KINDS`:
--   `routing/config.py:23-34` defines that vocabulary as "how much to distrust an OUTCOME" (a Beta prior
--   on false-accept/false-reject), and `procedure_check` already exists there. The three verifiers are the
--   IMPLEMENTATION of `procedure_check`, not new kinds.
--
-- MUTABILITY: [D] -- derived. It is a function of the tool version, the config knobs and the rule, all of
--   which are already recorded here; nothing else depends on it. Append-then-close semantics still apply
--   (a re-ingest supersedes via `version`, never by overwriting a check in place).
--
-- The gate and its writer land in the SAME change (hard rule 6, half-gate rule): migration 125 ships with
-- `app/services/check_runner.py` and `app/services/ast_grep_rules.py`, the only two writers.
--
-- Next free number: 126. Idempotent.

ALTER TABLE procedures ADD COLUMN IF NOT EXISTS verifier_check JSONB;

-- The shape the runner stores. Deliberately strict about the two fields that make a verdict auditable:
-- a pinned `tool_version` (an unpinned verdict is not evidence) and a `check_type` drawn from the closed
-- vocabulary `app/services/check_runner.py::VERIFIER_CHECK_TYPES`.
-- `expected_case_count` is the liveness assertion: ast-grep `test` exits 0 with "0 passed; 0 failed" when
-- a test file names a rule id that is not loaded, so a pass must be able to prove cases actually ran.
ALTER TABLE procedures DROP CONSTRAINT IF EXISTS procedures_verifier_check_chk;
ALTER TABLE procedures ADD CONSTRAINT procedures_verifier_check_chk
    CHECK (
        verifier_check IS NULL
        OR (
            jsonb_typeof(verifier_check) = 'object'
            AND verifier_check ? 'check_type'
            AND verifier_check ? 'tool_version'
            AND verifier_check ? 'source'
            AND verifier_check->>'check_type' IN ('actionlint', 'zizmor', 'ast_grep')
            AND length(verifier_check->>'tool_version') > 0
            AND length(verifier_check->>'source') > 0
            AND (
                NOT verifier_check ? 'expected_case_count'
                OR (
                    jsonb_typeof(verifier_check->'expected_case_count') = 'number'
                    AND (verifier_check->>'expected_case_count')::int > 0
                )
            )
        )
    );
