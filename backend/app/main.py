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
from app.api.deps import require_trustworthy_identity
from app.config import settings
from app.db.session import close_pool, create_pool
from app.services.authn import assert_boot_posture, install_actor_middleware

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
    assert_boot_posture(
        private_visibility_enabled=settings.private_visibility_enabled,
        real_auth_enabled=settings.real_auth_enabled,
        oidc_configured_=settings.oidc_issuer is not None and settings.oidc_audience is not None,
        multi_user_exposure_enabled=settings.multi_user_exposure_enabled,
    )
    app.state.pool = await create_pool()
    try:
        yield
    finally:
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


@app.get("/health")
async def health():
    return {"status": "ok"}
