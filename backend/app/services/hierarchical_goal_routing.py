"""Pure, bounded hierarchical Goal candidate routing.

Semantic search anchors and hierarchy-expanded candidates remain separate. Accepted
``SPECIALIZES`` edges can add bounded parent or child Goal candidates, but this module
never changes relevance scores, evaluates Procedure applicability, ranks globally, or
builds a transitive closure. Every expanded candidate carries the exact accepted-edge path
that introduced it and a flat fallback is reported explicitly when hierarchy contributes
nothing.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal


GoalId = str
TraversalDirection = Literal["parents", "children", "both"]
CandidateOrigin = Literal["semantic_anchor", "graph"]
RoutingMode = Literal["flat", "hierarchical"]

DEFAULT_MAX_HOPS = 1
DEFAULT_MAX_FANOUT = 8
DEFAULT_MAX_GRAPH_CANDIDATES = 64
MAX_SUPPORTED_HOPS = 8

_TRUNCATION_REASONS = frozenset(
    {
        "hop_limit_reached",
        "fanout_limit_reached",
        "graph_candidate_limit_reached",
    }
)


@dataclass(frozen=True)
class HierarchicalGoalRoutingConfig:
    """Caller-controlled limits for deterministic, non-transitive expansion."""

    max_hops: int = DEFAULT_MAX_HOPS
    max_fanout: int = DEFAULT_MAX_FANOUT
    max_graph_candidates: int = DEFAULT_MAX_GRAPH_CANDIDATES
    expand_parents: bool = True
    expand_children: bool = True

    def __post_init__(self) -> None:
        if type(self.max_hops) is not int or not 0 <= self.max_hops <= MAX_SUPPORTED_HOPS:
            raise ValueError(
                f"max_hops must be an integer from 0 through {MAX_SUPPORTED_HOPS}"
            )
        if type(self.max_fanout) is not int or self.max_fanout < 1:
            raise ValueError("max_fanout must be a positive integer")
        if type(self.max_graph_candidates) is not int or self.max_graph_candidates < 0:
            raise ValueError("max_graph_candidates must be a non-negative integer")
        if type(self.expand_parents) is not bool or type(self.expand_children) is not bool:
            raise ValueError("expand_parents and expand_children must be booleans")


@dataclass(frozen=True)
class GoalRelationProvenance:
    """One accepted direct edge and the exact bounded path that used it."""

    from_goal_id: GoalId
    to_goal_id: GoalId
    direction: Literal["parent", "child"]
    hop: int
    abstraction_path: tuple[GoalId, ...]
    relation_type: str
    status: Literal["accepted"]
    edge_id: str | None = None
    relation_id: str | None = None
    relation_version_id: str | None = None
    decision_id: str | None = None
    source_ids: tuple[str, ...] = ()
    provenance: str | None = None
    confidence: float | None = None

    @property
    def specific_goal_id(self) -> GoalId:
        if self.direction == "parent":
            return self.from_goal_id
        return self.to_goal_id

    @property
    def abstract_goal_id(self) -> GoalId:
        if self.direction == "parent":
            return self.to_goal_id
        return self.from_goal_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_goal_id": self.from_goal_id,
            "to_goal_id": self.to_goal_id,
            "specific_goal_id": self.specific_goal_id,
            "abstract_goal_id": self.abstract_goal_id,
            "direction": self.direction,
            "hop": self.hop,
            "abstraction_path": list(self.abstraction_path),
            "relation_type": self.relation_type,
            "status": self.status,
            "edge_id": self.edge_id,
            "relation_id": self.relation_id,
            "relation_version_id": self.relation_version_id,
            "decision_id": self.decision_id,
            "source_ids": list(self.source_ids),
            "provenance": self.provenance,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class RoutedGoalCandidate:
    """A semantic anchor or graph-expanded Goal kept in an explicit source lane."""

    candidate: dict[str, Any]
    origin: CandidateOrigin
    abstraction_path: tuple[GoalId, ...]
    relation_provenance: tuple[GoalRelationProvenance, ...] = ()

    @property
    def goal_id(self) -> GoalId | None:
        return _goal_id(self.candidate)

    @property
    def score(self) -> Any:
        return self.candidate.get("score")

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate": dict(self.candidate),
            "origin": self.origin,
            "abstraction_path": list(self.abstraction_path),
            "relation_provenance": [
                item.as_dict() for item in self.relation_provenance
            ],
        }


@dataclass(frozen=True)
class HierarchicalGoalRoutingDiagnostics:
    """Explicit flat-fallback, bounded-coverage, and relation-filter diagnostics."""

    mode: RoutingMode
    used_flat_fallback: bool
    fallback_reasons: tuple[str, ...]
    degraded: bool
    degraded_reasons: tuple[str, ...]
    truncated: bool
    semantic_anchor_count: int
    graph_candidate_count: int
    input_relation_count: int
    accepted_relation_count: int
    relevant_accepted_relation_count: int
    ignored_relation_statuses: dict[str, int]
    ignored_relation_types: dict[str, int]
    invalid_relation_count: int
    anchors_missing_goal_id: int
    limits: HierarchicalGoalRoutingConfig

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "used_flat_fallback": self.used_flat_fallback,
            "fallback_reasons": list(self.fallback_reasons),
            "degraded": self.degraded,
            "degraded_reasons": list(self.degraded_reasons),
            "truncated": self.truncated,
            "semantic_anchor_count": self.semantic_anchor_count,
            "graph_candidate_count": self.graph_candidate_count,
            "input_relation_count": self.input_relation_count,
            "accepted_relation_count": self.accepted_relation_count,
            "relevant_accepted_relation_count": self.relevant_accepted_relation_count,
            "ignored_relation_statuses": dict(self.ignored_relation_statuses),
            "ignored_relation_types": dict(self.ignored_relation_types),
            "invalid_relation_count": self.invalid_relation_count,
            "anchors_missing_goal_id": self.anchors_missing_goal_id,
            "limits": {
                "max_hops": self.limits.max_hops,
                "max_fanout": self.limits.max_fanout,
                "max_graph_candidates": self.limits.max_graph_candidates,
                "expand_parents": self.limits.expand_parents,
                "expand_children": self.limits.expand_children,
            },
        }


@dataclass(frozen=True)
class HierarchicalGoalRoutingResult:
    """Anchors remain first and distinct from all hierarchy-only candidates."""

    semantic_anchors: tuple[RoutedGoalCandidate, ...]
    graph_candidates: tuple[RoutedGoalCandidate, ...]
    diagnostics: HierarchicalGoalRoutingDiagnostics

    @property
    def candidates(self) -> tuple[RoutedGoalCandidate, ...]:
        return self.semantic_anchors + self.graph_candidates

    def as_dict(self) -> dict[str, Any]:
        return {
            "semantic_anchors": [item.as_dict() for item in self.semantic_anchors],
            "graph_candidates": [item.as_dict() for item in self.graph_candidates],
            "candidates": [item.as_dict() for item in self.candidates],
            "diagnostics": self.diagnostics.as_dict(),
        }


@dataclass(frozen=True)
class GoalTraversalPath:
    """One bounded parent or child path rooted at a semantic anchor."""

    goal_id: GoalId
    abstraction_path: tuple[GoalId, ...]
    relation_provenance: tuple[GoalRelationProvenance, ...]

    @property
    def depth(self) -> int:
        return len(self.abstraction_path) - 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "depth": self.depth,
            "abstraction_path": list(self.abstraction_path),
            "relation_provenance": [
                item.as_dict() for item in self.relation_provenance
            ],
        }


@dataclass(frozen=True)
class GoalTraversalResult:
    """Standalone multi-hop traversal output with explicit coverage diagnostics."""

    paths: tuple[GoalTraversalPath, ...]
    degraded: bool
    degraded_reasons: tuple[str, ...]
    truncated: bool
    input_relation_count: int
    accepted_relation_count: int
    ignored_relation_statuses: dict[str, int]
    ignored_relation_types: dict[str, int]
    invalid_relation_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "paths": [path.as_dict() for path in self.paths],
            "degraded": self.degraded,
            "degraded_reasons": list(self.degraded_reasons),
            "truncated": self.truncated,
            "input_relation_count": self.input_relation_count,
            "accepted_relation_count": self.accepted_relation_count,
            "ignored_relation_statuses": dict(self.ignored_relation_statuses),
            "ignored_relation_types": dict(self.ignored_relation_types),
            "invalid_relation_count": self.invalid_relation_count,
        }


@dataclass(frozen=True)
class _AcceptedGoalEdge:
    specific_goal_id: GoalId
    abstract_goal_id: GoalId
    edge_id: str | None
    relation_id: str | None
    relation_version_id: str | None
    decision_id: str | None
    source_ids: tuple[str, ...]
    provenance: str | None
    confidence: float | None
    specific_goal: Mapping[str, Any] | None
    abstract_goal: Mapping[str, Any] | None


@dataclass(frozen=True)
class _PreparedRelations:
    input_count: int
    accepted: tuple[_AcceptedGoalEdge, ...]
    ignored_statuses: dict[str, int]
    ignored_types: dict[str, int]
    invalid_count: int
    invalid_reasons: tuple[str, ...]
    node_payloads: dict[GoalId, Mapping[str, Any]]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _goal_id(candidate: Mapping[str, Any]) -> GoalId | None:
    return _text(candidate.get("goal_id")) or _text(candidate.get("id"))


def _count(mapping: dict[str, int], key: str) -> None:
    mapping[key] = mapping.get(key, 0) + 1


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _confidence(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return confidence if isfinite(confidence) else None


def _source_ids(edge: Mapping[str, Any]) -> tuple[str, ...]:
    raw = edge.get("source_ids")
    if raw is None:
        raw = edge.get("source_id")
    if raw is None:
        return ()
    values = raw if isinstance(raw, (list, tuple, set, frozenset)) else (raw,)
    rendered = {value for value in (_text(item) for item in values) if value is not None}
    return tuple(sorted(rendered))


def _endpoint_payload(
    edge: Mapping[str, Any],
    *,
    specific: bool,
) -> Mapping[str, Any] | None:
    keys = (
        ("specific_goal", "specific_goal_record", "specific_candidate")
        if specific
        else ("abstract_goal", "abstract_goal_record", "abstract_candidate")
    )
    for key in keys:
        value = edge.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _prepare_relations(
    relation_edges: Iterable[Mapping[str, Any]],
) -> _PreparedRelations:
    accepted: list[_AcceptedGoalEdge] = []
    ignored_statuses: dict[str, int] = {}
    ignored_types: dict[str, int] = {}
    invalid_reasons: list[str] = []
    node_payloads: dict[GoalId, Mapping[str, Any]] = {}
    input_count = 0
    invalid_count = 0

    for edge in relation_edges:
        input_count += 1
        if not isinstance(edge, Mapping):
            invalid_count += 1
            _append_reason(invalid_reasons, "invalid_relation_row")
            continue

        status_key = _text(edge.get("status")) or "<missing>"
        normalized_status = status_key.lower()
        if normalized_status != "accepted":
            _count(ignored_statuses, normalized_status)
            continue

        relation_type = _text(edge.get("relation_type"))
        if relation_type is None:
            relation_type_key = "<missing>"
        else:
            relation_type_key = relation_type
        if relation_type is None or relation_type.upper() != "SPECIALIZES":
            _count(ignored_types, relation_type_key)
            continue

        specific_goal_id = _text(edge.get("specific_goal_id"))
        abstract_goal_id = _text(edge.get("abstract_goal_id"))
        if specific_goal_id is None or abstract_goal_id is None:
            invalid_count += 1
            _append_reason(invalid_reasons, "accepted_relation_missing_goal_id")
            continue
        if specific_goal_id == abstract_goal_id:
            invalid_count += 1
            _append_reason(invalid_reasons, "accepted_relation_self_loop")
            continue

        specific_goal = _endpoint_payload(edge, specific=True)
        abstract_goal = _endpoint_payload(edge, specific=False)
        for goal_id, payload in (
            (specific_goal_id, specific_goal),
            (abstract_goal_id, abstract_goal),
        ):
            if payload is None or goal_id in node_payloads:
                continue
            payload_goal_id = _goal_id(payload)
            if payload_goal_id is not None and payload_goal_id != goal_id:
                invalid_count += 1
                _append_reason(invalid_reasons, "relation_goal_payload_id_mismatch")
                continue
            node_payloads[goal_id] = payload

        accepted.append(
            _AcceptedGoalEdge(
                specific_goal_id=specific_goal_id,
                abstract_goal_id=abstract_goal_id,
                edge_id=(
                    _text(edge.get("edge_id"))
                    or _text(edge.get("relation_version_id"))
                    or _text(edge.get("relation_id"))
                    or _text(edge.get("id"))
                ),
                relation_id=_text(edge.get("relation_id")),
                relation_version_id=_text(edge.get("relation_version_id")),
                decision_id=_text(edge.get("decision_id")),
                source_ids=_source_ids(edge),
                provenance=_text(edge.get("provenance")),
                confidence=_confidence(edge.get("confidence")),
                specific_goal=specific_goal,
                abstract_goal=abstract_goal,
            )
        )

    ignored_statuses = {
        key: value for key, value in ignored_statuses.items() if value > 0
    }
    ignored_types = {
        key: value for key, value in ignored_types.items() if value > 0
    }
    return _PreparedRelations(
        input_count=input_count,
        accepted=tuple(accepted),
        ignored_statuses=ignored_statuses,
        ignored_types=ignored_types,
        invalid_count=invalid_count,
        invalid_reasons=tuple(invalid_reasons),
        node_payloads=node_payloads,
    )


def _adjacency(
    edges: tuple[_AcceptedGoalEdge, ...],
    direction: TraversalDirection,
) -> dict[GoalId, list[tuple[GoalId, GoalId, Literal["parent", "child"], _AcceptedGoalEdge]]]:
    adjacency: dict[
        GoalId,
        list[tuple[GoalId, GoalId, Literal["parent", "child"], _AcceptedGoalEdge]],
    ] = {}
    for edge in edges:
        if direction in ("parents", "both"):
            adjacency.setdefault(edge.specific_goal_id, []).append(
                (edge.abstract_goal_id, edge.specific_goal_id, "parent", edge)
            )
        if direction in ("children", "both"):
            adjacency.setdefault(edge.abstract_goal_id, []).append(
                (edge.specific_goal_id, edge.abstract_goal_id, "child", edge)
            )
    return adjacency


def _relation_provenance(
    edge: _AcceptedGoalEdge,
    *,
    from_goal_id: GoalId,
    to_goal_id: GoalId,
    direction: Literal["parent", "child"],
    abstraction_path: tuple[GoalId, ...],
) -> GoalRelationProvenance:
    return GoalRelationProvenance(
        from_goal_id=from_goal_id,
        to_goal_id=to_goal_id,
        direction=direction,
        hop=len(abstraction_path) - 1,
        abstraction_path=abstraction_path,
        relation_type="SPECIALIZES",
        status="accepted",
        edge_id=edge.edge_id,
        relation_id=edge.relation_id,
        relation_version_id=edge.relation_version_id,
        decision_id=edge.decision_id,
        source_ids=edge.source_ids,
        provenance=edge.provenance,
        confidence=edge.confidence,
    )


def _traverse_prepared(
    start_goal_ids: Iterable[GoalId],
    prepared: _PreparedRelations,
    *,
    direction: TraversalDirection,
    config: HierarchicalGoalRoutingConfig,
) -> tuple[tuple[GoalTraversalPath, ...], tuple[str, ...]]:
    adjacency = _adjacency(prepared.accepted, direction)
    starts: list[GoalId] = []
    seen_starts: set[GoalId] = set()
    for raw_start in start_goal_ids:
        start = _text(raw_start)
        if start is None or start in seen_starts:
            continue
        seen_starts.add(start)
        starts.append(start)

    frontier: deque[
        tuple[
            GoalId,
            tuple[GoalId, ...],
            tuple[GoalRelationProvenance, ...],
        ]
    ] = deque((start, (start,), ()) for start in starts)
    paths: list[GoalTraversalPath] = []
    reasons: list[str] = []

    while frontier:
        current_id, current_path, current_provenance = frontier.popleft()
        neighbours = adjacency.get(current_id, ())
        depth = len(current_path) - 1
        if depth >= config.max_hops:
            if neighbours:
                _append_reason(reasons, "hop_limit_reached")
            continue
        if config.max_graph_candidates == 0:
            if neighbours:
                _append_reason(reasons, "graph_candidate_limit_reached")
            continue
        if len(neighbours) > config.max_fanout:
            _append_reason(reasons, "fanout_limit_reached")
        for next_id, from_goal_id, edge_direction, edge in neighbours[: config.max_fanout]:
            if next_id in current_path:
                _append_reason(reasons, "cycle_detected")
                continue
            if len(paths) >= config.max_graph_candidates:
                _append_reason(reasons, "graph_candidate_limit_reached")
                frontier.clear()
                break
            next_path = (*current_path, next_id)
            next_provenance = (
                *current_provenance,
                _relation_provenance(
                    edge,
                    from_goal_id=from_goal_id,
                    to_goal_id=next_id,
                    direction=edge_direction,
                    abstraction_path=next_path,
                ),
            )
            paths.append(
                GoalTraversalPath(
                    goal_id=next_id,
                    abstraction_path=next_path,
                    relation_provenance=next_provenance,
                )
            )
            frontier.append((next_id, next_path, next_provenance))

    return tuple(paths), tuple(reasons)


def traverse_accepted_goal_relations(
    start_goal_ids: Iterable[GoalId],
    relation_edges: Iterable[Mapping[str, Any]],
    *,
    direction: TraversalDirection = "both",
    config: HierarchicalGoalRoutingConfig | None = None,
) -> GoalTraversalResult:
    """Traverse accepted direct ``SPECIALIZES`` edges without producing a closure.

    Paths remain bounded by hop, per-node fanout, and result count. Input order is
    preserved, no score participates in traversal, and a cycle is recorded as degraded
    rather than followed.
    """
    if direction not in ("parents", "children", "both"):
        raise ValueError("direction must be 'parents', 'children', or 'both'")
    active_config = config or HierarchicalGoalRoutingConfig()
    prepared = _prepare_relations(relation_edges)
    paths, traversal_reasons = _traverse_prepared(
        start_goal_ids,
        prepared,
        direction=direction,
        config=active_config,
    )
    degraded_reasons: list[str] = []
    for reason in (*prepared.invalid_reasons, *traversal_reasons):
        _append_reason(degraded_reasons, reason)
    truncated = any(reason in _TRUNCATION_REASONS for reason in degraded_reasons)
    return GoalTraversalResult(
        paths=paths,
        degraded=bool(degraded_reasons),
        degraded_reasons=tuple(degraded_reasons),
        truncated=truncated,
        input_relation_count=prepared.input_count,
        accepted_relation_count=len(prepared.accepted),
        ignored_relation_statuses=dict(prepared.ignored_statuses),
        ignored_relation_types=dict(prepared.ignored_types),
        invalid_relation_count=prepared.invalid_count,
    )


def _normalize_goal_records(
    goal_records: Mapping[Any, Mapping[str, Any]] | None,
) -> dict[GoalId, Mapping[str, Any]]:
    if not goal_records:
        return {}
    normalized: dict[GoalId, Mapping[str, Any]] = {}
    for raw_key, value in goal_records.items():
        if not isinstance(value, Mapping):
            continue
        key = _text(raw_key)
        candidate_id = _goal_id(value)
        if key is not None and key not in normalized:
            normalized[key] = value
        if candidate_id is not None and candidate_id not in normalized:
            normalized[candidate_id] = value
    return normalized


def _graph_candidate_payload(
    goal_id: GoalId,
    prepared: _PreparedRelations,
    goal_records: Mapping[GoalId, Mapping[str, Any]],
) -> dict[str, Any]:
    payload = goal_records.get(goal_id) or prepared.node_payloads.get(goal_id)
    candidate = dict(payload) if payload is not None else {}
    candidate.setdefault("goal_id", goal_id)
    if not candidate:
        candidate["id"] = goal_id
    candidate.setdefault("score", 0.0)
    return candidate


def _merge_provenance(
    existing: tuple[GoalRelationProvenance, ...],
    incoming: tuple[GoalRelationProvenance, ...],
) -> tuple[GoalRelationProvenance, ...]:
    merged = list(existing)
    seen = {
        (
            item.edge_id,
            item.relation_version_id,
            item.relation_id,
            item.abstraction_path,
        )
        for item in existing
    }
    for item in incoming:
        key = (item.edge_id, item.relation_version_id, item.relation_id, item.abstraction_path)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return tuple(merged)


def _relevant_relation_count(
    edges: tuple[_AcceptedGoalEdge, ...],
    start_goal_ids: set[GoalId],
    config: HierarchicalGoalRoutingConfig,
) -> int:
    return sum(
        1
        for edge in edges
        if (
            config.expand_parents
            and edge.specific_goal_id in start_goal_ids
        )
        or (
            config.expand_children
            and edge.abstract_goal_id in start_goal_ids
        )
    )


def route_hierarchical_goal_candidates(
    semantic_anchors: Iterable[Mapping[str, Any]],
    relation_edges: Iterable[Mapping[str, Any]],
    *,
    goal_records: Mapping[Any, Mapping[str, Any]] | None = None,
    config: HierarchicalGoalRoutingConfig | None = None,
) -> HierarchicalGoalRoutingResult:
    """Route flat semantic anchors through bounded accepted hierarchy edges.

    Anchor payloads and scores are copied without alteration and always precede graph
    candidates. Relation edges are filtered to accepted ``SPECIALIZES`` rows. Parents and
    children are expanded independently in input order, duplicate graph Goals are merged
    with every direct-edge path, and the result reports flat fallback and incomplete
    bounded coverage separately. Graph records may be supplied through ``goal_records`` or
    endpoint fields on an edge; otherwise an ID-only record with a zero score is returned.
    """
    active_config = config or HierarchicalGoalRoutingConfig()
    anchor_payloads = [dict(item) for item in semantic_anchors]
    prepared = _prepare_relations(relation_edges)
    normalized_records = _normalize_goal_records(goal_records)

    semantic_anchors: list[RoutedGoalCandidate] = []
    anchor_ids: list[GoalId] = []
    anchors_missing_goal_id = 0
    for payload in anchor_payloads:
        goal_id = _goal_id(payload)
        if goal_id is None:
            anchors_missing_goal_id += 1
            semantic_anchors.append(
                RoutedGoalCandidate(
                    candidate=payload,
                    origin="semantic_anchor",
                    abstraction_path=(),
                )
            )
            continue
        anchor_ids.append(goal_id)
        semantic_anchors.append(
            RoutedGoalCandidate(
                candidate=payload,
                origin="semantic_anchor",
                abstraction_path=(goal_id,),
            )
        )

    anchor_id_set = set(anchor_ids)
    relevant_count = _relevant_relation_count(
        prepared.accepted,
        anchor_id_set,
        active_config,
    )
    fallback_reasons: list[str] = []
    if not anchor_payloads:
        _append_reason(fallback_reasons, "no_semantic_anchors")
    elif not anchor_id_set:
        _append_reason(fallback_reasons, "anchors_missing_goal_id")
    elif not prepared.accepted:
        _append_reason(fallback_reasons, "no_accepted_relations")
    elif (
        active_config.max_hops == 0
        or not active_config.expand_parents
        and not active_config.expand_children
    ):
        _append_reason(fallback_reasons, "hierarchy_expansion_disabled")
    elif relevant_count == 0:
        _append_reason(fallback_reasons, "no_relevant_accepted_relations")

    traversal_paths: list[GoalTraversalPath] = []
    degraded_reasons: list[str] = list(prepared.invalid_reasons)
    if anchors_missing_goal_id:
        _append_reason(degraded_reasons, "anchors_missing_goal_id")
    if (
        anchor_id_set
        and prepared.accepted
        and relevant_count
        and active_config.max_hops > 0
        and (active_config.expand_parents or active_config.expand_children)
    ):
        if active_config.expand_parents:
            parent_paths, parent_reasons = _traverse_prepared(
                anchor_ids,
                prepared,
                direction="parents",
                config=active_config,
            )
            traversal_paths.extend(parent_paths)
            for reason in parent_reasons:
                _append_reason(degraded_reasons, reason)
        if active_config.expand_children:
            child_paths, child_reasons = _traverse_prepared(
                anchor_ids,
                prepared,
                direction="children",
                config=active_config,
            )
            traversal_paths.extend(child_paths)
            for reason in child_reasons:
                _append_reason(degraded_reasons, reason)

    graph_candidates: list[RoutedGoalCandidate] = []
    graph_by_id: dict[GoalId, int] = {}
    for path in traversal_paths:
        if path.goal_id in anchor_id_set:
            continue
        existing_index = graph_by_id.get(path.goal_id)
        if existing_index is None:
            if len(graph_candidates) >= active_config.max_graph_candidates:
                _append_reason(degraded_reasons, "graph_candidate_limit_reached")
                continue
            graph_candidates.append(
                RoutedGoalCandidate(
                    candidate=_graph_candidate_payload(
                        path.goal_id,
                        prepared,
                        normalized_records,
                    ),
                    origin="graph",
                    abstraction_path=path.abstraction_path,
                    relation_provenance=path.relation_provenance,
                )
            )
            graph_by_id[path.goal_id] = len(graph_candidates) - 1
            continue
        existing = graph_candidates[existing_index]
        graph_candidates[existing_index] = RoutedGoalCandidate(
            candidate=existing.candidate,
            origin=existing.origin,
            abstraction_path=existing.abstraction_path,
            relation_provenance=_merge_provenance(
                existing.relation_provenance,
                path.relation_provenance,
            ),
        )

    if not graph_candidates and not fallback_reasons:
        if active_config.max_graph_candidates == 0 and relevant_count:
            _append_reason(fallback_reasons, "graph_candidate_limit_zero")
            _append_reason(degraded_reasons, "graph_candidate_limit_reached")
        elif traversal_paths:
            _append_reason(fallback_reasons, "expanded_goals_already_anchored")
        else:
            _append_reason(fallback_reasons, "no_graph_candidates")

    used_flat_fallback = bool(fallback_reasons)
    mode: RoutingMode = "flat" if used_flat_fallback else "hierarchical"
    truncated = any(reason in _TRUNCATION_REASONS for reason in degraded_reasons)
    diagnostics = HierarchicalGoalRoutingDiagnostics(
        mode=mode,
        used_flat_fallback=used_flat_fallback,
        fallback_reasons=tuple(fallback_reasons),
        degraded=bool(degraded_reasons),
        degraded_reasons=tuple(degraded_reasons),
        truncated=truncated,
        semantic_anchor_count=len(semantic_anchors),
        graph_candidate_count=len(graph_candidates),
        input_relation_count=prepared.input_count,
        accepted_relation_count=len(prepared.accepted),
        relevant_accepted_relation_count=relevant_count,
        ignored_relation_statuses=dict(prepared.ignored_statuses),
        ignored_relation_types=dict(prepared.ignored_types),
        invalid_relation_count=prepared.invalid_count,
        anchors_missing_goal_id=anchors_missing_goal_id,
        limits=active_config,
    )
    return HierarchicalGoalRoutingResult(
        semantic_anchors=tuple(semantic_anchors),
        graph_candidates=tuple(graph_candidates),
        diagnostics=diagnostics,
    )


expand_hierarchical_goal_candidates = route_hierarchical_goal_candidates


__all__ = [
    "DEFAULT_MAX_FANOUT",
    "DEFAULT_MAX_GRAPH_CANDIDATES",
    "DEFAULT_MAX_HOPS",
    "MAX_SUPPORTED_HOPS",
    "GoalRelationProvenance",
    "GoalTraversalPath",
    "GoalTraversalResult",
    "HierarchicalGoalRoutingConfig",
    "HierarchicalGoalRoutingDiagnostics",
    "HierarchicalGoalRoutingResult",
    "RoutedGoalCandidate",
    "expand_hierarchical_goal_candidates",
    "route_hierarchical_goal_candidates",
    "traverse_accepted_goal_relations",
]
