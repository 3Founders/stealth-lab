"""
Offline tests for the MCP prompts (backend/app/mcp_server/prompts.py): survey_repo and plan_and_run.

They prove the two prompts are the only ones registered, render through the SDK, name only real tools,
never direct unattended execution, state the claims.md grammar the parser reads, and are readable as
resources with the identical text (for clients that don't surface MCP prompts).
"""
from __future__ import annotations

import asyncio
import re

import pytest

import app.mcp_server.prompts as prm
import app.mcp_server.server as srv

_TOOLS = {"find_ways", "submit_way", "report_discovery", "recommend_models", "report_model_run"}
_GONE = ("find_best_way", "search_procedures", "init_workspace", "commit_local_sync", "inspect_run",
         "report_execution", "solve_task", "apply_change_set")
_AUTONOMY_SMELLS = ("without asking", "automatically execute and commit", "do not ask the user",
                    "commit without review", "act unattended")


def _run(coro):
    return asyncio.run(coro)


def test_only_the_two_prompts_are_registered_with_descriptions():
    prompts = srv.server._prompt_manager.list_prompts()
    assert sorted(p.name for p in prompts) == ["plan_and_run", "survey_repo"]
    assert all((p.description or "").strip() for p in prompts)


@pytest.mark.parametrize("name,args", [("survey_repo", {"repo_path": "/r"}), ("plan_and_run", {"task": "add docx"})])
def test_prompt_renders_through_sdk(name, args):
    result = _run(srv.server.get_prompt(name, args))
    msg = result.messages[0]
    text = msg.content.text if hasattr(msg.content, "text") else str(msg.content)
    assert len(text) > 100


def test_get_prompt_unknown_name_raises():
    with pytest.raises(Exception):
        _run(srv.server.get_prompt("no_such_prompt", {}))


def test_prompts_name_only_real_tools_and_never_direct_unattended_work():
    for body in (prm.survey_repo(), prm.plan_and_run("add a docx export")):
        for gone in _GONE:
            assert gone not in body, f"prompt names a removed tool {gone!r}"
        for smell in _AUTONOMY_SMELLS:
            assert smell not in body.lower()
    body = prm.plan_and_run("x")
    assert {"find_ways", "report_discovery"} <= {t for t in _TOOLS if t in body}


def test_survey_repo_states_the_claims_grammar_the_parser_reads():
    from app.execution.repo_facts import parse_repo_claims

    example = re.search(r"e\.g\. `(CLAIM\|[^`]+)`", prm.survey_repo("/repo")).group(1)
    claims, _ = parse_repo_claims(example)
    assert claims == [{"claim_id": "R-001", "statement": "Node 20.11", "topic": "runtime",
                       "scope": "repository", "source": ".nvmrc:1#sha=9f2c1ab"}]


def test_plan_and_run_has_the_tiny_packet_and_node_format():
    body = prm.plan_and_run("x")
    assert 'Do node N-3. Read: rg "N-3" .stealth/run.md' in body
    assert prm.RUN_MD_FORMAT in body
    assert "survey_repo" in body  # facts first


def test_prompts_are_also_readable_as_resources_with_the_same_text():
    from app.mcp_server import resources as res

    assert _run(res.survey_repo_prompt_resource()) == prm.survey_repo()
    assert _run(res.plan_and_run_prompt_resource()) == prm.plan_and_run("<your task>")
    uris = [r[0] for r in res._V1_RESOURCES]
    assert "stealth://prompts/survey_repo" in uris and "stealth://prompts/plan_and_run" in uris
