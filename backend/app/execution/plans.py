"""
Band 1.7 -- plan persistence boundary: compile, freeze, rebind.

The storage half is db/23_plan_persistence.sql (execution_plans /
task_graphs / executions, all frozen by trigger); this module is the
boundary half that produces and checks what those tables hold:

  invariant #1   every execution references an exact ExecutionPlan
                 -> validate_execution_binding() refuses any payload
                 without one; the table itself leaves no nullable path.
  invariant #2   every ExecutionPlan references an exact Procedure
                 version -> validate_procedure_ref() rejects versionless
                 references; migration 23's composite FK makes a dangling
                 one unwritable even by direct SQL.
  invariant #17  instantiation never silently modifies the source
                 Procedure -> compile_plan() fingerprints the procedure
                 payload it read (hash_procedure_version); replay calls
                 assert_procedure_unmodified() before rebinding.

Freeze semantics (why hashing looks the way it does): a plan's identity
is its semantic content. content_hash covers everything that DEFINES the
plan and excludes everything that merely LOCATES it (ids, timestamps,
visibility, owner) -- so two compiles from identical inputs produce the
IDENTICAL hash, which is what lets a replay rebind the existing frozen
plan instead of forking a phantom twin. Changed inputs of any kind mean
a new hash, hence a new plan row; editing a frozen one is impossible by
trigger and meaningless by design (schema.md: changed inputs mean
regenerate a new DAG, never edit one).

Pure functions only: nothing here touches a pool or connection, which is
what makes the whole contract provable offline (tests/test_band1_7_plans.py).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence
from uuid import UUID, uuid4

from app.models.plan import (
    ExecutionPlan,
    PlanNode,
    ProcedureRef,
    ResolvedClaimRef,
    TaskGraph,
)
from app.services.v0_gate import SCOPE_TYPES

# Narrowness ladder, index = rank: a node's scope may equal its plan's
# scope or sit STRICTLY FURTHER DOWN this ladder (spec 24: nodes inherit
# plan scope, may narrow it, never widen). The ladder is v0_gate's own
# canonical ordering -- global is widest, entity is most granular; the
# spec's worked example ("repository-level plan, one high-risk node
# gated to user-scope approval") is repository(rank 4) -> user(rank 6),
# consistent with this order.
SCOPE_NARROWNESS: tuple[str, ...] = SCOPE_TYPES

SAFETY_CHECKS = ("passed", "failed", "requires_review")
NODE_CLASSES = ("predictable", "uncertain", "high-risk")


class PlanViolation(ValueError):
    """A payload would bind an execution to something other than an
    exact frozen plan, or a compile would violate the exact-version /
    never-widen contracts. Callers surface this verbatim: it is a
    producer-side contract violation, not an internal error."""


# --------------------------------------------------------------- hashing


def canonical_json(payload: Any) -> str:
    """Deterministic serialization: key order and whitespace can never
    change a hash. UUIDs/datetimes stringify; callers pass JSON-ready
    payloads for anything more exotic."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_procedure_version(procedure_payload: Mapping[str, Any]) -> str:
    """Fingerprint of one procedure version's CONTENT (invariant #17).

    Takes whatever dict-shaped snapshot the caller reads from the
    procedures row -- the hash is over content, so it works identically
    against an ORM-ish dict, asyncpg Record(as_dict()), or a fixture."""
    return sha256_hex(canonical_json(procedure_payload))


# ------------------------------------------------------------ validators


def validate_procedure_ref(procedure_id: UUID | None, version: int | None) -> ProcedureRef:
    """Invariant #2: no versionless references accepted, ever."""
    if not procedure_id:
        raise PlanViolation("V-PLAN: procedure_id is required -- a plan cannot reference an anonymous procedure")
    if version is None:
        raise PlanViolation(
            "V-PLAN: procedure version is required -- versionless references "
            "are rejected (invariant #2)"
        )
    try:
        return ProcedureRef(procedure_id=procedure_id, version=int(version))
    except ValueError as exc:
        raise PlanViolation(f"V-PLAN: invalid procedure reference ({exc})") from exc


def _scope_rank(scope_type: str) -> int:
    try:
        return SCOPE_NARROWNESS.index(scope_type)
    except ValueError:
        raise PlanViolation(f"V-PLAN: unknown scope_type {scope_type!r}") from None


def validate_node_scope(
    plan_scope_type: Optional[str],
    plan_scope_entity: Optional[str],
    node_scope_type: Optional[str],
    node_scope_entity: Optional[str],
    *, where: str,
) -> None:
    """Nodes inherit plan scope; explicit node scope may narrow, never
    widen (spec 24). With no plan scope recorded, an explicit node scope
    would be widening against an unrecorded baseline and is refused --
    record the plan's scope instead."""
    if node_scope_type is None:
        return  # inherits the plan -- the common case
    if plan_scope_type is None:
        raise PlanViolation(
            f"V-PLAN: {where} sets explicit scope but its plan records none -- "
            "record the plan scope first; a node cannot narrow what was never stated"
        )
    if _scope_rank(node_scope_type) < _scope_rank(plan_scope_type):
        raise PlanViolation(
            f"V-PLAN: {where} widens scope ({node_scope_type} above plan "
            f"{plan_scope_type}) -- nodes may narrow plan scope, never widen it"
        )
    if node_scope_type == plan_scope_type:
        if (node_scope_entity or None) != (plan_scope_entity or None):
            raise PlanViolation(
                f"V-PLAN: {where} switches entity at equal scope granularity "
                f"({plan_scope_entity!r} -> {node_scope_entity!r}) -- sideways moves "
                "are not narrowing"
            )
        return
    if node_scope_type != "global" and not node_scope_entity:
        raise PlanViolation(f"V-PLAN: {where} narrows scope to {node_scope_type} but carries no entity_id")


def _as_node(raw: PlanNode | Mapping[str, Any], index: int) -> PlanNode:
    if isinstance(raw, PlanNode):
        return raw
    try:
        return PlanNode(**raw)
    except Exception as exc:
        raise PlanViolation(f"V-PLAN: node[{index}] is not a valid TaskNode ({exc})") from exc


def _check_acyclic(nodes: Sequence[PlanNode]) -> None:
    """Scheduling edges exist ONLY in node deps; they must form a DAG.
    Kahn's algorithm -- every cycle leaves indegree>0 nodes stranded."""
    by_order = {n.order: n for n in nodes}
    indegree = {n.order: 0 for n in nodes}
    dependents: dict[int, list[int]] = {n.order: [] for n in nodes}
    for n in nodes:
        for d in n.deps:
            indegree[n.order] += 1
            dependents[d].append(n.order)
    ready = [o for o, deg in indegree.items() if deg == 0]
    settled = 0
    while ready:
        o = ready.pop()
        settled += 1
        for dep in dependents[o]:
            indegree[dep] -= 1
            if indegree[dep] == 0:
                ready.append(dep)
    if settled != len(nodes):
        stranded = sorted(o for o, deg in indegree.items() if deg > 0)
        raise PlanViolation(f"V-PLAN: dependency cycle strands node(s) {stranded} -- scheduling edges must form a DAG")


def validate_graph(nodes: Sequence[PlanNode | Mapping[str, Any]]) -> list[PlanNode]:
    """Structural validation shared by compile-time and any direct-SQL
    auditor: unique orders, resolvable non-self deps, DAG, legal
    node_class. Scope narrowing needs the plan scope and lives in
    compile_plan()."""
    if not nodes:
        raise PlanViolation("V-PLAN: a plan needs at least one task node -- an empty graph cannot execute")
    normalized = [_as_node(raw, i) for i, raw in enumerate(nodes)]
    orders = [n.order for n in normalized]
    if len(set(orders)) != len(orders):
        raise PlanViolation("V-PLAN: duplicate node orders -- a node's identity within its graph is its order")
    for n in normalized:
        if not n.goal.strip():
            raise PlanViolation(f"V-PLAN: node {n.order} has an empty goal")
        for d in n.deps:
            if d == n.order:
                raise PlanViolation(f"V-PLAN: node {n.order} depends on itself")
            if d not in set(orders):
                raise PlanViolation(
                    f"V-PLAN: node {n.order} depends on {d}, which is not in its graph"
                )
        if n.node_class not in NODE_CLASSES:
            raise PlanViolation(
                f"V-PLAN: node {n.order} has unknown node_class {n.node_class!r} (valid: {NODE_CLASSES})"
            )
    _check_acyclic(normalized)
    return normalized


# -------------------------------------------------------------- compile


@dataclass(frozen=True)
class CompiledPlan:
    """What a compile hands back: the frozen pair, already linked, ready
    for two INSERTs. No UPDATE path exists or may be added."""

    plan: ExecutionPlan
    graph: TaskGraph


def _graph_content(nodes: Sequence[PlanNode]) -> dict[str, Any]:
    return {"nodes": [n.model_dump(mode="json") for n in nodes]}


def _plan_content(
    *,
    procedure: ProcedureRef,
    procedure_content_hash: str,
    scope_type: Optional[str],
    scope_entity_id: Optional[str],
    task_description: str,
    parameters: Mapping[str, Any],
    starting_state_id: Optional[UUID],
    resolved_claims: Sequence[ResolvedClaimRef],
    selected_branches: Sequence[str],
    implementations: Mapping[str, str],
    safety_check: Optional[str],
    verification_plan: Mapping[str, Any],
    graph_hash: str,
    extractor_version: str,
) -> dict[str, Any]:
    return {
        "v": 1,  # content-shape version; bump if the recipe below ever changes meaning
        "procedure": {"id": str(procedure.procedure_id), "version": procedure.version},
        "procedure_content_hash": procedure_content_hash,
        "scope": {"type": scope_type, "entity_id": scope_entity_id},
        "task": {"description": task_description},
        "parameters": parameters,
        "starting_state_id": starting_state_id,
        "resolved_claims": [c.model_dump(mode="json") for c in resolved_claims],
        "selected_branches": list(selected_branches),
        "implementations": dict(implementations),
        "safety_check": safety_check,
        "verification_plan": verification_plan,
        "graph_hash": graph_hash,
        "extractor_version": extractor_version,
    }


def compile_plan(
    *,
    procedure_id: UUID,
    procedure_version: int,
    procedure_row_id: UUID,
    procedure_payload: Mapping[str, Any],
    task_description: str,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    parameters: Optional[Mapping[str, Any]] = None,
    starting_state_id: Optional[UUID] = None,
    resolved_claims: Sequence[ResolvedClaimRef | Mapping[str, Any]] = (),
    selected_branches: Sequence[str] = (),
    implementations: Optional[Mapping[str, str]] = None,
    safety_check: Optional[str] = None,
    verification_plan: Optional[Mapping[str, Any]] = None,
    nodes: Sequence[PlanNode | Mapping[str, Any]] = (),
    extractor_version: str,
    created_by: Optional[str] = None,
    visibility: str = "public",
    owner_id: Optional[str] = None,
    plan_id: Optional[UUID] = None,
    graph_id: Optional[UUID] = None,
) -> CompiledPlan:
    """Instantiate ONE procedure version into ONE frozen plan + graph.

    Deterministic: identical inputs produce identical content_hash /
    graph_hash regardless of generated ids, so a replay can rebind the
    original rows (find_rebindable_plan). Never mutates procedure_payload
    (invariant #17) -- proven by test, not promised by comment."""
    if not task_description or not task_description.strip():
        raise PlanViolation("V-PLAN: task_description is required -- a plan compiles FOR a task")
    if not extractor_version or not extractor_version.strip():
        raise PlanViolation(
            "V-PLAN: derived objects require extractor_version (V0 gate) -- "
            "stamp the compiler as e.g. 'plan_compiler@1'"
        )
    if safety_check is not None and safety_check not in SAFETY_CHECKS:
        raise PlanViolation(f"V-PLAN: unknown safety_check {safety_check!r} (valid: {SAFETY_CHECKS})")

    procedure = validate_procedure_ref(procedure_id, procedure_version)

    claims: list[ResolvedClaimRef] = []
    for i, c in enumerate(resolved_claims):
        try:
            claims.append(c if isinstance(c, ResolvedClaimRef) else ResolvedClaimRef(**c))
        except Exception as exc:
            raise PlanViolation(f"V-PLAN: resolved_claims[{i}] must be {{claim_id, version}} ({exc})") from exc

    checked = validate_graph(nodes)
    for i, n in enumerate(checked):
        validate_node_scope(
            scope_type, scope_entity_id, n.scope_type, n.scope_entity_id,
            where=f"node[{i}] (order {n.order})",
        )

    procedure_content_hash = hash_procedure_version(procedure_payload)

    graph = TaskGraph(
        id=graph_id or uuid4(),
        execution_plan_id=UUID(int=0),  # linkage, set below; never hashed
        graph_hash="",
        nodes=checked,
        created_by=created_by,
        visibility=visibility,  # type: ignore[arg-type]
        owner_id=owner_id,
    )
    graph_hash = sha256_hex(canonical_json(_graph_content(graph.nodes)))

    content_hash = sha256_hex(
        canonical_json(_plan_content(
            procedure=procedure,
            procedure_content_hash=procedure_content_hash,
            scope_type=scope_type,
            scope_entity_id=scope_entity_id,
            task_description=task_description,
            parameters=dict(parameters or {}),
            starting_state_id=starting_state_id,
            resolved_claims=claims,
            selected_branches=list(selected_branches),
            implementations=dict(implementations or {}),
            safety_check=safety_check,
            verification_plan=dict(verification_plan or {}),
            graph_hash=graph_hash,
            extractor_version=extractor_version,
        ))
    )

    plan = ExecutionPlan(
        id=plan_id or uuid4(),
        procedure=procedure,
        procedure_row_id=procedure_row_id,
        task_graph_id=graph.id,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        task_description=task_description,
        parameters=dict(parameters or {}),
        starting_state_id=starting_state_id,
        resolved_claims=claims,
        selected_branches=list(selected_branches),
        implementations=dict(implementations or {}),
        safety_check=safety_check,  # type: ignore[arg-type]
        verification_plan=dict(verification_plan or {}),
        extractor_version=extractor_version,
        procedure_content_hash=procedure_content_hash,
        content_hash=content_hash,
        created_by=created_by,
        visibility=visibility,  # type: ignore[arg-type]
        owner_id=owner_id,
    )
    graph.execution_plan_id = plan.id
    return CompiledPlan(plan=plan, graph=graph)


# --------------------------------------------------------------- rebind


def find_rebindable_plan(
    candidates: Iterable[Mapping[str, Any]], compiled: CompiledPlan
) -> Optional[Mapping[str, Any]]:
    """Replay support: given stored execution_plans rows, return the one
    whose content_hash equals this compile's -- the IDENTICAL plan to
    rebind -- or None when history has no such plan yet. Ids never enter
    the hash, which is precisely what makes 'same inputs => same plan'
    hold across processes."""
    for row in candidates:
        if row.get("content_hash") == compiled.plan.content_hash:
            return row
    return None


def assert_procedure_unmodified(stored_hash: str, current_hash: str) -> None:
    """Invariant #17's replay gate: instantiation never silently modifies
    the source Procedure. A mismatch means the procedure version drifted
    after some plan was compiled from it -- refuse to rebind, and treat
    the drift as requiring a new procedure version (never an edit)."""
    if stored_hash != current_hash:
        raise PlanViolation(
            "V-PLAN: source procedure version changed since compilation "
            "(stored fingerprint != current) -- silent modification refused; "
            "issue a new procedure version instead"
        )


# ------------------------------------------------------------- bindings


def validate_execution_binding(
    *,
    execution_plan_id: UUID | None,
    task_graph_id: UUID | None,
    procedure_id: UUID,
    procedure_version: int,
    plan: Optional[ExecutionPlan] = None,
) -> dict[str, Any]:
    """Invariant #1 boundary: an execution payload without an exact plan
    (and its graph) is not representable. When the caller has the plan
    loaded, the denormalized procedure snapshot is cross-checked against
    it -- a self-describing execution must describe ITS OWN plan."""
    if not execution_plan_id:
        raise PlanViolation(
            "V-PLAN: execution_plan_id is required -- invariant #1 allows no "
            "planless executions"
        )
    if not task_graph_id:
        raise PlanViolation(
            "V-PLAN: task_graph_id is required -- every execution names the exact "
            "graph it ran (spec 22)"
        )
    ref = validate_procedure_ref(procedure_id, procedure_version)
    if plan is not None:
        if plan.id != execution_plan_id:
            raise PlanViolation(
                "V-PLAN: binding names a different plan than the one supplied "
                f"(payload {execution_plan_id} != plan {plan.id})"
            )
        if plan.task_graph_id != task_graph_id:
            raise PlanViolation(
                "V-PLAN: binding names a graph its plan does not own "
                f"(payload {task_graph_id} != plan's {plan.task_graph_id})"
            )
        if plan.procedure != ref:
            raise PlanViolation(
                "V-PLAN: execution's procedure snapshot disagrees with its plan "
                f"({ref} vs plan's {plan.procedure}) -- record the plan's exact version"
            )
    return {
        "execution_plan_id": execution_plan_id,
        "task_graph_id": task_graph_id,
        "procedure_id": ref.procedure_id,
        "procedure_version": ref.version,
    }
