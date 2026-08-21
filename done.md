# Experiential Memory Substrate for AI Agents

**IITB–Groww INV.ENT — Track B (Grand Challenge Fellowship)**

> ⚠️ **One thing to fill before submitting:** §8, team details.
> Nothing below is estimated, projected, or rounded up. Where a result is unflattering it is
> stated as such, because a reviewer who finds it themselves will discount everything else.
> The τ-bench numbers in §4.1 are reported at the confidence their own authors assigned them —
> a smoke test, not a finding.

---

## 1. One paragraph

AI agents today have no memory that survives a session. Every task starts from zero: the same
repository is re-explored, the same mistakes are re-made, the same tokens are re-spent. The
industry's answer is to stuff more context into the prompt — and on τ-bench we watched a large
model with **the entire policy document in its context window** violate that policy on *every
attempt*, while a model 15× smaller, given the same rule as **graph structure**, got it right
half the time using 58% fewer tokens. Knowledge that is retrievable is not knowledge that is
applied. We are building the **substrate that makes agent experience durable**: raw execution
traces become episodes, episodes become evidence-backed claims, and repeated successful
behaviour becomes a *verified, reusable procedure* with provenance back to the traces that
justify it. The bet is not a better agent — it is the storage and verification layer any agent
can learn on top of. On real SWE-bench Pro instances the architecture already cuts token spend
by **74.4%**.

---

## 2. The problem, precisely

An agent that solves a task today knows nothing tomorrow. The industry's answer is
retrieval-augmented memory — dump prior context into the prompt. That fails three ways, and we
can name each:

1. **Events are not knowledge.** A log of "edited `auth/middleware.py`" is not the fact "this
   codebase validates auth changes with a specific test suite." Without an interpretation layer,
   memory is an ever-growing transcript.
2. **Similarity is not applicability.** Retrieval finds *analogous* prior work. It cannot say
   whether that work is *valid now* — after the dependency changed, on this branch, given this
   state. Reuse based on cosine distance alone reuses things that no longer apply.
3. **Nothing is ever retracted.** When a stored belief becomes false, systems built on flat
   vector stores have no mechanism to find and invalidate what depended on it.

Our thesis, stated so it can be falsified:

> **Events are not memories. Claims are not procedures. Procedures are not agents.
> Traces are evidence-bearing experiences from which claims and procedures can be learned.**

---

## 3. What has actually been built

**~22,500 lines of production Python**, 546 test functions across 41 test files, running on
FastAPI + PostgreSQL + pgvector.

| Layer | State | Where |
|---|---|---|
| Bi-temporal knowledge graph (nodes, polymorphic edges, provenance) | **shipped** | `backend/db/01_ontology.sql`, `backend/app/models/ontology.py` |
| Hybrid retrieval — pgvector + Postgres FTS fused by Reciprocal Rank Fusion, then bounded graph expansion | **shipped** | `backend/app/services/retrieval.py` |
| Hierarchical retrieval — bottom-up clustered tree, confidence-adaptive beam descent | **shipped** | `backend/app/services/hierarchy.py` |
| HTN planner / DAG executor with localized replanning and per-node telemetry | **shipped** | `experiments/swebench_pro/htn_agent.py` |
| Truth-maintenance primitive — claims with supersession that preserves history | **shipped** | `backend/app/services/claims.py` |
| Agent Store — submission, review state machine, sandboxed execution | **shipped** | `backend/db/07_agents.sql`, `backend/app/services/agent_review_*.py` |
| Symbol-level code access (tree-sitter), static call-graph reachability | **shipped** | `backend/app/services/code_index.py`, `call_graph.py` |
| MCP server exposing the graph to external agents | **shipped** | `backend/app/mcp_server/` |
| Trace ingestion → episode → observation → claim → procedure pipeline | **fully specified, not built** | `.scratch/memory-substrate/` (18/18 decisions closed) |

A deliberate architectural distinction, enforced in the schema rather than by convention:
**event ≠ observation ≠ episode ≠ claim ≠ state ≠ procedure ≠ execution**. Most systems collapse
these into one "memory" object; that collapse is what makes retraction and verification
impossible later.

---

## 4. The experiments, in order

### 4.1 τ-bench — the origin finding

*Source: `3Found/TAUBENCH_RESULTS.md`. Its own header reads: "5-minute smoke test. Numbers
below are NOT a reportable finding, they only confirm the harness runs end-to-end." We report it
at exactly that confidence — 2 of 104 retail tasks, 2 trials per arm. **This is directional
evidence that motivated the project, not a validated result.***

The rule under test is real τ-bench retail policy: a refund must go to the order's **original**
payment method or an existing gift card — *not* to a different card the customer has on file,
**even if they explicitly ask for it**.

Four arms, on the adversarial task (order `#W9571698`, where the customer explicitly requests a
card that is on their account but is not the original payment method):

| arm | model | refund-destination accuracy | tokens |
|---|---|---|---|
| large, no graph | gpt-oss-120b | **0%** | 1,938 |
| large, **entire policy wiki in context** | gpt-oss-120b | **0%** | 6,868 |
| small, no graph | llama-3.1-8b | **0%** | 1,552 |
| small **+ graph** | llama-3.1-8b (routing) | **50%** | 2,854 |

**The result that started this project:** a large model with *the entire policy document in its
context window* proposed the wrong refund destination on **every single attempt**. A model
roughly 15× smaller, with the same rule supplied as **graph structure** rather than as prose in
context, got it right half the time — using **58% fewer tokens** than the context-stuffed arm.

The failure mode is the interesting part: every failing arm proposed the *same* wrong answer
(`credit_card_1565124` — the card the customer asked for) against ground truth
(`gift_card_7250692`). The models were not confused; they were being **agreeable**, and having
the rule in context did not stop them.

That is the thesis in miniature: **knowledge that is retrievable is not knowledge that is
applied.** Structure beats context-stuffing. Everything since has been an attempt to test that
claim at a scale where it could actually fail.

**Honest limits, stated by the run's own authors:** n = 2 tasks, 2 trials, budget-limited
sampling. `pass^k` conflates two independent decisions (item selection and refund destination),
which is why the two are reported separately. Nothing here is significant.

τ³-bench (the successor) was then profiled as the rigorous follow-up (`YC_EVIDENCE_PLAN.md`):
its `banking_knowledge` domain has 97 tasks scored by **deterministic DB-state hashing — no
judge LLM**, and every task ships `required_documents` (mean 9.78 doc ids) that the evaluator
never uses — so retrieval recall can be scored offline against real labels, for free. That makes
it the cheapest rigorous second domain available to us, and it is milestone M3.

### 4.2 Pre-benchmark demos — and why we stopped trusting them

Earlier work (`3Found/RESULTS.md`) showed the same pattern on self-constructed scenarios: a 7B
model **with the graph** produced the correct `PAUSE QUEUE order_processor` before
`ALTER TABLE orders`, while an 8B model without it emitted a naked `ALTER TABLE` — valid SQL, no
error, silent production corruption. A third arm (small, no graph) ruled out "it's just model
size."

We list this as *context, not evidence*, for the reason our own brief gave at the time: *"every
demo is self-constructed… they prove the mechanism works, they prove nothing about demand."*
When you write both the rule and the violation, you have built a demonstration, not a test. That
recognition is what pushed everything afterward onto external benchmarks whose tasks and graders
we did not author.

### 4.3 SWE-bench Pro pilot — dataset selection done properly

All 11 SWE-bench Pro repos were profiled before committing
(`experiments/swebench_pro/profile_dataset.py` → `profile.json`): instance count, language,
Docker image size, network requirement at test time, median pass-to-pass test count.
`ansible/ansible` won on every axis (96 instances, 0.54 GB image, plain pytest, no network).

We chose Pro over SWE-bench Atlas for a concrete reason: Pro ships a per-instance
`run_script.sh` and `parser.py` — **1000 of them, and they genuinely differ** (8 distinct
parsers across 40 ansible instances alone). Atlas ships only build commands, with no published
evidence that gold patches even resolve. On one machine, Pro ran that week; Atlas did not.

### 4.4 The three-arm graph-memory experiment — the core result

Same instance, same snapshot, same tools, same step budget. **One variable changes at a time**,
giving two clean paired comparisons:

```
no_memory    vs  graph_memory  →  does the knowledge graph help at all?
graph_memory vs  htn_memory    →  does DAG decomposition help, memory held fixed?
```

**Result — efficiency is real and large.** Across the 15 instances where all three arms are
valid:

| | `no_memory` | `htn_memory` |
|---|---|---|
| tokens | 2,579,395 | **660,938** |
| tool calls | 330 | **219** |

**A 74.4% reduction in token spend, with 34% fewer tool calls.**

**Result — accuracy is currently a liability, and we say so.** On the same paired set, flat
resolved 2/15 and HTN resolved **0/15**. Across a wider 28-instance pool both reach 4 resolved,
but that set is not paired, so the rigorous number is the unflattering one. *We report the
paired number.*

**Result — a generalizable failure class exists.** This is what makes a learning experiment
possible at all:

| failure class | distinct instances | repos |
|---|---|---|
| `RIGHT_FILE_wrong_fix` | **12** | **8** |
| `NO_EDIT_at_all` | 11 | 8 |
| `LOCALIZATION_miss` | 9–10 | 4 |

12 instances across **8 different repositories** means a procedure learned here must
*generalize*, not memorize one codebase. The dominant failure is knowing *where* but not *how* —
which is precisely the capability a verified procedure should transfer.

**Result — retrieval representation matters, with significance.** Joint embeddings (issue text +
gold diff) beat issue-only embeddings on retrieval: **p = 0.0066, n = 400**
(`compare_embeddings_n400.json`).

### 4.5 Methodological rigour — the part we would want examined

- **Bi-temporal holdout, not deletion.** Before running an instance, its own nodes are
  *invalidated* (`t_invalid` set), not deleted — exercising the same truth-maintenance mechanism
  production uses. If invalidation leaked anywhere, the held-out instance would retrieve itself
  and hit rate would be a giveaway 1.0.
- **The hierarchy is rebuilt after every holdout**, because internal tree nodes route on the mean
  of their children's embeddings — a tree built while the held-out leaf was live has that leaf
  folded into its parent's routing signal. Small leakage, real, cheap to remove.
- **Copy-paste is measured, not assumed away.** `score_copyability()` reports how much of the
  agent's patch could have been assembled verbatim from retrieved context. On the most recent
  run: **mean max copyable fraction 0.063** — the agent is not copying answers.
- **Two retrievers reported separately, never blended**, so a number can always be attributed to
  a mechanism.
- **McNemar's test for paired binary outcomes**, with zero-discordant-pair cases reported
  honestly as "no effect" rather than silently as p = 1.0.

### 4.6 Architecture decision map — this month's work

Before building the memory substrate we ran a structured decision process over the existing
codebase: **18 architectural decisions, each grounded in the actual code with `file:line`
citations, all closed** (`.scratch/memory-substrate/`). Four literature reviews backed it.

Two findings changed the design:

- **The utility problem** (Minton, macro-operator learning): stored procedures can make a system
  *net slower*, because match cost grows faster than the planning effort saved. A *correct*
  procedure can be worth deleting. **No LLM-agent work has confronted this** — the 2026
  skill-library survey does not mention it. We build the retirement criterion in from day one.
- **LLM confidence is not storable.** Raw token probabilities are uncalibrated; verbalized
  confidence is *uncorrelated with accuracy*. So we store **no confidence field** — confidence is
  derived from evidence, never asserted.

The process also found four real defects in our own code, including a schema/code drift where a
column read and written by production code **is created by no migration file**.

---

## 5. Honest status

**Proven:** large token reduction on real instances; a repeated, cross-repo failure class;
statistically significant retrieval improvement; a leak-free experimental harness.

**Not yet proven — and the fellowship is exactly what closes it:**

- Accuracy has not improved. It has regressed on the paired set.
- **`method_library.py` is a store, not a learning loop.** `persist_plan` writes
  `attempts: 1, successes: 1` *hardcoded*; nothing updates them. The Beta-Bernoulli scorer at
  `htn_agent.py:1487` raises `NotImplementedError`. `_bump_reuse_count` fires at *retrieval*
  time — it counts "was handed out," not "worked."
- **No provenance join key.** Nothing records that procedure X seeded run Y.
- **The truth-maintenance system is write-only.** `claims.py` writes `truth_state`; no code reads
  it, so an `OUT` claim is still served by retrieval.

We know these are gaps because we audited our own source to find them, and wrote them down
before anyone asked.

---

## 6. What the fellowship funds — milestones

Track B is milestone-based over 12–20 months; this maps to its four tranches.

| # | Milestone | Deliverable | Falsifiable success test |
|---|---|---|---|
| **M1** | Close the loop | Provenance join key, real outcome feedback, TMS made readable | A procedure's success statistics change *because of an execution outcome*, traceable end-to-end |
| **M2** | Verified reuse | 6-train / 6-test split on `RIGHT_FILE_wrong_fix`, stratified so **no repo appears in both** | The targeted capability moves on 6 held-out instances, reported against a no-procedure counterfactual. **n=6 is directional, not significant — we will say so** |
| **M3** | Second domain | τ³-bench `banking_knowledge` ablation: our retriever vs. BM25 baseline | Task reward (deterministic DB-hash) *and* retrieval recall vs. `required_documents` labels |
| **M4** | Substrate + transfer | Full trace→episode→claim→procedure pipeline; cross-domain motif test | Does a procedure learned in domain A measurably help in domain B? |

Every milestone has a number attached that can come out negative. That is deliberate.

---

## 7. Why this fits INV.ENT Track B

- **Deep-tech, not an application wrapper.** The contribution is a storage and verification
  substrate with formal properties (bi-temporal validity, provenance, retraction-readiness), not
  a prompt or a UI.
- **Research-grade rigour already demonstrated** — paired experiments, leakage control,
  significance testing, and published negative results, before any funding.
- **Milestone-shaped by nature.** Each stage produces a measurable claim that can fail
  independently: substrate → extraction → reuse → transfer.
- **Infrastructure timing.** Agent frameworks are proliferating; none has solved durable,
  verifiable agent memory. The layer beneath them is unclaimed.
- **IITB-native.** Built by IITB students on IITB infrastructure, and the 15,000 sq ft INV.ENT
  space plus faculty mentorship maps directly onto what compute-and-review-heavy experimental
  work needs.

---

## 8. Team

> **To fill.** Track B allows 1–4 core members. For each: name, roll number, department, year,
> and the specific component they own. A named faculty mentor is **mandatory** for Track B, plus
> up to 2 alumni advisors.

---

## 9. Verify any claim in this document

| Claim | Where |
|---|---|
| τ-bench arms, refund-destination accuracy, adversarial case | `3Found/TAUBENCH_RESULTS.md` (parent dir) |
| τ-bench run design and its stated limits | `3Found/BRIEF_TAUBENCH.md` (parent dir) |
| Pre-benchmark demos (`PAUSE QUEUE`) | `3Found/RESULTS.md` (parent dir) |
| 74.4% token reduction, failure-class table | `YC_EVIDENCE_PLAN.md` |
| Experimental design, holdout, statistics | `experiments/swebench_pro/GRAPH_EXPERIMENT.md` |
| Dataset selection rationale | `experiments/swebench_pro/README.md` |
| Raw per-run results | `experiments/swebench_pro/*_summary.json`, `*.jsonl` |
| p = 0.0066, n = 400 | `compare_embeddings_n400.json` |
| Codebase inventory with citations | `.scratch/memory-substrate/inventory.md` |
| 18 architecture decisions | `.scratch/memory-substrate/map.md` |
| Literature grounding | `.scratch/memory-substrate/research/` |
| Test suite | `backend/tests/` — 41 files, 546 test functions |

---

*Every figure here is traceable to a named file, and each is reported at the confidence its
source assigns it — the τ-bench numbers are labelled a smoke test because that is what their own
authors called them. The arc across §4 is that each experiment was more rigorous and less
flattering than the one before it. That is the intended direction.*
