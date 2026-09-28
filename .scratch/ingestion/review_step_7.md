# Step 7 review — codemods (Node.js userland-migrations; OpenRewrite)

Reviewed read-only, 2026-09-28. Uncommitted files inspected: `.scratch/ingestion/step_7_research.md`,
`backend/app/services/ingestion_sources/codemod_node.py`, `codemod_checks.py`, `openrewrite.py`,
`backend/app/ingestion/codemod_cli.py`, `backend/tests/test_ingestion_codemods_offline.py`. Confirmed
`backend/app/ingestion/admin.py` / `queue.py` / `ingestion_jobs.py` / `trace_worker.py` diffs belong to a
different in-flight lane (SkillMD `skillmd_cli` wiring, step 6) — codemod_cli.py is correctly **not**
wired into `admin.py` yet, matching its own docstring. No cross-lane contamination found.

## Status: ~60% complete

Research is exceptional and code is careful, well-tested at the unit level, and unusually honest about
its own limits. What's missing is entirely in the "prove it at scale" half of the spec: no pilot run has
been executed, no `step_7_SUMMARY.md` exists, no report JSON exists anywhere in the repo, and one hard
rule (no blocking calls inside `async def`) is violated twice in the write path.

## Verdict on what exists

- **Research** (`step_7_research.md`, 897 lines): outstanding. Every number is sourced and dated,
  contradicts the plan twice with evidence (jscodeshift → jssg; 28 recipes → 40), and finding #5 (a
  passing fixture is self-consistency, not correctness, citing a live broken-fixture issue
  nodejs/userland-migrations#249) is exactly the kind of thing this repo's "verified" claims need to survive.
- **Node.js reader** (`codemod_node.py`, 912 lines): reads a pinned local checkout (`DEFAULT_PINNED_COMMIT`
  at line 79, required not defaulted), resolves license via the *shared* `repo_license_policy` allowlist
  (`decide_repo_license`, `identify_spdx_from_text`), refuses to emit an artifact whose verdict isn't ALLOW
  (`CodemodLicenseBlocked`). This is real per-item, per-commit license gating, matching the spec.
- **Check runner** (`codemod_checks.py`, 1091 lines): a genuinely separate, dependency-free module that
  imports nothing from `app.*` (verified: no `from app` outside docstring text; test
  `test_codemod_checks_does_not_import_the_screening_module` at line 970 of the test file pins it). Correctly
  scopes itself as the Step 4 runner's *temporary* substitute, not a reimplementation that will collide with
  it later.
- **OpenRewrite reader** (`openrewrite.py`, 1150 lines): the license-reversal table (30 Apache-2.0 / 22
  Moderne) is hardcoded but the module is explicit everywhere (`check_tier: "static"`, `provenance_limits`
  list at openrewrite.py:1103-1129) that this is **declaration parsing, not the before/after check the spec
  asks for**. See Finding 1 below — this is the biggest spec gap and it was never raised as a board question.
- **CLI** (`codemod_cli.py`, 355 lines): dry-run by default, `--apply` requires a loopback DSN
  (`_dsn_is_loopback`), reuses the existing `ingest_skill_md` writer rather than an ad hoc INSERT (good — matches
  "evidence goes through the existing chokepoints" spirit even though this isn't the evidence.py path).

## Progress vs. spec, item by item

1. **Readers per catalog → Procedures with before/after tests as the check, via a sandboxed runner** —
   **partially met**. Node.js: yes, `run_jssg_fixture_check` (codemod_checks.py:415-480ish) actually executes
   `codemod jssg test --reporter json` in a subprocess with a scrubbed env and timeout, and reads real
   pass/fail. OpenRewrite: **no** — `openrewrite.py` never runs `RewriteTest`; `check_tier` is hardcoded
   `"static"` and the "check" is "does the id parse and is a test method named on the doc page"
   (`test_evidence_meaning`, openrewrite.py:1099-1102: "test method names read off the documentation page:
   evidence that a test exists, never an executed assertion"). The research (§3.3-3.4) justifies this as an
   infra-cost decision (JDK 21 + Gradle + network + Code Genome token), which is a reasonable call — but the
   spec explicitly says "as in step 4" (sandboxed runner) and the Common section's hard rule 3
   ("Schema/spec are frozen... file a numbered board question with a proposed default") applies to a check
   *tier* this weak, not just a new column. **No board question was filed** — `.scratch/build-board.md` has no
   mention of step 7, codemod, or openrewrite at all.
2. **Shared check runner is Step 4's job; did they avoid touching `screening.py`?** — **yes, correctly**.
   `codemod_checks.py` is a standalone module, pinned test at line 970 of the offline test file enforces the
   non-import. This is exactly right per the Common section's framing.
3. **Per-module license at a pinned commit, SPDX allowlist, rejection counts** — **met for Node.js, honestly
   short for OpenRewrite**. Node.js resolves SPDX from the actual checkout tree at the pinned commit.
   OpenRewrite's `license_metadata["commit"]` is hardcoded `None` (openrewrite.py:1073, 1145) and the module
   says so itself: "no repository commit was fetched, so the license verdict is a statement about the table
   snapshot date and not about a pinned tree" (openrewrite.py:1115-1116). Rejection reasons are tracked as a
   histogram (`rejections: dict[str, int]` in codemod_cli.py) rather than "assumed accepted until proven
   otherwise" — good, and there's a dedicated test (`test_openrewrite_gate_output_is_a_histogram_not_a_verdict_by_assertion`).
4. **Local-shard pilot: all Node.js migrations + ≥50 Apache-2.0 OpenRewrite recipes** — **not run**. No
   `report.json`, no shard registration evidence, no numbers anywhere in the repo. The CLI supports it
   (`--apply --shard-dsn-env ...`) but nobody has executed it. This is the largest concrete gap against the
   step's acceptance criteria.
5. **Summary deliverable** (`step_7_SUMMARY.md`) — **missing**. Steps 2 and 6 both have one; step 7 does not.

## Findings, severity-ranked

### HIGH — blocking calls inside `async def`, hard rule violated twice
- `backend/app/ingestion/codemod_cli.py:189` `async def run(...)` calls `adapter.fetch(ref)` synchronously at
  line 242.
  - For the Node.js adapter this reaches `run_jssg_fixture_check` in `codemod_checks.py`, which calls
    `subprocess.run(..., timeout=timeout_s)` (codemod_checks.py:430-438) — up to 120s of blocking I/O per
    recipe, executed directly inside a coroutine.
  - For the OpenRewrite adapter, `fetch()`/`discover()` reach `_default_http_get` (openrewrite.py:856-863),
    which calls `httpx.get(url, timeout=30, ...)` synchronously — up to 30s blocking network I/O per recipe
    page, again inside a coroutine.
  - CLAUDE.md, "Rules for every step": *"Never let a blocking call run inside an `async def`: use
    `app.utils.aio.run_blocking`."* `backend/app/utils/aio.py` exists precisely for this and is unused here.
  - **Practical impact today is low** (this CLI is a standalone one-shot process, not yet wired into
    `admin.py`'s shared dispatcher, so nothing else shares the event loop during a run) — but it is a literal
    hard-rule violation as written, and becomes a real problem the moment this CLI is registered in `admin.py`
    alongside other admin commands, or if `_apply`'s `await ingest_skill_md(...)` calls ever end up sharing a
    loop with a live server process.
  - **Fix:** wrap each `adapter.fetch(ref)` call in `await run_blocking(adapter.fetch, ref)`.

### MEDIUM — OpenRewrite's check tier is weaker than the spec asks for, with no board question filed
- `backend/app/services/ingestion_sources/openrewrite.py:1-19` self-documents that `RewriteTest` is never
  run, and `check_tier="static"` (line ~1033) means "the recipe's declaration parses... it never means the
  recipe transforms correctly." The step 7 spec says the check should be "before/after tests... run through a
  sandboxed runner, as in step 4," i.e. an *executed* check, for **both** catalogs.
  - This is a defensible engineering call given the JDK/Gradle/network/Code-Genome-token cost (well argued in
    `step_7_research.md` §3.3-3.4, §3.6), but CLAUDE.md's hard rule 3 requires a numbered board question with a
    proposed default when the built code falls short of what the frozen spec asks for. `.scratch/build-board.md`
    has no step-7 entry.
  - **Fix:** add a numbered question to the board now (proposed default: ship OpenRewrite at `check_tier:
    "static"` for this pilot, revisit executable RewriteTest checks only if/when a Java/Gradle sandbox budget is
    approved), so this doesn't silently become the permanent state.
- **Downstream risk to flag explicitly**: `openrewrite.py`'s `CheckOutcome` for every accepted Apache-2.0
  recipe has `passed=True` (line ~1032). Whatever consumes this artifact via `ingest_skill_md` needs to be
  able to tell "passed a static declaration check" apart from "passed an executed fixture check" (Node.js) —
  otherwise a reader of the resulting Procedure could read `passed=True` as the executed-fixture guarantee
  the spec calls for. The payload does carry `check_tier` for this purpose; whether the consuming surface
  (routing/applicability) actually branches on it was out of scope for this review and is worth a follow-up
  check before OpenRewrite Procedures reach a live routing decision.

### MEDIUM — acceptance criteria not yet demonstrated
- No pilot run exists (no `report.json`, no shard-registration record, no item/accept/reject/dedupe/Procedure
  counts anywhere in the repo). The spec's "Acceptance" line for step 7 is "all Node.js migrations and at
  least 50 Apache-2.0 OpenRewrite recipes on a local shard, each with a passing check; license rejection
  counts; proving tests" — the code can do this (`--apply --shard-dsn-env ...`) but it hasn't been run.
- No `step_7_SUMMARY.md`, unlike steps 2 and 6, which both have one. The Common section's "Deliverable (every
  step)" requires it.

### LOW — provenance/documentation gaps, already self-disclosed
- `openrewrite.py`'s `license_metadata["commit"]` is always `None` — acceptable per the static-tier scope
  decision above, but it means "license at the pinned commit" (spec wording) is only true for Node.js, not
  OpenRewrite. Already flagged candidly in-code (`provenance_limits`); listing it here only so it's tracked
  against the spec line, not because the code hides it.
- `codemod_cli.py:33-39` documents that `ingest_skill_md`'s write path has "no `provenance` column parameter,"
  so per-artifact provenance (repo, commit, path, SPDX, allowlist version, full check payload) lives only in
  the report JSON and the document body, not a queryable column. Self-flagged as a follow-up, not a defect
  introduced here, but worth carrying into the summary once written.
- `ARCHIVED_REPOS` (openrewrite.py, ~line 232) is explicitly a partial set ("15 of 23 archived... the 8
  unnamed ones are NOT silently assumed live"). Correct failure mode (fail closed is not what happens here —
  an *unlisted* archived repo would be treated as non-archived and could be accepted). Worth a comment/TODO
  that this understates rejections, not overstates them — i.e. it's a false-negative risk on the archived
  gate, not a false-accept-everything risk, since the license gate is the primary filter.

## Safety / correctness checklist

| Item | Status |
|---|---|
| Sandboxed before/after execution (Node.js) | subprocess with `timeout_s` (default 120s, codemod_checks.py:56), env allowlist scrub (`_ENV_ALLOWLIST`, lines 86-101) dropping proxy/AWS/NODE_OPTIONS vars, `cwd` pinned to the recipe dir, argv list (no shell) | OK, with the caveat below |
| No network during the check subprocess | Not explicitly sandboxed (no netns/firewall) — relies on `node_bin` defaulting to a pre-installed `codemod` CLI resolved via `shutil.which` (codemod_cli.py:133) rather than an `npx` fetch-on-demand, so no network *should* occur, but nothing actively blocks it if the installed CLI decided to phone home | Acceptable given docs, not airtight |
| No secrets in the subprocess | Env allowlist is a real allowlist (not a denylist), so this is solid | OK |
| Isolated temp dirs | Runs directly against the checkout's own recipe directory (`cwd=str(root)`), not a temp copy — a malicious/buggy transform with `--allow-fs` could mutate the pinned checkout in place. Low risk (these are curated first-party recipes, gated by license+catalog membership) but worth noting since "isolated temp dirs" was explicitly asked for in review scope | Gap |
| License check at exact commit | Node.js: yes. OpenRewrite: no (see Medium finding above) | Partial |
| Provenance on everything entering storage | Present in `license_metadata` / report, not yet in a dedicated DB column (self-flagged) | Partial |
| No blocking calls inside `async def` | Violated twice (see High finding) | **Fail** |
| Tenant/scope conventions | N/A directly — the write path delegates to the pre-existing `ingest_skill_md` (`backend/app/services/skill_ingestion.py`), which is out of this step's diff; not re-audited here | Not this step's code |
| Redaction | Content goes through `ingest_skill_md`'s own `redact_blocks_for_persistence`, not `trace_redaction.redact_event` — correct, because this is document-shaped content on the skill_md path (same path Step 3 uses), not the trace/event path `redact_event` gates | OK |

## Tests

Ran offline (DATABASE_URL unset):
```
cd backend && env -u DATABASE_URL python -m pytest -q -p no:cacheprovider tests/test_ingestion_codemods_offline.py
24 passed in 4.39s
```
Coverage is genuinely good for unit-level correctness: license quarantine (Moderne pointer file,
NOASSERTION, unidentifiable text, real ALLOW cases), fixture-layout discovery (nested and flat), negative-case
handling, Gate B wellformedness (JSON/JS/TS/malformed), a fake subprocess shim exercising both a real green
and a real red `jssg` result, the "never claims correctness" payload-shape test, the OpenRewrite gate's
accept/reject histogram, the screening-import isolation test, an LLM-import isolation test
(`test_no_module_in_this_source_path_imports_an_llm_client`), and CLI dry-run defaults.

**Missing test coverage** (proving tests the spec's acceptance line implies but aren't here):
- Nothing exercises the actual `async def run(...)` loop end-to-end against a fake pool/adapter to prove the
  accept/reject/dedupe counters are wired correctly across multiple items (only single-artifact unit tests
  exist).
- Nothing proving the blocking-call issue doesn't stall a shared loop (this would be a regression test *for*
  the High finding, once fixed — e.g. asserting `run()` doesn't call `subprocess.run`/`httpx.get` directly on
  the calling task).
- No test resolves against a real (even if tiny, checked-in fixture) Node.js checkout tree end-to-end — all
  Node.js tests build synthetic `tmp_path` trees, which is appropriate for offline/hermetic testing but means
  the 40-recipe, real-catalog claim in the research doc has not been machine-verified by a test, only by a
  one-time manual count during research.

## Efficiency

- **Node.js**: research recommends `git clone --depth 1` (small repo, 40 recipe dirs) — cheap and correct;
  no sparse-checkout machinery needed and none was built, appropriately. Cloning itself is left external to
  the code (`--checkout` takes an already-cloned path); this is fine for a pilot but means "clone" isn't part
  of what's proving repeatable yet.
- **OpenRewrite**: avoiding Gradle/Maven entirely (static-tier-only) is the single biggest efficiency win in
  this step, and it's the same design choice driving the Medium severity finding above — cheap and fast, at
  the cost of an unexecuted check. `httpx.get(..., timeout=30)` per recipe page, capped by `--limit` (default
  50) regardless of `--max-pages`, so even an unset `--max-pages` can't runaway-fetch the full ~4,481-page
  catalog in one CLI invocation — good bound.
- Both paths correctly avoid any LLM spend (`test_no_module_in_this_source_path_imports_an_llm_client`,
  `codemod_cli.py`'s "$0 LLM SPEND" docstring) — matches the step 7 goal of zero-cost checkable knowledge.

## Ordered remaining work

1. **Fix the blocking-call violation** (High): wrap `adapter.fetch(ref)` (codemod_cli.py:242) in
   `app.utils.aio.run_blocking`, or make the adapters' `fetch()` truly async internally. Add a regression test.
2. **File the board question** on `.scratch/build-board.md` for OpenRewrite's static-only check tier before
   calling the OpenRewrite side "done" — proposed default: accept static-tier for this pilot, flag
   RewriteTest execution as a funded follow-up.
3. **Run the actual local-shard pilot**: register a local shard, `--apply` both sources, and capture real
   counts (items in / accepted / rejected by reason / deduplicated / Procedures created / spend / bytes /
   wall time) — this is the acceptance criterion the step is graded on and it hasn't happened yet.
4. **Write `step_7_SUMMARY.md`** per the Common section's "Deliverable (every step)" template, using the pilot
   numbers from (3).
5. (Lower priority) Isolate the check subprocess's working directory (copy the recipe dir to a temp dir before
   running `--allow-fs`-capable transforms) rather than running in-place against the pinned checkout.
6. (Lower priority) Add an end-to-end test of `codemod_cli.run()` against a fake pool exercising multiple
   accept/reject/dedupe paths in one pass, not just single-artifact unit tests.
