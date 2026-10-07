# `stealthlab-mcp survey`: evaluation (Workstream C, 2026-10-07)

What was built for plan §4 (`docs/plan_2026-10_priors_library_survey.md`), how it was measured, and what is still open. All numbers below were measured on this machine (Windows 10, Node 24, git 2.50) on the date above.

## What it is

`stealthlab-mcp survey [path]` is the deterministic half of `survey_repo`. It lives in the npm client (`packaging/npm/lib/survey/`, no dependencies), runs locally and uploads nothing.

| step | module | output |
|---|---|---|
| list files: `git ls-files -s -t` (sparse/skip-worktree and submodules included), or a `.gitignore`-aware walk without git | `files.mjs` | — |
| resolve units: every workspace kind in plan §4.1, exact glob/negation expansion, nested declarations as child units, Bazel/Buck/Pants top-level groups, docs/notebooks/mobile/embedded/data kinds, vendored/generated/submodule zones, fixtures and examples as AUX, template clustering | `units.mjs` | `index/units.idx` |
| repository identity: remote (upstream > origin, SSH = HTTPS, GitLab subgroups, Azure `_git`, mirror map, partial clones) → root commit (not on shallow clones) → stored id → weak manifest/folder id | `identity.mjs` | `meta.json` `repo_identity` |
| deterministic facts, each cited `path:line`: version pins, engines, scripts and Makefile/justfile targets, CI commands with their working directory and build matrix, frameworks, Dockerfile/compose/env names, conventions, absence facts | `facts.mjs` | `claims.md`, `claims/<unit>.md` (`by=scanner` lines, stable ids) |
| validator for every fact (scanner's and agent's) | `validate.mjs` | `survey/rejected.md` |
| coverage per unit (topics covered or `absent` with `of=`), lazy mode above 40 units, read budgets | `survey.mjs` | `survey/worklist.md` |
| bounded history mining: fix commits → `library.md` (plan §3.2 grammar) + `library/solutions/<id>.diff`, 64 KB budget with `library/archive-historical.md` | `history.mjs` | `library.md` |
| incremental state: content hash of every cited file | `survey.mjs` | `index/survey.state.json` |

The `survey_repo` prompt (`backend/app/mcp_server/prompts.py`) now drives scanner → worklist → validator, asks for coverage instead of "at most 200 facts", and no longer asks the agent to compute hashes. The prompt hook (`lib/hook.mjs`) sends `claims.md` plus the pages of the units a prompt names (or the agent's working directory), within the server's 64 KB budget.

### Validator rules

- `source=path:line` must exist. Paths are posix and case-fixed against the listing; a symlink is resolved once; under a sparse checkout a missing file is `unverifiable`, not wrong.
- `#sha=` is the first 7 hex characters of the sha1 of the cited line after CRLF → LF and trailing-whitespace stripping. A moved line is re-anchored when the match is unique; a changed line makes the fact `stale`. A legacy whole-file `git hash-object` sha is accepted once and re-stamped.
- Agent facts:
  - text in backticks or quotes, and version numbers, must appear at the cited line (±3), elsewhere in the file (then the citation moves to that line), as an existing path, or as a command the manifests/CI confirm;
  - a blank line cannot be cited;
  - a statement with nothing checkable must share a content word with the cited lines;
  - secrets are rejected.
- Commands are cross-checked: npm/pnpm/yarn/bun scripts (with `--filter`/`-r`/`workspace`), make/just targets, pytest/tox/nox, cargo `-p`, go, Maven/Gradle wrappers, dotnet, bazel/buck/pants, mix, flutter/dart, bundle, composer, deno tasks, compose files. A command that contradicts them is rejected.
- A literal `|` inside a field is written `¦`. Fields are pipe-separated, and the old sanitiser turned `^20 || >=22` into `^20 // >=22`.

## Tests

- **`packaging/npm/test/survey.test.mjs`**: 45 offline tests (1 skipped on this machine: creating symlinks needs Developer Mode on Windows).
  - **Fixture generator** (`test/fixtures/survey/repos.mjs`): one repository per shape in §4.1, 20 shapes. They are single npm; pnpm with negation; npm workspaces + Nx; Lerna + Rush; Cargo workspace; go.work; uv workspace; several Python packages; Gradle with includeBuild and projectDir; nested Maven; .NET .sln; Bazel; Bazel with a container directory; CMake; Elixir umbrella + melos; polyglot; vendored/generated zones; special kinds; no manifest; dotfiles.
  - **Every §4.6 edge case**: shallow clone, no git, CRLF, symlink, sparse checkout, case-insensitive paths, nested workspaces, incremental one-file change, lazy mode, read budgets, history redaction and archiving, partial-clone remotes.
  - **Regression tests** for every precision bug found on real repositories.
- **Full npm suite:** 157 pass, 0 fail, 4 skipped.
- **Backend:** `tests/test_mcp_prompts_offline.py` (2 new tests), `test_repo_facts_offline.py`, `test_stealth_pipe_format_offline.py`: 56 pass. The server's own CLAIM parser accepts `scope=unit:<path>` and the extra `by=`/`key=`/`inherits=`/`of=` fields unchanged.

## Performance gates (§4.6)

`scripts/survey-bench.mjs`. Each run is a fresh process, `--no-history`; "cold" means no `.stealth/` yet.

| repository | files | cold | incremental | peak memory | gate |
|---|---|---|---|---|---|
| synthetic pnpm monorepo, 500 packages | 100,000 | 3.8 s | 2.4 s | 138 MB | < 10 s, < 500 MB: **pass** |
| synthetic pnpm monorepo, 500 packages | 1,000,000 | 10.9 s | 9.1 s | 368 MB | < 60 s, < 500 MB: **pass** |
| Linux kernel (`--depth 1 --filter=blob:none --sparse`) | 96,055 | 1.8 s | 1.5 s | 94 MB | < 10 s: **pass** |

- **Synthetic repos:** the entries beyond the 500 packages on disk are git index entries marked skip-worktree, which is how a 1M-file monorepo is usually checked out. Getting under 500 MB at 1M files took four changes:
  - parse `ls-files` from a temp-file buffer, so there's no second in-memory copy;
  - no per-file sha map;
  - index only the basenames the resolver looks up;
  - have `readText` allocate the file's size rather than its cap.

  Before those changes the peak was 770 MB.
- **Token gate:**
  - **Result:** the estimated agent input for a 500-package monorepo is 1,525 tokens, against 1,568 for a single-package repository (0.97×; the gate is < 3×). The figure is the same with and without lazy mode: the 500 members form one template and their manifests state their purpose, so only the root needs an agent pass.
  - **What this measures:** the estimate is the bytes on the worklist's read lists ÷ 4, plus 1,500 tokens per unit. That is the agent's input budget, not a measured LLM run.
- **Incremental gate:** after a one-file change, only the facts citing that file change (test `incremental: ...`).

## Gold set (24 repositories): development set

`scripts/survey-gold.mjs`, `survey-gold-repos.json`, `survey-gold-truth.json`. The repos are `--depth 1` clones of ky, vite, jest, nest, ripgrep, axum, cobra, opentelemetry-go, requests, poetry, fastapi, okhttp, spring-petclinic, MediatR, fmt, rules_go, phoenix, laravel, rails, mkdocs-material, dotfiles, nowinandroid, flutter samples, and this repository.

**How truth was written.** Truth units come from each repository's own declarations, expanded with git's `:(glob)` pathspec matcher and small independent parsers (not the scanner's code). Key facts are hand-written regexes for run/test/build commands and runtime versions.

**This is a development set.** I fixed scanner bugs it exposed, and I also corrected truth-file mistakes. Each correction was checked by reading the repository and is recorded in the truth file's `_corrections`:
- `docs/` (Sphinx) in requests and `support/` (an Android build) in fmt are units;
- `go/tools/*` in rules_go have their own `go.mod`;
- `{"type":"module"}` marker `package.json` files are not packages;
- nowinandroid's `build-logic/convention`;
- flutter-samples' native host apps.

| metric | first run | final |
|---|---|---|
| unit precision | 0.915 | 1.000 |
| unit recall | 0.982 | 1.000 |
| key-fact recall | 0.930 | 1.000 |

**Fact precision.** Seeded random samples of about 12 cited facts per repository, each hand-checked against the cited lines by me (one annotator). A fact counts as wrong if the statement is false or misleading, or if the cited line does not show it.

| sample | facts | correct | precision |
|---|---|---|---|
| seed 20261007, before fixes | 276 | 264 | 95.7% |
| seed 424242, a fresh sample after fixes | 277 | 271 | **97.8%** (gate ≥ 97%) |

The 18 errors across both samples were all fixed afterwards, with regression tests:
- `||` rendered as `//`;
- multi-line CI scripts read line by line (jq bodies, `\` continuations, heredocs);
- Go `// indirect` dependencies;
- a docs build command asserted without evidence;
- a Gradle Java version cited at a different line than the one it was read from;
- `dotnet-version: |` block values;
- a standalone Gradle build given a root-relative task;
- `app: :phoenix` read as a dependency;
- a nested JSON key cited instead of the top-level one;
- `apply false` plugins;
- convention plugins that register rather than apply;
- `includeGroupByRegex` parsed as `include`;
- backticks inside commands;
- Dockerfile templates and `ARG` defaults.

## Held-out set (6 repositories): truth written before the first run

flask, gin, tokio, retrofit, serilog, svelte (`survey-heldout-truth.json`). The scanner was run once, and the numbers below are from that run.

| metric | held-out run | after the fixes it triggered |
|---|---|---|
| unit precision | 0.994 (one extra: retrofit `website/`, an Astro docs site with its own `package.json`. On inspection it is a real unit that my truth missed) | — |
| unit recall | 1.000 | — |
| key-fact recall | 0.944 (17/18) | 1.000 |
| fact precision (72 sampled, hand-checked) | **94.4% (68/72)**, below the 97% gate | **98.6% (71/72)**, fresh sample (seed 31337) |

The held-out errors, all fixed afterwards with tests:
- multi-line Makefile recipes summarised by their first line (2);
- `<TargetFrameworks Condition=...>` combined with `$(TargetFrameworks)` (1, which is also the key-fact miss);
- the JavaScript body of `actions/github-script` read as a shell command (1).

The one miss in the re-sample was a Gradle `java-platform` (BOM) module given a `test` command it does not have. It is now fixed with a test.

## Held-out set 2 (6 repositories): shapes the first sets did not cover

deno std (a Deno workspace), commons-lang (single Maven project), ecto (Elixir), ruff (Rust workspace + Python + an npm playground), pytest (tox, Sphinx docs in `doc/en`), starlight (pnpm docs monorepo); see `survey-heldout2-truth.json`.

Truth was written before the first run. It deliberately counts what a repository *actually* builds or ships, including packages its workspace does not list (ruff's `playground/api`, `playground/deploy`). So this set also tests where "declared workspaces are exact" is too strict.

| metric | held-out run | after the fixes it triggered |
|---|---|---|
| unit precision | **0.940** | 0.996 |
| unit recall | **0.895** | 0.995 |
| key-fact recall | **0.944** | 1.000 |
| fact precision (64 sampled, hand-checked) | **96.9% (62/64)**, just below the gate | not re-sampled |

What it found, all fixed with a regression test:
- **No Deno workspace support.** `deno.json` `"workspace": [...]` was not parsed. Members still showed up through their own `deno.json` files, except `testing`, which matched the fixture-directory list.
- **Fixture cases counted as packages.** 29 `pyproject.toml` files under ruff's `crates/ty_completion_eval/truth/<case>/` became units. New rule: five or more undeclared sibling manifests of the same kind inside another package are a fixture batch.
- **Real packages a workspace does not list were dropped.** ruff's `playground` (a real app: "playground" was on the fixture list) and its `api`/`deploy` packages. New rule: an undeclared npm package that has a name and scripts, and that the workspace does not explicitly negate, is a unit.
- **Sphinx docs** found only directly under `docs/`, so pytest's `doc/en` was missed.
- **Fact errors:**
  - ecto's root reported as "named `root`" (mix `app: :ecto` not read);
  - a quoted shell assignment reported as a CI command.

Remaining after the fixes:
- ruff `scripts/benchmarks` is still treated as a fixture directory (the "benchmarks" name rule). I kept that policy; it is the one remaining recall miss.
- deno std `crypto/_wasm` (a Rust → WASM crate) is found but was missing from my truth. It is the one remaining extra.

**Honest reading.** Across the two held-out sets, before any fixes, deterministic fact precision was 94.4% and 96.9%. Unit detection was 0.994–1.0 on the first set but 0.94 / 0.90 on the second, because it hit three shapes I had not implemented: Deno workspaces, fixture batches and undeclared real packages. Each held-out round found new parser gaps, and fixing them did not regress the earlier sets: all three sets were re-run after every change. Expect the next unseen shape to cost a few points the same way. 72 + 64 facts is a small sample, and one annotator is a limitation.

## End to end: the agent half

I followed the new `survey_repo` prompt on ky as the agent: read only the worklist's files, wrote facts, ran `--validate`.

- **What the validator rejected:**
  - a deliberately wrong probe (`npm run lint`: no such script);
  - a fact whose backticked command I had abbreviated with `...`. This led to the prompt rule "copy exactly, never abbreviate".

  It also accepted a fact citing a blank line. That led to the blank-line and shared-word rules; with them in place, the fact was rejected and fixed.
- **Result:** after the fixes, the worklist was empty and the root unit was complete.
- **Limit:** this is one repository. The agent half's precision at scale (many repositories, several agents) is **not measured**.

## History mining on real history (this repository)

- **Speed:** 1,077 commits, of which 997 are in the 2-year window. Mining took 4.5 s (53 s before diffs were batched to 50 commits per `git show`).
- **Output:**
  - 136 fix entries;
  - 6 with secret-looking hunks dropped (`redacted=1`);
  - 7 diffs truncated at 64 KB (`diff=truncated`).
- **Size:** `library.md` is 65 KB within its budget; the older entries are in `library/archive-historical.md`.
- **Fix-commit filter:**
  - a conventional-commit type decides (`fix:` yes; `feat:`/`perf:` no, whatever the body says);
  - otherwise the subject must read like a fix;
  - the body counts only for "Fixes #N".

## Open items

1. **Publishing:** the prompt tells agents to run `npx -y stealthlab-mcp survey`. That works only once a version with `survey` is published to npm (publishing is the user's step). Until then the prompt's manual fallback applies.
2. **Workstream B:**
   - `find_ways` does not yet receive `repo_identity` (a new optional argument that B owns). `meta.json` already holds it, including `strength`, so B can stop `weak` identities matching other repositories.
   - `library.md` entries use B's §3.2 grammar but B's generated indexes do not exist yet.
3. **`--verify-commands`:** dry runs exist for pytest (`--collect-only`), `make -n`, `cargo metadata` and `go list`. Other ecosystems are only checked statically.
4. **Symlinks:** the symlink test is skipped on this Windows machine. The code path is covered on systems that allow symlink creation.
5. **Evaluation:** the gold set is a development set, and precision was judged by one annotator. A larger held-out set and a second annotator would firm up the 97% claim.
