# Step 0 summary — local pilot: 1,000 nebius SWE-rebench OpenHands trajectories

Date: 2026-09-28
Shard: **local Postgres 17.5, pgvector 0.8.0, `localhost:5432/postgres`** (`DATABASE_URL_LOCAL`), 103 tables pre-migrated.
**No production writes.** `backend/.env` points `DATABASE_URL` / `DATABASE_URL_DIRECT` / `CONTROL_DATABASE_URL` at Neon;
the launcher `run_step0_pilot.py` overrides all three to the local DSN before importing the app, and the CLI independently
refuses any non-loopback DSN. Experiment DBs (`kel_*`) were never contacted.

Research: `.scratch/ingestion/step_0_research.md`
Raw report: `.scratch/ingestion/step_0_pilot_1000.json` · probe: `.scratch/ingestion/step_0_pilot_probe.json`

---

## 1. What I researched

Full source list with identifiers, years and evidence tiers is in the research file. The load-bearing findings:

- **SWE-rebench** `arXiv:2505.20411` (NeurIPS 2025 D&B) — our plan's id verified correct. Success is the F2P+P2P triple
  against the agent's `model_patch` in the published container. The authors state the pipeline "may result in some tasks
  being imperfectly described or unsolvable", so **`resolved=0` is not a uniformly negative example** — the single most
  important caveat for step 1's failure Claims.
- **The oracle is not sound.** `arXiv:2410.06992` (SWE-Bench+): 31.08% of passed patches suspicious, 32.67% solution
  leakage. `arXiv:2506.12286` (SWE-Bench Illusion): instance- *and* repository-level memorization, both measurable.
  `resolved=1` is a floor, not a proof.
- **The under-used asset.** `gen_tests_correct` × `pred_passes_gen_tests` is a pre-computed **test-gaming detector**;
  Nebius states no other public trajectory dataset evaluates agent-generated tests. No published source reports the
  distribution of either field.
- **Retrieval/reuse quality is essentially unmeasured in agent memory.** Across SWE-smith, SWE-Gym, Agent Workflow
  Memory, ReasoningBank, SkillWeaver, SKILL-DISCO, ACE and the CBR literature, **none** report an IR metric. The single
  exception, `arXiv:2511.21730` (preprint, industry), measures **84% → 59% MAP under vocabulary shift** and reports its
  own LLM judge at **Cohen's κ = 0.178** on borderline pairs.
- **A retrieved bad record is self-reinforcing** (`arXiv:2505.16067`: input/output similarity r ≈ 1, errors amplified
  and written back). Its "future task evaluations are free quality labels" mechanism is directly usable in an
  append-only substrate.
- **Exa sweep:** the HF discussions tab is **empty** (no community reports); the card misreports its own competitors
  and shipped a literal `LINK-TO-BE-ADDED` placeholder; field drift is documented in the repo's commit history. **No
  reusable public chat-message→trajectory normalizer exists for this shape** — the nearest prior art is the card's own
  snippet, which has the two bugs below.

## 2. Corrections found by verifying locally (these beat the card and our own notes)

| what the card / our notes say | what the parquet actually has |
|---|---|
| 9 columns | **10** — `tools` and `model_patch` are undocumented in the card's field table |
| `pred_passes_gen_test` (singular) | **`pred_passes_gen_tests`** — the singular name does not exist; a `KeyError` waiting to happen |
| `resolved` / test counts are ints | `gen_tests_correct` and `pred_passes_gen_tests` are **floats**, nullable (`null` = no tests generated) |
| n/a | **No license field at all.** Per-item licensing must be joined from the parent `nebius/SWE-rebench` |
| n/a | `tool_calls` is the **literal string `"None"`** on non-assistant messages (so `if msg["tool_calls"]:` is truthy) |
| n/a | `function.arguments` is a **JSON string**, not a dict |
| "our existing adapter reads it" | Correct that it doesn't: `openhands.py` cannot read this shape. Its contract is unchanged by this step |

## 3. What I built

| file | what |
|---|---|
| `backend/app/services/ingestion_sources/chat_messages.py` | The chat-message normalizer (own module; `openhands.py` untouched). Pure, deterministic, no I/O. `tool_call_id` join folds each result into its caller's event, order-preserving — the same invariant `openhands._pair_history` holds. Own `SourceAdapter` for the HF dataset, streamed and revision-pinned. |
| `backend/app/ingestion/traj_pilot_cli.py` | `ingest-trajectories` pilot command with the gates, per-item instrumentation, and refusal guards. |
| `backend/tests/test_chat_message_trajectory_offline.py` | **37 proving tests**, offline, hand-rolled fakes. |
| `backend/app/services/trajectory_semantics.py` | **One enabler fix** (see §5): strip a markdown code fence before the strict JSON parse. 23 lines, no behaviour change otherwise. |
| `run_step0_pilot.py` (repo root) | Launcher that forces every DB pointer at localhost before importing the app. |
| `.gitignore` | One line: the 21k-entry license-join cache is a build artifact. |

**Design decisions worth stating.** The net `model_patch` is emitted as a final `WRITE` event, not parked in
`metadata`: `write_normalized_trajectory` redacts and size-caps `tool_input`/`tool_output` but writes `metadata` to
`agent_traces` verbatim, and a ~9 KB third-party diff does not belong on an uncapped unredacted path. `tools` is stored
as a *count*, not the 5 repeated function definitions. A `dedup_key` prefix distinct from `openhands:` prevents the
same trajectory ingested from a directory export and from HF colliding on a `NOT NULL UNIQUE` column.

## 4. The run

`python run_step0_pilot.py ingest-trajectories --shard-dsn-env DATABASE_URL_LOCAL --limit 1000 --root .. --semantics 0`

| metric | value |
|---|---|
| rows streamed / resolved | 1,000 / 1,000 |
| **accepted** | **713** |
| already in shard (idempotent re-write) | 16 |
| **rejected** | **271** — `duplicate_instance_in_run` 135, `held_out_repo` 64, `license_unmappable` 35, `license_quarantine` 27, `held_out_instance` 10 |
| license decisions | ALLOW 729 · QUARANTINE 27 · unmappable 35 |
| scaffold turn-capped (`exit_status`) | 30 |
| malformed rows / malformed events | **0 / 0** |
| events written | 43,842 |
| episodes written | 713 (**1.00 per trajectory**, as predicted) |
| wall time | **80.62 s** |
| throughput | **31,837 items/hour** |
| DB bytes | 126,337,024 (126 MB) |
| **model spend, raw path** | **$0.00** (0 LLM calls; 0 new `llm_spend` rows) |

Per accepted item: **61.49 events · 1.00 episode · 9,334.6 patch bytes · 177,190.8 DB bytes · $0.000000**.

**Knowledge items produced: 0 Goals, 0 Claims, 0 Procedures, 0 Observations.** Not a filter problem — the paid
extraction layer is broken. See §5.

### Projection for the full 32,161 resolved trajectories

| | |
|---|---|
| wall time | **~1.0 hour** single-process |
| DB size | **~6 GB** (`pg_total_relation_size` on all public tables) |
| raw-path model spend | **$0.00** |
| semantic extraction, 1 call/trajectory at the measured $0.022233 | **~$715** total, **~$9.40/day** at 10 items/min, **~3.2 days** of wall clock at $10/day |

## 5. Blockers and findings

### B1 — the paid extraction path produces nothing (blocker for step 1)
**13 of 13 extraction calls failed. $0.2001 spent, 0 knowledge items.**

I fixed the first cause (in this change): every response was ```` ```json ````-fenced and the strict parser rejected
all of them. `_strip_code_fence` now unwraps a fence before parsing; invalid JSON inside a fence still raises, and
fenced-but-valid JSON is not a degraded result.

The remaining cause is **prompt/schema drift in `trajectory_semantics._SYSTEM_PROMPT`** — three verified mismatches:

| prompt says | schema wants |
|---|---|
| `goal` | `primary_goal` |
| procedure step `subgoal_text` | step `description` |
| `OBSERVED` / `INFERRED` / `GENERALIZED` | `observed` / `inferred` / `generalized` |

A model that follows the prompt produces a response the strict parser *must* reject. Pinned as a test so it cannot
rot. **Fix is step-1 work**: align the prompt with the schema, or send `TrajectorySemanticExtraction.model_json_schema()`
in the request (the same lesson as the board's core-b `json_mode` entry, never applied here).

### B2 — the same client-shape trap, in reverse
`extract_trajectory_semantics` calls `client.chat.completions.create(...)` **without awaiting**, so it needs a *sync*
`OpenAI`. I first built an `AsyncOpenAI` and every call died with `'coroutine' object has no attribute 'choices'`.
The pilot now reuses `ingestion_jobs._general_compute_client()` rather than a second provider path. Separately: that
sync call runs inside an `async def`, which is exactly what `app/utils/aio.run_blocking` exists to prevent — a
pre-existing issue I did not fix (out of step-0 scope).

### F1 — extraction cost, measured
9 calls, **$0.022233/call**, 3,599 in / 762 out tokens avg. Retries multiply this; the probe's higher per-item figure
came from retry-on-failure, which is itself a finding: at today's 0% success rate every dollar is spent twice.

### F2 — the license gate would have rejected 100% of the corpus if built the obvious way
`license_name` is a GitHub *display name* (`"MIT License"`), and `repo_license_policy.identify_spdx_from_text` is a
license-**text** header matcher that returns `None` for those. The gate silently degrades to "everything unmappable".
The repo already has the right mapper (`verified_solutions_hf.normalize_spdx`, documented as handling "GitHub display
names"); I reused it. `CC-BY-4.0` is not on the permissive allowlist, so gating on the card's license would reject
everything — the corpus license is recorded as provenance, the gate is per item.

### F3 — 13.5% of the run is the same task twice
`duplicate_instance_in_run` = 135. The corpus averages 8.5 successful trajectories per resolved issue, so
"1,000 resolved rows" is ~865 distinct tasks. Worth deciding deliberately in step 1 whether to keep the best trajectory
per task, since keeping all of them is 15% duplicate work.

### F4 — repo-level held-out exclusion is the bigger half
64 repo-level exclusions vs 10 instance-level, from 21 scored repos. `arXiv:2506.12286` says repository-level
memorization is measurable, so this is the exclusion that matters most — and it is the one an instance-level-only
implementation would miss.

### F5 — measured 100-turn censoring
30/1,000 (3%) ended with `RuntimeError: Agent reached maximum iteration`. Neither the card nor the blog publishes this
distribution. Those are scaffold terminations, not model failures, and must not become failure Claims.

### F6 — one episode per trajectory
Merging assistant+tool roughly halves event counts (61.5 events/trajectory), so essentially every trajectory lands
under the 200-event subdivision threshold as a **single** episode. The per-episode semantic extractor will therefore
see a whole 64-step trajectory as one unit. Flagged for step 1 rather than special-cased.

### F7 — instrument bug found and fixed
`model_usd_in_window` used a trailing `now() - interval` window and swept up an earlier probe's spend, reporting
$0.20 for a run that made zero model calls. Now a true `[run_start, run_end]` interval. The 1,000-row figures above
use the corrected reading: **$0.00**, established twice — `llm_calls = 0`, and no `llm_spend` row timestamped inside
the run.

## 6. Tests

- New: **37** (`tests/test_chat_message_trajectory_offline.py`) — normalizer (merge/order/`"None"`-trap/arguments/
  test-promotion/unclassified/patch-event/ceiling/schema-pinning), per-item license gate, held-out exclusion at
  instance **and** repo level with the fail-closed loader, both pilot refusals, the fence fix, and the drift pin.
- Full offline suite: **4,288 passed / 678 skipped / 55 failed** in 6m00s.
- **Zero failures reference my code** (grepped for `chat_messages`, `traj_pilot`, `_strip_code_fence` → 0 hits).
  The 55 decompose as: **14 pre-existing** at HEAD (auth posture, economy auth, migration upgrade, tenant-SQL,
  procdoc/embedding, startup config) and **41 from in-flight step-6 work** that appeared in this checkout while I was
  running (`gh_client.py` is untracked; its redirect test fails with `GET ... -> 500`).

⚠️ **Working-tree warning.** Other lanes have been writing to *this same checkout* in parallel — step 3/4/6/7 research
and summaries, `codemod_*`, `skillmd_*`, `gh_client.py`, migration `125_procedure_verifier_check.sql`, and edits to
`admin.py`, `ingestion_jobs.py`, `trace_worker.py`, `procedures.py`. I stashed and restored once to establish a
baseline and confirmed the tree came back intact, and my only edit to a shared file
(`trajectory_semantics.py`) is the 23-line fence fix with no collision. But **step 0 should be re-run in its own
worktree before the numbers above are trusted as a clean measurement.**

## 7. Risks

1. **Extraction yield is 0.** Every downstream budget in steps 1–7 assumes knowledge comes out of a trajectory. Until
   B1 is fixed there is no yield to scale.
2. **CC-BY-4.0 does not relicense the 1,823 upstream repos.** The per-item gate is the only control; redistribution
   relies on Nebius's own compliance.
3. **`resolved=1` is a floor, not a proof** (SWE-Bench+). A "verified" Procedure built on it needs the evidence chain,
   not the flag.
4. **One shard, one process, no failures injected.** Throughput (31.8k items/h) is an upper bound: no retries, no
   contention, no partial-write recovery, no dedup contention.
5. **`pg_total_relation_size` over all public tables** is a coarse byte measure; it includes indexes and any other
   concurrent writer. The 6 GB projection is indicative, not a capacity plan.

## 8. Full-scale command and projected cost

```powershell
# raw path only: ~1.0 h, ~6 GB, $0.00
python run_step0_pilot.py ingest-trajectories `
  --shard-dsn-env DATABASE_URL_LOCAL --limit 32161 --root ..

# with extraction on every episode, once B1 is fixed: ~$715, ~3.2 days at the $10/day cap
python run_step0_pilot.py ingest-trajectories `
  --shard-dsn-env DATABASE_URL_LOCAL --limit 32161 --root .. --semantics 32161
```

**Production is not proposed and was not touched.** When it is, the launch runbook's credential list
(`INGEST_SERVICE_TOKEN`, `SERVICE_TOKEN_KEYS`, `GENERAL_COMPUTE_*`, `GITHUB_TOKEN`, `VERTEX_*`) must be satisfied
first — the step-6 runbook documents none of them.
