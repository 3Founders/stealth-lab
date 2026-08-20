"""
StealthLab MCP server. Four tools:
  - retrieve_precedent, apply_change_set: thin wrappers, zero new business
    logic, wrap already-tested read/write functions.
  - propose_synthesis: thin wrapper around LoopOrchestrator.run(), the real
    debate orchestration used throughout this project.
  - solve_task: NOT a pure wrapper -- see its own docstring's HONEST STATUS
    section. Reuses RepoSandbox/Agent verbatim but adds real new
    orchestration (a generic, non-SWE-bench-specific instance/prompt path)
    on top.

Built against the real, installed mcp==2.0.0 SDK (2026-07-28 spec),
verified by direct introspection of the installed package -- not against
remembered pre-2.0 API names. Confirmed real: MCPServer (not FastMCP,
renamed in v2), the .tool() decorator, Context.lifespan for accessing the
DB pool from within a tool call.

propose_synthesis/solve_task are genuinely long-running (multi-round
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
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from pydantic import AnyHttpUrl

from app.db.session import create_pool
from app.api.approval import decide, ApprovalRequest
from app.api.decompose import decompose, decide as decide_decomposition_fn, DecomposeRequest, DecideRequest
from app.models.change import ChangeSet
from app.services.access import AccessScope
from app.services.decomposition import DecompositionService
from app.services.embeddings import Embedder
from app.services.knowledge_conflict import detect_and_create_conflict_trigger
from app.services.knowledge_update import ChangeApplicationError, KnowledgeUpdater
from app.services.local_retrieval import assemble_structural_context, retrieve_local_first
from app.services.retrieval import HybridRetriever
from app.services.reuse_detection import _vector_candidates
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

    Deliberately NOT sufficient on its own: solve_task's repo_path is
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
        "agent. propose_synthesis and solve_task are genuinely long-running "
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
# propose_synthesis/solve_task created -- the call would appear to hang.
app = server.streamable_http_app()


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

    query: a natural-language description of the problem to find a
    precedent for -- ordinary conversational phrasing is the expected,
    normal case for this tool, not a special query syntax.
    """
    pool = ctx.request_context.lifespan_context["pool"]

    embedder = Embedder()
    query_vec = await embedder.embed_one(query, input_type="query")
    raw_candidates = await _vector_candidates(pool, query_vec, AccessScope.unrestricted())
    candidates = [c for c in raw_candidates if c.similarity >= RETRIEVE_PRECEDENT_THRESHOLD]

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


@server.tool()
async def solve_task(task_description: str, repo_path: str, ctx: Context,
                      model: str = "gemma-4-31B-it", max_steps: int = 25,
                      session_id: Optional[str] = None) -> str:
    """
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
    """
    pool = ctx.request_context.lifespan_context["pool"]

    if not os.path.isdir(repo_path):
        return f"REFUSED: repo_path {repo_path!r} is not a directory on this server."

    embedder = Embedder()
    query_vec = await embedder.embed_one(task_description, input_type="query")
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

    # Procedure retrieval + instantiation (closes the extraction ->
    # applicability -> use -> outcome loop end to end, for the first
    # time). Scope narrowed by the SAME environment_probe.py extraction
    # itself derives from -- a procedure extracted with scope={"language":
    # ["python"]} only matches a call whose current_scope agrees, real
    # narrowing on both sides, not a coincidence.
    #
    # require_verified DEFAULTS TO TRUE and is deliberately left there,
    # not weakened to False to make something show up here today. Ticket
    # 13's own wording: "verified gates automatic retrieval; a candidate
    # procedure remains explicitly invocable." This IS automatic
    # selection (the system chose to look, nothing named this procedure
    # by id), so the honest behavior is: nothing is returned until a
    # procedure has real evidence (>=10 successes, 0 failures, >=3
    # distinct contexts) -- which this exact call path is what will,
    # over repeated real use, accumulate.
    from app.services.applicability import find_applicable_procedures
    from app.services.environment_probe import probe_environment
    from app.services.procedures import record_execution_outcome

    # Synchronous filesystem reads (a handful of specific top-level
    # files -- package.json/lockfiles/requirements.txt/pyproject.toml,
    # not a repo walk, so far cheaper than the structural producers
    # AnujB's fix above addresses) -- off the event loop for the same
    # reason regardless: consistency with that fix, not a measured
    # stall of its own.
    procedure_scope: dict = {}
    facts = await asyncio.to_thread(probe_environment, repo_path)
    lang = next((f.object for f in facts if f.predicate == "language"), None)
    if lang:
        procedure_scope = {"language": [lang]}

    matched_procedures = await find_applicable_procedures(
        pool, goal_embedding=query_vec, current_scope=procedure_scope, limit=1,
    )
    matched_procedure = matched_procedures[0] if matched_procedures else None
    if matched_procedure:
        steps_text = "\n".join(
            f"  {i+1}. {s.get('action', s)}" for i, s in enumerate(matched_procedure.get("steps") or [])
        )
        procedure_block = (
            f"Relevant learned procedure found (verified, {matched_procedure['verification_stats'].get('successes', 0)} "
            f"prior successes): {matched_procedure['name']}\n{steps_text}"
        )
        memory_block = f"{memory_block}\n\n{procedure_block}" if memory_block else procedure_block

    sandbox = RepoSandbox(repo_path)
    client = OpenAI(
        max_retries=0,
        api_key=settings.require("general_compute_api_key"),
        base_url=settings.general_compute_base_url,
    )
    agent = Agent(client, model, max_steps=max_steps)
    instance = {
        "instance_id": f"mcp_solve_task_{secrets.token_hex(6)}",
        "repo": os.path.basename(os.path.abspath(repo_path)),
        "problem_statement": task_description,
    }

    # Agent.run is SYNCHRONOUS and genuinely blocking (real retry/backoff
    # sleeps included, up to tens of seconds on a 429) -- run it in a
    # thread so it doesn't block the event loop everything else on this
    # server shares, same reasoning TasksExtension's own docstring gives
    # for why this tool needs task-augmentation in the first place.
    run_result = await asyncio.to_thread(agent.run, instance, sandbox, "mcp_solve_task", memory_block)

    # Real success proxy, reusing run_graph_experiment.py's own
    # invalid-run marker ("api_error") in the negative direction: the
    # agent explicitly signalled done AND actually produced a diff --
    # "finished" alone can mean "gave up cleanly", not "succeeded".
    if matched_procedure:
        success = run_result.stop_reason == "finished" and bool(run_result.patch)
        await record_execution_outcome(
            pool, procedure_row_id=str(matched_procedure["id"]), success=success,
            context_key=os.path.basename(os.path.abspath(repo_path)),
            steps_used=len(run_result.tool_calls),
        )

    lines = [
        f"stop_reason: {run_result.stop_reason}",
        f"tool_calls: {len(run_result.tool_calls)}",
        f"files_edited: {run_result.files_edited}",
        f"tokens: prompt={run_result.usage.prompt_tokens}, "
        f"completion={run_result.usage.completion_tokens}, "
        f"calls={run_result.usage.calls}",
        f"wall_seconds: {run_result.wall_seconds:.1f}",
    ]
    if run_result.error:
        lines.append(f"error: {run_result.error}")
    diff = run_result.patch
    lines.append("\n--- DIFF ---\n" + diff if diff else "\n(no changes made)")
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
