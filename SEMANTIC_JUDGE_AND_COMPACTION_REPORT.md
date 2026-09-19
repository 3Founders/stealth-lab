# Semantic judge chain + context compaction — audit & report

Repository revision audited: `504cd93` (main), working tree already dirty with unrelated untracked work.
Backend: `backend/`. Tests: mocked providers only, no live calls.

## 1. Audit: where JEV / semantic judging exists

There is **no literal "JEV" provider** in the repo. "NLI/JEV" names the claim-conditioned second-stage judge
(`ApplicabilityJudge` protocol). The operator-hosted `POST /judge-applicability` service (`RemoteHTTPJudge`) is the
only JEV-shaped transport, so that is what "JEV" maps to.

| # | Use case | Input → Output | Provider (before) | Fallback (before) | Deterministic fallback? | Latency | Failure behaviour (before) |
|---|---|---|---|---|---|---|---|
| 1 | Claim-conditioned applicability / NLI (`applicability_judge.py`, `claim_conditioned_retrieval.py`, MCP `search_procedures`, `local_agent/runner.py`) | goal + candidate conditions + relevant Claims → `ApplicabilityJudgment` (4 verdicts) | none by default (`APPLICABILITY_JUDGE_PROVIDER` unset); `mock` / `llm` / `remote_http` | judge `None`/raising → survivors returned in **similarity order**, status `"unavailable"`; per-provider failure → **fabricated UNKNOWN** judgments | **Yes** (embedding-order ranking; `MockJudge` keyword scorer selectable by env). Also a latent bug: `RemoteHTTPJudge` set `last_batch_unavailable` but the orchestrator ignored it, so an outage came back as status `"ok"` with all-UNKNOWN verdicts | interactive (MCP tool) | silently degraded |
| 2 | Claim relation (`claim_equivalence.classify_claim_relation`, called from `claim_extraction.py:605`) | two statements → equivalent/contradicts/related/unrelated | single OpenAI-compatible client (default model `gemma-4-31B-it`) | honest abstention `"unknown"` | No | background ingestion | abstains |
| 3 | Other LLM call sites (`goals.py:195`, `implementation_goals.py:192`, `ingestion_admission.py:468`, `observations.py:333`, `step_grounding.py:199`, `intent_resolution.py:152`, `claim_extraction.py:359`, `trajectory_semantics.py:453`, `procedure_extraction/strategies.py:169`, `skill_extraction/{grounded,ungrounded}.py`) | extraction / adjudication prompts | one OpenAI-compatible client each (General Compute / OpenRouter / local via `extraction_routing.py`) | mostly `client=None` → abstain or degrade (e.g. `_extraction_client()` docstring: grounded extractor "degrades to deterministic_v1") | some degrade to deterministic extraction | background workers | varies |
| 4 | Context management | — | **none existed.** `services/context_compiler.compile_context` is a deterministic token-budget ranker, documented as not wired anywhere | — | — | — | — |

Existing infrastructure reused: `ingestion_jobs` queue (`claim_jobs`, `JOB_HANDLERS`, SKIP LOCKED),
`applicability_judgment_cache`, `run_collaboration` records (BLOCKER/HANDOFF/QUESTION…) for durable state,
`relevant_claims.get_relevant_claims`, `config.settings` (Gemini keys, `local_base_url`/Ollama, `local_judge_model`),
`trace_events` (`tool_call_id`) as the canonical raw trajectory, `context_compiler`'s 4-chars/token estimate.
Rows 2–3 were mapped from grep + docstrings, not read line by line.

## 2. What changed

**Shared abstraction — `app/services/semantic/`**
- `chain.py` `SemanticJudge`: ordered providers, ops `judge_applicability / judge_retention / summarize_context / judge_claim_relation`. Never raises on provider failure; returns `ChainResult(ok, provider, fallback_used, attempts…)`. No deterministic provider exists in this module.
- `providers.py`: `JEVProvider` (wraps `RemoteHTTPJudge`; only ops listed in `JEV_CAPABILITIES`, **never summaries**), `OpenAICompatProvider` (Gemini via its OpenAI-compatible endpoint with key rotation; Gemma via the existing local server). Unconfigured providers are skipped, not attempted.
- `errors.py`: TRANSIENT (timeout, 429/408/5xx, network; unknown errors default here) / PERMANENT (401/403/404/400, bad key, missing model → next provider immediately) / UNSUPPORTED (skipped).
- `policy.py`: `RetryPolicy` — 2 attempts per provider, exponential backoff + jitter, cap, per-call timeout, optional wall-clock `deadline_s` for interactive calls; `SemanticMetrics`.
- `applicability.py`: `ChainedApplicabilityJudge` implements the existing `ApplicabilityJudge` protocol.
- `jobs.py`: requeue on the **existing** `ingestion_jobs` queue (`semantic_judgment`, `compact_context`), dedup, backoff via new `run_after`, round accounting, exhaustion cooldown.

**Old judges made strict** (`applicability_judge.py`): `LLMJudge` / `RemoteHTTPJudge` now **raise** on outage or malformed reply instead of returning fabricated UNKNOWN. Judge output citing claim ids not in the input is rejected. `default_judge_from_env()` returns the chain; `mock` is refused when `settings.environment == PRODUCTION`. Cache put/lookup now key on the chain identity (previously a chain would never have hit its own cache).

**NLI/retrieval contract** (`claim_conditioned_retrieval.py`):
- all providers fail → `PENDING_SEMANTIC_JUDGMENT`, `candidates=[]`, uncached work requeued, `pending_job_id` returned; judged candidates stay cached so the retry completes.
- retries exhausted → `SEMANTIC_JUDGMENT_UNAVAILABLE` (an identical request within 15 min does not restart the loop).
- no judge configured → `SEMANTIC_JUDGMENT_UNAVAILABLE`.
- `use_claims=False` (caller's explicit opt-out) → similarity order labelled `not_requested`.
- `UNKNOWN` is only ever a model verdict.
- MCP `search_procedures` returns `contextual_judgment_status`, `pending_job_id`, `detail`; `runner.py` notes the state and continues ad-hoc.

**Compaction — `app/services/context_compaction/`**
- `normalize.py`: OpenAI-style, Anthropic-style, and `trace_events` inputs → `ContextItem`s; tool call+result grouped into one `Unit` by `call_id`.
- `pinning.py`: deterministic lifecycle pins. Hard (system, first/latest user message, user constraints, explicit) → always verbatim. Soft (unresolved failures, blocker references, recent window) → never DROP/REFERENCE_ONLY.
- `engine.py`: pins → batched retention judgment (one request per ≤20 units, state = concise relevant durable state) → guards → separate summary call for `KEEP_COMPACT` → derived view. Guards only make decisions **safer**: hallucinated `durable_refs` (not in real state) → COMPACT; failed results never DROP (negative-knowledge/loop prevention); low confidence never DROP/REFERENCE; a summary that loses the read path or failing test ids, or isn't smaller, reverts to verbatim.
- Providers all down → `skipped_unavailable`: last valid compacted view + every uncovered item verbatim (or everything verbatim), retry job queued. **No rule-based dropping exists.**
- Cache key = unit content hash + goal/node version + durable-state hash + chain identity (provider order + models) + prompt version + pin level.
- Triggers (`should_compact`): before-model-call over token threshold, large tool result, node completed, before handoff, before session continuation, explicit; tiny contexts never compact.
- `durable.py`: `build_stealth_state` (open blockers/questions/handoff derived like `pipe_format._collab_summary`, bounded relevant Claims), `prepare_handoff` (compact → HANDOFF record carrying the compact view → optional `run.md` refresh callback), `resume_context` (agent B: durable run state + last valid view, no transcript).
- Raw items/trajectory are never mutated; the view is stored in `context_compaction_views` (migration 93) and recomputable.

**Other**: `claim_equivalence.classify_claim_relation_via_chain` (returns `status="unavailable"`, never a guess); `ingestion_jobs.claim_jobs` honours `run_after`; job handlers registered.

## 2b. Trace-ingestion wiring (the primary integration)

`trajectory_semantics.extract_trajectory_semantics` (called by the MCP server, `api/trajectories.py`, `scripts/run_trajectory_ingestion.py`)
now runs raw `trace_events` through `app/services/trace_compaction.py` before building the extraction prompt that produces
Goals / Claims / Implementations / candidate Procedures.

- `items_from_trace_events` turns each trace row into a tool_call+result pair carrying the real trace event id (`raw_ref`).
- Durable state for the retention judge = episode goal + already-persisted Claims retrieved with the episode owner's `AccessScope`
  (never `unrestricted`), so persisted facts can become `KEEP_REFERENCE_ONLY` pointers.
- Every prompt line maps back to the real trace event id(s) (`index_refs`); extracted objects still cite real evidence. Dropped units have no index.
- **Not size-gated**: every episode goes through the judge, because short episodes also carry junk (progress narration, "waiting for another agent", "launched background task", heartbeats). The retention prompt (v2) names this junk as DROP. Kill switch only: `TRACE_INGESTION_COMPACTION_ENABLED=false`.
- Ingestion uses **no recency-window pin** (that pin protects a live agent's current step; offline it would shield every unit of a short episode from being dropped). Unresolved failures and hard pins still apply.
- Verbatim units render with the legacy line format.
- Cost: one batched retention call per episode (+ a summary call if anything is KEEP_COMPACT).
- Test isolation: `tests/conftest.py` autouse guard makes `SemanticJudge.from_settings()` an empty chain under pytest, so no test can reach a live provider with keys from `.env`.
- Providers down: under the cap the full legacy prompt is used (nothing dropped); over the cap it raises `ExtractionTransientFailure`
  instead of silently truncating events 201+, so `resume_failed_extraction_jobs` retries later.
- Behaviour change to be aware of: previously a >200-event trajectory was silently cut to its first 200 events; it is now compacted (or retried).
- Not covered: `handle_consolidate_local_episode` (local run events) feeds an already one-line-per-observation rendering, so it was left alone.
- Test: `tests/test_trace_ingestion_compaction_offline.py`.

## 3. Configuration (all in `app/config.py`, documented in `backend/.env.example`)
`SEMANTIC_PROVIDER_PRIMARY=jev`, `SEMANTIC_PROVIDER_FALLBACKS=gemini,gemma`, `JEV_BASE_URL`, `JEV_API_KEY`, `JEV_CAPABILITIES=applicability`,
`GEMINI_API_KEY[S]` (existing), `SEMANTIC_GEMINI_MODEL=gemini-2.5-flash`, `LOCAL_MODEL_PROVIDER=ollama`, `LOCAL_MODEL_NAME` (falls back to `LOCAL_JUDGE_MODEL`, only when `USE_LOCAL_MODELS` or set), `SEMANTIC_PROVIDER_TIMEOUT_MS=15000`,
`SEMANTIC_PROVIDER_RETRIES=2`, `SEMANTIC_JOB_MAX_RETRIES=3`, `SEMANTIC_BACKOFF_BASE_MS/MAX_MS`, `SEMANTIC_REQUEUE_DELAY_SECONDS`,
`CONTEXT_COMPACTION_TOKEN_THRESHOLD`, `CONTEXT_COMPACTION_LARGE_RESULT_TOKENS`. Secrets are read from env only; nothing is written to `.stealth`.

## 4. Files
New: `backend/app/services/semantic/{__init__,errors,policy,prompts,providers,chain,applicability,jobs}.py`,
`backend/app/services/context_compaction/{__init__,models,normalize,pinning,engine,durable}.py`,
`backend/db/93_semantic_judge_queue_and_context_views.sql`,
`backend/tests/{semantic_fakes,test_semantic_fallback_policy_offline,test_context_compaction_offline}.py`.
Modified: `app/config.py`, `.env.example`, `app/services/applicability_judge.py`, `claim_conditioned_retrieval.py`, `claim_equivalence.py`, `ingestion_jobs.py`, `app/mcp_server/server.py`, `app/local_agent/runner.py`; tests `test_applicability_judge_offline.py`, `test_claim_conditioned_retrieval_offline.py`, `test_mcp_six_tool_surface_offline.py` (rewritten to assert the new no-silent-fallback contract).

## 5. Tests
Required cases: A (JEV success, no fallback), B (→Gemini), C (→Gemma), D (+D2 last-valid-view) compaction retains context + retry queued, E NLI → PENDING + queued + `MockJudge` never invoked, F retry exhaustion → UNAVAILABLE + no restart loop, G UNKNOWN ≠ INAPPLICABLE, H unresolved failure retained, I stale persisted read → reference-only, J failed attempts kept, K batching (46 units → 3 requests), L handoff/resume, plus permanent-error skip, unsupported-op skip, deadline, backoff bounds, error classification, chain config order, pairing/no orphans, hard pins, summary literal-loss fallback, cache hit/invalidation, trigger policy, Anthropic-style normalization, provider-down handoff.

Results: new + touched suites **162 passed**. Full backend suite (excluding `test_document_skills.py`, which fails to *collect* — missing `app.skills.excel_generation`, unrelated): **3314 passed, 568 skipped, 1 failed**. The failure, `test_adapters_e2e::test_mcp_tool_adapter_reports_a_real_tool_error`, passes in isolation both with and without these changes (order-dependent flake, not caused by this work).

## 6. Remaining limitations / needs live smoke-testing
- **Wired into one loop only, opt-in:** `Agent(..., compactor=MessageCompactor(...))` in `app/execution/coding_agent.py` (`context_compaction/harness.py`; test `test_agent_compaction_e2e_offline.py`). Default `compactor=None` leaves the loop unchanged. `LocalAgentRunner` has no LLM message list of its own, so it is not the hook. No MCP tool exposes compaction (that would change the "six tool surface" contract); handoff helpers are still library code. The agent's default state has only the goal (no DB), so `KEEP_REFERENCE_ONLY` only works if the caller supplies a `StealthState` with durable refs.
- **Migration 93 and every SQL statement in `semantic/jobs.py` / `engine.py` were exercised only against in-memory fakes**, not a real Postgres. Needs one run against a real DB (including `claim_jobs` with `run_after`, `recently_exhausted`, the `payload || jsonb_build_object` root-id update).
- **JEV contract for retention/claim-relation (`/judge-retention`, `/judge-claim-relation`) is my assumption**, disabled by default (`JEV_CAPABILITIES=applicability`); only `/judge-applicability` is a pre-existing contract.
- Live smoke tests needed: JEV endpoint; Gemini OpenAI-compatible endpoint incl. free-tier 429 → key rotation; Ollama Gemma model name; real retention/summary prompt quality (mock "models" test the plumbing and guards, not judgment quality).
- Row 2–3 call sites (`claim_extraction`, `goals`, `ingestion_admission`, `observations`, extraction strategies…) still use their single-client abstain/degrade behaviour; only the chain function for claim relation exists. Migrating them (e.g. a `ChainClient` exposing `chat.completions.create`) is follow-up work; those that degrade to deterministic extraction were not audited line by line.
- Metrics are in-process (`semantic.policy.METRICS`, per-judge `judge.metrics`); nothing exports them. Cost estimates and token *actuals* are not tracked (4-chars/token estimate only).
- Retry payload for compaction embeds the items (capped at 1 MB; larger contexts are not requeued, only recorded).
- `requeue`/round loop relies on a worker calling `process_pending_jobs`; nothing here starts one.
