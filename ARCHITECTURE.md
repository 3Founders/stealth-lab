# Architecture — graph-memory + HTN agent on SWE-bench Pro

What each part is, why it is shaped that way, and where it is verified.
Every claim marked **[T-n]** is checked by a test — see [TEST.md](TEST.md).

---

## The question

**Does a knowledge graph of a codebase's own past fixes help a small model fix
a new bug — and does HTN decomposition help more?**

Three arms per instance, on identical repository snapshots:

| arm | agent | memory block | isolates |
|---|---|---|---|
| `no_memory` | flat | — | baseline |
| `graph_memory` | flat | retrieved precedents | **does the graph help?** |
| `htn_memory` | HTN DAG | retrieved precedents | **does decomposition help?** (memory fixed) |

`no_memory` → `graph_memory` and `graph_memory` → `htn_memory` each vary
exactly one thing. **[T-31]**

---

## 1 · Corpus and grading

**SWE-bench Pro** — 731 real GitHub issues, 11 repos, 4 languages
(go 38%, python 36%, js 23%, ts 3%). Each carries the issue text, the repo
pinned *before* the fix, the reference patch, and hidden tests
(`fail_to_pass`, `pass_to_pass`).

`pro_harness.evaluate()` runs the candidate patch in the instance's own Docker
image. **Resolved** iff every `fail_to_pass` passes **and** no `pass_to_pass`
breaks. A test that never ran counts as a failure — treating absence as a pass
is the one bug that manufactures successes. **[T-12]**

**Gold runs first, every instance.** If the reference patch does not resolve,
the instance is excluded before either arm spends a token. NodeBB is exactly
this case: its gold patch passes all 3 f2p then breaks 6 unrelated email
tests, so no patch can score better. **[T-11]**

Pilot: **10 of 11 repos** grade their own gold patch correctly (go 4/4,
python 3/3, ts 1/1, js 2/3; NodeBB the sole failure).

---

## 2 · The knowledge graph

```
task_node ──OWNS/RESOLVED_AT──▶ knowledge_node
the issue                        where the fix landed
title, problem statement,        files, symbols, interface,
requirements, interface,         and the gold diff
issue_categories                 (7.1 MB across 731)
```

731 task nodes + 731 knowledge nodes + 731 edges in `stealthlab_swebench`
(pgvector, HNSW `vector_cosine_ops`). **[T-21]**

**Why the split.** `HybridRetriever` matches a new issue against past *task*
nodes by meaning and keyword, then traverses one hop to pull the *knowledge*
node. Retrieval finds the **similar problem**; the graph supplies the
**answer location**. A flat list cannot do the second step. Verified not
decorative: 40/40 queries return knowledge nodes reached by expansion.
**[T-22]**

### Two embedding columns

| column | text | result |
|---|---|---|
| `embedding` | title + problem statement | baseline |
| `embedding_joint` | title + problem + **the gold diff** | **wins** |

Issue-only vectors cannot separate two senses of a domain word — *"Flipt Fails
to Authenticate with AWS ECR"* (registry login) outranked *"Authentication
cookies are not cleared"* (request middleware) for an auth-middleware query.
Only the diffs distinguish them.

**n=400, leave-one-out: joint better on 19 queries, worse on 5, tied on 376.
Sign test p = 0.0066.** All four metrics move together. `embedding_joint` is
the default. **[T-24]**

### Leave-one-out via the bi-temporal columns

Holding out instance *X* sets `t_invalid` on its task node, knowledge node and
edge. Every backend read already filters `t_invalid IS NULL`, so *X* vanishes
without being deleted, and testing another instance is two UPDATEs rather than
a 37-minute re-embed. The runner **aborts** if *X* retrieves itself.
**[T-23]**

The HTN tree is rebuilt *after* the holdout: internal nodes route on the mean
of their children's embeddings, so a tree built while *X* was live has *X*
folded into its parent's routing signal.

---

## 3 · The agents

Both use the same `RepoSandbox`, the same tools, the same 40-step leaf budget,
temperature 0, and both return `AgentRun` — so the harness cannot tell which
ran. That is what makes flat-vs-HTN controlled. **[T-45]**

### Tools

`list_dir` · `search` · `read_file` · `edit_file` · `create_file` ·
`delete_file` · `finish`

Editing is **exact string replacement**, and the diff is generated
mechanically. Asking a model to author a `git apply`-able diff conflates
fixing the bug with counting context lines, and a patch that fails to apply
grades identically to a wrong answer.

Three capabilities exist because an audit against all 731 gold patches showed
the agent could not otherwise do the corpus:

| requirement | instances | was |
|---|---|---|
| create a new file | **243 (33.2%)** | **impossible** |
| delete a file | 18 (2.5%) | impossible |
| rename | 10 (1.4%) | impossible |

**6 of the 20 experiment instances required a new file** and were unwinnable
in every arm regardless of model or retriever — 30% of the sample producing
guaranteed concordant failures, contributing zero discordant pairs. **[T-41]**

`edit_file` falls back to **whitespace-tolerant matching** when an exact match
fails: same non-space characters, still unique, replacement re-indented to the
file's own style by counting depth on a ladder of the snippet's distinct
indent widths. Go is 38% of the corpus and tab-indented; a model emitting
spaces could never match. **[T-42]**

### Flat agent (`agent.py`)

One growing transcript, resent whole on every call. Cost is `d·N²/2`.
Measured: teleport **1,067,259 tokens** in one arm over 40 steps — mean 26,681
per call, final context ~53K, because 22 file dumps rode along on every later
step.

### HTN agent (`htn_agent.py`) — DAG

```
        [1] create src/hooks/useWindowWidth.ts     deps: []
              │
        [2] export it from the barrel file        deps: [1]
              │
        [3] update the consumer to use the hook   deps: [2]

        [4] add the changelog fragment            deps: []   ← independent
```

**Planner** (one LLM call) emits a JSON DAG of 2–6 nodes with explicit `deps`.
**Executor** runs nodes in topological order, each in **its own fresh message
list** — system + issue + the DAG + one-line notes from finished nodes + that
node's local tool results. Context is O(local steps), not O(all steps).
**[T-33]**

**Failure is contained.** A node that fails after its replans is marked
`failed`, and only its *transitive dependents* become `blocked`. Independent
branches still run. **[T-35]**

**Replanning is localized.** A failed node asks the planner for one
alternative *method* for that node alone, up to `MAX_METHODS=2`. Completed
nodes keep their edits in the sandbox and their notes in the DAG; nothing
valid is re-executed. **[T-32]**

The DAG is validated on parse: self-loops and dangling deps dropped, cycles
broken by keeping only backward edges, and a single line of prose rejected as
"not a decomposition" rather than accepted as a one-step plan. **[T-34]**

---

## 4 · What is measured

**Resolution** — McNemar's exact test on paired outcomes, reported **with the
discordant-pair count**. Concordant pairs carry no information; with zero
discordant pairs there is no test, which is different from "no difference".
**[T-51]**

**Copyability** — what fraction of the gold patch's added lines were already
visible in what the agent was shown. If the memory arm wins, this says whether
it won by *reasoning from* precedents or by *copying one that contained the
answer*. Running 0.03–0.11, so wins are not lookup. **[T-53]**

**Retrieval quality** — `file_recall`, `dir_recall`, `same_repo_rate`, scored
before any agent runs, so "retrieval was wrong" is distinguishable from
"retrieval was right and the agent failed anyway". **[T-54]**

**HTN telemetry** — nodes done/failed/blocked, replans, DAG edges, planner
calls, decomposition failures.

### Power — read this before reading any p-value

The exact test floors at `2/2^k`, so **k ≥ 6 discordant pairs one-way** is
required for p<0.05. At a 10% baseline:

| n | +10pp | +20pp | +30pp | +40pp |
|---|---|---|---|---|
| **20** | 0.003 | 0.041 | 0.188 | 0.435 |
| 40 | 0.017 | 0.146 | 0.505 | 0.839 |
| 120 | 0.029 | 0.508 | 0.968 | 1.000 |

**A null at n=20 most likely means underpowered, not "no effect".**
`check_results.py` prints the shortfall on every summary so this cannot be
forgotten.

---

## 5 · Running it

```bash
python experiments/swebench_pro/run_graph_experiment.py -n 20     # 3 arms
python check_results.py final                                     # status, any time
```

Resumable: results append to JSONL and completed instances are skipped, but
rows that recorded a harness error are **not** counted as done — a transient
failure baked in permanently is how an earlier file acquired frozen
`api_error` rows. **[T-52]**

Every LLM call has a **180s timeout** and backoff capped at 20s. Without them
one unresponsive request froze a whole sweep for 77 minutes, looking exactly
like normal progress.
