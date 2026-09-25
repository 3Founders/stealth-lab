"""
MCP v1 surface (final_thing.md): exactly three tools (find_ways,
report_discovery, submit_way), the three related-claims resources, and the two client-side
prompts (survey_repo, plan_and_run). The rest
of the suite runs with STEALTHLAB_MCP_SURFACE=v2 (conftest) because tool
registration happens once at import -- so the real v1 surface is imported in a
fresh subprocess here, not in this process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import app.mcp_server.server as srv

BACKEND = Path(__file__).resolve().parents[1]

_PROBE = r"""
import json
import app.mcp_server.server as srv
rm = srv.server._resource_manager
tmpl = getattr(rm, "_templates", None) or getattr(rm, "templates", None) or {}
print(json.dumps({
    "tools": sorted(t.name for t in srv.server._tool_manager.list_tools()),
    "resources": sorted(tmpl.keys()),
    "prompts": sorted(p.name for p in srv.server._prompt_manager.list_prompts()),
}))
"""


def _surface(value: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["STEALTHLAB_MCP_SURFACE"] = value
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=180, check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_v1_exposes_exactly_three_tools_three_claim_resources_and_two_prompts():
    s = _surface("v1")
    assert s["tools"] == ["find_ways", "report_discovery", "submit_way"]
    assert s["resources"] == [
        "stealth://claims/{claim_id}",
        "stealth://goals/{goal_id}/claims",
        "stealth://procedures/{procedure_id}/claims",
    ]
    assert s["prompts"] == ["plan_and_run", "survey_repo"]


def test_v2_still_exposes_the_legacy_surface():
    s = _surface("v2")
    assert {"find_ways", "report_discovery", "find_best_way", "search_goals"} <= set(s["tools"])
    assert "stealth://runs/{run_id}" in s["resources"]
    assert {"solve_with_stealth", "survey_repo", "plan_and_run"} <= set(s["prompts"])


def test_surface_includes_pure_rule():
    assert srv.surface_includes("find_ways", "v1")
    assert srv.surface_includes("report_discovery", "v1")
    assert not srv.surface_includes("find_best_way", "v1")
    assert srv.surface_includes("find_best_way", "v2")


def test_v2_tools_stay_callable_in_process_even_when_unregistered():
    # v2 means "not exposed over MCP", not "deleted": the plain functions remain.
    assert callable(srv.find_best_way) and callable(srv.search_goals)
