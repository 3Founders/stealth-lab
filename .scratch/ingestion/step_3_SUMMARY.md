# Step 3 summary — SkillMD-138K, license-gated per repository, deduplicated by content

Date: 2026-09-28
Lane: step-3
Spend: **$0.00** of the $2/day cap (local Ollama embeddings + no billed extraction calls; the
provider path was exercised but every run stopped at the gates or at a pre-existing defect).
Writes: **local shard only** — control `kel_control` + shard `K002` in a disposable
`pgvector/pgvector:pg15` container on `127.0.0.1:55433`. No production write. The pilot refuses
to run against `kel_swebench*` / `kel_*:55432` (`assert_not_experiment_database`).

---

## 1. What was researched

Full record with every claim, URL, date and evidence tier:
**`.scratch/ingestion/step_3_research.md`**

Headline sources, all read rather than cited from memory:

| id | what it established here |
|---|---|
| HF `FayeZC/SkillMD-138K` card | 138,133 rows / 20,556 repos / CC-BY-4.0 covering **curation only**; the card itself says "users should consult the original repository's license". **No paper is linked on the card.** |
| arXiv:2604.04323 | Realistic retrieval over 34k licensed skills scored 38.4% vs a 35.4% no-skills baseline, and **below baseline on 2 of 3 models**. Agents loaded curated skills only 49% of the time. *An unfiltered pool is a net loss.* |
| arXiv:2602.12670 | Curated skills 33.9% → 50.5%; "at most three modules" beats exhaustive bundles. |
| arXiv:2607.00911 | 0.99 body-similarity reuse linkage; "enumerate the exact scenarios in which a skill should trigger". |
| arXiv:2607.01456 | >99% of a *popularity-filtered* 238-skill sample carries ≥1 of 26 "skill smells"; **oversized defined as >5,000 words**; smells are never fixed. |
| arXiv:2601.10338 | 26.1% of 31,132 real skills carry ≥1 vulnerability; 13.3% exfiltration; script-bundling skills 2.12× more likely. |
| arXiv:2602.06547 | **84.2% of vulnerabilities live in `SKILL.md` natural language**, not code; 73.2% "shadow features"; named coercive phrases. |
| arXiv:2510.26328 | Instruction-detection defences are ill-posed — "Agent Skills are all instructions"; "users should only rely on verified Agent Skills". |
| arXiv:2602.14211 / 2602.20156 | Cross-file payload hiding defeats doc-only scanning (34–57% ASR); up to 80% ASR; **scaling and filtering do not fix it**. |
| `agentskills.io/specification` | `name` 1–64 chars `a-z0-9-` no leading/trailing/double hyphen; `description` 1–1024; spec defines no content-quality bar. |

### Three corrections to the step brief

1. **There is no arXiv link on the dataset card** — it cites no paper. 2604.04323 is a *different*
   corpus and does not use this dataset.
2. "How AI Agent Skills Are Written, Adapted, and Maintained" is **arXiv:2607.00911**; no
   gitskills/mvaccargiu attribution exists.
3. **"`repo` gives the repository's license" is wrong for 12.7% of rows** — see §2. This was the
   single most consequential correction in the step.

## 2. What was built

All new code in its own modules. Shared files touched: **two lines** in
`backend/app/ingestion/admin.py` (a lazy import + a dispatch branch), following the existing
`_bench_cli` pattern.

| file | role |
|---|---|
| `backend/app/services/ingestion_sources/skillmd_dataset.py` | Revision-pinned HF reader, mirror/origin resolution, `SourceAdapter`, cached GitHub license resolver |
| `backend/app/services/ingestion_sources/skillmd_gate.py` | Pure gates: frontmatter, spec conformance, size, coercion, screening delegation, simhash near-dup |
| `backend/app/services/ingestion_sources/skillmd_pilot.py` | Pilot orchestration, isolation assertion, provider client factory, projection |
| `backend/app/services/ingestion_sources/skillmd_offline.py` | Fixture reader/raw-store/license resolver built from two real, redacted, re-fetched `SKILL.md` files |
| `backend/app/ingestion/skillmd_cli.py` | `skillmd-import` command |
| `backend/tests/test_skillmd_138k_offline.py` | **71 proving tests**, all passing |
| `.scratch/ingestion/step_3_research.md` | Research record |

### Decisions worth defending

- **The `content` column is never read from the parquet.** It is a 540 MB single column chunk
  inside one row group, and the dataset's own viewer is broken on it (`TooBigContentError`). The
  eight metadata columns cost ~20 MB; each skill's text is fetched from GitHub raw via its own
  `html_url`. This is also better provenance: a file deleted since the 2026-04-08 crawl is
  detected as dead rather than ingested from a 6-month-old snapshot.
- **Mirror recovery runs before any license resolution.** 12.7% of rows (17,492) live in
  aggregators; `NeverSight/skills_feed` alone holds 17,284 and encodes the origin as
  `data/skills-md/<owner>/<repo>/`. Resolving the *mirror's* license would launder a third
  party's content behind an aggregator's terms. An aggregator row whose origin is unrecoverable
  is **quarantined, not admitted**.
- **The license verdict is `repo_license_policy.classify_spdx`, reused verbatim.** An allowlist
  where `NOASSERTION`/absent means UNKNOWN, not permissive. No second, looser check was written.
- **No credential is ever attached to a non-GitHub host.** The raw fetcher sends no
  `Authorization` header and never follows a redirect with one attached — the exact defect the
  audit recorded in `github_corpus._default_http_get`.
- **Description quality is a signal, not a gate.** The real row `spec-save-design` states *what*
  without an explicit *when*; a hard gate discarded it. That is the same false-positive shape
  that got `trust_escalation` removed from the shared screener on 2026-09-16 (7 of 14 real skill
  documents rejected in one run). Hard rejects are now only: bad/absent `name`, absent or
  over-budget `description`, body <20 or >5,000 words.
- **Gate order is cheapest-and-certain first**, and there is a test per ordering claim:
  filename → mirror/origin → exact hash → fetch → frontmatter/size → coercion/screening →
  near-dup → license.

## 3. Tests

| | count |
|---|---|
| New proving tests | **71 passed** |
| Full offline suite | 4,287 passed / 678 skipped / 56 failed in 4m37s |
| Failures in my files | **0** |
| Regressions introduced | **0** |

56 failures break down as: **41 in `tests/test_step6_ingestion_sources_offline.py`** (another
lane's uncommitted work, present in this shared checkout and not mine) and 15 elsewhere —
14 of which are the pre-existing baseline the earlier audit recorded, plus one in
`test_procdoc_v2_pipeline_offline.py` that arrived with migration 125 from a parallel step.

Tests are pinned to measurements, not to implementation: the 9-column measured schema, the
12.7% mirror figure, the `data/skills-md/` origin path, the 64-hex (not 16-char) `content_hash`,
the four-value `source` enum, the 5,000-word oversized threshold, and the four coercive
phrases. Two real bugs were found by tests written before the run:

- **`discover()` was not idempotent.** `run_skill_ingestion` calls it itself; the second call
  replayed every row against the already-populated near-dup index and yielded nothing. Symptom:
  `errors: 0`, 2,000 rows admitted, **zero** procedures. A clean silent zero — the worst kind of
  pilot result. Now memoised, with a test that a replayed `discover()` neither refetches nor
  double-counts.
- **The `limit` was enforced in the wrong phase.** It was checked against an `admitted` counter
  the parallel path had not touched yet, so `--limit 1` yielded 2. Now enforced where the count
  happens, with a bounded `FETCH_OVERSAMPLE` scan budget, and a test asserting the parallel path
  produces accounting *identical* to serial.

A third finding came from the real corpus: a fresh `httpx` client per row cost ~2.5 s/fetch and
dominated the pilot. Fixed with a thread-local pooled client; the gate pass over 27,184 rows
dropped from >50 min (unfinished) to 14 min.

## 4. The run

**Gate pass** — `--limit 2000`, 27,184 rows scanned to admit 2,000:

| measure | value |
|---|---|
| rows seen | 27,184 |
| rows fetched from raw | 2,423 |
| **rows admitted** | **2,000** |
| admitted fraction of rows seen | **7.36%** |
| mirror rows | ~6,700 (24.5%) |
| mirror origin recovered | 3,360 |
| content bytes | 11,788,997 (**5,894 B/admitted**) |
| gate wall time | 849 s |
| bytes projected for full 138,133 | 68,361,144 (**~65 MiB**) |

**Compile pass** — `--limit 30`, end to end into the local shard, local `mxbai-embed-large`
embeddings:

| measure | value |
|---|---|
| artifacts seen | 30 |
| outcome: `unchanged` | 15 (already ingested by the earlier 60-item run) |
| outcome: `captured` | **1** |
| outcome: `rejected` | 2 — "extraction produced zero procedures" |
| outcome: `error` | 12 — 11 `ForeignKeyViolation`, 1 `PermanentIdentityConflict` |
| `accepted` (new procedures) | **1** |
| document claims produced | **48** |
| artifact blocks | 46 |
| sources / observations / document evidence | 1 / 1 / 1 |
| independent-step procedures | 3 |
| bytes per admitted | 5,848.5 |
| compile wall time | 926 s for 30 items (**30.9 s/item**) |

**Cost: $0.00.** Local embeddings, no billed extraction calls. Judging cost could not be
measured because no item reached a billed judging path before the two defects below; the
identity-judge cost this step was supposed to optimise remains **unmeasured**.

## 5. What blocked the rest, and what it means

Three findings, all pre-existing and none introduced here. They are why `accepted` is 1 of 30
rather than ~18.

1. **`procedures.verifier_check` was missing on a fully-migrated database** — 54 of 60 artifacts
   died with `UndefinedColumnError`. Migration `125_procedure_verifier_check.sql` exists and
   carries the column, but it had not been applied when I migrated; it arrived from a parallel
   step mid-run. After applying it, those errors went to zero. **Lesson worth keeping:** a lane
   that migrates once at the start of a long run can silently be running against a schema that a
   concurrent lane is still moving. Re-run `migrate.py --status` before interpreting a
   schema error as a code bug.
2. **Goal placement and Procedure placement diverge across shards.** 11 of 30 artifacts failed
   with `reference procedures.achieves_goal_id = <uuid> has no goal, neither locally nor by
   remote reference`. The local control database (K000) holds **371 goals** and 27 procedures,
   while the shard that accepted the Procedures holds none of those goals. A single-shard pilot
   is therefore **not** isolated to that shard for Goals. `admin verify-refs` exists to measure
   exactly this and should be run before any multi-shard ingestion is trusted.
3. **The claim extractor cannot parse the provider's JSON.** `claim_extraction.py:373` calls
   bare `json.loads` on the model response, so a fenced ```json block or a truncated response
   raises `JSONDecodeError` and the chunk silently yields no candidates. Observed repeatedly, and
   the same class of failure hit `skill_extraction` ("LLM response did not parse into the
   expected shape"). This is the same brittleness the debate panel fixed in 2026-08-26 by
   forcing `response_format=json_object`; it has not been applied here. **Recommend a numbered
   board question:** strip fences / request `json_mode` at both extractors.

Also worth recording: **22 of 37 fetched rows were already dead upstream (404)** in the small
sample and ~4%–6% in the larger one. The corpus is 6 months stale and was never updated after
its first 7 minutes. A full run pays to discover dead links unless the dataset is refreshed or
`html_url` liveness is re-checked first.

## 6. Projection to 138,133 — a projection, not a measurement

Admitted fraction **7.36%** → **~10,163 admitted skills**, ~65 MiB of content, from a
27,184-row sample.

Three reasons this number should not be quoted as a yield:

1. The sample is a **prefix of the crawler's row order** (90.0% registry rows first), not a
   uniform random sample. The admitted fraction may not transfer.
2. **The license gate was OFF for this run.** No `GITHUB_TOKEN` is configured, and
   unauthenticated GitHub allows 60 API calls/hour, so the gate could not be run at width. The
   resolver reports `rate_limited` separately from genuinely-unknown licenses precisely so this
   is never silently conflated. arXiv:2604.04323 applied a hard MIT + Apache-2.0 filter and kept
   34,198 of 216,000+ registry entries; a real allowlist pass here will cut the 10,163 further,
   and the size of that cut is **the number this step most wanted and did not measure.**
3. The admitted fraction is computed against **rows scanned**, not rows fetched; dead links and
   unrecoverable mirrors are inside it.

## 7. Risks

- **Screening is triage, not a trust gate.** arXiv:2602.20156 states plainly that this will not
  be solved by filtering. 5 of 37 fetched rows tripped the block-severity screener; the true
  rate on 138k is unknown and probably far higher.
- **This corpus has no script content**, so the SkillJect cross-file hiding attack (2602.14211)
  cannot be checked at all. A skill referencing a bundled script we cannot read is flagged
  (`references_uninspected_script`) but still admitted. A stricter posture — refuse such skills
  — is defensible and is a founder decision, not mine.
- **The compilation yield is 1 procedure per 30 gated skills**, and 12 of those 30 failed on a
  shard-placement defect. Until §5.2 and §5.3 are fixed, ingestion scale is not the bottleneck;
  correctness of the write path is.
- 30.9 s/item compile time makes a 138k run infeasible at this rate: ~5 days of wall time. The
  per-item cost is dominated by per-document LLM claim extraction on a local 8B model, not by
  embedding.

## 8. Full-scale command (needs the user's go-ahead, and two prerequisites)

```powershell
# PREREQUISITE 1 — a GitHub token, or the license gate cannot run at width.
#   60 requests/hour unauthenticated vs ~3,000 distinct origin repositories.
$env:GITHUB_TOKEN = "<token>"

# PREREQUISITE 2 — fix the two write-path defects in §5.2 and §5.3 first.
#   Running 138k rows now would spend real money to produce ~1 procedure per 30 items.

# Gate-only, full width, no writes. Costs nothing but network.
$env:DATABASE_URL       = "postgresql://.../kel_control"
$env:K002_DATABASE_URL  = "postgresql://.../kel_k002"
$env:EMBEDDING_PROVIDER_CHAIN = "local"
python -m app.ingestion.admin shard-weight K000 0    # a single-shard pilot must not fan out
python -m app.ingestion.admin skillmd-import --limit 10000 `
    --shard-dsn-env K002_DATABASE_URL --dry-run `
    --fetch-workers 8 --json ..\.scratch\ingestion\step_3_fullrun_gates.json
```

Projected cost and time, stated honestly:

| | projection |
|---|---|
| gate pass, full corpus | ~55 min wall, **$0**, ~2.4 M raw fetches |
| license lookups | ~3,000 API calls → **needs a token**; 50 min at 5,000/hr |
| compile pass, full corpus | **not projectable** — blocked on §5.2/§5.3, and 30.9 s/item × 10,163 ≈ 3.6 days |
| at the $2/day cap | the gate pass is free; any billed compile pass needs a working embedding provider (**Voyage has no payment method on this account** — it returned "You have not yet added your payment method") |

**Recommended next step is not more scale.** It is: get a `GITHUB_TOKEN`, run the gate-only
command above to finally measure the real license yield, and fix the two write-path defects.
Until the license gate has run at width, the honest statement about this source is *"at most
~10,163 skills are reachable, and the permissive-license subset is unmeasured."*

---

One paragraph: Step 3 researched SkillMD-138K and found the brief's licence assumption wrong for
12.7% of rows (they live in mirror repositories, so the repo's licence is the aggregator's, not
the author's), and that the dataset's own dedup was exact-hash only, leaving 5,573 paths across
20,391 rows duplicated; it then built four new modules — a revision-pinned reader, pure content
gates, a pilot orchestrator and a CLI — that admit 2,000 skills from 27,184 rows scanned (7.36%,
~5,894 B each), recording every rejection reason, the 22-of-37 dead-upstream rate and the
per-origin-license gate, with 71 new proving tests and no regressions. The compile path is blocked
by three pre-existing defects the pilot surfaced rather than caused: an unapplied migration 125,
Goals and Procedures landing on different shards, and two extractors calling bare `json.loads`
on fenced model output — so the licence yield at full width, the identity-judge cost, and any
credible cost projection remain unmeasured, and the recommendation is to fix those three before
spending on scale.
