"""
Central configuration.

Same shape as backend_v2's: every secret and tunable routes through one
object rather than scattered os.environ lookups, and secrets are Optional
with None defaults so importing any module offline (which the whole test
suite does) never requires a populated .env. Call settings.require() at the
point of use instead.

The four values under "Decisions raised, not guessed" are the ones
implement.md says to flag to a human rather than pick silently. They are
settings with documented defaults precisely so that changing them is a config
edit and an explicit act, not a code change nobody reviews. See the README
section of the same name.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: Optional[str] = None

    # Postgres schema plat_v1 owns. Every connection's search_path is this
    # schema first, then whichever schema hosts pgvector.
    #
    # plat_v1 and backend_v2 both define `task_nodes` and `traces` with
    # different columns. Sharing a database is a supported end state (the
    # convergence doc's option B, "two apps, one database"); sharing a
    # *schema* is not.
    #
    # Note what this does and does not guarantee. Ours being first means our
    # tables always shadow same-named ones elsewhere -- but the pgvector
    # schema has to be on the path for the `vector` type to resolve, and on
    # some installs (including the Supabase project this was built against)
    # that schema IS `public`. So isolation is conditional, not structural:
    # it holds while our tables exist. `db.verify_isolation` is what enforces
    # it, and it runs at app startup and after seeding.
    db_schema: str = "plat_v1"

    frontend_origin: str = "http://localhost:3000"

    db_pool_min_size: int = 2
    db_pool_max_size: int = 20

    # HNSW search breadth. pgvector's default of 40 favours latency over
    # recall; recall is the axis that matters when a miss means re-deriving a
    # task that already exists.
    hnsw_ef_search: int = 100

    # --- Model calls ---
    anthropic_api_key: Optional[str] = None
    # implement.md is explicit: claude-opus-5 with structured outputs, not
    # JSON parsed out of prose.
    model_id: str = "claude-opus-5"
    model_max_tokens: int = 16000
    # low | medium | high | xhigh | max. Left at the API default.
    model_effort: str = "high"
    model_timeout_s: float = 300.0

    # Published claude-opus-5 rates, per token. Used to turn reported token
    # usage into the `cost` column on a trace. A trace whose cost is always
    # zero makes the router's cost ordering meaningless, which is most of the
    # point of recording traces at all.
    model_input_cost_per_token: float = 5.0 / 1_000_000
    model_output_cost_per_token: float = 25.0 / 1_000_000

    # --- Embeddings ---
    voyage_api_key: Optional[str] = None
    embedding_model: str = "voyage-3-large"
    embedding_dimension: int = 1024  # must match VECTOR(n) in db/01_schema.sql

    # ------------------------------------------------------------------
    # Decisions raised, not guessed (implement.md, "Decisions to raise")
    # ------------------------------------------------------------------

    # 1. The auto-match score threshold.
    #    Needs tuning against real prompts; there is no principled value
    #    before there are real prompts to tune against. 0.03 is roughly the
    #    fused RRF score of a result that ranks first in one retrieval arm
    #    and second in the other (1/61 + 1/62 ~= 0.0325), i.e. "both arms
    #    agree this is the answer". Schema validation is the real gate --
    #    this only decides whether to bother checking it.
    auto_match_threshold: float = 0.03

    # 2. Whether map_to_schema may run unattended on a first-time layout.
    #    Default is no: on a layout with no cache entry, the stage that maps
    #    cells onto a target schema is the one place a wrong-but-plausible
    #    answer is both likely and invisible downstream. Set true to let it
    #    run anyway.
    allow_unreviewed_first_layout_mapping: bool = False

    # 3. Where run artifacts live once the per-run temp directory is gone.
    #    Default keeps them: a run whose output file was deleted before
    #    anyone fetched it is a failed run that reports success. Files land
    #    in <artifact_root>/<run_id>/.
    artifact_root: str = "./artifacts"
    keep_run_artifacts: bool = True

    # 4. Whether a failed stage fails the run.
    #    Default is yes -- fail loudly. A partial result reported as a run
    #    that "finished" is the failure mode that costs the most later.
    #    Set false to have the executor record the failure, stop, and return
    #    whatever stages did complete.
    fail_run_on_stage_failure: bool = True

    # --- Router ---
    # How far below the best-measured score an implementation may sit and
    # still be considered. Only applies to tasks that have eval results.
    quality_bar_tolerance: float = 0.05
    # Attempts after the first, per stage. implement.md caps this at 3.
    max_escalations: int = 3

    @property
    def allowed_origins(self) -> list[str]:
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
