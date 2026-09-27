# Whole-repo ingestion speed — implementation report

Date: 2026-09-24
Branch: `main`
Commits: `34bdd85`, `83d98d2`, `5bfdae6`, `6756339`

## Before

For `N` selected files, one repo job performed:

- `N` blocking raw GitHub fetches, each with a fresh HTTP client/TLS handshake.
- `N` content screens and `N` description LLM calls.
- `N` Goal embeddings, up to `N` batched Goal judge calls, `N` Procedure embeddings, and up to `5N` sequential Procedure judge calls.
- Roughly 21-33 SQL statements per file on one shard, or 40-60 with project B and routed shards.
- One full re-run for every new commit, even when 98%+ of blobs were unchanged.

At the default cap, a cold repo could spend about 110 description calls, 220 embeddings, up to 660 judge calls, and 2,300-6,600 SQL statements, all sequentially in one job.

## After

### Landed stages

1. **Selection and transport**
   - Validated domain names and `per_domain` (`1..50`).
   - Rejects truncated GitHub trees; skips symlinks; quotes commit-pinned paths.
   - Every GitHub HTTP call runs through `asyncio.to_thread`, so one repo no longer freezes sibling worker lanes or lease heartbeats.

2. **Incremental and content-addressed reuse**
   - SHA-256 is computed before screening and description.
   - One batched indexed query skips already-described `repo_file` content with a captured Procedure.
   - An optional base commit diffs git blob SHAs and does not fetch unchanged paths.
   - Same repository and commit remains idempotent.

3. **Bounded concurrency**
   - `REPO_FILE_CONCURRENCY` defaults to 2 and is clamped to 1-4.
   - Uses `GoalResolutionCache`, bounded fetch/process phases, and deterministic selection-order output.
   - Screening still completes before any description call.

4. **Procedure identity batching**
   - Up to five sequential Procedure judge calls become one `judge_identity_batch` call.
   - Candidate order, 0.75 confidence gate, first-qualifying outcome, unavailable handling, and replay are unchanged.

5. **Description and artifact batching**
   - Up to eight small files share one strict-JSON description call with per-item validation.
   - A malformed/abstained item cannot lose the rest of its batch.
   - Deterministic `code_quality` config templates use zero LLM calls when valid.
   - Artifact metadata writes are batched in groups of 64; executable blob PUTs remain per-file.

6. **Repo-local license policy**
   - License lookup is pinned to the ingested commit.
   - Nearest-ancestor in-tree `LICENSE*` overrides repo SPDX.
   - Permissive allowlist allows, copyleft rejects, unknown/`NOASSERTION`/custom licenses quarantine before any LLM call.
   - Screening decisions are persisted; audit-write failure never loses canonical ingestion.

## Fixture measurements

The deterministic fixture uses 2 ms fake fetch latency and 2 ms fake description latency.

| Metric | Before | After |
|---|---:|---:|
| Cold description calls for 12 files | 12 | 2 |
| Cold description calls per file | 1.0 | 0.167 |
| Template-eligible code-quality config | 1 | 0 |
| Procedure judge calls for 5 candidates | up to 5 | 1 |
| Artifact metadata statements for 12 files | 12 | 1 |
| Cold capture calls per file | 1.0 | 1.0 |
| Warm same-commit re-ingest description calls | 12 | 0 |
| Warm same-commit re-ingest capture calls | 12 | 0 |
| Unchanged files on an incremental commit | fetched and processed | not fetched |
| Fetch/process concurrency | 1 | 2 default, 4 ceiling |

The speed fixture gates above 1,000 files/minute with concurrency 4. End-to-end expected speedup is 2-4x for cold provider/fetch waits and roughly 10-100x for incremental re-ingestion, depending on change ratio.

## LLM calls and cost per file

A cold file previously cost one description call plus one Goal judge, one Procedure judge batch, and up to four additional pair judges. The new worst case is one-eighth of a description call for an eight-file batch plus one Procedure judge call, while the Goal judge is unchanged.

For the 12-file fixture this reduces description provider calls by 83%. Five-candidate Procedure identity reduces judge calls by 80%. Warm same-commit ingestion spends zero description and zero capture calls. A warm incremental commit spends zero calls on unchanged blobs.

Exact dollars were not measured locally: production model routing is provider-configured, local provider keys are invalid, and no live or production call was made. The budget ledger still records every provider call. At unchanged per-call prices, the fixture's provider-call share falls by roughly 80-85% cold and to zero for warm unchanged content.

## Verification

- Repo ingestion, speed, license, and description batching: 86 focused tests passed.
- Procedure batch and identity replay: 44 focused tests passed.
- Combined repo/skill/goal/identity selection: 276 passed, 1 unrelated startup-provider-config failure.
- Full offline backend suite: 3,862 passed, 673 skipped, 15 failed in 4m32s.

The 15 full-suite failures are outside the changed paths: MCP adapter evidence shape, auth posture/service-role scans, Goal API test fakes, private-Claim visibility, economy authentication overrides, duplicate migration numbering/target-search migrations, the pre-existing `goal_abstraction.py` tenant-SQL hygiene scan, `ingestion_jobs.py` procedure-embedding recipe scan, and startup provider configuration. This work changed only repo-ingestion code, the repo-only license helper, Procedure identity batching, and the repo-ingestion tests; it did not touch those subsystems.

## Deliberately unchanged

- No schema or frozen-document edits.
- No change to global document/skill screening semantics.
- No change to verification thresholds, `resolved_at`, evidence, or shard placement.
- No canonical Goal/Procedure write batching; identity and routing invariants remain per object.
- No similarity floor that could change identity recall; a floor requires a separately approved decision vocabulary.
- No per-file queue-job split; that changes queue fairness, ops metrics, and public-only job behavior outside repo ingestion.
- No tarball/partial-clone default; archive extraction and local temp handling remain a separately reviewed security surface.
- No deployment or production ingestion run.

## Open questions for the owner

1. Approve a measured A/B of codeload tarball or blob-filtered partial clone against the new concurrent raw fetch path.
2. Approve splitting very large repos into deterministic shard jobs after adding queue fairness and public-only job-type enforcement.
3. Approve a recall-only similarity floor that can only avoid a judge when it will create a new Goal.
4. Decide whether two-phase embedding batching is worth refactoring Goal identity ordering.
5. Run the real P5 throughput batch after the next owner-controlled deploy; Google Cloud billing and deployment remain closed here.
