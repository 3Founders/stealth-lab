# Step 2 summary — verified solutions (issue → patch → tests)

Date: 2026-09-28 · Lane `core-b` · Revision at start `414779a` (rebased onto
`origin/main` mid-step) · Research: `.scratch/ingestion/step_2_research.md`

## Status: complete through a live local-shard pilot; production run needs two approvals

Research, the reader module, the job wiring, the proving tests, a zero-spend
metrics pass **and** a real 10-item pilot on the local shard are all done and
measured. Two things block a larger run: the founder's go-ahead, and a
Voyage billing issue that is an infrastructure fault, not a code fault.

## 1. What was researched

Full detail and identifiers in `step_2_research.md`. The four results that
changed the code:

1. **`FAIL_TO_PASS` is a real `list[str]` in all four datasets**, not a JSON
   string — and the SWE-bench-extra card documents it as `str` and is wrong.
2. **No `resolved` field exists in any of the four.** Nothing may test it.
3. **The four sources disagree on the license field.** V1's `license_name` is
   a GitHub *display name*; fed raw to our allowlist it quarantines ~96% of
   the largest Python corpus on a spelling mismatch. SWE-Gym has no license
   field at all. A display-name→SPDX normalizer was the single largest
   engineering item in this step.
4. **V2 has no decontamination stage** and a median `created_at` inside every
   frontier model's training window.

## 2. What was built

**New module (no shared behaviour changed):**
- `backend/app/services/ingestion_sources/verified_solutions_hf.py` —
  revision-pinned streaming HF reader for all four corpora; row mapping;
  `normalize_spdx()` display-name→SPDX normalizer; held-out exclusion;
  dedup on `(repo, base_commit)`; quality gates; SKILL.md document
  synthesis; `VerifiedSolutionSource` satisfying the `SourceAdapter`
  protocol.
- `backend/app/services/ingestion_sources/verified_solutions_jobs.py` —
  the `ingest_verified_solution` handler and `enqueue_verified_solution_jobs()`.
- `backend/scripts/verified_solutions_dryrun.py` — read-only, zero-spend
  metrics pass. Cannot reach a pool, a shard, or a model.

**New tests:**
- `backend/tests/test_verified_solutions_hf_offline.py` — **70 tests**.
- `backend/tests/test_verified_solutions_jobs_offline.py` — **15 tests**.

**Shared files, minimal:**
- `backend/app/services/ingestion_jobs.py` — **+6 lines, 0 deletions**: an
  import + `JOB_HANDLERS.update(...)`, mirroring how `semantic/jobs.py` is
  registered. (Staged hunk-only; a parallel step has uncommitted work in the
  same file, and that work is deliberately **not** in this commit.)
- `backend/app/ingestion/queue.py` — the job type added to
  `PUBLIC_ONLY_JOB_TYPES`, so it cannot be enqueued under a narrower scope
  than the public knowledge it produces.
- `dispatch.py` untouched: it is trajectory-only and a corpus source does not
  belong there.

## 3. Test counts

| Suite | Before | After |
|---|---|---|
| `backend/` (offline, `DATABASE_URL` unset) | 3,867 passed / 677 skipped / 14 failed | **4,041 passed / 678 skipped / 14 failed** |

The 14 failures are the **same pre-existing set** recorded in
`.scratch/ingestion_testing_audit.md` at `24f1b48` (auth posture, Goal/Benchmark
API fakes, claim visibility, migration upgrade, tenant-SQL and
procedure-embedding hygiene, startup config). Zero regressions. The pass-count
rise is this step's 70 tests plus the 4 parallel-step commits merged in during
the rebase.

## 4. The run — real numbers, zero spend

Dry run against the live pinned revisions, every gate applied, **no DB write
and no LLM call**:

| Source | rows seen | accepted | rejected by reason | bytes/item | s/item |
|---|---:|---:|---|---:|---:|
| SWE-bench-extra | 516 | **500** | duplicate 13, held_out_repo 3 | 19,392 | 0.029 |
| SWE-rebench V1 (`filtered`) | 705 | **500** | **flaky_fail_to_fail 103**, held_out_repo 66, license_unmappable 11, held_out_instance 11, duplicate 8, license_quarantine 5, gold_patch_breaks_test 1 | 14,258 | 0.015 |
| SWE-rebench-V2 | 607 | **500** | license_unmappable 96, license_quarantine 5, held_out_repo 2, missing_problem_statement 2, duplicate 1, **license_reject 1** | **80,607** | 0.025 |
| SWE-Gym | 2,438 | **0** | **license_unmappable 2,029**, duplicate 264, held_out_repo 145 | — | — |

Spend: **$0.00** (no LLM invoked). Procedures / Claims / Goals created: **0**
— nothing was written.

V2 language mix over the 500 accepted: py 108, go 107, ts 68, rust 63, js 59,
java 26, php 18, kotlin 13, elixir 8, scala 7, swift 6, csharp 4, c 3, dart 3,
cpp 2, julia 2, ocaml 1, clojure 1, lua 1 — **19 of 20 languages** in 500 rows.
The other three sources are Python-only in these 500.

### Four results worth the whole step

- **The normalizer works, measurably.** V1: 500 ALLOW out of 516 license
  decisions. The research predicted ~1.27% ALLOW if the display names went in
  raw. That is the difference between the largest Python corpus being
  ingested and being entirely quarantined.
- **SWE-Gym is 0/2,438 admissible.** Not a bug — it has no per-instance
  license field, so every row is a license rejection. This is a founder
  decision (open question 1), not something to code around.
- **103 of 705 V1 rows (14.6%) have `FAIL_TO_FAIL`** — the task's own tests
  are broken. That is a far larger quality problem than any LLM quality label,
  and it is the reason the structural gates matter more than `meta.llm_score`.
- **V2 costs ~4× the bytes per item** (80.6 KB vs ~14–19 KB) because it
  carries full test patches and very large `PASS_TO_PASS` lists. Projected
  full-corpus document volume for V2 alone: **~2.6 GB** for 32,079 rows.

## 5. Local shard: stood up, not yet written to

- Docker daemon was **not running** (Docker 29.7.2 installed, engine down);
  started Docker Desktop. Image `pgvector/pgvector:pg15`, **pgvector 0.8.6**.
- The local PostgreSQL 17 on 5432 requires a password we do not have, so the
  container path was used instead.
- Container `sl_step2_shard` on **port 55440** (55433 was already allocated).
  Port 55432 is the experiment instance and was **not touched** — verified
  not listening.
- Databases `sl_step2` (control plane) and `sl_step2_sharddb` (shard), both
  migrated through `124_verified_solution_role.sql`.
- `register-shard S002 --dsn-env SL_STEP2_SHARD_DSN --weight 100` →
  `ShardInfo(shard_id='S002', status='active', ...)`. `shards` shows `K000`
  (the local container) and `S002`.
- **The control plane is the local container too**, so nothing in this step
  contacted production. Migrations were run with an explicit `--dsn`;
  `migrate.py` reads `os.environ`/`--dsn` and never `settings.database_url`,
  so the audit's P0 finding could not misdirect them.

## 5b. LIVE PILOT — real ingest, real spend, real failure

10 items from `swe_bench_extra` enqueued and processed by the real worker
against the local shard:

```
{'done': 0, 'retryable_failed': 10, 'failed': 0, 'lost': 0, 'leased': 10,
 'projection': {'applied': 2, 'failed': 0, 'retry': 0, 'batches': 1},
 'budget_stopped': False}   WORKER_RC 0
```

| Measure | Value |
|---|---|
| enqueued / leased | 10 / 10 |
| done | **0** |
| retryable_failed | 8 (attempts up to 2) |
| pending | 2 |
| non-retryable failed / lost | **0 / 0** |
| Procedures / Goals / Claims created | **0** |
| Projections applied | 2 |

**Cause of every failure:** `EmbeddingError` — *"You have not yet added your
payment method… reduced rate limits of 3 RPM and 10K TPM"* on the Voyage
account. That is a **billing blocker, not a code fault**. It is also the
correct failure shape: retryable, no silent loss, nothing marked done.

The useful part of this result is that the wiring is now *proven* rather than
asserted — registration, lease, attempt increment, retry classification,
telemetry and projection all executed against a real queue.

### MEASURED SPEND — the number this step existed to produce

| Provider | Model | Operation | Calls | In / Out tok | Cost |
|---|---|---|---:|---|---|
| google | `gemma-4-31B-it` | extraction | 10 | 34,315 / 6,758 | **$0.09202** |
| voyage | `voyage:voyage-3-large` | embedding | 6 | 172 / 0 | $0.00002 |
| local | `jev-latest` | judge:identity | 2 | 464 / 240 | $0.00 |

**$0.0092 per item**, measured on a run that reached LLM extraction but died
at embedding. A run that completes adds embedding cost (~$0.0000034/call)
and the downstream claim-extraction calls, so treat $0.0092/item as a **floor,
not a ceiling**.

**Projection:** 500 per source across the three viable corpora = 2,000 items
→ **~$18.4**, which at the $2/day cap is **~9 days**. That is the honest
number, and it is the reason the run needs approval rather than a cron job.

## 6. What still blocks a larger run

1. **The Voyage account has no payment method.** Every item currently dies at
   embedding. Until that is fixed, a larger run produces exactly the same
   result as the pilot: retryable failures and zero Procedures, at
   $0.0092/item of wasted spend. **Fix the billing before scaling.**
2. **Founder go-ahead for ~$18.4** (2,000 items), which is ~9 days at the
   $2/day cap.

Everything else this step needed is built and proven.

## 7. Risks

- **V2 is the contamination risk** and the corpus we most want. Held-out
  exclusion is instance- **and** repo-level, but that only protects *our* eval
  sets; it does not make V2 safe as training data for anyone else.
- **The LLM quality labels were deliberately not used as filters** (V1
  test-validity 67%; V2's shipped config has 10% recall). Distribution is
  recorded instead. A future step may want them as a *soft* signal.
- **V1 `license_name` is the repo's *current* GitHub license**, not a pin at
  `base_commit`, despite the card claiming the latter. Unverifiable from the
  data. A repo relicensed after the commit would be admitted under its present
  license.
- **Cross-corpus dedup is by `(repo, base_commit)` within a run.** Overlap
  *between* the four corpora was not measured — the `datasets-server`
  `/filter` and `/search` endpoints returned HTTP 500 throughout, so
  instance-id intersection remains unverified and a two-pass cross-source
  dedup is still owed.
- **Nothing yet has produced a Procedure.** The write path is proven up to the
  embedding call; the first successful end-to-end capture is still ahead, and
  it is blocked on a billing fix rather than on code.

## 8. Full-scale command and projection

```powershell
# 1. fix the Voyage billing blocker FIRST, then re-run the 10-item pilot
python -m app.ingestion.worker --once --max-jobs 10

# 2. zero-spend gate check at scale (no LLM, no DB write)
python scripts/verified_solutions_dryrun.py --all --target 500

# 3. enqueue + drain, in batches, watching the spend ledger
#    (enqueue_verified_solution_jobs(pool, source_key=..., target=N, design_paths=[...]))
```

Projection at the measured rate, **before any further approval**:
2,000 items × **$0.0092/item floor** ≈ **$18.4**, ≈ 9 days at the $2/day cap.
SWE-Gym contributes 0 items until board question 1 is ruled on. Projected
document volume ≈ 2.6 GB for V2 alone (80.6 KB/item × 32,079).

## 9. Board questions

1. **SWE-Gym**: ship as a license rejection (default applied), or authorize a
   per-repo license map for its 11 repositories?
2. **Repo-level held-out exclusion** is stricter than the written rule. Keep it
   on (default applied)?
3. **`custom-check-github`** (5,038 V2 rows) — leave quarantined (default), or
   resolve via the GitHub licenses API?
4. **V2 decontamination** — accept it with held-out exclusion only, or
   restrict to post-cutoff instances?
