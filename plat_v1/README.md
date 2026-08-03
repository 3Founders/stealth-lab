# plat_v1

A prompt goes in, work comes out.

Two paths. **Match**: a task already exists that does this, so bind it to the
best implementation for the caller's constraints and run it. **Decompose**:
nothing does, so propose a DAG of tasks, typecheck it deterministically, show
a human, and run it on approval.

The unit of everything is a **task node**: a name, a typed input schema, a
typed output schema, and a success criterion. What *satisfies* a task node is
open — a shell command, a Python function, a model call. Implementations
compete; the router picks the cheapest one that clears the bar.

`plat_v1` is standalone. It imports nothing from `backend_v2` and has its own
schema and its own FastAPI app. The reason is in `implement.md`: `backend_v2`'s
only write path goes through debate and human approval, which is correct for a
governance layer and wrong for an execution engine where most work should just
run. Ideas are ported; code is copied, not imported.

---

## Running it

```bash
cd plat_v1
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                            # then fill in DATABASE_URL

python scripts/seed.py                          # schema + the PDF -> Excel workflow
python scripts/seed.py --status                 # what's registered

# 8001, not 8000: backend_v2 already uses 8000, and the two are meant to be
# runnable side by side.
uvicorn app.main:app --reload --port 8001
```

### Sharing a database with `backend_v2`

plat_v1 lives in its own Postgres **schema**, set by `DB_SCHEMA` (default
`plat_v1`). This is not tidiness. Both apps define `task_nodes` and `traces`
with different columns, so sharing a schema would mean `CREATE TABLE IF NOT
EXISTS` silently skipping and this app then running against the other one's
table definitions.

Three things had to be true, and each was verified against a live database
rather than assumed:

- **`search_path` is a connection startup parameter, not a `SET`.** A runtime
  `SET` in the pool's init callback works on the first connection and silently
  stops working on every one after it — Supabase's Supavisor pooler
  multiplexes onto a rotating set of backends and the GUC doesn't follow you.
  Measured: acquire 0 saw `plat_v1, extensions`; acquires 1–3 saw `"$user",
  public, extensions`, and an unqualified `traces` then resolved to
  *backend_v2's table*. Startup parameters are replayed onto each backend, so
  they hold.
- **`CREATE TABLE IF NOT EXISTS` resolves against the target schema, not
  visibility.** With `plat_v1` first on the path it creates `plat_v1.traces`
  even while `public.traces` exists. Tested directly, in a rolled-back
  transaction.
- **pgvector's schema has to be on the path for the `vector` type to
  resolve, and you cannot guess which one it is.** Supabase documents
  `extensions`, and on the project this was built against `pgcrypto` is indeed
  there — but `vector` is in `public`. `discover_extension_schema()` looks it
  up instead.

Because that last point can put `public` on the search_path, isolation is
enforced rather than assumed, in two layers:

- **Per acquire**, the pool's `setup` hook compares `SHOW search_path` against
  what was asked for. `setup`, not `init` — `init` runs once per physical
  connection, so it structurally cannot see drift on a *re*-acquire, which is
  exactly the failure that was measured.
- **`verify_isolation()`** checks that all nine tables plat_v1 owns resolve to
  our schema, and runs at app startup, after seeding, and before the
  end-to-end script writes anything. It checks `relkind`, so a view over
  another schema's table cannot satisfy it.

Both `db/*.sql` files carry their own guard and are wrapped in an explicit
`BEGIN`/`COMMIT`. That last part matters: psql defaults to `ON_ERROR_STOP`
*off*, so a bare `RAISE` would print an error and psql would carry on and
build indexes on `backend_v2`'s live table. `02_seed`'s guard resolves
`to_regclass('task_nodes')` rather than reading `current_schema()`, because an
unqualified `INSERT` resolves by visibility across the whole path, not by the
current schema.

Use the **session** pooler (port 5432), never the transaction pooler (6543):
transaction-mode pooling preserves neither the search_path nor asyncpg's
prepared statements.

Frontend:

```bash
cd frontend
npm ci
cp .env.local.example .env.local
npm run dev
```

### Tests

The offline suite needs no database and no API keys:

```bash
python -m pytest            # 146 tests
```

The end-to-end check needs both a database and a seeded graph:

```bash
python scripts/seed.py
python scripts/e2e_pdf_excel.py
```

It generates a ruled-table PDF, runs it through all six stages, and asserts an
`.xlsx` came out, a trace row exists per stage, a second identical run hits the
cache and does less work, a *different* document with the same layout also hits
the cache, and retrieval finds the workflow from a natural-language prompt.
With `ANTHROPIC_API_KEY` set it additionally runs a variant whose column names
don't match the target schema, which forces the model mapping on the first run
and replays it for free on the second — that variant is the only one that
demonstrates marginal cost actually collapsing, so run it that way at least
once.

---

## The six subsystems

| Where | What |
|---|---|
| `services/intake.py`, `matching.py` | Hybrid retrieval (vector + FTS, fused with RRF, k=60). A match is auto-accepted only if the fused score clears the threshold **and** the caller's inputs validate against the task's input schema. |
| `services/decompose.py` | Model call producing a proposed plan, with the top ~10 existing tasks in the prompt so it reuses rather than reinvents. |
| `services/typecheck.py` | Deterministic plan validation. Pure function. No model calls. |
| `services/router.py` | Implementation selection. A lookup, never an inference call. |
| `services/executor.py` | Topological execution, per-stage validation, escalation. |
| `services/cache.py`, `traces.py` | Layout fingerprinting and trace recording. |

### The typechecker is the point

It is the reason this is worth building rather than just prompting a model, so
none of it is delegated to one. Seven rules, each with its own id so a failure
names what it violated, and each with its own unit test using a plan that
violates exactly that rule:

| Rule | Checks |
|---|---|
| `well_formed` | no empty schemas, no duplicate refs, no self-edges, no edge naming an undeclared ref |
| `acyclicity` | no cycles over REQUIRES and PRODUCES |
| `dataflow_closure` | every declared input is produced upstream or named in `external_inputs` |
| `type_compatibility` | a producer's output fits the consumer's input, structurally |
| `executable_leaf` | every leaf has at least one enabled implementation |
| `composite_interface` | a composite's declared interface is met by its expansion (inputs contravariant, outputs covariant) |
| `nesting_depth` | composites expand one level, not two |

An empty schema is treated as a failure because it is the author declining to
commit, and it defeats every check downstream. `{"type": "object"}` with no
`properties` counts as empty; `{"type": "object", "properties": {}}` does not —
the key being present is the commitment.

**Dataflow closure and type compatibility run inside expansions too**, with the
composite's declared inputs playing the role of `external_inputs`. This is not
a refinement: the seeded reference workflow *is* a composite, so all six real
stages live in an expansion. Checking only the top level meant every PRODUCES
edge in the production workflow went unvalidated while the plan reported clean
— the rules that justify the whole system, not running on the one plan that
matters.

A proposal that fails typecheck is stored with its problems and is **not
approvable**. The API refuses an approve decision on it and the UI renders no
approve button. Structural failure should never reach a human as something
they can wave through.

### The router never asks a model

```
probe cache (task + input fingerprint)
  hit  -> return the cached (implementation, params)
  miss -> candidates = enabled implementations
          drop those measured below the quality bar
          sort by (cost_estimate, latency_estimate_ms)
          return the first, with the rest as the escalation order
```

Using a model to decide whether to use a model eats the entire saving. The
quality bar defaults to the best score any implementation has achieved minus a
tolerance; an implementation with *no* recorded score is kept, because
excluding it means a newly registered implementation can never run, so can
never be scored, so can never become eligible. Escalation is capped at 3 past
the first attempt.

A bar the caller passed in and a bar derived from eval history are treated
differently when nothing meets it. The derived one is a heuristic, so the
router ignores it rather than failing a stage untried. The caller's is a
constraint, so the stage fails — running something they explicitly excluded
would be worse than returning nothing.

### The cache fingerprints layout, not content

A document's fingerprint is its **layout**. Two invoices from the same vendor
with different amounts must land on the same fingerprint, because what is
cached is *how to read this shape of document* — which implementation won and
what it worked out — not the answer. A content hash would give a ~0% hit rate
and make the mechanism decorative.

When a page has ruled lines or rectangles, they are the whole signature: pure
structure, content-independent by construction. Without them the fallback is
text positions, projected onto separate axes and filtered by recurrence, since
only the column *starts* are stable — "Hex bolt M8" and "Copper pipe 15mm"
differ in length and word count, so a fingerprint over every word position is a
content hash wearing a hat.

**Not every stage's input is a document, and that is the subtle half.** A
stage downstream of extraction receives *data*. `map_to_schema` gets
`typed_grid`, `columns` and `target_schema` — fingerprint all three and the
key is the actual cell values, so two invoices from the same vendor never
share an entry, for precisely the one stage in the chain that costs a model
call. A task can therefore declare a `cache_key`: the subset of its inputs the
fingerprint is taken over. `map_to_schema` declares
`["columns","target_schema"]`, keying the cache on the table's *shape*, which
is what the cached mapping is genuinely a function of. A `cache_key` naming an
input the stage didn't receive falls back to fingerprinting everything — an
over-specific fingerprint costs a miss, an under-specific one reuses a mapping
against data it was never validated on.

`"cache_as"` on an implementation spec is what makes the second document
actually cheaper: the model mapping stage records the *replay* implementation
against the layout, along with the mapping it worked out, so the next document
of that shape costs nothing.

Measured on the live database, running three documents through the seeded
workflow: run 2 (identical document) cached all six stages; run 3 (a
*different* document with the same layout) cached `classify_document`,
`detect_table_regions`, `extract_cell_structure` and `map_to_schema`, and
correctly did **not** cache `validate_types` or `write_xlsx` — those consume
the data itself, which really did change.

---

## The PDF → Excel reference workflow

Seeded by `db/02_seed_pdf_excel.sql`. It exists so the platform is concrete
rather than abstract, and because it is the work that has customers.

| # | Task | Input → Output | Implementations |
|---|---|---|---|
| 1 | `classify_document` | pdf → doc_type, page_count | `python` text density; `model` fallback |
| 2 | `detect_table_regions` | pdf → [(page, bbox)] | `python` ruled lines; `model` fallback |
| 3 | `extract_cell_structure` | pdf + regions → grid | `python` pdfplumber; `model` fallback |
| 4 | `validate_types` | grid → typed grid, errors | `python` only |
| 5 | `map_to_schema` | typed grid + target → rows | `python` replay, `python` template match, `model` mapping |
| 6 | `write_xlsx` | rows → file path | `python` only |

Stages 4 and 6 having no model implementation is the point, not an omission.
Six stages at 97% accuracy each is 83% end to end; the chain only holds because
most stages are exact. **When adding a stage, add its deterministic
implementation first and only add a model implementation if the deterministic
one measurably fails.**

A seventh node, `pdf_to_excel`, is a **composite** whose expansion is those six.
It exists so `POST /v1/run` can reach the workflow from a prompt, and so the
typechecker verifies the six stages actually satisfy the contract it advertises
before any of them run.

Model implementations receive the PDF as an attached document, not as a path
string — `"attach_documents": ["pdf_path"]` on the spec. Without that a
"fallback" would be handed the string `/tmp/x.pdf` and would confidently invent
an answer about a document it never saw, which is worse than no fallback.

---

## API

| Route | Behaviour |
|---|---|
| `POST /v1/run` | `{prompt, inputs, quality_bar?, max_cost?}`. Match → execute (200), or decompose → create a proposal and return **202** with its id. |
| `GET /v1/runs` | Run history with cost and latency totals. |
| `GET /v1/runs/{id}` | Status, plan, per-stage traces, totals. |
| `GET /v1/proposals` | Pending proposals with their typecheck results. |
| `GET /v1/proposals/{id}` | One proposal. |
| `POST /v1/proposals/{id}` | `{decision: "approve"｜"reject"}`. Approving persists the tasks and executes. |
| `GET /v1/tasks`, `POST /v1/tasks` | List, search, register. |
| `POST /v1/tasks/{id}/implementations` | Register an implementation. |
| `POST /v1/tasks/{id}/evals` | Attach an eval case set. |
| `POST /v1/evals/{id}/run` | Score every implementation of that task; write `eval_results`. |
| `GET /v1/config` | The four raised decisions and their current values. |

Approving is re-typechecked at decision time, not trusted from storage: the
graph moves between proposal and decision, and a task the plan reuses can be
superseded or have its last implementation disabled in the interim.

---

## Decisions raised, not guessed

`implement.md` names four decisions to flag rather than pick silently. They are
**settings with documented defaults**, so changing one is a config edit and a
deliberate act rather than a code change nobody reviews. `GET /v1/config`
reports the live values. **These four want a human's answer; the defaults are
placeholders chosen to fail safely, not recommendations.**

**1. The auto-match score threshold** — `AUTO_MATCH_THRESHOLD`, default `0.03`.
There is no principled value before there are real prompts to tune against.
0.03 is roughly the fused RRF score of a result that ranks first in one
retrieval arm and second in the other (`1/61 + 1/62 ≈ 0.0325`) — "both arms
agree this is the answer". Schema validation is the real gate; this only
decides whether to bother checking it.

**2. Whether `map_to_schema` may run unattended on a first-time layout** —
`ALLOW_UNREVIEWED_FIRST_LAYOUT_MAPPING`, default `false`. This is the one stage
where a wrong-but-plausible answer is both likely and invisible downstream.
Implemented generically: any implementation whose spec sets
`"first_layout_requires_review": true` will not run against a layout the cache
has never seen, so the executor doesn't hard-code a task name.

**3. Where run artifacts live once the temp directory is gone** —
`ARTIFACT_ROOT` / `KEEP_RUN_ARTIFACTS`, default `./artifacts` and `true`. A run
whose output file was deleted before anyone fetched it is a failed run that
reports success, and `.xlsx` files are the product here, not scratch. Nothing
cleans this directory up yet — that is the open half of this question.

**4. Whether a failed stage fails the run** — `FAIL_RUN_ON_STAGE_FAILURE`,
default `true`. Note what the permissive branch does and does not do: it keeps
the partial outputs, and it still reports the run as **failed**. Relabelling a
run with a hole in it as "succeeded" is the failure mode this setting exists to
make explicit, not to hide.

---

## Frontend

`frontend/` is a copy of `frontend_v2/frontend_v2` with `lib/api.ts` pointed at
this backend. `WorkflowGraph.tsx` is reused unchanged — hand-drawn SVG, no graph
library, because the graphs are small and the design language is custom.
`opsToGraph.ts` gained an adapter (`planToGraph`) rather than a rewrite: a
plan's `{nodes, edges}` is the same shape as a change set's create-and-link ops.

Deleted on copy: `Layer2Evidence.tsx` (no Layer 2 here) and `GroundedAnswer.tsx`
(no chat endpoint here).

Three things were built, and only three:

1. **A per-stage trace strip** (`components/TraceStrip.tsx`) on the run view —
   implementation used, outcome, cost, latency, one row per attempt. Failed
   attempts are shown, not filtered: a stage that succeeded on its third
   implementation is a different fact from one that succeeded first time.
2. **A typecheck-problems list** on the proposal detail page. A plain `<ul>`.
   A proposal that failed typecheck renders its problems and shows no approve
   button.
3. **Cost and latency totals** on the run view.

No graph library, no component library, no auth UI, no CRUD screens for
implementations or evals (curl is fine for those), no charts, no dark-mode
toggle.

---

## Built for the merge with `backend_v2`

The things that look like overkill for a single-operator v1 are the ones that
cost nothing now and become a data migration later:

- the table is `task_nodes`, matching `backend_v2`
- the four temporal columns (`t_valid`, `t_invalid`, `t_created`, `t_expired`)
  exist on nodes and edges even though v1 only sets two
- every read filters `t_invalid IS NULL`, and the HNSW and FTS indexes are
  **partial** on that predicate — a full index keeps superseded rows in the
  proximity graph and silently degrades recall as versions accumulate
- `provenance` uses `backend_v2`'s enum values
- edges are polymorphic (`source_table`, `target_table`) though v1 only writes
  task→task
- trace columns overlap `backend_v2`'s where the concept is the same
- UUID keys via `gen_random_uuid()`

There is no `superseded_by` column: supersession sets `t_invalid`, which is how
`backend_v2` already does it, and carrying both would mean two sources of truth
for "is this row live".

The six mistakes `backend_v2` already made and fixed are not repeated here:
dicts go to asyncpg directly for JSONB (never pre-serialised and cast), HNSW
indexes are partial, hydration is batched with `= ANY($1::uuid[])`, and the
rest are noted at their sites.

## Not in v1

Authentication or multi-tenancy, a debate panel, public submission or payments,
bi-temporal history beyond the columns above, background job queues (runs
execute synchronously), and Rust.
