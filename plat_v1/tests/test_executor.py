"""Execution: escalation order, the escalation cap, tracing, and composites."""
from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

import pytest

from app.config import settings
from app.models.plan import Expansion, Plan, PlanEdge, PlanNode
from app.services.cache import CacheHit
from app.services.executor import Executor
from app.services.router import Router
from app.services.traces import NullTraceRecorder
from tests.helpers import FakeRouterStore, ScriptedRunner, implementation, obj

TASK = uuid4()


@pytest.fixture(autouse=True)
def temp_artifacts(monkeypatch):
    """Keep test runs out of ./artifacts."""
    monkeypatch.setattr(settings, "keep_run_artifacts", False)


class FakeCacheStore:
    def __init__(self, seen: Optional[set[tuple[UUID, str]]] = None):
        self.writes: list[tuple] = []
        self.seen = seen or set()

    @property
    def cached_implementations(self) -> list[UUID]:
        return [w[2] for w in self.writes]

    async def write(self, task_node_id, fingerprint, implementation_id, params):
        self.writes.append((task_node_id, fingerprint, implementation_id, params))

    async def has_entry(self, task_node_id, fingerprint) -> bool:
        return (task_node_id, fingerprint) in self.seen


def one_node_plan(task_id: UUID = TASK) -> Plan:
    return Plan(
        nodes=[
            PlanNode(
                ref="n1",
                name="stage",
                input_schema=obj({"seed": "string"}),
                output_schema=obj({"value": "string"}),
                existing_task_id=task_id,
            )
        ],
        edges=[],
        external_inputs=["seed"],
    )


def build(store, runner, cache=None, recorder=None) -> Executor:
    return Executor(
        router=Router(store),
        store=store,
        runners={"python": runner},
        recorder=recorder or NullTraceRecorder(),
        cache_store=cache,
    )


# --- escalation ------------------------------------------------------------


async def test_escalates_in_cost_order_until_one_succeeds():
    store = FakeRouterStore(
        implementations=[
            implementation("c", 2.0),
            implementation("a", 0.0),
            implementation("b", 1.0),
            implementation("d", 3.0),
        ]
    )
    runner = ScriptedRunner(succeeds={"c"})
    result = await build(store, runner).execute(one_node_plan(), {"seed": "x"})

    assert result.status == "succeeded"
    assert runner.calls == ["a", "b", "c"]
    assert result.stages[0].attempts == 3
    assert result.outputs == {"value": "c"}


async def test_escalation_is_capped():
    store = FakeRouterStore(
        implementations=[implementation(name, float(i)) for i, name in enumerate("abcdef")]
    )
    runner = ScriptedRunner(succeeds=set())
    result = await build(store, runner).execute(one_node_plan(), {"seed": "x"})

    assert result.status == "failed"
    assert len(runner.calls) == 1 + settings.max_escalations
    assert runner.calls == ["a", "b", "c", "d"]
    assert "gave up after 4 attempts" in result.stages[0].error


async def test_first_attempt_succeeding_does_not_escalate():
    store = FakeRouterStore(implementations=[implementation("a", 0.0), implementation("b", 1.0)])
    runner = ScriptedRunner(succeeds={"a"})
    result = await build(store, runner).execute(one_node_plan(), {"seed": "x"})

    assert runner.calls == ["a"]
    assert result.stages[0].attempts == 1


async def test_failed_cache_hit_reroutes_instead_of_giving_up():
    """
    A hit returns no alternatives by design. Once it fails, the saving it
    bought is already spent, so the stage falls back to a real route rather
    than reporting a dead end.
    """
    cached = implementation("stale", 0.0)
    fresh = implementation("fresh", 1.0)
    store = FakeRouterStore(
        implementations=[cached, fresh],
        cache_hit=CacheHit(uuid4(), cached.id, {}, hits=7),
    )
    runner = ScriptedRunner(succeeds={"fresh"})
    result = await build(store, runner).execute(one_node_plan(), {"seed": "x"})

    assert result.status == "succeeded"
    assert runner.calls == ["stale", "fresh"]
    assert store.enumeration_calls == 1  # the re-route, not the initial hit


# --- validation and criteria ----------------------------------------------


async def test_output_failing_its_schema_counts_as_a_stage_failure():
    class WrongShape(ScriptedRunner):
        async def run(self, spec, inputs, ctx):
            from app.runners.base import RunnerResult

            self.calls.append(spec.get("id"))
            return RunnerResult(output={"wrong_key": 1})

    store = FakeRouterStore(implementations=[implementation("a", 0.0)])
    result = await build(store, WrongShape()).execute(one_node_plan(), {"seed": "x"})

    assert result.status == "failed"
    assert "value" in result.stages[0].error


async def test_success_criteria_are_evaluated():
    plan = one_node_plan()
    plan.nodes[0].success_criteria = {"equals": {"value": "b"}}

    store = FakeRouterStore(implementations=[implementation("a", 0.0), implementation("b", 1.0)])
    runner = ScriptedRunner(succeeds={"a", "b"})
    result = await build(store, runner).execute(plan, {"seed": "x"})

    # `a` runs, returns value="a", fails the criterion, and the stage escalates.
    assert runner.calls == ["a", "b"]
    assert result.status == "succeeded"


async def test_missing_input_fails_before_routing():
    store = FakeRouterStore(implementations=[implementation("a", 0.0)])
    runner = ScriptedRunner(succeeds={"a"})
    result = await build(store, runner).execute(one_node_plan(), {})

    assert result.status == "failed"
    assert runner.calls == []
    assert "seed" in result.stages[0].error


async def test_unbound_plan_node_is_refused():
    plan = one_node_plan()
    plan.nodes[0].existing_task_id = None
    result = await build(FakeRouterStore(), ScriptedRunner()).execute(plan, {"seed": "x"})

    assert result.status == "failed"
    assert "must be persisted" in result.stages[0].error


# --- tracing ---------------------------------------------------------------


async def test_every_attempt_is_traced_including_failures():
    store = FakeRouterStore(
        implementations=[implementation("a", 0.0), implementation("b", 1.0)]
    )
    recorder = NullTraceRecorder()
    await build(store, ScriptedRunner(succeeds={"b"}), recorder=recorder).execute(
        one_node_plan(), {"seed": "x"}
    )

    assert [r.outcome for r in recorder.records] == ["failure", "success"]
    assert [r.attempt for r in recorder.records] == [1, 2]


async def test_cache_entry_written_only_on_success():
    store = FakeRouterStore(implementations=[implementation("a", 0.0)])
    cache = FakeCacheStore()
    await build(store, ScriptedRunner(succeeds=set()), cache=cache).execute(
        one_node_plan(), {"seed": "x"}
    )
    assert cache.writes == []

    await build(store, ScriptedRunner(succeeds={"a"}), cache=cache).execute(
        one_node_plan(), {"seed": "x"}
    )
    assert len(cache.writes) == 1


async def test_cache_as_records_the_cheaper_stand_in(monkeypatch):
    """
    An expensive implementation that worked out something reusable can hand
    the cache entry to a cheap one. Without this the second run picks the
    expensive implementation again and the cache saves a lookup, not money.
    """
    replay = implementation("cached_replay", 0.0)
    expensive = implementation("model_mapping", 0.5)
    expensive.spec = {"id": "model_mapping", "cache_as": "cached_replay"}
    # Both belong to the same task, which is what cache_as resolves within.
    replay.task_node_id = expensive.task_node_id = TASK

    store = FakeRouterStore(implementations=[replay, expensive])
    cache = FakeCacheStore()
    runner = ScriptedRunner(succeeds={"model_mapping"})

    result = await build(store, runner, cache=cache).execute(one_node_plan(), {"seed": "x"})

    assert result.status == "succeeded"
    assert runner.calls == ["cached_replay", "model_mapping"]
    assert cache.cached_implementations == [replay.id]


async def test_cache_as_naming_a_missing_implementation_falls_back_to_itself():
    expensive = implementation("model_mapping", 0.5)
    expensive.spec = {"id": "model_mapping", "cache_as": "does_not_exist"}
    expensive.task_node_id = TASK

    store = FakeRouterStore(implementations=[expensive])
    cache = FakeCacheStore()
    await build(store, ScriptedRunner(succeeds={"model_mapping"}), cache=cache).execute(
        one_node_plan(), {"seed": "x"}
    )
    assert cache.cached_implementations == [expensive.id]


# --- cache_key: fingerprinting shape, not data -----------------------------


def two_input_plan(cache_key=None) -> Plan:
    return Plan(
        nodes=[
            PlanNode(
                ref="n1",
                name="map_to_schema",
                input_schema=obj({"columns": "string", "typed_grid": "string"}),
                output_schema=obj({"value": "string"}),
                existing_task_id=TASK,
                cache_key=cache_key,
            )
        ],
        edges=[],
        external_inputs=["columns", "typed_grid"],
    )


async def _fingerprint_for(cache_key, inputs) -> str:
    """Run a stage and return the fingerprint the cache was keyed on."""
    store = FakeRouterStore(implementations=[implementation("a", 0.0)])
    cache = FakeCacheStore()
    await build(store, ScriptedRunner(succeeds={"a"}), cache=cache).execute(
        two_input_plan(cache_key), inputs
    )
    return cache.writes[0][1]


async def test_cache_key_ignores_inputs_it_does_not_name():
    """
    The invoice case. Same columns, different cell values -- the mapping is
    the same, so the cache entry must be too. Without cache_key this stage
    hashes the data and two invoices from one vendor never share an entry.
    """
    january = await _fingerprint_for(["columns"], {"columns": "sku,qty", "typed_grid": "A-1,3"})
    february = await _fingerprint_for(["columns"], {"columns": "sku,qty", "typed_grid": "Z-9,88"})
    assert january == february


async def test_without_a_cache_key_every_input_counts():
    january = await _fingerprint_for(None, {"columns": "sku,qty", "typed_grid": "A-1,3"})
    february = await _fingerprint_for(None, {"columns": "sku,qty", "typed_grid": "Z-9,88"})
    assert january != february


async def test_a_different_shape_still_produces_a_different_key():
    invoice = await _fingerprint_for(["columns"], {"columns": "sku,qty", "typed_grid": "A-1,3"})
    receipt = await _fingerprint_for(["columns"], {"columns": "date,total", "typed_grid": "A-1,3"})
    assert invoice != receipt


async def test_a_cache_key_naming_a_missing_input_falls_back_to_all_of_them():
    """
    Safe direction: an over-specific fingerprint costs a miss, an
    under-specific one reuses a mapping against data it never saw.
    """
    one = await _fingerprint_for(["nonexistent"], {"columns": "a", "typed_grid": "1"})
    two = await _fingerprint_for(["nonexistent"], {"columns": "a", "typed_grid": "2"})
    assert one != two


# --- the first-layout gate -------------------------------------------------


async def test_reviewed_layout_gate_blocks_an_unseen_layout(monkeypatch):
    monkeypatch.setattr(settings, "allow_unreviewed_first_layout_mapping", False)

    gated = implementation("model_mapping", 0.0)
    gated.spec = {"id": "model_mapping", "first_layout_requires_review": True}
    fallback = implementation("deterministic", 1.0)

    store = FakeRouterStore(implementations=[gated, fallback])
    runner = ScriptedRunner(succeeds={"model_mapping", "deterministic"})
    result = await build(store, runner, cache=FakeCacheStore()).execute(
        one_node_plan(), {"seed": "x"}
    )

    assert runner.calls == ["deterministic"]  # the gated one never ran
    assert result.status == "succeeded"


async def test_reviewed_layout_gate_allows_a_known_layout():
    from app.services.cache import fingerprint_inputs

    fingerprint = fingerprint_inputs({"seed": "x"})
    gated = implementation("model_mapping", 0.0)
    gated.spec = {"id": "model_mapping", "first_layout_requires_review": True}

    store = FakeRouterStore(implementations=[gated])
    runner = ScriptedRunner(succeeds={"model_mapping"})
    cache = FakeCacheStore(seen={(TASK, fingerprint)})
    result = await build(store, runner, cache=cache).execute(one_node_plan(), {"seed": "x"})

    assert runner.calls == ["model_mapping"]
    assert result.status == "succeeded"


# --- composites ------------------------------------------------------------


async def test_composite_expands_inline_and_surfaces_its_declared_output():
    inner_a = PlanNode(
        ref="e1",
        name="first",
        input_schema=obj({"seed": "string"}),
        output_schema=obj({"middle": "string"}),
        existing_task_id=TASK,
    )
    inner_b = PlanNode(
        ref="e2",
        name="second",
        input_schema=obj({"middle": "string"}),
        output_schema=obj({"value": "string"}),
        existing_task_id=TASK,
    )
    composite = PlanNode(
        ref="c1",
        name="workflow",
        kind="composite",
        input_schema=obj({"seed": "string"}),
        output_schema=obj({"value": "string"}),
        existing_task_id=TASK,
        expansion=Expansion(
            nodes=[inner_a, inner_b],
            edges=[PlanEdge(type="PRODUCES", source_ref="e1", target_ref="e2")],
        ),
    )
    plan = Plan(nodes=[composite], edges=[], external_inputs=["seed"])

    class Echo(ScriptedRunner):
        async def run(self, spec, inputs, ctx):
            from app.runners.base import RunnerResult

            self.calls.append(ctx.node_ref)
            key = "middle" if ctx.node_ref == "e1" else "value"
            return RunnerResult(output={key: ctx.node_ref})

    store = FakeRouterStore(implementations=[implementation("only", 0.0)])
    result = await build(store, Echo()).execute(plan, {"seed": "x"})

    assert result.status == "succeeded"
    # Child stages are reported individually, then the composite itself.
    assert [s.node_ref for s in result.stages] == ["e1", "e2", "c1"]
    assert result.stages[-1].output == {"value": "e2"}


async def test_composite_rollup_does_not_double_count_cost_or_latency():
    """
    The run view totals a run by summing every trace row. The children each
    write one, so a roll-up carrying their sum would double every figure the
    user reads -- and the composite itself routes no implementation, so its
    own cost is genuinely nothing.

    The end-to-end script cannot catch this: every deterministic stage costs
    zero, and zero doubled is zero.
    """
    inner = PlanNode(
        ref="e1",
        name="expensive",
        input_schema=obj({"seed": "string"}),
        output_schema=obj({"value": "string"}),
        existing_task_id=TASK,
    )
    composite = PlanNode(
        ref="c1",
        name="workflow",
        kind="composite",
        input_schema=obj({"seed": "string"}),
        output_schema=obj({"value": "string"}),
        existing_task_id=TASK,
        expansion=Expansion(nodes=[inner], edges=[]),
    )
    plan = Plan(nodes=[composite], edges=[], external_inputs=["seed"])

    class Costly(ScriptedRunner):
        async def run(self, spec, inputs, ctx):
            from app.runners.base import RunnerResult

            self.calls.append(ctx.node_ref)
            return RunnerResult(output={"value": "x"}, cost=0.05)

    store = FakeRouterStore(implementations=[implementation("a", 0.0)])
    recorder = NullTraceRecorder()
    result = await build(store, Costly(), recorder=recorder).execute(plan, {"seed": "x"})

    assert result.status == "succeeded"
    # One real cost, counted once -- in memory and in what gets persisted.
    assert result.total_cost == pytest.approx(0.05)
    assert sum(r.cost for r in recorder.records) == pytest.approx(0.05)


async def test_composite_failure_names_the_child_that_failed():
    inner = PlanNode(
        ref="e1",
        name="first",
        input_schema=obj({"seed": "string"}),
        output_schema=obj({"value": "string"}),
        existing_task_id=TASK,
    )
    composite = PlanNode(
        ref="c1",
        name="workflow",
        kind="composite",
        input_schema=obj({"seed": "string"}),
        output_schema=obj({"value": "string"}),
        existing_task_id=TASK,
        expansion=Expansion(nodes=[inner], edges=[]),
    )
    plan = Plan(nodes=[composite], edges=[], external_inputs=["seed"])

    store = FakeRouterStore(implementations=[implementation("a", 0.0)])
    result = await build(store, ScriptedRunner(succeeds=set())).execute(plan, {"seed": "x"})

    assert result.status == "failed"
    assert "expansion stage 'e1' failed" in result.stages[-1].error


# --- the partial-result setting -------------------------------------------


async def test_partial_outputs_are_kept_when_configured(monkeypatch):
    """
    The permissive branch keeps what completed. It does not relabel the run
    as succeeded -- a run reporting success with a hole in it is the failure
    mode this setting exists to make explicit, not to hide.
    """
    monkeypatch.setattr(settings, "fail_run_on_stage_failure", False)

    good = PlanNode(
        ref="n1",
        name="good",
        input_schema=obj({"seed": "string"}),
        output_schema=obj({"value": "string"}),
        existing_task_id=TASK,
    )
    bad = PlanNode(
        ref="n2",
        name="bad",
        input_schema=obj({"value": "string"}),
        output_schema=obj({"final": "string"}),
        existing_task_id=TASK,
    )
    plan = Plan(
        nodes=[good, bad],
        edges=[PlanEdge(type="PRODUCES", source_ref="n1", target_ref="n2")],
        external_inputs=["seed"],
    )

    class OnlyFirst(ScriptedRunner):
        async def run(self, spec, inputs, ctx):
            from app.runners.base import RunnerError, RunnerResult

            self.calls.append(ctx.node_ref)
            if ctx.node_ref == "n1":
                return RunnerResult(output={"value": "ok"})
            raise RunnerError("second stage is broken")

    store = FakeRouterStore(implementations=[implementation("a", 0.0)])
    result = await build(store, OnlyFirst()).execute(plan, {"seed": "x"})

    assert result.status == "failed"
    assert result.outputs == {"value": "ok"}
