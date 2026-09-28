# Step 2 summary — verified solutions (issue → patch → tests)

Date: 2026-09-28 · Lane `core-b` · Revision at start `414779a` (rebased onto
`origin/main` mid-step) · Research: `.scratch/ingestion/step_2_research.md`

## Status: partial, and the missing part is the spend decision

Research, the reader module, the proving tests and the zero-spend metrics
table are **done and measured**. The local-shard **write** run is **not
done**: the ingest path requires a configured General Compute LLM client
(founder directive 2026-09-15 removed the deterministic fallback), so the
first row written costs money. Per the build prompt I am reporting projected
spend before a run larger than the pilot rather than starting one.

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

**New module (no shared file touched):**
- `backend/app/services/ingestion_sources/verified_solutions_hf.py` —
  revision-pinned streaming HF reader for all four corpora; row mapping;
  `normalize_spdx()` display-name→SPDX normalizer; held-out exclusion;
  dedup on `(repo, base_commit)`; quality gates; SKILL.md document
  synthesis; `VerifiedSolutionSource` satisfying the `SourceAdapter`
  protocol.

**New script:**
- `backend/scripts/verified_solutions_dryrun.py` — read-only, zero-spend
  metrics pass. Cannot reach a pool, a shard, or a model.

**New tests:**
- `backend/tests/test_verified_solutions_hf_offline.py` — **70 tests**.

**Shared files changed: none.** `dispatch.py` is trajectory-only and a corpus
source does not belong there; `admin.py` already had `register-shard`; no
`ingestion_jobs.py` edit was needed because nothing was wired to a job type
yet (see §6).

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

## 6. What is not done, and why

**The local-shard write run.** Two blockers, both deliberate:

1. **It costs money.** `compile_skill_artifact` refuses an artifact when no
   General Compute LLM client is configured. I have no measured $/item for
   the compile step, so any projection would be invented. The build prompt
   requires reporting projected spend before a run larger than the pilot.
2. **A job type is not wired.** A corpus source needs a job type + payload in
   `ingestion_jobs.py` (`ingest_verified_solution`), and the prompt asks that
   shared-file edits stay minimal. That edit is small but it is the seam where
   a half-gate could land, so it should be one deliberate change with its
   proving tests rather than a by-product of this commit.

**Proposing not to guess a $/item.** The honest next move is a **10-item
pilot** against `S002` to measure real spend, bytes and wall time, then
project. At the observed ~0.02–0.03 s/item of gating, 2,000 items of gating
cost is negligible; the LLM compile is the unknown.

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
- **Nothing in this step has written a row to any database.** The write path
  is unproven.

## 8. Full-scale command and projection (once the pilot sets $/item)

```powershell
# pilot first — 10 items, local shard, measures real $/item
python -m app.ingestion.worker --once --max-jobs 10 `
  --job-type ingest_verified_solution   # requires the job-type wiring in §6

# then the measured 500/source dry-run already in this repo, zero spend
python scripts/verified_solutions_dryrun.py --all --target 500
```

Full-corpus projection at the measured 500-per-source rates, **before any LLM
cost**: 27,878 + 32,079 + 6,376 + 2,438 rows scanned; **SWE-Gym contributes 0**
until open question 1 is ruled on. Projected document volume ≈ 2.6 GB for V2
alone. LLM cost: **unknown until the pilot — do not quote a number yet.**

## 9. Board questions

1. **SWE-Gym**: ship as a license rejection (default applied), or authorize a
   per-repo license map for its 11 repositories?
2. **Repo-level held-out exclusion** is stricter than the written rule. Keep it
   on (default applied)?
3. **`custom-check-github`** (5,038 V2 rows) — leave quarantined (default), or
   resolve via the GitHub licenses API?
4. **V2 decontamination** — accept it with held-out exclusion only, or
   restrict to post-cutoff instances?
