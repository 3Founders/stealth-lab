"""
No match. Ask the model for a plan.

Two decisions shape this file.

**The model does not write JSON Schema.** It declares typed fields --
`{"name": "regions", "type": "array", "item_type": "object"}` -- and the
conversion to JSON Schema happens here, deterministically. Structured outputs
cannot constrain "an arbitrary JSON Schema object" (every object needs
`additionalProperties: false`, which an open-ended schema value cannot have),
so asking for raw schemas would mean going back to parsing prose for exactly
the fields the typechecker depends on most. Typed fields are constrainable,
and they make "the model returned an empty schema" impossible to express
rather than merely forbidden.

**Every genuinely new leaf gets a model implementation.** Otherwise no
decomposed plan could ever pass `executable_leaf`, and decomposition would be
able to propose only plans made entirely of tasks that already exist. A typed
task node is satisfiable by a structured-output model call by construction,
so that is the floor -- and because the router sorts by cost, the moment
anyone registers a deterministic implementation for the same task it wins
automatically. The floor is not the target: see the README on adding the
deterministic implementation first.

Retrieval context goes into the prompt so the model reuses rather than
reinvents; `existing_task_id` is how that reuse is expressed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from app.config import settings
from app.models.plan import ImplementationSpec, Plan, PlanEdge, PlanNode
from app.runners.model import assert_structured_outputs_supported
from app.services.matching import TaskMatcher

log = logging.getLogger(__name__)

MAX_NODES = 12

FIELD_TYPES = ["string", "number", "integer", "boolean", "object", "array"]
ITEM_TYPES = ["string", "number", "integer", "boolean", "object", "none"]

_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "snake_case identifier"},
        "type": {"type": "string", "enum": FIELD_TYPES},
        "item_type": {
            "type": "string",
            "enum": ITEM_TYPES,
            "description": "element type when type is array; 'none' otherwise",
        },
        "description": {"type": "string"},
    },
    "required": ["name", "type", "item_type", "description"],
    "additionalProperties": False,
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "feasible": {"type": "boolean"},
        "reasoning": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "local id, e.g. n1"},
                    "name": {"type": "string", "description": "snake_case task name"},
                    "description": {"type": "string"},
                    "existing_task_id": {
                        "type": ["string", "null"],
                        "description": "id of an existing task to reuse, or null",
                    },
                    "inputs": {"type": "array", "items": _FIELD_SCHEMA},
                    "outputs": {"type": "array", "items": _FIELD_SCHEMA},
                },
                "required": ["ref", "name", "description", "existing_task_id",
                             "inputs", "outputs"],
                "additionalProperties": False,
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["REQUIRES", "PRODUCES"]},
                    "source_ref": {"type": "string"},
                    "target_ref": {"type": "string"},
                },
                "required": ["type", "source_ref", "target_ref"],
                "additionalProperties": False,
            },
        },
        "external_inputs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["feasible", "reasoning", "nodes", "edges", "external_inputs"],
    "additionalProperties": False,
}

SYSTEM = f"""\
You decompose a request into a directed graph of typed task nodes that a \
execution engine will run.

Each node is one concrete step with a typed input and a typed output. Not a \
phase like "analysis" -- something a program or a model call could actually \
execute and whose result can be checked. Use at most {MAX_NODES} nodes, and \
prefer the smallest graph that genuinely covers the request; padding a plan \
with generic steps makes it less useful, not more thorough.

Reuse before you invent. You are given the existing tasks most similar to \
this request. If one of them already does a step you need, set that node's \
`existing_task_id` to its id and copy its name; declare its inputs and \
outputs exactly as listed. Otherwise set `existing_task_id` to null.

Edges:
  PRODUCES  source's output feeds target's input. Only use this when they \
share at least one field name -- that shared name IS the dataflow.
  REQUIRES  source must run before target, but hands over no data.

Every field a node needs must either share a name with an upstream node's \
output field, or be listed in `external_inputs` (values the caller supplies). \
A field that is neither will be rejected.

Name fields in snake_case and reuse the same name across the nodes that pass \
a value along -- `pdf_path` produced by one node and consumed as `pdf_path` \
by the next. Renaming the same value between stages breaks the dataflow.

Set `item_type` to the element type when a field's type is "array", and to \
"none" otherwise.

Set `feasible` to false with an empty node list if the request does not \
describe work that can be decomposed into executable steps.
"""


@dataclass
class Decomposition:
    feasible: bool = False
    reasoning: str = ""
    plan: Plan = field(default_factory=Plan)
    problems: list[str] = field(default_factory=list)
    related_existing: list[str] = field(default_factory=list)
    cost: float = 0.0


def _to_json_schema(fields: list[dict[str, Any]], *, allow_empty: bool) -> dict[str, Any]:
    """
    Typed field declarations -> JSON Schema.

    `allow_empty` is asymmetric on purpose. A node with no inputs is
    meaningful (it reads the world rather than its predecessor), so an empty
    input list becomes an explicit `"properties": {}` -- a commitment to
    having none. A node with no *outputs* produces nothing and cannot be part
    of a dataflow, so it becomes a bare `{"type": "object"}`, which the
    typechecker flags as an empty schema. The plan fails loudly instead of
    silently containing a stage whose result nothing can use.
    """
    if not fields and not allow_empty:
        return {"type": "object"}

    properties: dict[str, Any] = {}
    for entry in fields:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        declared = entry.get("type") or "string"
        schema: dict[str, Any] = {"type": declared}
        if entry.get("description"):
            schema["description"] = str(entry["description"])
        if declared == "array":
            item_type = entry.get("item_type") or "none"
            schema["items"] = {} if item_type == "none" else {"type": item_type}
        properties[name] = schema

    return {"type": "object", "properties": properties, "required": sorted(properties)}


def _model_implementation(node_name: str, output_schema: dict[str, Any]) -> ImplementationSpec:
    return ImplementationSpec(
        name="model_fallback",
        kind="model",
        spec={
            "model": settings.model_id,
            "system": (
                f"You are the '{node_name}' stage of a typed pipeline. Produce exactly "
                f"the declared output from the given input. Do not invent values."
            ),
            "output_schema": output_schema,
        },
        # A rough per-call estimate, not a measurement. High enough that any
        # deterministic implementation registered later sorts ahead of it,
        # which is the behaviour we want the moment one exists.
        cost_estimate=0.05,
        latency_estimate_ms=20_000,
    )


def plan_from_payload(payload: dict[str, Any]) -> Plan:
    """Convert the model's structured response into a Plan. Pure; no I/O."""
    nodes: list[PlanNode] = []
    for raw in payload.get("nodes") or []:
        existing = raw.get("existing_task_id")
        task_id: Optional[UUID] = None
        if existing:
            try:
                task_id = UUID(str(existing))
            except ValueError:
                # A hallucinated id is a reuse claim we cannot honour. Treated
                # as a new node rather than dropped, so the plan still shows
                # what the model intended and typecheck reports the real
                # problem (no implementation) instead of a parse error.
                log.info("ignoring non-uuid existing_task_id %r", existing)

        input_schema = _to_json_schema(raw.get("inputs") or [], allow_empty=True)
        output_schema = _to_json_schema(raw.get("outputs") or [], allow_empty=False)

        node = PlanNode(
            ref=str(raw.get("ref") or "").strip(),
            name=str(raw.get("name") or "").strip(),
            description=str(raw.get("description") or ""),
            input_schema=input_schema,
            output_schema=output_schema,
            existing_task_id=task_id,
        )
        if task_id is None:
            node.implementations = [_model_implementation(node.name, output_schema)]
        nodes.append(node)

    edges = [
        PlanEdge(
            type=raw.get("type", "PRODUCES"),
            source_ref=str(raw.get("source_ref") or ""),
            target_ref=str(raw.get("target_ref") or ""),
        )
        for raw in payload.get("edges") or []
    ]

    return Plan(
        nodes=nodes,
        edges=edges,
        external_inputs=[str(i) for i in (payload.get("external_inputs") or [])],
        feasible=bool(payload.get("feasible", False)),
        reasoning=str(payload.get("reasoning") or "")[:2000],
    )


class DecompositionService:
    def __init__(self, matcher: Optional[TaskMatcher] = None, client_factory=None):
        self._matcher = matcher
        self._client_factory = client_factory

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()

        # Same structured-outputs floor as the model runner. Reported as a
        # failed decomposition with an actionable message rather than a
        # TypeError that reads like a bug in this code.
        assert_structured_outputs_supported()
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(
            api_key=settings.require("anthropic_api_key"),
            timeout=settings.model_timeout_s,
        )

    async def _context(self, prompt: str) -> tuple[str, list[str]]:
        """Prior art for the prompt. Failure here costs quality, not safety."""
        if self._matcher is None:
            return "", []
        try:
            matches = await self._matcher.search(prompt, top_k=10)
        except Exception as exc:  # noqa: BLE001
            log.warning("retrieval failed, decomposing without prior art: %s", exc)
            return "", []

        lines = []
        for match in matches:
            inputs = ", ".join((match.task.input_schema.get("properties") or {}).keys())
            outputs = ", ".join((match.task.output_schema.get("properties") or {}).keys())
            lines.append(
                f"- id={match.task.id} name={match.task.name}\n"
                f"    {match.task.description or '(no description)'}\n"
                f"    inputs: {inputs or '(none)'}\n"
                f"    outputs: {outputs or '(none)'}"
            )
        return "\n".join(lines), [m.task.name for m in matches]

    async def decompose(self, prompt: str, inputs: Optional[dict] = None) -> Decomposition:
        context, related = await self._context(prompt)

        user = ""
        if context:
            user += f"## Existing tasks that may already do part of this\n\n{context}\n\n"
        if inputs:
            user += (
                "## Inputs the caller is supplying\n\n"
                + json.dumps({k: _describe(v) for k, v in inputs.items()}, indent=2)
                + "\n\n"
            )
        user += f"## The request\n\n{prompt}\n"

        try:
            payload, cost = await self._call(user)
        except Exception as exc:  # noqa: BLE001
            # A model that times out, refuses, or returns something unusable
            # produces a failed proposal, not a 500. The operator should see
            # a rejected plan with a reason, not a stack trace.
            log.error("decomposition failed: %s", exc)
            return Decomposition(
                feasible=False,
                reasoning=f"Could not generate a plan: {exc}",
                related_existing=related,
            )

        try:
            plan = plan_from_payload(payload)
        except Exception as exc:  # noqa: BLE001
            return Decomposition(
                feasible=False,
                reasoning="The model's plan could not be interpreted.",
                problems=[f"could not read the proposed plan: {type(exc).__name__}: {exc}"],
                related_existing=related,
            )

        return Decomposition(
            feasible=plan.feasible and bool(plan.nodes),
            reasoning=plan.reasoning,
            plan=plan,
            related_existing=related,
            cost=cost,
            problems=(
                [] if plan.nodes else ["the model proposed no nodes"]
            ),
        )

    async def _call(self, user: str) -> tuple[dict[str, Any], float]:
        client = self._client()
        async with client.messages.stream(
            model=settings.model_id,
            max_tokens=settings.model_max_tokens,
            thinking={"type": "adaptive"},
            output_config={
                "effort": settings.model_effort,
                "format": {"type": "json_schema", "schema": PLAN_SCHEMA},
            },
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            message = await stream.get_final_message()

        if getattr(message, "stop_reason", None) == "refusal":
            raise RuntimeError("the model declined to decompose this request")

        usage = getattr(message, "usage", None)
        cost = 0.0
        if usage is not None:
            cost = (
                (getattr(usage, "input_tokens", 0) or 0) * settings.model_input_cost_per_token
                + (getattr(usage, "output_tokens", 0) or 0) * settings.model_output_cost_per_token
            )

        text = next((b.text for b in message.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError(f"no text block in response (stop_reason={message.stop_reason})")
        return json.loads(text), cost


def _describe(value: Any) -> str:
    """Summarise an input for the prompt without pasting a whole document into it."""
    if isinstance(value, str):
        return value if len(value) <= 200 else f"{value[:200]}... ({len(value)} chars)"
    if isinstance(value, (list, tuple)):
        return f"{type(value).__name__} of {len(value)}"
    if isinstance(value, dict):
        return f"object with keys {sorted(value)[:10]}"
    return type(value).__name__
