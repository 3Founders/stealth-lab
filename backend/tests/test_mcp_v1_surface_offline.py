"""
MCP v1 surface (final_thing.md): seven tools (find_ways, report_discovery,
submit_way, plus the model flow's recommend_models, report_model_run, report_result and call_model),
the three related-claims resources, and the two client-side
prompts (survey_repo, plan_and_run). The older v2
tools were removed; the surface is probed in a fresh subprocess.
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
    "static_resources": sorted(str(k) for k in (getattr(rm, "_resources", None) or {}).keys()),
    "prompts": sorted(p.name for p in srv.server._prompt_manager.list_prompts()),
}))
"""


def _surface(value: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=180, check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_v1_exposes_exactly_five_tools_three_claim_resources_and_two_prompts():
    s = _surface("v1")
    assert s["tools"] == ["call_model", "find_ways", "recommend_models", "report_discovery", "report_model_run", "report_result",
                         "submit_way"]
    assert s["resources"] == [
        "stealth://claims/{claim_id}",
        "stealth://goals/{goal_id}/claims",
        "stealth://procedures/{procedure_id}/claims",
    ]
    assert s["prompts"] == ["plan_and_run", "survey_repo"]
    # The same two prompts as readable resources, for clients that don't surface MCP prompts, and the
    # .stealth library format (library.md / routing.md grammar and the commands that keep them).
    assert s["static_resources"] == ["stealth://formats/library", "stealth://prompts/plan_and_run",
                                     "stealth://prompts/survey_repo"]


def test_the_v2_tools_are_gone():
    for name in ("find_best_way", "search_procedures", "init_workspace", "report_execution", "create_goal"):
        assert not hasattr(srv, name), name
    assert not hasattr(srv, "MCP_SURFACE") and not hasattr(srv, "surface_includes")
