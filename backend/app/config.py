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

    # --- V2 governance ---
    # On by default: an unprotected public endpoint that spends money per
    # call is the kind of thing that should require deliberate opt-out,
    # not deliberate opt-in.
    governance_enabled: bool = True
    daily_llm_budget_usd: float = 10.0
    per_viewer_daily_budget_usd: float = 1.0

    # Single-tenant placeholder (Section 12 auth seam).
    default_tenant_id: str = "00000000-0000-0000-0000-000000000001"

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
