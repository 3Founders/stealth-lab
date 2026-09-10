"""
StealthLab MCP server. Representative tools:
  - retrieve_precedent: thin read wrapper, zero new business logic, wraps
    an already-tested retrieval function.
  - submit_approval / decide_decomposition: the ONLY paths that mutate the
    knowledge graph. Each loads a persisted proposal (a debate scorecard
    row / a decompositions row), enforces its gate (PENDING_APPROVAL /
    status='proposed'), applies THAT row's stored change_set -- never a
    caller-supplied one -- and writes an audit row. The former ungated
    `apply_change_set` tool, which applied an arbitrary caller-supplied
    change_set with no gate or audit row, was removed in the post-freeze
    security hardening (v1-final-2026-09-03.1).
  - propose_synthesis: thin wrapper around LoopOrchestrator.run(), the real
    debate orchestration used throughout this project.
  - find_best_way (renamed from solve_task): NOT a pure wrapper -- see its
    own docstring's HONEST STATUS section. Reuses RepoSandbox/Agent
    verbatim for its tier-2 execution path, but adds real new orchestration
    (a generic, non-SWE-bench-specific instance/prompt path, plus the
    tier-1 fast-lookup path) on top.
  - check_procedure: demo.md C5's audit-mode ALLOW/WOULD_REFUSE tool; thin
    wrapper around app.services.applicability.check_procedure_reuse().

("Four tools" above is stale relative to the full @server.tool() list this
file actually defines today -- a pre-existing doc-drift gap, not one this
addition introduces or fixes; flagged on the board rather than silently
expanded into an out-of-scope rewrite.)

Built against the real, installed mcp==2.0.0 SDK (2026-07-28 spec),
verified by direct introspection of the installed package -- not against
remembered pre-2.0 API names. Confirmed real: MCPServer (not FastMCP,
renamed in v2), the .tool() decorator, Context.lifespan for accessing the
DB pool from within a tool call.

propose_synthesis/find_best_way are genuinely long-running (multi-round
debate / multi-step agent loop). Long-run semantics come from
tasks_extension.py, a real, hand-built implementation of SEP-2663 -- see
that module's own docstring for why (mcp==2.0.0 ships no Tasks runtime at
all yet; confirmed via exhaustive grep of the installed package plus the
SDK's own release notes, not assumed).
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys

import asyncpg
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
load_dotenv()

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from pydantic import AnyHttpUrl

from app.db.session import create_pool
from app.execution import durable_resume as _dres
from app.execution import durable_run as _dr
from app.execution import implementation_registry
from app.api.approval import decide, ApprovalRequest
from app.api.decompose import decompose, decide as decide_decomposition_fn, DecomposeRequest, DecideRequest
from app.services.access import AccessScope
from app.services.applicability import verified_procedure_candidates
from app.services.authn import (
    FetchingJwks,
    OidcConfig,
    TokenRejected,
    assert_deployment_mode_posture,
    current_actor_id,
    validate_token_async,
)
from app.services.decomposition import DecompositionService
from app.services.embeddings import Embedder
from app.services.knowledge_conflict import detect_and_create_conflict_trigger
from app.services.local_retrieval import assemble_structural_context, retrieve_local_first
from app.services.procedure_extraction import extract_procedure
from app.services.procedure_extraction.evidence import AgentRunEvidenceSource
from app.services.retrieval import HybridRetriever
from app.services.reuse_detection import ReusableNode, _vector_candidates
from app import observability
from app.config import settings
from fastapi import HTTPException

# Real, existing debate orchestration -- LoopOrchestrator.run(trigger_id) is
# the actual, already-tested entrypoint used by app/api/admin.py,
# app/services/human_participation.py, and every experiment script in this
# project. No new debate logic lives here.
from app.debate.panel import default_panel, default_judge
from app.debate.state_machine import DebateStateMachine
from app.services.loop import LoopOrchestrator

# RepoSandbox/Agent/TOOLS live in experiments/swebench_pro/, a SIBLING of
# backend/ (confirmed via the exact same sys.path pattern
# tests/test_agent_sandbox.py and tests/test_htn_agent.py already use to
# reach it -- not a new convention invented for this file).
_EXPERIMENTS_SWEBENCH_PRO = str(
    Path(__file__).resolve().parents[3] / "experiments" / "swebench_pro"
)
sys.path.insert(0, _EXPERIMENTS_SWEBENCH_PRO)
from agent import Agent, RepoSandbox  # noqa: E402

from openai import OpenAI

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from app.mcp_server.tasks_extension import TasksExtension
from app.mcp_server.claim_graph_page import CLAIM_GRAPH_HTML, FORCE_GRAPH_JS
from app.mcp_server.procedure_graph_page import PROCEDURE_GRAPH_HTML
from app.services import claim_graph_api
from app.services import procedure_task_graph_api
from app.services import product_model as _pm

# Set once by `lifespan` (below) so the non-MCP custom HTTP routes
# (/claim-graph, /claim-graph/data) can reach the same pool the MCP tools
# get via ctx.request_context.lifespan_context -- a plain Starlette route
# handler is not an MCP request and has no ctx. Single process
# (--workers 1 is already load-bearing here), so a module global is safe.
_LIFESPAN_STATE: dict = {}

# LOGGED, DELIBERATE, LOCAL OVERRIDE -- not a change to the shared
# PARTIAL_MATCH_THRESHOLD (0.70) used elsewhere in the platform.
#
# Real basis, confirmed via direct diagnosis: a natural, conversational
# query ("group CSV records by category, sum a value field, exclude
# rows by status") scored 0.6550 against the REAL, correct match --
# even lower than a full task instruction's 0.68 against the same node
# (found earlier, synthetic Task C). Short queries embed further from
# long, structured trajectory text than full task descriptions do, even
# for the exact same correct match -- and short, conversational queries
# are the NORMAL case for an LLM calling this tool, not an edge case.
# Real unrelated content stayed clearly separated (0.42 for a genuinely
# different pattern, 0.35-0.39 for real unrelated banking docs), so
# 0.60 has real headroom below it without inviting noise back in.
#
# HONEST LIMIT: based on n=2 real data points across two different
# query shapes (Task C's full instruction, this short query). A
# provisional, flagged value, not a validated recalibration.
RETRIEVE_PRECEDENT_THRESHOLD = 0.60


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[dict]:
    """Real DB pool, created once at server startup, closed once at
    shutdown -- not per-call. Matches the stateless-per-REQUEST model
    (no session state travels with the connection), while still reusing
    a real connection pool across requests, which is a resource-
    management concern, not a protocol-state concern -- the two aren't
    the same thing even though it's easy to conflate them."""
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL not set -- see backend/.env")
    pool = await create_pool(os.environ["DATABASE_URL"])
    _LIFESPAN_STATE["pool"] = pool
    try:
        yield {"pool": pool}
    finally:
        _LIFESPAN_STATE.pop("pool", None)
        await pool.close()


class OidcAwareTokenVerifier(TokenVerifier):
    """Bearer-token check for this server's HTTP transport -- reuses the
    EXACT same OIDC validation the real REST layer already uses
    (app.services.authn.validate_token_async / OidcConfig / FetchingJwks,
    wired onto app.main:app via install_actor_middleware), rather than
    inventing a second auth model for this transport.

    Tries OIDC first, when OIDC_ISSUER + OIDC_AUDIENCE are configured
    (`app.config.settings`, same fields `authn.OidcConfig.from_settings`
    already reads for the REST app): a bearer that validates as a real,
    signed token belonging to that IdP gets AccessToken.subject set to the
    token's real `sub` claim. This is what makes
    `mcp.server.auth.middleware.auth_context.get_access_token().subject`
    -- `_resolve_caller_identity`'s FIRST real identity source, below --
    resolve a genuine, per-caller, spoof-proof identity over this
    transport: two different OIDC-issued tokens now attribute writes to
    two different real subjects, closing the gap
    `_resolve_caller_identity`'s own docstring used to describe as always
    empty in this process.

    Falls back to the single shared STEALTHLAB_MCP_TOKEN (constant-time
    compared via secrets.compare_digest, since this is a bearer secret
    compared against attacker-controlled input over the network) when
    OIDC is not configured, or when the presented token does not validate
    as an OIDC JWT for the configured issuer/audience. That fallback
    AccessToken carries no .subject, exactly as before OIDC support
    existed -- this class only ADDS a real per-caller identity source when
    an operator opts into OIDC; it never weakens or removes the existing
    loopback-shared-secret gate, which stays the only mode in today's
    actual default deployment (OIDC_ISSUER/OIDC_AUDIENCE unset).

    Deliberately NOT sufficient on its own even with OIDC configured:
    find_best_way's repo_path is caller-controlled (see
    README_MCP_SERVER.md's "Known v1 limitations"). This gates WHO can
    reach that tool (and, with OIDC, WHO they really are), it does not
    make it safe against a caller who does hold a valid token -- that is
    why hosting stays loopback-only by default (see the ASGI app /
    uvicorn invocation below), not exposed via tunnel. (The former
    ungated `apply_change_set` write tool was removed in the post-freeze
    security hardening; graph mutation is now gated behind
    submit_approval / decide_decomposition.)
    """

    def __init__(self, shared_token: str, oidc_config: Optional[OidcConfig], jwks_provider):
        self._shared_token = shared_token
        self._oidc_config = oidc_config
        self._jwks_provider = jwks_provider

    async def verify_token(self, token: str) -> AccessToken | None:
        if self._oidc_config is not None:
            actor = None
            try:
                actor = await validate_token_async(
                    token, config=self._oidc_config, jwks_provider=self._jwks_provider,
                )
            except TokenRejected:
                pass  # not a valid OIDC token for this issuer/audience -- try the shared-secret fallback below
            if actor is not None:
                return AccessToken(
                    token=token, client_id=actor.subject,
                    scopes=["stealthlab:tools"], subject=actor.subject,
                )
        if not secrets.compare_digest(token, self._shared_token):
            return None
        return AccessToken(token=token, client_id="stealthlab-local", scopes=["stealthlab:tools"])


def _require_mcp_token() -> str:
    """Fail at import time, not on the first tool call -- same discipline
    as lifespan's own DATABASE_URL check just above."""
    token = os.environ.get("STEALTHLAB_MCP_TOKEN")
    if not token:
        raise RuntimeError(
            "STEALTHLAB_MCP_TOKEN not set -- generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"` "
            "and add it to backend/.env")
    return token


def _build_token_verifier(shared_token: str) -> OidcAwareTokenVerifier:
    """OIDC config comes from the exact same settings fields the REST
    app's install_actor_middleware reads (app.services.authn.OidcConfig.
    from_settings) -- None when OIDC_ISSUER/OIDC_AUDIENCE are unset, which
    is today's actual default posture (see README_MCP_SERVER.md); the
    verifier then runs shared-secret-only, unchanged from before this
    function existed.

    Boot guard (same "refuse to boot on a bad combination" shape as
    authn.assert_boot_posture for the REST app and
    tasks_extension.assert_single_worker): when DEPLOYMENT_MODE=shared is
    set explicitly, a shared-secret-only verifier means every caller is
    attributed as one identity -- wrong for a real multi-user
    deployment -- so require OIDC_ISSUER + OIDC_AUDIENCE. The default
    DEPLOYMENT_MODE=single_user never raises here (existing dev setups
    unaffected)."""
    assert_deployment_mode_posture(
        deployment_mode=settings.deployment_mode,
        oidc_issuer=settings.oidc_issuer,
        oidc_audience=settings.oidc_audience,
    )
    oidc_config = OidcConfig.from_settings(settings)
    jwks_provider = FetchingJwks(oidc_config.jwks_url) if oidc_config is not None else None
    return OidcAwareTokenVerifier(shared_token, oidc_config, jwks_provider)


_MCP_PORT = 8765  # not the SDK's default 8000, which app/main.py's FastAPI app already uses

server = MCPServer(
    name="stealthlab",
    version="1.0.0",
    instructions=(
        "Retrieval, debate, and knowledge-graph tools for StealthLab's "
        "bi-temporal task/knowledge graph, plus a retrieval-grounded coding "
        "agent. propose_synthesis and find_best_way are genuinely long-running "
        "(multi-round debate / multi-step agent loop) -- clients that "
        "declare the io.modelcontextprotocol/tasks extension capability get "
        "a CreateTaskResult back immediately and poll tasks/get; clients "
        "that don't get the plain synchronous result, same as before."
    ),
    lifespan=lifespan,
    extensions=[TasksExtension()],
    # Authorization applies to HTTP transports only -- stdio (the `mcp dev`
    # Inspector quickstart in README_MCP_SERVER.md) bypasses it entirely,
    # by protocol design, not by an oversight here.
    token_verifier=_build_token_verifier(_require_mcp_token()),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(f"http://127.0.0.1:{_MCP_PORT}"),
        resource_server_url=AnyHttpUrl(f"http://127.0.0.1:{_MCP_PORT}/mcp"),
        required_scopes=["stealthlab:tools"],
    ),
)

# ASGI app for hosted Streamable HTTP -- serves POST/GET on /mcp. The stdio
# entrypoint at the bottom of this file (`if __name__ == "__main__"`) is
# UNCHANGED and still what the Inspector quickstart uses; this is an
# additional way to run the same `server`, not a replacement.
#
# Serve with (from backend/):
#   uvicorn app.mcp_server.server:app --host 127.0.0.1 --port 8765 --workers 1
#
# --workers 1 is load-bearing, not incidental: TasksExtension's backing
# store (tasks_extension.py) is in-memory, so a second worker process
# would serve a tasks/get poll from a process that never saw the task
# propose_synthesis/find_best_way created -- the call would appear to hang.
# The SECOND ASGI app in this project -- instrumenting only main.py would
# leave all 9 MCP tools dark, which is the surface external agents
# actually call. No-op without SENTRY_DSN.
observability.init("mcp")


# ---------------------------------------------------------------------------
# Claim-graph viewer -- a read-only web page + JSON feed served by THIS MCP
# server, so anyone running the StealthLab MCP setup can open
# http://127.0.0.1:8765/claim-graph and see the live claim graph. The
# graph renderer (force-graph, vendored under app/mcp_server/vendor/) is
# served from /claim-graph/vendor/... -- no CDN, no build step, works
# fully offline. `@server.custom_route` routes are deliberately
# unauthenticated (the SDK reserves them for public health-check-style
# endpoints); this fits the loopback-only default posture and the fact
# that every handler here is strictly read-only. If the server is ever
# exposed beyond loopback, front it with a reverse proxy / auth the same
# way any other read endpoint would be.
# ---------------------------------------------------------------------------

def _graph_pool():
    pool = _LIFESPAN_STATE.get("pool")
    if pool is None:  # pragma: no cover - only before startup / after shutdown
        raise RuntimeError("server not started -- DB pool unavailable")
    return pool


@server.custom_route("/claim-graph", methods=["GET"], include_in_schema=False)
async def claim_graph_page(request: Request) -> HTMLResponse:  # noqa: ARG001
    return HTMLResponse(CLAIM_GRAPH_HTML)


@server.custom_route("/claim-graph/vendor/force-graph.js", methods=["GET"], include_in_schema=False)
async def claim_graph_vendor_forcegraph(request: Request) -> Response:  # noqa: ARG001
    return Response(FORCE_GRAPH_JS, media_type="application/javascript",
                    headers={"cache-control": "public, max-age=86400"})


@server.custom_route("/claim-graph/data", methods=["GET"], include_in_schema=False)
async def claim_graph_data(request: Request) -> JSONResponse:
    qp = request.query_params

    def _int(name: str, default: int) -> int:
        try:
            return int(qp.get(name, default))
        except (TypeError, ValueError):
            return default

    def _float(name: str, default: float) -> float:
        try:
            return float(qp.get(name, default))
        except (TypeError, ValueError):
            return default

    def _bool(name: str) -> bool:
        return str(qp.get(name, "")).lower() in ("1", "true", "yes", "on")

    result = await claim_graph_api.get_claim_graph_overview(
        _graph_pool(),
        scope=AccessScope.unrestricted(),
        limit=_int("limit", 200),
        include_retired=_bool("include_retired"),
        q=(qp.get("q") or None),
        with_status=qp.get("with_status", "true").lower() != "false",
        link_mode=(qp.get("link_mode") or "both"),
        sim_k=_int("sim_k", 3),
        sim_threshold=_float("sim_threshold", 0.55),
    )
    return JSONResponse(json.loads(json.dumps(result, default=str)))


# ---------------------------------------------------------------------------
# Procedure & task-node viewer -- the procedure-side counterpart of
# /claim-graph. Same posture: read-only, unauthenticated custom routes,
# force-graph served from the shared /claim-graph/vendor/ path (no second
# copy of the lib). Whole-corpus overview of live procedures + task nodes
# and how they connect (version chains, decomposition, hierarchy,
# subprocedure composition).
# ---------------------------------------------------------------------------
@server.custom_route("/procedure-graph", methods=["GET"], include_in_schema=False)
async def procedure_graph_page(request: Request) -> HTMLResponse:  # noqa: ARG001
    return HTMLResponse(PROCEDURE_GRAPH_HTML)


@server.custom_route("/procedure-graph/data", methods=["GET"], include_in_schema=False)
async def procedure_graph_data(request: Request) -> JSONResponse:
    qp = request.query_params

    def _int(name: str, default: int) -> int:
        try:
            return int(qp.get(name, default))
        except (TypeError, ValueError):
            return default

    def _bool(name: str, default: bool = False) -> bool:
        raw = qp.get(name)
        if raw is None:
            return default
        return str(raw).lower() in ("1", "true", "yes", "on")

    # the page sends `kinds=procedure` | `procedure,task`; also accept a
    # plain `include_tasks` bool.
    kinds_raw = qp.get("kinds")
    if kinds_raw is not None:
        include_tasks = "task" in {k.strip() for k in kinds_raw.split(",")}
    else:
        include_tasks = _bool("include_tasks", True)

    result = await procedure_task_graph_api.get_procedure_task_overview(
        _graph_pool(),
        scope=AccessScope.unrestricted(),
        limit=_int("limit", 150),
        q=(qp.get("q") or None),
        include_stale=_bool("include_stale", False),
        include_tasks=include_tasks,
        link_mode=(qp.get("link_mode") or "all"),
    )
    return JSONResponse(json.loads(json.dumps(result, default=str)))


app = server.streamable_http_app()


def _resolve_caller_identity(fallback: str) -> str:
    """Real caller-identity resolution for write-path attribution
    (created_by / approved_by / author), instead of the static,
    tool-name-derived strings this file used unconditionally before this
    function existed -- the exact "architecture assumes a single shared
    identity" gap product spec Phase 33 names.

    Two REAL, already-installed identity sources are checked, in order.
    Neither lives on `ctx`/`ctx.request_context` -- confirmed by reading
    mcp.server.mcpserver.Context and ServerRequestContext: both carry
    session/lifespan/request-id, no auth field. Both real sources are
    contextvars instead, so a tool body reads them directly; there is no
    ctx plumbing to add.

    1. mcp.server.auth.middleware.auth_context.get_access_token() -- the
       MCP SDK's own contextvar, auto-wired into this server's ASGI stack
       by AuthContextMiddleware because `server` above is constructed
       with token_verifier=_build_token_verifier(...) (confirmed by
       reading mcp/server/mcpserver/server.py: passing token_verifier
       makes create_app() add BearerAuthBackend + AuthContextMiddleware to
       the Starlette stack). Its AccessToken.subject (RFC 7662/9068 `sub`)
       is real per-request identity, when the verifier sets one.

       CLOSED FOR REAL (was the single-shared-identity gap): OidcAware
       TokenVerifier (this file, above) now tries OIDC validation first --
       reusing app.services.authn.validate_token_async, the SAME
       validation the REST app's install_actor_middleware uses -- when
       OIDC_ISSUER/OIDC_AUDIENCE are configured. A caller presenting a
       real, signed OIDC token gets AccessToken.subject set to that
       token's real `sub`, so two different OIDC identities now resolve
       to two different values here, proven end-to-end (real signed
       tokens, real persisted rows, two distinct identities) by
       test_mcp_server_identity_e2e.py::test_two_distinct_oidc_identities_
       attribute_to_distinct_rows_and_ignore_spoofed_approver_id.

       HONEST REMAINING GAP: with OIDC_ISSUER/OIDC_AUDIENCE unset (today's
       actual default posture -- see README_MCP_SERVER.md), the verifier
       falls back to the single shared STEALTHLAB_MCP_TOKEN and never sets
       .subject, so every caller still gets the same
       client_id="stealthlab-local" and no subject in that mode -- this
       branch is empty exactly as documented for the local/shared-secret
       posture, honestly, not silently.

    2. app.services.authn.current_actor_id() -- the real OIDC actor
       contextvar authn.py's ASGI middleware populates on app.main:app
       (port 8000, install_actor_middleware). Checked here too because
       it is the other real identity mechanism this codebase has, and
       because this MCP server runs as its OWN separate ASGI app/process
       (uvicorn app.mcp_server.server:app, port 8765) which never calls
       install_actor_middleware -- so today this branch is also always
       empty in this process. A future deployment that serves MCP from
       inside the same ASGI app as app.main would get this for free.

    Neither present -> the unchanged, honest `fallback` (the pre-existing
    hardcoded string, or a caller-supplied parameter this replaces only
    when a REAL identity was resolved) -- never a fabricated identity.

    INVARIANT (the thing this function exists to guarantee, restated
    explicitly so a future call site can't silently reintroduce the bug
    this closes): a caller-supplied, identity-shaped tool PARAMETER
    (`approver_id`, or anything `actor_id`/`created_by`-shaped) must
    NEVER be trusted as the attributed identity when a real one is
    resolvable here. Every write-path call site below passes such a
    parameter, if it has one, as `fallback=` -- never as the returned
    value directly. Proven end-to-end against a real, persisted row (not
    just in isolation) by test_mcp_server_identity_e2e.py::
    test_decide_procedure_resolved_identity_overrides_spoofed_approver_id,
    which calls decide_procedure with both a mocked-real resolved
    identity AND a different, attacker-supplied approver_id and asserts
    the PERSISTED `procedures.approved_by` row shows the resolved
    identity, never the spoofed one.

    Full current call-site list (write-path attribution only; read-only
    tools don't call this):
      - find_best_way: capture_procedure's created_by (ad-hoc capture),
        both record_plan_execution's created_by (tier-1 and tier-2 runs).
      - reproduce_procedure: mark_procedure_stale's detected_by,
        compile_plan's created_by, record_plan_execution's created_by.
      - submit_procedure: capture_procedure's created_by.
      - decide_procedure: approve_procedure/reject_procedure's
        approved_by -- fallback=approver_id (the caller-supplied,
        self-asserted parameter), so a resolved real identity OVERRIDES
        it rather than being overridden by it.
      - decide_decomposition: DecideRequest's approver_id (the real
        decompositions.approver_id audit column) -- fallback=approver_id.
        A real audit gap this same identity-hardening pass missed the
        first time: found and closed during a later hardening audit
        (this pass), same "no resolution attempt" bug, at a different
        call site.
      - submit_approval: ApprovalRequest's approver_id (the real
        approvals.approver_id audit column) -- fallback=approver_id.
        Same gap, same fix, found in the same pass as decide_decomposition
        above.
    """
    token = get_access_token()
    if token is not None and token.subject:
        return token.subject
    actor_id = current_actor_id()
    if actor_id:
        return actor_id
    return fallback


async def _resolve_trace_id(pool, *, parent_run_id: Optional[str], session_id: Optional[str]) -> str:
    """MCP hardening B16: a real trace_id, always populated, distinct
    from session_id (a caller/session-scoped identifier that can span
    many unrelated calls) -- a trace is ONE causal chain: a root run plus
    every recursive child it spawns.

    Before this: both find_best_way call sites passed `trace_id=
    session_id` directly into start_run/run_graph_durably -- a child run
    got its OWN session's id (or None, if the child call carried none)
    instead of inheriting the parent's, and a root call with no
    session_id left trace_id NULL forever (never auto-generated).

    Fix: a child (parent_run_id given) inherits its parent's real,
    already-persisted trace_id -- the SAME chain, regardless of what
    session_id (if any) the child call happens to carry. A root call
    keeps using session_id when the caller supplied one (byte-identical
    to before for every existing caller that does), and only generates a
    fresh id via uuid7() when there truly is nothing to key off of.
    """
    if parent_run_id is not None:
        parent_trace_id = await pool.fetchval(
            "SELECT trace_id FROM execution_runs WHERE id = $1", parent_run_id,
        )
        if parent_trace_id:
            return str(parent_trace_id)
    if session_id:
        return session_id
    from app.utils.ids import uuid7
    return str(uuid7())


def _caller_access_scope() -> AccessScope:
    """Read-path visibility scope for the product-model tools -- the MCP
    analogue of the REST ``get_scope`` dependency (``app/api/deps.py``).

    A real resolved OIDC identity (the same two contextvar sources
    ``_resolve_caller_identity`` checks) -> that user's scope
    (``AccessScope.for_user``). Nothing resolvable -- the default
    shared-token / loopback posture -> ``AccessScope.anonymous()``: the
    SAME public-only denial semantics an unauthenticated REST caller gets.

    Never ``AccessScope.unrestricted()`` from a tool call -- that bypasses
    visibility entirely and is exactly what let a private Problem's
    Benchmark / Evaluation leak through ``inspect_problem`` /
    ``inspect_evaluation`` / ``compare_solutions`` / ``find_best_solution``
    (final-V1 eval Bug #8). This is not a second authorization system: the
    guard itself lives in ``product_model`` (inherit the owning Problem's
    scope); this only supplies the caller identity REST already supplies.
    """
    token = get_access_token()
    if token is not None and token.subject:
        return AccessScope.for_user(token.subject)
    actor_id = current_actor_id()
    if actor_id:
        return AccessScope.for_user(actor_id)
    return AccessScope.anonymous()


class _RepoExecutionRefused(Exception):
    """Hosted-mode repo authorization refused the call (tool returns REFUSED)."""


async def _authorize_repo_execution(
    ctx: Context, repo_path: Optional[str], workspace_id: Optional[str]
) -> Optional[str]:
    """Phase 1 P0 hosted-repository authorization boundary.

    Local/loopback mode (hosted_execution_enabled=False, the default):
    repo_path passes through UNCHANGED -- including `None`. This
    function is an authorization boundary, not a "repo_path is required
    for this tool" validator -- several callers (find_best_way's
    lookup-only paths, reproduce_procedure's optional transfer tier)
    legitimately call it with repo_path=None and are responsible for
    their OWN "do I actually need a repo_path here" check afterward.
    (Fixed: this used to raise `_RepoExecutionRefused("repo_path is
    required.")` on None even in local mode, contradicting this exact
    docstring and this module's own callers' expectations -- confirmed
    live via test_find_best_way_plan_only_e2e.py's two tests, which this
    fix makes pass again.)

    Hosted mode: repo_path is NOT the authorization mechanism. The caller
    names a registered workspace id; the server resolves the filesystem
    path from registered_workspaces (db/41) after confirming the caller's
    tenant owns it. A caller-supplied repo_path, if any, must match the
    registered root exactly. Caller-controlled paths never select what is
    mounted; RepoSandbox's traversal guard stays as defense in depth.
    """
    from app.config import settings
    from app.services import workspace_registry as wr

    if not getattr(settings, "hosted_execution_enabled", False):
        return repo_path

    subject = None
    token = get_access_token()
    if token is not None and token.subject:
        subject = token.subject
    else:
        subject = current_actor_id()
    if not subject:
        raise _RepoExecutionRefused(
            "hosted execution requires an authenticated identity."
        )
    if workspace_id is None:
        raise _RepoExecutionRefused(
            "hosted execution requires workspace_id (a registered workspace); "
            "caller-supplied filesystem paths are not an authorization mechanism."
        )
    from app.services.authn import Actor, tenant_scope_for_actor

    _, tenant_scope = await tenant_scope_for_actor(pool=ctx.request_context.lifespan_context["pool"], actor=Actor(subject=subject))
    try:
        workspace = await wr.resolve_workspace_for_actor(
            ctx.request_context.lifespan_context["pool"],
            workspace_id=workspace_id,
            actor_tenant_id=tenant_scope.tenant_id,
        )
        return wr.enforce_hosted_repo_path(
            settings=settings, repo_path=repo_path, workspace=workspace
        )
    except wr.WorkspaceNotFound as exc:
        raise _RepoExecutionRefused(f"REFUSED: {exc}") from exc
    except wr.WorkspaceNotAuthorized as exc:
        raise _RepoExecutionRefused(f"REFUSED: {exc}") from exc



@server.tool()
async def retrieve_precedent(query: str, ctx: Context) -> str:
    """
    Find prior solved patterns relevant to a new problem description.

    Real retrieval via app.services.reuse_detection's underlying
    _vector_candidates -- the same real, already-tested query this
    project has used throughout (real p=0.0066 n=400 result on joint
    embeddings, real Task A/B/C synthetic-task validation). Calls the
    lower-level function directly rather than find_reusable_nodes()
    specifically to apply RETRIEVE_PRECEDENT_THRESHOLD instead of the
    shared platform default -- see that constant's comment for why.

    Fused with applicability.verified_procedure_candidates() (2026-08-27
    fix): _vector_candidates only ever queries task_nodes/knowledge_nodes
    -- `procedures` was never in this tool's candidate set at all, a real
    gap bootstrap_demo.py's own findings named. Procedures show up here
    ONLY once verified -- see verified_procedure_candidates' own
    docstring for the founder ruling on why an unverified/candidate
    procedure deliberately does NOT bypass that gate to appear here
    (considered and rejected: it would let a caller read "similar
    precedent found" as an implicit reuse signal without ever going
    through check_procedure's real applicability/precondition cascade).

    query: a natural-language description of the problem to find a
    precedent for -- ordinary conversational phrasing is the expected,
    normal case for this tool, not a special query syntax.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    embedder = Embedder()
    query_vec = await embedder.embed_one(query, input_type="query")
    raw_candidates = await _vector_candidates(pool, query_vec, AccessScope.unrestricted())
    procedure_rows = await verified_procedure_candidates(pool, query_vec, AccessScope.unrestricted())
    raw_candidates = raw_candidates + [
        ReusableNode(
            id=str(r["id"]), table="procedures", name=r["name"], description=r["goal"],
            similarity=float(r["similarity"]), method="vector",
        )
        for r in procedure_rows
    ]
    candidates = sorted(
        (c for c in raw_candidates if c.similarity >= RETRIEVE_PRECEDENT_THRESHOLD),
        key=lambda c: c.similarity, reverse=True,
    )

    if not candidates:
        return (
            f"No precedent found above this tool's real similarity threshold "
            f"({RETRIEVE_PRECEDENT_THRESHOLD}) for this query."
        )

    lines = [
        f"Found {len(candidates)} real precedent(s) "
        f"(threshold={RETRIEVE_PRECEDENT_THRESHOLD}, a scoped override -- "
        f"see RETRIEVE_PRECEDENT_THRESHOLD's comment for the real basis):"
    ]
    for c in candidates:
        lines.append(
            f"- [{c.table}] {c.name!r} (similarity={c.similarity:.4f}, id={c.id})"
        )
    return "\n".join(lines)


# The ungated `apply_change_set` tool was removed here in the post-freeze
# security hardening (v1-final-2026-09-03.1). It accepted an arbitrary
# caller-supplied change_set JSON and applied it to the real graph with no
# approval gate, no persisted decision, and no audit row -- the only
# ungated public write to the knowledge graph. Graph mutation now goes
# ONLY through submit_approval (a PENDING_APPROVAL debate scorecard's
# STORED change_set + an `approvals` audit row) and decide_decomposition
# (a status='proposed' decompositions row's STORED change_set + a status/
# approver/decided_at update). Both apply the persisted proposal's own
# change_set, never one supplied by the caller of the decision.


@server.tool()
async def propose_synthesis(trigger_id: str, ctx: Context) -> str:
    """
    Run a real debate over an existing trigger and return every surviving
    candidate's scorecard plus its change_set, ready to hand to
    apply_change_set.

    Thin wrapper around LoopOrchestrator.run(trigger_id) -- the exact real,
    already-tested orchestration used by app/api/admin.py,
    app/services/human_participation.py, and every real experiment script in
    this project (default_panel()/default_judge() are the same real,
    heterogeneous-model panel construction used everywhere else, not a
    bespoke panel invented for this tool). No new debate logic lives here.

    trigger_id: the id of an EXISTING row in the `triggers` table. This
    tool does not create triggers -- a trigger must already exist (created
    by whatever upstream monitoring/detection produced it). Passing an
    unknown id is a real, reported failure, not silently ignored.

    Genuinely long-running (multi-round debate across a real heterogeneous
    panel + judge). If your client declares the io.modelcontextprotocol/tasks
    extension capability, this returns a CreateTaskResult immediately and
    you poll tasks/get for the eventual result; otherwise it blocks until
    the debate finishes.

    HONEST LIMIT, carried over from Experiment 3's real, measured result:
    debate CLASSIFICATION (is there a real conflict, and in which direction)
    is validated at 27/32 on real PEP pairs. Debate SYNTHESIS/MERGE (what
    this tool actually produces) has been validated only once, after 3 real
    failures, on one synthetic pair -- read every change_set by hand before
    trusting it, exactly as apply_change_set's own docstring already warns.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    try:
        trigger_uuid = UUID(trigger_id)
    except ValueError:
        return f"REFUSED: {trigger_id!r} is not a valid UUID."

    orchestrator = LoopOrchestrator(pool, default_panel(), default_judge())
    try:
        scorecards = await orchestrator.run(trigger_uuid)
    except LookupError as exc:
        return f"REFUSED: {exc}"
    except (asyncio.CancelledError, BaseException):
        # REAL BUG FOUND VIA ACTUAL MCP INSPECTOR TESTING (not hypothetical):
        # a client that disconnects/times out mid-debate leaves the debate
        # stuck at IN_DEBATE forever -- IN_DEBATE's only legal predecessor is
        # OPEN (app/debate/state_machine.py's own transition table), so a
        # retry on the same trigger_id hits "cannot move debate from
        # IN_DEBATE to IN_DEBATE" and the trigger is permanently unusable
        # without manual intervention. REJECTED is a legal successor of
        # IN_DEBATE (state machine explicitly allows this), so close it out
        # cleanly here instead of leaving an orphan -- using the real
        # DebateStateMachine.transition(), not a raw UPDATE, so
        # debate_events keeps an honest record of what happened.
        row = await pool.fetchrow(
            "SELECT debate_id FROM triggers WHERE id = $1", trigger_uuid
        )
        if row and row["debate_id"] is not None:
            machine = DebateStateMachine(pool)
            try:
                state = await machine.current_state(row["debate_id"])
                if state not in ("APPROVED", "REJECTED"):
                    await machine.transition(
                        row["debate_id"], "REJECTED",
                        reason="orphaned: tool call cancelled/failed mid-debate",
                        actor="propose_synthesis_cleanup",
                    )
            except Exception:  # noqa: BLE001 -- best-effort cleanup; the
                # original cancellation/error is what actually matters and
                # must not be swallowed by a cleanup failure.
                pass
        raise

    if not scorecards:
        return (
            "No scorecards produced -- the panel either reached no candidate, "
            "no candidate reached the minimum supporter threshold, or every "
            "candidate failed structural validation. Check the debates/"
            "debate_events tables for the real reason (state machine "
            "transition + reason string were persisted even though no "
            "scorecard was)."
        )

    lines = [f"{len(scorecards)} real scorecard(s) produced:"]
    for sc in scorecards:
        row = await pool.fetchrow(
            "SELECT change_set FROM candidates WHERE id = $1", sc.candidate_id
        )
        change_set_json = json.dumps(row["change_set"]) if row else "null"
        lines.append(
            f"\n--- candidate {sc.candidate_id} ---\n"
            f"summary: {sc.summary}\n"
            f"proposers: {sc.proposers}\n"
            f"layer1.passed: {sc.layer1.passed}\n"
            f"recommendation: {sc.recommendation}\n"
            f"change_set: {change_set_json}"
        )
    return "\n".join(lines)


def _render_step(step) -> str:
    """One stored procedure step, as a line for the agent's memory block.

    Prefers `goal` (the generalized phrasing) over `action` (the literal
    "Call Read (3x)") because the abstract form is what transfers to a
    different task -- falling back to `action` for rows written before
    steps carried a goal, and to the raw value for anything that isn't a
    dict at all (the previous behaviour, kept).
    """
    if not isinstance(step, dict):
        return str(step)
    text = step.get("goal") or step.get("action") or str(step)
    tools = [
        impl.get("name") for impl in (step.get("allowed_implementations") or [])
        if isinstance(impl, dict) and impl.get("name")
    ]
    return f"{text}  [tool: {', '.join(tools)}]" if tools else text


async def _bind_plan_to_registry(pool, compiled_plan, procedure_payload: dict):
    """Directive Sec 20's "resolve -> bind" stage, run for real, over the
    ONE real task_node link a stored procedure already carries:
    `procedures.migrated_from_task_node_id` (`db/18_procedures.sql`) --
    populated when a procedure was migrated from a task_node's own
    htn_method_library entry, `NULL` for an ad-hoc/directly captured
    procedure. This never fabricates a task_node_id from a goal string
    (see `implementation_executor.py`'s own module docstring on exactly
    that refusal) -- every real node of a plan compiled from this
    procedure's steps is treated as satisfying that SAME task (a
    procedure-level, not step-level, link, which is the only real one
    that exists today), and a `None` link is a true no-op: the plan comes
    back byte-identical, matching the registry's own "nothing resolved"
    behavior everywhere else.

    Run AFTER `compile_plan()` and BEFORE `persist_compiled_plan()` at
    every real production call site, so a durable implementation bound
    here is frozen into the persisted `task_graphs.nodes` row itself, not
    merely resolved in memory and discarded.

    PLAN-PINNING GUARD (directive Sec 31 -- "a newly registered
    implementation must not silently replace an implementation already
    frozen into a plan"): checks `find_plan_for_task` -- keyed on the
    real, stable (`procedure_row_id`, `task_description`) pair, NOT
    `content_hash` -- first. `bind_plan_implementations` deliberately
    changes `content_hash` when it freezes an implementation (see its own
    docstring), so a content_hash lookup on this fresh, not-yet-bound
    compile could never find an already-bound stored plan; the
    (procedure_row_id, task_description) pair is stable across binding
    and is exactly "the same task, replayed." When a plan for that pair
    is already persisted, its already-frozen nodes are returned
    UNCHANGED -- never re-resolved -- so a newer implementation activated
    after the original run cannot change which implementation a replay
    of that exact plan binds to. Only a genuinely first-time compile (no
    existing row for this pair) resolves and freezes a fresh binding.
    """
    task_node_id = procedure_payload.get("migrated_from_task_node_id")
    if not task_node_id:
        return compiled_plan
    from app.execution.plan_persistence import find_plan_for_task

    existing = await find_plan_for_task(
        pool, procedure_row_id=compiled_plan.plan.procedure_row_id,
        task_description=compiled_plan.plan.task_description,
    )
    if existing is not None:
        return existing

    from app.execution.implementation_executor import bind_plan_implementations

    return await bind_plan_implementations(
        pool, compiled_plan, scope=AccessScope.unrestricted(),
        task_node_ids={n.order: str(task_node_id) for n in compiled_plan.graph.nodes},
    )


async def _respond_tier1_hit(pool, task_description: str, matched_procedure: dict) -> str:
    """Tier-1 lookup hit: REAL execution now, not just a returned text
    listing -- one real, cheap LLM call per real stored step, in real
    dependency order, via the same graph_executor.py the tier-2 coding
    demo proved live. This is genuinely still the "seamless, callable
    anywhere" tier: no repo_path, no sandbox, no tool-calling loop -- just
    the procedure's own steps, reasoned through for real, in order.

    A stored procedure's steps are linear by construction (no branching
    field exists -- db/18_procedures.sql's own DDL comment), so
    `steps_to_linear_nodes()` derives deps=[i-1] straight from the
    existing `order` field. No schema change, no fabrication -- proven
    against the real corpus in test_stored_procedure_multistep_live.py.

    Compiles and persists a real execution_plans/task_graphs/executions
    triplet, matching what tier-2 does -- a plan is real whether it then
    runs lightweight reasoning or a full sandboxed agent. What this
    deliberately does NOT do: call record_execution_outcome() (ticket-13
    verification evidence). Evidence.py's own invariant #13 is explicit --
    "the model said it worked is not criteria" -- and per-step reasoning
    here has no objective, externally-checkable success signal the way
    tier-2's real diff/stop_reason check does. Recording it as
    verification evidence would be exactly the self-report evidence.py
    exists to refuse. The Execution row's outcome records "did the graph
    finish running", a materially weaker and more honest claim.
    """
    from app.execution.graph_executor import NodeResult, execute_task_graph
    from app.execution.plan_persistence import persist_compiled_plan, record_plan_execution
    from app.execution.plans import compile_plan
    from app.execution.procedure_graph import ProcedureCompositionError, expand_procedure_steps

    steps = matched_procedure.get("steps") or [{"order": 0, "goal": task_description}]
    is_verified = matched_procedure.get("verification_state") == "verified"
    status = (
        f"verified, {matched_procedure['verification_stats'].get('successes', 0)} prior successes"
        if is_verified
        else "UNVERIFIED -- opted in via allow_unverified_procedures, use at your own judgment"
    )

    # Phase 10 (memory-substrate map): expand any subprocedure_ref steps
    # into their referenced procedure's own real steps BEFORE compiling --
    # compile_plan itself stays pure/pool-free (its own documented
    # invariant); expansion is the async, DB-touching step that runs
    # before it, same as every other real caller of compile_plan.
    #
    # Robustness fix (found live once the is_engineering_fixture drift
    # was fixed and 1496 previously-hidden real corpus procedures became
    # matchable again): a matched procedure with a dangling/cyclic/too-
    # deep subprocedure_ref must REFUSE gracefully, not crash
    # find_best_way with an unhandled exception -- a single corrupted
    # corpus row must never be able to take the whole tool down for
    # every future caller whose query happens to match it.
    try:
        nodes = await expand_procedure_steps(
            pool, procedure_id=matched_procedure["procedure_id"],
            procedure_version=matched_procedure["version"], steps=steps,
        )
    except ProcedureCompositionError as exc:
        return (
            f"REFUSED: matched procedure {matched_procedure['procedure_id']} has an "
            f"unresolvable composed step -- {exc}"
        )
    compiled_plan = compile_plan(
        procedure_id=matched_procedure["procedure_id"],
        procedure_version=matched_procedure["version"],
        procedure_row_id=matched_procedure["id"],
        procedure_payload=matched_procedure,
        task_description=task_description,
        nodes=nodes,
        extractor_version="find_best_way_plan_compiler@1",
        created_by="find_best_way",
    )
    compiled_plan = await _bind_plan_to_registry(pool, compiled_plan, matched_procedure)
    compiled_plan, _ = await persist_compiled_plan(pool, compiled_plan)

    client = OpenAI(
        max_retries=0,
        api_key=settings.require("general_compute_api_key"),
        base_url=settings.general_compute_base_url,
    )

    async def run_node(node) -> NodeResult:
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model="gemma-4-31B-it",
            messages=[
                {"role": "system", "content": f"Overall task: {task_description}"},
                {"role": "user", "content": node.goal + " Answer concretely, in 1-3 sentences."},
            ],
            max_tokens=150,
        )
        text = (resp.choices[0].message.content or "").strip()
        return NodeResult(status="success" if text else "failure", notes=text)

    result = await execute_task_graph(compiled_plan.graph, run_node=run_node)
    from app.execution.implementation_executor import plan_implementation_id

    await record_plan_execution(
        pool, compiled=compiled_plan,
        outcome=result.outcome,
        created_by=_resolve_caller_identity(fallback="find_best_way"),
        implementation_id=plan_implementation_id(compiled_plan),
    )

    steps_text = "\n".join(
        f"  {order + 1}. {node.goal}\n"
        f"     -> {result.node_results[order].notes if order in result.node_results else '(skipped -- blocked by an earlier failed step)'}"
        for order, node in enumerate(sorted(compiled_plan.graph.nodes, key=lambda n: n.order))
    )

    return (
        f"Found existing best-known way ({status}): {matched_procedure['name']}\n"
        f"Reasoned through {len(compiled_plan.graph.nodes)} real step(s) "
        f"(outcome: {result.outcome}):\n{steps_text}\n\n"
        "(lookup-tier reasoning only -- no sandboxed run, no file edits; "
        "pass mode='full_run' with a repo_path for that)"
    )


async def _respond_plan_only(
    pool, task_description: str, matched_procedure: dict,
    route: Optional[str] = None, route_decision_id: Optional[str] = None,
    workspace_id: Optional[str] = None, session_id: Optional[str] = None,
    parent_run_id: Optional[str] = None, parent_node_id: Optional[str] = None,
    ancestor_chain=None, repo_path: Optional[str] = None,
    relevant_claim_refs: Optional[list[dict]] = None,
    implementation_candidates: Optional[list[dict]] = None,
) -> str:
    """`mode='plan_only'`: compile and persist the real execution graph
    (same expand_procedure_steps -> compile_plan -> persist_compiled_plan
    pipeline `_respond_tier1_hit` uses) and hand it back as structured
    data -- WITHOUT calling this server's own LLM even once. No
    `client.chat.completions.create`, no `execute_task_graph`, no
    `record_plan_execution` (nothing ran yet, there is no outcome to
    record).

    WHY THIS EXISTS: a caller that is itself an LLM-driven agent host
    (Claude Code, Cursor, any MCP-embedded client) already pays for its
    own reasoning/tool-calling -- `_respond_tier1_hit`'s per-step
    reasoning call and tier-2's sandboxed Agent loop both spend THIS
    server's own configured LLM credentials
    (settings.require("general_compute_api_key")) redundantly on top of
    that. This mode returns the real, composed step plan (subprocedure
    references already expanded, dependencies already resolved) as data
    the caller executes with its OWN LLM and its OWN native file/tool
    capabilities, then reports back via `report_execution` -- StealthLab
    stays pure procedural memory for this call, never an executor.

    The compiled plan IS still persisted (a real execution_plans/
    task_graphs row -- cheap, DB-only, no LLM) so a later `report_execution`
    call has a real plan to attach evidence to, same content-hash dedup
    contract every other compile_plan caller gets.
    """
    from app.execution.plan_persistence import persist_compiled_plan
    from app.execution.plans import compile_plan
    from app.execution.procedure_graph import ProcedureCompositionError, expand_procedure_steps

    steps = matched_procedure.get("steps") or [{"order": 0, "goal": task_description}]
    try:
        nodes = await expand_procedure_steps(
            pool, procedure_id=matched_procedure["procedure_id"],
            procedure_version=matched_procedure["version"], steps=steps,
        )
    except ProcedureCompositionError as exc:
        return (
            f"REFUSED: matched procedure {matched_procedure['procedure_id']} has an "
            f"unresolvable composed step -- {exc}"
        )
    compiled_plan = compile_plan(
        procedure_id=matched_procedure["procedure_id"],
        procedure_version=matched_procedure["version"],
        procedure_row_id=matched_procedure["id"],
        procedure_payload=matched_procedure,
        task_description=task_description,
        nodes=nodes,
        extractor_version="find_best_way_plan_compiler@1",
        created_by="find_best_way",
    )
    compiled_plan = await _bind_plan_to_registry(pool, compiled_plan, matched_procedure)
    compiled_plan, _ = await persist_compiled_plan(pool, compiled_plan)

    # MCP hardening B3/B32: a real, durable procedure_run_id for a
    # Procedure accepted for use via plan_only -- created 'pending', never
    # driven (no execute_run call). The host later reports progress via
    # report_execution and, once this gate's continue_run tool exists,
    # inspects/advances it by that id instead of re-searching.
    from app.execution.durable_graph import create_pending_run

    # B9-B13: the cycle check needs a concrete candidate -- only known
    # now, this call's own matched_procedure. Depth/budget/wall-clock
    # were already checked (cheaply, before any of this compile/persist
    # work) by the caller.
    if ancestor_chain is not None:
        from app.execution.recursion_guard import assert_no_cycle
        assert_no_cycle(ancestor_chain, matched_procedure["procedure_id"])

    trace_id = await _resolve_trace_id(pool, parent_run_id=parent_run_id, session_id=session_id)
    from app.services.verification import compute_verification_plan_id
    verification_plan_id = compute_verification_plan_id(matched_procedure)
    procedure_run_id = await create_pending_run(
        pool, compiled_plan,
        procedure_id=matched_procedure["procedure_id"],
        procedure_version=matched_procedure["version"],
        created_by=_resolve_caller_identity(fallback="find_best_way_plan_only"),
        scope_type=compiled_plan.plan.scope_type,
        scope_entity_id=compiled_plan.plan.scope_entity_id,
        workspace_id=workspace_id, trace_id=trace_id,
        verification_plan_id=verification_plan_id,
        route_decision_id=route_decision_id,
        parent_run_id=parent_run_id, parent_node_id=parent_node_id,
    )

    # MCP hardening B35: best-effort .stealth/ projection refresh -- a
    # filesystem write failure (permissions, no repo_path, read-only
    # mount) must never break the primary MCP response, but it must also
    # never be silently swallowed; the payload's own `stealth_projection`
    # field says what happened.
    stealth_projection_status: Optional[str] = None
    if repo_path is not None:
        from app.execution.stealth_projection import generate_projection

        try:
            await generate_projection(pool, workspace_root=repo_path, procedure_run_id=procedure_run_id)
            stealth_projection_status = "written"
        except OSError as exc:
            stealth_projection_status = f"write_failed: {exc}"

    payload = {
        "mode": "plan_only",
        "route": route,
        "route_decision_id": route_decision_id,
        "procedure_run_id": procedure_run_id,
        "stealth_projection": stealth_projection_status,
        "procedure_id": str(matched_procedure["procedure_id"]),
        "procedure_row_id": str(matched_procedure["id"]),
        "version": matched_procedure["version"],
        "name": matched_procedure["name"],
        "verification_state": matched_procedure.get("verification_state"),
        "invariants": matched_procedure.get("invariants") or [],
        "preconditions": matched_procedure.get("preconditions") or [],
        # B1/B32: real results of this call's own relevant-Claims-retrieval
        # and candidate-Implementation-resolution pipeline steps (route_
        # decision.py::decide_route) -- never fabricated, and empty exactly
        # when nothing real was found (never padded to look complete).
        "relevant_claim_refs": relevant_claim_refs or [],
        "implementation_candidates": implementation_candidates or [],
        "missing_required_implementations": not bool(implementation_candidates),
        "steps": [
            {
                "order": node.order, "goal": node.goal,
                "deps": node.deps,
                "step_ref": (
                    {"procedure_id": str(node.step_ref.procedure_id), "version": node.step_ref.version}
                    if node.step_ref else None
                ),
            }
            for node in sorted(compiled_plan.graph.nodes, key=lambda n: n.order)
        ],
        "execution_plan_id": str(compiled_plan.plan.id),
        "instructions": (
            "No StealthLab-side LLM call was made for this plan. Execute "
            "these steps yourself, in dependency order, using your own "
            "reasoning and tools against the real repository. Call "
            "continue_run(procedure_run_id=<procedure_run_id above>) at any "
            "point to get the current next-action packet anchored to this "
            "exact run. When done, call report_execution(procedure_id=<"
            "procedure_id above>, success=<bool>, context_key=<a real "
            "identifier for this run's environment, e.g. the repo name>, "
            "steps_used=<int>) so the outcome becomes real evidence."
        ),
    }
    return json.dumps(payload, indent=2, default=str)


@server.tool()
async def find_best_way(task_description: str, ctx: Context,
                         repo_path: Optional[str] = None,
                         mode: str = "auto",
                         model: str = "gemma-4-31B-it", max_steps: int = 25,
                         session_id: Optional[str] = None,
                         allow_unverified_procedures: bool = False,
                         resume_run_id: Optional[str] = None,
                         workspace_id: Optional[str] = None,
                         parent_run_id: Optional[str] = None,
                         parent_node_order: Optional[int] = None) -> str:
    """
    Two-tier: find the best known way to do this, seamlessly callable at
    any point in a workflow -- not just as a heavyweight task entrypoint.

    TIER 1 (lookup, always runs first, no repo_path required): checks
    `find_applicable_procedures()` for a strong existing match and, if one
    exists, returns it immediately -- sub-second, no sandboxed agent run.
    This is what makes the tool safe to call opportunistically mid-workflow
    ("is there a known best way to do this?") rather than only as a
    committing, expensive action.

    TIER 2 (execution, needs repo_path): the real, sandboxed, tool-calling
    agent loop -- runs only when tier 1 found nothing strong enough, or
    `mode="full_run"` asks for it explicitly. This is everything
    `solve_task` (this tool's previous name) used to always do
    unconditionally.

    `mode`: "auto" (default) -- tier 1, falling back to tier 2 if needed
    and `repo_path` is given. "lookup_only" -- tier 1 only, ever; an honest
    "no strong match" is a normal answer, not an error. "full_run" -- skip
    tier 1, go straight to tier 2 (requires `repo_path`). "plan_only" --
    like "lookup_only" (tier 1, never falls to tier 2), but returns the
    real compiled step plan as structured JSON instead of running this
    server's own LLM to reason through it -- for a caller that is itself
    an LLM-driven agent (Claude Code, Cursor, any MCP-embedded host) and
    would rather spend its OWN reasoning on the steps than pay for a
    redundant server-side pass. See `_respond_plan_only`'s own docstring.

    EVERY tier-2 run persists a real execution plan (Band 1.7:
    `execution_plans`/`task_graphs`/`executions`, see
    `app/execution/plan_persistence.py`) -- matched runs reference the
    matched procedure; unmatched runs first capture a fresh ad-hoc
    `candidate` procedure from the task itself (via `capture_procedure()`)
    so the plan always has a real procedure to reference, never a
    workaround. This also means an unmatched run leaves the substrate a
    real, reusable candidate it didn't have before, same "candidate first,
    earn verified later" discipline used everywhere else in this codebase.

    Retrieval-grounded coding agent: find prior solved patterns relevant to
    this task, then run a real, sandboxed, tool-calling agent loop against
    an on-disk repo to solve it.

    HONEST STATUS -- this is NOT a pure wrapper like the other three tools
    (this module's own docstring's "no new business logic" claim does not
    fully hold here, stated plainly rather than glossed over):
      - RepoSandbox and Agent are reused VERBATIM from
        experiments/swebench_pro/agent.py -- the real, already-tested
        sandboxed file-edit/read/search machinery and tool-calling loop
        (retry/backoff on transient provider errors included).
      - Agent.run()'s `instance` dict normally carries SWE-bench-specific
        fields (requirements/interface/etc, all optional and gracefully
        degraded via spec_block()'s real .get()-based handling) -- this
        tool constructs a MINIMAL instance ({instance_id, repo,
        problem_statement}) instead, since a general coding task has no
        SWE-bench spec fields to carry. The SYSTEM prompt's own wording
        ("fixing a real bug") is SWE-bench-flavored language that still
        functions correctly for non-bug-fix tasks (add/refactor/etc), but
        reads slightly off -- a real, minor rough edge, not a functional one.
      - Retrieval grounding (retrieve_precedent's real underlying
        _vector_candidates) is genuinely wired in as Agent.run()'s
        memory_block, matching Experiment 4's real validated finding that
        retrieved trajectories helped an SLM pass on the first try. HONEST
        GAP: _vector_candidates only returns name/id/similarity, not full
        trajectory text -- Experiment 4's real flow fetched full content
        separately once a match was found; this tool does not yet do that
        second fetch, so memory_block here is a pointer/summary, not the
        full retrieved trajectory Experiment 4 actually validated.
      - STRUCTURAL CONTEXT (handoff item 2, real wiring added here):
        retrieve_local_first()'s structural/temporal/semantic union
        (local_retrieval.py) is genuinely called now, via
        assemble_structural_context(). Cold-start caveat, stated in that
        function's own docstring and repeated here because it matters at
        THIS call site specifically: without `session_id`, or on a
        session with no prior file_touched observations, the structural
        and temporal tiers are seeded from `git diff --name-only HEAD`
        instead (uncommitted repo state) -- lower precision than a real
        session's working set, and this tool's own doc says so rather
        than presenting it as equivalent.
      - EXTRACTION (memory-substrate blocker #1, closed here): a run that
        finishes with a real diff (stop_reason=="finished" and a
        non-empty patch) is fed to extract_procedure() afterward, via
        AgentRunEvidenceSource built from this run's own files_edited/
        tool_calls. This is the first live caller extract_procedure() has
        ever had outside its own tests -- a successful find_best_way tier-2 call now
        can, not always will (V5/validators can still refuse), produce a
        real procedures row. That row is NOT verified on creation
        (verification_state defaults unverified; ticket 13's >=10
        successes/0 failures/>=3 contexts gate still applies before
        automatic retrieval will ever surface it) -- extraction closes the
        write side of the loop, not the bootstrap-cold-start gap on the
        read side (memory-substrate blocker #2, still open).

    SECURITY, stated plainly, not discovered later: repo_path is
    caller-controlled. RepoSandbox refuses to let edits escape repo_path
    itself (real, tested path-traversal guard), but nothing in this tool
    stops a caller from pointing repo_path at a sensitive real directory on
    this server's filesystem in the first place. Fine for a trusted/internal
    deployment (this project's current, explicit, accepted-for-now
    posture per the handoff) -- a real gap to close before any
    untrusted-multi-tenant deployment.

    task_description: plain-language description of the coding task --
    ordinary phrasing, same as retrieve_precedent's query.
    repo_path: absolute path to an existing repo checkout on this server's
    filesystem.
    model: defaults to the same real default used elsewhere in this project
    (run_graph_experiment.py's --model default) via General Compute --
    NOT Groq qwen3.6-27b, the real, measured, more-expensive SLM choice
    from Experiment 4's cost finding.
    max_steps: tool-call budget for the agent loop.
    session_id: optional -- when a real Claude Code (or other) session id
    is known for this call, its file_touched/commit_made observations
    seed the structural tier at real, session-scoped precision. Omitted
    or unknown: falls back to a git-diff seed (see STRUCTURAL CONTEXT
    above).
    allow_unverified_procedures: default False keeps this call's procedure
    retrieval on the production default (require_verified=True -- ticket
    13's >=10 successes/0 failures/>=3 contexts bar, real evidence only).
    Pass True to ALSO consider procedures this same extraction pass has
    ever created that haven't earned that evidence yet (memory-substrate
    blocker #2's opt-in path) -- a real, deliberate developer choice to
    try a candidate on its own unverified merits, not a way to make
    verification optional by default. Only affects retrieval; extraction
    (see EXTRACTION above) always runs on a real success regardless of
    this flag.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    from app.services.route_decision import (
        RouteDecision as _RouteDecision,
        classify_intent,
        decide_route,
        persist_route_decision,
    )

    async def _refuse(reason: str) -> str:
        # B2: every route, including refusals, is persisted -- "routing
        # becomes observable and testable", not merely a returned string.
        await persist_route_decision(pool, _RouteDecision(
            route="refused", reason=reason, intent=classify_intent(task_description, mode),
            task_description=task_description, mode=mode, repo_path=repo_path,
            session_id=session_id, workspace_id=workspace_id,
            requires_repository=repo_path is not None,
        ))
        return f"REFUSED: {reason}"

    if mode not in ("auto", "lookup_only", "full_run", "plan_only"):
        return await _refuse(
            "mode must be one of 'auto', 'lookup_only', 'full_run', "
            f"'plan_only' (got {mode!r})."
        )
    if repo_path is not None and not os.path.isdir(repo_path):
        return await _refuse(f"repo_path {repo_path!r} is not a directory on this server.")
    if mode == "full_run" and repo_path is None:
        return await _refuse("mode='full_run' requires repo_path.")

    # Phase 1 P0: hosted-mode repo authorization. Local mode: passthrough.
    try:
        repo_path = await _authorize_repo_execution(ctx, repo_path, workspace_id)
    except _RepoExecutionRefused as exc:
        return await _refuse(str(exc))
    if repo_path is not None and not os.path.isdir(repo_path):
        return await _refuse(f"repo_path {repo_path!r} is not a directory on this server.")

    # MCP hardening B9-B14: dynamic recursive child retrieval. A caller
    # (typically the host, mid-execution of a PARENT run) names
    # parent_run_id/parent_node_order to signal "this find_best_way call
    # is for a reusable subproblem of that specific run/step" -- exactly
    # B14's "find_best_way(goal=subproblem, parent_run_id=..., ...)".
    # Budget/depth/wall-clock are checked NOW (cheap, no embedding cost);
    # the cycle check (needs a candidate procedure) runs later, once one
    # is chosen, right before any child run is actually created.
    from app.execution.recursion_guard import (
        ChildExecutionBudgetExceeded, CostBudgetExceeded, RecursionCycleDetected,
        RecursionDepthExceeded, ToolCallBudgetExceeded, TokenBudgetExceeded,
        WallClockBudgetExceeded, assert_no_cycle, check_recursion_limits,
    )

    parent_node_row_id: Optional[str] = None
    ancestor_chain = None
    if parent_run_id is not None:
        if parent_node_order is not None:
            parent_node_row_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id = $1 AND node_order = $2",
                parent_run_id, parent_node_order,
            )
            if parent_node_row_id is None:
                return await _refuse(
                    f"parent_node_order {parent_node_order} not found on parent_run_id {parent_run_id!r}"
                )
            parent_node_row_id = str(parent_node_row_id)
        try:
            ancestor_chain = await check_recursion_limits(
                pool, parent_run_id=parent_run_id, parent_node_id=parent_node_row_id,
            )
        except (
            RecursionDepthExceeded, ChildExecutionBudgetExceeded, WallClockBudgetExceeded,
            TokenBudgetExceeded, ToolCallBudgetExceeded, CostBudgetExceeded,
        ) as exc:
            return await _refuse(str(exc))

    from app.services.applicability import find_applicable_procedures
    from app.services.environment_probe import invariant_bindings_from_facts, probe_environment
    from app.services.procedures import capture_procedure, get_procedure, record_execution_outcome

    embedder = Embedder()
    query_vec = await embedder.embed_one(task_description, input_type="query")

    # TIER 1 -- lookup, always attempted, no repo_path required. Scope
    # narrowing (below) is repo-dependent and simply skipped without one;
    # a repo-less call still gets a real, if less-narrowed, match attempt.
    procedure_scope: dict = {}
    invariant_bindings: dict = {}
    if repo_path is not None:
        # Synchronous filesystem reads (a handful of specific top-level
        # files -- package.json/lockfiles/requirements.txt/pyproject.toml,
        # not a repo walk) -- off the event loop regardless of how cheap,
        # same discipline as every other blocking call in this tool.
        facts = await asyncio.to_thread(probe_environment, repo_path)
        lang = next((f.object for f in facts if f.predicate == "language"), None)
        if lang:
            procedure_scope = {"language": [lang]}
        # Phase 3: real package_version facts, converted to the numeric
        # bindings check_hard_constraints()'s invariant stage actually
        # consumes -- the connective tissue that was missing (invariants
        # existed and were already wired through find_applicable_procedures,
        # but nothing real ever populated invariant_bindings before this).
        invariant_bindings = invariant_bindings_from_facts(facts)

    # require_verified DEFAULTS TO TRUE and is deliberately left there, not
    # weakened to False to make something show up here today. Ticket 13's
    # own wording: "verified gates automatic retrieval; a candidate
    # procedure remains explicitly invocable." This IS automatic selection
    # (the system chose to look, nothing named this procedure by id), so
    # the honest behavior is: nothing is returned until a procedure has
    # real evidence (>=10 successes, 0 failures, >=3 distinct contexts) --
    # which this exact call path is what will, over repeated real use,
    # accumulate.
    matched_procedures = await find_applicable_procedures(
        pool, goal_embedding=query_vec, current_scope=procedure_scope, limit=1,
        require_verified=not allow_unverified_procedures,
        invariant_bindings=invariant_bindings,
        embedding_model_id=embedder.embedding_model_id(),
    )
    matched_procedure = matched_procedures[0] if matched_procedures else None

    # B1/B2: the formal RouteDecision, computed and persisted regardless
    # of which branch below ends up answering -- by this point repo
    # authorization has already succeeded (or repo_path is None), so
    # `authorized=True` here reflects that, not a re-check.
    route_decision = await decide_route(
        pool, task_description=task_description, mode=mode, repo_path=repo_path,
        authorized=True, authorization_detail={},
        goal_embedding=query_vec, current_scope=procedure_scope,
        invariant_bindings=invariant_bindings,
        require_verified=not allow_unverified_procedures,
        embedding_model_id=embedder.embedding_model_id(),
        goal_text=task_description, session_id=session_id, workspace_id=workspace_id,
    )
    route_decision_id = await persist_route_decision(pool, route_decision)

    if matched_procedure is not None and mode == "plan_only":
        try:
            return await _respond_plan_only(
                pool, task_description, matched_procedure,
                route=route_decision.route, route_decision_id=route_decision_id,
                workspace_id=workspace_id, session_id=session_id,
                parent_run_id=parent_run_id, parent_node_id=parent_node_row_id,
                ancestor_chain=ancestor_chain, repo_path=repo_path,
                relevant_claim_refs=route_decision.relevant_claim_refs,
                implementation_candidates=route_decision.implementation_candidates,
            )
        except RecursionCycleDetected as exc:
            return await _refuse(str(exc))
    if matched_procedure is not None and mode != "full_run":
        return await _respond_tier1_hit(pool, task_description, matched_procedure)
    if mode in ("lookup_only", "plan_only"):
        return (
            "No strong existing match found (this is a normal, honest "
            "answer, not a failure) -- pass mode='full_run' with a "
            "repo_path to solve it fresh."
        )
    if repo_path is None:
        return (
            "No strong existing match found, and no repo_path was given -- "
            "pass repo_path to run a full solve (mode='auto' or 'full_run')."
        )
    if route_decision.route == "needs_clarification":
        # B1's core new behavior: do NOT silently fall through to a real
        # sandboxed tier-2 run when the single best-matching procedure is
        # blocked only on an UNKNOWN (not violated) precondition -- ask,
        # rather than either fabricate applicability or refuse outright.
        # Only reachable here: matched_procedure is None (a genuine match
        # can never be "needs_clarification" -- see decide_route), mode
        # is 'auto' or 'full_run' (lookup_only/plan_only already returned
        # above, unchanged), and repo_path is not None (side-effecting
        # execution is the thing being gated).
        return json.dumps({
            "route": "needs_clarification",
            "route_decision_id": route_decision_id,
            "reason": route_decision.reason,
            "near_miss_procedure_id": route_decision.procedure_id,
            "near_miss_procedure_row_id": route_decision.procedure_row_id,
            "blocking_unknowns": route_decision.decision_critical_unknowns,
            "instructions": (
                "One or more preconditions above have no known answer in "
                "the current scope (not violated -- simply never asserted). "
                "Resolve them (e.g. supply the missing fact as a claim, or "
                "probe the environment) and call find_best_way again, or "
                "pass allow_unverified_procedures=True / a different "
                "task_description if you want to proceed without this "
                "procedure's guidance."
            ),
        }, indent=2)

    # TIER 2 -- execution. Everything below is what this tool always did
    # unconditionally under its previous name (solve_task); it now only
    # runs when tier 1 didn't already answer the question.
    raw_candidates = await _vector_candidates(pool, query_vec, AccessScope.unrestricted())
    candidates = [c for c in raw_candidates if c.similarity >= RETRIEVE_PRECEDENT_THRESHOLD]
    memory_block = ""
    if candidates:
        memory_block = "Prior solved pattern(s) that may be relevant (see HONEST GAP above -- summary only, not full trajectory text):\n" + "\n".join(
            f"- [{c.table}] {c.name} (similarity={c.similarity:.2f})" for c in candidates
        )

    # Structural context (handoff item 2's real wiring): a genuinely
    # independent retrieval path from the precedent lookup above -- see
    # local_retrieval.py's own header for why union, not cascade, is the
    # right composition. Best-effort: a git failure or an empty repo
    # must not abort the whole tool, so seed_files defaults to [] rather
    # than propagating an exception.
    seed_files: list[str] = []
    try:
        git_diff = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=5,
        )
        if git_diff.returncode == 0:
            seed_files = [line.strip() for line in git_diff.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        pass

    structural = await assemble_structural_context(
        pool, session_id=session_id, repo_root=repo_path, seed_files=seed_files,
    )
    structural_result = await retrieve_local_first(
        pool, task_description, embedder=embedder, structural=structural,
    )
    if structural_result.text:
        structural_block = (
            "Structurally/temporally relevant context from this repo "
            f"(tiers: {structural_result.tiers_included}):\n{structural_result.text}"
        )
        memory_block = f"{memory_block}\n\n{structural_block}" if memory_block else structural_block
    if matched_procedure:
        steps_text = "\n".join(
            f"  {i+1}. {_render_step(s)}"
            for i, s in enumerate(matched_procedure.get("steps") or [])
        )
        is_verified = matched_procedure.get("verification_state") == "verified"
        status = (
            f"verified, {matched_procedure['verification_stats'].get('successes', 0)} prior successes"
            if is_verified
            else "UNVERIFIED -- opted in via allow_unverified_procedures, use at your own judgment"
        )
        procedure_block = (
            f"Relevant learned procedure found ({status}): {matched_procedure['name']}\n{steps_text}"
        )
        memory_block = f"{memory_block}\n\n{procedure_block}" if memory_block else procedure_block

    # Every tier-2 run gets a real procedure to reference (find_best_way's
    # "every run gets a persisted plan" decision) -- a matched run uses the
    # match; an unmatched run captures a fresh ad-hoc `candidate` now,
    # BEFORE executing, from the task itself. No evidence exists yet (the
    # run hasn't happened), so this is capture_procedure() directly, not
    # extract_procedure() -- same distinction extract_procedure()'s own
    # module docstring draws between capture-time and extraction-time.
    if matched_procedure is not None:
        plan_procedure_row_id = str(matched_procedure["id"])
    else:
        adhoc_owner = _resolve_caller_identity(fallback="find_best_way_adhoc")
        adhoc = await capture_procedure(
            pool, name=f"ad-hoc: {task_description[:80]}", goal=task_description,
            steps=[{"order": 0, "goal": task_description}],
            provenance="system_pending_review", scope_type="global",
            created_by=adhoc_owner,
            # B19 fix: local runtime learning from a user's own execution
            # starts PRIVATE, never implicitly public (spec rule 11:
            # "Local/private knowledge never becomes global implicitly").
            # capture_procedure()'s own default is 'public' -- correct
            # for its OTHER real callers (bulk skill-package ingestion,
            # explicit publication, explicit submit_procedure), wrong for
            # this one, which app/api/procedures.py's own REST capture
            # endpoints already get right (visibility="private" there
            # too) -- this call site was the one outlier, now fixed to
            # match that established, already-correct precedent.
            # owner_id MUST be set alongside visibility="private" --
            # access.py's visibility_predicate() matches private rows via
            # `owner_id = viewer_id`; a NULL owner_id would make this row
            # invisible to EVERYONE, including its own creator.
            visibility="private", owner_id=adhoc_owner,
        )
        plan_procedure_row_id = adhoc["id"]
    procedure_payload = await get_procedure(pool, plan_procedure_row_id)

    # B9-B13: same deferred cycle check as the plan_only path, done here
    # (before any compile/persist/sandbox work) rather than after --
    # only meaningful for a FRESH run (resuming an existing run_id can
    # never create a new cycle; its parent linkage, if any, was already
    # validated when IT was first created).
    if ancestor_chain is not None and resume_run_id is None:
        try:
            assert_no_cycle(ancestor_chain, procedure_payload["procedure_id"])
        except RecursionCycleDetected as exc:
            return await _refuse(str(exc))

    sandbox = RepoSandbox(repo_path)
    client = OpenAI(
        max_retries=0,
        api_key=settings.require("general_compute_api_key"),
        base_url=settings.general_compute_base_url,
    )

    # Real execution-plan persistence (Band 1.7) -- every tier-2 run,
    # matched or ad-hoc, regardless of outcome: a failed run is still real
    # history worth keeping, not just successes. See
    # app/execution/plan_persistence.py for why this is two/three plain
    # INSERTs and not a transaction.
    from app.execution.graph_executor import NodeResult
    from app.execution.plan_persistence import persist_compiled_plan
    from app.execution.plans import compile_plan
    from app.execution.procedure_graph import ProcedureCompositionError, expand_procedure_steps

    steps = procedure_payload.get("steps") or [{"order": 0, "goal": task_description}]
    # Phase 10: same real expansion as the tier-1 lookup path above --
    # a composed procedure's referenced sub-procedure steps are spliced
    # in before compile_plan sees them.
    try:
        nodes = await expand_procedure_steps(
            pool, procedure_id=procedure_payload["procedure_id"],
            procedure_version=procedure_payload["version"], steps=steps,
        )
    except ProcedureCompositionError as exc:
        return await _refuse(
            f"procedure {procedure_payload['procedure_id']} has an unresolvable "
            f"composed step -- {exc}"
        )
    compiled_plan = compile_plan(
        procedure_id=procedure_payload["procedure_id"],
        procedure_version=procedure_payload["version"],
        procedure_row_id=UUID(plan_procedure_row_id),
        procedure_payload=procedure_payload,
        task_description=task_description,
        nodes=nodes,
        extractor_version="find_best_way_plan_compiler@1",
        created_by="find_best_way",
    )
    compiled_plan = await _bind_plan_to_registry(pool, compiled_plan, procedure_payload)
    compiled_plan, _ = await persist_compiled_plan(pool, compiled_plan)

    # REAL PER-STEP EXECUTION, not one monolithic call over the whole
    # task -- one real Agent.run() per real step, against the SAME
    # sandbox so edits persist between steps, exactly the pattern proven
    # live in test_graph_executor_coding_live.py. Each node's own
    # AgentRun is kept (keyed by order) so the response and the
    # extraction/outcome logic below can aggregate over the real,
    # multi-step run instead of a single call.
    node_runs: dict[int, "AgentRun"] = {}  # noqa: F821 -- forward ref, real type from agent.py
    node_notes: list[str] = []

    async def run_node(node) -> NodeResult:
        prior_context = ("\n\nPrior steps completed:\n" + "\n".join(node_notes)) if node_notes else ""
        node_instance = {
            "instance_id": f"mcp_find_best_way_{secrets.token_hex(6)}_step{node.order}",
            "repo": os.path.basename(os.path.abspath(repo_path)),
            "problem_statement": f"{task_description}\n\nCurrent step: {node.goal}",
        }
        node_agent = Agent(client, model, max_steps=max_steps)
        # Agent.run is SYNCHRONOUS and genuinely blocking (real
        # retry/backoff sleeps included) -- run it in a thread so it
        # doesn't block the event loop, same reasoning TasksExtension's
        # own docstring gives for why this tool needs task-augmentation.
        node_run = await asyncio.to_thread(
            node_agent.run, node_instance, sandbox, "mcp_find_best_way",
            memory_block + prior_context,
        )
        node_runs[node.order] = node_run
        # Per-STEP success is just "the agent finished cleanly" -- unlike
        # the old single-call check, requiring a real diff per node would
        # be wrong here: an investigate-only step (e.g. "locate the bug")
        # never produces a patch, and that is not a failure.
        succeeded = node_run.stop_reason == "finished"
        note = f"step {node.order} ({node.goal}): stop_reason={node_run.stop_reason}, tool_calls={len(node_run.tool_calls)}"
        node_notes.append(note)
        return NodeResult(status="success" if succeeded else "failure", notes=note)

    # DURABLE execution (final-V1 §3): this is THE production tier-2 path,
    # so it runs on the durable substrate -- one execution_run per graph,
    # per-node state persisted, resumable after a crash through this same
    # tool (`resume_run_id`). durable_run appends the immutable
    # `executions` row itself on terminal, so there is no separate
    # record_plan_execution call below any more.
    from app.execution.durable_graph import run_graph_durably

    trace_id = await _resolve_trace_id(pool, parent_run_id=parent_run_id, session_id=session_id)
    from app.services.verification import compute_verification_plan_id
    graph_result = await run_graph_durably(
        pool, compiled_plan, run_node,
        procedure_id=str(compiled_plan.plan.procedure.procedure_id),
        procedure_version=int(compiled_plan.plan.procedure.version),
        created_by=_resolve_caller_identity(fallback="find_best_way"),
        parent_run_id=parent_run_id, parent_node_id=parent_node_row_id,
        scope_type=compiled_plan.plan.scope_type,
        scope_entity_id=compiled_plan.plan.scope_entity_id,
        verification_plan_id=compute_verification_plan_id(procedure_payload),
        side_effecting_orders=set(),  # V1: sandbox is rebuilt per invocation, so a step is replayable on resume (prior steps ride forward as context) -- not a park-on-crash side effect
        resume_run_id=resume_run_id,
        workspace_id=workspace_id, trace_id=trace_id,
        route_decision_id=route_decision_id,
    )
    durable_run_id = graph_result.run_id

    # Aggregate across every real node that actually ran (skipped nodes
    # contribute nothing -- they never called the agent at all).
    all_tool_calls = [tc for r in node_runs.values() for tc in r.tool_calls]
    all_files_edited = sorted({f for r in node_runs.values() for f in r.files_edited})
    combined_patch = "\n".join(r.patch for r in node_runs.values() if r.patch)
    total_prompt_tokens = sum(r.usage.prompt_tokens for r in node_runs.values())
    total_completion_tokens = sum(r.usage.completion_tokens for r in node_runs.values())
    total_calls = sum(r.usage.calls for r in node_runs.values())
    total_wall_seconds = sum(r.wall_seconds for r in node_runs.values())

    # B12: real, atomic accumulation of this run's own resource usage --
    # the ONLY real signal source for tokens/tool-calls this codebase has
    # (AgentRun.usage/tool_calls, already aggregated above for the
    # response text). No cost-per-token pricing table exists anywhere in
    # this codebase, so cost_usd stays 0 here -- an honest "not tracked",
    # never a guessed dollar figure.
    from app.execution.durable_run import record_run_usage

    await record_run_usage(
        pool, durable_run_id, tokens=total_prompt_tokens + total_completion_tokens,
        tool_calls=len(all_tool_calls),
    )

    # Real success proxy, same spirit as the old single-call check --
    # the WHOLE graph must have finished (not partial/needs_rework) AND
    # produced a real, non-empty combined diff. "finished" alone can mean
    # "gave up cleanly", not "succeeded".
    run_succeeded = graph_result.outcome == "success" and bool(combined_patch)
    if matched_procedure:
        await record_execution_outcome(
            pool, procedure_row_id=str(matched_procedure["id"]), success=run_succeeded,
            context_key=os.path.basename(os.path.abspath(repo_path)),
            steps_used=total_calls,
        )

    # NOTE: the immutable `executions` row is appended by durable_run's
    # _finalize (implementation_id pinned via plan_implementation_id) --
    # do NOT call record_plan_execution here or the run gets two.

    # Procedure extraction (memory-substrate blocker #1: extract_procedure()
    # otherwise has zero non-test callers, so nothing a developer does
    # through this tool ever becomes a reusable procedure). Gated on the
    # SAME success proxy as the outcome recording above -- extracting from
    # a run that gave up or produced no diff would try to generalize from
    # nothing; extract_procedure()'s own V5 check refuses that too, but
    # gating here avoids the wasted derive/strategy work entirely.
    extraction_note = None
    if run_succeeded:
        observations = [
            {"observation_type": "file_touched", "label": f, "properties": {"file_path": f}}
            for f in all_files_edited
        ]
        evidence_source = AgentRunEvidenceSource(
            goal_text=task_description, outcome="success",
            observations=observations, tool_sequence=all_tool_calls,
            session_id=session_id, steps_used=total_calls,
        )
        extraction = await extract_procedure(
            pool, evidence_source, client=client, repo_root=repo_path,
            entry_seed_files=seed_files, extractor_scope=procedure_scope,
            # B19 fix: same reasoning as the ad-hoc capture_procedure()
            # call above -- a procedure LEARNED from this user's own
            # execution starts private, never implicitly public. owner_id
            # set alongside it for the same reason (visibility_predicate
            # matches private rows via owner_id = viewer_id).
            visibility="private", owner_id=_resolve_caller_identity(fallback="find_best_way_extract"),
        )
        if extraction.procedure_id:
            extraction_note = (
                f"extracted_procedure: {extraction.procedure_id} "
                f"(extracted_by={extraction.extracted_by}, unverified until real reuse "
                f"accrues evidence -- see should_disable_procedure_retrieval)"
            )
        elif extraction.validation_failures:
            extraction_note = (
                "extraction_skipped: " + "; ".join(extraction.validation_failures)
            )

    errors = [r.error for r in node_runs.values() if r.error]
    lines = [
        f"procedure_run_id: {durable_run_id}",
        f"route_decision_id: {route_decision_id}",
        f"graph_outcome: {graph_result.outcome}",
        f"steps: {len(compiled_plan.graph.nodes)} total, {len(node_runs)} executed "
        f"({len(compiled_plan.graph.nodes) - len(node_runs)} skipped)",
        *[f"  {note}" for note in node_notes],
        f"tool_calls: {len(all_tool_calls)}",
        f"files_edited: {all_files_edited}",
        f"tokens: prompt={total_prompt_tokens}, completion={total_completion_tokens}, calls={total_calls}",
        f"wall_seconds: {total_wall_seconds:.1f}",
    ]
    if errors:
        lines.append(f"errors: {errors}")
    if extraction_note:
        lines.append(extraction_note)
    lines.append("\n--- COMBINED DIFF ---\n" + combined_patch if combined_patch else "\n(no changes made)")
    return "\n".join(lines)


@server.tool()
async def reproduce_procedure(procedure_id: str, repo_path: str, ctx: Context,
                               model: str = "gemma-4-31B-it", max_steps: int = 25,
                               transfer_repo_path: Optional[str] = None,
                               workspace_id: Optional[str] = None,
                               transfer_workspace_id: Optional[str] = None) -> str:
    """
    Deliberately re-run an EXISTING procedure's own steps against a real
    repo to test whether it still reproduces its claimed result, and
    record a real `reproduction` evidence row -- not another
    `execution_result` row.

    WHY THIS TOOL EXISTS (memory-substrate gap, this pass): every
    `verified` procedure today stands ONLY on `execution_result` evidence
    -- organic reuse recorded as a side effect of `find_best_way`'s
    tier-2 runs (see `record_execution_outcome`). `procedure_evidence_
    stats` (db/24_evidence.sql) has always accepted `reproduction` as an
    equally-qualifying evidence type toward the verified-transition gate
    (db/30's engine trigger, `independent_supporting_required >= 1`), but
    until this tool nothing ever produced one -- repeated successful USE
    is real evidence, but it is not the same claim as "this was
    independently re-run specifically to check it still works," which is
    what the founder's spec means by reproduction/replay.

    REPLAY/TRANSFER TIERS (spec's Phase 9 vocabulary -- "Replay A",
    "Replay B", "Transfer C", "Transfer D"): this tool covers exactly two
    of the four, honestly:
      - Replay A (same instance) -- ALWAYS run: re-run against `repo_path`,
        the same repo/instance the caller points at.
      - Transfer C (different repository, same task family) -- run ONLY
        when `transfer_repo_path` is supplied: re-run the SAME procedure's
        SAME stored steps against a second, different real repo checkout.
        "Same task family" is the caller's responsibility to satisfy by
        choosing a `transfer_repo_path` that plausibly needs the same
        procedure -- this tool does not infer task-family membership.
      - Replay B (perturbed same domain -- change irrelevant details,
        still work?) and Transfer D (OOD where reasonable) are explicitly
        DEFERRED, not attempted here. Both need a notion of "perturb this
        repo without changing task-relevance" (B) or a deliberately
        dissimilar-domain corpus (D) that this tool has no machinery for
        yet -- claiming either would be dishonest scope inflation.

    Each tier that actually runs is recorded as its OWN, SEPARATE
    `reproduction` evidence row (never merged into one), distinguished by
    a real, queryable `context_key` convention rather than a new DB
    `evidence_type`: same-instance rows keep the tool's original
    `context_key=f"reproduction:{repo_basename}"` (byte-for-byte
    unchanged from before `transfer_repo_path` existed -- no caller
    observes a behavior change by this tool alone growing a new
    parameter), and a transfer-tier row is written with
    `context_key=f"reproduction:transfer:{repo_basename}->{transfer_basename}"`.
    This is a deliberate "no new schema" choice: `context_key` (db/24_
    evidence.sql) already exists specifically to let downstream capability
    math distinguish "ran here" from "ran there" (see
    `procedure_extraction/failure_handlers.py::capability_for_stream`,
    which already reads `context_key` as the outcome's "environment");
    the `reproduction:transfer:` prefix is a *convention* on that real
    column, queryable with a plain `LIKE 'reproduction:transfer:%'`, not a
    parallel evidence_type that every downstream consumer would need to
    learn about. A first-class `evidence_type` axis was considered and
    rejected for this pass: nothing downstream currently needs "N
    transfer-tier reproductions" to compute differently from "N same-repo
    reproductions" at the SQL aggregate level (both are equally
    `independent_supporting_required` toward `verified` today) -- if that
    changes, the prefix is still there to filter on.

    Reuses the exact execution machinery `find_best_way`'s tier 2 already
    proves live (compile_plan -> persist_compiled_plan -> RepoSandbox +
    Agent per real step via execute_task_graph -> record_plan_execution),
    with one difference: the procedure is NOT looked up by task
    description or ad-hoc captured -- it's fetched by its own
    `procedure_id` and its OWN stored steps are what get re-executed, and
    the outcome is written via `record_execution_outcome(...,
    evidence_type="reproduction")` instead of the default
    `"execution_result"`. When `transfer_repo_path` is given, this whole
    machinery runs a SECOND time against it, independently.

    STALENESS CHECK (Phase 4, runs BEFORE any agent call, only when the
    procedure carries numeric invariants): probes `repo_path`'s real
    environment (same `probe_environment`/`invariant_bindings_from_facts`
    machinery `find_best_way` and `search_procedures` already use) and
    checks the procedure's own invariants against it via
    `invariants.py::check_invariants_async`. A genuine contradiction (not
    an unbound/undecidable variable) marks the procedure
    `staleness='stale'` and returns immediately -- no agent run, since
    re-running a procedure whose environment assumptions the probe just
    disproved would not be testing reproduction, it would be confirming
    what the probe already found. When `transfer_repo_path` is supplied,
    this SAME check also runs against the transfer target's OWN probed
    environment before attempting a transfer run -- a procedure whose
    invariant the transfer repo's real environment contradicts is a real,
    meaningful "transfer failed because the environment doesn't qualify"
    signal, and is reported as such rather than silently skipped.

    procedure_id: a procedures row `id` (not `procedure_id`'s stable
    handle) -- the exact version row being reproduced, matching evidence's
    own version-pinning requirement (V-EVD: "evidence targeting a
    procedure must pin its exact version").
    repo_path: absolute path to an existing repo checkout on this
    server's filesystem, same security posture as `find_best_way`
    (caller-controlled, RepoSandbox path-traversal guarded, not a
    multi-tenant-safe boundary yet).
    transfer_repo_path: optional absolute path to a SECOND, different
    real repo checkout -- "different repository/instance, same task
    family" per the spec's Transfer C tier. When omitted (the default),
    this tool's behavior is unchanged from before this parameter existed.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    # Phase 1 P0: hosted-mode repo authorization for BOTH tiers. Local
    # mode: unchanged passthrough. The transfer tier gets its own
    # workspace authorization when a transfer target is named.
    try:
        repo_path = await _authorize_repo_execution(ctx, repo_path, workspace_id)
        if transfer_repo_path is not None or transfer_workspace_id is not None:
            transfer_repo_path = await _authorize_repo_execution(
                ctx, transfer_repo_path, transfer_workspace_id
            )
    except _RepoExecutionRefused as exc:
        return f"REFUSED: {exc}"
    if not os.path.isdir(repo_path):
        return f"REFUSED: repo_path {repo_path!r} is not a directory on this server."
    if transfer_repo_path is not None and not os.path.isdir(transfer_repo_path):
        return f"REFUSED: transfer_repo_path {transfer_repo_path!r} is not a directory on this server."

    from app.services.procedures import (
        get_procedure, mark_procedure_stale, record_execution_outcome, ProcedureNotFound,
    )

    procedure_payload = await get_procedure(pool, procedure_id)
    if procedure_payload is None:
        raise ProcedureNotFound(procedure_id)

    steps = procedure_payload.get("steps") or []
    if not steps:
        return (
            f"REFUSED: procedure {procedure_id} has no steps to reproduce "
            "(empty steps list -- nothing to re-run)."
        )
    task_description = procedure_payload.get("goal") or procedure_payload.get("name") or procedure_id

    # Phase 4 (memory-substrate map, this pass): before spending an agent
    # run, check whether the procedure's OWN bound numeric invariants
    # (e.g. "pandas_version >= 2.0") are still satisfied by THIS repo's
    # real, freshly-probed environment -- the same invariant-checking
    # primitive applicability.py's retrieval-time cascade already uses,
    # applied here as a staleness *detector* rather than a retrieval
    # disqualifier. A genuine contradiction means re-running the
    # procedure's steps would not be testing reproduction at all (the
    # environment has moved past what the procedure claims to need) --
    # so this marks the procedure stale and returns early instead of
    # wasting a sandboxed run on a foregone conclusion. `staleness`
    # (db/18_procedures.sql) is a real, enforced hard constraint in
    # applicability.py's cascade already; until this pass nothing in
    # production ever SET it away from 'fresh' -- this is that producer.
    from app.services.environment_probe import invariant_bindings_from_facts, probe_environment
    from app.services.invariants import check_invariants_async

    invariants = procedure_payload.get("invariants") or []

    async def _staleness_report(target_repo_path: str) -> Optional[str]:
        """Same Phase 4 staleness check, factored so both the same-
        instance repo and (when supplied) the transfer-tier repo run it
        against THEIR OWN real, freshly-probed environment -- a
        contradiction discovered on the transfer target is a real,
        meaningful "transfer failed because the environment doesn't
        qualify" signal, not something to silently skip. Returns a
        formatted STALE report (and marks the procedure stale as a side
        effect) on a genuine contradiction; None when invariants are
        absent, satisfied, or undecidable."""
        if not invariants:
            return None
        facts = probe_environment(target_repo_path)
        bindings = invariant_bindings_from_facts(facts)
        invariant_result = await check_invariants_async(invariants, bindings)
        if not invariant_result.violated:
            return None
        updated = await mark_procedure_stale(
            pool, procedure_row_id=procedure_id,
            reason=f"invariant(s) {invariant_result.violated} contradicted by "
                   f"probed bindings {bindings} at {target_repo_path}",
            detected_by=_resolve_caller_identity(
                fallback=f"reproduce_procedure:{os.path.basename(os.path.abspath(target_repo_path))}"
            ),
        )
        return (
            f"STALE: procedure {procedure_id}'s invariant(s) "
            f"{invariant_result.violated} are contradicted by this repo's real, "
            f"probed environment ({bindings}) -- marking staleness="
            f"'{updated['staleness']}' instead of reproducing against a known-"
            "incompatible environment. No agent run was attempted."
        )

    same_repo_stale = await _staleness_report(repo_path)
    if same_repo_stale is not None:
        # Byte-for-byte unchanged early return from before transfer_repo_path
        # existed: a stale primary repo means no run of ANY tier was
        # attempted, transfer included.
        return same_repo_stale

    from app.execution.graph_executor import NodeResult
    from app.execution.plan_persistence import persist_compiled_plan
    from app.execution.plans import compile_plan
    from app.execution.procedure_graph import ProcedureCompositionError, expand_procedure_steps

    client = OpenAI(
        max_retries=0,
        api_key=settings.require("general_compute_api_key"),
        base_url=settings.general_compute_base_url,
    )

    # Phase 10: expand once, reused by both the same-repo and (when
    # requested) transfer-tier runs below -- both replay the SAME
    # procedure's same steps, just against different target repos.
    try:
        expanded_nodes = await expand_procedure_steps(
            pool, procedure_id=procedure_payload["procedure_id"],
            procedure_version=procedure_payload["version"], steps=steps,
        )
    except ProcedureCompositionError as exc:
        return (
            f"REFUSED: procedure {procedure_payload['procedure_id']} has an "
            f"unresolvable composed step -- {exc}"
        )

    async def _run_tier(target_repo_path: str, context_key: str) -> dict:
        """One full replay/transfer run of the procedure's OWN stored
        steps against `target_repo_path`, recorded as its own
        `reproduction` evidence row under `context_key`. Identical
        machinery for both tiers -- only the target repo and the
        context_key convention differ."""
        sandbox = RepoSandbox(target_repo_path)
        compiled_plan = compile_plan(
            procedure_id=procedure_payload["procedure_id"],
            procedure_version=procedure_payload["version"],
            procedure_row_id=UUID(procedure_id),
            procedure_payload=procedure_payload,
            task_description=task_description,
            nodes=expanded_nodes,
            extractor_version="reproduce_procedure_plan_compiler@1",
            created_by=_resolve_caller_identity(fallback="reproduce_procedure"),
        )
        compiled_plan = await _bind_plan_to_registry(pool, compiled_plan, procedure_payload)
        compiled_plan, _ = await persist_compiled_plan(pool, compiled_plan)

        node_runs: dict[int, "AgentRun"] = {}  # noqa: F821
        node_notes: list[str] = []

        async def run_node(node):
            prior_context = ("\n\nPrior steps completed:\n" + "\n".join(node_notes)) if node_notes else ""
            node_instance = {
                "instance_id": f"mcp_reproduce_procedure_{secrets.token_hex(6)}_step{node.order}",
                "repo": os.path.basename(os.path.abspath(target_repo_path)),
                "problem_statement": f"{task_description}\n\nCurrent step: {node.goal}",
            }
            node_agent = Agent(client, model, max_steps=max_steps)
            node_run = await asyncio.to_thread(
                node_agent.run, node_instance, sandbox, "mcp_reproduce_procedure", prior_context,
            )
            node_runs[node.order] = node_run
            succeeded = node_run.stop_reason == "finished"
            note = f"step {node.order} ({node.goal}): stop_reason={node_run.stop_reason}, tool_calls={len(node_run.tool_calls)}"
            node_notes.append(note)
            return NodeResult(status="success" if succeeded else "failure", notes=note)

        # DURABLE execution (final-V1 §3): a stateful sandboxed agent
        # replay -- same durable substrate as find_best_way tier-2.
        # durable_run appends the immutable executions row.
        from app.execution.durable_graph import run_graph_durably

        from app.services.verification import compute_verification_plan_id

        graph_result = await run_graph_durably(
            pool, compiled_plan, run_node,
            procedure_id=str(compiled_plan.plan.procedure.procedure_id),
            procedure_version=int(compiled_plan.plan.procedure.version),
            created_by=_resolve_caller_identity(fallback="reproduce_procedure"),
            scope_type=compiled_plan.plan.scope_type,
            scope_entity_id=compiled_plan.plan.scope_entity_id,
            verification_plan_id=compute_verification_plan_id(procedure_payload),
            side_effecting_orders=set(),  # V1: sandbox rebuilt per run -> step is replayable on resume
        )

        all_files_edited = sorted({f for r in node_runs.values() for f in r.files_edited})
        combined_patch = "\n".join(r.patch for r in node_runs.values() if r.patch)
        total_calls = sum(r.usage.calls for r in node_runs.values())

        # Same real success proxy as find_best_way's tier 2: the whole
        # graph finished AND produced a non-empty diff -- "finished"
        # alone can mean "gave up cleanly", not "reproduced".
        run_succeeded = graph_result.outcome == "success" and bool(combined_patch)

        updated = await record_execution_outcome(
            pool, procedure_row_id=procedure_id, success=run_succeeded,
            context_key=context_key,
            steps_used=total_calls,
            evidence_type="reproduction",
        )

        return {
            "run_succeeded": run_succeeded,
            "graph_outcome": graph_result.outcome,
            "total_nodes": len(compiled_plan.graph.nodes),
            "executed_nodes": len(node_runs),
            "node_notes": node_notes,
            "files_edited": all_files_edited,
            "updated": updated,
            "combined_patch": combined_patch,
        }

    same_repo_basename = os.path.basename(os.path.abspath(repo_path))
    same_result = await _run_tier(repo_path, f"reproduction:{same_repo_basename}")

    lines = [
        f"reproduction_outcome: {'REPRODUCED' if same_result['run_succeeded'] else 'FAILED_TO_REPRODUCE'}",
        f"graph_outcome: {same_result['graph_outcome']}",
        f"steps: {same_result['total_nodes']} total, {same_result['executed_nodes']} executed",
        *[f"  {note}" for note in same_result["node_notes"]],
        f"files_edited: {same_result['files_edited']}",
        f"verification_state_after: {same_result['updated']['verification_state']} "
        f"(availability: {same_result['updated']['availability']})",
    ]
    lines.append(
        "\n--- COMBINED DIFF ---\n" + same_result["combined_patch"]
        if same_result["combined_patch"] else "\n(no changes made)"
    )

    # Transfer C tier (spec: "different repository/instance, same task
    # family") -- only attempted when the caller asked for it. Recorded
    # as a wholly separate reproduction evidence row (own context_key,
    # own transaction via record_execution_outcome) so a same-repo
    # success and a transfer failure (or vice versa) are never conflated
    # into one number.
    if transfer_repo_path is not None:
        transfer_repo_basename = os.path.basename(os.path.abspath(transfer_repo_path))
        lines.append(f"\n--- TRANSFER TIER (Transfer C: {same_repo_basename} -> {transfer_repo_basename}) ---")

        transfer_stale = await _staleness_report(transfer_repo_path)
        if transfer_stale is not None:
            lines.append(transfer_stale.replace(
                "STALE:", "TRANSFER STALE:",
            ))
        else:
            transfer_context_key = f"reproduction:transfer:{same_repo_basename}->{transfer_repo_basename}"
            transfer_result = await _run_tier(transfer_repo_path, transfer_context_key)
            lines.extend([
                f"transfer_outcome: {'REPRODUCED' if transfer_result['run_succeeded'] else 'FAILED_TO_REPRODUCE'}",
                f"transfer_graph_outcome: {transfer_result['graph_outcome']}",
                f"transfer_steps: {transfer_result['total_nodes']} total, {transfer_result['executed_nodes']} executed",
                *[f"  {note}" for note in transfer_result["node_notes"]],
                f"transfer_files_edited: {transfer_result['files_edited']}",
                f"transfer_verification_state_after: {transfer_result['updated']['verification_state']} "
                f"(availability: {transfer_result['updated']['availability']})",
                f"transfer_context_key: {transfer_context_key}",
            ])
            lines.append(
                "\n--- TRANSFER DIFF ---\n" + transfer_result["combined_patch"]
                if transfer_result["combined_patch"] else "\n(no changes made on transfer repo)"
            )

    return "\n".join(lines)


@server.tool()
async def detect_conflict_trigger(new_node_id: str, ctx: Context) -> str:
    """
    Check whether an existing knowledge_node conflicts with something else
    already in the graph, and if so, open a real trigger ready for
    propose_synthesis.

    Thin wrapper around detect_and_create_conflict_trigger -- the exact
    real, already-tested function that closes the gap this project's own
    handoff docs flagged: "No MCP tool creates a conflict trigger; clients
    can only run debates on already-queued triggers." No new detection
    logic lives here; this only formats the real function's output.

    Under the hood (already real, already tested, not reimplemented here):
    finds the single best-matching existing knowledge_node in the
    PARTIAL_MATCH_THRESHOLD..FULL_MATCH_THRESHOLD band (0.70-0.90 --
    "related enough to matter, not identical enough to be a simple
    duplicate"; >=0.90 is dedup's job, not debate's), creates a proxy
    task_node ("Reconcile: X vs Y") linked to both conflicting nodes via
    CONFLICTS_WITH edges, computes any real date-overlap fact in actual
    Python date math (not left for the panel to get wrong in prose), and
    opens a trigger row.

    new_node_id: id of an EXISTING knowledge_node -- typically one you
    just created or updated (e.g. via apply_change_set or decompose_task)
    and want checked against the rest of the graph.

    Returns the new trigger_id (hand it straight to propose_synthesis), or
    a plain "no conflict found" message -- which is a normal, common,
    non-error outcome, not a failure.

    HONEST SCOPE: this only checks the SINGLE best match, not every match
    above threshold (deliberately, per the underlying function's own
    docstring -- multiple simultaneous conflicts need a design decision,
    one debate for all of them or one each, that isn't made here). This
    also only covers knowledge-conflict-triggered debates -- it does NOT
    create the OTHER real trigger kind (metric-threshold triggers off task
    execution stats like error_rate/cost/cycle_time), which is a separate,
    internal-monitoring-driven mechanism (TriggerDetector), not something
    an external MCP client would naturally initiate.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    try:
        node_uuid = UUID(new_node_id)
    except ValueError:
        return f"REFUSED: {new_node_id!r} is not a valid UUID."

    row = await pool.fetchrow(
        "SELECT id FROM knowledge_nodes WHERE id = $1 AND t_invalid IS NULL", node_uuid
    )
    if row is None:
        return f"REFUSED: no live knowledge_node {new_node_id} (not found, or already superseded)."

    trigger_id = await detect_and_create_conflict_trigger(pool, new_node_id)
    if trigger_id is None:
        return (
            f"No conflict found for {new_node_id} in the "
            f"PARTIAL_MATCH_THRESHOLD..FULL_MATCH_THRESHOLD band (0.70-0.90) -- "
            f"a normal, common outcome. No trigger created."
        )
    return (
        f"Conflict detected -- trigger created: {trigger_id}\n"
        f"Hand this trigger_id to propose_synthesis to open the debate."
    )


@server.tool()
async def check_procedure(procedure_id: str, query: str, ctx: Context) -> str:
    """
    demo.md C5: ALLOW or WOULD_REFUSE reuse of a NAMED procedure right now,
    citing the real hard-constraint / capability evidence behind the
    verdict. AUDIT MODE ONLY (demo.md §2 item 3 / §4) -- this INFORMS the
    calling agent, it never blocks a call; nothing here stops you from
    proceeding, the verdict is yours to act on.

    Thin wrapper, same discipline as retrieve_precedent/apply_change_set:
    ALL decision logic is app.services.applicability.check_procedure_reuse(),
    which reuses (does not reinvent) the SAME non-compensatory
    check_hard_constraints() cascade find_applicable_procedures() runs, plus
    procedure_extraction/failure_handlers.capability_for_stream() -- the
    SAME evidence-stream recompute the capability_demotion failure handler
    already uses. See that function's own docstring for exactly what is and
    isn't reused (incl. why precondition_gate.py's postcondition check does
    NOT apply here).

    procedure_id: the STABLE procedure handle (`procedures.procedure_id`,
    constant across a version chain) -- NOT a per-version row id. Resolved
    here to its current live version (t_invalid IS NULL).
    query: plain-language description of what you're about to do with this
    procedure. Accepted for audit-log / future scope-narrowing use; HONEST
    LIMIT stated plainly -- it does not (yet) feed the decision itself, since
    scope/exclusion matching needs STRUCTURED scope, not free text, and
    nothing here extracts structure from a natural-language query (same
    honest limit check_procedure_reuse's own docstring states for
    precondition_gate.py).

    Returns the pinned demo.md §3 JSON contract exactly:
    {"verdict": "ALLOW"|"WOULD_REFUSE", "procedure": ..., "reason": ...,
     "evidence": [...], "capability_note": ...}
    On a bad/unknown procedure_id: "REFUSED: ..." (a caller error, not a
    verdict about a real procedure).
    """
    pool = ctx.request_context.lifespan_context["pool"]

    from app.services.applicability import ProcedureNotFound, check_procedure_reuse

    try:
        result = await check_procedure_reuse(
            pool, procedure_id=procedure_id, access_scope=AccessScope.unrestricted(),
        )
    except ProcedureNotFound as exc:
        # finding C: the caller may have passed the procedures.id row key
        # instead of the stable handle -- resolve it and retry once.
        canonical = await _canonical_procedure_id(pool, procedure_id)
        if canonical is None or canonical == procedure_id:
            return f"REFUSED: {exc}"
        try:
            result = await check_procedure_reuse(
                pool, procedure_id=canonical, access_scope=AccessScope.unrestricted(),
            )
        except ProcedureNotFound as exc2:
            return f"REFUSED: {exc2}"

    return json.dumps({
        "verdict": result.verdict,
        "procedure": result.procedure,
        "reason": result.reason,
        "evidence": result.evidence,
        "capability_note": result.capability_note,
    })


# ---------------------------------------------------------------------------
# Minimal library-primitive surface (architecture audit, section J:
# .scratch/research/global-procedural-memory-architecture-audit-2026-08-30.md).
# Each tool below is a thin wrapper -- zero new decision logic -- around a
# function that already exists and is already exercised by find_best_way's
# own two tiers. The point of exposing them standalone is that a caller who
# already has a procedure_id (from a prior search, or from another harness
# entirely) can search/check/report/submit without find_best_way's own
# bundled task-description-driven flow.
# ---------------------------------------------------------------------------


async def _canonical_procedure_id(pool, given: str) -> "str | None":
    """Accept EITHER the stable ``procedures.procedure_id`` family handle OR
    a per-version ``procedures.id`` row key, and return the stable handle
    for the live version. Final-V1 eval finding C: the MCP procedure tools
    take the family handle, but the row key is what a caller sees in the
    DB / the /procedure-graph viewer / another tool's output, and passing
    it was bounced with an unhelpful "no live procedure". Returns None when
    neither resolves to a live row (``t_invalid IS NULL``)."""
    try:
        u = UUID(str(given))
    except (ValueError, AttributeError, TypeError):
        return None
    row = await pool.fetchrow(
        "SELECT procedure_id::text AS pid FROM procedures "
        "WHERE (procedure_id = $1::uuid OR id = $1::uuid) AND t_invalid IS NULL "
        "ORDER BY (procedure_id = $1::uuid) DESC LIMIT 1",
        u,
    )
    return row["pid"] if row else None


async def _resolve_live_procedure(pool, procedure_id: str) -> dict:
    """Shared resolver: a procedure handle -> its current live version row.
    Accepts the stable ``procedure_id`` OR the ``procedures.id`` row key
    (finding C) via ``_canonical_procedure_id``; the row read itself is the
    exact query applicability.py::check_procedure_reuse() uses, so both
    paths agree by construction on "the current live version"."""
    from app.services.applicability import ProcedureNotFound

    try:
        UUID(str(procedure_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ProcedureNotFound(f"{procedure_id!r} is not a valid procedure id (UUID)") from exc
    canonical = await _canonical_procedure_id(pool, procedure_id)
    if canonical is None:
        raise ProcedureNotFound(
            f"no live procedure for {procedure_id} -- tried it as both the "
            "procedure_id family handle and the procedures.id row key"
        )
    row = await pool.fetchrow(
        "SELECT * FROM procedures WHERE procedure_id = $1::uuid AND t_invalid IS NULL",
        UUID(canonical),
    )
    if row is None:
        raise ProcedureNotFound(f"no live procedure for procedure_id={procedure_id}")
    return dict(row)


@server.tool()
async def search_procedures(task: str, ctx: Context, state: str = "{}", limit: int = 5,
                             require_verified: bool = False,
                             invariant_bindings: str = "{}") -> str:
    """
    Find procedures applicable to a task/state -- lookup only, nothing
    executes. Thin wrapper: all decision logic is
    app.services.applicability.find_applicable_procedures(), the SAME
    non-compensatory cascade find_best_way's own tier-1 calls internally.

    task: plain-language description of what's being attempted, same
    phrasing style as retrieve_precedent's query.
    state: JSON object of current-scope predicates (e.g.
    '{"language": ["python"]}') -- structured, not free text. "{}" (the
    default) means no scope narrowing.
    require_verified: whether to restrict the browse to verified and
    approved procedures. This lookup defaults to False so candidate
    procedures can be inspected and retrieved during corpus cold start;
    automatic selection callers continue to pass True explicitly.
    invariant_bindings: JSON object of real numeric quantities the CALLER
    already knows (e.g. '{"pandas_version": 2.1}') -- fed straight into
    check_hard_constraints' numeric-invariant stage (invariants.py). This
    is a remote tool with no filesystem of its own to probe; a caller
    that has a real local repo (see app.local_agent.runner, which probes
    it via environment_probe.probe_environment +
    invariant_bindings_from_facts) computes these locally and sends the
    result here, exactly as find_best_way's own repo_path path does
    server-side when it has a repo to probe directly.

    Returns a JSON array of {id, procedure_id, version, name, goal,
    verification_state, similarity}. `similarity` is null for a
    hard-filter survivor that couldn't be ranked (no goal embedding
    supplied, or the row has none) -- never fabricated.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    from app.services.applicability import find_applicable_procedures
    from app.services.embeddings import Embedder

    try:
        current_scope = json.loads(state) if state else {}
    except json.JSONDecodeError as exc:
        return f"REFUSED: state must be a JSON object ({exc})"
    try:
        bindings = json.loads(invariant_bindings) if invariant_bindings else {}
    except json.JSONDecodeError as exc:
        return f"REFUSED: invariant_bindings must be a JSON object ({exc})"

    embedder = Embedder()
    goal_vec = await embedder.embed_one(task, input_type="query")
    matches = await find_applicable_procedures(
        pool, goal_embedding=goal_vec, current_scope=current_scope,
        require_verified=require_verified, limit=limit,
        invariant_bindings=bindings,
        embedding_model_id=embedder.embedding_model_id(),
    )
    return json.dumps([
        {
            "id": str(m["id"]), "procedure_id": str(m["procedure_id"]),
            "version": m["version"], "name": m["name"], "goal": m["goal"],
            "verification_state": m["verification_state"],
            "similarity": m.get("_similarity_score"),
        }
        for m in matches
    ])


@server.tool()
async def get_claim_graph(ctx: Context, limit: int = 200, include_retired: bool = False,
                          q: str | None = None, with_status: bool = True,
                          link_mode: str = "both", sim_k: int = 3,
                          sim_threshold: float = 0.55) -> str:
    """
    The current claim graph as nodes + edges -- the same data the
    /claim-graph web page in this server renders. Thin wrapper around
    app.services.claim_graph_api.get_claim_graph_overview; read-only, no
    new query logic here.

    limit: max claim nodes (clamped 1..600). One extra row is checked
    internally so `truncated` is honest, never a silent cap.
    include_retired: default False -> only claims still believed
    (truth_state='IN'). True also returns superseded/contradicted claims
    (status 'retired'/'contradicted').
    q: optional case-insensitive substring filter on the claim statement.
    with_status: default True -> each node carries its real lifecycle
    state (current/supported/stale/disputed/contradicted/retired), one
    bounded read per node. False = faster raw dump, truth_state only.
    link_mode: "both" (default) / "relations" / "similarity". Real
    claim<->claim relation edges are usually sparse; "similarity" adds
    undirected k-NN edges in claim-embedding space so related claims are
    visibly connected.
    sim_k: nearest neighbours per node for similarity edges (1..8).
    sim_threshold: minimum cosine similarity for a similarity edge (>=0.3).

    Returns JSON: {nodes:[{id, statement, truth_state, status, subject,
    predicate, object, epistemic_status, scope_type, scope_entity_id,
    created_by, t_valid, degree}], edges:[{id, source, target, kind
    ('relation'|'similarity'), relation?, weight?}], counts:{claims_total,
    claims_shown, edges, edges_by_kind, by_status}, truncated, link_mode,
    generated_at}.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    result = await claim_graph_api.get_claim_graph_overview(
        pool, scope=AccessScope.unrestricted(),
        limit=limit, include_retired=include_retired, q=q, with_status=with_status,
        link_mode=link_mode, sim_k=sim_k, sim_threshold=sim_threshold,
    )
    return json.dumps(result, default=str)


@server.tool()
async def get_relevant_claims(goal: str, ctx: Context, context: Optional[str] = None, top_k: int = 10) -> str:
    """
    MCP hardening B30: bounded, compact Claim references relevant to a
    goal/subproblem -- NEVER the whole Claim graph (for that, see
    `get_claim_graph`). Reuses the same hybrid vector+lexical retrieval
    `retrieve_precedent`/`decompose_task` already use
    (`HybridRetriever`), restricted to real Claims (`knowledge_nodes`
    where `node_type='claim'` -- a claim_family hub or other non-Claim
    knowledge_node is never presented as one).

    `context`: optional free text (environment facts, constraints) --
    concatenated into the same retrieval query, not a second query path.
    `top_k`: capped at 25 regardless of what is requested -- the working
    set MUST be bounded.

    Returns a JSON array of compact refs: {claim_id, version, scope,
    status, belief, statement, reason_for_relevance, applicability,
    evidence_summary}. `evidence_summary` is a pointer, not the full
    evidence -- fetch that lazily via `stealth://claims/{claim_id}` or
    `get_claim_graph` when actually needed.
    """
    from app.services.relevant_claims import get_relevant_claims as _get_relevant_claims

    pool = ctx.request_context.lifespan_context["pool"]
    refs = await _get_relevant_claims(
        pool, goal=goal, context=context, top_k=top_k, access_scope=_caller_access_scope(),
    )
    return json.dumps(refs, default=str)


@server.tool()
async def get_procedure(procedure_id: str, ctx: Context) -> str:
    """
    Fetch one procedure's full current detail by its stable handle.
    Thin wrapper around the same live-version resolver
    check_procedure/check_applicability/report_execution all share.

    procedure_id: the STABLE handle (`procedures.procedure_id`), not a
    per-version row id -- resolved here to its current live version
    (t_invalid IS NULL).
    """
    pool = ctx.request_context.lifespan_context["pool"]
    from app.services.applicability import ProcedureNotFound

    try:
        procedure = await _resolve_live_procedure(pool, procedure_id)
    except ProcedureNotFound as exc:
        return f"REFUSED: {exc}"

    return json.dumps(procedure, default=str)


@server.tool()
async def check_applicability(procedure_id: str, ctx: Context, state: str = "{}",
                               require_verified: bool = True) -> str:
    """
    Is this NAMED procedure applicable right now, given this state?
    Thin wrapper around app.services.applicability.check_hard_constraints()
    -- the SAME non-compensatory cascade find_applicable_procedures()/
    find_best_way run internally, exposed standalone for a caller that
    already has a procedure_id (e.g. from search_procedures) and just
    wants a yes/no plus the reason, without re-running a full search.

    state: JSON object of current-scope predicates, same shape as
    search_procedures' `state` argument.
    require_verified: real ticket-13 gate, default True -- a `candidate`
    procedure (not yet earned verification evidence) correctly reports
    applicable=False with failed_constraints=["verification_state"]
    under the default, same as it would inside find_best_way's own
    automatic-selection path. Pass False to check hard constraints alone
    (preconditions/scope/exclusions/temporal validity), the same
    explicit opt-in find_best_way's `allow_unverified_procedures` is.

    Returns {"applicable": bool, "failed_constraints": [...],
    "similarity_score": float|null}. A violated hard constraint is a
    disqualification here, never a low score -- this module's own
    defining principle, unchanged by being exposed standalone.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    from app.services.applicability import ProcedureNotFound, check_hard_constraints

    try:
        procedure = await _resolve_live_procedure(pool, procedure_id)
    except ProcedureNotFound as exc:
        return f"REFUSED: {exc}"

    try:
        current_scope = json.loads(state) if state else {}
    except json.JSONDecodeError as exc:
        return f"REFUSED: state must be a JSON object ({exc})"

    result = await check_hard_constraints(
        pool, procedure, current_scope=current_scope, require_verified=require_verified,
    )
    return json.dumps({
        "applicable": result.applicable,
        "failed_constraints": result.failed_constraints,
        "similarity_score": result.similarity_score,
    })


@server.tool()
async def report_execution(procedure_id: str, success: bool, context_key: str, ctx: Context,
                            steps_used: int | None = None,
                            success_criteria: dict[str, Any] | None = None,
                            failure_class: str | None = None,
                            observations_json: str | None = None,
                            tool_sequence_json: str | None = None,
                            task_description: str | None = None,
                            session_id: str | None = None) -> str:
    """
    Report a real execution outcome for a NAMED procedure. Thin wrapper
    around app.services.procedures.record_execution_outcome() -- the
    real, single source of truth for every ticket-13 lifecycle
    transition AND the real evidence writer (one execution_result row
    per call, invariant-#13-gated: a success needs real, explicit
    success_criteria, never bare model-asserted success).

    context_key: caller's own notion of "distinct context" (different
    repo/environment/dependency set). Ticket 13's verified threshold
    needs >=3 DISTINCT keys across >=10 successes with 0 failures -- a
    caller that always passes the same string can never reach verified
    regardless of how many times it reports success.
    success_criteria: a structured object, NOT a JSON-encoded string --
    {"predicate": "non-empty string"} and/or {"metrics": {non-empty
    object}}. Only meaningful when success=true. A malformed shape
    (blank predicate, empty/absent metrics, both absent while the field
    IS provided) is REFUSED by record_execution_outcome()'s own
    evidence-layer check (invariant #13, app/execution/evidence.py's
    _check_success_criteria). Omitting the field entirely on a real
    success is different from providing an empty one: omission lets
    record_execution_outcome() synthesize criteria from what the call
    itself measured (steps_used/match_cost/realised_savings) -- that
    synthesis, not this parameter, is what keeps bare model-asserted
    success out of the evidence table. Passing explicit criteria only
    lets the caller say something more specific than "the run completed".
    failure_class: one of evidence.py's real failure_class values, when
    success=false and the caller knows the cause. Omitted is honest
    (lands in the requires_review queue) rather than guessed.

    MCP hardening B18 fix: this used to be the ONLY thing this tool did,
    which meant a HOST-EXECUTED run (the `plan_only`/`continue_run`
    lease pattern -- the host runs outside Stealth's own sandbox) could
    never feed the learning loop the way `find_best_way`'s own tier-2
    sandboxed runs already do (they call `extract_procedure()` directly
    on success). `observations_json`/`tool_sequence_json` are the OPTIONAL
    evidence a host can now attach: when `success=True` and
    `observations_json` is given, this ALSO calls the SAME
    `extract_procedure()` pipeline tier-2 uses (same
    `AgentRunEvidenceSource`, same private-by-default visibility/owner_id
    this session's B19 fix already established, same V5 novelty/
    validation gate -- "if existing Procedure succeeded, do not
    duplicate it" is `extract_procedure()`'s OWN job, not re-implemented
    here). `observations_json`: JSON array of
    `{"observation_type","label","properties"}` objects (the same shape
    `AgentRunEvidenceSource` already documents). `tool_sequence_json`:
    JSON array of tool-call name strings. `task_description`: the
    original goal text (falls back to `context_key` if omitted -- less
    accurate, but never blocks extraction over a missing label).
    Omitting both `observations_json`/`tool_sequence_json` (the default)
    is byte-identical to this tool's pre-existing behavior -- outcome
    recording only, no extraction attempt.

    Returns the procedure row's state AFTER any transition this call
    caused (promotion to verified, quarantine opening/closing), plus
    `extraction` (present only when an extraction attempt was made):
    `{"procedure_id": ...}` on a new candidate, or
    `{"skipped": "<validation failure reason>"}` when V5 refused it.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    from app.services.applicability import ProcedureNotFound
    from app.services.procedures import record_execution_outcome

    try:
        procedure = await _resolve_live_procedure(pool, procedure_id)
    except ProcedureNotFound as exc:
        return f"REFUSED: {exc}"

    # Real MCP clients validate this against the tool's advertised
    # object schema before the call ever reaches here; this guard is
    # for direct/offline callers (tests, local_agent) that skip that
    # layer -- either way, a non-object value is refused outright,
    # never string-parsed (that was the whole bug: a JSON-encoded
    # string could never be both valid str AND a real MCP client's
    # natural structured-object call).
    if success_criteria is not None and not isinstance(success_criteria, dict):
        return (
            "REFUSED: success_criteria must be a JSON object, e.g. "
            '{"predicate": "..."} -- not a JSON-encoded string or other type'
        )

    try:
        updated = await record_execution_outcome(
            pool, procedure_row_id=str(procedure["id"]), success=success,
            context_key=context_key, steps_used=steps_used,
            success_criteria=success_criteria, failure_class=failure_class,
        )
    except Exception as exc:  # noqa: BLE001 -- a producer-side contract
        # violation (e.g. invariant #13's bare-success refusal, or an
        # unknown failure_class) must reach the caller as a real
        # refusal, not an unhandled 500.
        return f"REFUSED: {exc}"

    response = {
        "procedure_id": procedure_id,
        "verification_state": updated["verification_state"],
        "availability": updated["availability"],
        "verification_stats": updated["verification_stats"],
    }

    # B18 fix: host-executed learning loop. Gated on success AND real
    # evidence being supplied -- never attempted from bare success=True
    # alone (that would be exactly the self-report-as-evidence anti-
    # pattern this codebase's evidence layer exists to refuse).
    if success and observations_json is not None:
        try:
            observations = json.loads(observations_json)
            tool_sequence = json.loads(tool_sequence_json) if tool_sequence_json else []
        except json.JSONDecodeError as exc:
            response["extraction"] = {"skipped": f"malformed JSON -- {exc}"}
        else:
            from app.services.procedure_extraction import extract_procedure
            from app.services.procedure_extraction.evidence import AgentRunEvidenceSource

            owner = _resolve_caller_identity(fallback="report_execution_extract")
            evidence_source = AgentRunEvidenceSource(
                goal_text=task_description or context_key, outcome="success",
                observations=observations, tool_sequence=tool_sequence,
                session_id=session_id, steps_used=steps_used,
            )
            extraction = await extract_procedure(
                pool, evidence_source,
                # B19: same private-by-default posture as find_best_way's
                # own tier-2 extraction call -- learning from a host-
                # executed run never implicitly goes public.
                visibility="private", owner_id=owner,
            )
            response["extraction"] = (
                {"procedure_id": str(extraction.procedure_id)} if extraction.procedure_id
                else {"skipped": "; ".join(extraction.validation_failures) or "no candidate extracted"}
            )

    return json.dumps(response, default=str)


@server.tool()
async def submit_procedure(name: str, goal: str, steps_json: str, ctx: Context,
                            domain: str | None = None,
                            provenance: str = "system_pending_review") -> str:
    """
    Submit a new candidate procedure. Thin wrapper around
    app.services.procedures.capture_procedure() -- lands
    verification_state='candidate' (schema default, ticket 13's "nothing
    is born verified"), never fabricated as verified. It earns 'verified'
    exactly the way every other procedure in this substrate does: real
    reuse via report_execution, accruing real evidence.

    steps_json: JSON array of {"order": int, "goal": str} -- planner-
    neutral step descriptions (db/18_procedures.sql's own convention; no
    dependency/branching fields exist on steps today, so a submitted
    procedure is a straight, ordered sequence).
    domain: optional free-form locality signal (e.g. "coding"); also
    becomes the scope entity when set (scope_type="entity"), or the
    procedure is scoped "global" when omitted.
    provenance: real, gated enum (v0_gate.py) -- defaults to
    'system_pending_review', this codebase's existing convention for
    system/user-submitted, not-yet-approved content (extract_procedure()
    uses the same default for the same reason).

    REAL BUG THIS SESSION'S OWN LIVE TEST FOUND: a procedure captured
    with no embedding is not just "unranked" in search_procedures --
    find_applicable_procedures() fills its `limit` quota from ranked
    (embedded) survivors FIRST and only appends unranked ones into
    whatever slots are left, so an embedding-less procedure can be
    completely starved out by irrelevant-but-embedded rows the moment
    the corpus has >= `limit` of those. A real embedding is therefore
    not an optimization here, it's required for the procedure to be
    reachable at all once the corpus has any real size -- computed here,
    same Embedder + input_type="document" convention method_library.py's
    persist_plan() already uses for stored (not query-time) text.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    from app.services.embeddings import Embedder
    from app.services.procedures import capture_procedure
    from app.services.retrieval_document import (
        RETRIEVAL_DOCUMENT_VERSION,
        build_procedure_retrieval_document,
        retrieval_document_sha256,
    )
    from app.services.v0_gate import V0Violation

    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        return f"REFUSED: steps_json must be a JSON array ({exc})"

    # ONE authoritative procedure representation: embed the canonical
    # retrieval document, never a bare `goal`. A goal-only vector would be
    # a competing, impoverished representation for the same corpus.
    embedder = Embedder()
    retrieval_doc = build_procedure_retrieval_document(
        {"name": name, "goal": goal, "steps": steps, "domain": domain}
    )
    doc_vec, meta = await embedder.embed_one_with_metadata(
        retrieval_doc, input_type="document"
    )

    try:
        result = await capture_procedure(
            pool, name=name, goal=goal, steps=steps,
            provenance=provenance, domain=domain,
            scope_type="entity" if domain else "global",
            created_by=_resolve_caller_identity(fallback="mcp_submit_procedure"),
            embedding=doc_vec,
            embedding_model_id=meta.model_id,
            embedding_provider=meta.provider,
            embedding_input_type=meta.input_type,
            embedding_text_hash=meta.text_sha256,
            retrieval_document=retrieval_doc,
            retrieval_document_version=RETRIEVAL_DOCUMENT_VERSION,
            retrieval_document_sha256=retrieval_document_sha256(retrieval_doc),
        )
    except V0Violation as exc:
        return f"REFUSED: {exc}"

    return json.dumps({
        "id": result["id"], "procedure_id": result["procedure_id"],
        "verification_state": "candidate",
    })


@server.tool()
async def decide_procedure(procedure_id: str, approver_id: str, decision: str, ctx: Context) -> str:
    """
    THE 6TH PRIMITIVE this session's own production-level test of the
    5-tool surface found missing: a real human sign-off action.
    approval_status='approved' is a real, separate gate
    (applicability.py's own documented design -- "only AUTOMATIC
    selection requires both real evidence AND a human sign-off"), and
    until this tool, nothing exposed app.services.procedures.
    approve_procedure()/reject_procedure() over MCP at all -- a fully
    'verified' procedure (real ticket-13 evidence, 10+ successes, 3+
    distinct contexts) was STILL correctly refused by
    check_applicability's default gate with no way to clear it.

    Deliberately ORTHOGONAL to verification_state, matching
    approve_procedure()'s own docstring: approving a procedure does not
    fast-track statistical verification, and a verified procedure is not
    auto-approved. The two axes stay independent on purpose -- real
    evidence answers "does this work", a human sign-off answers "is this
    safe/appropriate to auto-select", and neither substitutes for the
    other.

    Same "approved"/"rejected" vocabulary and idempotency-adjacent shape
    as decide_decomposition/submit_approval, for consistency across this
    server's approval-shaped tools -- this one records a real ChangeSet
    (Band 1.9c, invariant #7: approval is a [V] status mutation and must
    be auditable), same as the underlying function already does.

    procedure_id: the STABLE handle, resolved to its current live version.
    approver_id: who is deciding -- stored as approved_by, and in the
    real ChangeSet's author field.
    decision: "approved" or "rejected".
    """
    pool = ctx.request_context.lifespan_context["pool"]
    from app.services.applicability import ProcedureNotFound
    from app.services.procedures import approve_procedure, reject_procedure

    if decision not in ("approved", "rejected"):
        return f"REFUSED: decision must be 'approved' or 'rejected', got {decision!r}."

    try:
        procedure = await _resolve_live_procedure(pool, procedure_id)
    except ProcedureNotFound as exc:
        return f"REFUSED: {exc}"

    # `approver_id` is a caller-supplied, self-asserted parameter -- the
    # exact spoofable shape authn.py's own contextvar override already
    # closes for ingest's payload actor_id (see authn.py's module
    # docstring). A REAL resolved identity, when one is available, wins
    # over that self-assertion the same way; the self-asserted value
    # survives only as the honest fallback when no real identity was
    # resolved (today: always, for the reasons _resolve_caller_identity
    # documents).
    resolved_approver = _resolve_caller_identity(fallback=approver_id)
    if decision == "approved":
        await approve_procedure(pool, procedure_row_id=str(procedure["id"]), approved_by=resolved_approver)
    else:
        await reject_procedure(pool, procedure_row_id=str(procedure["id"]), approved_by=resolved_approver)

    updated = await _resolve_live_procedure(pool, procedure_id)
    return json.dumps({
        "procedure_id": procedure_id,
        "approval_status": updated["approval_status"],
        "approved_by": updated["approved_by"],
        "verification_state": updated["verification_state"],
    }, default=str)


@server.tool()
async def decompose_task(problem: str, ctx: Context) -> str:
    """
    Turn an unstructured problem description into a real, structured graph
    proposal -- new task_nodes/knowledge_nodes/edges -- persisted to the
    real `decompositions` table, WITHOUT writing anything to the actual
    graph yet.

    REAL BUG FOUND AND FIXED after this tool's first version shipped: it
    used to tell callers to apply the result via apply_change_set. That
    was wrong, and a genuinely serious gap -- apply_change_set uses
    KnowledgeUpdater.apply(), which never calls validate_generative(),
    the capability-boundary check that's this project's own stated "only
    real guarantee" against a prompt-injected/hijacked model (V2_STATUS.md:
    generated content may only CREATE new nodes and connect them to each
    other -- never modify, invalidate, or attach to anything that already
    exists). Using apply_change_set on this tool's output would have
    bypassed that guarantee entirely. Use decide_decomposition instead --
    it calls the real, correct app.api.decompose.decide(), which re-runs
    validate_generative() at apply time specifically so a proposal
    tampered with in storage still can't escalate.

    This version calls the real app.api.decompose.decompose() endpoint
    function directly (not the bare DecompositionService -- that was the
    root cause of the bug above: it skipped the real endpoint's
    persistence step entirely, so there was never a real decomposition_id
    for a proper decide step to reference). No new decomposition logic
    lives here.

    HONEST GAP, stated plainly: the real endpoint's rate-limiting and
    cost-governance dependencies (enforce_limits, a real per-viewer
    scope_key) aren't replicated here -- this tool uses a fixed
    "mcp_decompose_task" scope_key, so real per-caller rate limits and
    spend caps do NOT apply to calls made through this MCP tool the way
    they would through the real HTTP endpoint. Fine for trusted/internal
    use (this project's current, explicit posture), a real gap to close
    before opening this specific tool to untrusted callers.

    problem: plain-language description of the workflow/problem to
    decompose -- ordinary phrasing, up to ~20,000 characters (the real
    endpoint's own limit).

    Returns: the real decomposition_id (hand this to decide_decomposition),
    feasibility, reasoning, structural problems (block safe_to_propose),
    objections (surfaced, not auto-blocking), suspected manipulation,
    related existing content, and the change_set for your own review.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    if not problem.strip():
        return "REFUSED: problem is empty."
    if len(problem) > 20_000:
        return f"REFUSED: problem is {len(problem)} chars, over the real 20,000-char limit."

    result = await decompose(
        DecomposeRequest(problem=problem),
        pool=pool,
        scope=AccessScope.unrestricted(),
        scope_key="mcp_decompose_task",
    )

    lines = [
        f"decomposition_id: {result.id}",
        f"feasible: {result.feasible}",
        f"safe_to_propose: {result.safe_to_propose}",
        f"node_count: {result.node_count}",
        f"is_novel: {result.is_novel}",
        f"suspected_manipulation: {result.suspected_manipulation}",
        f"reasoning: {result.reasoning}",
    ]
    if result.structural_problems:
        lines.append(f"structural_problems (BLOCKS safe_to_propose): {result.structural_problems}")
    if result.objections:
        lines.append(f"objections (surfaced, not blocking -- your call): {result.objections}")
    if result.related_existing:
        lines.append(f"related_existing: {result.related_existing}")
    if result.reused_nodes:
        lines.append(f"reused_nodes (matched against existing graph): {result.reused_nodes}")
    if result.suggested_agents:
        lines.append(f"suggested_agents: {result.suggested_agents}")

    lines.append(f"\nops (for your review): {json.dumps(result.ops)}")
    lines.append(
        f"\nOnce reviewed, call decide_decomposition({result.id!r}, approver_id, "
        f"\"approved\" or \"rejected\") -- NOT apply_change_set."
    )
    return "\n".join(lines)


@server.tool()
async def decide_decomposition(decomposition_id: str, approver_id: str, decision: str,
                                ctx: Context) -> str:
    """
    The REAL, capability-boundary-checked approve/reject step for a
    decompose_task proposal -- calls the exact real, already-tested
    app.api.decompose.decide() function directly (plain importable async
    function, not called over HTTP).

    THIS IS THE CORRECT PATH for decompose_task's output. On approval,
    this calls KnowledgeUpdater.apply_generated(), which re-runs
    validate_generative() at apply time -- the capability check ran once
    at generation, and running it again here means a proposal tampered
    with in storage between propose and decide still cannot escalate to
    modifying or invalidating existing graph content. Every node/edge
    written this way is tagged `public_generated`, so the graph never
    loses track of which content came from an untrusted submission versus
    a company's own documents. apply_change_set does NOT do any of this
    -- do not use it for decompose_task's output.

    decomposition_id: the real id from decompose_task's output.
    approver_id: who is deciding -- stored in the real decompositions row.
    SELF-ASSERTED, and only used as-is when no real identity is
    resolvable (see `_resolve_caller_identity`'s own docstring's
    invariant) -- a resolved real identity always overrides it, the same
    spoofing-proof discipline every other write-path attribution site in
    this file already follows. This is the one site the identity-
    hardening pass this session missed: it wrote `approver_id` straight
    into `DecideRequest` unconditionally, the exact "no resolution
    attempt" gap that invariant exists to close.
    decision: "approved" or "rejected".

    Real idempotency guard (from the underlying decide()): re-deciding an
    already-decided decomposition is refused, not silently re-applied --
    every apply inserts new nodes, so approving twice would create a
    duplicate subgraph.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    try:
        decomposition_uuid = UUID(decomposition_id)
    except ValueError:
        return f"REFUSED: {decomposition_id!r} is not a valid UUID."

    if decision not in ("approved", "rejected"):
        return f"REFUSED: decision must be 'approved' or 'rejected', got {decision!r}."

    resolved_approver = _resolve_caller_identity(fallback=approver_id)
    body = DecideRequest(approver_id=resolved_approver, decision=decision)
    try:
        result = await decide_decomposition_fn(decomposition_uuid, body, pool)
    except HTTPException as exc:
        return f"REFUSED ({exc.status_code}): {exc.detail}"

    lines = [
        f"decomposition_id: {result.id}",
        f"decision: {result.decision}",
        f"created_nodes: {result.created_nodes if result.created_nodes else '(none -- rejected, nothing applied)'}",
    ]
    if result.refs:
        lines.append(f"refs: {result.refs}")
    return "\n".join(lines)


@server.tool()
async def submit_approval(scorecard_id: str, approver_id: str, decision: str, ctx: Context,
                           note: str | None = None) -> str:
    """
    The REAL, gated approve/reject step for a debate-produced scorecard --
    calls the exact real, already-tested app.api.approval.decide() function
    directly (not reimplemented, not called over HTTP -- FastAPI route
    functions are plain importable async functions, so this just calls it).

    THIS IS THE FIX for a real gap found during this project's own MCP
    testing: apply_change_set is a raw, UNGATED write primitive -- it does
    not check debate state, does not require APPROVED, and does not write
    an audit row. Used directly on a propose_synthesis scorecard's
    change_set, apply_change_set completely bypasses human approval and
    the approvals audit trail this system was explicitly built to
    enforce (app/api/approval.py's own comment: "an approval recorded
    against a change that did not apply would be a false audit trail,
    which is worse than no audit trail"). submit_approval is the correct
    path for anything that came from propose_synthesis. apply_change_set
    remains the correct path for decompose_task's output, which never has
    a debate/scorecard to begin with.

    On approval, this does three things atomically (all real, all in the
    underlying decide(), not duplicated here): applies the change_set via
    the real KnowledgeUpdater, writes a row to the approvals table, and
    transitions the debate to APPROVED. On rejection: records the
    rejection and transitions to REJECTED -- nothing is applied.

    scorecard_id: id of a scorecard from propose_synthesis's real output.
    approver_id: who is deciding -- stored in the real audit row.
    SELF-ASSERTED, and only used as-is when no real identity is
    resolvable -- same spoofing-proof discipline as decide_procedure's
    own approver_id handling (see `_resolve_caller_identity`'s
    docstring). This was the other site the identity-hardening pass this
    session missed: `approver_id` went straight into `ApprovalRequest`
    unconditionally, letting a caller self-assert the identity written to
    the real `approvals` audit row this system was explicitly built to
    make trustworthy.
    decision: "approved" or "rejected".
    note: optional reason, stored in the real audit row and used as the
    real state-machine transition's reason if given.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    try:
        scorecard_uuid = UUID(scorecard_id)
    except ValueError:
        return f"REFUSED: {scorecard_id!r} is not a valid UUID."

    if decision not in ("approved", "rejected"):
        return f"REFUSED: decision must be 'approved' or 'rejected', got {decision!r}."

    resolved_approver = _resolve_caller_identity(fallback=approver_id)
    body = ApprovalRequest(approver_id=resolved_approver, decision=decision, note=note)
    try:
        result = await decide(scorecard_uuid, body, pool)
    except HTTPException as exc:
        return f"REFUSED ({exc.status_code}): {exc.detail}"

    lines = [
        f"approval_id: {result.approval_id}",
        f"decision: {result.decision}",
        f"applied_ops: {result.applied_ops if result.applied_ops else '(none -- rejected, nothing applied)'}",
    ]
    if result.export_markdown:
        lines.append(f"\n--- export ---\n{result.export_markdown}")
    return "\n".join(lines)


@server.tool()
async def resolve_implementation(task_node_id: str, ctx: Context,
                                  hint_kinds_json: str | None = None) -> str:
    """
    Directive Sec 44/76: "which concrete, durable implementation should
    satisfy this task node?" Thin wrapper around
    `app.execution.implementation_registry.resolve()` -- no new business
    logic, same read-only/public posture as `find_best_way`/
    `get_procedure` (scope=AccessScope.unrestricted(), matching every
    other read tool in this file).

    task_node_id: the task_nodes row id to resolve against.
    hint_kinds_json: optional JSON array of kind strings, an ordered
    preference (same shape `PlanNode.implementation_hint` uses). Omit
    for no preference -- the most recently registered active
    implementation wins.

    Returns the same honest shape the REST endpoint
    (POST /v1/tasks/{id}/resolve-implementation) returns: implementation_id
    is null with a real reason when nothing resolves -- never a
    fabricated pick, never a 404-shaped refusal for a genuine "nothing is
    linked" answer. Never echoes a credential value -- none is ever
    stored (see implementation_registry.py's own docstring).
    """
    pool = ctx.request_context.lifespan_context["pool"]

    hint_kinds = None
    if hint_kinds_json:
        try:
            parsed = json.loads(hint_kinds_json)
        except json.JSONDecodeError as exc:
            return f"REFUSED: hint_kinds_json must be a JSON array of strings ({exc})"
        if not isinstance(parsed, list):
            return "REFUSED: hint_kinds_json must be a JSON array of strings."
        hint_kinds = tuple(parsed)

    resolved = await implementation_registry.resolve(
        pool, task_node_id, scope=AccessScope.unrestricted(), hint_kinds=hint_kinds,
    )
    if resolved is None:
        reason = (
            "no active implementation is linked to this task"
            if hint_kinds is None else
            f"no active implementation matching hint kinds {list(hint_kinds)} is linked to this task"
        )
        return json.dumps({
            "implementation_id": None, "provider": None, "kind": None,
            "requirements": None, "invocation": None, "reason": reason,
        })

    reason = (
        "resolved to most recent active implementation, no hint given"
        if hint_kinds is None else "resolved via hint preference order"
    )
    return json.dumps({
        "descriptor": implementation_registry.descriptor(resolved),  # canonical execution ABI (§1/§27)
        "reason": reason,
    }, default=str)


@server.tool()
async def inspect_implementation(implementation_id: str, ctx: Context) -> str:
    """
    Directive Sec 76: fetch one durable implementation row by id. Thin
    wrapper around `implementation_registry.get()` -- public/read-only,
    same anti-enumeration posture as the REST endpoint (a missing or
    invisible row REFUSES the same way, never distinguishing the two).

    Also includes the B29 Implementation lifecycle position (REGISTERED
    -> RESOLVABLE -> AVAILABLE -> VERIFIED_IN_CONTEXT -> REUSED, plus
    UNAVAILABLE/RETIRED flags and any real recorded failure classes),
    derived from this row's own status/verification_status plus real
    evidence/binding facts -- see
    app/execution/implementation_lifecycle.py.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    row = await implementation_registry.get(pool, implementation_id, scope=AccessScope.unrestricted())
    if row is None:
        return f"REFUSED: no implementation found for id {implementation_id!r}."
    from app.execution.implementation_lifecycle import compute_implementation_lifecycle_state
    lifecycle = await compute_implementation_lifecycle_state(pool, implementation_id)
    # Full row for humans + the canonical, deterministic, secret-free
    # execution descriptor (§1/§22/§27) a harness consumer binds against.
    return json.dumps(
        {**row, "descriptor": implementation_registry.descriptor(row), "lifecycle": lifecycle}, default=str,
    )


@server.tool()
async def list_task_implementations(task_node_id: str, ctx: Context, status: str = "active") -> str:
    """
    Directive Sec 76: every implementation linked to a task_node. Thin
    wrapper around `implementation_registry.get_for_task()`. `status`
    defaults to 'active'; pass 'all' to see every lifecycle state (an
    inspection view, same sentinel the REST endpoint uses).
    """
    pool = ctx.request_context.lifespan_context["pool"]
    resolved_status = None if status == "all" else status
    if resolved_status is not None and resolved_status not in implementation_registry.STATUS_VALUES:
        return (
            f"REFUSED: unknown status {resolved_status!r} "
            f"(valid: {implementation_registry.STATUS_VALUES}, or 'all')."
        )
    rows = await implementation_registry.get_for_task(
        pool, task_node_id, scope=AccessScope.unrestricted(), status=resolved_status,
    )
    return json.dumps(rows, default=str)


@server.tool()
async def submit_implementation(
    procedure_id: str, role: str, ctx: Context,
    implementation_id: Optional[str] = None,
    name: Optional[str] = None, kind: Optional[str] = None, provider: Optional[str] = None,
    version: int = 1, description: Optional[str] = None,
    supported_steps_json: str = "[]", locator_json: str = "{}",
    invocation_json: str = "{}", input_schema_json: str = "{}", output_schema_json: str = "{}",
    requirements_json: str = "{}", source_ref: Optional[str] = None,
    author: Optional[str] = None, license: Optional[str] = None,
) -> str:
    """
    MCP hardening B23: "a tool builder MUST be able to submit/register an
    Implementation against one or more existing Procedures without
    creating a reusable TaskNode." Two modes, both ending in a real
    `procedure_implementations` row (the pre-existing, real, bi-temporal
    relation table `app/services/skill_ingestion.py` already writes and
    `publication.py` already reads -- migration 58 gave it the migration
    file it never had; this tool is a second, independent writer of the
    SAME table, not a parallel one):

    1. `implementation_id` given -- links that ALREADY-registered,
       durable Implementation (`inspect_implementation`/
       `resolve_implementation`'s own identity) to `procedure_id`.
    2. `implementation_id` omitted -- registers a brand-new Implementation
       first (via `implementation_registry.register()`, same "nothing is
       born trusted" candidate/unverified posture every other capture
       path here uses), THEN links it. Requires `name`/`kind`/`provider`.

    The relation itself is always born `status='candidate'` via THIS
    call path -- calling this does not make the binding `active`; that
    is a separate, evidence-driven promotion (not automated here,
    matching B23's own "Do not invent numeric coverage/quality scores
    unless they come from recorded evaluation"). Note: `skill_ingestion.
    py`'s OWN, separate, unrelated call path relies on this table's
    column default (`'active'`) for its bundled-script implementations,
    whose trust comes from package admission elsewhere -- this tool
    never relies on that default, it always states `candidate` itself.

    `procedure_id` is the STABLE Procedure family id (not a specific
    version's row id) -- same identity `execution_runs.procedure_id`
    already uses. HONEST LIMITATION inherited from the real table: there
    is no per-Procedure-version pinning -- a relation applies to the
    whole family, every version, always (the real table has no
    `procedure_version` column at all).

    `role`: primary | supporting | partial | verification (B23's own
    vocabulary). `supported_steps_json`: JSON array of step orders this
    Implementation actually covers -- `"[]"` (the default) means
    unrestricted (applies to every step), NOT "covers zero steps".
    """
    from app.services.procedure_implementation_bindings import (
        ROLES, ProcedureImplementationBindingError, link_implementation,
    )

    if role not in ROLES:
        return f"REFUSED: role must be one of {ROLES}, got {role!r}."
    try:
        supported_steps = json.loads(supported_steps_json)
        locator = json.loads(locator_json)
        invocation = json.loads(invocation_json)
        input_schema = json.loads(input_schema_json)
        output_schema = json.loads(output_schema_json)
        requirements = json.loads(requirements_json)
    except json.JSONDecodeError as exc:
        return f"REFUSED: malformed JSON parameter -- {exc}"

    pool = ctx.request_context.lifespan_context["pool"]
    created_by = _resolve_caller_identity(fallback="submit_implementation")

    if implementation_id is None:
        if not (name and kind and provider):
            return (
                "REFUSED: implementation_id was omitted, so name, kind, and "
                "provider are all required to register a new Implementation."
            )
        try:
            new_impl = await implementation_registry.register(
                pool, name=name, kind=kind, provider=provider, created_by=created_by,
                description=description, version=version, locator=locator,
                invocation=invocation, input_schema=input_schema, output_schema=output_schema,
                requirements=requirements, source_ref=source_ref, author=author, license=license,
            )
        except implementation_registry.ImplementationRegistryError as exc:
            return f"REFUSED: {exc}"
        except asyncpg.UniqueViolationError:
            return (
                f"REFUSED: an implementation named {name!r} from provider {provider!r} "
                f"version {version} already exists -- resolve/inspect it and pass its "
                "implementation_id instead of re-registering."
            )
        implementation_id = new_impl["id"]

    try:
        binding = await link_implementation(
            pool, procedure_id=procedure_id, implementation_id=implementation_id, role=role,
            supported_steps=supported_steps,
            created_by=created_by,
        )
    except ProcedureImplementationBindingError as exc:
        return f"REFUSED: {exc}"
    return json.dumps({"implementation_id": implementation_id, "binding": binding}, default=str)


@server.tool()
async def get_implementation_capability(implementation_id: str, ctx: Context) -> str:
    """
    Directive Sec 76: capability estimate for one durable implementation.
    Prefers the sibling `app.services.capabilities.get_implementation_
    capability` when it exists (parallel workstream this same wave);
    falls back to the same honest, clearly-labeled provisional Wilson-
    interval computation `app/api/implementations.py::
    _inline_capability_fallback` uses, duplicated here rather than
    imported across the api/mcp_server boundary (this file imports no
    app.api.* modules today -- keeping that boundary intact rather than
    introducing the first such cross-import). Public/read-only, no
    credential exposure -- same posture as every read tool in this file.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    scope = AccessScope.unrestricted()

    parent = await implementation_registry.get(pool, implementation_id, scope=scope)
    if parent is None:
        return f"REFUSED: no implementation found for id {implementation_id!r}."

    try:
        from app.services.capabilities import (  # type: ignore[import-not-found]
            get_implementation_capability as _sibling_get_capability,
        )
    except ImportError:
        from app.services.access import visibility_predicate
        from app.services.procedure_extraction.capability import (
            band_for_p, route_for_p, wilson_interval,
        )

        vis_sql, vis_params = visibility_predicate(scope, param_index=2)
        rows = await pool.fetch(
            f"""
            SELECT * FROM evidence
            WHERE target_type = 'implementation' AND target_id = $1::uuid
              AND t_invalid IS NULL AND {vis_sql}
            ORDER BY t_valid ASC
            """,
            implementation_id, *vis_params,
        )
        evidence = [dict(row) for row in rows]

        outcome_bearing = [
            e for e in evidence
            if e.get("direction") == "supports"
            and e.get("evidence_type") in ("execution_result", "reproduction")
            and e.get("outcome_status") in ("success", "failure")
        ]
        total = len(outcome_bearing)
        successes = sum(1 for e in outcome_bearing if e["outcome_status"] == "success")
        p_lower, p_upper = wilson_interval(successes, total)
        independent_groups = len({
            e["independence_group"] for e in outcome_bearing if e.get("independence_group")
        })
        band = band_for_p(p_lower) if (total > 0 and successes > 0) else 0
        result = {
            "p_estimate": p_lower, "p_lower": p_lower, "p_upper": p_upper,
            "evidence_count": total, "success_count": successes,
            "independent_groups": independent_groups, "band": band,
            "routing": route_for_p(p_lower).value, "level_gated": None,
            "provisional": True,
        }
        return json.dumps(result, default=str)

    # The sibling module's own signature takes no `scope` -- it reads
    # evidence unfiltered by visibility (its own design choice, not
    # altered here). We've already confirmed the parent row itself is
    # visible above.
    result = await _sibling_get_capability(pool, implementation_id)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Product-model tools (directive §37) -- Problem / Benchmark / Solution /
# Evaluation. Every one is a thin read wrapper over
# app.services.product_model: REST and MCP converge on that one service,
# no ranking or lineage logic here.
# ---------------------------------------------------------------------------
@server.tool()
async def find_problem(query: str, ctx: Context, limit: int = 10) -> str:
    """
    Natural-language search for a Problem. Returns ranked matches
    (title/description/objective), scoped to the caller. JSON:
    {query, problems:[{id, title, status, objective, ...}]}.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    rows = await _pm.find_problem(pool, query, scope=_caller_access_scope(), limit=limit)
    return json.dumps({"query": query, "problems": rows}, default=str)


@server.tool()
async def inspect_problem(problem_id: str, ctx: Context) -> str:
    """
    One Problem with its benchmarks, solutions and the current
    evidence-derived leaderboard (current best VERIFIED solution, or
    []=none yet). JSON: {problem, benchmarks, solutions, leaderboard}.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    scope = _caller_access_scope()
    p = await _pm.get_problem(pool, problem_id, scope=scope)
    if p is None:
        return "REFUSED: problem not found or out of scope"
    return json.dumps({
        "problem": p,
        "benchmarks": await _pm.list_problem_benchmarks(pool, problem_id, scope=scope),
        "solutions": await _pm.list_problem_solutions(pool, problem_id, scope=scope),
        "leaderboard": await _pm.problem_leaderboard(pool, problem_id, scope=scope),
    }, default=str)


@server.tool()
async def list_problem_solutions(problem_id: str, ctx: Context) -> str:
    """Every Solution associated with a Problem (association rows only, no
    target objects copied). JSON: {solutions:[...]}."""
    pool = ctx.request_context.lifespan_context["pool"]
    rows = await _pm.list_problem_solutions(pool, problem_id, scope=_caller_access_scope())
    return json.dumps({"problem_id": problem_id, "solutions": rows}, default=str)


@server.tool()
async def compare_solutions(problem_id: str, solution_ids_json: str, ctx: Context) -> str:
    """
    Compare specific Solutions of one Problem on their COMPARABLE completed
    evaluations only (§18/§52). solution_ids_json: a JSON list of solution
    ids. JSON: {leaderboard:[only the requested, comparable ones],
    current_best, conditional_leaders, excluded:[ids not comparable or
    without evidence]}.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    try:
        want = set(json.loads(solution_ids_json))
    except (ValueError, TypeError):
        return "REFUSED: solution_ids_json must be a JSON list of ids"
    lb = await _pm.problem_leaderboard(pool, problem_id, scope=_caller_access_scope())
    kept = [e for e in lb["leaderboard"] if e["solution_id"] in want]
    excluded = sorted(want - {e["solution_id"] for e in kept})
    best = [s for s in lb["current_best"] if s in want]
    return json.dumps({
        "problem_id": problem_id, "benchmark_id": lb["benchmark_id"],
        "leaderboard": kept, "current_best": best,
        "current_best_is_tie": len(best) > 1,
        "conditional_leaders": {k: v for k, v in lb["conditional_leaders"].items()
                                if v in want},
        "excluded": excluded,
        "note": "only completed, mutually-comparable evaluations are ranked (§18).",
    }, default=str)


@server.tool()
async def inspect_evaluation(evaluation_id: str, ctx: Context) -> str:
    """
    One Evaluation: its version-pinned procedure/implementation, recomputed
    metrics, verification summary, status, and the linked execution ids
    (the lineage a completed result must have). JSON: the evaluation row +
    {executions:[...]}.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    e = await _pm.get_evaluation(pool, evaluation_id, scope=_caller_access_scope())
    if e is None:
        return "REFUSED: evaluation not found"
    return json.dumps(e, default=str)


@server.tool()
async def find_best_solution(goal: str, ctx: Context) -> str:
    """
    Natural-language goal -> matched Problem -> that Problem's current best
    VERIFIED solution, derived from completed-evaluation lineage
    (§38/§51). Never picks a "best" from text similarity alone: the match
    is a Problem, the answer is that Problem's evidence-derived
    leaderboard. Returns {result: "verified" | "no verified solution yet"
    | "no matching problem", matched_problem, current_best, leaderboard,
    conditional_leaders}.

    Distinct from `find_best_way`, which is the retrieval-grounded HTN
    coding agent (precedent -> plan -> execute). This one answers "which
    known solution is measurably best" and does not execute anything.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    return json.dumps(
        await _pm.find_best_way(pool, goal, scope=_caller_access_scope()),
        default=str,
    )


# ---------------------------------------------------------------------------
# Durable execution-run retry / resume tools (final-V1 §2, §34) -- a thin
# MCP surface over the PROVEN durable-run service (app/execution/durable_run.py,
# migrations 36/37) via app.execution.durable_resume. NO retry/resume logic
# lives here: every state transition, lease, terminal fence and attempt
# bound is owned by durable_run. Reads are open; mutations are gated on the
# resolved caller identity matching execution_runs.created_by.
# ---------------------------------------------------------------------------
@server.tool()
async def continue_run(procedure_run_id: str, ctx: Context, repo_path: Optional[str] = None) -> str:
    """
    MCP hardening B4/B32: the normal way to keep working against a
    Procedure a prior `find_best_way` call already selected, instead of
    re-searching. Loads the EXACT pinned Procedure version this run was
    created against, the current node/run state, each precondition's
    live TRUE/FALSE/UNKNOWN status (never collapsed -- B27), and
    (bounded, current-node-only) recommended Implementations, then
    returns the smallest useful next-action packet: current_phase_or_node,
    objective, required_preconditions, relevant_claim_refs,
    recommended_implementations, required_checks, allowed_branches,
    blocking_unknowns, next_when_satisfied.

    Read-only -- this tool does not advance the run. Report real progress
    via `report_execution`; retry a specific failed/blocked node via
    `retry_run_node`; resume after a crash via `resume_execution_run`.
    (Host-executed progress reporting that itself transitions node state
    is a separate, later gate -- B6 -- not built here.)

    `repo_path`: optional -- when given, also refreshes this run's
    `.stealth/{context.md,run.json,meta.json}` projection (B35) under
    that workspace root, from the SAME canonical state just returned.
    Best-effort: a write failure never turns this call into a REFUSED
    (the primary MCP response is the source of truth either way), but
    is surfaced via `stealth_projection` in the response, never swallowed.

    REFUSED if `procedure_run_id` does not exist.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    context = await _dres.get_run_context(pool, procedure_run_id)
    if context is None:
        return f"REFUSED: procedure_run_id {procedure_run_id!r} not found"
    if repo_path is not None:
        from app.execution.stealth_projection import generate_projection

        try:
            await generate_projection(pool, workspace_root=repo_path, procedure_run_id=procedure_run_id)
            context["stealth_projection"] = "written"
        except OSError as exc:
            context["stealth_projection"] = f"write_failed: {exc}"
    return json.dumps(context, default=str)


@server.tool()
async def verify_completion(procedure_run_id: str, ctx: Context, reports_json: str = "[]") -> str:
    """
    MCP hardening B34/B32: evaluates the SELECTED Procedure's explicit
    success criteria (`postconditions`) for this run -- never "did the
    host say it finished?". Criteria are derived from the pinned
    Procedure version itself (`postcondition:0`, `postcondition:1`, ...,
    in order); a criterion with no recorded evidence stays
    `inconclusive`, never silently passing.

    `reports_json`: optional JSON array of evidence reports to record
    BEFORE evaluating, one object per criterion, each shaped
    `{"criterion_id": "postcondition:0", "method": "...", ...}` where
    the remaining fields depend on `method`:
      self_report           -- claimed_success: bool
      artifact_inspection   -- passed: bool, evidence_refs?: [str]
      deterministic_check   -- passed: bool, evidence_refs?: [str]
      independent_agent     -- passed: bool, evidence_refs?: [str]
      real_world_outcome    -- passed: bool, evidence_refs?: [str]
      human_review          -- reviewer: str, reviewed_targets: [str],
                                 criterion_answers: {}, verdict: bool,
                                 evidence_refs?: [str]
    every shape may also carry `detail`: str.

    THE STRONGEST STATE IS NEVER CALLER-WRITABLE: `method` selects which
    evidence-class-specific function records the result, and each one
    computes the resulting state itself from what that class can
    actually support -- `self_report` can never produce `verified` no
    matter what `passed`/`claimed_success` says.

    Omit `reports_json` (or pass `"[]"`) to just READ the current
    evaluation without recording anything new.

    Returns `{execution_run_id, overall_state, criteria: [...]}`.
    REFUSED if `procedure_run_id` does not exist or a report names an
    unknown `criterion_id`/`method`.
    """
    from app.execution.procedure_graph import fetch_procedure_version
    from app.services import verification as _verif

    pool = ctx.request_context.lifespan_context["pool"]
    run = await pool.fetchrow(
        "SELECT procedure_id, procedure_version, created_by FROM execution_runs WHERE id = $1::uuid",
        procedure_run_id,
    )
    if run is None:
        return f"REFUSED: procedure_run_id {procedure_run_id!r} not found"
    procedure = await fetch_procedure_version(pool, run["procedure_id"], run["procedure_version"])
    if procedure is None:
        return f"REFUSED: pinned procedure version not found for run {procedure_run_id!r}"

    try:
        reports = json.loads(reports_json)
    except json.JSONDecodeError as exc:
        return f"REFUSED: malformed reports_json -- {exc}"
    if not isinstance(reports, list):
        return "REFUSED: reports_json must be a JSON array."

    criteria_by_id = {c.criterion_id: c for c in _verif.derive_criteria(procedure)}
    created_by = _resolve_caller_identity(fallback="verify_completion")

    for report in reports:
        if not isinstance(report, dict):
            return f"REFUSED: each report must be a JSON object, got {report!r}"
        criterion_id = report.get("criterion_id")
        method = report.get("method")
        criterion = criteria_by_id.get(criterion_id)
        if criterion is None:
            return f"REFUSED: unknown criterion_id {criterion_id!r} for this pinned procedure version."
        if method not in _verif.METHODS:
            return f"REFUSED: unknown method {method!r} (valid: {_verif.METHODS})."
        common = dict(
            pool=pool, execution_run_id=procedure_run_id, criterion_id=criterion_id,
            statement=criterion.statement, required=criterion.required,
            detail=report.get("detail"), created_by=created_by,
        )
        try:
            if method == "self_report":
                await _verif.record_self_report(claimed_success=bool(report["claimed_success"]), **common)
            elif method == "artifact_inspection":
                await _verif.record_artifact_inspection(
                    passed=bool(report["passed"]), evidence_refs=report.get("evidence_refs"), **common,
                )
            elif method == "deterministic_check":
                await _verif.record_deterministic_check(
                    passed=bool(report["passed"]), evidence_refs=report.get("evidence_refs"), **common,
                )
            elif method == "independent_agent":
                await _verif.record_independent_agent(
                    passed=bool(report["passed"]), evidence_refs=report.get("evidence_refs"), **common,
                )
            elif method == "real_world_outcome":
                await _verif.record_real_world_outcome(
                    passed=bool(report["passed"]), evidence_refs=report.get("evidence_refs"), **common,
                )
            elif method == "human_review":
                await _verif.record_human_review(
                    reviewer=report["reviewer"], reviewed_targets=report["reviewed_targets"],
                    criterion_answers=report.get("criterion_answers", {}),
                    verdict=bool(report["verdict"]), evidence_refs=report.get("evidence_refs"), **common,
                )
        except KeyError as exc:
            return f"REFUSED: report for {criterion_id!r} (method={method!r}) missing required field {exc}"
        except _verif.VerificationError as exc:
            return f"REFUSED: {exc}"

    result = await _verif.evaluate_run_completion(pool, execution_run_id=procedure_run_id, procedure=procedure)
    return json.dumps(result, default=str)


@server.tool()
async def declare_file_intent(
    procedure_run_id: str, node_order: int, owner_agent_id: str, ctx: Context,
    write_exact_json: str = "[]", write_globs_json: str = "[]",
    read_exact_json: str = "[]", read_globs_json: str = "[]",
    symbols_expected_to_modify_json: str = "[]", lease_seconds: int = 3600,
) -> str:
    """
    MCP hardening B36: declare (or renew) which files/globs one node of
    a ProcedureRun expects to read/write, BEFORE starting substantial
    work -- so a second agent working on an overlapping run can detect
    the conflict instead of silently racing it. Advisory coordination,
    not an OS lock (nothing here stops an actual filesystem write) --
    but the declaration itself is real and durable, and overlap
    detection is real, not a placeholder.

    Checks for conflicts FIRST: if `write_exact`/`write_globs` overlaps
    ANOTHER node's still-live declaration (any run, any owner -- not
    this exact (procedure_run_id, node_order), which may freely
    re-declare/renew its own intent), REFUSES with the exact conflicting
    run/node/owner/files rather than silently allowing the overlap or
    overwriting the other declaration.

    `lease_seconds`: how long this declaration stays live before it is
    honestly stale and excluded from future conflict checks (B36:
    "expired/stale lease" must itself be detected, never treated as
    still-claiming).
    """
    from app.execution.coordination import (
        DependencyViolation, FileIntentConflict, declare_file_intent as _declare,
    )

    try:
        write_exact = json.loads(write_exact_json)
        write_globs = json.loads(write_globs_json)
        read_exact = json.loads(read_exact_json)
        read_globs = json.loads(read_globs_json)
        symbols = json.loads(symbols_expected_to_modify_json)
    except json.JSONDecodeError as exc:
        return f"REFUSED: malformed JSON parameter -- {exc}"

    pool = ctx.request_context.lifespan_context["pool"]
    try:
        row = await _declare(
            pool, execution_run_id=procedure_run_id, node_order=node_order,
            owner_agent_id=owner_agent_id, read_exact=read_exact, read_globs=read_globs,
            write_exact=write_exact, write_globs=write_globs,
            symbols_expected_to_modify=symbols, lease_seconds=lease_seconds,
        )
    except DependencyViolation as exc:
        return f"REFUSED: {exc}"
    except FileIntentConflict as exc:
        return json.dumps({
            "conflict": True,
            "conflicts": [
                {
                    "execution_run_id": c.execution_run_id, "node_order": c.node_order,
                    "owner_agent_id": c.owner_agent_id, "kind": c.kind,
                    "overlapping_files": c.overlapping_files,
                    "overlapping_symbols": c.overlapping_symbols,
                }
                for c in exc.conflicts
            ],
        }, default=str)
    except ValueError as exc:
        return f"REFUSED: {exc}"
    return json.dumps({"conflict": False, "declaration": row}, default=str)


@server.tool()
async def inspect_run(run_id: str, ctx: Context) -> str:
    """
    Inspect a durable execution run: overall status, per-node status /
    attempt_count / max_attempts / error_class, the pinned implementation
    binding, worker/lease, first-pass vs final, the full per-node attempt
    history, the run's position in the B4 Stealth Execution Contract
    (RUN_CREATED -> ... -> FINALIZED, derived from real transactionally-
    persisted facts across route_decisions/execution_run_nodes/
    execution_run_events/verification_results/evidence -- see
    app/execution/stealth_execution_contract.py), and the B17/B33
    planned-vs-actual deviation report (per-node: did it fail, get
    blocked, need a retry, or run under a DIFFERENT implementation than
    the compiled plan named -- see app/execution/plan_deviation.py).
    Read-only. JSON: {status, nodes:[...], history:[...],
    execution_contract:{reached, current_state, skipped_optional},
    plan_deviation:{per_node, material_deviation, summary}}. REFUSED if
    the run does not exist.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    status = await _dres.run_status_by_id(pool, run_id)
    if status is None:
        return f"REFUSED: execution run {run_id!r} not found"
    history = await _dres.node_history_by_id(pool, run_id)
    from app.execution.plan_deviation import compute_plan_deviation
    from app.execution.stealth_execution_contract import compute_execution_contract_state
    contract_state = await compute_execution_contract_state(pool, run_id)
    deviation = await compute_plan_deviation(pool, run_id)
    return json.dumps(
        {
            "status": status, "history": history, "execution_contract": contract_state,
            "plan_deviation": deviation,
        },
        default=str,
    )


@server.tool()
async def resume_execution_run(run_id: str, ctx: Context) -> str:
    """
    Resume an eligible durable run through the durable-run service. A
    coding-agent / sandbox run (its plan compiled by
    find_best_way_plan_compiler / reproduce_procedure_plan_compiler, or a
    pending node with no real provider) is NOT faked -- it returns
    {"status": "needs_product_context", ...} pointing at
    find_best_way(resume_run_id=...) / reproduce_procedure. An
    already-terminal run is an idempotent no-op. REFUSED if the run does
    not exist or the resolved caller is not its creator.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    actor_id = _dres.resolved_caller_identity_or_none()
    worker_id = f"mcp-{_resolve_caller_identity(fallback='resume_execution_run')}"
    try:
        result = await _dres.resume_run_by_id(
            pool, run_id, worker_id=worker_id, actor_id=actor_id,
        )
    except _dres.NotYourRun as e:
        return f"REFUSED: not your run -- {e}"
    except _dr.ResumeInProgress as e:
        return f"REFUSED: run is being resumed by another worker -- {e}"
    except _dr.DurableRunError as e:
        return f"REFUSED: {e}"
    return json.dumps(result, default=str)


@server.tool()
async def retry_run_node(run_id: str, node_order: int, ctx: Context, force: bool = False) -> str:
    """
    Explicit bounded retry of ONE failed / resumable / blocked node of a
    durable run, through the durable-run service. `force=True` bumps that
    node's max_attempts by 1 (operator override for an exhausted node). A
    succeeded node is never retried -- the service returns "already
    succeeded -- terminal". Same needs_product_context refusal as
    resume_execution_run for coding-agent / no-provider runs. REFUSED if
    the run/node does not exist or the resolved caller is not the run's
    creator.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    actor_id = _dres.resolved_caller_identity_or_none()
    worker_id = f"mcp-{_resolve_caller_identity(fallback='retry_run_node')}"
    try:
        result = await _dres.retry_run_node_by_id(
            pool, run_id, node_order,
            worker_id=worker_id, actor_id=actor_id, force=force,
        )
    except _dres.NotYourRun as e:
        return f"REFUSED: not your run -- {e}"
    except _dr.ResumeInProgress as e:
        return f"REFUSED: run is being resumed by another worker -- {e}"
    except _dr.DurableRunError as e:
        return f"REFUSED: {e}"
    return json.dumps(result, default=str)


@server.tool()
async def report_node_progress(
    run_id: str, node_order: int, ok: bool, ctx: Context,
    result_json: str = "{}", error_class: Optional[str] = None, error_json: str = "{}",
) -> str:
    """
    MCP hardening B6: host-executed Procedure lease progress reporting.
    For a run whose node was executed OUTSIDE Stealth's own sandbox (the
    `plan_only`/`continue_run` pattern -- `find_best_way(mode='plan_only')`
    hands back real steps for the host's OWN tools to execute), this is
    how the host reports that ONE node's real, observed outcome back so
    the durable run's own state actually reflects it -- rather than that
    node sitting `pending` forever.

    Transitions through the EXACT SAME node-claim/finish mechanics the
    server's own driving loop (`execute_run`/`resume_run`) uses -- no
    second state-transition path, and the terminal-state fence still
    applies (an already-`succeeded` node cannot be rewritten). REFUSED
    if the run/node does not exist, the resolved caller is not the run's
    creator, or `result_json`/`error_json` is malformed. `{"claimed":
    false, "status": ...}` (NOT a REFUSED) is the honest answer when
    this node could not be claimed right now (already succeeded, or
    claimed by a live different worker) -- distinct from a genuine
    authorization/not-found refusal.
    """
    try:
        result = json.loads(result_json)
        error = json.loads(error_json)
    except json.JSONDecodeError as exc:
        return f"REFUSED: malformed JSON parameter -- {exc}"

    pool = ctx.request_context.lifespan_context["pool"]
    actor_id = _dres.resolved_caller_identity_or_none()
    worker_id = f"mcp-host-report-{_resolve_caller_identity(fallback='report_node_progress')}"
    try:
        outcome = await _dres.report_node_progress_by_id(
            pool, run_id, node_order, actor_id=actor_id, ok=ok,
            result=result, error_class=error_class, error=error, worker_id=worker_id,
        )
    except _dres.NotYourRun as e:
        return f"REFUSED: not your run -- {e}"
    except _dr.DurableRunError as e:
        return f"REFUSED: {e}"
    return json.dumps(outcome, default=str)


@server.tool()
async def get_route_decision(route_decision_id: str, ctx: Context) -> str:
    """
    Read-only inspection of one persisted RouteDecision (MCP hardening
    B2: "routing becomes observable and testable"). Every `find_best_way`
    call -- REFUSED, needs_clarification, assist, plan_ready,
    execution_ready, or no_applicable_procedure -- persists exactly one
    of these; this tool is how a caller (or a test) inspects why a
    specific call was routed the way it was, after the fact.
    REFUSED if no such route decision exists.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    from app.services.route_decision import get_route_decision as _get_route_decision
    decision = await _get_route_decision(pool, route_decision_id)
    if decision is None:
        return f"REFUSED: route_decision {route_decision_id!r} not found"
    return json.dumps(decision, default=str)


# ---------------------------------------------------------------------------
# ADDITIVE read-only MCP Resources + Prompts surface. Registered here, after
# every @server.tool() above, so resources.py can import the tool-layer
# helpers (_caller_access_scope / _resolve_live_procedure / ...) it composes
# over -- nothing above this line changes. Resources are visibility-scoped
# reads; prompts are orchestration-policy text. Neither mutates anything.
# ---------------------------------------------------------------------------
from app.mcp_server.resources import register_resources
from app.mcp_server.prompts import register_prompts

register_resources(server)
register_prompts(server)


if __name__ == "__main__":
    server.run()
