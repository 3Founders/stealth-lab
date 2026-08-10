# Knowledge Updation Experiment — Plan

## Claim (pre-registered, single sentence)

> A debate-adjudicated, non-destructively-versioned knowledge graph, revised by contradicting
> evidence, corrects false precedents and stops them propagating to later tasks — more than
> either no memory or memory that updates without adjudication.

Not "memory helps" (already shown). Not "we beat the leaderboard" (unwinnable: SWE-bench
Verified is at 96%, saturated). The differentiator is *what happens when stored knowledge
turns out to be wrong* — no surveyed system (Mem0, A-Mem, MemCoder, TOKI, NeuSymMS, TGMS)
closes that loop against real execution.

## Corpus decision

**Primary: plain SWE-bench, chronological per-repo streams.** 19,008-instance train split, real
history, no vision pipeline. Ordering by `created_at` gives forward/backward-transfer structure
without inheriting SWE-Bench-CL's harness (its own preliminary runs stayed under 8.5% pass rate,
and it has zero citations — no brand to borrow, so run the idea under our own name on a
benchmark people recognize).

**Optional overlay: SWE-bench Multimodal** (612 rows, 102 dev / 510 test, 5 JS repos,
`image_assets` = githubusercontent URLs). Only worth the vision pipeline if the UI-specific claim
is wanted. Adds one genuinely better contradiction source — see below. Check link-rot on
`image_assets` before assuming it's usable.

## Conditions (2×2, not system-vs-baseline)

| | Frozen after ingestion | Revises on new evidence |
|---|---|---|
| **Naive** | — (skip) | **C2** — append/overwrite, no gate, no versioning |
| **Adjudicated** | **C1** — debate-gated once, then frozen | **C3** — the system |

Plus **C0**: no memory.

- **C3 vs C2** — headline. Same retriever, same top-k. Only adjudication + non-destructiveness differ.
- **C3 vs C1** — marginal value of updating at all, write-quality held constant.
- **C2 vs C0** — can naive self-updating memory be net *negative*? Report either way.

Held identical across all four: base model, scaffold, retrieval budget (top-k), decoding params,
tool-call budget, pinned model snapshots. A condition may only win on what it does with
contradictory evidence.

## Revision triggers

1. `FAIL_TO_PASS` failure on a task whose patch cited a retrieved precedent → debate that node.
2. (Multimodal overlay only) `version` mismatch between retrieved precedent and current task.
   Chart.js v2→v3 config breaks are real; a fix correct under v2 is active misinformation under v3.
   Sharper than (1) because it fires *before* an attempt is wasted, not after.

## Sub-experiments

**(a) Natural stream** — all four conditions over chronological per-repo sequences.
Metrics: accuracy, Forward Transfer, Backward Transfer, Forgetting, tool/token efficiency.

**(b) Fault injection** — ~15–20 pre-selected issues. Insert a plausible-but-wrong precedent
before issue *B*; check (i) does it get cited, (ii) does C3 invalidate it post-outcome while C2
leaves it standing, (iii) does the error propagate to a later related issue *C*. Injection set
frozen before any condition runs.

## Statistics

- **Stream-level**: unit of independence is the **repo-stream**, not the issue — issues within a
  repo share a memory trajectory; treating them as independent is pseudo-replication. Aggregate
  per repo, paired Wilcoxon signed-rank across repos, Benjamini-Hochberg across the metric family.
  Reuse `eval/statistics.py`'s FDR layer but **not** its Welch t-test — Welch assumes independent
  groups, wrong test here.
- **Fault injection**: McNemar (paired binary). Needs far fewer samples to detect a real effect.
- **Primary metrics, fixed now**: ΔBWT (C3 vs C2) and the McNemar result. Accuracy/FT/efficiency
  are secondary and do not decide success if they disagree.
- **Falsification**: C3 ≈ C2 on ΔBWT *and* no significant McNemar difference ⇒ claim is false,
  adjudication adds nothing over generic updating memory. Report that.

## Ingestion mapping (per instance)

- `knowledge_node`: name = short fix summary, description = `problem_statement` + change summary,
  `properties = {repo, instance_id, base_commit, version, files_touched, FAIL_TO_PASS, PASS_TO_PASS}`.
- Graph **indexes, doesn't duplicate** — full diff text stays out; fetched on demand via
  `base_commit`/`pr_url` for the handful of candidates that survive retrieval.
- `task_node` (optional): inferred procedure pattern. Flag as reconstruction, not ground truth —
  SWE-bench gives issue and diff, never the reasoning path.
- (Multimodal only) vision model captions each asset once at ingestion; caption is embedded through
  the existing text embedder. Same pattern as hierarchy rollup summaries — no new embedder.

## Execution harness — the actual blocker

`sandbox.py` has verified isolation (`unshare --net` confirmed blocking, RLIMIT_CPU/AS confirmed
killing) but runs one Python `run(input_data)` function against a closed `SKILL_REGISTRY`. Wrong
shape for this. Needed:

1. **Split network from execution.** Pre-fetch stage (network on, cached per `(repo, base_commit)`)
   does clone/checkout. Sandboxed stage (patch-apply + test-run) gets a materialized checkout and
   needs zero network — preserves the existing invariant rather than punching through it.
2. **Reuse SWE-bench's own per-instance Docker images.** `unshare` gives no dependency isolation
   across repos with different runtimes; Docker does. New deps: docker SDK, gitpython.
3. **Third `agent_execution_mode`** (`sandboxed_repo`) in `07_agents.sql` — don't overload
   `skill_ref`, whose meaning for `local_skill` is structurally different.
4. **Resource envelope scaled up** (real suites run minutes), still hard-capped, still fail-closed.
5. **Output is existing `ExecutionResult`** — `FAIL_TO_PASS`/`PASS_TO_PASS` becomes the check
   inside `outcome`, not a new type. Layer 2 already reads this shape.

## Build order

0. **Pilot** — 1 repo, 4 conditions, ~20 issues. Shakes out harness bugs, rough effect size. Not a result.
1. Execution skill (repo checkout + patch-apply + test-run on the Docker path).
2. Ingestion + full paired run across repos with long-enough streams.
3. Multimodal overlay, only if the UI claim is wanted.

## Housekeeping (do before anything runs)

`backend/.gitignore` was deleted in `10604cf` and `__pycache__/*.pyc` got committed as a result.
`.env` is untracked (verified) but holds a live Supabase password plus Voyage and General Compute
keys — one `git add -A` from leaking. Restore `.gitignore`, `git rm --cached` the `.pyc` files.

Supabase: DSN is Session-mode pooler (port 5432) — correct, since `db/session.py` needs prepared
statements for its JSONB codec. `vector` + `pgcrypto` are both enableable. **Ask before first
connect / migration run.**
