# YC evidence plan: make "verified procedures" real, measured per-capability

## Context

YC in **under 2 weeks**. Scoped to that, with cuts stated explicitly rather
than quietly dropped.

### What is actually true today (measured, not claimed)

- **Efficiency is real and large.** On the 15 instances where all three arms are
  valid: `no_memory` 2,579,395 tok vs `htn_memory` 660,938 tok — **74.4%
  reduction**, 219 vs 330 tool calls.
- **Accuracy is a liability.** Same paired set: flat 2/15 resolved, htn **0/15**.
  (Across the wider 28-instance pool both reach 4 resolved, but that set is not
  paired, so the rigorous number is the unflattering one.)
- **A repeated, generalizable failure class exists** — the finding that makes a
  learning demo possible at all:

  | failure class | distinct instances | repos |
  |---|---|---|
  | `RIGHT_FILE_wrong_fix` | **12** | **8** (max 2/repo) |
  | `NO_EDIT_at_all` | 11 | 8 |
  | `LOCALIZATION_miss` | 9-10 | 4 |

  8 repos across 12 instances means a procedure learned here must *generalize*,
  not memorize one codebase — that is what makes a 6-train/6-test split sound.
  Localization is no longer the top gap (htn 4 misses vs flat 6 — the path fixes
  worked). The dominant failure is knowing *where* but not *how*.

### What is NOT true today — the gap between pitch and code

Verified by reading source, not assumed:

- **`method_library.py` is a store, not a learning loop.** `persist_plan` writes
  `success_criteria = {"attempts": 1, "successes": 1, ...}` **hardcoded**, and
  nothing ever updates them. `htn_agent.py:1487`'s Beta-Bernoulli `_method_score`
  raises `NotImplementedError`. `_bump_reuse_count` fires at **retrieval** time —
  it counts "was handed out", not "worked".
- **No provenance join key.** Nothing records that procedure X seeded run Y;
  `times_reused` is a bare scalar. `run.htn["seeded_from_library"]` is an
  in-memory bool, "not yet wired into run_graph_experiment.py".
- **No outcome feedback, no counterfactual.** `failure_capture.py` keys failures
  by `instance_id` with **no procedure reference**.
- **The TMS is write-only.** `claims.py` writes `truth_state`; **no code reads
  it** — an `OUT` claim is still served by retrieval.
- **No backtest harness for procedures.** `app/eval/layer2.py` is real but
  validates *debate change-sets against `traces`*, not procedures against
  historical instances. Its Tiers 1-2 are `NOT IMPLEMENTED`.

**Intended outcome:** three claims that survive a technical partner probing them
— efficiency (already true), per-capability measurement (data exists, needs
aggregating), and a *genuinely verified* procedure loop (the differentiator,
currently a stub).

---

## τ³-bench: integration is cheap, so it stays in

An earlier draft cut this as "1-2 weeks". Reading the actual clone
(`experiments/tau3_bench/_tau2_bench_src`, branch `main`, pkg `tau2` v1.0.1)
shows it is closer to **~2 days**, and it hands us something valuable free:

- **Custom agent = one factory call.** `registry.register_agent_factory(factory,
  "name")` then drive from Python (`examples/agents/minimal_text_agent.py`).
  No entry points. Factories must accept `**kwargs` — `runner/build.py:100`
  passes `llm, llm_args, task, audio_native_config, audio_taps_dir` to all of them.
- **Custom retrieval tool = one mixin.** A class with `metaclass=ToolKitType`
  and one `@is_tool(ToolType.READ)` method, subclassing `KnowledgeTools`
  (`domains/banking_knowledge/retrieval_mixins.py` is the model). `Environment`
  assembly is ~25 lines.
- **Scoring is deterministic.** banking_knowledge's 97 tasks are 88 × `["DB"]`
  + 9 × `["ACTION"]` — DB-state hash matching, **no judge LLM**. Cheap, fast,
  reproducible backtesting. `pass^k` is implemented at
  `metrics/agent_metrics.py:113`.
- **`required_documents` is free ground truth.** All 97 tasks carry it (mean
  9.78 doc ids) and it is **not used by the evaluator** — only substituted into
  a prompt. So we can score retrieval recall offline against real labels. That
  is precisely the per-capability measurement for the knowledge domain.

Three gotchas that must be respected:
- **Use `tasks/task_*.json`, not `tasks.json`** — 13 tasks differ; `tasks.json`
  is stale, and `get_environment` loads the directory. (Our existing
  `ingest_banking_knowledge.py` docstring points at the stale file.)
- **Do not baseline against the default `alltools` variant.** It spins up a
  sandboxed shell that can `cat`/`grep` the raw documents, so it can bypass
  retrieval entirely — not an apples-to-apples retrieval comparison. Baseline
  against a pure-retrieval variant (`bm25` or `qwen_embeddings`).
- **No train/test split exists for banking_knowledge** (unlike airline/retail/
  telecom, which have `split_tasks.json`). Maintain our own split externally via
  `--task-ids` / `get_tasks(..., task_ids=[...])`.
- Domain time is frozen at `KNOWLEDGE_FIXED_DATE = 2025-11-14` — good for
  reproducibility, worth stating in any writeup.

## Step 1 (days 1-2) — Measure the fixes already written

The localization pre-pass and typed dependency edges are **implemented, green at
601 tests, and have never been run against the benchmark**. The 0/15 is stale
evidence. Cheapest possible accuracy movement, and building a learning demo on
an unmeasured base would misattribute its gains.

```bash
cd experiments/swebench_pro
py -3 run_graph_experiment.py --arms no_memory,graph_memory,htn_memory -n 20 \
  --max-steps 200 --steps-per-subgoal 20 --model deepseek-v3.2 \
  --out yc_baseline.jsonl
```

One sweep at a time (concurrent sweeps delete each other's Docker images —
`finally: remove_image` at `run_graph_experiment.py:321`). Gate: `n_usable` near
20, no `tok=0` arms — the `pull_image` and `is_transient` retry fixes should now
absorb the blips that voided earlier runs. **This is also the baseline for Steps
3-4; do not re-run it later.**

## Step 2 (days 3-4) — Make the procedure loop real (the honest minimum)

Three small changes that convert the stub into a mechanism. Without these,
"verified procedure" is not a defensible phrase.

1. **Provenance join key** — in `backend/app/services/method_library.py`, record
   which procedure seeded which run: an edge from the procedure's `task_nodes`
   row to the instance's, `custom_edge_type='SEEDED_BY'`, carrying
   `properties.instance_id` — exactly the shape `failure_capture.py` already
   uses. Thread `seeded_from_library` + the procedure id into
   `run_graph_experiment.py`'s per-arm record, where it is currently dropped.
2. **Outcome feedback** — after a run, update `success_criteria.attempts`/
   `successes` from the real result (`subgoals_done > 0 and subgoals_failed == 0`,
   the condition existing callers already use). Move `_bump_reuse_count` off the
   retrieval path so "handed out" and "worked" stop being the same number.
3. **Make the TMS readable** — filter retrieval to exclude
   `properties.truth_state = 'OUT'` in `graph_memory.retrieve()` /
   `HybridRetriever`. This is what makes the TMS load-bearing rather than
   decorative, and Step 5 depends on it.

Unit tests alongside, in the established `FakeDB` style
(`backend/tests/test_claims.py`, `test_failure_capture.py`).

## Step 3 (days 5-7) — The causal experiment: does a verified procedure help?

The core claim, and the one competitors can't restate as "we have memory too".
Target `RIGHT_FILE_wrong_fix` (12 instances, 8 repos).

1. **Split** 6 train / 6 test, **stratified so no repo appears in both**.
2. **Cluster + synthesize** from the 6 train failures (already captured by
   `failure_capture.py` with `reason`/`last_evidence`) into one candidate
   procedure — reuse `propose_synthesis`/`LoopOrchestrator`, don't write a new
   synthesizer.
3. **Backtest gate** — replay the candidate against *train* instances only, and
   promote only if it beats the recorded baseline. Reuse
   `backend/app/eval/statistics.py` (`welch_comparison`, `benjamini_hochberg`,
   `required_sample_size`) — real, implemented, exactly the right tool. **This
   gate is what the word "verified" is doing.**
4. **Promote** via `claims.py`: passing procedure becomes a claim with
   `truth_state='IN'`; what it replaces gets `relate_claims(..., 'SUPERSEDES')`
   and flips to `OUT` (Step 2.3 means retrieval now honors that).
5. **Measure on the 6 held-out instances only**, reporting the delta on the
   *targeted* capability, not aggregate resolve rate.

**Non-negotiable framing:** n=6 held-out is directional, not significant. Say
"the loop runs end-to-end and moved the targeted capability on held-out cases" —
never "statistically validated". Report the counterfactual (same instances, no
promoted procedure) alongside.

## Step 4 (days 8-9) — Node-level capability scorecard

No new instrumentation; every field is already logged. Aggregate existing
per-run records into the four capabilities the failure analysis produced:

| capability | measured from (already recorded) |
|---|---|
| **LOCATE** | `files_edited_correct` ∩ `gold_files`, `retrieval.file_recall` |
| **FIX** | `graded.f2p_passed` / `f2p_missing` given correct localization |
| **NOT-BREAK** | `graded.p2p_broke` |
| **COMPLETE** | `subgoals_done`/`subgoals_failed`, `no_patch`, `discarded_patch_bytes` |

A small script over the result JSONL emitting a per-arm, per-capability table.
This is the "granular capability check", and it is what makes Step 3's claim
legible ("we targeted FIX and FIX moved") rather than vanishing into an
aggregate pass rate. On the τ³ side the same script gains a **RETRIEVE** column
scored against `required_documents`.

## Step 5 (days 9-10) — τ³-bench banking_knowledge ablation

Second domain, real benchmark, deterministic scoring.

1. Verify the corpus is in the DB: `SELECT count(*) FROM knowledge_nodes WHERE
   node_type='policy_document'`. If zero, run
   `backend/scripts/ingest_banking_knowledge.py experiments/tau3_bench/_tau2_bench_src`.
   **Re-point it at `tasks/`, not the stale `tasks.json`**, and ingest
   `required_documents` while there (the deferred scope its own docstring flags).
2. Write `experiments/tau3_bench/` : a `KnowledgeTools` subclass whose
   `@is_tool` search queries our graph via `HybridRetriever` (truth_state-filtered,
   per Step 2.3), plus a driver registering it and running a task list.
3. **Ablation:** memory-on (our retriever) vs memory-off (`bm25` or
   `qwen_embeddings` — *not* `alltools`), same tasks, same agent LLM.
4. Report **both**: task reward (DB-hash, deterministic) *and* retrieval recall
   against `required_documents`. The second is the differentiated number and
   costs nothing extra.

## If time runs short — cut in this order

Steps 1, 4, 5 are cheap and high-value; Steps 2-3 are the differentiator but the
most work. **Cut order: 3 → 2 → 5 → 4 → 1.** Step 1 is never cut; without it
every other number is uninterpretable. If Step 3 falls, the pitch is
"efficiency + per-capability measurement across two benchmarks, learning loop
demoed qualitatively" — still honest, still differentiated.

## Verification

- `cd backend && python -m pytest tests -q` — **601 passing** now; must stay
  green through Steps 2-3.
- Step 1: `n_usable` ≈ 20, no `tok=0` arms.
- Step 3: the promoted procedure must be traceable end-to-end — procedure id →
  seeded runs → outcome — via the Step 2.1 join key. If that query can't be
  written, Step 2 isn't done.
- Step 4: the scorecard must reproduce the 74.4% efficiency number from the same
  JSONL, as a self-check that it reads fields correctly.
- Step 5: a τ³ run completes and `required_documents` recall is computable for
  both arms.

## Files

**Modify:** `backend/app/services/method_library.py` (provenance + outcome
feedback); `experiments/swebench_pro/graph_memory.py` +
`backend/app/services/retrieval.py` (truth_state filter);
`experiments/swebench_pro/run_graph_experiment.py` (persist procedure id);
`backend/scripts/ingest_banking_knowledge.py` (use `tasks/`, add
`required_documents`).
**Create:** backtest/promotion script and capability-scorecard script under
`experiments/swebench_pro/`; `experiments/tau3_bench/` adapter + driver; tests in
`backend/tests/`.
**Reuse, do not rewrite:** `app/eval/statistics.py` (Welch + BH), `claims.py`,
`failure_capture.py`, `propose_synthesis`/`LoopOrchestrator`, `HybridRetriever`,
the MCP server, τ³'s own `registry`/`ToolKitType`.
**Not touched:** `pro_harness.py`, `db/01_ontology.sql` (every change fits
existing columns), τ³-bench upstream source (we register, never fork).

## Risks

- **Biggest risk is overclaiming the loop's autonomy.** After Step 3 it is a
  *demonstrated* loop on one failure class with a human in the synthesis step —
  not a self-improving agent. Describe it that way.
- Step 1 may show the accuracy fixes didn't help. Real possible outcome; better
  known on day 2 than in the interview. It shifts the pitch to efficiency-led.
- n=6 held-out and n≈20 sweeps cannot carry statistical claims. Every number
  needs an interval or an explicit "directional".
- **Disk: 27GB free**, each SWE-bench image is 0.5-5GB. The 50GB target needs
  the admin-elevated `diskpart compact vdisk` on `docker_data.vhdx` (~23GB
  recoverable). Step 1's sweep is the most likely thing to hit
  `No space left on device` without it.
- τ³ adds a second Python env (3.12+, `uv`) and its own model spend — budget
  for it separately from the SWE-bench sweeps.
