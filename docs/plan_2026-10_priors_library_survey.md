# Plan (2026-10-07): routing priors, library.md, survey_repo

Four workstreams. Each runs in its own session (prompts in §6).
Shared contracts (§5) are fixed here so the sessions can work in parallel.

## 0. Ground truth this plan builds on

- **Routing** (`backend/app/routing`) is a hierarchical Bayesian IRT model, the same family as MIRT/NIRT-Router, which top RouterArena:
  `logit P = θ[m,t] + γ[s] + δ[m,s] − b[g] + ⟨a[g],z[m]⟩ + ⟨c[π],z[m]⟩ − ε[i]`
  - The Goal side already has a cold start: an embedding regression `W·φ(g)` plus the DAG parents' residuals.
  - The model side has **no** cold start. A model with no predecessor gets `θ ~ N(0, σθ)` and `z ~ N(0, I)`, a pure guess. Only version successors inherit (`SIGMA_NEW_VERSION`, `SUCCESSOR_Z_SD`).
  - "Aggregate-only public results as a binomial likelihood" is in `docs/model_routing_plan.md` but **not implemented**. `routing-import` only takes per-instance rows.
  - Production has 0 routing observations, so it has never been fitted.
- **Model plan:** `find_ways` attaches a model plan only when a `CandidateProvider` is configured or `candidates` are passed (`app/routing/plan.py: wants_plan`). Otherwise there is no model at all.
- **`.stealth` today:** `claims.md` (facts), `procedures.md` (chosen ways), `run.md` (execution plan), `index/*.idx` plus `root.idx`, `meta.json`, journal, `faulted.json`. No file holds a model choice, a model distribution, or this repo's own past solutions.
- **Evidence:**
  - Same-repo past fixes with real diffs lifted "right cause" from 27 to 42 of 96 (p=0.023).
  - Steps-only knowledge did nothing.
  - Repo-first retrieval: 22–24% useful vs 10% over the whole corpus, and 31% when the repo has ≥30 Goals.
  - So **this repo's own solved problems plus code are the core**. Global knowledge is an enhancer.

## 1. Answers to the three open questions

**(a) Why 35–70 facts?** That was my estimate of what a survey of a mid-size repo would produce. It was not derived from anything. The only real rule is the prompt's cap of 200, and a fixed count is the wrong rule anyway.
- New rule (§4): **coverage, not count**. Every (unit × topic) cell gets at least one cited fact or an explicit `absent` fact.
- A single-package repo lands around 40–80 facts. A monorepo scales with its packages and is split into per-unit pages.

**(b) Repo identity** is a stable answer to "which repository is this?":
- the normalised git remote (`github.com/owner/name`), hashed;
- with no remote, the hash of the root commit;
- plus a path for each unit in a monorepo.

It is useful in four ways:
1. **Retrieval.** Most global Goals come from a known repo (SWE-bench-style `owner__repo-NNN`). If the server knows "this is django/django", it can put that repo's 40 past fixes first. That is exactly the setting that gave +15 right causes.
2. **Library bootstrap.** The first time you run in a repo with public history, `library.md` is seeded from it.
3. **Enterprise tier.** The private Goals of the same internal repo match by hash, without exposing the name.
4. **Clones and forks share memory.** Supermemory does the same: the container key is the hash of the normalised remote, falling back to the local path.

**(c) The model should be picked by default.** Agreed. Today, with no provider, the plan says `no_candidates`. Change (§2.4):
- every `find_ways` reply carries a model per step;
- every `library.md` Goal points at a routing row.

The candidate set is always non-empty:
- the caller's own model (MCP `clientInfo` or a declared one), plus
- connected providers (`app/providers`), plus
- the public catalogue the user can run.

With the priors from §2, a recommendation is meaningful even at zero observations.

## 2. Workstream A: a SOTA prior setter (model side), without trained embeddings

**Principle.** Everything we know enters **the one existing Bayesian model**, either as a prior mean (covariates) or as a likelihood (observations). There are no separate heuristics.
- This is what GenRec's "catalog-aware head + content cold start" means for us. The "content" is a model's public metadata and public results, not a trained embedding.
- Embeddings stay frozen features: the Goal `φ`, already stored. We never update them, so no training is needed.

### 2.1 A model card table and covariate priors (new; replaces `θ ~ N(0, σ)`)

`routing_model_card(model_key, family, provider, release_date, training_cutoff, open_weights, log_params?, context_len, reasoning, price_in, price_out, cached_price, public_scores jsonb, as_of, source)`

```
θ[m,0] ~ N( βᵀ x_m , σθ² )          x_m = standardised covariates; a missing one is imputed and gets its own indicator
z[m]   ~ N( B x_m , s_z² I )        skills also start from covariates (code-heavy vs maths-heavy benchmarks)
family pooling: x_m includes a family one-hot with a hierarchical prior, so a new family member with no predecessor starts near its family
```

- `β` and `B` are learned jointly across **all** catalogue models, hundreds of them via public results.
- So the prior for a model released today is a *posterior prediction from its metadata*. This is the IRT version of "content-based cold-start init".
- Successor inheritance stays and takes priority when a predecessor exists.

### 2.2 Public evidence as likelihood (most of the strength)

| source | form | enters as |
|---|---|---|
| SWE-bench / SWE-bench Verified / Multilingual `experiments` repo | per-instance resolved, per submission (model + scaffold) | per-instance observations on **our own leaf Goals**: join by instance id = `row_ref`. Our 20k verified Goals come from these benchmarks, so on day one many Goals have dozens of (model, scaffold) outcomes |
| Terminal-Bench, LiveCodeBench, Aider polyglot, BigCodeBench (per-task where published) | per-instance | same; the benchmark's subtree is the Goal set |
| RouterBench (~405k outcomes, 11 models) and RouterEval (~200M records, ~8.5k LLMs, 12 benchmarks) | per-instance | observations on benchmark Goals; this mostly trains `z`, `B` and `β` across many models |
| Leaderboards with only a % (vendor cards, Arena-type tables) | aggregate | **the new binomial likelihood**: `k ~ Binomial(n, E_i[σ(θ+…−b_g−ε_i)])`, marginalised over instances with the existing Gauss-Hermite quadrature (`quadrature.py`), mapped to the benchmark's Goal node |
| Chatbot Arena preferences (RouteLLM's data: 80k+ battles) | pairwise | phase 2 only: a Bradley–Terry likelihood `σ(λ·(U(m,q) − U(m',q)))` with the query mapped to a Goal via `W·φ`, and its own temperature λ < 1 because preference ≠ correctness. RouteLLM's main lesson: augment preference data with golden labels (we have those, verified checks) |
| price table (`routing-sync-prices`) + public trajectories with token counts | cost | `costs.py` pooled token priors per (Goal, unit) |

Rules:
- **Contamination term.** When an item predates the model's `training_cutoff`, add `κ_kind · 1[item_date < cutoff]` (benchmark kind only, `κ ≥ 0` learned). This stops memorised benchmarks from inflating priors.
- **Public vs private.** Public observations carry `visibility=public` and check kind `benchmark` with its own α/β (already in `CHECK_PRIORS`).
- **No double counting.** One source per (instance, model, scaffold). A dedupe key goes on import.
- **Rejected again:** OpenRouter spend share as a success prior. Keep it only to *discover* models for the catalogue.

### 2.3 Goal side: strengthening without training

- Add cheap structural features to `φ`, computed at ingestion from the verified patch: files touched, hunks, lines changed, languages, number of tests, and whether the fix spans packages.
- These are known strong predictors of SWE-bench difficulty and need no model training.
- Keep the PCA-16 embedding. Benchmark-level difficulty becomes a parent node, through the DAG, so a new item inherits its benchmark's `b`.

### 2.4 Default model plan (question c)

- Add a built-in `CallerModelCandidates` provider, always configured: the caller's own model from `clientInfo`, or from a `my_model` argument or a `STEALTH_DEFAULT_MODELS` env var.
- Add `PublicCatalogCandidates`: catalogue models the user has connections for.
- `wants_plan()` then returns true by default.
- The reply always carries `model_plan`. At zero data it is the prior ranking, labelled `basis: prior`.

### 2.5 Proving it is SOTA (no claims without these)

- **SBC.** It exists; rerun it on the new terms.
- **Held-out predictive tests:**
  - leave-one-model-out (pure cold start: is the metadata prior better than `N(0,σ)` and better than "same family average"?);
  - leave-one-benchmark-out;
  - temporal split (models released after date T).
- **Routing metrics on held-out instances:**
  - quality–cost curves;
  - APGR and CPT(50%/80%) as in RouteLLM;
  - RouterBench AIQ;
  - **baselines:** best single model, cheapest model, kNN router, RouteLLM-MF re-implemented on the same data, and our model without covariates.
- Report the numbers in `docs/`. Ship only if leave-one-model-out log-lik and APGR beat the baselines.

## 3. Workstream B: `library.md`, routing rows, and how the `.stealth` files fit together

### 3.1 Book analogy, mapped to files

| book part | file | read when | size |
|---|---|---|---|
| back cover blurb, a **dynamic summary** | `.stealth/SUMMARY.md` (generated) | **always, whole**, at session start and on every MCP call | ≤ 3 KB |
| table of contents | `index/root.idx` (exists) | always | ≤ 4 KB |
| chapter: facts | `claims.md`, or `claims/<unit>.md` in monorepos | by topic / unit range | ≤ 64 KB per page |
| chapter: this repo's solved problems | **`library.md`** (new) | by index lookup | ≤ 64 KB + `library/archive-*.md` |
| appendix: code | `library/solutions/<id>.diff` | only when an entry is used | any |
| back-of-book index | `index/library.idx` (generated) + `index/terms.idx` (inverted: file path / symbol / error string / tag → ids) | grep | ≤ 64 KB |
| errata / routing table | **`routing.md`** (generated by the server's reply) | when choosing a model | small |
| working notes | `procedures.md`, `run.md` (exist) | for the current task | — |

**"Dynamic summaries."** I could not find an OpenAI feature by that exact name. The closest documented OpenAI design is Codex memories:
- `memory_summary.md` is read whole at session start and token-truncated;
- details live in `MEMORY.md`, which the agent **greps** (no vector search);
- background consolidation runs after sessions go idle;
- entries unused for 30 days are pruned.

We take the same pattern: `SUMMARY.md` is regenerated whenever a section's content hash changes. Each library section also keeps a one-line running summary in its header, so the summary stays current without reading everything.

### 3.2 `library.md` lines (pipe grammar, same style as `pipe_format.py`)

```
GOAL|L-7f3a|<title>|unit=<unit_path or .>|g=<global goal id or ->|outcome=pass|verified_at=2026-10-07|route=R-7f3a
PROC|L-7f3a.p1|<name>|p=<global procedure id or ->|solution=solutions/L-7f3a.diff|touches=src/x.py#sha=1a2b3c4,src/y.py#sha=…
STEP|L-7f3a.p1:1|<action|instruction|subgoal>|<do>|check=<cmd>
```

- Ids are random and authoritative. Line ranges are disposable, so the idx self-heals from block hashes.
- **Edits.** `touches=path#sha` uses `git hash-object`. When the sha changes, the entry is marked `stale` and re-checked; it is not deleted.
- **Merges.** One entry per block, append-only, sorted by id, with `.gitattributes: library.md merge=union`, so concurrent branches merge cleanly. A generated idx is never committed with conflicts: it is rebuilt.
- **Committed to git** (it is team knowledge). `routing.md` and `SUMMARY.md` are generated and can be rebuilt.

### 3.3 The model distribution: a pointer, not inline

- The posterior changes with every fit, so putting it in `library.md` would churn git.
- `route=R-7f3a` points to a row in `routing.md`:

```
ROUTE|R-7f3a|goal=L-7f3a|g=<global id>|fit=<fit_id>|as_of=…|step=*|ladder=modelA|scaffold:p=0.81[0.70,0.89]:$0.04 > modelB|…
OBS|R-7f3a|modelA|scaffold|n=3|ok=2|last=2026-10-06        # local sufficient statistics: what happened HERE
```

**On every MCP call:**
1. The agent sends `library.idx` rows plus the `OBS` lines of the matching routes. They are small, and they are the sufficient statistics.
2. The server runs `local_refit` (it exists in `fit.py`): global posterior as prior, local OBS as likelihood. The result is a repo-specific distribution.
3. It returns refreshed `ROUTE` rows.

So the local distribution is always fed in and routing is personalised, while raw code never leaves the machine.

### 3.4 How the files interact (one task, end to end)

1. Read `SUMMARY.md` and `root.idx`.
2. grep `terms.idx` for the files and errors in the task; get matching library ids.
3. `find_ways(query, repo_claims, repo_identity, library_rows, obs)`. Local candidates are judged **before** the top-8 cut and outrank global ones of equal fit.
4. The reply returns procedures (local + global), `verified_solution` when allowed, `model_plan`, and `ROUTE` rows.
5. The agent writes `procedures.md`, `run.md` (each NODE carries `model=`) and `routing.md`.
6. The agent runs with `report_result` per attempt, which updates OBS.
7. After checks pass, it **writes back** a new `library.md` entry with its diff.
8. `SUMMARY.md` and the idx files are regenerated.

## 4. Workstream C: `survey_repo` for every repo shape, with no loss of quality

### 4.1 A deterministic scanner first, the LLM second

A new local tool, `stealth survey` (in `packaging/`, runs locally, uploads nothing), built on `git ls-files` so it respects `.gitignore`. It outputs `index/units.idx`, a **unit graph**:

| shape | detection |
|---|---|
| single package | one manifest at root |
| JS/TS workspaces | `pnpm-workspace.yaml`, `package.json#workspaces` (npm/yarn/bun), `lerna.json`, `nx.json`/`project.json`, `turbo.json`, `rush.json` |
| Rust | `Cargo.toml [workspace]` |
| Go | `go.work`; several `go.mod` |
| Python | uv/Poetry/hatch/pdm workspace sections, several `pyproject.toml`/`setup.py` |
| JVM | Gradle `settings.gradle(.kts)` includes; Maven `<modules>` |
| .NET | `*.sln` → `*.csproj` |
| Bazel / Buck / Pants | `WORKSPACE`/`MODULE.bazel`, `BUILD(.bazel)`, `BUCK`, `pants.toml` |
| C/C++ | CMake `add_subdirectory`, Meson |
| others | Elixir umbrella, Dart melos, Swift Package, Ruby gemspecs |
| polyglot mono (e.g. this repo: Python backend + TS frontend + packaging) | several ecosystems, independent roots |
| git submodules / subtrees / vendored / `third_party` | `.gitmodules`; vendored dirs are **excluded** from facts except one `absent`/`vendored` fact |
| generated code | headers like "Code generated … DO NOT EDIT", `*_pb2.py`, `dist/`, etc. → excluded |
| IaC / deploy | terraform, helm, k8s, docker-compose → topic `runtime`/`ci` |
| notebooks / data / docs sites / mobile / embedded / dotfiles | own unit kinds |
| huge repos (>50k files) | sample per unit; cap reads per unit; record `coverage=partial` |
| no remote / fork / sparse checkout / LFS | identity falls back to the root commit; `upstream` remote noted |

The scanner also computes **repo identity** (§5.1) and each unit's manifests, versions, scripts and CI jobs **deterministically**: no LLM, so no hallucination.

### 4.2 The LLM fills facts, per unit

- The LLM reads only what the scanner selected for each unit, writes facts with `scope=unit:<path>` (the existing 5th field), and covers each topic or marks it `absent`.
- Monorepos use `claims/<unit>.md` pages with `root.idx` routing (the page design already exists).

### 4.3 No quality loss: a validator, not trust

- A deterministic validator rejects any fact whose `source=path:line` does not exist or whose sha does not match.
- `test`/`build` commands are cross-checked against manifest scripts and CI.
- Optional `--verify-commands` dry-runs them (`--help`/`--collect-only`).

### 4.4 Library bootstrap, local and private

- Mine `git log` for fix commits: message + diff + tests touched. They become `library.md` entries with `outcome=historical`.
- This is the local version of the "same-repo past fixes with diffs" that won the experiment. It needs no server.

### 4.5 Eval set and incremental re-survey

- **Gold set:** about 24 repos covering the table in §4.1, with hand-checked facts.
- **Metrics:** fact precision, recall of key facts (run/test/build/runtime versions), units found vs true units.
- **Gate:** precision ≥ 0.97 and key-fact recall ≥ 0.9.
- **Incremental re-survey:** only units whose source shas changed.
- **Synthetic fixtures too:** a generator builds one small repo per shape in §4.1, plus the edge cases in §4.6 (nested workspaces, shallow clone, no git, CRLF, symlinks, sparse checkout). They run as fast offline unit tests. The gold set measures quality; the fixtures measure coverage.

### 4.6 Efficiency and edge cases (required, not optional)

**Unit resolution**
- Workspace globs are expanded exactly, including negations (`!packages/x`).
- Precedence: the most specific declared workspace wins. Nested declarations (an Nx project inside a pnpm workspace, Gradle `includeBuild`, a Cargo workspace inside a polyglot repo) become **child units**, never duplicates.
- Bazel, Buck and Pants: a unit is a top-level package group (the first directory level under the root that holds BUILD files, or `//foo/...` targets), **not** every BUILD file.
- No manifest at all: a single root unit. Facts come from the Makefile, justfile, CI, README and shebangs.

**Cost bounded by unit count**
- *Inheritance:* root facts (runtime, CI, lint) are written once at `scope=repo`. A unit only records how it differs.
- *Template dedupe:* units whose manifests and scripts match after normalisation (version and name stripped) are clustered. One representative is surveyed with the LLM; the others get deterministic facts plus `inherits=<rep>`.
- *Lazy surveying:* above 40 units (configurable), only the root plus the units touched by the current task are surveyed. The rest stay as scanner-only facts until first touched (`coverage=scanner`).
- *Budgets:* token and file-read caps per unit and per survey, with `coverage=partial` recorded when hit.

**Identity fallback chain**
1. Normalised remote. Rules:
   - SSH and HTTPS forms normalise to the same value;
   - GitLab subgroups are kept;
   - Azure `_git` is handled;
   - an `upstream` remote is preferred over `origin` for forks;
   - mirror hosts can be mapped in config.
2. The root commit. On a shallow clone, it comes from `git rev-list --max-parents=0` if available; otherwise `meta.json` keeps an identity recorded earlier.
3. `"p:" + sha256(first manifest name and path)`, marked `weak`.
4. No git at all: a `weak` identity, and file listing falls back to a directory walk that honours `.gitignore`.

**Validator robustness**
- Line hashes are computed after normalising CRLF to LF and stripping trailing whitespace.
- Paths are posix, and symlinks are resolved once.
- Case-insensitive file systems are handled.
- Under a sparse checkout, a missing file means `unverifiable`, not `wrong`.

**Incremental re-survey**
- A dependency map runs from each unit to the shared files it reads (root lockfile, root config, CI).
- A changed shared file invalidates only the facts that cite it, not whole units.

**Bounded history mining**
- Defaults: the last 2 years, at most 5,000 commits, path-scoped per unit, and the fix-commit filter applied before any diff is read (`git log --name-only` first).

**Performance gates (checked in CI on fixtures and a large public repo, e.g. a Linux kernel or Chromium-sized checkout)**
- The scanner finishes in under 10 s at 100k files and under 60 s at 1M files.
- Scanner memory stays under 500 MB.
- LLM survey tokens grow with the number of distinct unit *templates*, not the number of units. For example, a 500-package pnpm monorepo should cost under 3× a single-package repo.
- A re-survey after a one-file change touches only the facts citing that file.

## 5. Contracts shared by the workstreams (fixed now)

### 5.1 `repo_identity`

```
remote  = normalise(origin url): lowercase host/owner/name, strip scheme, credentials, .git, port
repo_id = "r:" + sha256(remote)[:16]       or   "c:" + sha256(root commit hash)[:16]  when there is no remote
          or "p:" + sha256(first manifest name+path)[:16] with strength=weak (shallow clone without stored id, or no git); see §4.6
unit_id = repo_id + ":" + posix relative path ("." for root)
public_name = owner/name   (sent ONLY if the user allows it; otherwise the hash only)
```

The scanner (C) writes it to `meta.json`. find_ways (A/B) accepts `repo_identity={repo_id, public_name?, units?}`.

### 5.2 `model_plan` reply (A produces; B writes `routing.md` from it)

`{basis: prior|posterior, fit_id, as_of, steps: [{step: "*"|order, ladder: [{unit, p_ok_mean, p_ok_q05, p_ok_q95, cost_mean}]}], instance_key}`

### 5.3 `find_ways` new optional arguments

`repo_identity`, `library_rows` (library.idx lines, ≤ 64 KB), `route_obs` (OBS lines). All are optional. With none of them, behaviour is exactly as today: **no regression**.

## 6. Order, conflicts and constraints

- **A, B and C** start in parallel.
- **D** (evaluation) starts in parallel, building harness and data. Its final runs wait for B and C.
- Each session works on its own branch (`feat/routing-priors`, `feat/library`, `feat/survey`, `feat/local-eval`), keeps `server.py` edits small and isolated, and rebases on main before pushing.
- **Constraints for every session:**
  - never print keys or DSNs; never commit `.env*` or `*neon_shards.env*`;
  - no Vertex/GCP calls (projects suspended);
  - minimise Neon egress (read-only, batched, prefer local scratch copies);
  - offline tests for every change;
  - no change to existing find_ways behaviour unless the new arguments are passed;
  - the quality of core mechanisms must not drop: measure it, don't assume it.
