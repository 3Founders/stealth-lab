# Whole-repo ingestion speed research

Date: 2026-09-24
Scope: `ingest_repo` only. Spec v4, `schema.md`, goal/procedure identity semantics, verification, and sharding placement are unchanged.

## Baseline cost from current code

For `N` selected files the current path performs, per file, one blocking GitHub raw fetch, one content screen, one description LLM call, one Goal embedding, at most one batched Goal judge call, one Procedure embedding, and up to five sequential Procedure judge calls. It performs roughly 21-33 SQL statements on a single-shard deployment and 40-60 with project B and routed shards. At the default `per_domain=10`, one repo is capped at 110 files and all work is serial inside one job. A cold worst-case repo job therefore pays about 110 HTTP requests, 110 description calls, 220 embeddings, up to 660 judge calls, and 2,300-6,600 SQL statements.

Re-ingesting a new commit is currently as expensive as a cold run: the content hash is computed after the description call, there is no commit diff, and the commit-pinned `uri` prevents the existing artifact unique key from firing across commits.

## Stage 1 — discovery and selection

| Current cost | Proposed change | Expected speedup | Correctness risk | Proving test |
|---|---|---|---|---|
| 110 files maximum from 11 independent per-domain caps; no global bound; truncated trees and symlinks are silently accepted; no import/README ranking | Validate `per_domain` and domain names; honor GitHub `truncated`; skip symlink mode `120000`; deterministic shallow-first + import/README relevance ordering; retain the existing per-domain cap | Up to 2x useful-file yield at the same cap; avoids wasted LLM calls on deeper files | Ordering changes which files are ingested; keep deterministic and cap-neutral | selection order, truncated rejection, symlink skip, invalid per-domain rejection, monorepo fixture |
| One 110-file job | Defer splitting into shard jobs | Removes the 110-file retry tail without queue/CLI behavior changes | Splitting changes job keys, queue fairness, ops metrics, and PUBLIC_ONLY_JOB_TYPES outside repo ingestion | Deferred; requires explicit owner approval |

Mature pattern: bounded work sets plus content/entry guards. The repository already has the correct tree guards in `github_corpus.py`; reuse them rather than inventing new rules.

## Stage 2 — fetching

| Current cost | Proposed change | Expected speedup | Correctness risk | Proving test |
|---|---|---|---|---|
| One blocking `httpx` request per file, new client/TLS handshake, blocking DNS and retry sleeps inside the event loop | Run the existing SSRF/retry fetch through `asyncio.to_thread`; use a persistent `httpx.Client` for the default transport; fetch files concurrently only after screening-independent byte acquisition; require token and surface 403/429 distinctly | 3-8x fetch phase; unblocks all worker lanes | Concurrency can reorder output; preserve deterministic commit order and identical per-file URLs/hashes | non-blocking fake, one client reuse, bounded request count, retry on transport, 429 classified |
| Alternative: codeload tarball or partial clone | Reuse the blob-filtered shallow clone already in `github_corpus.py`, or safely extract a commit tarball | 110 requests to 1 clone/archive | Archive traversal/decompression and local temp cleanup are new security surfaces | Deferred behind the simpler async/keep-alive win; no behavior regression while serial fetch is safe |

## Stage 3 — content-addressed cache and incremental commits

| Current cost | Proposed change | Expected speedup | Correctness risk | Proving test |
|---|---|---|---|---|
| SHA-256 is computed after the LLM; no cross-commit reuse; a new commit re-pays all descriptions and identity work | Hash before screening/description; batch query `ingested_artifacts` by content hash; reuse stored description/Procedure result; compute deterministic tree diff from the last successfully ingested commit to the new commit and process changed/added paths only | 10-100x incremental re-ingest; near-zero cost for unchanged blobs | Wrong reuse can attach a description to the wrong path; key reuse by repository+path and verify stored locator | same sha processed once, cross-commit identical blob skipped, changed file processed, same repo+commit idempotent, moved path handled conservatively |

Existing `ingested_artifacts` indexes on `content_hash` and `(source_id, commit)` are the canonical substrate. No schema change is required.

## Stage 4 — description LLM call

| Current cost | Proposed change | Expected speedup | Correctness risk | Proving test |
|---|---|---|---|---|
| One general-compute chat call per file; no structured-output mode; 12k head-only truncation; malformed JSON is reported as abstain | Batch small files into one strict JSON structured-output call; include path/domain/role and bounded excerpts; use cheap configured description model; deterministic templates for lock/config-like domains only when no Procedure semantics are lost | 4-10x fewer description calls; 50-80% lower description cost | Batched parse failure could drop many files; per-item validation, bounded batch size, and item-level retry | N files produce ceil(N/batch) calls, one bad item does not lose the batch, template domain makes zero calls, budgets recorded per item |

## Stage 5 — Goal and Procedure identity

| Current cost | Proposed change | Expected speedup | Correctness risk | Proving test |
|---|---|---|---|---|
| Goal judging is batched; Procedure identity still judges up to five candidates one-by-one; repo goal cache is a plain dict; no similarity floor; near-identical files each resolve | Use `judge_identity_batch("procedure", ...)` preserving fused candidate order and the 0.75 gate; use `GoalResolutionCache`; pre-group byte-identical/near-identical files after description; skip judge when exact/alias hit already exists; defer similarity floor unless it can only fail toward create | 60-80% fewer judge calls; 2N embeddings become O(N/batch) in the two-phase path | Reordering candidates changes identity decisions; grouping can merge distinct Goals | one procedure judge call for five candidates, order preserved, exact match zero calls, cache single-flight, grouped files share one identity decision |
| A distance floor could save more calls | Only allowed if it records `no_candidates` and never returns `same` | 0-1 call/Goal | Changes identity recall and may need a migration | Deferred; explicitly not in the first implementation |

## Stage 6 — persistence

| Current cost | Proposed change | Expected speedup | Correctness risk | Proving test |
|---|---|---|---|---|
| One artifact upsert per file; canonical Goal/Procedure writes remain correctly per-object and routed to shards | Batch only independent `ingested_artifacts` metadata upserts; keep executable blob PUTs separate and bounded; never batch canonical writes across identity/shard invariants | Removes N-1 artifact statements; modest end-to-end gain | Cross-database identity decisions and per-object route/projection locks prevent canonical batching | batched artifact rows, executable bytes only once, same rerun creates nothing new, routed writes unchanged |

## Stage 7 — bounded concurrency

| Current cost | Proposed change | Expected speedup | Correctness risk | Proving test |
|---|---|---|---|---|
| One file at a time; worker has 4 lanes; control pool is `2*lanes+4`; search pool and shard pools are additional; judge semaphore is 4 per process | Add `REPO_FILE_CONCURRENCY` default 2, validated 1-4; `GoalResolutionCache(max_concurrency=F)`; keep output order deterministic; provider budget guard stays per call; heartbeat connection headroom preserved | 2-4x on provider and fetch waits, approximately 2-3x whole-file pipeline | 429 overshoot, pool exhaustion, lease heartbeat starvation | max in-flight bound, deterministic result order, cache single-flight, pool headroom, provider outage does not lose completed files |

A per-file job split is the next step after measured evidence, not part of the first change.

## Stage 8 — license and safety

| Current cost | Proposed change | Expected speedup | Correctness risk | Proving test |
|---|---|---|---|---|
| One default-branch repo SPDX denylist; `NOASSERTION` passes; flag/block findings are collapsed into a boolean skip; no screening audit row | Fetch license at the pinned commit; new repo-only SPDX allowlist verdict with `ALLOW/QUARANTINE/REJECT`; nearest-ancestor `LICENSE*` from the returned tree; quarantine unknown/mixed licenses; record screening decisions; content/prompt-injection screen remains before every description call | No direct speed gain; prevents wasted ingestion and supplies auditable data | Unknown licenses become quarantined rather than silently admitted | permissive allow, NOASSERTION quarantine, copyleft reject, subfolder override, audit rows written, screening precedes LLM |

## Ranked implementation plan

1. Baseline fixture harness plus selection guards and non-blocking fetch.
2. Content-hash short-circuit and incremental commit diff.
3. Batched Procedure identity judging and two-phase/batched embeddings.
4. Batched file processing with `GoalResolutionCache` and bounded concurrency.
5. Batched description calls, strict JSON, and deterministic template domains.
6. Batched artifact metadata persistence.
7. Pinned-commit per-file license allowlist, quarantine, and screening audit.
8. Re-measure, then decide on tarball/partial clone and per-file job splitting from evidence.

## Invariants that must not regress

- License and content screening precede every LLM call.
- Every source locator remains commit-pinned and every selected file carries SHA-256.
- Bytes are stored only for executable source.
- No secret, assertion, or untrusted instruction text is stored.
- Goal and Procedure identity decisions remain recorded and replayable.
- Same repository and commit twice creates nothing new.
- Retries reuse decisions already made.
- Canonical writes remain routed and scope/provenance validated.

## Stop points requiring owner approval

- A schema change, including a new identity decision vocabulary for a similarity floor.
- Splitting a repo into multiple queue jobs, which changes queue fairness, ops metrics, and public-only job-type behavior outside repo ingestion.
- Switching the default transport from GitHub raw files to archive/clone acquisition.
- Changing global screening or license semantics for non-repo ingestion.
