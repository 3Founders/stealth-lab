"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import evals, proposals, run, tasks
from app.config import settings
from app.db import close_pool, get_pool, qualified_schema, verify_isolation

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","component":"%(name)s","message":"%(message)s"}',
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # get_pool rather than create_pool: close_pool only closes the
    # module-level pool, so a directly-created one is never closed.
    app.state.pool = await get_pool()

    # Refuse to serve if `task_nodes` or `traces` resolve outside our schema.
    # plat_v1 can share a database with backend_v2, which owns tables of the
    # same names with different columns; pgvector's schema has to sit on the
    # search_path for the `vector` type to resolve, and on some installs that
    # schema is `public`. Starting up misrouted would mean serving another
    # application's rows, so fail loudly instead.
    try:
        async with app.state.pool.acquire() as conn:
            await verify_isolation(conn)
    except BaseException:
        # Close the pool before propagating. The try/finally below only wraps
        # the yield, so a failure here would leave the module-level pool open
        # -- one leaked pool per reload under `uvicorn --reload` against a
        # database that has not been seeded yet.
        await close_pool()
        raise
    log.info("schema %s verified", qualified_schema())

    try:
        yield
    finally:
        await close_pool()


app = FastAPI(title="plat_v1", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(run.router)
app.include_router(proposals.router)
app.include_router(tasks.router)
app.include_router(evals.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/config")
async def config():
    """
    The four decisions implement.py says to raise rather than guess, and what
    they are currently set to. Exposed so the answer to "why did it do that"
    is one request away rather than a code read.
    """
    return {
        "auto_match_threshold": settings.auto_match_threshold,
        "allow_unreviewed_first_layout_mapping": settings.allow_unreviewed_first_layout_mapping,
        "artifact_root": settings.artifact_root,
        "keep_run_artifacts": settings.keep_run_artifacts,
        "fail_run_on_stage_failure": settings.fail_run_on_stage_failure,
        "quality_bar_tolerance": settings.quality_bar_tolerance,
        "max_escalations": settings.max_escalations,
        "model_id": settings.model_id,
    }
