# Step 4 summary — verifiers as check types (actionlint, zizmor, ast-grep) + ast-grep-essentials

Date: 2026-09-28 · Branch: working tree on `main` @ `24c8d23` · Author: step-4 agent
Status: **built, proven, run on a LOCAL shard. No production writes.**

---

# 0. THE CHECK RUNNER INTERFACE — PUBLISHED FIRST, FOR STEP 7

Step 7 (codemods) needs this to run OpenRewrite/Node codemod before/after tests. Read this
section first; everything else in this file is provenance for it.

Module: `backend/app/services/check_runner.py`. The import surface is deliberate and small.

## 1.1 The one call you need

```python
from app.services.check_runner import VerifierCheck, run_check, CheckResult

result: CheckResult = await run_check(
    VerifierCheck(
        check_type="ast_grep",              # a closed vocabulary, see 1.2
        tool_version="0.45.3",              # REQUIRED — an unpinned verdict is not evidence
        source="my-corpus@<sha>",          # provenance string
        config={"snapshot_tests": False},  # every knob that changes the verdict
        expected_case_count=1,              # liveness assertion; None disables the count check
    ),
    files={                                # keys are paths RELATIVE to a fresh temp workspace
        "sgconfig.yml": b"...yaml...",
        "rules/my-rule.yml": b"...yaml...",
        "tests/my-rule-test.yml": b"...yaml...",
    },
    timeout_seconds=60.0,
    # ast_grep_binary="<pinned ast-grep>"   # or set SL_AST_GREP_BIN
    # docker_binary="docker",               # containerised tools only
)
```

`run_check` is `async`, so **wrap any blocking setup with `app.utils.aio.run_blocking`** (it
never blocks the event loop). It stages `files` into a throwaway temp dir, executes, and
removes the dir — including on timeout.

## 1.2 `VERIFIER_CHECK_TYPES` — the closed vocabulary

```python
("actionlint", "zizmor", "ast_grep")
```

**For step 7, note this is a *closed* vocabulary.** A codemod check is a **fourth kind**, so
step 7 needs its own board question (see §7). Do not silently pass a new string: `run_check`
raises `CheckSpecError` for an unknown type, on purpose.

## 1.3 The verdict is FIVE-valued, not boolean

```python
VERDICTS = ("pass", "fail", "error", "no_input", "not_run")
```

**The only value that may close a Procedure is `pass`** (`CheckResult.passed`). `error` and
`no_input` are never pass. This is not stylistic — see §1.5.

For routing, map explicitly and never collapse:
`pass→accepted` · `fail→rejected` · `error|no_input|not_run→NEITHER` (unknown, do not
attribute a wrong/correct rate to the recommender).

## 1.4 `CheckResult` shape

| field | type | notes |
|---|---|---|
| `verdict` | `str` | one of `VERDICTS` |
| `check_type` | `str` | the tool |
| `tool_version` | `str` | the pin that produced this verdict |
| `findings` | `tuple[CheckFinding, ...]` | `code`, `message`, `severity`, `path`, `line` (**1-based for every tool** — zizmor's `json-v1` 0-based rows are normalised) |
| `exit_code` | `int` | `-1` when we never executed |
| `timed_out` | `bool` | a timeout is `error`, never `pass` |
| `duration_ms` | `int` | |
| `detail` | `str` | short, actionable, never raw source |
| `to_json()` | `dict` | the storable form |

## 1.5 What step 7 inherits, and why each item exists

| guarantee | why it is there (evidence in §4) |
|---|---|
| `no_input` is distinct from `pass` | ast-grep **exits 0 with `0 passed; 0 failed`** when a test file names an unloaded rule id. Reproduced locally. A boolean runner would record a Procedure as *verified* having checked nothing. |
| a pass must name what it checked | `expected_rule_id` assertion: a pass whose output never mentions the rule id is `error`. |
| pinned versions | SWE-bench maintainers found the *evaluator* non-deterministically marking the gold patch wrong on 30/300 instances. Pinning is a correctness control, not hygiene. |
| `--network none` enforced at the sandbox | `--offline` is a *tool* policy; the docs promise no API use but say nothing about process egress. Only the sandbox enforces it. |
| credentials scrubbed from the env | Same leak class the ingestion audit found in `github_corpus._default_http_get` (Authorization reused across redirects). A verifier that can read a token can leak it into a finding we publish. |
| never falls back silently | A missing binary is `error` with the env var named. Precedent: `ContainerSandboxExecutor`'s "Refusing to fall back". |
| isolation flags are a **pure function** | `container_argv()` — assertable with no daemon, and 9 tests fail loudly if any flag is dropped. |
| `InputPathEscape` reused | Same escape guard as `sandbox_executor`; an absolute or `..` key raises rather than writing outside the workspace. |
| timeout is bounded | `0 < timeout <= 300`, enforced before anything executes. |

## 1.6 Storage (migration 125)

`procedures.verifier_check JSONB`, named CHECK `procedures_verifier_check_chk`:
`{check_type ∈ (actionlint, zizmor, ast_grep), tool_version non-empty, source non-empty,
config object, expected_case_count positive int?}`. Carried forward on
`supersede_procedure` so a new version never silently stops being checkable.

Written by `capture_procedure(..., verifier_check=...)` — the gate and its writer ship in the
same change. **Not** stored in `postconditions` (that would be silently dropped by
`derive_criteria`) and **not** in `screening.CHECK_TYPES` (different vocabulary, different
subject — board Q-STEP4-1).

## 1.7 The step-binding shape (already validated by `source_locators.validate_binding`)

```python
"binding": {
    "kind": "binary",                  # needs a `path` too, or it is rejected
    "binary": "ast-grep",
    "path": "ast-grep",
    "args": ["test", "--skip-snapshot-tests"],
    "sandbox_policy": "isolated_network",   # or "isolated"
    "verifier": check.to_json(),           # allowed key on a binding
}
```

A step with a `verifier` is an executable step whose outcome is machine-decided. That is the
whole "routing with a check" shape, and step 7 should emit exactly this with its own
`verifier` payload.

## 1.8 For step 7 specifically

- Codemod checks are a **fourth check type** — needs a board question and a registry entry in
  `VERIFIER_REGISTRY` (image/binary + flags). Until then `run_check` refuses it.
- OpenRewrite needs a JVM and a long timeout, so a local binary or a heavier image than
  `512m`/`1 cpu`; expect to raise `SANDBOX_MEMORY`/`SANDBOX_CPUS` per tool, which are module
  constants precisely so that is a one-line, visible change.
- Recipe before/after tests are the natural `valid`/`invalid` pair, and the
  "expected count actually ran" assertion is the thing that stops a broken harness reporting
  success. Keep it.
- Do **not** let the agent's own report touch the verdict. Per SWE-bench #538 a patch that
  overwrites the test file flips the eval to `resolved: true`; per arXiv:2603.25764
  self-report and executed verification disagree by up to 56 points.

---

# 1. What was researched

Full record with URLs, identifiers and evidence tiers: **`.scratch/ingestion/step_4_research.md`**.

Headline findings:
- **Q-STEP4-1 is the load-bearing one.** `screening.CHECK_TYPES` is 1:1 with the DB CHECK
  `check_type_chk_screening_decisions` and records *admission screening of untrusted source
  text*. A verifier verdict is a different fact about a different subject at a different
  moment. Extending it would need a spec change and would make a screening row
  indistinguishable from a verification verdict. Implemented behind a separate vocabulary.
- **No published false-accept/false-reject rate exists for actionlint, zizmor or
  ast-grep.** Stated as a null result, not extrapolated from other tools on other corpora.
  Every study that measured both directions found false-accept ≫ false-reject (7.8%, 11.0%,
  19.78%, 24%), and CodeQL's autobuild failed on 71% of repos — the dangerous state is
  "no output", not "wrong output".
- **Tool facts, all read not recalled:** actionlint exit 0/1/2/3 and its silent
  auto-detection of `shellcheck`/`pyflakes` on PATH (disabled explicitly, or the verdict
  depends on the runner image); zizmor exit 0/1/2/3/11–14, `--format=sarif` forcing exit 0
  even with findings, `--no-ignores` needed or audited code can silence its own finding,
  `--offline` being a tool policy and not a sandbox.
- **Corpus correction:** the canonical repo is `coderabbitai/ast-grep-essentials`, not
  `ast-grep/`. It has **no releases**, so it is pinned by commit SHA.

# 2. What was built

| file | what | new? |
|---|---|---|
| `backend/app/services/check_runner.py` | the runner: 3-value registry, pure `container_argv`/`local_argv`, three parsers with liveness assertions, env scrubbing, `VerifierCheck`/`CheckFinding`/`CheckResult` | **new** |
| `backend/app/services/ast_grep_rules.py` | the corpus reader + writer: id-joined test discovery, license gate, per-rule check, Procedure payload, reason-counted counters | **new** |
| `backend/db/125_procedure_verifier_check.sql` | `procedures.verifier_check JSONB` + `procedures_verifier_check_chk`, additive, idempotent, "Next free number: 126" | **new** |
| `backend/tests/test_verifier_checks_offline.py` | **72 proving tests** (offline; no DB, no daemon, no ast-grep) | **new** |
| `backend/scripts/ingest_ast_grep_rules.py` | the run entrypoint; refuses experiment DBs, refuses a non-local DSN without `--allow-production` | **new** |
| `backend/app/services/procedures.py` | **the writer for the gate** (same change, half-gate rule): `verifier_check` param, column, `$48::jsonb`, supersede carry-forward + cast | **edited** (additive only) |

Migration 125 was applied to the local shard; `124` was the previous high, so 125 was free.

# 3. Tests

| | count |
|---|---|
| New offline proving tests | **72 passed, 0 failed** |
| Full backend suite, `DATABASE_URL` unset | **4,245 passed · 678 skipped · 55 failed** (6m53s) |
| Failures in files this step touched | **0** |
| Baseline before this step (from the ingestion audit, `24c8d23`) | 3,867 passed · 677 skipped · 14 failed |

**On the 55 failures — none are mine, and here is the evidence rather than the claim.**
41 of the 55 are in `tests/test_step6_ingestion_sources_offline.py`, which is a *concurrent
lane's* in-flight work on step 6 of this same wave. The rest are scattered across
`test_economy_hardening_offline.py` (3), `test_bypass_closure_offline.py` (3),
`test_phase2_authorization_offline.py`, `test_auth_enforcement_offline.py`,
`test_auth_hardening_offline.py`, `test_phase1_security_boundaries_offline.py`,
`test_retrieval_identity_offline.py`, `test_claim_graph_api_offline.py`,
`test_migration_upgrade_e2e.py` (1 each). The one procedures-adjacent failure,
`test_procdoc_v2_pipeline_offline.py`, asserts on `app/services/ingestion_jobs.py` — a file
this step never touched (it is the double-encoded-payload file the audit flagged). The jump
from 14 to 55 is the concurrent step-6 lane, not this change. **This is stated, not
assumed: the number is not pinned by this step and should not be read as its exit criterion.**

Two bugs the tests caught in *my own* code, both fixed and both now regression-tested:
1. The refusal message ("ast-grep binary not found; set `SL_AST_GREP_BIN`") was being
   overwritten by the parser's generic "no result summary" — the operator would have lost the
   only actionable line. Fixed with an explicit short-circuit.
2. The liveness assertion used the wrong unit and **rejected a genuinely passing rule**
   (§4.4b). The assertion catching my own mistake is the best evidence it is worth having.

# 4. The run (LOCAL SHARD ONLY)

## 4.1 Environment, verified not assumed

- Docker 29.7.2, daemon responding.
- `go` **absent** → `go install` for actionlint is impossible; the pinned image is the only path.
- Local shard: `pgvector/pgvector:pg15`, **port 55441** (55433 was already allocated; **55432
  and the `kel_*` experiment databases were deliberately never touched**), database
  `sl_step4b`, migrations 01–125 applied.
- Pinned and version-verified by running them: `rhysd/actionlint:1.7.12` (`1.7.12`, go1.26.1),
  `ghcr.io/zizmorcore/zizmor:1.29.0` (`zizmor 1.29.0`), npm `@ast-grep/cli@0.45.3`.

## 4.2 The metrics table

| metric | value |
|---|---|
| rules discovered | **184** |
| **accepted Procedures** | **117** |
| quarantined | **67** — all `check_error` |
| rejected | 0 (license gate allowed all) |
| discovered == accounted | **184 == 184 ✓** |
| Procedures carrying a `verifier_check` | **117 / 117** |
| distinct `source_key` | 117 (re-run: 0 accepted, 113→117 unchanged, still 117 rows — **idempotent**) |
| Goals created (`knowledge_nodes`) | **0** — see limitation L3 |
| **model spend** | **$0.00** — this path makes no LLM call |
| wall time (cold) | 125.9 s for 184 checks |
| wall time (warm) | ~18 s |
| **seconds per accepted Procedure** | **1.08 s** |
| bytes per item | 386 kB total, **3.3 KB/row** average |
| database growth | 17 MB (schema + 117 rows) |
| **check verdicts** | `pass: 117`, `error: 67`, `fail: 0`, `no_input: 0` |

Language spread of the 117: java 28, python 19, cpp 11, go 11, javascript 6, rust 6, c 5,
kotlin 5, ruby 5, swift 5, csharp 4, typescript 4, scala 2, plus 5 one-offs from upstream
casing variants (`C`, `Cpp`, `JavaScript`, `TypeScript`, `html`, `php`).

**`fail: 0` is the number to read twice.** Not one of the 117 passed a broken rule, and not
one of the 67 failed its assertions — all 67 failed to *load*. So this corpus has produced
zero evidence about rule quality, only about rule *loadability*.

## 4.3 The 67 quarantined: an upstream defect, not ours

All 67 are `Error: Cannot parse rule` (exit 8): they use a prelude `utils:` form
(`PATTERN_1(identifier)`, `PATTERN_3(field_expression)`) that ast-grep 0.43.0, 0.44.1 and
0.45.3 all reject as a reserved-character utility id. Per-rule isolation does **not** rescue
them — each fails individually. They become no Procedures. Yield is **117/184 = 63.6%**, and
I had projected ~154 before running it; the correction is recorded in the research doc and in
Q-STEP4-4 rather than quietly dropped.

**Standing maintenance item:** the corpus must be re-checked whenever ast-grep or the
upstream commit moves. 67 rules is a lot of unrealised knowledge.

## 4.4 Two more defects found by running, not reading

**(a) Test files do not follow the naming convention for 5 rules** — one is literally
`networkcredential-hardcoded-secret-python-test.yml` for a **csharp** rule, one has a
`typecript` typo, one filename is missing a path segment. Joining by filename would have
wrongly quarantined 5 usable rules. Fixed to join on the **declared `id`** (what ast-grep
itself matches on, unique by the corpus's own contract). Recovering these took the yield
from **113 to 117**. The inverse is counted too: a test matching no rule is reported as
`test_without_rule`.

**(b) `N passed` counts rule test FILES, not snippets.** A test with 1 valid + 1 invalid
case reports `1 passed`. My first liveness assertion compared against
`len(valid)+len(invalid)` and correctly rejected a passing rule. Expectation is now 1 test
group, with `snippet_count` kept for reporting only, plus a new assertion that a pass must
*name* the rule it checked.

# 5. License and safety

- ast-grep-essentials **Apache-2.0** (root `LICENSE` governs the rule content) → **ALLOW** on
  `repo_license_policy.DEFAULT_ALLOWLIST` (`repo-license-allowlist@v1`), verified per item.
  Recorded honestly: `package.json` declares `"license": "ISC"`. Both are on the allowlist so
  the verdict is the same, but the reason string cites the root LICENSE because that is what
  covers what we ingest.
- actionlint MIT, zizmor MIT, ast-grep CLI MIT — all ALLOW.
- Sandbox: `--network none --read-only --cap-drop ALL --security-opt no-new-privileges
  --user 65534:65534 --memory 512m --cpus 1.0 --pids-limit 128 --rm`, plus a hard timeout.
- ast-grep has **no anonymously-pullable container image** (`ghcr.io/ast-grep/cli` → denied;
  `ast-grep/cli` does not exist on Docker Hub), so it runs as a pinned **local** binary with a
  scrubbed environment. That is a genuine reduction in isolation for one of the three tools,
  stated in the module docstring rather than glossed.

# 6. Risks and honest limitations

- **L1 — the whole point is unproven.** No false-accept rate exists for these tools and this
  run measured **zero** real-world verifier outcomes. We now *record* verdicts with full
  config, which is the precondition for measuring one. Until then, treat a `pass` as
  "this rule fired as its author asserted", never as "this code is safe".
- **L2 — 63.6% ceiling** from an upstream defect we cannot fix. Re-check on every tool bump.
- **L3 — no Goals created.** The Procedure's `goal` column is populated (the goal statement),
  but no `knowledge_nodes` Goal row exists: this path deliberately skips LLM goal
  adjudication to stay at $0. If step 7 or routing needs goal-linked rows, that is extra
  work, not a bug in this step.
- **L4 — snapshots not run** (`--skip-snapshot-tests`). We assert fire/no-fire, which is what
  a Procedure's check means; upstream's byte-exact message/span snapshots are their own
  regression hygiene. Recorded in the stored config so it is not a silent omission.
- **L5 — pre-existing duplicate migration numbers** found while testing: 72, 73, 74 and 110
  each have two files. Ordering and checksum tracking for those is ambiguous. Not caused by
  this step, not fixed here, but it should be looked at.
- **L6 — the 55 suite failures are not pinned by this step.** See §3.

# 7. Board questions filed (I own these)

`.scratch/build-board.md`, section *"2026-09-28 — STEP4 …"*. All non-blocking, all
implemented behind their proposed defaults so everything lands either way.

- **Q-STEP4-1 (the one that matters)** — keep `screening.CHECK_TYPES` untouched; new
  vocabulary `VERIFIER_CHECK_TYPES` + migration 125. *Ratification flips a name, not a design.*
- **Q-STEP4-2** — five-valued verdict, not boolean. *Proposed: adopted.*
- **Q-STEP4-3** — a single verifier pass must not by itself close a Procedure. *Proposed: two
  independent signals before `verified`; a single pass may feed a `procedure_check` routing
  observation, which is a prior and not a verification claim.*
- **Q-STEP4-4** — the 67 unparseable rules, with corrected numbers. *Proposed: as implemented.*
- **Q-STEP4-5** — no measured base rate; `--no-ignores` always, recorded in the stored config.

# 8. Full-scale command, cost and time

There is no larger run to propose: **the corpus is 184 rules and all 184 have been run.** The
only scaling knob is re-running against a newer upstream commit or a newer ast-grep, which is
a re-check rather than an expansion.

```powershell
# local shard only
$env:DATABASE_URL = "postgresql://postgres@127.0.0.1:<port>/<db>"   # NEVER a kel_* db
$env:SL_AST_GREP_BIN = "<path to pinned ast-grep 0.45.3>"
python scripts\ingest_ast_grep_rules.py --root "<checkout of ast-grep-essentials>" --full-scale-cost
```

- **Cost: $0.00** — no LLM call on this path, at any corpus size.
- **Time: ~18 s warm, ~126 s cold** for 184 checks; 1.08 s per accepted Procedure.
- **Idempotent**: re-running reports every row `unchanged` and creates nothing.
- **actionlint / zizmor are built and proven but have no Procedures yet** — they attach to
  **step 6** (workflow histories), which is where the GitHub Actions content comes from.

**A production run needs the user's go-ahead.** The script refuses a non-local DSN without
`--allow-production`, and refuses `kel_*` on 55432 outright.

# 9. Coordination flag — read before step 7 merges

Observed in the working tree while this step was finishing: **step 7 has already landed
`backend/app/services/ingestion_sources/codemod_checks.py` and
`backend/app/services/ingestion_sources/openrewrite.py`, plus
`.scratch/ingestion/step_7_SUMMARY.md`.** This step's `check_runner.py` did not exist when
those were written, so **step 7 may have built a second, parallel check path.**

That is a real integration risk and not something to resolve silently. Two possibilities,
and they need different work:

- **Step 7's checks are independent** (it needs a JVM and a long timeout, which
  `check_runner`'s `512m`/`1 cpu` container profile will not suit). Then the honest answer is
  that there are two runners with two isolation contracts, and someone should own the
  question of whether that is intended.
- **Step 7 duplicated this runner.** Then one of the two should be deleted and the other
  extended — most likely keeping `check_runner.py` as the single security contract, since it
  is the one with a pure, flag-asserted isolation argv and 9 tests that fail if a flag is
  dropped.

**What I did NOT do:** I did not touch step 7's files, and I did not add a fourth check type to
`VERIFIER_REGISTRY` on its behalf. `VERIFIER_CHECK_TYPES` stays a closed three-value
vocabulary; adding a codemod type is step 7's board question to file, exactly as §1.2 says.

**Recommended next action:** whoever integrates steps 6 and 7 should read
`check_runner.py`'s module docstring and `step_7_SUMMARY.md` side by side and decide this
explicitly, before either lands. It is a one-hour decision that is much cheaper now than
after both are merged.
