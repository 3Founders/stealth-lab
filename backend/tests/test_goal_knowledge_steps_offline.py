"""find_ways renders each step of a way for the agent. Ingested ways (app/ingest/verified) store steps as
{"do", "role", "check"}; older and extracted ways use {"goal" | "description" | "action", "expected_outcome"}.
Both shapes must reach the agent with their text and check -- the ingested shape used to come back as null."""
from __future__ import annotations

from app.execution import goal_knowledge as gk
from app.execution.goal_resolution import ResolvedGoalNode


def _tree(steps):
    proc = {"procedure_id": "P1", "id": "V1", "name": "way", "version": 1, "steps": steps}
    return ResolvedGoalNode(goal_id="G1", goal_name="goal", depth=0, chosen="procedure", procedure=proc,
                            children=[])


def _steps(steps):
    return gk.goal_tree_to_knowledge(_tree(steps))["procedures"][0]["steps"]


def test_ingested_steps_keep_their_text_role_and_check():
    out = _steps([{"order": 0, "kind": "action", "do": "Find where the path is built", "role": "plan"},
                  {"order": 1, "kind": "action", "do": "Run the failing test", "role": "verify",
                   "check": "pytest t.py::test_a"}])
    assert [s["do"] for s in out] == ["Find where the path is built", "Run the failing test"]
    assert [s["role"] for s in out] == ["plan", "verify"]
    assert out[0]["check"] is None and out[1]["check"] == "pytest t.py::test_a"


def test_older_step_shapes_are_unchanged():
    out = _steps([{"order": 0, "goal": "Install deps", "expected_outcome": "npm ls exits 0"},
                  {"order": 1, "action": "Run build"}])
    assert [s["do"] for s in out] == ["Install deps", "Run build"]
    assert out[0]["check"] == "npm ls exits 0" and "role" not in out[0]


def test_a_tool_step_carries_its_locator_and_the_way_its_rank():
    # The web-search Goal's Way: one step, bound to Exa, with the server's own endpoint (2026-11-14).
    loc = {"kind": "mcp", "server": "ai.exa/exa", "url": "https://mcp.exa.ai/mcp"}
    tree = gk.goal_tree_to_knowledge(_tree([{
        "order": 1, "title": "Search the web with Exa",
        "binding": {"kind": "tool", "tool": "web_search_exa", "server_url": "https://mcp.exa.ai/mcp", "locator": loc},
    }]))
    proc = tree["procedures"][0]
    assert proc["rank"] == 1
    assert proc["steps"][0]["kind"] == "action"
    assert proc["steps"][0]["locator"] == loc


def test_a_binding_without_an_explicit_locator_uses_its_server_url():
    out = _steps([{"order": 0, "binding": {"kind": "tool", "tool": "t", "server_url": "https://x.example/mcp"}}])
    assert out[0]["locator"]["url"] == "https://x.example/mcp"
    assert out[0]["locator"]["kind"] == "tool"
