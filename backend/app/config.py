"""
Central configuration (MVP plan, Section 12).

All secret/environment access routes through this object rather than
scattered os.environ[] calls -- one seam to change when secrets move from
env vars to a dedicated manager.

Secrets are Optional with None defaults so importing any module for tests
or offline work doesn't require a populated .env. Call settings.require()
at the point of actual use instead, which fails loudly with a useful
message rather than an opaque None.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/, i.e. the directory that actually holds .env.
#
# env_file was a bare ".env", which pydantic-settings resolves against the
# CURRENT WORKING DIRECTORY, not this package. That silently produced a
# fully-default Settings for anything launched from the repo root -- every
# secret None, every require() failing with "set it in your .env" while the
# populated .env sat one directory away. The failure surfaces only at the
# first API call, which for a long ingestion job is after the expensive part
# has already run.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # Later entries win, so a .env in the working directory still overrides
    # the packaged one -- the previous behaviour, kept.
    model_config = SettingsConfigDict(
        env_file=(_BACKEND_ROOT / ".env", ".env"), extra="ignore"
    )

    database_url: Optional[str] = None

    # Heterogeneous debate panel (Section 7): three distinct model families.
    anthropic_api_key: Optional[str] = None
    fireworks_api_key: Optional[str] = None   # Kimi K3
    openai_api_key: Optional[str] = None      # third seat
    groq_api_key: Optional[str] = None        # Experiment 4 SLM arm -- OpenAI-compatible

    # Judge (Section 8.1 / Nirnaya) must be independent of the panel
    # (enforce_independence checks model family). The panel already uses
    # all three of the above families, so the judge needs a fourth,
    # distinct provider -- Gemini via its OpenAI-compatible endpoint.
    google_api_key: Optional[str] = None

    voyage_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    # Comma-separated list of Gemini API keys, rotated in order when a key
    # hits its quota (429). Two free-tier keys double the effective TPM
    # ceiling -- task_008 died in phaseJ precisely because four concurrent
    # simulations exhausted a single key's 30K tokens/minute mid-sweep.
    gemini_api_keys: Optional[str] = None

    # --- Local model provider (development / unblocked testing) ---
    # Any OpenAI-compatible local server (Ollama, LM Studio, llama.cpp's
    # server, vLLM). This exists so the full loop can be exercised with
    # real model output when paid API access isn't available -- the
    # pipeline's genuine unknowns (does a real model emit parseable JSON?
    # does it cite in the expected format?) can't be answered by mocks.
    #
    # Not a production substitute: small local models reason materially
    # worse than frontier ones, so a debate that converges locally says
    # the plumbing works, not that the panel's judgement is sound.
    use_local_models: bool = False
    local_base_url: str = "http://localhost:11434/v1"
    local_panel_models: str = "llama3.2,qwen2.5,mistral"
    local_judge_model: str = "gemma2"
    local_embedding_model: str = "mxbai-embed-large"  # 1024-dim, matches the schema

    # --- General Compute (hosted open-weight, OpenAI-compatible) ---
    # An alternative to both the paid closed-model roster and local
    # Ollama: real hosted inference, open-weight models, materially
    # cheaper than the closed frontier roster. Model names are filled in
    # per account (check https://docs.generalcompute.com for the current
    # catalog) rather than hardcoded, since availability changes.
    use_general_compute: bool = False
    general_compute_api_key: Optional[str] = None
    # Confirmed via General Compute's own docs (Vercel AI SDK example),
    # not their homepage marketing snippet, which omits the /v1 and is
    # wrong. Get this wrong and every call 404s before it ever reaches a
    # model.
    general_compute_base_url: str = "https://api.generalcompute.com/v1"
    # Comma-separated, must be genuinely distinct model families -- the
    # heterogeneity check enforces this at construction, not just at
    # request time, so a bad configuration fails loudly before spending
    # anything.
    general_compute_panel_models: str = ""
    general_compute_judge_model: str = ""

    # --- OpenRouter (WAVE-3 debate panel wiring) ---
    # Fourth provider posture next to local / General Compute / the paid
    # closed roster: ONE OpenRouter account serves all four debate seats
    # from OPENROUTER_API_KEY in backend/.env, so scan -> debate -> approve
    # runs end-to-end locally without any other vendor credential.
    # The defaults below are the CHEAP four-family roster PROBED LIVE on
    # the founder account (2026-08-26): every slug answered a tiny
    # completion before being pinned here. Two lessons from that probe,
    # both now load-bearing knowledge:
    #   - catalog listing != endpoint availability: deepseek-chat-v3.2 was
    #     listed by GET /models yet rejected as "not a valid model ID" on
    #     chat; anthropic/claude-3-5-haiku had simply been retired.
    #   - ox-alpha is a REASONING model: give it real max_tokens or it
    #     spends the whole budget thinking and returns empty content
    #     (app/debate/panel.py's OpenRouterAgent passes max_tokens through;
    #     seats default to 2000, ample for this).
    # A slug the account stops serving degrades to a RECORDED skipped turn
    # with its HTTP status in the failure trail -- never silent. Override
    # OPENROUTER_PANEL_MODELS / OPENROUTER_JUDGE_MODEL to taste
    # (https://openrouter.ai/models). Backoff/retry constants live in
    # app/debate/panel.py, not here, so tests prove them retunable without
    # rebuilding Settings.
    use_openrouter: bool = False
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Comma-separated; must be genuinely distinct model families -- the
    # heterogeneity check enforces this at construction, before any spend.
    openrouter_panel_models: str = (
        "ox-alpha,openai/gpt-4o-mini,anthropic/claude-haiku-4.5"
    )
    openrouter_judge_model: str = "google/gemini-2.5-flash"

    # --- Agent execution: file upload/output handling ---
    agent_upload_dir: str = "/tmp/agent_uploads"
    agent_output_dir: str = "/tmp/agent_outputs"
    max_upload_bytes: int = 20 * 1024 * 1024  # 20MB per file -- generous for a
                                                # scanned medical report, still a
                                                # real bound against resource exhaustion

    # Model IDs, overridable without touching code.
    anthropic_model: str = "claude-sonnet-4-6"
    fireworks_model: str = "accounts/fireworks/models/kimi-k3"
    openai_model: str = "gpt-4.1"
    gemini_model: str = "gemini-2.5-pro"
    groq_model: str = "qwen/qwen3.6-27b"  # Experiment 4 SLM arm -- dense, real,
                                            # hosted; confirmed the strongest coding
                                            # option Groq currently serves (the true
                                            # sparse Qwen3.6-35B-A3B isn't available
                                            # via any pay-per-token hosted API as of
                                            # this writing, only self-hosted/on-demand)
    groq_base_url: str = "https://api.groq.com/openai/v1"
    experiment_4_llm_model: str = "deepseek-v3.2"  # switched from gpt-oss-120b:
                                            # real SWE-Bench Verified data shows
                                            # DeepSeek V3.2 clearly ahead on
                                            # practical coding (67.8% vs 62.4%),
                                            # the benchmark closest in shape to
                                            # what this experiment actually tests.
                                            # gpt-oss-120b led on unrelated
                                            # benchmarks (CodeForces, GPQA) but
                                            # those aren't what we're measuring.
                                            # UNCERTAIN: General Compute's exact
                                            # model string wasn't directly
                                            # confirmed from their own docs --
                                            # "deepseek-v3.2" is the most common
                                            # naming convention across other
                                            # providers, but if this 400s, the
                                            # error should list valid model names;
                                            # update this field with whatever that
                                            # says. Still a frontier-ADJACENT
                                            # open-weight model, not a true
                                            # closed-lab frontier system -- same
                                            # honest limit as gpt-oss-120b had.
    embedding_model: str = "voyage-3-large"
    embedding_dimension: int = 1024  # must match VECTOR(n) in 01_ontology.sql

    # Provider chain for embeddings -- tried in order, first success wins.
    # "gemini" needs GEMINI_API_KEY; "voyage" needs VOYAGE_API_KEY. A
    # provider that fails (missing key, 429, outage) falls through to the
    # next instead of starving callers -- the exact failure mode that took
    # down substrate_search mid-phaseH when Voyage's 3-RPM free tier ran dry.
    embedding_provider_chain: str = "gemini,voyage"
    gemini_embedding_model: str = "gemini-embedding-001"
    # Shared cross-process TPM budget for Gemini embed calls -- all callers
    # (tau2 sweeps, backfills, smoke tests) draw from one rolling-minute
    # window kept in backend/logs/gemini_bucket.json. Default leaves ~17%
    # headroom under the free tier's 30K tokens/min ceiling. 0 disables.
    embed_tpm_budget: int = 25000
    gemini_usage_log: bool = True

    # Debate parameters (Section 7).
    max_debate_rounds: int = 5
    min_supporters_for_eval: int = 2

    # Layer 1 eval gate (Section 8.1).
    groundedness_threshold: float = 0.5

    # --- V2 access control ---
    # Private visibility is off until real authentication exists. These
    # two flags are checked together at startup (app/api/deps.py):
    # enabling private content while identity is still an unverified
    # header would expose private data to anyone who sets it.
    private_visibility_enabled: bool = False
    real_auth_enabled: bool = False

    # --- Band 2.9 identity gate (services/authn.py) ---
    # OIDC-only authN: issuer + audience configure token validation; the
    # JWKS URL defaults to <issuer>/.well-known/jwks.json. Both required
    # before any exposure flag may turn on -- assert_boot_posture refuses
    # to boot on half-enabled identity. No user management ships with
    # this: subjects arrive pre-provisioned from the IdP.
    oidc_issuer: Optional[str] = None
    oidc_audience: Optional[str] = None
    oidc_jwks_url: Optional[str] = None
    multi_user_exposure_enabled: bool = False

    # --- MCP server deployment-mode guard (backend/app/mcp_server/server.py) ---
    # OidcAwareTokenVerifier's shared-STEALTHLAB_MCP_TOKEN fallback (no
    # per-caller .subject) is fine for local/single-user dev -- it is
    # exactly what runs when OIDC_ISSUER/OIDC_AUDIENCE are unset. Nothing
    # about that fallback stops a real hosted/multi-user deployment from
    # running on it by accident, silently losing per-user attribution.
    # "single_user" (default, unchanged behavior) leaves the shared-token
    # fallback allowed regardless of OIDC config. "shared" is the explicit
    # hosted/multi-user opt-in: assert_deployment_mode_posture (app/
    # services/authn.py, called at MCP server import time) refuses to boot
    # in this mode unless BOTH OIDC_ISSUER and OIDC_AUDIENCE are set --
    # same "refuse to boot on a bad combination" discipline as
    # assert_boot_posture (REST app, Band 2.9) and
    # tasks_extension.assert_single_worker. Default "single_user" keeps
    # every existing dev setup working unchanged -- this flag must be set
    # explicitly to change behavior, never inferred.
    deployment_mode: str = "single_user"

    # --- V2 governance ---
    # On by default: an unprotected public endpoint that spends money per
    # call is the kind of thing that should require deliberate opt-out,
    # not deliberate opt-in.
    governance_enabled: bool = True
    daily_llm_budget_usd: float = 10.0
    per_viewer_daily_budget_usd: float = 1.0

    # Single-tenant placeholder (Section 12 auth seam).
    default_tenant_id: str = "00000000-0000-0000-0000-000000000001"

    # --- Automatic ingestion loop (app/services/ingestion_scheduler.py) ---
    # The in-process scheduler that turns normal agent work into procedure
    # candidates on a timer, so nothing has to remember to trigger it.
    #
    # ingestion_auto_mode (V1 default "local"): where automatic learning
    #   lands.
    #     "local"  -- P0-1: read the local trace collector output
    #                 (.claude/traces/<session>.jsonl) and write PRIVATE
    #                 candidates into the workspace LocalProcedureStore.
    #                 DB-free. A raw local trace is NEVER uploaded to the
    #                 global server just because auto-learning is on;
    #                 crossing to global stays the explicit publish path.
    #     "global" -- the shared substrate path (POST
    #                 /v1/admin/ingestion/process: trace_events ->
    #                 observations -> claims -> shared procedures). For a
    #                 deliberate shared/company deployment only; needs a DB.
    # Defaults are small and positive so the automatic path does bounded
    # real work; INGESTION_AUTO_ENABLED=false disables it entirely (tests,
    # dev that must do no automatic work).
    ingestion_auto_enabled: bool = True
    ingestion_auto_mode: str = "local"
    ingestion_auto_interval_seconds: int = 60
    # local mode:
    ingestion_auto_workspace: Optional[str] = None      # default: CLAUDE_PROJECT_DIR or cwd
    ingestion_auto_trace_dir: Optional[str] = None       # default: <workspace>/.claude/traces
    ingestion_auto_max_sessions: int = 5
    # global mode:
    ingestion_auto_promote_limit: int = 5
    ingestion_auto_extract_limit: int = 5
    ingestion_auto_job_limit: int = 500

    # --- MCP task-state single-worker guard (Phase 34 / MCP TASK STATE) ---
    # TasksExtension's task store is in-memory and single-process (see
    # app/mcp_server/tasks_extension.py's module docstring: a tasks/get poll
    # routed to a different worker than the one that created the task 404s).
    # This is the explicit, operator-declared MCP server worker count --
    # checked at boot by tasks_extension.assert_single_worker(), mirroring
    # app/services/authn.py's assert_boot_posture(): an explicit settings
    # check that refuses to boot on a bad combination, not runtime process
    # introspection (which uvicorn's --workers does not expose to the app
    # by default). Default 1 matches the documented, load-bearing
    # `uvicorn app.mcp_server.server:app --workers 1` deployment command
    # (README_MCP_SERVER.md, server.py). Set MCP_WORKER_COUNT to whatever a
    # deployment actually launches with -- a mismatch between this and the
    # real `--workers` flag is a deployment-config bug this guard cannot see
    # (see its own docstring's honest limit), but a value >1 here is always
    # refused.
    mcp_worker_count: int = 1

    # --- Observability (app/observability.py) ---
    # Optional like every other secret here: absent DSN means Sentry stays
    # off and init() is a no-op, so nothing about local or offline work
    # changes. PRODUCTION_READINESS.md's "What does not exist at all"
    # names observability first -- a hosted failure is currently silent.
    sentry_dsn: Optional[str] = None
    # Sampled, not 1.0: traces are the expensive half and the useful
    # signal here is errors. Overridable per deployment.
    sentry_traces_sample_rate: float = 0.1
    # Tags every event so api/mcp/worker errors are separable in one
    # project rather than three.
    environment: str = "local"
    release: Optional[str] = None

    def require(self, field: str) -> str:
        value = getattr(self, field, None)
        if not value:
            raise RuntimeError(
                f"Missing required setting '{field}'. Set {field.upper()} in your .env "
                f"(see .env.example)."
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
