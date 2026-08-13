# The graph-memory / HTN experiment, explained

`README.md` and `DESIGN_EXPLAINED.md` next to this file describe the
**pilot**: two arms, one repo (ansible), a standalone Python port of the
retrieval logic that doesn't touch Postgres. That pilot shook out harness
bugs and produced `results.jsonl`.

This file describes what runs now, in `run_graph_experiment.py` /
`graph_memory.py` / `htn_agent.py`, producing `graph_experiment_joint.jsonl`.
It is a different, larger experiment built on top of the pilot's harness:
**it runs against the real backend** — the actual `backend/app/services/`
code this whole platform ships, live Postgres + pgvector, not a port of it —
and it adds a third arm to test a second, independent question.

---

## 1. The two questions this answers

Three arms, same instance, same snapshot, same tools, same step budget.
Only one thing changes at a time, giving two clean paired comparisons:

```
no_memory  vs  graph_memory   ->  does the knowledge graph help at all?
graph_memory  vs  htn_memory  ->  does DAG decomposition help, memory held fixed?
```

| arm | agent | sees retrieved memory? |
|---|---|---|
| `no_memory` | flat | no |
| `graph_memory` | flat | yes |
| `htn_memory` | HTN | yes |

A fourth arm (`htn_no_memory`) would complete the 2×2 but costs another
~8 minutes per instance, and at these resolution rates the experiment is
already power-limited — n matters more than the interaction term
(`run_graph_experiment.py`, `ARM_SPEC` comment).

---

## 2. What's actually different from the pilot

| | pilot (`README.md`) | this experiment |
|---|---|---|
| repos | ansible only | mixed (ansible, teleport, tutanota, element-web, protonmail, openlibrary, flipt, navidrome, trivy, ...) |
| arms | 2 (`no_memory` / `memory`) | 3 (`no_memory` / `graph_memory` / `htn_memory`) |
| retrieval | standalone port of the RRF logic, no DB | live call into `backend/app/services/retrieval.py`'s real `HybridRetriever`, against Postgres + pgvector |
| memory structure | flat top-5 list | the real knowledge graph: `task_nodes` / `knowledge_nodes` / `edges`, plus an HTN hierarchy tree for beam-search retrieval |
| agent | flat only | flat **and** HTN (DAG decomposition, personas, localized replanning) |
| symbol access | `read_file` only | + `list_symbols` / `read_symbol` via tree-sitter (`code_index.py`) |
| syntax checking | none | every edit re-parsed; advisory in the flat agent, a hard gate in the HTN agent |

Because this experiment calls the real backend modules directly
(`from app.config import settings`, `from app.services.retrieval import
HybridRetriever`), it is exposed to real backend regressions in a way the
pilot never was — see [§8](#8-operational-note-this-keeps-getting-reverted).

---

## 3. The knowledge graph and the holdout mechanism

`graph_memory.py` is the bridge between this experiment and the backend's
actual data model.

**Bi-temporal holdout, not deletion.** Before running an instance, its own
task node, knowledge node, and the edge between them are invalidated
(`t_invalid` set) rather than deleted. Every backend read path already
filters `t_invalid IS NULL`, so the instance disappears from retrieval
without touching the database's shape, and restoring it afterward is one
`UPDATE`. Running the holdout through the same truth-maintenance mechanism
the backend uses in production also exercises that mechanism: if
invalidation leaked anywhere, the held-out instance would retrieve itself
and the hit rate would be a giveaway 1.0.

**The hierarchy is rebuilt after every holdout.** Internal nodes in the HTN
tree route on the mean of their children's embeddings. A tree built while
the held-out leaf was still live has that leaf folded into its parent's
routing signal — small leakage (one child among up to twelve), but real, and
cheap (seconds) to remove by rebuilding.

**Two retrievers, reported separately, never blended:**
- `HybridRetriever` — RRF over vector + Postgres full-text search to pick
  entrypoints, then one hop along `OWNS`/`RESOLVED_AT` to the matched
  issue's code-location node. This is "similar problem → where that problem
  lived," and it's the piece a flat store can't do.
- `hierarchical_search` — beam descent through the HTN tree.

They answer different questions and can disagree, so both are recorded
(`rec["retrieval"]` vs `rec["htn"]`) rather than combined into one number
that couldn't be attributed to either mechanism.

**Two embedding columns.** `embedding` (issue text only) vs
`embedding_joint` (issue text + gold diff). Measured on this corpus, joint
embeddings beat issue-only on retrieval (p = 0.0066, n = 400) — see
`compare_embeddings_n400.json`. `--embedding-column` controls which one
`HybridRetriever` uses; `embedding_joint` only exists on `task_nodes`, so
that mode implicitly restricts search there (`graph_memory.retrieve`,
`retrieval.py`'s `HybridRetriever.__init__`).

**What memory renders, and what it deliberately never includes.** Titles,
problem statements, changed file paths, changed symbols — and optionally
truncated prior patches (`--include-patches`, capped by `--patch-chars`).
Never the *unbounded* patch text: a store handing over a complete working
fix for a near-duplicate issue would measure copy-paste, not transfer. That
risk is tracked directly — `score_copyability()` reports how much of an
agent's actual patch could have been assembled verbatim from retrieved
context, per instance.

---

## 4. The two agent architectures

Both share `RepoSandbox` and the same tool implementations
(`list_dir`, `search`, `read_file`, `list_symbols`, `read_symbol`,
`edit_file`, `create_file`, `delete_file`), so the harness genuinely cannot
tell which one ran — that's what makes flat-vs-HTN a controlled comparison,
not just two different codebases.

### Flat agent (`agent.py`)

One growing message list, same as the pilot. Its main cost is structural:
resending the entire history every call means token cost grows roughly
`O(steps²)` — measured on this corpus, one `gravitational/teleport` episode
hit 1,067,259 tokens over 40 steps because 22 file dumps rode along on every
later step.

### HTN agent (`htn_agent.py`: `HTNAgent` → `AugmentedHTNAgent` →
`ResearchHTNAgent`)

`run_graph_experiment.py` imports `AugmentedHTNAgent as HTNAgent` — that's
the class actually used.

- **DAG decomposition, not a list.** The planner breaks the issue into 2-4
  subgoals with explicit dependencies. Each subgoal runs in its OWN message
  list — context is `O(local steps)`, not `O(all steps)` — and a failed node
  blocks only its transitive dependents, not independent branches.
- **Localized replanning.** A failed subgoal gets ONE alternative approach
  from a fresh planner call grounded in the actual last tool result
  (`_replan_evidence`), not the whole transcript re-reasoning over itself.
- **Persona-scoped tools.** Each node is classified `locator` / `verifier` /
  `editor` from its goal text, and its tool access is restricted to match —
  a `verifier` node cannot call `edit_file`, so it can't quietly do the
  editor's job instead of checking it.
- **Speculative parallel execution.** Every simultaneously-ready node in the
  DAG runs concurrently (`ThreadPoolExecutor`), reserving step budget
  synchronously per node so a batch can never overspend the run's total.
- **SLA-aware depth gating.** Past 70% of an optional wall-clock/token
  budget, `decompose_subgoal` is withdrawn — the agent is forced into
  shallow, direct fixes instead of planning work it no longer has budget to
  execute.
- **Static pre/postcondition checks.** A subgoal naming a file that doesn't
  exist (and doesn't ask to create one) fails in 0 LLM calls. A subgoal
  cannot be marked done while a file it touched fails to parse
  (`code_index.syntax_errors`, all four corpus languages via tree-sitter) —
  a hard gate, not just a warning.

`ResearchHTNAgent` adds graph-backed method-library reuse (looks up a
previously-successful decomposition for a similar issue instead of planning
from scratch) but isn't wired into this runner's `ARM_SPEC` yet.

---

## 5. Symbol-level reads (`code_index.py`)

Tree-sitter, not an LSP daemon — deliberately. A persistent
pyright/gopls/tsserver would need a cold index per instance (every
SWE-bench Pro instance is a fresh checkout), commonly 30s-2min, which
conflicts with the per-run time budget. Tree-sitter is a pure parser: no
project-wide index, milliseconds per file, runs on the host.

Two things it buys:
- `list_symbols` / `read_symbol` — read one function instead of a whole
  file. Measured: ~170 tokens for a symbol read vs up to ~3,500 tokens for
  the chained `read_file` calls needed to reach the same code by paging.
  `STEPS_PER_SUBGOAL` (9) and the flat agent's step budget (28) were both
  widened this session specifically to bank that saving as *more attempts*,
  not just cheaper ones.
- `syntax_errors` — a cheap host-side re-parse after every edit. A patch
  that doesn't even parse is a guaranteed `FAIL_TO_PASS` failure, and
  previously that was only discoverable from the external grading
  container, after the episode had already ended.

What it does *not* do: resolve cross-file references. "Who calls this
function" still needs `search` by name, which over- and under-returns. That
gap is Pattern A below, and is explicitly not what this file covers fixing.

---

## 6. Grading and instance selection

`pro_harness.py` ports `scaleapi/SWE-bench_Pro-os`'s own entry-script
ordering and per-instance parsers. Resolved iff every `FAIL_TO_PASS` passes
and no `PASS_TO_PASS` breaks.

**Every instance is gold-gated first.** The reference patch runs before
either arm spends a token; if it doesn't resolve, the instance is excluded.
An unresolvable gold patch would otherwise drag every arm toward zero for a
reason unrelated to what's being measured.

**`select_instances`** additionally requires: a `run_script` exists, the
repo's gold patch was verified in the pilot, every gold file is
hand-editable (excludes `make generate`-style regenerated files, which no
agent can fix regardless of retrieval quality), and 1-4 gold files (bounds
grading time). Selection round-robins across repos so the result isn't a
statement about one repo's conventions.

---

## 7. Statistics

`summarise()` in `run_graph_experiment.py` computes, per arm pair, an exact
binomial McNemar test on discordant pairs (`mcnemar()`) — concordant pairs
(both arms solve it, or both fail it) carry no information about a
difference between arms, so only discordant pairs enter the test. Zero
discordant pairs means "no evidence," reported explicitly as distinct from
"no effect," not silently as p=1.0.

An instance only counts toward `n_usable` if **every** arm produced a valid
run on it (`valid` excludes provider-error truncations) — a row where one
arm was killed mid-episode can't contribute a paired comparison, and
dropping only that arm would compare different instance sets.

---

## 8. Diagnosed failure modes (this session)

A full pass across every completed row in
`{graph_experiment_joint,results,_speed}.jsonl` (48 unresolved arm-runs)
surfaced two distinct patterns:

### Pattern A — cross-file miss (not fixed, out of scope for the current fix)

When an agent DOES produce a patch, the largest failure category is editing
only some of the files a fix genuinely requires — the missed file is
reachable only by tracing a call from the file the agent did find into a
helper defined elsewhere, not visible from the issue text or name/regex
search alone (`internetarchive/openlibrary`, `flipt-io/flipt`,
`gravitational/teleport`, `navidrome/navidrome`, `tutao/tutanota`).

### Pattern B — HTN thrashes into zero output (fixed)

`htn_memory` was producing **zero patch in 50% of its failing runs**, vs 20%
for `no_memory` and 0% for `graph_memory`. Root-caused directly against real
NO_PATCH rows, not guessed from code reading alone:

- **`AugmentedHTNAgent.PERSONAS`** never listed `list_symbols`/`read_symbol`
  for ANY persona, so every HTN node — regardless of persona — silently lost
  the cheap symbol-read path described in §5 and was forced onto expensive
  `read_file`/`search` loops that exhausted the whole step budget before
  ever reaching `edit_file`. Confirmed live in `gravitational/teleport`,
  `tutao/tutanota`, `element-hq/element-web` NO_PATCH rows: editor-persona
  nodes, 18 tool calls, all `read_file`/`search`, zero symbol reads, zero
  edits.
- **`_persona()`'s keyword match** classified any goal containing "locate"
  as the edit-less `locator` persona, even when the same goal also
  instructed an edit — confirmed live in `instance_ansible__ansible`'s
  node 2: *"Locate the FQCN validation function ... and update the
  validation logic to ..."* got stripped of `edit_file` access entirely.

Both fixed in `htn_agent.py`: personas now share a `_READ_TOOLS` base
including the symbol tools, and `_persona()` checks the goal text after its
first `" and "` for a word-boundary edit verb before committing to
`locator`/`verifier`. Covered by `TestPersonaToolAccess` in
`backend/tests/test_htn_agent.py`, using the real goal strings above as
fixtures.

---

## 9. Operational note: this keeps getting reverted

Because this experiment imports the real backend directly
(`app.config.settings`, `app.services.retrieval.HybridRetriever`), it broke
twice this session when those two files' **uncommitted** fixes were
silently discarded back to the last commit (`git status` shows them as `M`
against HEAD, not ahead of it — the fixes were never committed). Symptom
both times: `TypeError: HybridRetriever.__init__() got an unexpected
keyword argument 'embedding_column'`.

Until these fixes are committed, anything that discards uncommitted
working-tree changes (an editor "discard changes" action, `git checkout --
<file>`, `git stash`/`reset --hard`) will silently reintroduce this
failure. If it recurs, `backend/app/services/retrieval.py` and
`backend/app/config.py` are the two files to check first.

---

## 10. Running it

Full ingest-to-grade runbook, all flags, and every failure mode hit this
session with its fix: [`RUNBOOK.md`](RUNBOOK.md). The quick version:

```bash
cd experiments/swebench_pro

# Full sweep, all three arms, default 20 instances:
py -3 run_graph_experiment.py --out graph_experiment_joint.jsonl

# Targeted re-check of specific instances / a single arm (cheap, fast):
py -3 run_graph_experiment.py \
  --arms htn_memory \
  --instance-ids <id1>,<id2>,... \
  --out graph_experiment_htn_fix_check.jsonl
```

Requires Docker running (for grading containers) and the `stealthlab-pg`
Postgres container up. Resumable — results append to JSONL and completed
instances are skipped, so an interrupt costs at most the instance in
flight.

Key flags: `--embedding-column {embedding,embedding_joint}`,
`--arms no_memory,graph_memory,htn_memory` (comma list, any subset),
`--max-steps` (default 28), `--top-k` (default 4), `--model` (default
`gemma-4-31B-it`).

Summarize any results file:
```bash
py -3 -c "from run_graph_experiment import summarise; import json
rows=[json.loads(l) for l in open('graph_experiment_joint.jsonl')]
print(json.dumps(summarise(rows), indent=2))"
```
