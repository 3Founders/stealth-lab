"""
StealthLab MCP server. Four tools:
  - retrieve_precedent, apply_change_set: thin wrappers, zero new business
    logic, wrap already-tested read/write functions.
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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional
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
from app.api.approval import decide, ApprovalRequest
from app.api.decompose import decompose, decide as decide_decomposition_fn, DecomposeRequest, DecideRequest
from app.models.change import ChangeSet
from app.services.access import AccessScope
from app.services.applicability import verified_procedure_candidates
from app.services.authn import current_actor_id
from app.services.decomposition import DecompositionService
from app.services.embeddings import Embedder
from app.services.knowledge_conflict import detect_and_create_conflict_trigger
from app.services.knowledge_update import ChangeApplicationError, KnowledgeUpdater
from app.services.local_retrieval import assemble_structural_context, retrieve_local_first
from app.services.procedure_extraction import extract_procedure
from app.services.procedure_extraction.evidence import AgentRunEvidenceSource
from app.services.retrieval import HybridRetriever
from app.services.reuse_detection import ReusableNode, _vector_candidates
from app import observability
from app.config import settings
from fastapi import HTTPException

# apply_debate_result.py lives in scripts/synthetic_tasks/, not app/ --
# same real, working sys.path pattern debate_curation.py (experiments/
# swebench_pro/) already uses to reach it, not a new approach.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "synthetic_tasks"))
from apply_debate_result import auto_preserve_missing_keys, preflight_validate

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

from app.mcp_server.tasks_extension import TasksExtension

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
    try:
        yield {"pool": pool}
    finally:
        await pool.close()


class StaticTokenVerifier(TokenVerifier):
    """Minimal bearer-token check for a single-tenant, loopback-bound
    deployment -- NOT a real OAuth flow. The SDK positions this server as
    an OAuth 2.1 RESOURCE server (it validates tokens, it does not issue
    them), and requires token_verifier+auth to be passed together; this is
    the simplest thing that satisfies that contract.

    Constant-time comparison (secrets.compare_digest) because this is a
    bearer secret compared against attacker-controlled input over the
    network -- a naive `==` leaks timing information proportional to the
    matching prefix length. `secrets` is already imported above (used for
    instance_id generation); this is its second, more load-bearing use.

    Deliberately NOT sufficient on its own: find_best_way's repo_path is
    caller-controlled and apply_change_set is an ungated write (see
    README_MCP_SERVER.md's "Known v1 limitations"). This gates WHO can
    reach those tools, it does not make either tool safe against a caller
    who does hold a valid token -- that is why hosting stays loopback-only
    (see the ASGI app / uvicorn invocation below), not exposed via tunnel.
    """

    def __init__(self, token: str):
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(token, self._token):
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
    token_verifier=StaticTokenVerifier(_require_mcp_token()),
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
       with token_verifier=StaticTokenVerifier(...) (confirmed by reading
       mcp/server/mcpserver/server.py: passing token_verifier makes
       create_app() add BearerAuthBackend + AuthContextMiddleware to the
       Starlette stack). Its AccessToken.subject (RFC 7662/9068 `sub`) is
       real per-request identity, when the verifier sets one.

       HONEST GAP, not fixed here: StaticTokenVerifier (this file, above)
       validates ONE shared STEALTHLAB_MCP_TOKEN for every caller and
       never sets .subject -- every caller today gets the same
       client_id="stealthlab-local" and no subject, so in the current
       deployment this branch is always empty. That IS the single-
       shared-identity gap; closing it for real needs per-caller tokens
       or an OIDC-verifying TokenVerifier (a real identity provider),
       explicitly out of scope for this change. What this function does
       is make the seam real: the moment a verifier ever sets .subject,
       every call site below picks it up with zero further change.

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
    """
    token = get_access_token()
    if token is not None and token.subject:
        return token.subject
    actor_id = current_actor_id()
    if actor_id:
        return actor_id
    return fallback


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


@server.tool()
async def apply_change_set(change_set_json: str, ctx: Context) -> str:
    """
    Validate and apply a change_set directly to the real graph, WITHOUT
    any approval gate.

    USE submit_approval INSTEAD if this change_set came from
    propose_synthesis. This tool does not check debate state, does not
    require APPROVED, and does not write an approvals audit row -- using
    it on a debate scorecard's change_set bypasses human approval entirely,
    a real gap this project's own MCP testing found and submit_approval
    exists specifically to close. Use apply_change_set for change_sets
    that never had a debate to begin with -- decompose_task's output is
    the real, intended case (decomposition proposals aren't debated,
    they're reviewed directly by whoever calls apply_change_set).

    Thin wrapper around the exact real, already-validated pipeline this
    project proved works end-to-end on a real case (the synthetic Task
    A/B merge): auto_preserve_missing_keys (deterministic bookkeeping
    carry-forward, not left to LLM judgment) -> preflight_validate (an
    INDEPENDENT safety check against the real current DB state,
    regardless of what the proposal itself claims) -> KnowledgeUpdater's
    real, transactional apply. This tool adds no new validation logic
    of its own -- every real safety check already exists and is already
    tested elsewhere.

    change_set_json: a JSON string matching the real ChangeSet schema
    (a dict with an "ops" list) -- typically decompose_task's output, or
    a manually constructed proposal for testing.

    Will genuinely REFUSE, not silently do something wrong, if: the
    JSON is malformed, pre-flight validation finds a real problem (e.g.
    a proposal that would silently delete real existing data), or
    KnowledgeUpdater itself rejects the change set. Every refusal
    returns the real, specific reason -- never fails silently.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    try:
        change_set_dict = json.loads(change_set_json)
    except json.JSONDecodeError as exc:
        return f"REFUSED: change_set_json is not valid JSON -- {exc}"

    change_set_dict = await auto_preserve_missing_keys(pool, change_set_dict)
    problems = await preflight_validate(pool, change_set_dict)
    if problems:
        lines = ["REFUSED -- pre-flight validation found real problem(s), independent of "
                 "what the proposal itself claims:"]
        lines.extend(f"  - {p}" for p in problems)
        return "\n".join(lines)

    try:
        change_set = ChangeSet.model_validate(change_set_dict)
    except Exception as exc:  # noqa: BLE001 -- real pydantic ValidationError, report it plainly
        return f"REFUSED: change_set does not match the real ChangeSet schema -- {exc}"

    updater = KnowledgeUpdater(pool)
    try:
        applied = await updater.apply(change_set, approver_id="mcp_apply_change_set")
    except ChangeApplicationError as exc:
        return f"REFUSED by KnowledgeUpdater itself -- {exc}"

    lines = ["Applied successfully -- this was a real write to the graph, not a proposal:"]
    for a in applied:
        lines.append(f"  {a}")
    return "\n".join(lines)


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
    from app.execution.procedure_graph import expand_procedure_steps

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
    nodes = await expand_procedure_steps(
        pool, procedure_id=matched_procedure["procedure_id"],
        procedure_version=matched_procedure["version"], steps=steps,
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
    await record_plan_execution(
        pool, compiled=compiled_plan,
        outcome=result.outcome, created_by="find_best_way",
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


@server.tool()
async def find_best_way(task_description: str, ctx: Context,
                         repo_path: Optional[str] = None,
                         mode: str = "auto",
                         model: str = "gemma-4-31B-it", max_steps: int = 25,
                         session_id: Optional[str] = None,
                         allow_unverified_procedures: bool = False) -> str:
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
    tier 1, go straight to tier 2 (requires `repo_path`).

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

    if mode not in ("auto", "lookup_only", "full_run"):
        return f"REFUSED: mode must be one of 'auto', 'lookup_only', 'full_run' (got {mode!r})."
    if repo_path is not None and not os.path.isdir(repo_path):
        return f"REFUSED: repo_path {repo_path!r} is not a directory on this server."
    if mode == "full_run" and repo_path is None:
        return "REFUSED: mode='full_run' requires repo_path."

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
    )
    matched_procedure = matched_procedures[0] if matched_procedures else None

    if matched_procedure is not None and mode != "full_run":
        return await _respond_tier1_hit(pool, task_description, matched_procedure)
    if mode == "lookup_only":
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
        adhoc = await capture_procedure(
            pool, name=f"ad-hoc: {task_description[:80]}", goal=task_description,
            steps=[{"order": 0, "goal": task_description}],
            provenance="system_pending_review", scope_type="global",
            created_by=_resolve_caller_identity(fallback="find_best_way_adhoc"),
        )
        plan_procedure_row_id = adhoc["id"]
    procedure_payload = await get_procedure(pool, plan_procedure_row_id)

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
    from app.execution.graph_executor import NodeResult, execute_task_graph
    from app.execution.plan_persistence import persist_compiled_plan, record_plan_execution
    from app.execution.plans import compile_plan
    from app.execution.procedure_graph import expand_procedure_steps

    steps = procedure_payload.get("steps") or [{"order": 0, "goal": task_description}]
    # Phase 10: same real expansion as the tier-1 lookup path above --
    # a composed procedure's referenced sub-procedure steps are spliced
    # in before compile_plan sees them.
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure_payload["procedure_id"],
        procedure_version=procedure_payload["version"], steps=steps,
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

    graph_result = await execute_task_graph(compiled_plan.graph, run_node=run_node)

    # Aggregate across every real node that actually ran (skipped nodes
    # contribute nothing -- they never called the agent at all).
    all_tool_calls = [tc for r in node_runs.values() for tc in r.tool_calls]
    all_files_edited = sorted({f for r in node_runs.values() for f in r.files_edited})
    combined_patch = "\n".join(r.patch for r in node_runs.values() if r.patch)
    total_prompt_tokens = sum(r.usage.prompt_tokens for r in node_runs.values())
    total_completion_tokens = sum(r.usage.completion_tokens for r in node_runs.values())
    total_calls = sum(r.usage.calls for r in node_runs.values())
    total_wall_seconds = sum(r.wall_seconds for r in node_runs.values())

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

    await record_plan_execution(
        pool, compiled=compiled_plan,
        outcome=graph_result.outcome, created_by="find_best_way",
    )

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
                               transfer_repo_path: Optional[str] = None) -> str:
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
            detected_by=f"reproduce_procedure:{os.path.basename(os.path.abspath(target_repo_path))}",
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

    from app.execution.graph_executor import NodeResult, execute_task_graph
    from app.execution.plan_persistence import persist_compiled_plan, record_plan_execution
    from app.execution.plans import compile_plan
    from app.execution.procedure_graph import expand_procedure_steps

    client = OpenAI(
        max_retries=0,
        api_key=settings.require("general_compute_api_key"),
        base_url=settings.general_compute_base_url,
    )

    # Phase 10: expand once, reused by both the same-repo and (when
    # requested) transfer-tier runs below -- both replay the SAME
    # procedure's same steps, just against different target repos.
    expanded_nodes = await expand_procedure_steps(
        pool, procedure_id=procedure_payload["procedure_id"],
        procedure_version=procedure_payload["version"], steps=steps,
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
            created_by="reproduce_procedure",
        )
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

        graph_result = await execute_task_graph(compiled_plan.graph, run_node=run_node)

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

        await record_plan_execution(
            pool, compiled=compiled_plan,
            outcome=graph_result.outcome, created_by="reproduce_procedure",
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
        return f"REFUSED: {exc}"

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


async def _resolve_live_procedure(pool, procedure_id: str) -> dict:
    """Shared resolver: a stable procedure_id -> its current live version
    row. The exact query applicability.py::check_procedure_reuse() already
    uses -- reused here, not duplicated, so both paths agree by
    construction on what "the current live version" means."""
    from app.services.applicability import ProcedureNotFound

    try:
        proc_uuid = UUID(str(procedure_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ProcedureNotFound(f"{procedure_id!r} is not a valid procedure id (UUID)") from exc
    row = await pool.fetchrow(
        "SELECT * FROM procedures WHERE procedure_id = $1::uuid AND t_invalid IS NULL",
        proc_uuid,
    )
    if row is None:
        raise ProcedureNotFound(f"no live procedure for procedure_id={procedure_id}")
    return dict(row)


@server.tool()
async def search_procedures(task: str, ctx: Context, state: str = "{}", limit: int = 5,
                             require_verified: bool = True,
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
    require_verified: real ticket-13 gate, default True unchanged from
    every other caller in this codebase -- pass False to also see
    candidates that haven't earned verification evidence yet.
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
                            success_criteria: str | None = None,
                            failure_class: str | None = None) -> str:
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
    success_criteria: JSON object with a non-empty 'predicate' string
    and/or a non-empty 'metrics' object -- REQUIRED shape whenever
    success=true (invariant #13); omit to let
    record_execution_outcome() synthesize one from steps_used alone.
    failure_class: one of evidence.py's real failure_class values, when
    success=false and the caller knows the cause. Omitted is honest
    (lands in the requires_review queue) rather than guessed.

    Returns the procedure row's state AFTER any transition this call
    caused (promotion to verified, quarantine opening/closing) -- so a
    caller can observe a state change as a direct result of its own report.
    """
    pool = ctx.request_context.lifespan_context["pool"]
    from app.services.applicability import ProcedureNotFound
    from app.services.procedures import record_execution_outcome

    try:
        procedure = await _resolve_live_procedure(pool, procedure_id)
    except ProcedureNotFound as exc:
        return f"REFUSED: {exc}"

    try:
        criteria = json.loads(success_criteria) if success_criteria else None
    except json.JSONDecodeError as exc:
        return f"REFUSED: success_criteria must be a JSON object ({exc})"

    try:
        updated = await record_execution_outcome(
            pool, procedure_row_id=str(procedure["id"]), success=success,
            context_key=context_key, steps_used=steps_used,
            success_criteria=criteria, failure_class=failure_class,
        )
    except Exception as exc:  # noqa: BLE001 -- a producer-side contract
        # violation (e.g. invariant #13's bare-success refusal, or an
        # unknown failure_class) must reach the caller as a real
        # refusal, not an unhandled 500.
        return f"REFUSED: {exc}"

    return json.dumps({
        "procedure_id": procedure_id,
        "verification_state": updated["verification_state"],
        "availability": updated["availability"],
        "verification_stats": updated["verification_stats"],
    }, default=str)


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
    from app.services.v0_gate import V0Violation

    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        return f"REFUSED: steps_json must be a JSON array ({exc})"

    embedder = Embedder()
    goal_vec = await embedder.embed_one(goal, input_type="document")

    try:
        result = await capture_procedure(
            pool, name=name, goal=goal, steps=steps,
            provenance=provenance, domain=domain,
            scope_type="entity" if domain else "global",
            created_by=_resolve_caller_identity(fallback="mcp_submit_procedure"),
            embedding=goal_vec,
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

    body = DecideRequest(approver_id=approver_id, decision=decision)
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

    body = ApprovalRequest(approver_id=approver_id, decision=decision, note=note)
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


if __name__ == "__main__":
    server.run()
