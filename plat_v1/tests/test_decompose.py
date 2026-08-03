"""
Plan parsing.

The requirement: malformed model output becomes a failed proposal, not an
exception. An operator should see a rejected plan with a reason attached, not
a 500 and a stack trace in the log.
"""
from __future__ import annotations

import json


from app.services.decompose import (
    PLAN_SCHEMA,
    DecompositionService,
    plan_from_payload,
)
from app.services.typecheck import typecheck
from tests.helpers import FakeAnthropic


GOOD_PAYLOAD = {
    "feasible": True,
    "reasoning": "Two steps: find the tables, then read them.",
    "nodes": [
        {
            "ref": "n1",
            "name": "find_tables",
            "description": "locate tables",
            "existing_task_id": None,
            "inputs": [{"name": "pdf_path", "type": "string", "item_type": "none",
                        "description": "the pdf"}],
            "outputs": [{"name": "regions", "type": "array", "item_type": "object",
                         "description": "table regions"}],
        },
        {
            "ref": "n2",
            "name": "read_tables",
            "description": "read cells",
            "existing_task_id": None,
            "inputs": [{"name": "regions", "type": "array", "item_type": "object",
                        "description": "table regions"}],
            "outputs": [{"name": "grid", "type": "array", "item_type": "object",
                         "description": "cells"}],
        },
    ],
    "edges": [{"type": "PRODUCES", "source_ref": "n1", "target_ref": "n2"}],
    "external_inputs": ["pdf_path"],
}


def test_a_well_formed_payload_becomes_a_plan_that_typechecks():
    plan = plan_from_payload(GOOD_PAYLOAD)
    assert [n.ref for n in plan.nodes] == ["n1", "n2"]
    assert plan.external_inputs == ["pdf_path"]
    assert typecheck(plan) == []


def test_new_nodes_get_a_model_implementation_so_they_are_executable():
    """
    Without this every decomposed plan fails `executable_leaf`, and
    decomposition could only ever propose plans made of tasks that already
    exist. The router's cost sort means a deterministic implementation
    registered later wins automatically.
    """
    plan = plan_from_payload(GOOD_PAYLOAD)
    assert [i.kind for i in plan.nodes[0].implementations] == ["model"]
    assert plan.nodes[0].implementations[0].cost_estimate > 0


def test_a_node_with_no_outputs_is_flagged_rather_than_silently_accepted():
    payload = json.loads(json.dumps(GOOD_PAYLOAD))
    payload["nodes"][1]["outputs"] = []
    problems = typecheck(plan_from_payload(payload))
    assert any(p.rule == "well_formed" and "empty output_schema" in p.message
               for p in problems)


def test_a_node_with_no_inputs_is_allowed():
    payload = json.loads(json.dumps(GOOD_PAYLOAD))
    payload["nodes"] = [payload["nodes"][0]]
    payload["nodes"][0]["inputs"] = []
    payload["edges"] = []
    payload["external_inputs"] = []
    assert typecheck(plan_from_payload(payload)) == []


def test_an_empty_payload_produces_an_empty_plan_not_an_exception():
    plan = plan_from_payload({})
    assert plan.nodes == []
    assert plan.feasible is False


def test_a_hallucinated_task_id_does_not_break_parsing():
    payload = json.loads(json.dumps(GOOD_PAYLOAD))
    payload["nodes"][0]["existing_task_id"] = "task-that-does-not-exist"
    plan = plan_from_payload(payload)
    # Treated as a new node, so the plan still shows what was intended and
    # typecheck reports something meaningful rather than a parse error.
    assert plan.nodes[0].existing_task_id is None
    assert typecheck(plan) == []


async def test_unparseable_model_output_becomes_a_failed_decomposition():
    client = FakeAnthropic(text="Here's a plan for you!  (no JSON at all)")
    service = DecompositionService(client_factory=lambda: client)

    result = await service.decompose("turn my PDFs into spreadsheets")

    assert result.feasible is False
    assert "Could not generate a plan" in result.reasoning
    assert result.plan.nodes == []


async def test_a_model_error_becomes_a_failed_decomposition():
    client = FakeAnthropic(error=RuntimeError("connection reset"))
    service = DecompositionService(client_factory=lambda: client)

    result = await service.decompose("anything")

    assert result.feasible is False
    assert "connection reset" in result.reasoning


async def test_a_refusal_becomes_a_failed_decomposition():
    client = FakeAnthropic(text="{}", stop_reason="refusal")
    service = DecompositionService(client_factory=lambda: client)

    result = await service.decompose("anything")
    assert result.feasible is False
    assert "declined" in result.reasoning


async def test_a_valid_response_becomes_a_feasible_decomposition():
    client = FakeAnthropic(text=json.dumps(GOOD_PAYLOAD))
    service = DecompositionService(client_factory=lambda: client)

    result = await service.decompose("find and read the tables in a pdf")

    assert result.feasible is True
    assert len(result.plan.nodes) == 2
    # The request went out with structured outputs, not a "reply with JSON" prompt.
    sent = client.messages.calls[0]
    assert sent["output_config"]["format"]["schema"] is PLAN_SCHEMA
    assert sent["thinking"] == {"type": "adaptive"}


async def test_no_nodes_is_reported_as_a_problem():
    payload = {"feasible": True, "reasoning": "hmm", "nodes": [], "edges": [],
               "external_inputs": []}
    client = FakeAnthropic(text=json.dumps(payload))
    result = await DecompositionService(client_factory=lambda: client).decompose("x")

    assert result.feasible is False
    assert result.problems == ["the model proposed no nodes"]


def test_the_plan_schema_is_strict_enough_for_structured_outputs():
    """Every object closed and every property required, or the API rejects it."""

    def check(schema, path="root"):
        if schema.get("type") == "object" or "properties" in schema:
            assert schema.get("additionalProperties") is False, path
            assert set(schema.get("required", [])) == set(schema.get("properties", {})), path
            for name, sub in (schema.get("properties") or {}).items():
                check(sub, f"{path}.{name}")
        if isinstance(schema.get("items"), dict):
            check(schema["items"], f"{path}[]")

    check(PLAN_SCHEMA)
