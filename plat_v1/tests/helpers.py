"""Shared fixtures for the offline suite. No database, no API keys, no network."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID, uuid4

from app.models.plan import Expansion, ImplementationSpec, Plan, PlanEdge, PlanNode
from app.models.task import Implementation
from app.runners.base import RunContext, RunnerError, RunnerResult
from app.services.cache import CacheHit


def obj(fields: dict[str, str], required: Optional[list[str]] = None) -> dict[str, Any]:
    """A minimal object schema: {"pdf_path": "string"} -> JSON Schema."""
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in fields.items()},
        "required": sorted(fields) if required is None else required,
    }


def impl_spec(name: str = "cheap") -> ImplementationSpec:
    return ImplementationSpec(name=name, kind="python", spec={"ref": "tables:validate_types"})


def node(
    ref: str,
    inputs: dict[str, str],
    outputs: dict[str, str],
    *,
    name: Optional[str] = None,
    with_implementation: bool = True,
    **kwargs,
) -> PlanNode:
    return PlanNode(
        ref=ref,
        name=name or ref,
        input_schema=obj(inputs),
        output_schema=obj(outputs),
        implementations=[impl_spec()] if with_implementation else [],
        **kwargs,
    )


def valid_plan() -> Plan:
    """
    The reference well-formed plan the rule tests mutate.

    Two stages: one reads a PDF and reports table regions, the next reads the
    same PDF plus those regions and returns a grid. `pdf_path` is external and
    consumed twice; `regions` flows along the PRODUCES edge.
    """
    return Plan(
        nodes=[
            node("n1", {"pdf_path": "string"}, {"regions": "array"}),
            node("n2", {"pdf_path": "string", "regions": "array"}, {"grid": "array"}),
        ],
        edges=[PlanEdge(type="PRODUCES", source_ref="n1", target_ref="n2")],
        external_inputs=["pdf_path"],
    )


def valid_composite_plan() -> Plan:
    child_a = node("e1", {"pdf_path": "string"}, {"regions": "array"})
    child_b = node("e2", {"pdf_path": "string", "regions": "array"}, {"grid": "array"})
    composite = PlanNode(
        ref="c1",
        name="composite",
        kind="composite",
        input_schema=obj({"pdf_path": "string"}),
        output_schema=obj({"grid": "array"}),
        expansion=Expansion(
            nodes=[child_a, child_b],
            edges=[PlanEdge(type="PRODUCES", source_ref="e1", target_ref="e2")],
        ),
    )
    return Plan(nodes=[composite], edges=[], external_inputs=["pdf_path"])


def rules(problems) -> set[str]:
    return {p.rule for p in problems}


# ---------------------------------------------------------------------------
# Router / executor doubles
# ---------------------------------------------------------------------------


def implementation(
    name: str, cost: float, latency: int = 0, impl_id: Optional[UUID] = None, kind: str = "python"
) -> Implementation:
    return Implementation(
        id=impl_id or uuid4(),
        task_node_id=uuid4(),
        name=name,
        kind=kind,
        spec={"id": name},
        cost_estimate=cost,
        latency_estimate_ms=latency,
    )


@dataclass
class FakeRouterStore:
    implementations: list[Implementation] = field(default_factory=list)
    scores: dict[UUID, float] = field(default_factory=dict)
    cache_hit: Optional[CacheHit] = None
    enumeration_calls: int = 0
    recorded_hits: list[UUID] = field(default_factory=list)

    async def enabled_implementations(self, task_node_id: UUID) -> list[Implementation]:
        self.enumeration_calls += 1
        return list(self.implementations)

    async def latest_eval_scores(self, task_node_id: UUID) -> dict[UUID, float]:
        return dict(self.scores)

    async def probe_cache(self, task_node_id: UUID, fingerprint: str) -> Optional[CacheHit]:
        return self.cache_hit

    async def record_cache_hit(self, entry_id: UUID) -> None:
        self.recorded_hits.append(entry_id)

    async def implementation_by_id(self, implementation_id: UUID) -> Optional[Implementation]:
        return next((i for i in self.implementations if i.id == implementation_id), None)

    async def implementation_by_name(
        self, task_node_id: UUID, name: str
    ) -> Optional[Implementation]:
        return next(
            (
                i
                for i in self.implementations
                if i.name == name and i.task_node_id == task_node_id
            ),
            None,
        )


@dataclass
class ScriptedRunner:
    """Succeeds or fails per implementation name, and records the order tried."""

    succeeds: set[str] = field(default_factory=set)
    kind: str = "python"
    calls: list[str] = field(default_factory=list)

    async def run(self, spec: dict, inputs: dict, ctx: RunContext) -> RunnerResult:
        name = spec.get("id", "?")
        self.calls.append(name)
        if name not in self.succeeds:
            raise RunnerError(f"{name} was scripted to fail")
        return RunnerResult(output={"value": name})


# ---------------------------------------------------------------------------
# Anthropic SDK doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeBlock:
    text: str
    type: str = "text"


@dataclass
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeMessage:
    content: list[FakeBlock]
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "end_turn"
    stop_details: Any = None


class _FakeStream:
    def __init__(self, message: FakeMessage):
        self._message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get_final_message(self) -> FakeMessage:
        return self._message


class _FakeMessages:
    def __init__(self, message: Optional[FakeMessage], error: Optional[Exception]):
        self._message = message
        self._error = error
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return _FakeStream(self._message)


class FakeAnthropic:
    def __init__(
        self, text: str = "{}", error: Optional[Exception] = None,
        stop_reason: str = "end_turn", usage: Optional[FakeUsage] = None,
    ):
        message = FakeMessage(
            content=[FakeBlock(text=text)],
            usage=usage or FakeUsage(),
            stop_reason=stop_reason,
        )
        self.messages = _FakeMessages(message, error)
