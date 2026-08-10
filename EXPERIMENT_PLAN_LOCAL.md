# Experiment Plan — Local Execution Design

Runnable version of `EXPERIMENT_PLAN_FINAL.md`, scoped to what is verifiably present on this
machine. Everything below was checked against the actual filesystem, not assumed.
The original plan is left intact; where this document contradicts it, the contradiction is
stated explicitly rather than silently corrected.

---

## 0. What was verified on this machine

| Thing | Status | Evidence |
|---|---|---|
| AFTER dataset | **present** | HF cache `datasets--DavydenkoGr--AFTER`, 613 files, 22 skills, 129 tasks |
| AFTER gold labels | **present** | every `task.toml` has `skills = [...]` |
| SWE-bench Pro | present | HF cache `datasets--ScaleAI--SWE-bench_Pro` |
| tau-bench | present, **wrong domains** | `vendor/tau-bench/` has `airline`, `retail` only |
| τ³-bench `banking_knowledge` | **absent** | not in vendored tau-bench |
| EvoMemBench | **absent** | not on disk, not in HF cache |
| CL-Bench | **absent** | not on disk, not in HF cache |
| AFTER validators (`test_outputs.py`) | **do not exist** | `0` files matching `test*.py` in the entire dataset |
| Local PostgreSQL 17 | running, port 5432 | service `postgresql-x64-17` |
| **pgvector on local PG17** | **NOT INSTALLED** | no `vector.control` in `share\extension\` |
| Docker | installed, **daemon stopped** | `29.0.1`; engine pipe not found |
| Ollama | running, port 11434 | `llama3.1:8b`, `qwen2.5-coder:7b` |
| Ollama embedding model | **not pulled** | `mxbai-embed-large` configured but absent |
| Voyage API key | set | `.env` |
| General Compute | configured | panel models + judge `gemma-4-31B-it` |
| Anthropic / OpenAI / Google / Fireworks keys | **empty** | `.env` |
| `DATABASE_URL` | **points at Supabase** | `aws-1-ap-south-1.pooler.supabase.com:5432` |
| `resolve_subtask_reuse` / `batch_hierarchical_search` | **exist** | landed in `fa0b237`, `03b310b` |
| `preconditions` / `postconditions` | **do not exist anywhere** | zero matches across `backend/` |

---

## 1. Five blockers, and how each is resolved

### B1 — pgvector is not installed locally *(hard blocker)*

`db/01_ontology.sql` line 4 is `CREATE EXTENSION IF NOT EXISTS vector;`. Without it the schema
does not apply and nothing downstream runs. The existing PG17 install has 61 extensions; `vector`
is not one of them, and there is no official prebuilt pgvector for Windows.

**Resolution: run the DB in Docker**, not in the native PG17 service. `pgvector/pgvector:pg17`
ships the extension precompiled. Use port **5433** so the existing PG17 on 5432 is untouched.
Building pgvector from source with MSVC is the alternative and is rejected — it is exactly the
kind of step that fails halfway.

### B2 — `DATABASE_URL` points at Supabase

Every script reads `settings.database_url`. Left as-is, the experiment writes 22 skill nodes,
a hierarchy, and synthetic traces into the shared cloud DB.

**Resolution:** repoint `.env` at the container before anything runs (§2, step 4). Keep the
Supabase URL commented in-file so it is recoverable.

### B3 — AFTER ships no graders *(invalidates a premise of the original plan)*

`EXPERIMENT_PLAN_FINAL.md` line 12 says these benchmarks "ship their own execution/grading
harnesses ... which solves the 'no execution sandbox exists' blocker," and Experiment 4 specifies
"pass/fail via AFTER's `test_outputs.py`". **There are zero `test*.py` files in the AFTER
dataset.** AFTER provides `instruction.md`, `task.toml`, and `source_artifacts/` — inputs, not
verdicts. The execution-sandbox blocker is *not* solved by this substrate.

**Resolution:** split the experiments by what can be graded without a sandbox.
Retrieval experiments grade against `task.toml:skills` — objective, no execution needed, and
this is where the real result lives. Generation experiments (Exp 4) lose objective pass/fail and
are demoted accordingly (§6).

### B4 — Missing substrates for Experiments 2 and 3

EvoMemBench (all CROSSEP/INEP splits) and CL-Bench are absent, and the vendored tau-bench has no
`banking_knowledge`. Experiment 2's primary and Experiment 3's primary substrates do not exist here.

**Resolution:** Experiment 3 is redesigned as a self-contained supersession case study on the real
debate machinery — it never needed a benchmark, only a contradiction and a downstream query (§5).
Experiment 2's Hypothesis A is **cut**; its Hypothesis B is partially salvageable but tests a
mechanism that does not exist — see B5.

### B5 — There is no structural matching in this backend *(invalidates the plan's thesis sentence)*

`EXPERIMENT_PLAN_FINAL.md` line 5 defines the claim as: *"'reusable' means a structurally-matched,
precondition/postcondition-typed procedure, not a name or embedding match."* The strings
`precondition` and `postcondition` appear **nowhere in `backend/`**. `TaskSpec` carries
`io_schema` and `success_criteria` (both JSONB), and nothing reads either during retrieval.

All matching in this system is cosine similarity plus Jaccard lexical fallback:
[reuse_detection.py:88](backend/app/services/reuse_detection.py#L88),
[hierarchy.py:376](backend/app/services/hierarchy.py#L376),
[subtask_reuse.py:114](backend/app/services/subtask_reuse.py#L114). It **is** a name/embedding match.

**Resolution:** restate the testable claim to match the code. The defensible version:

> Hierarchical, per-subtask embedding retrieval identifies the individual reusable components of a
> composite task, where flat whole-task embedding retrieval recovers at most one.

That is a real claim, it is the thing Part C actually implements, and AFTER can test it with 40
composite tasks and 96 gold components. Experiment 2's Hypothesis B ("postcondition mismatch blocks
retrieval") is **cut** — there is no postcondition mechanism to block anything.

---

## 2. Setup (run once, in order)

```powershell
# 1. Start Docker Desktop and wait for the engine
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
# wait until this prints a version:
docker version --format '{{.Server.Version}}'

# 2. Postgres 17 + pgvector, on 5433 so the native PG17 on 5432 is untouched
docker run -d --name stealthlab-pg `
  -e POSTGRES_PASSWORD=stealthlab -e POSTGRES_DB=stealthlab `
  -p 5433:5432 -v stealthlab-pgdata:/var/lib/postgresql/data `
  pgvector/pgvector:pg17

# 3. Apply all 11 migrations in order (native psql client works fine against the container)
$env:PGPASSWORD="stealthlab"
$psql="C:\Program Files\PostgreSQL\17\bin\psql.exe"
Get-ChildItem backend\db\*.sql | Sort-Object Name | ForEach-Object {
  Write-Host "applying $($_.Name)"
  & $psql -h localhost -p 5433 -U postgres -d stealthlab -v ON_ERROR_STOP=1 -f $_.FullName
}

# 4. Repoint .env  (comment out the Supabase line, do not delete it)
#    DATABASE_URL=postgresql://postgres:stealthlab@localhost:5433/stealthlab
```

**Gate — do not proceed past a failure here.** This is step 1 of the original plan's build order
and it is the right gate:

```powershell
cd backend
python integration_check_v2_hierarchy.py
```

It exercises `build_hierarchy_for_table` against real Postgres, which is where
`avg(embedding)` ([hierarchy.py:217](backend/app/services/hierarchy.py#L217)) is confirmed or
denied. Every experiment below depends on it. All hierarchy correctness claims to date come from a
fake-DB harness.

### Embedding throughput — the one thing that will otherwise fail mid-run

Voyage free tier is **3 requests/min and 10K tokens/min**, and
[`Embedder`](backend/app/services/embeddings.py#L38) has no throttle, no batching by token count,
and no cache. Embedding 22 skill docs + 129 instructions in a naive loop gets rate-limited, and
every call site **silently degrades to lexical** instead of raising — so the run would appear to
succeed while measuring the wrong thing.

**Required:** reuse the throttled, disk-cached embedder that already exists at
[knowledge.py:81](backend/experiments/swebench_pro/knowledge.py#L81) (`MAX_BATCH_TOKENS = 7000`,
21 s spacing, SHA256 cache). Port it to `experiments/after/embed_cache.py` and use it for corpus
construction. Budget: ~10 minutes one-time, ~0 thereafter (cached).

Also assert loudly rather than degrade: before scoring, verify every seeded node has
`embedding IS NOT NULL`, and abort if any retrieval arm reports `method="lexical"`.

---

## 3. Corpus construction (shared by Experiments 1 and 2)

| | |
|---|---|
| **Library** | 22 AFTER skills → `task_nodes`. `name` = skill dir, `description` = `SKILL.md` body. Seeded via `Onboarder.seed()`, `provenance='company_ingested'`. |
| **Tree** | `build_hierarchy_for_table(pool, 'task_nodes', apply=True)` → internal nodes + `OWNS`/`PARENT_OF` edges. |
| **Query set** | 129 `instruction.md` |
| **Gold** | `task.toml:skills` |

No leakage: skills and tasks are independent artifacts, and no task text enters the library. Use
`SKILL.md`, not `SKILL_HANDCRAFT.md` — pick one and record which; they are two versions of the same
content and mixing them makes the library incoherent.

Verified composition:

| skills per task | tasks |
|---|---|
| 1 | 89 |
| 2 | 24 |
| 3 | 16 |
| **composite (≥2)** | **40** (96 gold components) |

Composites are spread across all six roles (de 7, ds 6, genai 9, infra 5, pm 7, swe 6), so
role is not confounded with compositeness.

---

## 4. Experiment 1 — Reusability *(Tier 1, primary)*

### Hypothesis A — task-level retrieval

Restricted to the **89 single-skill tasks**, where "the correct skill" is unambiguous.

| Arm | Mechanism |
|---|---|
| A0 | Lexical baseline — `_lexical_overlap` ([reuse_detection.py:71](backend/app/services/reuse_detection.py#L71)) |
| A1 | Flat embedding — `_vector_candidates` ([reuse_detection.py:88](backend/app/services/reuse_detection.py#L88)) |
| A2 | Hierarchical — `hierarchical_search` beam descent ([hierarchy.py:376](backend/app/services/hierarchy.py#L376)) |

Metrics: precision@1, recall@3, recall@5, and **comparisons per query** (`SearchResult.comparisons`
— the cost axis the tree exists to win on).

`backend/after_results.json` already records a flat run: top1 0.729, top3 0.915, top5 0.930 over
n=129, skills=22. Treat it as a **sanity target for A1, not as a result** — the script that
produced it is not in the repo, so it is unreproducible as it stands. If A1 lands near 0.73 on the
same 129, the harness is wired correctly.

The honest expectation for A2: hierarchical retrieval over a 22-leaf corpus should **match** flat
accuracy at fewer comparisons, not beat it. 22 leaves is far below the scale where tree search
helps accuracy. Report it as a cost result. Claiming an accuracy win at this corpus size would
not survive review.

### Hypothesis B — subtask-level retrieval *(the actual Part C test)*

**n = 40 composite tasks, 96 gold components.**

| Arm | Mechanism |
|---|---|
| B-flat | Embed the whole instruction, take top-k |
| B-subtask | `/v1/decompose` → ChangeSet → `resolve_subtask_reuse` ([subtask_reuse.py:74](backend/app/services/subtask_reuse.py#L74)) → `report.matches` |

**Primary metric: component-level recall** — of a task's gold components, how many are identified
*individually*. Paired per task, so McNemar (recovered-all vs. not) and Wilcoxon signed-rank over
the 40 paired recall values. Not a t-test: the pairs are matched by construction.

Secondary: false-component rate (matches to skills not in gold), and `used_flat_fallback` rate.

### The failure mode this design must survive

`resolve_subtask_reuse` drops an op only at `similarity >= FULL_MATCH_THRESHOLD` (**0.90**), and it
calls `batch_hierarchical_search` with **no flat fallback** — if the tree returns
`used_flat_fallback=True`, it records nothing at all
([subtask_reuse.py:118](backend/app/services/subtask_reuse.py#L118)).

Cosine between a generated subtask string ("Create pivot tables in the output workbook") and a
full `SKILL.md` document plausibly sits around 0.6–0.8. **At threshold 0.90 the most likely outcome
is zero matches across all 40 tasks** — which reads as "the mechanism does not work" when it
actually means "the threshold was set for a different comparison."

Do not run this as a single-threshold pass/fail. Instead:

1. **Calibration split** — hold out 12 of the 40 composites. Sweep threshold 0.50→0.95 in 0.05
   steps, plot component recall and false-component rate.
2. **Fix the operating point** on the calibration split, write it down, then run the remaining 28
   once. Report both the swept curve and the single held-out number.
3. Log the raw similarity distribution regardless of threshold — that distribution is a publishable
   result on its own, and it is what tells you whether 0.90 is defensible anywhere.

Also record `report.embed_calls` (should be exactly 1 per task regardless of subtask count) — that
is the batching claim in the module docstring, and it is cheap to verify.

### Decomposition variance

B-subtask needs an LLM. Use General Compute (the only configured provider — Anthropic/OpenAI keys
are empty). Fix `temperature=0` where the provider allows it, pin the model string, and run each
task **3 times**, reporting median component recall plus spread. A single sample over 40 tasks
cannot separate mechanism from decoding noise.

---

## 5. Experiment 3 — Debate + Update *(Tier 1, redesigned substrate)*

τ³-bench `banking_knowledge` is absent, and no substitute is needed — this experiment only ever
required a contradiction plus a downstream query.

**Setup**
1. Seed `KnowledgeNode`: *"Refunds require the original receipt"*, `t_valid = T0`.
2. Seed a `task_node` for the refund procedure, plus edges.
3. Inject the contradiction at T1: *"As of T1, refunds under $50 do not require a receipt."*

**Firing the debate.** `TriggerDetector.scan()` reads the **`traces` table only**
([triggers.py:59](backend/app/services/triggers.py#L59)) — debate cannot fire on ingested content
(this is the same gap covered earlier in this conversation). So insert synthetic traces against the
refund task node with an error rate past threshold, exactly as
[bootstrap_demo.py](backend/scripts/bootstrap_demo.py) does, then `POST /v1/admin/scan`.

**Panel:** General Compute, 3 distinct families + `gemma-4-31B-it` judge.
`enforce_independence` ([loop.py:79](backend/app/services/loop.py#L79)) will reject a judge sharing
a family with the panel — verify the configured roster passes *before* the run, not during.

**Assertions (deterministic, SQL-checkable):**
- candidates propose citation-by-node-id ops
- old node has `t_invalid = T1`
- a `SUPERSEDES` edge exists, new → old

**Downstream behavioural check:** ask *"customer wants a $30 refund, no receipt — approve?"* through
(a) `HybridRetriever` + `llama3.1:8b`, and (b) a flat-RAG baseline holding both documents embedded
with no invalidation logic. Expected: (a) follows the new rule, (b) serves the stale one or hedges.

**Statistical honesty:** this is n=1 with ~5 paraphrases of the query. It is a **case study**, not a
powered test. Report it as an existence proof that the bi-temporal mechanism changes downstream
behaviour, and do not attach a p-value.

---

## 6. Demoted and cut

### Experiment 2 — **cut**
Hypothesis A needs EvoMemBench CROSSEP-KNOW and CL-Bench sequencing; neither is on this machine.
Hypothesis B tests precondition/postcondition matching, which does not exist (B5). A cross-role
transfer study using AFTER's six roles is genuinely possible later, but it would measure embedding
confusion between roles — a different and much weaker claim than the one written. Do not run a
relabelled version and present it as Experiment 2.

### Experiment 4 — **demoted to Tier 3, token-only**
Two independent problems:
- **No grader** (B3). Without `test_outputs.py`, success must come from an LLM judge, which is not
  benchmark-grade and cannot support the "closes the gap to frontier" claim as written.
- **No frontier arm.** Anthropic, OpenAI, Google, and Fireworks keys are all empty. The 2×2's
  "frontier" cell would be another General Compute open-weight model — that is not a frontier
  reference ceiling, and labelling it one would misstate the result.

What *is* objectively measurable today: **tokens per episode, with and without the `TaskNode`
trajectory**, on `llama3.1:8b` and `qwen2.5-coder:7b`. Token compression is a real, honest,
self-contained result and needs no grader. Run that; leave accuracy alone until either a sandbox
or a frontier key exists.

---

## 7. Run order

| # | Step | Blocking? | Est. |
|---|---|---|---|
| 0 | Docker + pgvector + migrations (§2) | **yes** | 20 min |
| 1 | `integration_check_v2_hierarchy.py` green | **yes** | 5 min |
| 2 | Port throttled embedder; build 22-skill library + tree | **yes** | 30 min + 10 min embed |
| 3 | Exp 1 Hyp A (A0/A1/A2, n=89); check A1 ≈ 0.73 | no | 1 h |
| 4 | Exp 1 Hyp B calibration sweep (12 tasks) | no | 2 h |
| 5 | Exp 1 Hyp B held-out run (28 tasks × 3 seeds) | no | 3 h |
| 6 | Exp 3 supersession case study | no | 3 h |
| 7 | Exp 4 token-compression only | no | 2 h |

Steps 0–2 are shared infrastructure; everything else depends on them. Step 3 is the cheapest real
result and validates the harness against a known number before any expensive run.

---

## 8. Headline claim, restated to match the code

> On 40 composite tasks from AFTER (96 gold components), hierarchical per-subtask retrieval
> identifies individual reusable components that flat whole-task retrieval misses, at a calibrated
> similarity threshold, with one embedding call per task regardless of subtask count.

Testable here, today, with the substrate on this disk and the code in this repo. It does not claim
structural matching, does not claim a frontier comparison, and does not claim execution-verified
correctness — none of which this machine can currently support.
