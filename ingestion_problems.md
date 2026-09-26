# Ingestion problems — handoff (2026-09-24)

## Status (updated 2026-09-24, end of day)

| Item | Status | Commit |
|---|---|---|
| P1 batched identity judge, P2 no judge for step goals, P3 replay-safe job keys, P4 bounded parallel goal prefetch | fixed | `791524f` (core-a) |
| P5 measurement | 300-doc batch (`p5-measure-2026-09-24`) queued; runs after the next deploy | — |
| P6 extraction/claim calls in `llm_spend` | fixed | `b23a7e4` |
| **P7 (new)** sync OpenAI client called inside async extraction froze the worker event loop (other lanes + lease renewals stalled for the whole call) | fixed (`asyncio.to_thread`) | `b23a7e4` |
| **P8 (new)** license gate never fired for GitHub documents (adapter didn't fetch the license; key mismatch `license` vs `spdx_id`) | fixed | `a6c905d` |
| S1 queue documents from ops CLI | fixed: `enqueue document`, `stealth-ops ingest` auto-detects | `8088019` |
| S2 web pages/PDFs by URL in prod | fixed: `UrlFetchAdapter`, SSRF-checked per hop | `edf059c` |
| S3 CI/infra/scripts files in prod | covered by the repo ingester (`ingest_repo` job) | `758166f` |
| S5 reference files copied to object storage | fixed: bytes stored only for `executable_source` | `758166f` |
| S4 vision PDF, S6 shards, S7 local env | open | — |

None of the commits above are deployed yet; the Cloud Run worker is still on image `8d39552`.

Measured throughput is ~11–17 docs/hour on 16 Cloud Run lanes. Single-procedure documents
finish in 3–8 s; multi-procedure documents (12–17 procedures, e.g. anthropics/skills xlsx/docx
packages) take 800–1000 s and often retry 4–5 times. Almost all of that time is **goal identity
resolution**, not fetching or extraction. Line numbers are as of commit `da98c51`.

## Evidence (production Neon, last 30 h, queried 2026-09-23)

- `llm_spend`: **2,046** `judge:identity` calls (google/gemini-2.5-flash, ~900 input tokens each)
  vs **~360** rows in `identity_decisions` → ~5.7 judge calls per decision.
- `identity_decisions`: average candidates per decision = **5.0** (= `DEFAULT_JUDGE_TOP_N`).
- `identity_decisions.job_id` is NULL for every recent ingestion job (0 decisions linked to jobs 622–646).
- Jobs 629, 640, 643, 645 needed 4–5 attempts; 629 died permanently with
  `SemanticJudgmentUnavailable ... 5 candidate(s) unresolved`.

Reproduce: see the queries at the bottom.

---

## P1 — Identity judge makes one sequential LLM call per candidate (biggest)

- **Where:** `backend/app/services/identity_resolution.py:327-328` (`resolve_goal_identity`):
  `for cand in candidates: res = await judge.judge_identity("goal", cand_text, cand.text)`.
  Same pattern at `identity_resolution.py:478` (merge/relink path). `top_n` defaults to
  `DEFAULT_JUDGE_TOP_N = 5` (`identity_resolution.py:48`).
- **Effect:** up to 5 back-to-back LLM calls per goal resolution. A 15-procedure document needs
  ~30+ resolutions → 150+ serial judge calls → the 800–1000 s jobs.
- **Fix:** judge all candidates in ONE call (the prompt lists candidates 1..N, the model returns a
  relation+confidence per candidate), keeping the same verdict semantics (`same` ≥
  `SAME_MIN_CONFIDENCE` → reuse; low-confidence `same` → `related`; narrower/broader/related
  relations recorded). The per-pair method lives on `SemanticJudge.judge_identity` in
  `backend/app/services/semantic/` — add a batched method beside it and keep the per-pair one for
  other callers. Optional extra: skip the judge entirely when the best vector candidate is far
  below a similarity floor.
- **Acceptance:** offline test proving one judge call per resolution for N candidates; the
  existing identity tests (`tests/test_goal_identity_e2e.py`, `tests/test_goals_offline.py`) stay
  green.

## P2 — Step-level goals get full LLM judging despite being designed not to

- **Where:** `backend/app/services/skill_ingestion.py:2494-2517` (inside `compile_skill_artifact`).
  The comment ("Cost tradeoff, disclosed") says step goals pass `embedder` but NOT `client`, so
  tier-5 LLM adjudication is skipped. Since identity moved to `judge`, passing no client no longer
  skips anything: `find_or_create_goal` (`backend/app/services/goals.py:328-332`) calls
  `resolve_goal_identity(judge=None)`, which falls back to `default_judge()`
  (`identity_resolution.py:119`) and judges every step goal with up to 5 LLM calls.
- **Fix:** give step-goal resolution an explicit no-LLM mode (exact/alias/embedding tiers only),
  e.g. a `judge_mode="none"` / sentinel judge passed from the step call site; update the stale
  comment.
- **Acceptance:** offline test that step-goal resolution makes zero judge calls; the
  procedure-level goal still judges.

## P3 — One judge failure restarts the whole document; retries redo all finished work

- **Where:**
  - `identity_resolution.py:329-333` raises `SemanticJudgmentUnavailable` on the first failed
    candidate call; the job fails and is retried from scratch.
  - Replay support already exists: `resolve_goal_identity` reuses a prior decision by
    `idempotency_key` (`identity_resolution.py:293-301`), and `capture_procedure` accepts
    `identity_job_id` / `identity_idempotency_key` (`backend/app/services/procedures.py:153`, used
    at `:270`, `:289`) — but nothing passes them:
    - `handle_ingest_skill_package` (`backend/app/services/ingestion_jobs.py:255`, call at `:308`)
      and `handle_ingest_document` (`ingestion_jobs.py:334`, call at `:397`) never pass a job id
      into `compile_skill_artifact` (`skill_ingestion.py:2133`).
    - `compile_skill_artifact`'s `capture_procedure` calls (`skill_ingestion.py:2545`, plus the
      derived-procedure helpers at `:1978`, `:2054`, `:2105`) and the direct goal calls
      (`:2514`, `:2680`) never pass `job_id` / `idempotency_key`.
- **Effect:** a retry re-runs extraction plus every judge call already made; transient Google
  429/503s turn into 4–5× the cost and latency.
- **Fix:** thread the job id from the handlers into `compile_skill_artifact`, and derive a stable
  per-goal idempotency key (e.g. `f"{job_id}:{normalized_goal}"`) down to every
  `find_or_create_goal` / `capture_procedure` call so a retry reuses completed decisions. Consider
  retrying a single failed judge call with backoff before failing the whole job.
- **Acceptance:** offline test showing a second run with the same job id reuses prior decisions
  (zero new judge calls for already-decided goals); `identity_decisions.job_id` populated in
  production.

## P4 — Everything inside one document is serial

- **Where:** `skill_ingestion.py:2482` (`for i, proc in enumerate(extracted.procedures)`), the
  step loop inside it (`:2514`), and the standalone-goals loop (`:2680`). Each resolution waits for
  the previous one.
- **Fix:** resolve the goals for a document concurrently under a bounded semaphore (start ~4). Safe
  at the DB level: `find_or_create_goal` already handles insert races (`ON CONFLICT` on
  `goal_names` and a `UniqueViolationError` retry in `goals.py`). Keep deterministic ordering of
  outputs (`procedure_ids[0]` is returned as the outcome's primary id; `i == 0` does one-time
  work). A per-document goal cache already exists (`find_or_create_goal_cached`, `goals.py`) —
  reuse it and guard against two concurrent resolutions of the same key.
- **Do after P1–P3**, then re-measure.

## P5 — Throughput was measured on a batch too small to mean anything

- 19 documents over 16 lanes: most lanes sat idle while 3–4 slow documents finished, so "11
  docs/hour" is mostly tail latency. Re-measure after P1–P3 with a queue of several hundred
  documents.

## P6 — Observability gaps

- Extraction LLM calls are not recorded in `llm_spend`:
  `backend/app/services/skill_extraction/ungrounded.py:266`,
  `backend/app/services/skill_extraction/grounded.py:337`, and claim extraction
  `backend/app/services/claim_extraction.py:359` call `client.chat.completions.create` directly.
  Only judge and embedding calls reach the ledger (`backend/app/services/governance.py:573`), so
  extraction latency and cost are invisible and the daily budget cap undercounts.
- `identity_decisions.job_id` is always NULL for ingestion (fixed by P3).

---

## Secondary problems (not throughput, but block volume ingestion)

| # | Problem | Where |
|---|---|---|
| S1 | No way to queue non-SKILL.md documents from the ops CLI; `ingest_document` jobs had to be inserted by hand (jobs 641–646). | `scripts/ops/stealth_ops/ingest.py`; queue helper pattern: `enqueue_skill_package_jobs` in `ingestion_jobs.py` |
| S2 | Production can only fetch GitHub-hosted documents. The format adapters (HTML/PDF/DOCX/Markdown) read only `local_path`/`raw_bytes`, and a JSON job payload can't carry bytes, so web pages (blog postmortems) and web PDFs can't be ingested in prod. Needs a URL-fetch adapter or offloading bytes to object storage in the payload. | `ingestion_jobs.py:334` (`handle_ingest_document`); `backend/app/services/ingestion_sources/document_adapters/*.py` |
| S3 | The CI-workflow / AGENTS.md / runbook adapters read a local checkout only, so they can't run as production jobs. | `backend/app/services/ingestion_sources/repo_procedural.py` (`LocalDir*Source`) |
| S4 | PDF extraction is text-layer only (`pdfplumber`); diagrams, scans and image content are lost. Planned: render pages (`pypdfium2` is installed) and send image + text to a vision model. | `document_adapters/pdf_adapter.py` |
| S5 | Non-executable reference files (style/design/docs) are copied to object storage like executable scripts. Only executables need the hashed copy (the executor refuses to run without it: `backend/app/execution/step_binding.py:115-148`); references should be commit-pinned URL + hash + description only. | `skill_ingestion.py:1896` (`_preserve_script_artifact`), `:1921` (`store_blob`) |
| S6 | Only one Neon shard (`K000`) exists; no body shards. Design agreed in chat: K000 = goal index, K001+ = procedures/claims placed per goal, grown by the existing `stealth-ops capacity --apply` rollover. | `backend/app/services/shards.py` (`choose_shard`, `choose_child_shard`), `goals.py` goal placement, `scripts/ops/stealth_ops/shardops.py` |
| S7 | Local runs are unreliable: from this machine Python cannot reach `raw.githubusercontent.com` (TLS "wrong version number" — network interception) and 6 of 7 local LLM keys are invalid. Run ingestion on Cloud Run, not locally. | local `backend/.env` |

## Deploy notes

- Commit `da98c51` (content-based names for derived procedures) is not deployed yet.
- `stealth-ops deploy-workers` builds from the **local working tree**, not git HEAD. The tree
  contains a teammate's uncommitted edits (`backend/app/config.py`,
  `backend/app/services/ingestion_jobs.py`, `backend/app/services/semantic/providers.py`,
  `deploy/ingestion/cloudrun/job.yaml`, others); make sure the tree has your changes and nothing
  broken before deploying.
- Worker job: `stealth-ingest-worker`, region `asia-south1`. `INGEST_JOB_TIMEOUT_SECONDS` is 3000.

## Rules that apply (CLAUDE.md)

Spec v4 and `schema.md` are frozen; no backfills (existing rows keep old names/placement); proving
tests ship in the same change; paste pytest counts in commit messages. Offline suite = run with
`DATABASE_URL` unset. As of `8535157`, the full backend suite had 8–9 pre-existing unrelated
failures (auth / claim-graph / identity-posture tests, plus one order-dependent flaky test);
don't count those against these fixes.

## Evidence queries

```sql
-- judge calls vs decisions (last 30 h)
SELECT operation, model, count(*), sum(input_tokens) FROM llm_spend
WHERE occurred_at > now() - interval '30 hours' GROUP BY 1, 2 ORDER BY 3 DESC;
SELECT object_type, decision, count(*),
       avg(jsonb_array_length(coalesce(candidates, '[]'::jsonb))) AS avg_candidates
FROM identity_decisions WHERE created_at > now() - interval '30 hours' GROUP BY 1, 2;
-- per-job duration and attempts
SELECT id, job_type, attempts, extract(epoch FROM (completed_at - claimed_at))::int AS dur_s
FROM ingestion_jobs WHERE status = 'done' ORDER BY id DESC LIMIT 30;
```
