# Review: Step 3 — SkillMD-138K (in progress, uncommitted)

Reviewed 2026-09-28. Read-only review; nothing in the working tree was touched.

Files in scope: `.scratch/ingestion/step_3_research.md`,
`backend/app/services/ingestion_sources/{skillmd_dataset,skillmd_gate,skillmd_offline,skillmd_pilot}.py`,
`backend/app/ingestion/skillmd_cli.py`, `backend/app/ingestion/admin.py` (wiring diff),
`backend/tests/test_skillmd_138k_offline.py`. Confirmed `backend/app/ingestion/queue.py`,
`backend/app/services/ingestion_jobs.py`, `backend/app/services/trace_worker.py`,
`backend/tests/test_trace_payload_cap.py` are a **different, unrelated workstream**
(verified-solutions job type + trace-batching) — not part of Step 3, not reviewed further here.

## 1. Status: ~55% complete

| Spec item | State |
|---|---|
| Literature review + Exa | **Done, and unusually rigorous.** `step_3_research.md` corrects three wrong premises in the build brief itself (no arXiv paper for the dataset, wrong citation for gitskills, `html_url`-from-`repo` breaks on 12.7% mirror rows), reads 13 papers/docs with dates and tiers, and re-derives the schema by reading the parquet footer directly rather than trusting the card. |
| Dataset reader | **Done.** `SkillMD138KReader` (skillmd_dataset.py:316-371), revision-pinned (`DATASET_REVISION = "0d73048a"`, line 86), metadata-only (skips the 540MB `content` column, matching the dataset viewer's own known failure mode), local parquet cache. |
| Per-repo license via GitHub licenses API, cached, rate-limited | **Done.** `GitHubLicenseResolver` (skillmd_dataset.py:627-745): one cached lookup per repo, `min_interval` throttle, `403`/`429` tracked separately from "no license" (skillmd_dataset.py:710-711), routes through the existing `repo_license_policy.classify_spdx` allowlist (line 737) rather than a second policy. |
| Content-hash dedup + star/recency prior | **Partially done.** Exact-hash dedup (skillmd_dataset.py:501-508) and simhash near-dup (skillmd_gate.py:458-491) both exist and are order-pinned by test. Star prior exists as a soft, non-rejecting signal (`below_star_prior`, never used to gate — skillmd_dataset.py:498-499). **Recency prior does not exist**, because the dataset genuinely has no recency/last-commit column — `step_3_research.md` §1 documents this as a measured absence, not an oversight, so this is an honest partial rather than a silent gap. |
| Prompt-injection screen, quarantine not drop | **Done.** `gate_text` (skillmd_gate.py:327-401) routes block-severity screener findings and the paper-sourced coercive-language patterns to `"quarantine"`, never `"reject"` or silent drop (skillmd_gate.py:385-395); reuses `screening.screen_document_text` rather than reimplementing detection (skillmd_gate.py:314-324). |
| Cheap identity judging (batch judging, per-doc goal cache, skip-judging exact dups) | **Partially verified.** Exact duplicates never reach the fetch/gate/compile stage at all (skillmd_dataset.py:501-508 runs before any network call), which is the strongest form of "skip judging." Batch judging and the per-document goal cache are inherited from the shared `run_skill_ingestion`/`compile_skill_artifact` path by reuse, not reimplemented here — consistent with the reuse instruction, but this review did not trace into `skill_ingestion.py` far enough to confirm those mechanisms actually fire under `concurrency=1` (the default `skillmd_pilot.py` uses, unchanged). |
| 2,000-skill local-shard pilot with metrics | **Not run.** `run_skillmd_pilot` and `skillmd-import` CLI exist and are unit-tested against fixtures, but there is no evidence of an actual execution against a local Postgres shard: no `.scratch/ingestion/step_3_SUMMARY.md`, no run-output JSON/markdown, no shard artifacts anywhere in the tree. The acceptance criterion ("2,000 skills on a local shard: license outcome distribution, dedup rate, quarantine count, judge calls and $ per skill, Goals created vs matched") is unmet — only the harness to produce those numbers exists. |
| Projection for 138k | Harness exists (`project_to_full_corpus`, skillmd_pilot.py:170-198) and is honestly labelled as an unverified projection (explicit caveat that the corpus's row order is not a random sample), but with no real pilot run yet, there are no real numbers to project from. |
| Deliverable: `step_3_SUMMARY.md` | **Missing.** Only `step_3_research.md` exists. Per the Common section's stated deliverable, this step is not done until that summary — with real run numbers — exists. |

**Reasoning for ~55%:** research (weight ~15%) is complete and excellent; the reader, gate, license resolver, dedup, screening and CLI (weight ~55%) are built and covered by fast, well-targeted offline tests; but the step's actual acceptance gate — a real 2,000-row run on a local shard producing a metrics table and a SUMMARY — is 0% done. That's the single largest remaining item and it's also the one the spec treats as the deliverable, not a nice-to-have.

## 2. Correctness findings, severity-ranked

### HIGH — blocking network calls run synchronously inside `async def`, violating the hard rule
`backend/app/services/ingestion_sources/skillmd_pilot.py:68` declares `run_skillmd_pilot` as
`async def`, then at line 106 calls `refs = list(source.discover())` — a fully synchronous call.
`SkillMD138KSource.discover()` (skillmd_dataset.py:519-534) does the real network I/O: it builds a
`ThreadPoolExecutor` and calls `pool.map(self._fetch_and_gate, candidates)` (line 531-532), which
blocks the calling thread — i.e. the async event loop's thread — until every one of up to
`limit * FETCH_OVERSAMPLE` (see next finding) raw-content fetches and their `time.sleep` backoffs
(`_http_get`, skillmd_dataset.py:140-169, `_HTTP_BACKOFF_SECONDS = 1.5`) complete. The license
resolver's own `_throttle()` (skillmd_dataset.py:685-691) also calls `time.sleep` synchronously,
again from inside this same `async def` call chain (via `_decide_license` at line 561, called from
inside the loop that runs after `discover()`... actually `_decide_license` is itself called
synchronously inside `discover()`'s PHASE C, so this is the same violation).

This is exactly the pattern `CLAUDE.md`'s hard rule and the Common section call out by name:
*"Never let a blocking call run inside an `async def`: use `app.utils.aio.run_blocking`."*
`app/utils/aio.run_blocking` is never imported or used anywhere in `skillmd_pilot.py` or
`skillmd_dataset.py`. If `run_skillmd_pilot` is ever awaited on a server that's also serving other
async requests (e.g. from an admin endpoint rather than only the standalone CLI process), it will
stall the entire event loop for the full gate/fetch wall time (minutes, per the pilot's own
`gate_seconds` metric).

**Fix:** wrap the synchronous `source.discover()` (and, ideally, the whole gate pass) in
`await run_blocking(lambda: list(source.discover()))` in `skillmd_pilot.py:106`. Since
`skillmd-import` is currently only invoked from the standalone `admin.py` CLI process (single
coroutine, nothing else sharing the loop), this has not caused an observed failure yet — but it
is a real, named-rule violation waiting to bite the moment this pilot is called from anywhere
that shares an event loop with other traffic (e.g. wired into a web-triggered job).

### MEDIUM — the fetch stage does not respect the admission limit, multiplying network cost
`skillmd_dataset.py:519-534`: `discover()` first materializes the **entire** oversampled candidate
list (`candidates = list(self._candidates())`, bounded by `candidate_budget = limit *
FETCH_OVERSAMPLE + 1` at line 478-480, i.e. up to `2000 * 12 + 1 = 24,001` candidates for the
documented 2,000-skill pilot), then fetches **all of them** via `pool.map` (line 531-532) before
PHASE C ever checks `self.stats.admitted >= self._limit` (line 540). The limit-check comment at
lines 414-422 explains *why* the check was moved to PHASE C (an earlier version's PHASE-A check
never fired in the parallel path), but the fix as written does not re-introduce any early
termination — it just makes the accounting correct after the fact, at the cost of always doing
the full oversampled fetch even when the target is reached at candidate #300 of 24,001.

The module's own comment (skillmd_dataset.py:420-421) says the 12x multiplier was picked against
a *small* dry run (11 rows, 5 admitted → ~45% yield), which would suggest an oversample closer to
2-3x, not 12x. At a real ~45% yield, a 2,000-skill pilot needs roughly 4,500 candidates, not
24,000 — this fetches roughly 5x more raw GitHub content, and makes roughly 5x more downstream
gate/screen calls, than the target requires.

**Fix:** either fetch in bounded chunks (fetch a chunk, check `admitted` against `limit`, stop
requesting further chunks once satisfied) or lower `FETCH_OVERSAMPLE` and accept that a run might
occasionally fall short of `limit` and report the shortfall (which the stats already support via
`scan_budget_exhausted`). The current shape can't finish early even in principle.

### LOW — spend cap not visibly enforced in this step's own code
`step_3_research.md` states a tightened `$2/day` spend cap for this step (line 5), but neither
`skillmd_pilot.py` nor `skillmd_cli.py` reference a spend ledger, `enforce_limits`, or any other
cap check — the pilot's `--embed` flag is the only cost lever exposed, and LLM judging cost is
entirely delegated to whatever `run_skill_ingestion`/`compile_skill_artifact` already enforces
globally. This review did not trace far enough into `skill_ingestion.py` to confirm a global
spend gate exists and would catch a runaway run; if it does, this is fine by reuse; if it doesn't,
a `--limit 2000 --dry-run` run is safe (no LLM calls per `run_skillmd_pilot`'s dry-run early
return at pilot.py:130-133) but a non-dry-run pilot has no step-local stop button.

### Non-finding — reuse of the existing skill path is correct, if indirect
The Step 3 spec text says "use the existing `skill_md.py` / `ingest_skill_package` path." The
adapter here (`SkillMD138KSource`, `source_type = "skill_md_138k"`) is **not** one of
`skill_md.py`'s two adapters (`LocalDirSkillSource` / `GitHubSkillSource`, `skill_md.py:112-289`)
and is not registered in its `SOURCE_ADAPTERS` dispatch table — but that dispatch table is for
CLI source *discovery* (`ingest skill-dir` / `ingest skill-repo`), not the compiler. The actual
shared machinery — `run_skill_ingestion` / `compile_skill_artifact` in `skill_ingestion.py`,
which does the admission gate, screening persistence, novelty check, Goal/Claim extraction — is
reused verbatim (`skillmd_pilot.py:143-145` calls `run_skill_ingestion(pool, source, ...)`,
exactly as `skill_md.py`'s own adapters would be driven). This is the right interpretation of
"reuse the existing path": a new `SourceAdapter` implementation for a new source shape, feeding
the one shared compiler, rather than a duplicate compiler. No new `ingest_skill_package`-shaped
job handler was written either — correct, since the pilot drives `run_skill_ingestion` directly
rather than going through the job queue.

### Non-finding — license gate correctness
The mirror-origin-before-license ordering (`resolve_origin` before any `_decide_license` call,
skillmd_dataset.py:432-440) is correct and is the change the research doc calls "the single
highest-value change in this step" — verified by
`test_license_is_resolved_against_the_ORIGIN_not_the_mirror`
(`test_skillmd_138k_offline.py:155-163`), which asserts the mirror's own slug
(`NeverSight/skills_feed`) is never asked and the recovered origin
(`nu1nux/open-skills`) is. `classify_spdx`'s NOASSERTION/absent-license-is-QUARANTINE
posture (`repo_license_policy.py:257-273`) is reused unmodified, not re-implemented with a
looser default — correct per the module's own stated hard-floor design.

### Non-finding — no blocking calls other than the one flagged above
`skillmd_gate.py` is pure (no I/O at all, confirmed by its own module docstring and by inspection
— the only "impure" call is the lazy `screening.screen_document_text` import at
skillmd_gate.py:322, which is itself synchronous but is only ever invoked from the same
already-synchronous `discover()` call chain, so it's the same single violation, not a second one).

### Non-finding — provenance
`SourceArtifact.license_metadata` (skillmd_dataset.py:578-590) carries dataset id, revision,
row content hash, mirror/origin flags and gate version; `SourceRef`/`SourceArtifact.commit` is
set from the origin's own ref (`html_url_parts`, skillmd_dataset.py:268-277), not the mirror's —
consistent with hard rule 2 (scope + provenance on everything entering storage).

## 3. Tests

Ran (from `backend/`, `DATABASE_URL` unset):
```
env -u DATABASE_URL python -m pytest -q -p no:cacheprovider tests/test_skillmd_138k_offline.py
```
**Result: 67 passed, 0 failed, 0.58s.** No `DATABASE_URL`-gated skip triggered (correctly offline
by construction — no network, no Postgres).

Coverage is genuinely good, not just line-count padding: there are specific tests pinning gate
*order* (`test_size_gates_run_before_screening_so_a_thin_row_is_never_screened`), the
parallel/serial accounting invariant (`test_parallel_fetch_produces_accounting_identical_to_serial`),
the PHASE-A/PHASE-C limit-enforcement fix itself
(`test_parallel_fetch_still_enforces_the_admitted_limit`), and the mirror-vs-origin license
resolution (`test_license_is_resolved_against_the_ORIGIN_not_the_mirror`). It does **not** test:
`run_skillmd_pilot` itself end-to-end against a fake pool (only the pure gate/source layer is
tested; `skillmd_pilot.py`'s async orchestration, the `assert_not_experiment_database` call site
inside `run_skillmd_pilot`, and the metrics-dict assembly in lines 118-166 have no direct test —
only `assert_not_experiment_database` itself is unit-tested in isolation, lines 524-539). The
blocking-call issue above would not have been caught by this suite, since it runs everything
synchronously anyway (no event loop contention in a single-threaded pytest run).

## 4. Efficiency

- **Streaming the 0.56GB dataset:** correctly avoided. `METADATA_COLUMNS` (skillmd_dataset.py:88-90)
  excludes `content`; the reader pulls only the ~20MB metadata slice via `fsspec`'s HTTP range
  reads (skillmd_dataset.py:339-356) and can cache it locally. This is the single most important
  efficiency decision in the module and it's correct and well-documented (skillmd_dataset.py:13-27).
- **GitHub API budget for ~20.5k repos:** the license resolver makes at most 1 API call per
  **distinct origin repository** (cached, `_fetch_spdx` returns on first successful template,
  skillmd_dataset.py:698-717), never per row. For the full corpus this bounds API calls to
  well under 20,556 (fewer once mirrors collapse to their handful of recovered origins), which
  is efficient and correctly reasoned about in the module docstring. Unauthenticated GitHub's
  60/hour limit is explicitly called out as insufficient beyond ~40 repos
  (skillmd_dataset.py:178) — a real constraint for any full-scale run without `GITHUB_TOKEN` set.
- **Raw content fetch (the actual bottleneck):** as detailed in the MEDIUM finding above, the
  `FETCH_OVERSAMPLE = 12` constant causes roughly 5x more raw-content GET requests than a
  2,000-skill target needs at the measured ~45% yield, and none of them can be skipped once
  fetched because the whole oversampled batch is submitted to the thread pool before the limit
  is checked. This is the main efficiency defect in the current code.
- **Judging cost:** exact-duplicate rows are dropped before any fetch or judge call
  (skillmd_dataset.py:501-508), which is the cheapest possible form of "skip-judging for exact
  duplicates." Batch judging / per-document goal cache for the surviving admitted set is
  inherited from `run_skill_ingestion`, not verified independently in this review (see MEDIUM
  finding above for the caveat).

## 5. Ordered list of what remains

1. **Fix the blocking-call violation** (HIGH, §2): wrap `source.discover()` (and the license
   resolver's `time.sleep` throttle) in `app.utils.aio.run_blocking` inside `run_skillmd_pilot`.
2. **Fix or accept the oversample inefficiency** (MEDIUM, §2): chunk the fetch stage so it can
   stop once `limit` admits are reached, or lower `FETCH_OVERSAMPLE` and let a run fall short of
   `limit` with a reported reason.
3. **Run the actual 2,000-skill pilot against a real local shard** — this is the acceptance
   criterion and hasn't happened yet. Needs a local Postgres shard registered
   (`admin register-shard`), then `skillmd-import --limit 2000 --dry-run` first (network + license
   calls only, no writes, safe to run to get real disposition numbers), then a non-dry-run pass
   once the dry-run numbers look sane and spend is projected.
4. **Write `.scratch/ingestion/step_3_SUMMARY.md`** with the real run's metrics table (license
   outcome distribution, dedup rate, quarantine count, judge calls and $/skill, Goals created vs
   matched) and the projection for the full 138k, per the Common section's stated deliverable.
5. **Confirm the spend cap** (LOW, §2): verify whether a global spend-ledger cap actually bounds
   a non-dry-run pilot, or add a step-local guard before running at $2/day-relevant scale.
6. Optional/nice-to-have, not blocking: a direct test of `run_skillmd_pilot`'s own orchestration
   (metrics assembly, `dry_run` branch, `assert_not_experiment_database` call site) against a
   fake pool, since currently only the pure gate/source layer underneath it is tested.
