# Review: ingestion Step 2 — verified solutions (SWE-rebench / -V2 / -extra / SWE-Gym)

Reviewer: read-only pass. Scope: commit `e26310f`, `.scratch/ingestion/step_2_research.md` +
`step_2_SUMMARY.md`, and the uncommitted
`backend/app/services/ingestion_sources/verified_solutions_jobs.py` +
`backend/tests/test_verified_solutions_jobs_offline.py`. Diffs to `admin.py` / `queue.py` /
`ingestion_jobs.py` / `trace_worker.py` were inspected; **the `trace_worker.py` /
`test_trace_payload_cap.py` changes are an unrelated batching optimization by another
concurrent session** (confirmed by the "another agent's WIP" comment left in `admin.py`'s
diff for `skillmd_cli`) and are out of scope for this review.

## Verdict: **good with fixes**

The reader, license normalizer, held-out gate, dedup and structural quality gates are
correct, well-researched, and genuinely tested. The one real defect is architectural, not
cosmetic: **the job handler never calls `verified_solutions.preserve()`**, so the thing
Step 2's own docstring and the build prompt both promise — "gold patch → the verified
solution ... using the existing `verified_solution` + `source_locator` model" — does not
happen. The patch reaches storage only as markdown text inside a generic LLM-compiled
document. There is also one real hard-rule violation (blocking network I/O inside `async
def`) in the uncommitted enqueue path. Neither is a redo; both are bounded, well-scoped
fixes on top of otherwise solid work.

---

## Findings, ranked by severity

### 1. [HIGH] The verified-solution artifact model is never used — `preserve()` is dead code for this pipeline

- `backend/app/services/ingestion_sources/verified_solutions_hf.py:9-11` (module docstring):
  *"Each accepted row becomes a Procedure whose check is the F2P predicate, with the gold
  patch preserved through `verified_solutions.preserve()`."*
- `backend/app/services/ingestion_sources/verified_solutions_jobs.py:39-110`
  (`handle_ingest_verified_solution`): builds a `SourceArtifact` and calls
  `compile_skill_artifact(...)` — nothing else. No import of, or call to,
  `app.services.verified_solutions.preserve`.
- `backend/app/services/verified_solutions.py:52` (`preserve`) has **zero callers** anywhere
  under `backend/app/` outside its own module (`grep -rn "verified_solutions.preserve"` only
  matches the docstring sentence above, not a call site). `SOURCE_TYPE` is imported into the
  new handler purely to tag `SourceArtifact.source_type`; `solution_ref`/`resolve` (used by
  `retrieval_service.py:1371` and `find_ways`) can never find anything this pipeline writes,
  because nothing this pipeline writes goes through `preserve()`.

What actually happens instead: `build_skill_document()`
(`verified_solutions_hf.py:478-539`) embeds the gold patch and test patch as fenced
```diff``` blocks inside a synthesized SKILL.md-shaped document, and that whole document is
handed to `compile_skill_artifact`, the same **generic, LLM-driven** extractor used for
skills, docs, and CI histories. The patch is not preserved byte-for-byte with
`execution_allowed: false` provenance (`ingested_artifacts` role `verified_solution`); it is
prose the extraction model reads and re-derives Procedure steps/Claims/Goals from. This is
exactly the failure mode `verified_solutions.py`'s own docstring exists to prevent (`"Code
committed somewhere durable is pointed at, never copied"`).

Consequence for the step's own stated goal ("checkable knowledge... tests → the Procedure's
check"): the FAIL_TO_PASS list similarly only exists as a bullet list under a `## Check`
markdown heading (`verified_solutions_hf.py:514-522`), not as a structured check the routing
loop (`screening.CHECK_TYPES`) can run. Nothing wires it into an actual executable check
type. So the two headline claims of Step 2 — a durable verified-solution artifact, and a
checkable Procedure — are both currently aspirational text inside a document, not wired
data.

**Concrete fix**: in `handle_ingest_verified_solution`, after `compile_skill_artifact`
returns `outcome` with `status in ("captured", "new_version")`, call
`verified_solutions.preserve(pool, procedure_row_id=outcome.procedure_id, code=<row.patch>,
task=<row.problem_statement, truncated>, language=<row.language>, locator={"repository":
repo, "commit": base_commit, "path": f"instances/{instance_id}", "uri": payload["uri"]},
owner_id=..., visibility=...)` for `outcome.procedure_id` and any ids in
`outcome.script_procedure_ids` / `outcome.reference_procedure_ids`. The gold patch and test
patch need to travel in the payload for this (they currently do not — only the synthesized
`content` markdown is enqueued; see Finding 3). This needs its own proving test (a fake
`compile_skill_artifact` returning a `procedure_id`, asserting `preserve` was called with the
right `code`/`locator`) before it can be called wired rather than asserted.

### 2. [MEDIUM] Blocking network I/O runs inside `async def enqueue_verified_solution_jobs` — hard rule 2026-09-28 build-prompt rule, and CLAUDE.md's own async-blocking rule

- `backend/app/services/ingestion_sources/verified_solutions_jobs.py:127` declares `async def
  enqueue_verified_solution_jobs(...)`.
- Line 160: `for row, content in source.iter_admissible():` — a **synchronous** generator
  (`VerifiedSolutionSource.iter_admissible`, `verified_solutions_hf.py:596-614`) that pulls
  from `datasets.load_dataset(..., streaming=True)` (`verified_solutions_hf.py:585-594`),
  i.e. real HTTP calls to the Hugging Face Hub, executed synchronously on every `next()`.
- This loop runs directly on the event loop for the entire gating pass — for the real
  27.9k/32.1k/6.4k/2.4k-row corpora that is tens of minutes of blocking network I/O with
  `await enqueue(...)` calls interleaved, on the same single-worker process the
  `run_blocking` docstring (`backend/app/utils/aio.py:1-9`) explicitly says must not be
  blocked ("The MCP server is one process with one event loop... freezes that loop for the
  whole call... every other request on the process waits").
- Note: `backend/app/services/ingestion_sources/dispatch.py:95-96` has the identical pattern
  (`for ref in adapter.discover(): artifact = adapter.fetch(ref)` inside `async def`) for the
  OpenHands filesystem adapter, so this is a pre-existing gap in the codebase, not one Step 2
  invented from nothing — but dispatch.py's blocking cost is local disk I/O on a handful of
  files; this one is real network I/O over the full corpus, and it is new code that had a
  clean opportunity to do it correctly. `step_2_research.md` §5 does correctly identify
  `SourceAdapter` as "a **sync** Protocol" — that's true of the interface, but the enqueue
  loop that *drives* the sync interface from async code is the caller's responsibility to
  thread off the loop, and it does not.

**Concrete fix**: wrap the scan in `asyncio.to_thread`/`run_blocking`. Simplest shape:
collect admissible rows for the batch in a thread-run helper (`def _scan_batch(source,
target): return list(itertools.islice(source.iter_admissible(), target))` called via `await
run_blocking(_scan_batch, source, target)`), then do the `await enqueue(...)` calls from the
async function over the materialized list. This does lose the "stop as soon as `target` is
reached without buffering the rest of a partially-scanned batch" property only if `target` is
large; `itertools.islice` still stops the generator at `target`, so no correctness change,
just moved off the loop.

### 3. [LOW] `fetch(ref)` re-scans the whole corpus per call — documented, but a footgun for any future caller outside the enqueue path

- `verified_solutions_hf.py:625-649` (`VerifiedSolutionSource.fetch`): re-runs
  `iter_admissible()` from the start for every single `ref`, explicitly to keep the dedup
  state pass-local (comment at line 626-629 is honest about this). `discover()` at line
  616-623 does the same for every yielded ref if a caller then calls `fetch()` per ref — an
  O(n²) shape over a 32k-row corpus.
- This is *not* currently exercised on the real corpora: `enqueue_verified_solution_jobs`
  (the only production-shaped caller) uses `iter_admissible()` directly and puts the
  already-synthesized `content` straight into the job payload (`verified_solutions_jobs.py:
  160-182`), correctly avoiding the quadratic path, and the module's own docstring explains
  why. But `discover()`/`fetch()` still satisfy `SourceAdapter` and nothing stops a future
  caller (a generic `dispatch.py`-style walker, or a test helper) from using them the naive
  way and getting a very slow, very expensive-in-HF-bandwidth surprise. Worth a one-line
  docstring warning on `discover()` itself (it currently only appears on `fetch()`), or
  raising if `discover()`+`fetch()` are used together above some row count.

### 4. [LOW] Reported metrics are real and reproducible, but no raw log artifact is committed

- `step_2_SUMMARY.md` §4's metrics table is **not asserted from nothing** — I re-ran
  `backend/scripts/verified_solutions_dryrun.py --source swe_bench_extra --target 5
  --max-scan 200` live against the pinned revision and got a consistent, real result (5/5
  ALLOW, all python, real byte counts, ~6s/item over the network) confirming the pipeline
  genuinely streams and gates real rows rather than fabricating numbers. But the actual
  500-per-source run whose exact figures are quoted in the SUMMARY (e.g. "103/705 V1 rows
  carry FAIL_TO_FAIL", "V2 costs ~80.6 KB/item") has no saved stdout/JSON artifact anywhere
  in the repo (`.scratch/ingestion/` has only the two markdown files) — so the specific
  numbers are trustworthy but not independently auditable from disk. Minor: save
  `verified_solutions_dryrun.py --all --target 500 > .scratch/ingestion/step_2_dryrun.json`
  output alongside the summary next time.

### 5. Not a defect, but worth surfacing: `max-scan` in the dry-run script is effectively dead for low-yield corpora

- `backend/scripts/verified_solutions_dryrun.py:39-60` (`scan`): `scanned` is only
  incremented inside the loop body, which only runs for rows `iter_admissible()` *yields*
  (i.e. already-accepted rows) — so the `--max-scan` safety cap never fires for a corpus with
  a low or zero acceptance rate (exactly SWE-Gym's shape: 0/2,438 accepted here). It happened
  to be harmless in this run because every corpus is ≤32k rows, but if this script is ever
  pointed at a much larger near-all-reject corpus it will scan unboundedly despite
  `--max-scan` implying a cap. Fix: increment the scan counter from
  `counters.rows_seen` (already tracked accurately inside `GateCounters`,
  `verified_solutions_hf.py:389`) rather than from the accepted-row loop body.

---

## Acceptance criteria (build-prompt Step 2) — status

| Criterion | Status |
|---|---|
| 500 items/source on a local shard with metrics table | **Gating-only, verified real** (see Finding 4): SWE-bench-extra/V1/V2 each hit 500 accepted, SWE-Gym 0/2,438, on live pinned-revision data. No DB write happened — by design, per the SUMMARY's own §6, because the LLM client isn't configured and a real write would spend money without a measured $/item first. This is an honest, deliberate partial, not a fabricated pass. |
| Proving tests: reader, mapping, license gate, dedup, held-out exclusion | **Met.** 70 tests in `test_verified_solutions_hf_offline.py`, independently re-run here: 70 passed. Plus 15 tests in the uncommitted `test_verified_solutions_jobs_offline.py` for the wiring layer, also re-run here: 15 passed. |
| V2 language/repo breakdown | **Met.** 19/20 languages in the 500-accepted sample, table in SUMMARY §4, structurally verified against `row_to_verified_solution_row`'s `language` field handling. |
| Full-run command + projection | **Partial, honestly flagged.** SUMMARY §8 gives the scan-side command and corpus sizes but explicitly refuses to project $/item ("unknown until the pilot — do not quote a number yet") because the LLM compile cost is unmeasured. Correct posture per the build prompt's spend rules. |
| "Verified solution artifacts used correctly" (per this review's own checklist item) | **Not met** — see Finding 1. This is the one criterion from the review brief, not the build prompt verbatim, but it is exactly what the build prompt's Step 2 §Build-1 asks for ("mapping to `verified_solution` artifacts... using the existing `verified_solution` + `source_locator` model"), and it is not wired. |

## Correctness checklist (from the review brief)

- License decided per task via the SPDX allowlist, NOASSERTION/missing rejected: **correct**
  (`normalize_spdx` + `classify_spdx`, `_NON_IDENTIFYING` frozenset includes `noassertion`,
  `""`, `none`, `null`; `custom-check-github` correctly treated as non-identifying, not a
  license).
- Held-out exclusion uses `experiments/swebench*/runs/design.json` test+calibration ids:
  **correct and verified against the real files** — `test`=193, `calibration`=12 in each of
  the two design files present, `scored_repos` includes `django/django`, and the repo-level
  exclusion default (`include_repos=True`) is exercised by the tests.
- Dedup key sound: **correct** — `(repo, base_commit)`, documented rationale (two PRs landing
  on the same base commit are one target), tested.
- Provenance in `source_locator` (source, revision, instance id, license, extractor version):
  **structurally present on the `SourceArtifact`** (commit, path, source_id,
  license_metadata) but — per Finding 1 — never reaches `procedures.source_locator` via
  `durable_locator`/`preserve`, so this provenance dies at the `SourceArtifact` boundary
  rather than landing where `find_ways` reads it.
- No blocking calls inside `async def`: **violated**, see Finding 2.
- `tenant_transaction`/`scope_predicates`: N/A to this module — `enqueue()` (pre-existing,
  unmodified by Step 2 except the `PUBLIC_ONLY_JOB_TYPES` addition) writes via
  `pool.fetchrow` directly, which is queue.py's established (pre-existing) pattern, not a
  Step-2 regression.
- Spend recorded: **correct as far as this step's code goes** — every gate is pure/free, the
  only spend point (`compile_skill_artifact`'s LLM calls) is the pre-existing, already-
  instrumented path; `_tel.span("ingestion.compile", ...)` wraps the call.

## Test counts (re-run here, `DATABASE_URL` unset)

```
cd backend && env -u DATABASE_URL python -m pytest -q -p no:cacheprovider tests/test_verified_solutions_hf_offline.py
70 passed

cd backend && env -u DATABASE_URL python -m pytest -q -p no:cacheprovider tests/test_verified_solutions_jobs_offline.py
15 passed
```

Both suites are meaningful, not tautological: e.g.
`test_v1_display_name_would_be_quarantined_without_the_normalizer` pins the exact regression
the normalizer exists to prevent; `test_held_out_beats_license_in_the_reason_order` and
`test_enqueue_gates_before_it_enqueues` assert real rejection-reason ordering that a sloppy
refactor would silently reorder; `test_no_llm_client_still_calls_the_compiler_so_the_refusal_
is_recorded` would fail if a future edit added a silent-capture fallback. They would catch a
broken implementation, not just execute it.

## Is `verified_solutions_jobs.py` complete, or half-done?

**Half-done, but the missing half is the important half.** The wiring that exists — job-type
registration, public-only scope enforcement, idempotency keys, payload provenance fields,
graceful refusal on no LLM client, pre-spend gating in `enqueue_verified_solution_jobs` — is
correct and well-tested (15/15 passing, and the tests genuinely probe the wiring, not just
happy-path smoke). What is missing is the actual point of the step: routing the gold patch
through `verified_solutions.preserve()` so it becomes a real, queryable `verified_solution`
Procedure artifact rather than prose inside a generically-compiled document (Finding 1), plus
threading `row.patch`/`row.test_patch`/`row.fail_to_pass` through the job payload so that call
has something to preserve (currently only the synthesized markdown `content` is enqueued,
not the raw patch/test fields — those need to ride along too, which is itself object-storage-
sized payload the `offload=True` default already handles).

## What remains to finish Step 2

1. Fix Finding 1: thread raw `patch`/`test_patch`/`fail_to_pass` fields through the enqueue
   payload, call `verified_solutions.preserve()` from the handler on a successful compile,
   with a proving test.
2. Fix Finding 2: move the corpus scan off the event loop in
   `enqueue_verified_solution_jobs`.
3. A 10-item pilot against a configured LLM client (as SUMMARY §6/§8 already proposes) to get
   real $/item before any 500-item or full-corpus write, now including the preserve() cost
   (currently zero, since it's unwired) in that measurement.
4. Decide the four numbered board questions in `step_2_SUMMARY.md` §9 (SWE-Gym license,
   repo-level held-out default, `custom-check-github`, V2 decontamination posture) — all
   already stated with proposed defaults, waiting on a founder ruling, not blocking code.
5. Cross-corpus dedup between the four sources (currently only within-source, per SUMMARY
   §7 "Risks" — `datasets-server` `/filter`/`/search` returned HTTP 500 during research, so
   this is honestly flagged as owed, not silently skipped).
