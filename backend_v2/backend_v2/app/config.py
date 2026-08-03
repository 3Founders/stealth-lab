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
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: Optional[str] = None

    # CORS origins for the browser frontend, comma-separated. Read through
    # settings, not os.environ: pydantic-settings loads .env into this
    # object without exporting to the process environment, so an
    # os.environ lookup silently ignores the .env value and falls back to
    # the default.
    frontend_origin: str = "http://localhost:3000"

    # Connection pool. 10 was a placeholder from single-user development;
    # every request holds a connection for the whole of a debate or a
    # retrieval, so the ceiling is concurrent requests, not CPU count.
    # Keep max_size under the database's own connection limit -- Supabase's
    # session pooler and a self-hosted Postgres have very different ones.
    db_pool_min_size: int = 2
    db_pool_max_size: int = 20

    # HNSW search breadth (pgvector default is 40). Higher trades latency
    # for recall. Applied per connection in db/session.py.
    hnsw_ef_search: int = 100

    # Ceiling on edges returned by one traversal. Depth bounds hops, not
    # breadth: a single hub node connected to thousands of others makes a
    # depth-2 walk enormous. See GraphStore.traverse_from for what this
    # does and does not bound.
    max_traversal_edges: int = 2000

    # Heterogeneous debate panel (Section 7): three distinct model families.
    anthropic_api_key: Optional[str] = None
    fireworks_api_key: Optional[str] = None   # Kimi K3
    openai_api_key: Optional[str] = None      # third seat

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

    # Model IDs, overridable without touching code.
    anthropic_model: str = "claude-sonnet-4-6"
    fireworks_model: str = "accounts/fireworks/models/kimi-k3"
    openai_model: str = "gpt-4.1"
    gemini_model: str = "gemini-2.5-pro"
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

    @property
    def allowed_origins(self) -> list[str]:
        """
        `frontend_origin` split into a list.

        A list rather than one value because the browser matches Origin as
        an exact string: http://localhost:3003 and http://127.0.0.1:3003
        are the same server and two different origins, and a dev machine
        routinely uses both. Getting this wrong surfaces in the UI as
        "could not reach the API", which points at the wrong thing --
        the request arrived and was answered, the browser just discarded
        the response. Production can still set a single origin.
        """
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

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
