# Where the 20 minutes goes

Measured from the most recent complete run with real timing data,
`experiments/swebench_pro/graph_experiment_3arm_check1.jsonl` (6 instances,
`--arms no_memory,htn_memory`, model=`gemma-4-31B-it`, `max_steps=28`,
`--parallel-grading` **not** passed, i.e. off — the default). This is not a
guess: every number below is read directly out of `rec["wall_seconds"]` and
`rec[arm]["wall_seconds"]`, which `run_one()` already records
(`run_graph_experiment.py:220,328,401`).

## 1. The headline split

| bucket | mean | median | % of instance |
|---|---|---|---|
| **instance total** | 880.9s (14.7min) | 911.5s | 100% |
| **agent LLM loop** (sum of both arms' `run.wall_seconds`) | 309.3s | 252.5s | 35% |
| **everything else** (grading + docker + db + fs) | 571.5s | 472.9s | **65%** |

**The tests, not the model, are the bottleneck.** "Running the model" (both
arms' full tool-call loop, LLM round-trips included) is a third of the wall
clock. The other two-thirds is `evaluate()` — spinning up a fresh docker
container per arm and running that repo's *real* CI-grade test suite
(`ansible-test`, `go test`, full jest/playwright suites, etc.), not a unit
test.

## 2. Function-by-function

Per instance, `run_one()` (`run_graph_experiment.py:209`) does, **in this
order**:

| step | function | what it does | cost driver |
|---|---|---|---|
| 1 | `hold_out` / `rebuild_hierarchy` / `retrieve` / `restore_all` | Postgres queries + one embedding call | milliseconds–seconds, negligible |
| 2 | `pull_image` (`pro_harness.py:160`) | `docker image inspect`, skip if present | ~0s once the image is cached locally |
| 3 | `snapshot_repo` | tar the repo at `base_commit` | seconds |
| 4 | `evaluate()` **on the gold patch** | full docker container, real test suite | **skipped in 5/6 rows** — hit in `gold_cache.json` (16 instances cached). First-time cost is identical to step 6. |
| 5 | `agent.run()` / `htn.run()` **per arm, sequential** | the actual LLM tool-call loop, up to `max_steps=28` steps, each an LLM call with `REQUEST_TIMEOUT=180s` and up to `MAX_RETRIES=4` | **35% of wall time** — this is "the model" |
| 6 | `evaluate()` **per arm, sequential** (`grade_one`, line 294) | full docker container per arm: `git apply` → `run_script.sh` → real test suite → parse | **the majority of the other 65%** |
| 7 | `remove_image` / `safe_rmtree` | `docker rmi -f` + recursive delete of the instance workdir | seconds |

Step 6 is sequential **by default**: `--parallel-grading` defaults to `False`
(`run_graph_experiment.py:548`). With 2 arms that's 2x the grading wall-clock
paid serially; with 3 arms (the normal config) it's 3x. This is the single
biggest lever available without touching the agent at all — `grade_one` is
already written to be race-free (isolated workspace + `--rm` container per
arm, nothing shared), the flag is off only because it can spike RAM/CPU on a
constrained host (`--memory 6g --cpus 4` reserved *per concurrent container*).

## 3. Is this actually fixable, or is it the benchmark?

Per-instance total wall time correlates with how much grading happened, not
with agent speed:

| instance | total | agent (both arms) | implied grading+overhead |
|---|---|---|---|
| flipt | 428s | 97s | 331s |
| ansible | 460s | 379s | 81s (small ansible-test slice) |
| teleport | 783s | 436s | 347s |
| vuls | 1040s | 126s | 914s |
| element-web | 1242s | 83s | 1159s (jest/playwright suite) |
| openlibrary | 1333s | 734s | 599s |

`element-web` spends 83s on the model and **1159s on grading** — an 18-file
JS/TS monorepo whose test container is genuinely slow to boot and run,
regardless of what patch it's grading. That part is the benchmark's cost, not
this codebase's — SWE-bench Pro's own upstream harness pays the same tax.
`--parallel-grading` doesn't shrink it either (there's only 1 arm's worth of
grading dominating, not 3 running serially) — nothing in this codebase can
make `jest`/`playwright` faster.

**So: expect real per-instance time to stay in the 5–20 min range regardless of
fixes here** — that's dictated by which repo you draw, not by this harness.
What *is* fixable is the multiplier from running grading arm-by-arm instead of
concurrently, which is a real 2–3x on the "everything else" bucket.

## 4. Recommendation

Pass `--parallel-grading` for local runs where the machine has the headroom
(3 concurrent containers × 6g/4cpu). It doesn't change what's being measured
(each arm still gets its own isolated `--rm` container and workspace — see the
comment at `run_graph_experiment.py:304-311`), only the wall-clock shape.
Nothing else in the "everything else" bucket is worth touching: DB ops are
already sub-second, `pull_image`/gold-eval are already cached
(`gold_cache.json`, `image_present()` short-circuit), and step 7's cleanup is
seconds.

---

# Why runs are not resolving — failure analysis

Superseded from an earlier 6-instance read by `graph_experiment_3arm_check1.jsonl`
finishing at **n=11**: zero errors, zero exclusions, one model
(`gemma-4-31B-it`), one budget (`max_steps=28`) — the cleanest dataset in the
repo. All `graded`/`htn` fields inspected directly, not inferred.

| arm | resolved | tokens | tokens/instance |
|---|---|---|---|
| `no_memory` | 5/11 (45%) | 1,944,103 | 176,736 |
| `htn_memory` | 4/11 (36%) | **996,396** | **90,581** |

Discordant pairs: `no_memory`-only 2, `htn`-only 1 → McNemar p = 1.0 — **no
evidence of a resolution difference in either direction** at this n (3
discordant pairs is the "no information" zone `mcnemar()`'s own docstring
warns about). What *is* solid: HTN resolves 4 of 11 on **51% of the tokens**
(996,396 / 1,944,103 — a 49% savings, not 49% usage), and won an instance
(`vuls`) that `no_memory` failed.

## The two structural defects behind most of the non-resolutions

Across the 32 nodes HTN planned in these 11 runs:

| node outcome | count | share |
|---|---|---|
| `done` | 15 | 47% |
| ran, unfinished (`pending`) | 6 | 19% |
| **never ran — budget-starved** (`attempts==0, pending`) | **7** | **22%** |
| never ran — dependency cascade (`attempts==0, blocked`) | 2 | 6% |
| ran, `failed` | 2 | 6% |

**8 of 11 runs ended in `stop_reason=step_budget`**; only 2 reached
`finished`. Root cause: `TOTAL_STEP_BUDGET=28` but
`MAX_SUBGOALS(4) × STEPS_PER_SUBGOAL(9) = 36` — the run could not fund even
one attempt per planned subgoal, and one node's retries could legally
consume the whole budget before a later node was ever attempted. Traced
exactly on `ansible-f327e65d`: node 1's three 9-step rounds (27 of 28 steps)
left node 2 a 1-step grant, below `MIN_VIABLE_SUBGOAL_BUDGET`, declined.

Separately: of 32 gold files across these instances, **53% are never
mentioned in any node's goal**, even by basename. No repair mechanism can fix
a file that was never named — `_verify_precondition` only rewrites paths that
*were* named (wrong directory, not missing entirely), and
`decompose_subgoal` (the only mechanism that can *add* a node to a plan) has
fired **0 times in 66 recorded HTN runs**. This is the same "cross-file miss"
pattern the earlier 6-instance read already named on `teleport`/`openlibrary`
— now confirmed structural, not incidental, and now measured (53%, not "some
files").

**Status: both defects fixed this session** — see the plan doc / commit for
`TOTAL_STEP_BUDGET` raised to 72, a reservation floor stopping one node from
starving never-run siblings, and per-node instrumentation (tokens, steps,
files touched, gold-file hit rate) added specifically so the cross-file-miss
question — is `_candidate_files` failing to surface the gold file, or is the
planner ignoring one it was shown? — is answerable from data on the next run
instead of guessed. The gold-file-omission fix itself is **not yet done**;
the instrumentation is what the next diagnosis run needs.

## Other failure modes present (n=11)

- **Wrong fix on the right file** (`vuls`/no_memory before the HTN arm fixed
  it): edited the correct file, applied cleanly, but the fix itself was
  logically wrong — the one failure mode neither more retrieval nor more
  budget obviously helps.
- **No self-verification step**: neither arm runs the test suite before
  submitting a patch, so a regression is invisible to the agent until the
  harness grades it. Contributes to `p2p_broke` cases in the broader
  (pre-Aug-15) result history; not isolated to a specific instance here.

## Model validity — corrected

An earlier version of this file called `gemma-4-31B-it` "the
default/unreliable model" versus "the currently-preferred deepseek-v3.2".
Measured across every arm-run in the repo's result files, the opposite is
true:

| model | arm-runs | valid | api_error | validity |
|---|---|---|---|---|
| **gemma-4-31B-it** | 99 | 96 | 3 | **97%** |
| deepseek-v3.2 | 18 | 3 | 15 | 17% |
| gpt-oss-120b | 1 | 0 | 1 | 0% |

gemma-4-31B-it is the only model in this harness that reliably completes
episodes, and the only one with enough valid runs to measure anything from.
The time-distribution analysis above (grading cost vs. agent-loop cost) is
model-independent regardless of which model wrote the patch, and does not
depend on this correction.

---

# How `htn_memory` actually routes to the backend

Read straight off the call chain, `run_graph_experiment.py` → `graph_memory.py`
→ `backend/app/services/{retrieval,hierarchy}.py` → `htn_agent.py`. The
important, non-obvious fact first:

**There are two separate backend "routing" mechanisms in this codebase, and
only one of them actually reaches the agent.** `graph_memory` and `htn_memory`
are given the *identical* memory block, retrieved the *identical* way
(`HybridRetriever`). The HTN *tree* descent (`hierarchical_search`) runs too,
every instance, but its result is recorded for diagnostics only
(`rec["htn"]`) — it never touches what the agent sees. The actual difference
between `graph_memory` and `htn_memory` is entirely on the **agent** side:
whether the tool-call loop is flat or decomposed into a subgoal DAG. Below is
every function in the order it actually executes.

## A. Retrieval — supplies the memory block (same for graph_memory AND htn_memory)

```
run_one()                                    run_graph_experiment.py:209
  │
  ├─ hold_out(pool, iid)                     graph_memory.py:77
  │    UPDATE task_nodes/knowledge_nodes/edges SET t_invalid=now()
  │    WHERE this instance — so it can't retrieve itself.
  │
  ├─ rebuild_hierarchy(pool, embedder)       graph_memory.py:105
  │    → build_hierarchy_for_table(pool, "task_nodes", ...)
  │                                          backend/app/services/hierarchy.py:263
  │    Rebuilds the beam-search tree over the now-live (holdout-adjusted)
  │    leaves. Needed for section B below, not for retrieve() itself.
  │
  ├─ query = f"{title}\n\n{problem_statement[:1500]}"
  │
  ├─ retrieve(pool, query, embedder, top_k, embedding_column)
  │                                          graph_memory.py:163
  │    │
  │    ├─ HybridRetriever(pool, embedder, scope, embedding_column, tables)
  │    │                                     backend/app/services/retrieval.py:80
  │    │
  │    └─ .retrieve(query, top_k, expand_depth=1, max_context_nodes)
  │         backend/app/services/retrieval.py:182
  │         │
  │         ├─ _vector_search()   pgvector `<=>` ANN over embedding_column
  │         ├─ _lexical_search()  Postgres `ts_rank` full-text search
  │         ├─ Reciprocal Rank Fusion of the two rank lists → top_k entrypoints
  │         └─ one-hop traverse_from() along non-PARENT_OF edges
  │              (GraphStore.traverse_from, expand_depth=1)
  │              → RetrievalResult(nodes=[...], entrypoint_ids=[...])
  │    │
  │    ├─ _instance_of() / _hydrate()        graph_memory.py:129,143
  │    │    maps each retrieved node back to its instance_id + files/
  │    │    symbols/patch/requirements (LEFT JOIN task_nodes↔knowledge_nodes
  │    │    filtered to node_type='code_location')
  │    │
  │    └─→ list[GraphHit], diag              (rec["retrieval"]/rec["retrieval_diag"])
  │
  ├─ restore_all(pool)                       graph_memory.py:95  (un-invalidate)
  │
  └─ render_context(hits, max_chars, include_patches, ...)
       graph_memory.py:256
       Turns GraphHit list into the actual prompt text: each precedent as
       "[repo] title / files changed / how that fix was made" (gold diff
       rewritten to SEARCH/REPLACE via diff_to_search_replace, so it matches
       the agent's own edit_file tool format) → `memory_block: str`
```

`memory_block` is then passed straight through:
```python
runner = agent if kind == "flat" else htn        # run_graph_experiment.py:289
run = runner.run(sample, sandbox, arm, memory_block=memory_block if use_memory else "")
```
For `graph_memory`, `runner` is the flat `Agent` (`agent.py`) — `memory_block`
goes straight into its system/first-user message every step. For
`htn_memory`, `runner` is `AugmentedHTNAgent` (`htn_agent.py`) — see section C.

## B. The HTN tree descent — diagnostic only, NOT what the agent sees

```
run_one()
  └─ rec["htn"] = await htn_route(pool, query, embedder)     graph_memory.py:235
       └─ hierarchical_search(pool, "task_nodes", query, beam=3, adaptive=True)
                                              backend/app/services/hierarchy.py:391
            Beam-descends the tree rebuilt in step A: at each level, route
            toward the child whose mean-child-embedding is closest to the
            query vector (mean-vector routing, hierarchy.py:217, per the
            module docstring), narrowing the beam width adaptively, until a
            leaf task_node is reached or confidence drops below
            confidence_floor (used_flat_fallback=True).
       └─→ {instance_id, leaf_name, similarity, used_flat_fallback, comparisons}
```
This result is written to `rec["htn"]` (a top-level diagnostic field on the
result row) purely so the beam-search mechanism can be evaluated on its own
merits later — the module docstring is explicit about this ("recorded
alongside but not blended"). **Nothing in `AugmentedHTNAgent.run()` reads
`rec["htn"]` or calls `htn_route`/`hierarchical_search` itself.** The name
"htn_memory" refers to the agent's *task*-decomposition strategy, not to this
tree-descent retrieval path — a naming collision worth knowing about before
assuming the arm comparison says anything about the beam search.

## C. The HTN agent — where memory_block is actually consumed

```
AugmentedHTNAgent.run(instance, sandbox, arm, memory_block, ...)   htn_agent.py:925
  │
  ├─ nodes = self._decompose(instance, memory_block, usage, trace, sandbox)
  │                                          htn_agent.py:454
  │    │  *** the only place memory_block is read anywhere in the HTN path ***
  │    │
  │    ├─ self._seed_plan() → method_library reuse check (skip decompose
  │    │    entirely if a stored DAG for a near-identical goal exists)
  │    │
  │    ├─ self._candidate_files(instance, sandbox)          htn_agent.py:412
  │    │    LOCAL, zero-LLM: regex-extracts identifiers from the problem
  │    │    statement/interface, greps them via RepoSandbox.search(), ranks
  │    │    files by hit count → up to 8 verified real paths
  │    │
  │    ├─ build planner prompt:
  │    │    PLANNER_SYSTEM.format(repo, max_subgoals)
  │    │    + problem_statement + spec_block(instance) + candidate_block
  │    │    + memory_block            ← the retrieved precedents land HERE
  │    │
  │    ├─ self._chat(msgs, usage)                            htn_agent.py:295
  │    │    ONE LLM call (shared is_transient/backoff_seconds retry policy
  │    │    with agent.py) → raw plan text
  │    │
  │    └─ self.parse_dag(text) → list[Node]  (cycle-broken, MAX_SUBGOALS-capped,
  │         dangling `deps`/`requires` edges dropped)
  │
  ├─ self._schedule(instance, sandbox, nodes, usage, tool_log, trace)
  │    Runs the DAG: for each node whose `deps`/`requires` are satisfied,
  │    executes it via _run_node → per-step tool-call loop against
  │    RepoSandbox (edit_file/search/read_file/...), each step another
  │    self._chat() call. _build_context() (htn_agent.py:509) builds the
  │    (done, plan) blocks shown to each node's executor prompt — this is
  │    NOT memory_block again; retrieved precedents are only ever seen once,
  │    at planning time, not re-shown per subgoal.
  │    On failure/low-confidence evidence: self._replan() (htn_agent.py:492)
  │    issues one more targeted LLM call for an alternative subgoal text.
  │
  └─→ AgentRun(patch=sandbox.diff(), usage, tool_calls, stop_reason,
              wall_seconds, htn={plan, nodes, replans, subgoals_done, ...})
```

`AugmentedHTNAgent` (htn_agent.py:1010) subclasses `HTNAgent` and overrides the
no-op extension points named in the "extension points" block (`_build_context`
etc.) plus adds `_shallow()` (an SLA time-budget gate) and `_persona()`
(keyword-matched system-prompt selection per subgoal) — neither touches
`memory_block` or the retrieval path; both operate purely on the already-built
DAG and elapsed wall time.

## The one-sentence version

`memory_block` is built once per instance by `HybridRetriever` (vector RRF +
Postgres FTS + one-hop graph expansion) and handed unchanged to whichever
agent is running; the HTN *tree* beam-search (`hierarchical_search`) is a
separate mechanism that runs in parallel purely for its own diagnostic
row and never feeds the agent. What actually makes `htn_memory` different from
`graph_memory` is that `AugmentedHTNAgent._decompose()` reads `memory_block`
once, at planning time, to help write a subgoal DAG — the flat `Agent` instead
re-sends `memory_block` on every single step.
