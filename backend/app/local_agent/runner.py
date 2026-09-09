"""
Phase 1 of the local/global runtime split (architecture audit +
imperative-twirling-plum.md): a thin local agent with real Agent+
RepoSandbox execution and ZERO database dependency -- it reaches the
global procedural-memory server as a real MCP client
(mcp.client.streamable_http + ClientSession, the exact code path proven
live this session in test_real_mcp_client_live.py) instead of touching
Postgres directly.

STRUCTURAL BOUNDARY, not just convention: this module must never import
asyncpg or app.db.session -- tests/test_local_agent_runner_offline.py
parses this file's own AST to prove it, not just grep the text.

Two seams are factored out as module-level functions specifically so the
offline contract test can swap them for fakes without needing a real
network, a real LLM, or a real sandbox:
  _open_client_session -- real MCP transport construction
  _run_local_node      -- real per-node Agent+RepoSandbox execution
Swapping both lets the offline test prove the CALL SEQUENCE (search ->
get -> execute -> report) and the no-match short-circuit, independent of
whether the real mechanisms underneath ever change.

UNIFIED LOCAL+GLOBAL RETRIEVAL (product spec Phase 1+2, wired in here):
when `repo_path` is a real, existing directory, `run()` checks that
workspace's own private `LocalProcedureStore` (app.local_agent.local_store)
ALONGSIDE the remote global corpus, via
app.local_agent.unified_retrieval.orchestrate_unified_search, before
falling through to the prior global-only call sequence for a fake/
nonexistent path (offline tests, a bare sandbox with no real workspace).
A `local`-sourced match never leaves this process: its outcome is
recorded into the SAME local store (record_local_execution_outcome), never
reported to the remote server -- Rule 6, no implicit private->global
promotion, evidence included. NOTE this module now transitively imports
`app.services.procedures` (for its real, shared verification-threshold
constants, not a second copy -- Rule 6) which itself imports `asyncpg` as
a library; that import never opens a connection or touches
`app.db.session`, so the structural "no database dependency" contract the
offline test below actually checks (direct imports of asyncpg/app.db.session
in THIS file) still holds -- but it's a real, worth-noting change in what
"zero database dependency" means for this module, from "doesn't even
import the driver" to "imports the driver as a library, never a live
connection."
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.execution.artifact_validation import gate_execution_success
from app.execution.behavioral_validation import (
    extract_behavioral_contract,
    gate_execution_success_with_behavior,
)
from app.execution.graph_executor import NodeResult, execute_task_graph
from app.execution.implementations import resolve_implementation
from app.execution.procedure_graph import steps_to_linear_nodes
from app.local_agent.local_learning import maybe_capture_local_candidate
from app.local_agent.local_store import LocalProcedureStore
from app.local_agent.unified_retrieval import orchestrate_unified_search
from app.models.plan import TaskGraph
from app.services.embeddings import Embedder
from app.services.environment_facts import (
    invariant_bindings_from_facts,
    probe_environment,
    probe_python_version,
)


@dataclass
class LocalRunResult:
    matched_procedure: Optional[dict]
    graph_outcome: str
    files_edited: list[str] = field(default_factory=list)
    combined_patch: str = ""
    node_notes: list[str] = field(default_factory=list)
    # "local" | "global" | "local_adhoc" | None (no match, no ad-hoc
    # attempted or captured) -- which store the executed procedure came
    # from. Added when unified local+global retrieval was wired in; a
    # caller ignoring this field (all pre-existing ones did) sees
    # unchanged behavior, since it's a trailing field with a default.
    source: Optional[str] = None
    # Phase 12 (personal learning loop): set when an ad-hoc (no-match)
    # run succeeded well enough to be captured as a new local candidate
    # procedure -- see local_learning.py::maybe_capture_local_candidate.
    # None on every other path (a match was used, or the ad-hoc run
    # didn't clear the real success bar).
    captured_candidate: Optional[dict] = None
    # Gate 3 (experiment instrumentation, trailing fields with defaults so
    # every pre-existing caller sees unchanged behavior):
    # retrieval_log -- the FULL ranked retrieval evidence (rank, name, ids,
    #   verification_state, source/scope) the retrieval layer actually
    #   produced, for experiment reporting. Ranking here is the
    #   deterministic policy key (verification/capability/freshness/
    #   specificity -- unified_retrieval.py); that layer exposes NO cosine
    #   similarity for its global ranking, so none is recorded here. Empty
    #   when retrieval was bypassed or never attempted.
    # metrics -- run-level wall-clock/metrics dict (timestamps, latency,
    #   steps, llm_calls, tokens, stop reason, failure info). Real values
    #   or None/"not_available" -- never invented.
    retrieval_log: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _retrieval_entry(procedure: Any, source: Optional[str], rank: int) -> dict:
    """One ranked retrieval result, exactly as the retrieval layer produced
    it -- only real fields the candidate/procedure dict actually carries,
    never fabricated ones."""
    if not isinstance(procedure, dict):
        return {"rank": rank, "source": source}
    return {
        "rank": rank,
        "name": procedure.get("name"),
        "id": procedure.get("id"),
        "procedure_id": procedure.get("procedure_id"),
        "version": procedure.get("version"),
        "verification_state": procedure.get("verification_state"),
        "source": source or procedure.get("source"),
    }


def _run_metrics(started: float, node_results: dict, node_notes: list,
                 extra: Optional[dict] = None) -> dict:
    """Aggregate real run-level metrics from node results + notes. Any
    metric this process genuinely lacks is None, never an estimate.
    stop_reason is parsed from the per-node note format _run_local_node
    emits (stop_reason=...) -- the real final agent stop reason."""
    prompt_tokens = sum(r.data.get("prompt_tokens") or 0 for r in node_results.values())
    completion_tokens = sum(r.data.get("completion_tokens") or 0 for r in node_results.values())
    llm_calls = sum(r.data.get("llm_calls") or 0 for r in node_results.values())
    tool_calls = sum(r.data.get("tool_calls") or 0 for r in node_results.values())
    stop_reason = None
    for note in reversed(node_notes):
        m = re.search(r"stop_reason=([^,]+)", note)
        if m:
            stop_reason = m.group(1)
            break
    failure_info = next(
        (n for n in node_notes if "VALIDATION FAILED" in n or n.startswith("REFUSED:")),
        None)
    metrics = {
        "started_at_unix": round(started, 3),
        "ended_at_unix": round(time.time(), 3),
        "latency_s": round(time.time() - started, 3),
        "steps": len(node_results),
        "llm_calls": llm_calls,
        "prompt_tokens": prompt_tokens if prompt_tokens else None,
        "completion_tokens": completion_tokens if completion_tokens else None,
        "total_tokens": (prompt_tokens + completion_tokens) if (prompt_tokens or completion_tokens) else None,
        "tool_calls": tool_calls,
        # The provider/model name is the caller's configured model string;
        # per-token cost information is NOT available from this stack, so
        # cost is explicitly "not_available" rather than estimated.
        "cost": "not_available",
        "stop_reason": stop_reason,
        "failure_info": failure_info,
    }
    if extra:
        metrics.update(extra)
    return metrics


# The MCP session is held open for this transport's entire lifetime,
# including while _run_local_node blocks synchronously (via
# asyncio.to_thread) running a real Agent+RepoSandbox loop against
# GENERAL_COMPUTE -- traffic that never touches this MCP connection at
# all. A short client-side timeout here times out the underlying
# streamable-http connection's own idle read, not any one MCP call, and
# the mcp package's internal TaskGroup (client/session.py,
# client/streamable_http.py) then raises an ExceptionGroup with BOTH its
# read-loop and write-loop sub-tasks failing together -- observed live:
# two real pilot trials on a task requiring more agent exploration each
# failed at wall_clock_seconds_total 60.01s/60.02s, matching the
# previous timeout=60 to the millisecond (see
# .scratch/final_agent_experiment/remediation-results.md). Set generously
# above the orchestrator's own outer per-trial wall-clock ceiling (600s,
# see .scratch/final_agent_experiment/protocol.md) so this transport-level
# timeout is never the thing that kills a trial early -- the outer,
# already-designed budget stays the real governing limit.
_MCP_SESSION_HTTP_TIMEOUT_SECONDS = 650


@asynccontextmanager
async def _open_client_session(server_url: str, token: str):
    """Real MCP transport -- the exact nested-context-manager shape proven
    live in test_real_mcp_client_live.py, factored into one seam so a
    test can swap the whole thing for a fake session with no real
    network at all."""
    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=_MCP_SESSION_HTTP_TIMEOUT_SECONDS,
    )
    async with streamable_http_client(server_url, http_client=http_client) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=_MCP_SESSION_HTTP_TIMEOUT_SECONDS,
        ) as session:
            yield session


def _resolve_git_head_sha(git_dir: str) -> Optional[str]:
    """Real HEAD commit SHA, read directly from `.git`'s own on-disk
    layout -- no `git` subprocess, matching this codebase's existing
    pure-filesystem-read discipline (app.services.environment_facts).
    Stable across an arbitrary number of clones/re-clones of the SAME
    commit, regardless of what any of them happen to be named on disk."""
    head_path = os.path.join(git_dir, "HEAD")
    if not os.path.isfile(head_path):
        return None
    try:
        content = open(head_path, encoding="utf-8").read().strip()
    except OSError:
        return None

    if not content.startswith("ref:"):
        # Detached HEAD: the file itself already names a real commit SHA.
        return content or None

    ref = content[len("ref:"):].strip()
    ref_path = os.path.join(git_dir, ref)
    if os.path.isfile(ref_path):
        try:
            sha = open(ref_path, encoding="utf-8").read().strip()
        except OSError:
            return None
        return sha or None

    # Loose ref file doesn't exist -- the branch may have been packed
    # (common right after a fresh clone). Fall back to packed-refs.
    packed_path = os.path.join(git_dir, "packed-refs")
    if not os.path.isfile(packed_path):
        return None
    try:
        with open(packed_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("^"):
                    continue
                sha, _, ref_name = line.partition(" ")
                if ref_name == ref:
                    return sha
    except OSError:
        return None
    return None


_GIT_REMOTE_ORIGIN_URL_RE = re.compile(
    r'\[remote\s+"origin"\][^\[]*?\burl\s*=\s*(\S+)', re.DOTALL,
)


def _read_git_remote_url(git_dir: str) -> Optional[str]:
    """Real configured `origin` remote URL, read straight from `.git/config`
    text -- a stable identity signal for "which real repository" that,
    unlike a HEAD SHA, survives even across genuinely different commits of
    the SAME repository. Purely advisory alongside the SHA below: absence
    (a local-only repo with no remote configured) is honest and handled,
    never fabricated."""
    config_path = os.path.join(git_dir, "config")
    if not os.path.isfile(config_path):
        return None
    try:
        content = open(config_path, encoding="utf-8").read()
    except OSError:
        return None
    match = _GIT_REMOTE_ORIGIN_URL_RE.search(content)
    return match.group(1) if match else None


def _git_repo_identity(repo_path: str) -> Optional[str]:
    """Real repository identity derived from git's own on-disk metadata,
    independent of the disposable filesystem path this checkout happens
    to live at -- `origin` remote URL (when configured) plus the real HEAD
    commit SHA. Returns None (never a fabricated identity) when
    `repo_path` carries no discoverable `.git` metadata at all -- the
    caller then honestly falls back to the folder name, the only signal
    left.

    THE REAL DEFECT THIS CLOSES: the prior context_key derived repo
    identity from `os.path.basename(repo_path)` alone -- a bare folder
    name under the CALLER's control, not the repository's own identity.
    Two disposable clones of the exact same repo+commit, checked out under
    two differently-named folders (or renamed between runs), previously
    produced two DIFFERENT context_keys despite being genuinely the same
    context -- letting a verification campaign manufacture fake
    ">=3 distinct contexts" by nothing more than renaming/re-cloning the
    same checkout. Conversely, two ACTUALLY different repositories that
    happened to be checked out under the same conventional folder name
    (e.g. both named "repo") previously collapsed into the same
    context_key. A commit SHA is real, content-derived, and cannot be
    gamed by renaming a directory; combined with the remote URL it also
    distinguishes two repos at a coincidentally-identical commit (a
    shared empty-init history, say)."""
    git_dir = os.path.join(os.path.abspath(repo_path), ".git")
    if not os.path.isdir(git_dir):
        return None
    head_sha = _resolve_git_head_sha(git_dir)
    if head_sha is None:
        return None
    remote_url = _read_git_remote_url(git_dir)
    return f"{remote_url or 'no-remote'}@{head_sha}"


def _local_context_key(repo_path: str, facts: list) -> str:
    """Meaningful context identity (product spec P0: 'Fix context
    identity'). A bare repo folder name collapses every run against the
    same checkout into ONE context regardless of which branch/dependency
    set was actually active -- so ten runs against the same repo on ten
    different days, with a package upgraded partway through, silently
    counted as ten repeats of the SAME context, undermining ticket 13's
    real ">=3 DISTINCT contexts" requirement.

    Real and deterministic, never randomized: real repository identity
    (see `_git_repo_identity` -- git remote+HEAD SHA when this is a real
    git checkout, honestly falling back to the folder name only when it
    is not) + a stable hash of every real fact `probe_environment`
    actually returned (language, package versions -- exactly what feeds
    invariant_bindings already) -- so the SAME repo checked out with the
    SAME dependency state always reduces to the SAME context_key (no
    artificial diversity, and no gaming it by renaming/re-cloning the same
    checkout), while a genuinely different environment (a branch with a
    different pandas pin, say) or a genuinely different repository
    produces a genuinely different one. Facts are sorted before hashing so
    key order never affects the result."""
    repo_identity = _git_repo_identity(repo_path) or os.path.basename(os.path.abspath(repo_path))
    fact_parts = sorted(f"{fact.predicate}={fact.object}" for fact in facts)
    if not fact_parts:
        return f"{repo_identity}:no-probed-facts"
    fact_hash = hashlib.sha256("|".join(fact_parts).encode("utf-8")).hexdigest()[:12]
    return f"{repo_identity}:{fact_hash}"


def _ensure_swebench_pro_on_path() -> None:
    """experiments/swebench_pro is a sibling of backend/, same sys.path
    pattern app/mcp_server/server.py already uses. Factored into its own
    function so both real call sites below share one implementation."""
    import sys
    from pathlib import Path
    experiments_swebench_pro = str(Path(__file__).resolve().parents[3] / "experiments" / "swebench_pro")
    if experiments_swebench_pro not in sys.path:
        sys.path.insert(0, experiments_swebench_pro)


async def _run_local_node(node, *, task_description: str, repo_path: str,
                           model: str, max_steps: int,
                           node_notes: list[str]) -> NodeResult:
    """One real step, one real Agent+RepoSandbox tool-calling turn against
    the SAME repo path -- the exact mechanism proven live in
    test_graph_executor_coding_live.py and find_best_way's tier-2, moved
    to the client side. Returns real per-node data (files_edited, patch)
    in NodeResult.data so the caller can aggregate without needing to
    know this function's own internals -- what makes this seam swappable
    for a fake in the offline test.

    Real sandbox/client construction happens HERE, not in the caller --
    the offline test's own first run caught the real bug in constructing
    them eagerly in run(): a fake _run_local_node would still have paid
    for a real OpenAI client requiring a real API key it never needed.
    Constructing per-node is cheap (both are lightweight objects) and
    keeps every requirement for "a fake run_node needs zero real
    dependencies" true structurally, not by discipline.

    REAL GAP CLOSED (audit pass): every other real repo_path-accepting
    call site in this codebase (server.py's find_best_way and
    reproduce_procedure) refuses upfront with os.path.isdir(repo_path)
    before doing anything else -- this was the one real execution
    boundary that didn't. RepoSandbox.__init__ (experiments/swebench_pro/
    agent.py) never validates root exists; without this check, a
    nonexistent repo_path silently reached a real, billed OpenAI call
    (Agent.run()) that could only ever fail deep inside the tool-calling
    loop with a confusing raw OS error, rather than refusing cleanly and
    for free. Not a path-traversal issue (RepoSandbox._resolve already
    guards that regardless of whether root exists) -- a wasted-real-work
    /honest-refusal gap, closed the same way the other call sites already
    close it. run()'s own top-level orchestration deliberately still does
    NOT gate on this (see run()'s own os.path.isdir use for the LOCAL
    STORE decision only) -- this function, where RepoSandbox/Agent/OpenAI
    are actually constructed, is the correct, single real boundary.
    """
    import asyncio

    if not os.path.isdir(repo_path):
        note = f"REFUSED: repo_path {repo_path!r} is not a directory on this machine."
        node_notes.append(note)
        return NodeResult(status="failure", notes=note)

    _ensure_swebench_pro_on_path()
    from agent import Agent, RepoSandbox
    from openai import OpenAI

    sandbox = RepoSandbox(repo_path)
    client = OpenAI(
        max_retries=0,
        api_key=os.environ["GENERAL_COMPUTE_API_KEY"],
        base_url=os.environ.get("GENERAL_COMPUTE_BASE_URL", "https://api.generalcompute.com/v1"),
    )

    prior_context = ("\n\nPrior steps completed:\n" + "\n".join(node_notes)) if node_notes else ""
    instance = {
        "instance_id": f"local_agent_{secrets.token_hex(6)}_step{node.order}",
        "repo": os.path.basename(os.path.abspath(repo_path)),
        "problem_statement": f"{task_description}\n\nCurrent step: {node.goal}",
    }

    node_agent = Agent(client, model, max_steps=max_steps)
    run_result = await asyncio.to_thread(
        node_agent.run, instance, sandbox, "local_agent", prior_context,
    )
    note = f"step {node.order} ({node.goal}): stop_reason={run_result.stop_reason}, tool_calls={len(run_result.tool_calls)}"
    node_notes.append(note)
    succeeded = run_result.stop_reason == "finished"
    # Gate 3 (experiment instrumentation): surface the Agent's real token
    # accounting (experiments/swebench_pro/agent.py Usage) alongside the
    # existing tool-call count. Real numbers from the real provider usage
    # object, or 0 when the provider did not report them -- never estimates.
    return NodeResult(
        status="success" if succeeded else "failure",
        notes=note,
        data={"files_edited": run_result.files_edited, "patch": run_result.patch,
              "tool_calls": len(run_result.tool_calls),
              "prompt_tokens": run_result.usage.prompt_tokens,
              "completion_tokens": run_result.usage.completion_tokens,
              "llm_calls": run_result.usage.calls,
              # Diagnostic pass-through, never used for success/failure
              # (that is decided above, purely from stop_reason). The tool
              # NAMES and the model's final prose were both discarded here
              # -- only their count survived -- so a run that ended with
              # stop_reason="no_tool_call" and touched no files could not
              # be diagnosed afterwards: whether it ever attempted to write
              # the file it was asked for, and what it said instead, were
              # simply not recorded anywhere.
              "tool_names": list(run_result.tool_calls),
              "final_message": run_result.final_message,
              # Surfaced so a caller (e.g. an experiment orchestrator
              # scoring/recording a trial) can see whether this run needed
              # provider-side error recovery at all -- was previously a
              # purely internal Agent.run() loop variable with no way out.
              # Never changes success/failure or usage accounting: a
              # recovered run's tokens/steps already include every real
              # attempted call, this is visibility only.
              "recoveries": run_result.recoveries},
    )


class LocalAgentRunner:
    """local task -> remote search_procedures -> remote check_applicability
    -> procedure -> local execution graph -> Agent+RepoSandbox ->
    local outcome/evidence -> remote report_execution.

    Reuses execute_task_graph/steps_to_linear_nodes unchanged -- the same
    real scheduler and conversion the server-side execution already uses;
    this class only supplies a different (local, DB-free) run_node and a
    different (remote-MCP) source of truth for which procedure to run.
    """

    def __init__(self, server_url: str, token: str, *,
                 model: str = "gemma-4-31B-it", max_steps: int = 8):
        self.server_url = server_url
        self.token = token
        self.model = model
        self.max_steps = max_steps

    async def _execute_steps(
        self, steps: list[dict], *, task_description: str, repo_path: str,
    ) -> tuple[list[str], dict[int, NodeResult], Any]:
        """Real per-step Agent+RepoSandbox execution over `steps` -- the
        SAME machinery for a matched procedure's real steps or a single
        ad-hoc step, factored out so both paths in `run()` share one real
        implementation rather than two copies. Returns (node_notes,
        node_results, graph_result)."""
        nodes = steps_to_linear_nodes(steps)
        graph = TaskGraph(execution_plan_id=uuid4(), graph_hash="local-agent-run", nodes=nodes)

        node_notes: list[str] = []
        node_results: dict[int, NodeResult] = {}

        async def run_node(node) -> NodeResult:
            # P1 (product spec: "Complete implementation abstraction"):
            # before spending a real Agent+RepoSandbox run on this node,
            # ask the real registry whether the node's own declared
            # implementation_hint is one this process can actually
            # satisfy. A hint-less node (every stored procedure today)
            # resolves to "frontier", which IS what _run_local_node does
            # -- so this changes nothing for any node writing this pass
            # found in the real corpus. A node that names ONLY an
            # unimplemented kind (e.g. "slm") gets an honest, explicit
            # failure -- never a silent no-op, and never a silent
            # frontier run pretending to be something else.
            resolution = resolve_implementation(node.implementation_hint)
            if not resolution.supported:
                note = (
                    f"step {node.order} ({node.goal}): implementation kind "
                    f"{resolution.kind!r} not yet implemented -- {resolution.reason}"
                )
                node_notes.append(note)
                result = NodeResult(status="failure", notes=note)
                node_results[node.order] = result
                return result

            result = await _run_local_node(
                node, task_description=task_description, repo_path=repo_path,
                model=self.model, max_steps=self.max_steps, node_notes=node_notes,
            )
            node_results[node.order] = result
            return result

        graph_result = await execute_task_graph(graph, run_node=run_node)
        return node_notes, node_results, graph_result

    async def run(self, task_description: str, repo_path: str, *,
                   allow_unverified: bool = False,
                   experimental_no_retrieval: bool = False) -> LocalRunResult:
        """allow_unverified: default False -- production default is
        verified + approved procedures only (require_verified=True on the
        remote search_procedures call), matching find_best_way's own
        already-correct allow_unverified_procedures=False default
        server-side (app/mcp_server/server.py). Pass True only for
        explicit development/experimentation use, never as a silent
        default -- a `candidate` (unverified/unapproved) procedure must
        never be selected for real execution unless the caller opted in
        by name.

        experimental_no_retrieval: EXPERIMENTAL-ONLY (Gate 3 A/B arm A),
        default False. When True, StealthLab procedure retrieval is
        bypassed ENTIRELY -- no local search, no remote search_procedures
        call, no match -- and the task routes straight into the EXISTING
        ad-hoc execution machinery below (the same single-step path a
        genuine no-match takes; nothing is duplicated, and no retrieval
        result is fabricated: matched_procedure is None and
        retrieval_log is empty). This must never be enabled by a
        production caller; it exists solely so an A/B experiment's
        no-retrieval arm is a deliberate, explicit bypass rather than
        depending on "no procedure happened to match". With the default
        False, behavior is byte-for-byte the pre-Gate-3 path."""
        run_started = time.time()
        async with _open_client_session(self.server_url, self.token) as session:
            await session.initialize()

            # Phase 3: probe the LOCAL repo (never sent to the remote
            # server as raw filesystem data, only as derived numeric
            # bindings) so a remote procedure's real invariant (e.g.
            # "pandas_version >= 2.0") can be evaluated against this
            # repo's actual pinned versions -- the same real mechanism
            # find_best_way's own repo_path path uses server-side,
            # applied here because THIS is the process with a real repo
            # to probe. Pure, synchronous, off the event loop.
            local_facts = await asyncio.to_thread(probe_environment, repo_path)
            # V1: local hard-constraint preconditions (checked below via
            # check_local_hard_constraints, reached through
            # orchestrate_unified_search) resolve against these SAME
            # probed facts -- UNKNOWN != TRUE, so a precondition this
            # process cannot resolve locally rejects automatic selection
            # rather than passing silently. python_version is included
            # even though probe_environment() itself is manifest-derived
            # only, because "which interpreter is actually running this
            # process" is real, always-available local evidence a
            # precondition can legitimately name.
            local_facts = local_facts + [probe_python_version()]
            invariant_bindings = invariant_bindings_from_facts(local_facts)

            # Phase 1+2 (memory-substrate map): check the workspace's own
            # private procedure library ALONGSIDE the remote global corpus,
            # ranked by one real policy (unified_retrieval.py) -- rather
            # than always going straight to global. Real, existing
            # directory only: a fake/nonexistent repo_path (offline tests,
            # a bare CI sandbox with no real workspace) skips the local
            # store entirely and falls back to the prior global-only
            # behavior verbatim, rather than trying to create a store file
            # under a path that was never a real workspace.
            store = LocalProcedureStore(repo_path) if os.path.isdir(repo_path) else None
            # Gate 3: full ranked retrieval evidence, surfaced verbatim from
            # whatever the retrieval layer below actually produced (rank,
            # name, ids, verification_state, source). Empty when retrieval
            # is bypassed. No similarity numbers -- this layer's global
            # ranking is the deterministic policy key, not a float score.
            retrieval_log: list[dict] = []
            matched, source = None, None
            no_retrieval_note: str | None = None
            if experimental_no_retrieval:
                # EXPERIMENTAL arm-A bypass (see docstring): skip retrieval
                # entirely; matched stays None so control falls into the
                # EXISTING ad-hoc machinery below -- no duplicated logic,
                # nothing fabricated. (node_notes does not exist yet at this
                # point -- it is created by _execute_steps below -- so the
                # marker is appended there.)
                no_retrieval_note = (
                    "EXPERIMENTAL NO-RETRIEVAL ARM: StealthLab procedure "
                    "retrieval bypassed by explicit caller request.")
            elif store is not None:
                # REAL GAP CLOSED: orchestrate_unified_search's
                # query_embedding param has existed since Phase 1+2, and
                # LocalProcedureStore.search_local_procedures() only ranks
                # by real cosine similarity when one is supplied -- this
                # call site never supplied it, so every local search was
                # silently lexical-only, substring-matching the task
                # description against stored name/goal text no matter how
                # differently a real procedure's own wording described the
                # same capability. One real embedding call (the same
                # Embedder every other real caller in this codebase uses,
                # no second provider) now makes semantic ranking real here
                # too, matching what the store has supported since it was
                # built.
                query_embedding = await Embedder().embed_one(task_description, input_type="query")
                ranked = await orchestrate_unified_search(
                    session, store,
                    task_description=task_description,
                    invariant_bindings=invariant_bindings,
                    environment_facts=local_facts,
                    require_verified=not allow_unverified,
                    limit=3,
                    query_embedding=query_embedding,
                )
                matched, source = (ranked[0].procedure, ranked[0].source) if ranked else (None, None)
                retrieval_log = [
                    _retrieval_entry(r.procedure, r.source, i)
                    for i, r in enumerate(ranked)
                ]
            else:
                search_result = await session.call_tool(
                    "search_procedures",
                    {
                        "task": task_description, "require_verified": not allow_unverified,
                        "limit": 3, "invariant_bindings": json.dumps(invariant_bindings),
                    },
                )
                matches = json.loads(search_result.content[0].text)
                matched, source = (matches[0], "global") if matches else (None, None)
                retrieval_log = [
                    _retrieval_entry(m, "global", i) for i, m in enumerate(matches)
                ]

            # Phase 12 (personal learning loop): nothing matched, local or
            # global -- rather than giving up (the prior behavior), run
            # the task ad-hoc as a single real step, mirroring find_best_
            # way's own server-side ad-hoc-run precedent. A real success
            # becomes a new local candidate procedure (never global --
            # Rule 6), so future similar tasks in this workspace have
            # something to match against.
            if matched is None:
                if store is None:
                    return LocalRunResult(
                        matched_procedure=None, graph_outcome="no_match",
                        retrieval_log=retrieval_log,
                        metrics=_run_metrics(
                            run_started, {}, [],
                            extra={"retrieval_attempted": not experimental_no_retrieval},
                        ),
                    )
                steps = [{"order": 0, "goal": task_description}]
                node_notes, node_results, graph_result = await self._execute_steps(
                    steps, task_description=task_description, repo_path=repo_path,
                )
                if no_retrieval_note is not None:
                    node_notes.insert(0, no_retrieval_note)
                all_files_edited = sorted({
                    f for r in node_results.values() for f in r.data.get("files_edited", [])
                })
                combined_patch = "\n".join(
                    r.data["patch"] for r in node_results.values() if r.data.get("patch")
                )
                run_succeeded = graph_result.outcome == "success" and bool(combined_patch)
                # REAL GAP CLOSED (verification success criterion): a
                # declared success from the raw agent mechanism alone (a
                # "finished" stop_reason plus a non-empty patch) is not
                # evidence the produced artifact actually works -- see
                # app.execution.artifact_validation's module docstring for
                # the real D1 incident (an unimportable module accepted as
                # success) this closes. Never overturns a declared failure;
                # only tightens a declared success against the strongest
                # deterministic check this process actually has for each
                # edited file's real artifact type.
                run_succeeded, validation_failure_reason = gate_execution_success(
                    repo_root=repo_path, declared_success=run_succeeded,
                    files_edited=all_files_edited,
                )
                if validation_failure_reason is not None:
                    node_notes.append(f"ARTIFACT VALIDATION FAILED: {validation_failure_reason}")
                # REAL GAP CLOSED: an ad-hoc-captured candidate previously
                # got no embedding at all (local_learning.py never computed
                # or accepted one), so it was only ever findable by lexical
                # substring match against its own name/goal text -- a later
                # task worded differently could never match it even though
                # search_local_procedures has supported real cosine-
                # similarity ranking since Phase 1+2. Same real Embedder
                # this function already uses for its own search step above,
                # storage-time convention (input_type="document"), matching
                # submit_procedure's own real-embedding-at-storage precedent
                # (app/mcp_server/server.py::submit_procedure).
                capture_embedding = await Embedder().embed_one(task_description, input_type="document")
                captured = maybe_capture_local_candidate(
                    store, task_description=task_description, node_notes=node_notes,
                    files_edited=all_files_edited, combined_patch=combined_patch,
                    run_succeeded=run_succeeded, repo_root=repo_path,
                    node_results=node_results, environment_facts=local_facts,
                    embedding=capture_embedding,
                )
                # REAL GAP CLOSED: the run that JUST succeeded and produced
                # this candidate is the run's own first real evidence --
                # leaving it uncounted meant every freshly captured
                # candidate started at attempts=0 despite one genuine,
                # already-known-successful execution existing for it. This
                # is the SAME record_local_execution_outcome real reuse
                # already calls for a MATCHED local procedure below, applied
                # here to the procedure's own originating run. Uses the same
                # meaningful context_key this module now derives (see
                # _local_context_key) rather than a bare repo folder name.
                if captured is not None:
                    adhoc_tool_calls = sum(r.data.get("tool_calls", 0) for r in node_results.values())
                    store.record_local_execution_outcome(
                        row_id=captured["id"], success=True,
                        context_key=_local_context_key(repo_path, local_facts),
                        steps_used=adhoc_tool_calls,
                    )
                return LocalRunResult(
                    matched_procedure=None,
                    graph_outcome=graph_result.outcome,
                    files_edited=all_files_edited,
                    combined_patch=combined_patch,
                    node_notes=node_notes,
                    source="local_adhoc" if captured else None,
                    captured_candidate=captured,
                    retrieval_log=retrieval_log,
                    metrics=_run_metrics(
                        run_started, node_results, node_notes,
                        extra={"retrieval_attempted": not experimental_no_retrieval},
                    ),
                )

            if source == "local":
                # Already has full steps/etc from LocalProcedureStore --
                # no remote round trip needed, and nothing about this
                # workspace's private procedure is ever sent out.
                procedure = matched
            else:
                proc_result = await session.call_tool(
                    "get_procedure", {"procedure_id": matched["procedure_id"]},
                )
                procedure = json.loads(proc_result.content[0].text)

            steps = procedure.get("steps") or [{"order": 0, "goal": task_description}]
            node_notes, node_results, graph_result = await self._execute_steps(
                steps, task_description=task_description, repo_path=repo_path,
            )

            all_files_edited = sorted({
                f for r in node_results.values() for f in r.data.get("files_edited", [])
            })
            combined_patch = "\n".join(
                r.data["patch"] for r in node_results.values() if r.data.get("patch")
            )
            total_tool_calls = sum(r.data.get("tool_calls", 0) for r in node_results.values())
            run_succeeded = graph_result.outcome == "success" and bool(combined_patch)
            # REAL GAP CLOSED (verification success criterion): see the
            # matching comment in the ad-hoc branch above and
            # app.execution.artifact_validation's module docstring -- a
            # declared success is never itself evidence the produced
            # artifact actually works, only the strongest deterministic
            # check available for its real artifact type is.
            #
            # Gate 2B: when the matched procedure DECLARES a behavioral
            # contract (domain_payload.behavioral_contract), artifact
            # validation alone is still not enough -- the composed gate
            # (app.execution.behavioral_validation) additionally runs the
            # deterministic verifier registered for the contract's
            # capability kind. Procedures without a contract keep the
            # exact previous artifact-only behavior.
            run_succeeded, validation_failure_reason = gate_execution_success_with_behavior(
                repo_root=repo_path, declared_success=run_succeeded,
                files_edited=all_files_edited,
                behavioral_contract=extract_behavioral_contract(procedure),
            )
            if validation_failure_reason is not None:
                node_notes.append(
                    f"POST-EXECUTION VALIDATION FAILED: {validation_failure_reason}")
            # REAL GAP CLOSED: a bare repo folder name collapses every run
            # against the same checkout into ONE context regardless of
            # which branch/dependency set was actually active -- see
            # _local_context_key.
            context_key = _local_context_key(repo_path, local_facts)

            if source == "local":
                # Stays entirely in this process -- a local procedure's
                # outcome is never reported to the remote server (Rule 6:
                # no implicit private -> global promotion, evidence
                # included).
                store.record_local_execution_outcome(
                    row_id=matched["id"], success=run_succeeded,
                    context_key=context_key, steps_used=total_tool_calls,
                )
            else:
                await session.call_tool("report_execution", {
                    "procedure_id": matched["procedure_id"],
                    "success": run_succeeded,
                    "context_key": context_key,
                    "steps_used": total_tool_calls,
                })

            return LocalRunResult(
                matched_procedure=matched,
                graph_outcome=graph_result.outcome,
                files_edited=all_files_edited,
                combined_patch=combined_patch,
                node_notes=node_notes,
                source=source,
                retrieval_log=retrieval_log,
                metrics=_run_metrics(
                    run_started, node_results, node_notes,
                    extra={"retrieval_attempted": True},
                ),
            )
