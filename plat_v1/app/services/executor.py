"""
DAG execution.

Topologically sort the plan, then for each node:

  1. assemble inputs from external inputs and upstream outputs
  2. validate them against the node's input_schema
  3. ask the router for an implementation
  4. run it through the matching runner
  5. validate the output and evaluate the success criteria
  6. record a trace, whatever happened
  7. on failure, escalate to the next implementation in cost order

Dataflow is by property name in a single flat namespace. A node consumes any
declared input that some upstream node produced under that name, or that the
caller supplied. That is the same model the typechecker checks, so a plan
that typechecks is a plan whose inputs resolve -- the runtime assembly step
below should never be where a missing input is discovered.

Composite nodes expand inline: their subgraph executes in the same namespace
before the outer plan continues. One level, which typecheck enforces.
"""
from __future__ import annotations

import logging
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Protocol
from uuid import UUID

from app.config import settings
from app.models.plan import Plan, PlanEdge, PlanNode
from app.models.run import RunResult, StageResult
from app.models.task import Implementation
from app.runners.base import RunContext, Runner, RunnerError
from app.services.cache import fingerprint_inputs
from app.services.criteria import evaluate_criteria
from app.services.router import RouteDecision, Router
from app.services.traces import NullTraceRecorder, TraceRecord, TraceRecorder
from app.services.typecheck import topological_order
from app.services.validation import validate_value

log = logging.getLogger(__name__)


def _cache_inputs(node: PlanNode, node_inputs: dict[str, Any]) -> dict[str, Any]:
    """
    The subset of a stage's inputs that its cache fingerprint is taken over.

    Without this, a stage downstream of extraction fingerprints its *data*.
    `map_to_schema` receives typed_grid, columns and target_schema, so hashing
    all three keys the cache on the actual cell values -- two invoices from
    the same vendor with different amounts would never share an entry, for
    precisely the one stage in the chain that costs a model call. Declaring
    `cache_key = ["columns","target_schema"]` keys it on the table's shape,
    which is what the cached mapping is genuinely a function of.

    A cache_key naming a property the stage didn't receive falls back to
    fingerprinting everything. That direction is safe: an over-specific
    fingerprint costs a cache miss, an under-specific one reuses a mapping
    against data it was never validated on.
    """
    if node.cache_key is None:
        return node_inputs
    missing = [name for name in node.cache_key if name not in node_inputs]
    if missing:
        log.warning(
            "task %r declares cache_key %s but did not receive %s; "
            "fingerprinting all inputs instead",
            node.name, node.cache_key, ", ".join(missing),
        )
        return node_inputs
    return {name: node_inputs[name] for name in node.cache_key}


class ExecutionStore(Protocol):
    """The little the executor needs from the database beyond the router."""

    async def implementation_by_id(self, implementation_id: UUID) -> Optional[Implementation]: ...


class Executor:
    def __init__(
        self,
        router: Router,
        store: Optional[ExecutionStore] = None,
        runners: Optional[dict[str, Runner]] = None,
        recorder: Optional[TraceRecorder] = None,
        cache_store: Optional[Any] = None,
    ):
        from app.runners import default_runners

        self._router = router
        self._store = store
        self._runners = runners if runners is not None else default_runners()
        self._recorder = recorder or NullTraceRecorder()
        self._cache = cache_store

    # ------------------------------------------------------------------

    async def execute(
        self,
        plan: Plan,
        inputs: dict[str, Any],
        run_id: Optional[UUID] = None,
        quality_bar: Optional[float] = None,
        max_cost: Optional[float] = None,
    ) -> RunResult:
        workdir = self._workdir(run_id)
        result = RunResult(run_id=run_id, status="running")

        available: dict[str, Any] = dict(inputs)
        produced: dict[str, Any] = {}

        failure = await self._run_nodes(
            plan.nodes,
            plan.edges,
            available=available,
            produced=produced,
            stages=result.stages,
            workdir=workdir,
            run_id=run_id,
            quality_bar=quality_bar,
            max_cost=max_cost,
        )

        if failure is not None:
            result.status = "failed"
            result.error = f"stage '{failure.node_ref}' failed: {failure.error}"
            # The permissive branch still reports the run as failed. Returning
            # "succeeded" with a hole in the middle is the failure mode that
            # costs the most later -- what the setting buys is keeping the
            # partial outputs, not relabelling the outcome.
            result.outputs = {} if settings.fail_run_on_stage_failure else produced
            return result

        result.status = "succeeded"
        result.outputs = produced
        return result

    # ------------------------------------------------------------------

    async def _run_nodes(
        self,
        nodes: list[PlanNode],
        edges: list[PlanEdge],
        *,
        available: dict[str, Any],
        produced: dict[str, Any],
        stages: list[StageResult],
        workdir: Path,
        run_id: Optional[UUID],
        quality_bar: Optional[float],
        max_cost: Optional[float],
        parent_trace_id: Optional[UUID] = None,
    ) -> Optional[StageResult]:
        """Run an ordered subgraph. Returns the first failing stage, or None."""
        for node in topological_order(nodes, edges):
            if node.kind == "composite":
                stage = await self._run_composite(
                    node,
                    available=available,
                    produced=produced,
                    stages=stages,
                    workdir=workdir,
                    run_id=run_id,
                    quality_bar=quality_bar,
                    max_cost=max_cost,
                )
            else:
                stage = await self._run_leaf(
                    node,
                    available=available,
                    workdir=workdir,
                    run_id=run_id,
                    quality_bar=quality_bar,
                    max_cost=max_cost,
                    parent_trace_id=parent_trace_id,
                )

            # A composite reports itself after its children, so the stage
            # strip reads in execution order and the roll-up sits under the
            # work it rolled up.
            stages.append(stage)

            if stage.outcome != "success":
                return stage

            available.update(stage.output)
            produced.update(stage.output)
        return None

    async def _run_composite(
        self,
        node: PlanNode,
        *,
        available: dict[str, Any],
        produced: dict[str, Any],
        stages: list[StageResult],
        workdir: Path,
        run_id: Optional[UUID],
        quality_bar: Optional[float],
        max_cost: Optional[float],
    ) -> StageResult:
        if node.expansion is None or not node.expansion.nodes:
            stage = StageResult(
                node_ref=node.ref,
                task_name=node.name,
                task_node_id=node.existing_task_id,
                outcome="failure",
                error="composite node has no expansion",
            )
            await self._record_stage(stage, run_id)
            return stage

        started = time.monotonic()
        child_stages: list[StageResult] = []
        failure = await self._run_nodes(
            node.expansion.nodes,
            node.expansion.edges,
            available=available,
            produced=produced,
            stages=child_stages,
            workdir=workdir,
            run_id=run_id,
            quality_bar=quality_bar,
            max_cost=max_cost,
        )
        stages.extend(child_stages)

        elapsed = int((time.monotonic() - started) * 1000)
        if failure is not None:
            rolled_up = StageResult(
                node_ref=node.ref,
                task_name=node.name,
                task_node_id=node.existing_task_id,
                outcome="failure",
                # Zero, not the sum of the children -- see _record_stage.
                latency_ms=0,
                cost=0.0,
                error=f"expansion stage '{failure.node_ref}' failed: {failure.error}",
            )
            await self._record_stage(rolled_up, run_id)
            return rolled_up

        # The composite surfaces exactly what it declared, no more: a caller
        # depending on an intermediate the composite never promised would be
        # depending on its internals.
        declared = (node.output_schema.get("properties") or {}).keys()
        surfaced = {k: available[k] for k in declared if k in available}

        # The composite's own contract is checked too, not just its children's.
        # An expansion where every stage passed but the promised output never
        # materialised is a broken workflow definition, and it is the kind
        # that looks fine right up until a caller reads a missing key.
        violations = validate_value(surfaced, node.output_schema, f"{node.ref}.output")
        violations += evaluate_criteria(node.success_criteria, surfaced)
        # cost and latency are deliberately zero on the roll-up. Its children
        # each wrote their own trace row, and both `load_run` and `list_runs`
        # total a run by summing every row -- so carrying the children's sum
        # here again would double every figure the run view reports, which is
        # the one number a reader trusts. The composite's own contribution to
        # cost is genuinely nothing: it routes no implementation.
        rolled_up = StageResult(
            node_ref=node.ref,
            task_name=node.name,
            task_node_id=node.existing_task_id,
            outcome="failure" if violations else "success",
            latency_ms=0,
            cost=0.0,
            error="; ".join(violations[:5]) if violations else None,
            output={} if violations else surfaced,
        )
        # A composite is a stage, so it gets a trace row like any other.
        # `load_run` rebuilds the run view from traces, so without this the
        # roll-up -- and the reason a workflow failed its own contract --
        # never appears on GET /v1/runs/{id}.
        await self._record_stage(rolled_up, run_id)
        return rolled_up

    # ------------------------------------------------------------------

    async def _run_leaf(
        self,
        node: PlanNode,
        *,
        available: dict[str, Any],
        workdir: Path,
        run_id: Optional[UUID],
        quality_bar: Optional[float],
        max_cost: Optional[float],
        parent_trace_id: Optional[UUID] = None,
    ) -> StageResult:
        stage = StageResult(node_ref=node.ref, task_name=node.name)

        task_id = node.existing_task_id
        if task_id is None:
            stage.error = (
                "plan node is not bound to a task node; a plan must be persisted "
                "before it can execute"
            )
            await self._record_stage(stage, run_id)
            return stage
        stage.task_node_id = task_id

        declared = (node.input_schema.get("properties") or {}).keys()
        node_inputs = {name: available[name] for name in declared if name in available}

        problems = validate_value(node_inputs, node.input_schema, f"{node.ref}.input")
        if problems:
            stage.error = "; ".join(problems[:5])
            await self._recorder.record(
                TraceRecord(
                    node_ref=node.ref,
                    outcome="failure",
                    run_id=run_id,
                    task_node_id=task_id,
                    input=node_inputs,
                    error=stage.error,
                    parent_trace_id=parent_trace_id,
                )
            )
            return stage

        fingerprint = fingerprint_inputs(
            _cache_inputs(node, node_inputs), cache_key=node.cache_key
        )
        decision = await self._router.route(
            task_id, fingerprint, quality_bar=quality_bar, max_cost=max_cost
        )
        if not decision.found:
            stage.error = f"no implementation could be routed: {decision.reason}"
            await self._recorder.record(
                TraceRecord(
                    node_ref=node.ref,
                    outcome="failure",
                    run_id=run_id,
                    task_node_id=task_id,
                    input=node_inputs,
                    error=stage.error,
                    parent_trace_id=parent_trace_id,
                )
            )
            return stage

        return await self._attempt_with_escalation(
            node=node,
            task_id=task_id,
            node_inputs=node_inputs,
            fingerprint=fingerprint,
            decision=decision,
            stage=stage,
            workdir=workdir,
            run_id=run_id,
            quality_bar=quality_bar,
            max_cost=max_cost,
            parent_trace_id=parent_trace_id,
        )

    async def _attempt_with_escalation(
        self,
        *,
        node: PlanNode,
        task_id: UUID,
        node_inputs: dict[str, Any],
        fingerprint: str,
        decision: RouteDecision,
        stage: StageResult,
        workdir: Path,
        run_id: Optional[UUID],
        quality_bar: Optional[float],
        max_cost: Optional[float],
        parent_trace_id: Optional[UUID],
    ) -> StageResult:
        max_attempts = 1 + settings.max_escalations
        queue: list[Implementation] = [decision.selected]  # type: ignore[list-item]
        queue.extend(decision.alternatives)
        tried: set[UUID] = set()
        cache_hit = decision.from_cache
        params = dict(decision.params)
        last_error = "no attempt was made"

        attempt = 0
        while attempt < max_attempts:
            if not queue:
                if cache_hit and not tried:
                    break  # nothing to escalate to
                # A cache hit returns no alternatives by design -- the point
                # of a hit is to skip candidate enumeration entirely. Once it
                # fails, that saving is already spent, so re-route properly.
                fresh = await self._router.route(
                    task_id, fingerprint, quality_bar=quality_bar,
                    max_cost=max_cost, ignore_cache=True,
                )
                if not fresh.found:
                    break
                queue = [i for i in [fresh.selected, *fresh.alternatives] if i and i.id not in tried]
                cache_hit = False
                if not queue:
                    break

            implementation = queue.pop(0)
            if implementation.id in tried:
                continue
            tried.add(implementation.id)
            attempt += 1

            hydrated = await self._hydrate(implementation)
            if hydrated is None:
                last_error = "routed implementation no longer exists"
                await self._record_attempt(
                    node, task_id, implementation, attempt, node_inputs, {},
                    "failure", last_error, cache_hit, 0.0, 0, run_id, parent_trace_id,
                )
                cache_hit = False
                stage.attempts = attempt
                continue
            implementation = hydrated

            blocked = await self._first_layout_gate(implementation, task_id, fingerprint)
            if blocked:
                last_error = blocked
                await self._record_attempt(
                    node, task_id, implementation, attempt, node_inputs, {},
                    "failure", blocked, cache_hit, 0.0, 0, run_id, parent_trace_id,
                )
                cache_hit = False
                continue

            stage.implementation_id = implementation.id
            stage.implementation_name = implementation.name
            stage.implementation_kind = implementation.kind
            stage.attempts = attempt
            stage.cache_hit = cache_hit

            started = time.monotonic()
            output: dict[str, Any] = {}
            cost = 0.0
            error: Optional[str] = None

            runner = self._runners.get(implementation.kind)
            if runner is None:
                error = f"no runner registered for kind '{implementation.kind}'"
            else:
                ctx = RunContext(
                    workdir=workdir,
                    node_ref=node.ref,
                    task_name=node.name,
                    output_schema=node.output_schema,
                    run_id=run_id,
                    params=params,
                )
                try:
                    result = await runner.run(dict(implementation.spec), node_inputs, ctx)
                    output, cost = result.output, result.cost
                    params = {**params, **result.params}
                except RunnerError as exc:
                    error = str(exc)
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"

            elapsed = int((time.monotonic() - started) * 1000)

            if error is None:
                violations = validate_value(output, node.output_schema, f"{node.ref}.output")
                violations += evaluate_criteria(node.success_criteria, output)
                if violations:
                    error = "; ".join(violations[:5])

            stage.cost += cost
            stage.latency_ms += elapsed

            await self._record_attempt(
                node, task_id, implementation, attempt, node_inputs, output,
                "failure" if error else "success", error, cache_hit, cost, elapsed,
                run_id, parent_trace_id,
            )

            if error is None:
                stage.outcome = "success"
                stage.output = output
                stage.error = None
                await self._write_cache(
                    task_id, fingerprint, await self._cache_target(implementation), params
                )
                return stage

            last_error = error
            log.info(
                "stage %s attempt %d via %s failed: %s",
                node.ref, attempt, implementation.name, error,
            )
            # A cached route that failed is no longer a cached route.
            cache_hit = False

        stage.outcome = "failure"
        stage.error = (
            f"{last_error} (gave up after {attempt} attempt"
            f"{'s' if attempt != 1 else ''}; cap is {max_attempts})"
        )
        return stage

    # ------------------------------------------------------------------

    async def _hydrate(self, implementation: Implementation) -> Optional[Implementation]:
        """
        A cache hit returns an id, not a spec. Fetch the real row before running.
        """
        if implementation.spec or implementation.name != "(cached)":
            return implementation
        if self._store is None:
            return None
        return await self._store.implementation_by_id(implementation.id)

    async def _first_layout_gate(
        self, implementation: Implementation, task_id: UUID, fingerprint: str
    ) -> Optional[str]:
        """
        Hold back implementations marked as needing a reviewed layout.

        Set `"first_layout_requires_review": true` on an implementation's spec
        and it will not run against a layout the cache has never seen. This is
        the seam for the map_to_schema question -- the one stage where a
        wrong-but-plausible answer is both likely and invisible downstream --
        without hard-coding a task name into the executor.
        """
        if not implementation.spec.get("first_layout_requires_review"):
            return None
        if settings.allow_unreviewed_first_layout_mapping:
            return None
        if self._cache is None:
            return None
        if await self._cache.has_entry(task_id, fingerprint):
            return None
        return (
            f"'{implementation.name}' is marked as requiring a reviewed layout and this "
            f"layout has not been seen before. Set "
            f"ALLOW_UNREVIEWED_FIRST_LAYOUT_MAPPING=true to permit it."
        )

    async def _cache_target(self, implementation: Implementation) -> UUID:
        """
        Which implementation the cache should record for this layout.

        Normally the one that just succeeded. An implementation may name a
        cheaper stand-in with `"cache_as": "<name>"` -- "I worked out what
        this layout needs; next time run that instead, with my params". This
        is how a stage that costs a model call once costs nothing thereafter,
        and it is the difference between the cache saving a routing lookup
        and the cache saving the actual money.
        """
        name = implementation.spec.get("cache_as")
        if not name:
            return implementation.id

        lookup = getattr(self._store, "implementation_by_name", None)
        if lookup is None:
            return implementation.id

        replacement = await lookup(implementation.task_node_id, str(name))
        if replacement is None or not replacement.enabled:
            log.warning(
                "implementation %s names cache_as='%s', which is not an enabled "
                "implementation of the same task; caching itself instead",
                implementation.name, name,
            )
            return implementation.id
        return replacement.id

    async def _record_stage(self, stage: StageResult, run_id: Optional[UUID]) -> None:
        """Trace a stage that isn't a routed attempt -- currently composites."""
        await self._recorder.record(
            TraceRecord(
                node_ref=stage.node_ref,
                outcome=stage.outcome,
                run_id=run_id,
                task_node_id=stage.task_node_id,
                output=stage.output,
                error=stage.error,
                cost=stage.cost,
                latency_ms=stage.latency_ms,
            )
        )

    async def _record_attempt(
        self, node, task_id, implementation, attempt, inputs, output,
        outcome, error, cache_hit, cost, latency_ms, run_id, parent_trace_id,
    ) -> None:
        await self._recorder.record(
            TraceRecord(
                node_ref=node.ref,
                outcome=outcome,
                run_id=run_id,
                task_node_id=task_id,
                implementation_id=implementation.id if implementation else None,
                attempt=attempt,
                input=inputs,
                output=output,
                error=error,
                cache_hit=cache_hit,
                cost=cost,
                latency_ms=latency_ms,
                parent_trace_id=parent_trace_id,
            )
        )

    async def _write_cache(
        self, task_id: UUID, fingerprint: str, implementation_id: UUID, params: dict
    ) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.write(task_id, fingerprint, implementation_id, params)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not write cache entry for %s: %s", task_id, exc)

    @staticmethod
    def _workdir(run_id: Optional[UUID]) -> Path:
        """
        Where a run's files live.

        Default keeps them under `artifact_root/<run_id>`: a run whose output
        file was deleted with the temp directory before anyone fetched it is
        a failed run that reports success, and .xlsx files are the product
        here, not scratch. Set KEEP_RUN_ARTIFACTS=false for a temp directory.
        """
        if settings.keep_run_artifacts:
            directory = Path(settings.artifact_root) / str(run_id or f"adhoc-{uuid.uuid4()}")
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        return Path(tempfile.mkdtemp(prefix="plat_v1_"))
