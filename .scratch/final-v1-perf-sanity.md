# FINAL-V1 §5 — performance sanity baseline

Scope: production-readiness **sanity** check, not scale benchmarking. Measure the
real code paths for small representative workloads over repeated runs; look for
obvious pathological behaviour (N+1, unbounded read, repeated embed/rank/compile,
unbounded retry). No micro-optimisation, no production-code changes.

Probe: `.scratch/perf_probe.py` (hand-run, not pytest). Raw numbers:
`.scratch/perf_results.json`. Every row below is the median/95th of **N=20**
timed calls (embedding: N=6 distinct + 5 repeat) after one warm-up call.

## Environment

- Remote **Supabase session pooler** (`aws-0-ap-south-1.pooler.supabase.com:5432`)
  reached over the **public internet from a Windows dev box**.
- `create_pool(dsn, statement_cache_size=0)` — transaction-pooler safe, registers
  the JSONB codec.
- **Every absolute latency below includes one or more real network round-trips**
  (~25–40 ms each, judging by the single-query `get_problem`/`get_benchmark`
  floor). These numbers are a **ceiling, not a floor** — co-located app+DB will be
  far lower. What matters for a sanity check is the **shape**: the per-call
  **query count** and whether it grows with data size.
- `query_count` is "SQL statements put on the wire for one instrumented call",
  counted by monkeypatching `asyncpg.connection.Connection.{fetch,fetchrow,
  fetchval,execute,executemany}`. It therefore **includes** `tenant_transaction`
  BEGIN / `SET LOCAL` / COMMIT and asyncpg's per-acquire connection-reset
  statement — i.e. it slightly over-counts vs. "queries the service wrote".
  Roughly: subtract ~1 per pooled acquisition for the true figure.

## Measured baselines

| Path | p50 ms | p95 ms | mean ms | DB queries | notes |
|---|---:|---:|---:|---:|---|
| `product_model.get_problem` | 40 | 255 | 50 | **2** | 1 scoped `SELECT` + acquire reset. Flat. |
| `product_model.list_problem_solutions` | 78 | 304 | 117 | **4** | `get_problem` gate + 1 `SELECT s.*`. Flat. |
| `product_model.get_benchmark` | 38 | 255 | 47 | **2** | 1 `SELECT`. Flat. |
| `product_model.problem_leaderboard` | 150 | 374 | 157 | **8** | **fixed** — see below. No N+1. |
| `product_model.get_evaluation` | 58 | 84 | 61 | **4** | 1 `SELECT` + 1 `SELECT` for linked execution ids. Flat. |
| `product_model.complete_evaluation` (6 linked execs) | 284 | 441 | 283 | **13** | 1 fetch + 6 link `INSERT`s + 1 verified-count + 1 `UPDATE` + txn. Linear in `len(execution_ids)` (caller-bounded), not a read N+1. |
| `durable_run.start_run` (3-node) | 133 | 361 | 149 | **7** | 1 run `INSERT` + 3 node `INSERT`s + txn. Linear in node count. |
| `durable_run.execute_run` (3-node, trivial callback) | 1081 | 1788 | 1150 | **47** | per-node claim/finish + `_load` reload each drive pass + finalize. Linear in node count; drive loop is bounded (below). |
| `durable_run.resume_run` (1 crashed node, mid-node worker-loss) | 1180 | 2043 | 1243 | **70** | claim + resume bookkeeping + re-drive of remaining nodes. Bounded. |
| `mcp.find_problem` | 27 | 238 | 48 | **2** | thin wrapper over `product_model.find_problem`. |
| `mcp.inspect_problem` | 283 | 744 | 332 | **16** | `get_problem` + `list_problem_benchmarks` + `list_problem_solutions` + `problem_leaderboard` — see redundancy note. Fixed. |
| `mcp.find_best_solution` | 172 | 213 | 160 | **10** | `find_problem` + `problem_leaderboard`. Fixed. |
| `claim_graph_api.get_claim_graph_overview` (`link_mode="both"`, `with_status=True`) | 394 | 952 | 430 | **98** | **O(N nodes)** lifecycle-state fan-out — see watch-point. Bounded + documented. |
| `claim_graph_api.get_claim_graph_overview` (`with_status=False`) | 160 | 827 | 206 | **8** | node `SELECT` + total count + relations edge query + similarity LATERAL. Flat. |
| `embeddings.Embedder().embed_one` (distinct text) | 3431 | 5599 | 3815 | n/a | **external provider API** (Voyage/Gemini chain) over public internet. |
| `embeddings.Embedder().embed_one` (same text ×5) | 0.02 | — | 691* | n/a | calls 2–5 served from in-process `_EMBED_CACHE`; *mean skewed by the one cold call (3452 ms). |
| `find_best_way` tier-2 | — | — | — | — | **not measured, requires sandbox** (real repo + model). |

## Pathology review

### `problem_leaderboard` — N+1 check: NONE FOUND

`backend/app/services/product_model.py:479` `problem_leaderboard` issues a **fixed
4 SQL reads regardless of how many solutions or evaluations the problem has**:

- `list_problem_solutions` (`product_model.py:490`) → `get_problem` gate (1) + `SELECT s.*` (1)
- `list_problem_benchmarks` (`product_model.py:491`) → 1
- `list_problem_evaluations` (`product_model.py:494`) → 1

The per-solution work is a **pure-Python loop over the already-fetched lists**
(`product_model.py:499-533`: `se = [e for e in completed if e["solution_id"] == s["id"]]`).
No query is issued inside the loop. Wilson interval (`product_model.py:515`) is
arithmetic. Measured `query_count = 8` (= 4 reads + txn/reset overhead) with 2
solutions + 2 completed evaluations; re-running the probe with the larger
`test_product_model_e2e` fixture (2 sol / 30 execs each) gives the **same 8**.
**Not an N+1. Not an unbounded read** in practice (solutions/evaluations per
problem are small product objects).

### `claim_graph_api.get_claim_graph_overview(with_status=True)` — bounded O(N) fan-out — WATCH-POINT (not a regression)

`backend/app/services/claim_graph_api.py:396`:

```python
for cid, state in await asyncio.gather(*(_one(c) for c in node_ids)):
```

runs `claims.get_claim_lifecycle_state` (`backend/app/services/claims.py:752`)
**once per returned node**, and each of those calls issues ~4–5 sub-`SELECT`s
(truth_state, open-conflict-trigger, SUPERSEDES/CONTRADICTS edges, reaffirming
relations). Measured: `query_count` **98 with `with_status=True` vs 8 with
`with_status=False`** for a ~20-node graph → ≈4.5 queries per node.

This is **bounded and deliberate**, not a hidden pathology:

- `limit` is clamped to `_GRAPH_OVERVIEW_MAX_LIMIT = 600` (`claim_graph_api.py:342`);
- concurrency is capped by `asyncio.Semaphore(_GRAPH_OVERVIEW_STATUS_CONCURRENCY=8)`
  (`claim_graph_api.py:384`);
- the cost is documented in the docstring (`claim_graph_api.py:329-333`: "one
  bounded read per node … Pass False for a faster raw dump");
- a `with_status=False` fast path exists and is flat at 8 queries.

Worst case is ~600 × 5 ≈ 3000 statements for a single maxed-out overview call.
Fine for the current corpus (tens of claims) and for a visualiser that asks for
`with_status=False` on large graphs. **Follow-up for the lead** (not fixed here):
if claim counts grow into the hundreds, replace the per-node loop with one
set-based lifecycle query, or default `with_status=False` above some node count.

### `mcp.inspect_problem` — redundant repeated reads — MINOR (constant factor, not data-scaled)

`backend/app/mcp_server/server.py:2725` calls `get_problem`, then
`list_problem_solutions` (which calls `get_problem` again as its visibility gate),
then `problem_leaderboard` (which calls `list_problem_solutions` → `get_problem`
**again**). Net: `get_problem` runs 3× and the solutions `SELECT` runs 2× per
`inspect_problem`. It is a **fixed constant** (does not grow with data), costs ~2
extra cheap PK look-ups, and keeps each service function independently
scope-safe. Not a pathology; noted so the lead can decide whether a single
pre-scoped fetch is worth threading through.

### Durable execution — retry / drive loops are bounded — NONE FOUND

- `_run_one_node` (`backend/app/execution/durable_run.py:219`):
  `while attempt < max_attempts:` — hard bound `max_attempts` (default 3).
  Inline retry only when `_is_retryable(ec) and attempt < max_attempts`
  (`durable_run.py:236`); `RETRYABLE_ERROR_CLASSES` is a fixed frozenset
  (`durable_run.py:39`).
- `resume_run` (`durable_run.py:362`) re-arms a failed node **only** if
  `_is_retryable(n["error_class"]) and n["attempt_count"] < n["max_attempts"]`
  (`durable_run.py:383`) — no unbounded re-arm.
- `_drive` (`durable_run.py:247`) `while progressed:` advances only on a real node
  state change and `break`s on the first non-success; it cannot spin. `_load`
  (`durable_run.py:153`) reloads all nodes once per drive pass — 2 queries ×
  (#passes ≈ #nodes). Linear in node count; measured 47 queries for a 3-node
  happy path, 70 for a 3-node resume-after-crash. No growth with anything
  unbounded.

### Embedding — no repeated embed of the same text — NONE FOUND

`backend/app/services/embeddings.py:181-199`: `embed` does a cache pass against
the process-local `_EMBED_CACHE` and only sends **misses** to the provider chain.
Measured: same-text ×5 → p50 **0.02 ms** (4 cache hits after 1 cold call).
Distinct text is a genuine external API call (p50 3.4 s, p95 5.6 s over the
public internet) — expected and outside our control; it is not a DB path and is
not called in a loop on any of the read paths above.

### Unbounded-read scan — minor watch-points, acceptable for V1

`list_problem_solutions` / `list_problem_benchmarks` / `list_problem_evaluations`
(`product_model.py:280/222/430`) have **no `LIMIT`**. They are bounded in
practice by "solutions / benchmarks / evaluations attached to one problem" —
small product objects — so this is acceptable for V1. `find_problem` /
`list_problems` / `get_claim_graph_overview` all clamp their `LIMIT`
(50 / 200 / 600) and the graph reader also returns a `truncated` flag.
**Follow-up for the lead:** add a defensive cap to the three `list_problem_*`
readers if a single problem is ever expected to carry thousands of evaluations.

## GO / NO-GO

**GO.** No obvious production-readiness regression. Every DB path has a
**bounded, data-size-independent (or explicitly `LIMIT`-capped) query count**:
`problem_leaderboard` is a fixed 4 reads with **no N+1**; durable execution has
**bounded retries and a non-spinning drive loop**; embeddings **cache repeats**.
The one O(N) fan-out — `get_claim_graph_overview(with_status=True)`, ~4.5
queries/node — is **bounded (≤600 nodes), concurrency-limited, documented, and
has a flat `with_status=False` fast path**; it is recorded above as a follow-up,
not a blocker. Absolute latencies are inflated by a remote session pooler over
the public internet and are a ceiling, not a representative production number.

Baseline documented; probe is re-runnable via
`DATABASE_URL=... python .scratch/perf_probe.py`.
