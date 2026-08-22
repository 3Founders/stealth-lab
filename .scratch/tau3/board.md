# τ³-Banking coordination board

Claims protocol: claim a ticket by writing `- [x] <ticket> @<agent> <timestamp>` under
Claims. Update Status when done. Never edit another agent's claimed files without a note here.

## Live runs (auto-resume safe)

| save-to | config | timeout | PID | state |
|---|---|---|---|---|
| phaseD_stealthlab_gemma | stealthlab_procedures | 1200s | 39160 | 241/388, mean 0.017 |
| phaseE_stealthlab_bm25_gemma | stealthlab_bm25 | 1200s | 45612 | 31/388, mean 0.161 |

## Claims

- [x] A1a prompt reframe (3 prompt files) @stealthlab-8c 2026-08-22 — DONE
- [x] Phase1a top_k 5→18 @stealthlab-8c 2026-08-22 — DONE
- [x] stealthlab_bm25 / stealthlab_alltools variants @stealthlab-8c 2026-08-22 — DONE
- [x] A1b structured bridge rendering (GOAL/ELIGIBILITY/STEPS/WATCH OUT) + substrate_get(topic) @ox-alpha 2026-08-22 — DONE (verified vs live DB; all 3 substrate toolkits expose both tools)
- [ ] A2 doc→procedure compiler (seed v2) @ox-alpha
- [ ] A5 hybrid RRF inside bridge @ox-alpha (phaseE result informs design)
- B-tickets unclaimed

## Status log

- 2026-08-22 (ox-alpha): plan written to 0xAlphaplan.md; phaseD autopsy done (see that file).
  phaseD mean 0.017 confirms substrate-only arm dead; phaseE early signal 0.161 validates
  hybrid retrieval. Starting A1b then A2.
- 2026-08-22 (ox-alpha): A1b landed in stealthlab_bridge.py + retrieval_mixins.py +
  retrieval_toolkits.py. Render now leads with imperative GOAL, surfaces ELIGIBILITY before
  STEPS, keeps [tool:] bindings, closes with WATCH OUT. New substrate_get(topic) = ILIKE
  exact fetch (no embedding call). NOTE for running sweeps: phaseD/phaseE processes hold the
  OLD code in memory — these changes take effect on the NEXT run (phaseF+).
- 2026-08-22 (ox-alpha): A2 scoping done. Two grounded sources confirmed:
  (1) 698 docs — 98% have ## sections, 34% mention eligibility, 20% numbered steps;
  tool NAMES appear in only ~6% of docs (my earlier "docs name tools" assumption was wrong);
  (2) tools.py registers ~47 @is_discoverable_tool agent tools + user tools, each with a
  docstring parseable via parse_discoverable_tool_docstring() -> description + params.
  DESIGN: ## section -> step; eligibility bullets -> preconditions; lexical join
  (doc title/content tokens <-> tool descriptions) -> allowed_implementations bindings.
  Deterministic end-to-end, no LLM, provenance company_ingested.
- A2 EXECUTION CONSTRAINT: phaseD + phaseE substrate_search hits the SAME live procedures
  table — inserting v2 rows (or updating v1) mid-sweep contaminates both arms. Write +
  dry-validate the compiler now, EXECUTE only after both sweeps exit. New-arm config then
  points at v2-only rows (e.g. scope tag or created_by filter).
