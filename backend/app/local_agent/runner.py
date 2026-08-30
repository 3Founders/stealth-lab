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
"""
from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.execution.graph_executor import NodeResult, execute_task_graph
from app.execution.procedure_graph import steps_to_linear_nodes
from app.models.plan import TaskGraph


@dataclass
class LocalRunResult:
    matched_procedure: Optional[dict]
    graph_outcome: str
    files_edited: list[str] = field(default_factory=list)
    combined_patch: str = ""
    node_notes: list[str] = field(default_factory=list)


@asynccontextmanager
async def _open_client_session(server_url: str, token: str):
    """Real MCP transport -- the exact nested-context-manager shape proven
    live in test_real_mcp_client_live.py, factored into one seam so a
    test can swap the whole thing for a fake session with no real
    network at all."""
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)
    async with streamable_http_client(server_url, http_client=http_client) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=60) as session:
            yield session


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
    """
    import asyncio

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
    return NodeResult(
        status="success" if succeeded else "failure",
        notes=note,
        data={"files_edited": run_result.files_edited, "patch": run_result.patch,
              "tool_calls": len(run_result.tool_calls)},
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

    async def run(self, task_description: str, repo_path: str, *,
                   allow_unverified: bool = True) -> LocalRunResult:
        async with _open_client_session(self.server_url, self.token) as session:
            await session.initialize()

            search_result = await session.call_tool(
                "search_procedures",
                {"task": task_description, "require_verified": not allow_unverified, "limit": 3},
            )
            matches = json.loads(search_result.content[0].text)
            if not matches:
                return LocalRunResult(matched_procedure=None, graph_outcome="no_match")
            matched = matches[0]

            proc_result = await session.call_tool(
                "get_procedure", {"procedure_id": matched["procedure_id"]},
            )
            procedure = json.loads(proc_result.content[0].text)

            steps = procedure.get("steps") or [{"order": 0, "goal": task_description}]
            nodes = steps_to_linear_nodes(steps)
            graph = TaskGraph(execution_plan_id=uuid4(), graph_hash="local-agent-run", nodes=nodes)

            node_notes: list[str] = []
            node_results: dict[int, NodeResult] = {}

            async def run_node(node) -> NodeResult:
                result = await _run_local_node(
                    node, task_description=task_description, repo_path=repo_path,
                    model=self.model, max_steps=self.max_steps, node_notes=node_notes,
                )
                node_results[node.order] = result
                return result

            graph_result = await execute_task_graph(graph, run_node=run_node)

            all_files_edited = sorted({
                f for r in node_results.values() for f in r.data.get("files_edited", [])
            })
            combined_patch = "\n".join(
                r.data["patch"] for r in node_results.values() if r.data.get("patch")
            )
            total_tool_calls = sum(r.data.get("tool_calls", 0) for r in node_results.values())
            run_succeeded = graph_result.outcome == "success" and bool(combined_patch)

            await session.call_tool("report_execution", {
                "procedure_id": matched["procedure_id"],
                "success": run_succeeded,
                "context_key": os.path.basename(os.path.abspath(repo_path)),
                "steps_used": total_tool_calls,
            })

            return LocalRunResult(
                matched_procedure=matched,
                graph_outcome=graph_result.outcome,
                files_edited=all_files_edited,
                combined_patch=combined_patch,
                node_notes=node_notes,
            )
