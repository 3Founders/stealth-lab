# Review: step 6 (CI workflow histories + dependency-bump PRs)

2026-09-29. Light review done inline (the delegated reviewer stopped at the usage limit before writing).

## Verdict: done as built; Source A blocked on a license ruling; one stale parallel attempt to clean up

- **Committed attempt `374eddc`** is complete: research, `step_6_SUMMARY.md`, 10 files (`ci_workflow_history.py`,
  `bot_dependency_prs.py`, `workflow_knowledge.py`, `held_out.py`, `step6_admin.py`, admin wiring), and
  `tests/test_step6_sources_offline.py` passes (part of a 258-pass run on 2026-09-29, `DATABASE_URL` unset).
- **Source A (Zenodo workflow histories, CC-BY-4.0) ingests zero items by design** until
  `Q-STEP6-CCBY.md` is ruled on. `repo_license_policy.DEFAULT_ALLOWLIST` has 8 ids and no CC-BY; the measured
  1,000-item pilot returned `QUARANTINE: 1000`. The safe default is correct.
- **Cross-step impact of that ruling:** CC-BY-4.0 also covers the nebius SWE-rebench OpenHands trajectories
  (steps 0/1) and the SkillMD-138K compilation (step 3; its files carry per-repository licenses, so it is less
  affected). A single founder ruling (allow CC-BY-4.0 with the attribution recorded in provenance and shown
  where served, or keep it quarantined) unblocks several steps at once.

## Stale parallel attempt (untracked; not part of the commit)

- `backend/app/services/ingestion_sources/gh_client.py` and `backend/tests/test_step6_ingestion_sources_offline.py`
  come from a second, abandoned step-6 build. The test file expects a different `bot_dependency_prs` API than
  the committed module, so **all 41 of its tests fail**, and no committed code imports `gh_client`.
- **Recommendation:** delete both, or, if `gh_client.py` has something the committed code lacks (e.g. a
  shared, rate-limited GitHub client for steps 3 and 6), fold it in and delete the stale test. It was not
  deleted here: untracked files can't be recovered, and deleting them is the user's call.

## Not re-checked in depth
Token handling, GitHub API budget math, and actionlint/zizmor attachment (step 4, the check runner, is not built)
were not re-verified line by line in this pass. The step's own summary covers them.
