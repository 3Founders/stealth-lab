# Step 7 Summary — codemods as checkable Procedures

Date: 2026-09-28
Scope: `## Common` + `## Step 7` of `.scratch/prompts/ingestion_build_prompts.md`
Spend: **$0.00** (cap was $2/day). This source makes zero LLM calls by construction.
Target: local shard only. **No production writes.**

---

## 1. What I researched

Full report with 52 cited sources: `.scratch/ingestion/step_7_research.md`.

Primary sources read directly: the `codemod` npm registry metadata (1.18.3, Apache-2.0,
`engines.node >= 16`), the `nodejs/userland-migrations` repository at pinned commit
`48b9b9a1d1385e7f1d2de8a8482d557447f512c0` (1,779 paths enumerated), the
`docs.openrewrite.org` sitemap (4,680 URLs, live-fetched), the Moderne Source Available
License text itself (6,658 bytes, read in full), and `docs.moderne.io`'s module table.

Five findings that changed the design:

1. **The Node.js codemods are not jscodeshift.** They are JS ast-grep (`codemod jssg`)
   rules driven by a Rust CLI. `jscodeshift` does not appear in the dependency graph.
   The research's hypothesis about the popular test framework was wrong: there are zero
   jest snapshots and zero `__testfixtures__` directories; there are three distinct
   hand-rolled layouts.
2. **The license risk is inverted from the plan.** One root MIT `LICENSE` covers all 40
   recipes, and all 16 `@nodejs/*` npm names 404 — there is no published artifact whose
   license could diverge. The *hosted* 1,220-package registry, not this repo, is the
   actual risk surface.
3. **The OpenWrite reversal the plan warned about is real and severe.** 30 of 52 modules
   are Apache-2.0; 22 are Moderne Source Available. The license text forbids making the
   functionality available to third parties as a service and names Sourcegraph and
   Amazon Q Code Transformer as prohibited. `rewrite-spring` flipped on 2024-12-13 and
   Spring Tools dropped it entirely. **1,263 recipe pages (28% of the catalog) are out.**
4. **Structural gotcha the plan does not mention:** Moderne repos carry a 43-byte pointer
   file, `LICENSE.md` → `LICENSE/moderne-source-available-license.md`. GitHub's licensee
   reports `NOASSERTION`. A gate that reads only the root file mis-classifies all 22.
5. **A fixture is a self-consistency check, not a correctness check** — and the catalog
   ships broken `expected` outputs. This is why Gate B exists (see §5).

## 2. What I built

Five new files, all in this lane. **No pre-existing file was modified** — verified: every
` M ` entry in `git status` belongs to another lane (Step 3's `admin.py` /
`ingestion_jobs.py` / `trace_worker.py` work, and a concurrent `queue.py` edit that
appeared mid-task and contains no codemod reference), and `screening.py`,
`repo_license_policy.py`, `dispatch.py` and the `skillmd_*` files are untouched.

| File | Lines | Role |
|---|---|---|
| `backend/app/services/ingestion_sources/codemod_checks.py` | 1097 | sandboxed check runner: Gate A (fixture suite) + Gate B (output well-formedness) |
| `backend/app/services/ingestion_sources/codemod_node.py` | 912 | `userland-migrations` adapter, `source_type="codemod_node"` |
| `backend/app/services/ingestion_sources/openrewrite.py` | 1150 | OpenRewrite catalog adapter, `source_type="openrewrite"` |
| `backend/app/ingestion/codemod_cli.py` | 355 | `ingest-codemods` pilot command, dry-run by default |
| `backend/tests/test_ingestion_codemods_offline.py` | 1558 | 31 offline proving tests |

Step 4's shared check runner has **not** landed, so `codemod_checks.py` is a minimal
runner behind the same conceptual interface (`CheckOutcome` with `tier`, `semantics`,
`case_count`, `gates`, `failures`), designed to be replaced by Step 4's without touching
the adapters. `screening.py` is not imported by any of the five files (pinned by test).

## 3. Tests

```
python -m pytest tests/test_ingestion_codemods_offline.py -q
31 passed
```
`DATABASE_URL` unset. No network. 24 → 31 tests after the real-execution fixes in §5;
each new test names the catalog fixture that exposed its bug.

Full suite is not claimed: the working tree carries two other lanes' in-flight work
(Step 3 and Step 6 files), so a whole-suite number here would not be attributable.

## 4. The runs

Both against local shard `postgresql://postgres:postgres@127.0.0.1:55432/sl_step7`
(PostgreSQL 15.19 + pgvector 0.8.6 in Docker, all 127 migrations applied).
**`--dry-run` throughout. Nothing was written.**

### Node.js — all 39 discoverable recipes, real execution

| | |
|---|---|
| recipes discovered | 39 (of 40 dirs; `correct-ts-specifiers` has no `codemod.yaml` and is excluded from the npm workspaces) |
| accepted | **39** (one root MIT `LICENSE`, `repo-license-allowlist@v1` ALLOW for all) |
| rejected | 0 |
| **checks passing** | **38 / 39** |
| **fixture cases executed** | **434**, 0 failures |
| negative (no-op) cases | 32 |
| well-formedness verdicts | 28 `wellformed`, 6 `no_parser`, 3 `duplicate_declaration`, 2 `inherited_unparseable` |
| wall time | 65.6 s for the whole catalog |
| bytes produced | 67,223 |
| **dollars** | **0.00** |

**The one recipe that does not pass: `v22-to-v24`.** It has no `src/`, no `tests/`, and
its test script is literally `echo "no test necessary"` — it reports PASS with zero
fixtures. This is the most-requested migration class in the catalog and it is entirely
unverified. Its gate reads `jssg_fixture_suite: not_run`, and it must not be served as
trusted knowledge. (Note: a naive count of "39/39 pass" would have hidden this; the
`case_count: 0` is what exposed it.)

Cross-check against the catalog's own `npm test`, run independently before my runner
existed: **39/39 recipes green, 397 cases, 0 failures.** My runner's 434 is higher
because it also runs the two `remove-dependencies` transforms and the `tests/` root that
the recipes' own scripts invoke differently. The two agree that nothing is actually broken.

### OpenRewrite — 900-page live crawl

| | |
|---|---|
| pages seen | 2,564 |
| **accepted (Apache-2.0, static check)** | **701** |
| `rejected:moderne_source_available` | **1,535** |
| `rejected:module_archived` | 124 |
| `rejected:no_ruling` | 4 |
| `OpenRewriteIdUnavailable` (id not extractable) | 168 |
| `ConnectError` / `RuntimeError` (network) | 31 |
| modules represented | 10 (`rewrite-third-party` 321, `rewrite-java` 155, `rewrite-maven` 95, `rewrite-core` 59, …) |
| **all accepted are Apache-2.0** | yes, 701/701 |
| check tier | `static` for all 701 — **no executed assertion, ever** |
| wall time | 875 s (network-bound) |
| **dollars** | **0.00** |

**The ≥50 acceptance bar is met ~14× over.**

`tech.picnic:error-prone-support` (1,098 pages, 24% of the whole catalog) is rejected
as `third_party_unverified` — it is not an OpenRewrite repo and its license was never
verified.

## 5. What real execution found that reading the code did not

The first build was green in tests and wrong in reality. Running the actual `codemod`
CLI exposed **eight** defects, each of which made a correct catalog look broken or a
broken one look green. All are fixed and pinned:

1. **`--output-format json` does not exist.** The flag is `--reporter json`. The wrong
   name is rejected by clap with exit 2, so every recipe read as "all fixtures failed."
2. **The reporter emits newline-delimited JSON, not one document.** `json.loads` on the
   whole stream raised, so every recipe read as an unreadable result. Now the terminal
   `suite` event is the authority, and a stream with case lines but no summary is
   explicitly *not* rounded up to a verdict.
3. **One transform must not be run against the whole `tests/` directory.** A
   multi-transform recipe shares one directory and each transform owns a subset, declared
   by its own `--filter`. Ignoring it made `timers-deprecations` report 3/15 on a green
   catalog. Filters are now parsed from the recipe's own script, never reconstructed.
4. **The declared `-l` language beats the manifest's `targets.languages`.**
   `chalk-to-util-styletext` runs its dependency-removal transform as `-l json`; forcing
   the manifest value made the tool emit nothing at all.
5. **Manifest capabilities and declared flags must be merged, not concatenated.** The
   same `--allow-fs` appears in both; passing it twice makes clap exit 2 before any test
   runs, which reads identically to a missing tool.
6. **`--strictness` must keep its value.** Dropping `cst` turns it into a missing-argument
   error, same symptom again.
7. **A `.ts` file under `src/` is not automatically a transform.**
   `fs-access-mode-constants` keeps its own harness at `src/workflow.test.ts`; running it
   resolves `node:assert/strict` in the sandbox and fails all 8 cases. Only transforms a
   declared command names are run; the rest are recorded in a `undeclared_src_entries` gate.
8. **An unparseable `expected` output is not automatically a defect.** Two of the five
   remaining Gate B hits are fixtures whose *input* is equally unparseable, and three
   declare a binding twice on purpose. Gate B now asks whether the transform
   **introduced** the defect by comparing the pair, which is the only question it exists
   to answer.

I also found and fixed a real bug in the delivered code: **Gate A and Gate B shared one
`node_bin` parameter.** `codemod jssg test` is a Rust CLI and `node --check` is the
runtime's parser; passing the same path to both makes every `.js` fixture look malformed
rather than misconfigured. They are now separate parameters (`node_bin`, `node_exec`),
which turns that mistake into a `TypeError` at the call site, plus a CLI flag and a test.

## 6. The license gate, verified independently

I did not take the gate on trust. `repo_license_policy.py` is reused unmodified. Run
directly against the three shapes that matter:

| Input | Verdict |
|---|---|
| Moderne pointer file (`LICENSE.md` → `LICENSE/moderne-…md`), repo says `Other` | **QUARANTINE** |
| `NOASSERTION` | **QUARANTINE** |
| `Apache-2.0` at root | **ALLOW**, `repo-license-allowlist@v1` |
| unidentifiable license text | **QUARANTINE** |

The pointer-file case is fail-closed for a subtle reason: `license_paths()` only collects
blobs whose filename stem starts with `license`/`licence`/`copying`, and
`moderne-source-available-license.md` does not. So the pointer resolves, its own text is
unidentifiable, and the verdict is QUARANTINE. That is the right answer reached by a
route nobody designed for it, so it is pinned by a named test rather than left to chance.

## 7. Honest limits

- **A passing check means self-consistency, not correctness.** The fixture is maintained
  by the same party as the transform. The payload carries
  `check_semantics: "self-consistency"` and a claim bounded to *"this transform
  deterministically maps these N input shapes to these N output shapes."* No code path
  emits "verified correct", and a test fails if that string ever appears.
- **The 701 OpenRewrite Procedures have no executable check.** Executing them needs
  JDK 21 (a JRE is explicitly insufficient), Gradle, network dependency resolution and a
  Code Genome token — not compatible with this budget or with offline CI. They are marked
  `check_tier: "static"`. **Do not serve them as verified knowledge.** This is the one
  part of the acceptance criterion I met on static rather than executable checks, and it
  is a genuine shortfall, not a shortcut.
- **No writes were performed.** `--apply` is implemented but unexecuted; it refuses any
  non-loopback DSN. `ingest_skill_md` has no `provenance` column parameter, so
  authoritative per-artifact provenance lives in the report and the document body.
- **Knowledge-item counts are `null`, listed under `unmeasured`.** I did not measure
  Procedures/Claims/Goals created and will not report zeros for counts nobody produced.
- **`ARCHIVED_REPOS` holds 15 of the 23 archived repos the research named.** A module in
  an unnamed archived repo would be admitted; the docstring says so.
- **Version drift is unverified at newer versions.** 44 of 64 sampled doc pages declare a
  module version the license table does not (e.g. 8.92.9 vs 8.90.1). Both are recorded and
  the drift is added to `provenance_limits`; no license was re-verified at the newer
  version. Namespace→module attribution is a curated table plus one default that errs
  permissive for uncatalogued `java/*` namespaces.
- **The Node.js adapter reads a local checkout and never fetches.** At full scale this
  means a clone-and-pin step per run, not an HTTP reader.
- **`v22-to-v24` is ingested but not trusted.** It passes a zero-fixture check. Consider
  quarantining it outright.

## 8. Full-scale command and projection

```powershell
# Local shard, both sources. Read-only; add --apply (loopback DSN only) to write.
cd backend
$env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55432/sl_step7?sslmode=disable"

# Node.js: 39 recipes, ~434 executed cases. Needs a clone at the pinned commit and
# the CLI:  npm i -g codemod@1.18.3  (and `npm install` in the checkout for the
# workspace deps the transforms import).
python -m app.ingestion.codemod_cli ingest-codemods --source nodejs `
  --checkout <clone> --commit 48b9b9a1d1385e7f1d2de8a8482d557447f512c0 `
  --node-bin codemod --node-exec node --limit 40 --out node.json --dry-run

# OpenRewrite: 701 Apache-2.0 recipes from a 900-page crawl.
python -m app.ingestion.codemod_cli ingest-codemods --source openrewrite `
  --max-pages 900 --limit 900 --out openrewrite.json --dry-run
```

| | Node.js | OpenRewrite |
|---|---|---|
| items at full crawl | 39 | 701 (of ~2,564 pages seen) |
| time | ~66 s | ~875 s (network-bound) |
| **dollars** | **$0.00** | **$0.00** |
| shard bytes | 67 KB produced | 64 KB per 50 items |
| check tier | executable | static |

**Projection: this entire source costs $0 to ingest at any scale.** The only cost is
wall time and the one-time `npm install`. That is the property that makes it worth doing
before the LLM-priced sources, and it is a direct answer to step 0's question about what
sets the budget for everything else: codemods contribute coverage at zero marginal cost.

## 9. Risks and recommended follow-ups

1. **Get a human or second opinion on the OpenRewrite static tier before serving it.**
   A static declaration check is weaker than the plan assumed. Either accept it as
   candidates-not-knowledge, or fund a credentialed Gradle run for the top N.
2. **Quarantine `v22-to-v24`.** Zero fixtures, most-requested migration class.
3. **`correct-ts-specifiers` is invisible to the adapter** (no `codemod.yaml`, bespoke
   `node --test` harness, excluded from workspaces). It is 1 of 40 recipes.
4. **Fill in `ARCHIVED_REPOS` to all 23** so the archived-module gate is complete.
5. **Per-recipe OpenRewrite license verification at ingest time** rather than trusting
   the module table; 44 of 64 sampled pages already carry a version the table lacks.
6. **Board question, per hard rule 3 (spec frozen):** the plan's Step 7 assumes a check
   kind for codemod transforms. `routing.CHECK_KINDS` is a closed vocabulary
   (`benchmark | tests | procedure_check | judge | self_report`) and `screening.CHECK_TYPES`
   is a separate closed set. I recorded `check_kind: "benchmark"` with
   `check_semantics: "self-consistency"` and did **not** extend either vocabulary.
   Proposed default: leave both closed; the tier/semantics fields carry the distinction.
   Needs a ruling before the routing tables consume these.
7. **Step 4's runner will supersede `codemod_checks.py`.** When it lands, the adapters
   should be repointed at it and `codemod_checks.py` deleted, keeping the tests that
   encode the eight findings above.
