# The code cascade: keep the non-trivial code of outstanding repositories, and show it to small models

2026-09-29. Code: `backend/app/services/code_cascade/`, `backend/app/services/code_exemplars.py`, `backend/app/ingestion/cascade_cli.py`,
migration `128_reference_code_role.sql`. Tests: `test_code_cascade_structural_offline.py`, `test_code_cascade_pipeline_offline.py`.

## What it is

Point it at a GitHub repository. It finds the functions and classes that carry real logic, has a model confirm and describe each in
one generic sentence, and stores them so that, later, a small model asked to do a related task can be shown the actual code, with
its source and license, inside a small token budget.

It is a **cascade**: every stage is cheaper than the next and reports its own pass count, so "we found the important code" is a
checkable funnel and the only paid stage sees a few dozen candidates, not a repository.

```
S0  fetch + license      one pinned-commit tarball; the license is read AT that commit; the nearest LICENSE governs each file
S1  hard file filters    drop vendored / generated / minified / binary / tiny / __init__; tag tests and examples (kept, weighed down)
S2  structural features  cyclomatic complexity, nesting, distinct callees, loops / recursion / async / regex, copy-paste fingerprint
S3  score + rank spans   plumbing scores exactly ZERO; a size window keeps spans a small model can hold; import-graph centrality
S4  model judge          "would a small model LEARN something reusable here?" -> generic capability sentence, why it is subtle
S5  store                one-step Procedure (Goal = the capability) + the span preserved as a `reference_code` artifact
S6  find + render        find_exemplars(goal) -> render_for_slm(token_budget): an attributed, budget-fitted block
```

## Why structure first, then a model

A model asked "find the important code in this repository" reads too much and is guessing. Structure is free and deterministic, and
it separates code worth studying from code worth skipping: getters, delegates, stubs, re-exports and data containers carry no
decisions; an algorithm has branches that nest, calls many different things, loops, recurses. So S2/S3 rank spans by that, and the
model is asked only three questions about the survivors: is there a reusable technique here, what is it in one generic sentence,
and what is easy to get wrong. The generic sentence becomes the Procedure's Goal, so it is validated against the same quality gate
every Goal passes (no file paths, no repository names, no code syntax) *before* the write.

## What is stored, and where (nothing new except one artifact role)

| What | Where |
|---|---|
| the capability sentence | `procedures.goal` on a **one-step** Procedure, embedded from its canonical retrieval document so it is found by ordinary retrieval |
| the exact place | `procedures.source_locator`, `granularity: "span"`, `commit`, `line_start`/`line_end`, `content_hash` |
| the code itself | `ingested_artifacts`, role **`reference_code`** (migration 128), bytes inline or in object storage |
| why it is subtle, pitfalls, difficulty, license, attribution notice | `content_ref.exemplar` on that artifact |
| which run and license | one `IngestionContext` per run, `license_spdx` recorded, so the run can be removed as a class with `admin license-takedown` |

**One deliberate difference from verified solutions** (migration 124): a verified solution with a durable location keeps no bytes,
because the agent opens the locator. A reference-code span **always keeps its text**, because the reader is a small model that
cannot follow a link. It is a reference, never executable (`ingested_artifacts_exec_role_chk` already allows only
`executable_source` to be executable).

## Showing it to a model

```python
exemplars = await code_exemplars.find_exemplars(pool, embedder, "limit concurrent requests and queue the rest", language="go")
prompt_section = code_exemplars.render_for_slm(exemplars, token_budget=3000, max_items=2)
```

`find_exemplars` embeds the goal as a query, compares it only with vectors in the **same embedding space**, under the caller's
visibility scope, across shards. It applies a similarity floor (`DEFAULT_MIN_SIMILARITY = 0.60`): nearest-neighbour search always
returns something, and without a floor an off-topic task is handed the closest HTTP router. `render_for_slm` emits whole exemplars
only (half a function is worse than none), skips one that does not fit and still takes a smaller one later, uses a longer code
fence when the code contains backticks, and puts the source, commit, lines, license and the upstream-notice reminder on every block.

## Safety

* **License first, fails closed.** An unidentified or copyleft license keeps a file out of scoring entirely; a vendored subfolder
  with its own LICENSE does not inherit the root's; a file whose own header carries a GPL/AGPL/SSPL/proprietary notice inside a
  permissive repository is dropped after ranking. MIT/BSD/Apache require the notice to travel with copies, so it is stored with
  every exemplar and rendered with every block.
* Span text is **untrusted third-party data**: screened for prompt injection before it is sent to the judge, passed through the
  known-token redaction before it is hashed or stored, and never executable.
* The judge runs under `ingest_budget` (guard before every call, ledger after); `BudgetExceeded` stops the run.
* `--apply` needs the model; nothing is stored on structure alone. A dry run writes nothing and prints what it would store.

## Measured (2026-09-29, scratch database, Vertex Gemini judge, `gemini-embedding-2`)

| repository | license | files | spans scored | candidates | judged | **stored** | declined by the model | seconds |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| go-chi/chi | MIT | 83 | 410 | 284 | 16 | **6** | 10 | 140 |
| pallets/click | BSD-3-Clause | 76 | 1,144 | 706 | 16 | **8** | 8 | 93 |
| expressjs/express | MIT | 131 | 142 | 72 | 16 | **3** | 13 | 64 |

* **Cost:** 18 judge calls (31.6k tokens in, 4.9k out) = **$0.077** on the ledger for all three, about **$0.026 per repository**,
  plus $0.015 of embeddings. The ledger prices Vertex at a deliberately rounded-up rate, so real spend is lower.
* **What it picked** reads as each project's real core: chi's radix-tree `findRoute` and `patNextSegment`, `Throttle`, `Recoverer`;
  click's `_parse_decls` and path conversion; express's `res.send`, `app.use`. Ranking was checked by reading it on click, chi,
  express and ky, not fitted, and it caught two extractor gaps that were then fixed (assignment-style JS functions such as
  `res.send = function send(){}` were invisible; `from . import mod` recorded no import edge).
* **The model earns its keep:** it declined 31 of 48 candidates (glue, thin wrappers, code that needs private context).
* **Retrieval** (17 exemplars, six tasks): the routing task returned chi's router first, concurrency limiting returned `Throttle`,
  "parse command line options" (python) returned click's option parser, the JS response task returned `res.send`, crash recovery
  returned `Recoverer`. Best relevant hits scored 0.68-0.73; the best hit for an off-topic task scored 0.54 (hence the 0.60 floor).
* **Rendered block:** about 950 tokens for one full exemplar with its notes, so a 3,000-token budget holds two or three.

## Honest limits

* "Non-trivial" is decided by structure and confirmed by a model reading the span. Neither proves the code is **correct**, and the
  cascade does not run it. An exemplar is a technique worth studying from a well-regarded project, not a verified solution.
* **Nothing yet proves it helps.** Retrieval relevance is measured on six hand-written tasks; whether showing these to a small
  model improves its output is an experiment (the `experiments/harness` three-arm design is the place), not a claim made here.
* Rust and Java are scored structurally but get no import-centrality term; other languages are not scored at all (the funnel reports
  `unsupported_language`). Call names are syntactic.
* Repositories whose git tree GitHub truncates, archives over 80 MB, and archived repositories are refused, not half-ingested.
* The similarity floor is calibrated to one embedding model and 17 exemplars; re-measure when either changes (the score travels in
  `extra["similarity"]`).
* Finding the "amazing repository" in the first place is not built: `cascade-repo` takes a repository you name. Candidate sources
  for a selector are stars and recent activity, the dependency graph's in-degree (packages many projects import), and curated lists.

## Running it

```powershell
python -m app.ingestion.admin cascade-repo go-chi/chi --no-judge                 # free: structure only, prints the funnel
python -m app.ingestion.admin cascade-repo go-chi/chi --top 12 --max-usd 1       # dry run WITH the judge: read what would be stored
python -m app.ingestion.admin cascade-repo go-chi/chi --top 12 --max-usd 1 --apply
```

Migration 128 must be applied first (the `--apply` path refuses to start on a database with pending migrations).

## Next

1. Surface `reference_code` in `find_ways` next to `verified_solution` (the same place, a sibling field) so agents get it without a
   second call; gate it with a setting like `knowledge_verified_examples`.
2. A repository selector and a batch runner with a spend cap and per-repo resume.
3. Re-cascading on a new commit: a changed span supersedes its exemplar (the bi-temporal machinery already does this for procedures).
4. The experiment: with and without exemplars in the prompt of a small model on a held-out set of tasks.
