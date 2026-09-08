"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import observability
from app.api import admin, agent_store, agents, approval, chat, decompose, graph, ingest
from app.api import claims, implementations, me, procedures, projects, repositories, search, solutions, tasks
from app.api import problems, runs
from app.api import publications, workspaces
from app.api import contributors as contributors_api
from app.api import profile as profile_api
from app.api.deps import require_trustworthy_identity
from app.config import settings
from app.db.session import close_pool, create_pool
from app.services import ingestion_scheduler
from app.services.authn import assert_boot_posture, install_actor_middleware, oidc_configured

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","component":"%(name)s","message":"%(message)s"}',
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast if private visibility is enabled without real auth --
    # that combination would expose private content to anyone who sets
    # an X-Viewer-Id header.
    require_trustworthy_identity()
    # Band 2.9 frozen posture: multi-user exposure / real_auth_enabled
    # without OIDC configured refuses to boot -- the identity gate cannot
    # silently slip to a later band.
    # oidc_configured() accounts for BOTH the generic OIDC_ISSUER/OIDC_AUDIENCE
    # path and the Supabase Auth preset (SUPABASE_PROJECT_URL +
    # SUPABASE_JWT_AUDIENCE) — a Supabase-only deployment must count as
    # "identity configured", and a half-configured preset raises here so the
    # bad posture fails at boot rather than silently degrading to anonymous.
    assert_boot_posture(
        private_visibility_enabled=settings.private_visibility_enabled,
        real_auth_enabled=settings.real_auth_enabled,
        oidc_configured_=oidc_configured(settings),
        multi_user_exposure_enabled=settings.multi_user_exposure_enabled,
        hosted_execution_enabled=settings.hosted_execution_enabled,
    )
    app.state.pool = await create_pool()
    # The seam this directive closes: without this, "user does normal
    # agent work" never becomes "a procedure candidate exists in storage"
    # unless a human remembers to curl /v1/admin/ingestion/process on a
    # timer. INGESTION_AUTO_ENABLED=false (tests, some dev setups) skips
    # starting the task but still sets app.state.ingestion_scheduler so
    # the status endpoint always has something to report.
    ingestion_scheduler.start(app)
    try:
        yield
    finally:
        await ingestion_scheduler.stop(app)
        await close_pool()


# Before the app object exists, so an error raised during middleware or
# router import is still reported. No-op without SENTRY_DSN.
observability.init("api")

app = FastAPI(title="Workflow Debate Platform", version="0.1.0", lifespan=lifespan)

# CORS: a separately-hosted frontend (e.g. Vercel) is a different origin
# from the API (e.g. Railway/Render), so the browser blocks requests here
# by default without this. FRONTEND_ORIGIN is a single configured origin
# for v0 -- tighten before this serves more than one frontend deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Band 2.9 identity gate: bearer-token validation + actor propagation on
# every request. Pass-through no-op while OIDC is unconfigured; the boot
# posture guard above is what keeps that honest.
install_actor_middleware(app, settings)

app.include_router(ingest.router)
app.include_router(approval.router)
app.include_router(admin.router)
app.include_router(graph.router)
app.include_router(chat.router)
app.include_router(decompose.router)
app.include_router(agents.router)
app.include_router(agent_store.router)

# Backend Master Build Wave 1 (2026-09-01): read-only domain API layer,
# pure composition over the existing substrate -- no new schema. See
# .scratch/backend_architecture_audit.md §4 for what each router composes.
app.include_router(claims.router)
app.include_router(procedures.router)
app.include_router(solutions.router)
app.include_router(repositories.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(me.router)
app.include_router(search.router)

# Implementation Registry wave (2026-09-01): durable implementation
# identity + REST surface, migration 33. See
# .scratch/implementation_registry_architecture.md.
app.include_router(implementations.router)

# Final-V1 product layer (migration 35): Problem / Benchmark / Solution /
# Evaluation + evidence-derived leaderboard. All routes delegate to
# app.services.product_model -- REST and MCP share that one service.
app.include_router(problems.router)

# Final-V1 §2: retry/resume REST surface over the durable-run service
# (app/execution/durable_run.py, migrations 36/37). Thin -- delegates to
# app.execution.durable_resume; no retry logic in the router.
app.include_router(runs.router)
app.include_router(publications.router)
app.include_router(workspaces.router)
app.include_router(contributors_api.router)
app.include_router(profile_api.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
