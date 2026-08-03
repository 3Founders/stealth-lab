# implement.md — build `plat_v1`

Instructions for building the first executable version of the platform. Read the
whole document before writing code. Where it says **must**, that constraint is
load-bearing and there is a reason for it in the design; don't optimise it away.

## What this is

A prompt goes in, work comes out. Two paths:

1. **Match** — a task already exists that does this. Bind it to the best
   implementation for the caller's constraints and run it.
2. **Decompose** — no task exists. Propose a DAG of tasks, typecheck it
   deterministically, show a human, and run it on approval.

The unit of everything is a **task node**: a name, a typed input schema, a typed
output schema, and a success criterion. What *satisfies* a task node is open — a
shell command, a Python function, a model call, or a composite of other tasks.
Implementations compete; the router picks the cheapest one that clears the bar.

## Not in v1

Do not build these. They are deliberate omissions, not oversights.

- Authentication or multi-tenancy. Single trusted operator.
- A debate panel, fallacy checking, or statistical evaluation.
- Public submission, payments, royalties, or reputation.
- Bi-temporal history. Task definitions get a simple integer `version` and a
  `superseded_by` pointer; that's enough.
- A UI beyond whatever is needed to approve a proposal. curl is acceptable.
- Background job queues. Runs execute synchronously; long runs are a v2 problem.
- Rust. Python throughout.

## Relationship to `backend_v2`

`plat_v1` is **standalone**. It imports nothing from `backend_v2` and has its own
schema and its own FastAPI app.

The reason: `backend_v2`'s only write path goes through debate and human
approval, which is correct for a governance layer and wrong for an execution
engine where most work should just run. Merging them in v1 would compromise
both. Port ideas freely — the hybrid retrieval pattern in
`backend_v2/backend_v2/app/services/retrieval.py` and the capability boundary in
`app/models/change.py` are both worth reading — but copy code rather than
importing it.

## Stack

Match the existing project: Python 3.13, FastAPI, asyncpg, Postgres 15+ with
pgvector. Anthropic SDK for model calls.

**Model calls must use `claude-opus-5` with structured outputs**
(`output_config={"format": {"type": "json_schema", "schema": ...}}`), not JSON
parsed out of prose. Prose-wrapped JSON is the single most likely thing to break
and there is no reason to inherit that failure mode in new code. Use
`thinking={"type": "adaptive"}` and set `max_tokens` generously.

## Layout

```
plat_v1/
  db/
    01_schema.sql
    02_seed_pdf_excel.sql
  app/
    main.py
    config.py
    db.py
    models/            # pydantic types: Task, Implementation, Plan, RunRequest
    services/
      intake.py        # prompt -> normalised intent + candidate match
      matching.py      # hybrid retrieval over task nodes
      decompose.py     # model call -> proposed plan
      typecheck.py     # deterministic plan validation  <-- no model calls here
      router.py        # implementation selection
      executor.py      # DAG execution
      cache.py         # input-fingerprint cache
      traces.py        # trace recording
    runners/
      command.py       # shell command implementations
      python_fn.py     # registered python callables
      model.py         # LLM implementations
    api/
      run.py, tasks.py, proposals.py, evals.py
  tests/
  scripts/
    seed.py
  requirements.txt
  README.md
```

## Data model

`db/01_schema.sql`. Idempotent.

| Table | Purpose |
|---|---|
| `tasks` | id, name, description, `kind` (`leaf`\|`composite`), `input_schema` jsonb, `output_schema` jsonb, `success_criteria` jsonb, embedding vector(1024), version int, superseded_by uuid null, created_at |
| `task_edges` | id, `edge_type` (`REQUIRES`\|`PRODUCES`\|`DECOMPOSES_TO`), source_id, target_id, properties jsonb |
| `implementations` | id, task_id, `kind` (`command`\|`python`\|`model`), spec jsonb, cost_estimate numeric, latency_estimate_ms int, enabled bool, created_at |
| `evals` | id, task_id, cases jsonb (list of `{input, expected}`), scorer text |
| `eval_results` | id, implementation_id, eval_id, score numeric, cost numeric, latency_ms int, ran_at |
| `runs` | id, request_text, plan jsonb, status (`pending`\|`awaiting_approval`\|`running`\|`succeeded`\|`failed`), created_at, finished_at |
| `traces` | id, run_id, task_id, implementation_id, input jsonb, output jsonb, outcome (`success`\|`failure`), cost numeric, latency_ms int, at |
| `proposals` | id, request_text, plan jsonb, typecheck jsonb, status (`pending`\|`approved`\|`rejected`), decided_by, decided_at |
| `cache_entries` | id, task_id, fingerprint text, implementation_id, params jsonb, hits int, last_hit_at |

Index `tasks.embedding` with HNSW **and make it partial on `superseded_by IS NULL`**
— a full index keeps superseded rows in the proximity graph and silently degrades
recall against live tasks as versions accumulate. Same for the FTS index.

## The six subsystems

### 1. Intake and matching (`intake.py`, `matching.py`)

Given a prompt, find task nodes that might already do the job.

Hybrid retrieval, same shape as `backend_v2`'s: vector search over
`tasks.embedding` plus Postgres full-text over name+description, fused with
Reciprocal Rank Fusion (k=60). Return top 5 with scores.

**Batch the hydration.** One `WHERE id = ANY($1::uuid[])` per table, not a query
per hit.

A match is accepted automatically only if the top result's fused score exceeds a
configured threshold *and* the caller's supplied inputs validate against that
task's `input_schema`. Otherwise fall through to decomposition. Schema validation
is the real gate here — semantic similarity alone will happily match "extract
tables from a PDF" to "extract text from a PDF".

### 2. Decomposition (`decompose.py`)

No match. Ask the model for a plan.

Retrieve the top ~10 existing tasks first and put them in the prompt, so the
model reuses rather than reinventing. Request a structured plan:

```json
{
  "feasible": true,
  "reasoning": "...",
  "nodes": [
    {"ref": "n1", "name": "...", "description": "...",
     "input_schema": {...}, "output_schema": {...},
     "existing_task_id": null}
  ],
  "edges": [{"type": "PRODUCES", "source_ref": "n1", "target_ref": "n2"}],
  "external_inputs": ["path_to_pdf"]
}
```

`existing_task_id` non-null means "reuse this task rather than creating a new
one" — this is how reuse gets expressed. `input_schema` and `output_schema` are
JSON Schema and **must not be empty objects**; reject the plan at typecheck if
they are. An empty schema is the model declining to commit, and it defeats every
check downstream.

A plan never executes straight from the model. It goes to typecheck, then to a
proposal awaiting human approval.

### 3. Typechecker (`typecheck.py`) — the important one

**Pure function. No model calls, no database writes, no network.** Takes a plan,
returns a list of problems. Empty list means valid.

This is the piece that makes generated plans trustworthy, and it is the reason
the platform is worth building rather than just prompting a model. Do not
delegate any of it to an LLM.

Rules:

1. **Dataflow closure.** Every declared input of every node is either produced by
   an upstream node via a `PRODUCES` edge, or named in `external_inputs`. A
   dangling input is a hard failure.
2. **Type compatibility across edges.** Where node A produces into node B, A's
   `output_schema` must satisfy B's `input_schema`. Structural check: every
   required property of B's input exists in A's output with a compatible type.
3. **Acyclicity** over `REQUIRES` and `PRODUCES`.
4. **Every leaf is executable** — has at least one enabled implementation.
   (A node referencing `existing_task_id` inherits that task's implementations.)
5. **Composite interfaces hold.** If a node is `composite`, its declared
   interface must be satisfied by its expansion: the expansion accepts at least
   what the composite declares (inputs contravariant) and produces at least what
   it promises (outputs covariant).
6. **No empty schemas**, no duplicate refs, no self-edges, no edge referencing an
   undeclared ref.

Every rule gets its own unit test with a plan that violates exactly that rule.

### 4. Router (`router.py`)

Given a task and the caller's constraints, pick an implementation.

```
probe cache (task_id + input fingerprint)
  hit  -> return cached (implementation, params)
  miss -> candidates = enabled implementations for task
          filter to those whose latest eval score >= quality_bar
          sort by (cost_estimate, latency_estimate_ms)
          return first
```

**The routing decision must be a lookup, never an inference call.** Using a model
to decide whether to use a model eats the entire saving.

`quality_bar` defaults to the best score any implementation has achieved on the
task's eval, minus a configurable tolerance. If a task has no eval yet, order by
cost and let the escalation path handle failures.

**Escalation.** If the selected implementation fails its `success_criteria`,
retry with the next candidate in cost order. Cap at 3 escalations per stage,
then fail the stage.

### 5. Executor (`executor.py`)

Topologically sort the plan. For each node in order:

1. Assemble inputs from `external_inputs` and upstream outputs.
2. Validate inputs against `input_schema`; fail the stage if invalid.
3. Ask the router for an implementation.
4. Run it through the matching runner.
5. Validate the output against `output_schema` and evaluate `success_criteria`.
6. Record a trace regardless of outcome.
7. On failure, escalate per the router rules.

Composite nodes expand inline and execute their subgraph before continuing.
**One level of nesting is enough for v1** — reject deeper nesting at typecheck
with a clear message rather than half-supporting it.

Runners:

| kind | `spec` shape | Notes |
|---|---|---|
| `command` | `{"template": "pdftotext {input} {output}", "timeout_s": 60}` | Substitute inputs by name. Run with `subprocess`, no shell. Working directory is a per-run temp dir. |
| `python` | `{"ref": "runners.tables:extract_with_pdfplumber"}` | Resolve against an explicit registry dict — **never** `importlib` on a value from the database. |
| `model` | `{"model": "claude-opus-5", "system": "...", "output_schema": {...}}` | Structured outputs. Record token usage into the trace's cost. |

### 6. Cache and traces (`cache.py`, `traces.py`)

The cache is the thing that makes marginal cost collapse — treat it as a
first-class subsystem, not an optimisation.

Fingerprint an input deterministically per task. For documents, that means a
*layout* fingerprint, not a content hash: page count, page dimensions, and a
coarse hash of text-block bounding-box positions. Two invoices from the same
vendor with different amounts must produce the same fingerprint. A content hash
would give a 0% hit rate and make the whole mechanism pointless.

On a cache hit, skip routing and reuse the recorded implementation and params.
Increment `hits`. Write a cache entry after any successful run whose stage passed
its success criteria.

Every stage execution writes a trace row — always, including failures. Traces are
the input to future routing decisions and the only measurement you will ever have
of what actually works.

## Reference workflow: PDF → Excel

Seed this in `db/02_seed_pdf_excel.sql` and make the end-to-end test run it. It
exists so the platform is concrete rather than abstract, and because it is the
work that has customers.

| # | Task | Input → Output | Implementations to seed |
|---|---|---|---|
| 1 | `classify_document` | pdf → doc_type, page_count | `python`: heuristic on text density; `model`: fallback |
| 2 | `detect_table_regions` | pdf → list of (page, bbox) | `python`: pdfplumber ruled-line detection; `model`: VLM fallback |
| 3 | `extract_cell_structure` | pdf + regions → grid of cells | `python`: pdfplumber; `model`: fallback |
| 4 | `validate_types` | grid → typed grid, errors | `python` only — **deterministic, no model** |
| 5 | `map_to_schema` | typed grid + target schema → rows | `model` (this is the one that genuinely needs reasoning); `python`: template match when a cached layout hit |
| 6 | `write_xlsx` | rows → file path | `python` only — **deterministic, no model** |

Stages 4 and 6 having no model implementation is the point, not an omission. Six
stages at 97% accuracy each is 83% end to end; the chain only holds because most
stages are exact. **When adding a stage, add its deterministic implementation
first and only add a model implementation if the deterministic one measurably
fails.**

## API

| Route | Behaviour |
|---|---|
| `POST /v1/run` | `{prompt, inputs, quality_bar?, max_cost?}`. Match → execute, or decompose → create a proposal and return `202` with its id. |
| `GET /v1/runs/{id}` | Status, plan, per-stage traces, total cost and latency. |
| `GET /v1/proposals` | Pending proposals with their typecheck results. |
| `POST /v1/proposals/{id}` | `{decision: "approve"\|"reject"}`. Approving persists the tasks and executes. |
| `GET /v1/tasks` | List and search tasks. |
| `POST /v1/tasks/{id}/implementations` | Register an implementation. |
| `POST /v1/tasks/{id}/evals` | Attach an eval case set. |
| `POST /v1/evals/{id}/run` | Score every implementation of that task; write `eval_results`. |

A proposal that fails typecheck is stored with its problems and is **not
approvable** — the API rejects an approve decision on it. Structural failure
should never reach a human as something they can wave through.

## Tests

Must pass with no database and no API keys:

- Typechecker: one test per rule, each with a plan violating exactly that rule,
  plus a valid plan that passes cleanly.
- Router: cheapest-clearing-bar selection; cache hit bypasses routing; escalation
  order on failure; escalation cap.
- Cache fingerprinting: two documents with the same layout and different content
  produce the same fingerprint; different layouts don't.
- Plan parsing: malformed model output becomes a failed proposal, not an
  exception.
- Runner dispatch with fakes for all three kinds.

Requiring Postgres (a separate script, as `backend_v2` does it):

- Seed the PDF→Excel workflow, run a real PDF end to end, assert an `.xlsx` is
  produced and a trace row exists per stage.
- Second identical run hits the cache and records a lower cost.
- Retrieval returns the seeded tasks for a natural-language prompt.

## Build order

1. Schema, config, db connection, health endpoint.
2. Task and implementation CRUD; seed the six PDF→Excel tasks with their
   deterministic implementations only.
3. Runners and the executor over a hand-written plan. Get one PDF to Excel
   end to end with no matching, no routing, no decomposition.
4. Traces.
5. Typechecker with its full test suite.
6. Router and cache.
7. Matching.
8. Decomposition and proposals.
9. Evals and scoring.

Step 3 is the milestone that proves the thing works. Do not build the router or
the decomposer before a single PDF has become a spreadsheet.

## Frontend — reuse `frontend_v2`, add almost nothing

Copy `frontend_v2/frontend_v2` to `plat_v1/frontend` and point `lib/api.ts` at
the new backend. Do not design a new frontend. The existing surface is six routes
and three components, and it already fits — a proposed plan is a DAG of typed
nodes, which is exactly what it was built to render.

What serves what:

| `plat_v1` needs | Already exists |
|---|---|
| Submit a prompt, see the resulting plan | `app/workbench/page.tsx` |
| Render a plan as a graph | `components/WorkflowGraph.tsx` + `lib/opsToGraph.ts` |
| List pending proposals | `app/approvals/page.tsx` |
| Review one and approve or reject | `app/approvals/[id]/page.tsx` |
| Run history | `app/archive/page.tsx` |
| API client | `lib/api.ts` |

**Reuse `WorkflowGraph.tsx` and `opsToGraph.ts` as they are.** A plan's
`{nodes, edges}` is the same shape as a change set's create-and-link ops, so
`opsToGraph` needs an adapter function at most — not a rewrite. The renderer is
deliberately hand-drawn SVG with no graph-library dependency, because the graphs
are small and the design language is custom. Keep it that way.

**Delete on copy:**

- `components/Layer2Evidence.tsx` — there is no Layer 2 in `plat_v1`.
- `components/GroundedAnswer.tsx` — there is no chat endpoint in `plat_v1`.

**Build only these three, and keep each small:**

1. A per-stage trace strip on the run detail view — implementation used, outcome,
   cost, latency, one row per stage. This is the only genuinely new thing, and it
   belongs inside the existing archive/detail page rather than on a new route.
2. A typecheck-problems list on the proposal detail page. A plain `<ul>` of
   strings. A proposal that failed typecheck renders its problems and shows no
   approve button.
3. A cost and latency total on the run view.

**Do not add:** a graph visualisation library (react-flow, d3, cytoscape), a
component library or design system, authentication UI, CRUD screens for
implementations or evals (curl is fine for those in v1), charts, or a dark-mode
toggle. If a screen is only needed by you and not by the person approving a plan,
it does not need to exist.

At convergence the two frontends merge back — which is another reason to keep the
copy as close to the original as possible.

## Decisions to raise rather than guess

Flag these to the human instead of picking silently:

- The auto-match score threshold. It needs tuning against real prompts.
- Whether `map_to_schema` should ever run without a human seeing the output on a
  first-time layout.
- Where run artifacts live once the temp directory is gone.
- Whether a failed stage should fail the run or produce a partial result with the
  failure recorded.

---

# Convergence with `backend_v2`

`plat_v1` is standalone now, for the reason given above. But the two systems are
halves of one thing — `backend_v2` governs *changes to knowledge*, `plat_v1`
*executes tasks* — and they will probably want to be one system eventually. This
section exists so the build doesn't foreclose that.

## Do these now; they cost nothing and save the migration

Zero effort today, expensive to retrofit:

1. **Name the table `task_nodes`, not `tasks`.** Match `backend_v2`'s name.
2. **Carry the four temporal columns** — `t_valid`, `t_invalid`, `t_created`,
   `t_expired` — on `task_nodes` and on the edge table, even though v1 only ever
   sets `t_valid` and `t_created`. Adding bi-temporality to a populated table
   later is a migration; having unused columns costs nothing.
3. **Filter every read on `t_invalid IS NULL`** from day one, and make the HNSW
   and FTS indexes partial on that predicate. This is how v1 expresses
   `superseded_by` — drop that column and use the temporal ones instead.
4. **Include a `provenance` column** with the same enum values `backend_v2` uses
   (`company_ingested`, `company_debate`, `prior_library`, `public_generated`),
   defaulting to `company_ingested`.
5. **Use the polymorphic edge shape** — `source_id`, `source_table`, `target_id`,
   `target_table` — even though v1 only ever has task→task edges.
6. **Match trace column names** where they overlap: `task_node_id`, `outcome`,
   `cost`, `latency_ms`, `parent_trace_id`, `timestamp`. Add v1's extras
   (`run_id`, `implementation_id`, `input`, `output`) alongside.
7. **UUID primary keys** via `gen_random_uuid()`.

Follow these and the schema merge later is mostly `ALTER TABLE ... ADD COLUMN`
rather than a data migration.

## Six mistakes already made and fixed once — don't make them again

These were found in `backend_v2` the hard way, several only against a real
database. Reintroducing them in fresh code would be the most annoying possible
outcome.

1. **Pass dicts to asyncpg directly for JSONB.** Do not pre-serialise in Python
   and cast in SQL — that silently corrupts how the connection decodes JSON on
   every later read once a type codec is registered.
2. **Rate limits and spend caps need an advisory lock,** not just a transaction.
   Under READ COMMITTED, ten concurrent requests were approved against a limit
   of three.
3. **Access predicates go inside the recursive CTE,** never as a post-filter on
   the result — otherwise a reachable-only-through-a-private-edge node leaks.
   (Not needed in v1, but relevant the moment visibility exists.)
4. **HNSW indexes must be partial** on the live-row predicate. An unfiltered
   index over a soft-delete table decays recall silently as dead rows accrue.
5. **Batch hydration.** One `= ANY($1::uuid[])` per table. The original retrieval
   path did ~31 round trips per query.
6. **Use numerically stable formulas** in any statistics — two-pass variance, and
   absolute rather than relative change (relative is undefined at a zero
   baseline, which is exactly the case that matters).

## Three possible end states

| End state | Selected when |
|---|---|
| **A. Converge — `plat_v1` becomes the runtime, `backend_v2`'s governance becomes a module inside it** | The debate loop proves out against real models *and* a customer needs governed knowledge changes and execution in one system. Most likely outcome. |
| **B. Two apps, one database** | Both are in production with different users and different latency budgets, and the only real coupling is the shared graph. Lowest-risk, and a legitimate permanent answer. |
| **C. Retire `backend_v2`; keep only its knowledge graph and approval gate** | The debate panel never justifies its cost against real models. Then Layer 1, Layer 2, and the panel get deleted, and the bi-temporal graph plus human approval move into `plat_v1`. |

C is a real possibility, not a strawman. The debate engine is the most expensive
component and the least validated — it has never run against a paid model. Hold
the option open rather than assuming a merge.

## Migration steps, if end state A

Ordered. Each step should leave both systems working.

1. **Unify the schema.** Add `plat_v1`'s columns to `backend_v2`'s `task_nodes`
   (`kind`, `input_schema`, `output_schema`). Add the `implementations`,
   `evals`, `eval_results`, `runs`, `proposals`, and `cache_entries` tables.
   Reconcile the two trace tables into one.
2. **Move the executor, router, and runners into `backend_v2`** as
   `app/services/execution/`. They have no dependency on the debate loop.
3. **Move the typechecker in and wire it ahead of the existing approval path,**
   so a proposed change set is structurally validated before a human sees it.
   This replaces the critique model's current attempt at dataflow checking in
   `app/services/decomposition.py` — delete that instruction from the prompt.
4. **Route `/v1/decompose` through the typechecker,** and make a plan that fails
   it unapprovable rather than merely flagged.
5. **Point trigger detection at the unified traces table.** Execution traces from
   `plat_v1` become the input to `backend_v2`'s bottleneck detection — this is
   the point where the two halves start compounding rather than coexisting.
6. **Feed eval results into the debate.** A candidate change to a task can now
   cite measured implementation performance instead of a simulated replay,
   which is the strongest tier of evidence Layer 2 was designed for and has
   never had.
7. **Retire `plat_v1`'s standalone app** once every route is served from the
   merged one.

Step 5 is the one that matters. Everything before it is plumbing; step 5 is where
execution starts producing the evidence governance needs.

## When not to converge

Do not merge for tidiness. Specifically, hold off while any of these is true:

- `plat_v1`'s schema is still changing week to week
- The debate loop still hasn't run against a real frontier model
- No single user needs both halves
- `plat_v1` has under a month of real usage

Two working systems are cheaper than one broken one. The cost of staying split is
a duplicated schema; the cost of merging early is losing the ability to change
either half quickly.
