# Ingestion review: steps 0-8 (2026-09-29)

A granular review of the ingestion build (`.scratch/prompts/ingestion_build_prompts.md`) against its acceptance
criteria. Sources: each step's `.scratch/ingestion/step_N_SUMMARY.md`, the read-only reviews
(`review_step_{2,3,6,7}.md`), the code, and test runs on 2026-09-29 (`DATABASE_URL` unset). Blockers are in
`BLOCKERS.md`.

**Bottom line:**
- The builders did careful, well-researched work, and every step's offline tests pass (337 across steps 2, 3, 4,
  6 and 7; 144 across steps 0 and 4).
- **No step has produced verified knowledge on a real run yet.** The pilots stopped on shared blockers:
  embeddings (I1), extraction parsing (I3/I4), shard references (I5) and the migration order (I6).
- None of these blockers is a flaw in a step's own design.

## Status by step

| Step | Built | Tests | Real run | Verdict |
|---|---|---|---|---|
| 0: 1,000 SWE-rebench OpenHands trajectories | Chat-message normalizer, pilot CLI, license join, held-out gate. **Untracked.** | 37 proving tests (part of 144 passed) | 1,000 streamed → **713 accepted**, 271 rejected (135 duplicate in run, 64 held-out repo, 35 license unmappable, 27 quarantine, 10 held-out instance). **Extraction yield 0**: 13/13 calls failed, $0.20 | Normalizer and gates correct. Blocked on I3 (prompt/schema drift). |
| 1: full trajectories (~32k) | Not started | — | — | Waits on step 0's yield. |
| 2: verified solutions | Reader, normalizer, license, held-out, quality gates (`e26310f`); job wiring (`58026bd`). **This review added:** gold patch preserved as a `verified_solution` artifact, and HF iteration off the event loop | 70 + 21 passed (6 new) | 500 admitted per source for extra, V1 and V2; **SWE-Gym 0** (2,029 license-unmappable). 10 jobs enqueued → **0 done** (Voyage, I1) | Good. The previously missing core (`preserve()` had no callers) is fixed. Ready once I1 is cleared. |
| 3: SkillMD-138K | Reader, per-repo license (mirror-aware), dedup, injection screen, pilot (`985559c`) | 67 passed | 30-item compile: **1 accepted**; losses from migration 125 missing (54/60) and cross-shard refs (11/30) | Strong research (found 3 errors in the build prompt). Blocked on I1/I5/I6. |
| 4: verifiers as check types | `check_runner.py` (actionlint, zizmor, ast-grep; pinned, isolated argv), ast-grep rules, migration 125. **Untracked.** | part of 144 passed; 9 isolation-flag tests | Local-shard run, no production writes | Solid security contract. Must commit 125 with its code (I6), and resolve the two-runners question (I9). |
| 5: SWE-agent + SWE-smith trajectories | Not started | — | — | Waits on step 0's normalizer and yield. |
| 6: CI histories + bot PRs | Committed `374eddc` | tracked file passes; untracked duplicate fails 41 | Source A: **0/1,000** (CC-BY-4.0 quarantined, I7). PR scrape partial (time budget, not code) | Done as built. Blocked on I7. Clean up the duplicate attempt (I11). |
| 7: codemods | Node + OpenRewrite adapters, own check runner, CLI. **Untracked.** | 31 proving tests | Node: **39 accepted, 38/39 checks pass**. OpenRewrite: static check only | Node side is ready. OpenRewrite needs I8. Possible runner duplication (I9). |
| 8: sampled large sources | Not started | — | — | Waits on step 0's yield table. |

## Defects found and fixed in this review

| Severity | Where | Problem | Fix |
|---|---|---|---|
| High | `verified_solutions_jobs.py` | Gold patch and FAIL_TO_PASS never stored as a structured artifact; `verified_solutions.preserve()` had zero callers | Payload carries patch/F2P/problem; the handler calls `preserve()` on `captured`/`new_version` (6 proving tests) |
| High | `verified_solutions_jobs.py:160` | Sync HF generator (network I/O) iterated inside `async def` | Advanced via `run_blocking(next, …)` |
| High | `skillmd_pilot.py:106,110` | `discover()`/`fetch()` (network, thread pool, sleeps) on the event loop | `run_blocking` (included in `985559c`) |
| High | `codemod_cli.py:242` | `adapter.fetch` → `subprocess.run` (≤120 s) and `httpx.get` inside `async def` | `run_blocking` (working tree; commit with step 7) |
| High | `claim_extraction.py:373` | Bare `json.loads` dropped every fenced reply: silent zero claims, for every source | `_parse_json_object` (`cc455d8`, 8 tests) |

## Open defects (not fixed here)

| Severity | Where | Problem |
|---|---|---|
| High | `trajectory_semantics._SYSTEM_PROMPT` | Prompt fields ≠ schema fields (I3). The owner is editing this file. |
| Medium | `skill_extraction` | Same "did not parse" class as the claim extractor |
| Medium | `skillmd_dataset.py:519-534` | Fetch stage submits the whole ×12 oversampled batch before the limit check, ~5× more fetches than the yield needs |
| Medium | Step 7 OpenRewrite | Static tier only; `license_metadata["commit"]` always `None`; checks run in place against the pinned checkout |
| Medium | Steps 4 and 7 | Two check runners with different isolation contracts (I9) |
| Low | Step 2 dry-run script | `--max-scan` counts accepted rows, not rows seen |
| Low | Step 3 | No spend cap wired into the pilot path |

## Cross-cutting lessons
1. **Every step independently hit "blocking call inside async".** A multi-level static check (async → sync →
   blocking) belongs in CI; the current scanner only follows one level.
2. **Migrations moving under a running pilot** (step 3 vs step 4) caused 54/60 failures. Run
   `migrate.py --status` at the start of every run, and commit migrations with the code that uses them.
3. **License gates work as designed and cut deep:** SWE-Gym 2,029/2,438 unmappable, step 6 Source A 0/1,000.
   The CC-BY-4.0 ruling (I7) is the largest single lever on usable volume.
4. **Research quality is high.** Several builders corrected the build prompt itself: SkillMD mirrors, the nebius
   trajectory format, OpenRewrite licensing, and codemod fixtures proving self-consistency rather than correctness.

## Recommended order once blockers clear
1. Fix I1/I2 (Gemini embeddings, paid tier) and rerun step 2's 10-item pilot. It is fully built.
2. Commit steps 0, 4 and 7 with migration 125 (I6, I10), and resolve I9.
3. Fix I3, rerun step 0's 13-call probe, then its 1,000-item yield measurement (this sets steps 1, 5 and 8).
4. Get the I7/I8 rulings, then step 6 Source A and OpenRewrite.
5. Scale per step within the $2/day caps (I12).
