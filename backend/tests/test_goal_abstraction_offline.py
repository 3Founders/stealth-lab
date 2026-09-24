"""Offline contract tests for the additive Goal abstraction DAG slice."""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.services.access import AccessScope, TenantScope
from app.services.goal_abstraction import (
    GoalRelationCycleError,
    GoalRelationRedundancyError,
    GoalRelationScopeError,
    GoalRelationSelfError,
    GoalRelationStatusConflict,
    adjudicate_goal_relation,
    derive_abstraction_levels,
    derive_goal_abstraction_state,
    expand_goal_neighbors,
    get_goal_abstraction_state,
    is_transitively_redundant,
    persist_goal_relation,
    rebuild_goal_abstraction_state,
    validate_goal_relation_graph,
)

TENANT = "00000000-0000-0000-0000-000000000001"
RESOLVED = datetime(2026, 9, 24, tzinfo=timezone.utc)


def gid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def edge(specific: int, abstract: int, status: str = "accepted") -> dict[str, Any]:
    return {
        "specific_goal_id": gid(specific),
        "abstract_goal_id": gid(abstract),
        "relation_type": "SPECIALIZES",
        "status": status,
    }


def goal(
    number: int,
    *,
    name: str | None = None,
    scope_type: str = "global",
    scope_entity_id: str | None = None,
    visibility: str = "public",
    owner_id: str | None = None,
    resolved_at: datetime | None = None,
    home_shard_id: str = "K000",
    tenant_id: str | None = TENANT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    goal_id = gid(number)
    canonical = {
        "id": goal_id,
        "canonical_name": name or f"Goal {number}",
        "description": None,
        "status": "active",
        "scope_type": scope_type,
        "scope_entity_id": scope_entity_id,
        "visibility": visibility,
        "owner_id": owner_id,
        "resolved_at": resolved_at,
        "version": 1,
        "home_shard_id": home_shard_id,
        "t_invalid": None,
    }
    projected = {
        "id": goal_id,
        "home_shard_id": home_shard_id,
        "projected_status": "active",
        "projected_version": 1,
        "projected_scope_type": scope_type,
        "projected_scope_entity_id": scope_entity_id,
        "projected_visibility": visibility,
        "projected_owner_id": owner_id,
        "tenant_id": tenant_id,
        "canonical_name": canonical["canonical_name"],
    }
    return projected, canonical


def accepted_paths(
    relations: list[dict[str, Any]], start: str, *, parents: bool = False
) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for relation in relations:
        if relation.get("status", "accepted") != "accepted":
            continue
        source = relation["specific_goal_id"]
        target = relation["abstract_goal_id"]
        if parents:
            source, target = target, source
        adjacency.setdefault(source, set()).add(target)
    seen: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(adjacency.get(current, ()))
    return seen



def _decoded_jsonb(value: Any) -> dict[str, Any]:
    # The real pool's jsonb codec takes Python objects; a pre-serialized
    # string would be stored as a JSON string and violate the object CHECK.
    assert isinstance(value, dict), f"jsonb parameter must be a dict, got {type(value).__name__}"
    return dict(value)

class _Acquire:
    def __init__(self, pool: "FakePool"):
        self.pool = pool

    async def __aenter__(self):
        return self.pool

    async def __aexit__(self, *exc):
        return False


class _Transaction:
    def __init__(self, pool: "FakePool"):
        self.pool = pool

    async def __aenter__(self):
        self.pool.committed += 1
        return self.pool

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.pool.commits += 1
        else:
            self.pool.rollbacks += 1
        return False


class FakePool:
    def __init__(self) -> None:
        self.projected: dict[str, dict[str, Any]] = {}
        self.goals: dict[str, dict[str, Any]] = {}
        self.relations: dict[tuple[str, str], dict[str, Any]] = {}
        self.state: dict[str, dict[str, Any]] = {}
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.canonical_batches: list[list[str]] = []
        self.state_writes: list[str] = []
        self.committed = 0
        self.commits = 0
        self.rollbacks = 0

    def add_goal(self, *record: tuple[dict[str, Any], dict[str, Any]]) -> None:
        for projected, canonical in record:
            self.projected[canonical["id"]] = projected
            self.goals[canonical["id"]] = canonical

    def add_relation(self, specific: int, abstract: int, **values: Any) -> None:
        row = {
            "specific_goal_id": gid(specific),
            "abstract_goal_id": gid(abstract),
            "relation_type": "SPECIALIZES",
            "status": "accepted",
            "confidence": None,
            "provenance": "test",
            "decision_id": None,
            "decision_metadata": {},
            "decided_by": None,
            "decided_at": None,
            "scope_type": "global",
            "scope_entity_id": None,
            "tenant_id": TENANT,
            "created_at": RESOLVED,
            "updated_at": RESOLVED,
        }
        row.update(values)
        self.relations[(row["specific_goal_id"], row["abstract_goal_id"])] = row

    def _projected_row(self, goal_id: str) -> dict[str, Any]:
        row = dict(self.projected[goal_id])
        row["tenant_id"] = row.get("tenant_id") or TENANT
        return row

    def acquire(self):
        return _Acquire(self)

    def transaction(self):
        return _Transaction(self)

    @staticmethod
    def _placeholder(sql: str, name: str) -> int | None:
        match = re.search(rf"{re.escape(name)}\s*=\s*\$(\d+)", sql)
        return int(match.group(1)) - 1 if match else None

    def _tenant_matches(self, row: dict[str, Any], sql: str, args: tuple[Any, ...]) -> bool:
        index = self._placeholder(sql, "tenant_id")
        tenant_id = row.get("tenant_id") or TENANT
        return index is None or str(tenant_id) == str(args[index])

    def _visible(self, row: dict[str, Any], sql: str, args: tuple[Any, ...]) -> bool:
        if not self._tenant_matches(row, sql, args):
            return False
        if "visibility = 'public'" not in sql and "owner_id =" not in sql:
            return True
        visibility = row.get("projected_visibility")
        owner = row.get("projected_owner_id")
        if visibility == "public":
            return True
        owner_index = self._placeholder(sql, "owner_id")
        return owner_index is not None and owner is not None and owner == args[owner_index]

    async def fetch(self, sql: str, *args: Any):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, args))
        if "WITH RECURSIVE descendants" in normalized and "up(goal_id)" in normalized:
            # component closure of the changed edge over accepted relations
            edges = [
                (r["specific_goal_id"], r["abstract_goal_id"])
                for r in self.relations.values() if r["status"] == "accepted"
            ]
            seen = {str(args[0]), str(args[1])}
            frontier = list(seen)
            while frontier:
                current = frontier.pop()
                for source, target in edges:
                    for nxt in ((target,) if source == current else ()) + ((source,) if target == current else ()):
                        if nxt not in seen:
                            seen.add(nxt)
                            frontier.append(nxt)
            return [{"goal_id": goal_id} for goal_id in sorted(seen)]
        if "FROM goals" in normalized and "WHERE id = ANY" in normalized:
            ids = [str(item) for item in args[0]]
            self.canonical_batches.append(ids)
            return [dict(self.goals[item]) for item in ids if item in self.goals]
        if "FROM goal_search_index" in normalized and "g.goal_id = ANY" in normalized:
            return [
                self._projected_row(str(item))
                for item in args[0]
                if str(item) in self.projected and self._visible(self.projected[str(item)], normalized, args)
            ]
        if "FROM goal_search_index" in normalized and "g.status IN" in normalized:
            return [
                self._projected_row(str(goal_id))
                for goal_id, row in self.projected.items()
                if self._visible(row, normalized, args)
            ]
        if "FROM goal_relations r JOIN (" in normalized:
            anchor = str(args[0])
            scope_type = str(args[1])
            scope_entity = args[2]
            limit = int(args[3])
            rows: list[dict[str, Any]] = []
            for relation in self.relations.values():
                if relation["status"] != "accepted":
                    continue
                if relation.get("scope_type") != scope_type or relation.get("scope_entity_id") != scope_entity:
                    continue
                neighbor_id = (
                    relation["abstract_goal_id"]
                    if relation["specific_goal_id"] == anchor
                    else relation["specific_goal_id"]
                )
                projected = self.projected.get(neighbor_id)
                if (
                    projected is None
                    or projected.get("projected_scope_type") != scope_type
                    or projected.get("projected_scope_entity_id") != scope_entity
                ):
                    continue
                if not self._tenant_matches(relation, normalized, args):
                    continue
                if relation["specific_goal_id"] != anchor and relation["abstract_goal_id"] != anchor:
                    continue
                neighbor_id = (
                    relation["abstract_goal_id"]
                    if relation["specific_goal_id"] == anchor
                    else relation["specific_goal_id"]
                )
                if projected is None or not self._visible(projected, normalized, args):
                    continue
                rows.append(
                    {
                        **relation,
                        "direction": "parent" if relation["specific_goal_id"] == anchor else "child",
                        "neighbor_id": neighbor_id,
                        "home_shard_id": projected["home_shard_id"],
                        "projected_status": projected["projected_status"],
                        "projected_version": projected["projected_version"],
                        "projected_scope_type": projected["projected_scope_type"],
                        "projected_scope_entity_id": projected["projected_scope_entity_id"],
                        "projected_visibility": projected["projected_visibility"],
                        "projected_owner_id": projected["projected_owner_id"],
                        "tenant_id": projected.get("tenant_id") or TENANT,
                        "canonical_name": projected["canonical_name"],
                    }
                )
            rows.sort(key=lambda row: (row["canonical_name"], row["neighbor_id"]))
            return rows[:limit]
        if "WITH RECURSIVE descendants" in normalized and "SELECT goal_id FROM descendants" in normalized:
            specific, abstract = str(args[0]), str(args[1])
            descendants = accepted_paths(self.relations.values(), specific, parents=True)
            ancestors = accepted_paths(self.relations.values(), abstract)
            return [{"goal_id": goal_id} for goal_id in sorted(descendants | ancestors)]
        if "FROM goal_relations r" in normalized and "r.status = 'accepted'" in normalized:
            return [
                dict(relation)
                for relation in self.relations.values()
                if relation["status"] == "accepted" and self._tenant_matches(relation, normalized, args)
            ]
        raise AssertionError(f"unexpected fetch: {normalized}")

    async def fetchrow(self, sql: str, *args: Any):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, args))
        if "FROM goal_relations" in normalized and "FOR UPDATE" in normalized:
            return self.relations.get((str(args[0]), str(args[1])))
        if "INSERT INTO goal_relations" in normalized:
            row = {
                "specific_goal_id": str(args[0]),
                "abstract_goal_id": str(args[1]),
                "relation_type": "SPECIALIZES",
                "status": args[2],
                "confidence": args[3],
                "provenance": args[4],
                "decision_id": str(args[5]) if args[5] else None,
                "decision_metadata": _decoded_jsonb(args[6]),
                "decided_by": args[7],
                "decided_at": args[8],
                "scope_type": args[9],
                "scope_entity_id": args[10],
                "tenant_id": str(args[11]) if args[11] else None,
                "created_at": RESOLVED,
                "updated_at": RESOLVED,
            }
            self.relations[(row["specific_goal_id"], row["abstract_goal_id"])] = row
            return row
        if "UPDATE goal_relations" in normalized:
            key = (str(args[0]), str(args[1]))
            row = {
                **self.relations[key],
                "status": args[2],
                "confidence": args[3],
                "provenance": args[4],
                "decision_id": str(args[5]) if args[5] else None,
                "decision_metadata": _decoded_jsonb(args[6]),
                "decided_by": args[7],
                "decided_at": args[8],
                "scope_type": args[9],
                "scope_entity_id": args[10],
                "tenant_id": str(args[11]) if args[11] else None,
                "updated_at": RESOLVED,
            }
            self.relations[key] = row
            return row
        if "FROM goal_abstraction_state s" in normalized:
            state = self.state.get(str(args[0]))
            if state is None or not self._visible(state, normalized, args):
                return None
            return state
        raise AssertionError(f"unexpected fetchrow: {normalized}")

    async def fetchval(self, sql: str, *args: Any):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, args))
        specific, abstract = str(args[0]), str(args[1])
        relations = list(self.relations.values())
        if "FROM ancestors" in normalized:
            return specific in accepted_paths(relations, abstract)
        if "FROM descendants" in normalized:
            without_direct = [
                relation
                for relation in relations
                if not (
                    relation["specific_goal_id"] == specific
                    and relation["abstract_goal_id"] == abstract
                )
            ]
            return specific in accepted_paths(without_direct, abstract, parents=True)
        raise AssertionError(f"unexpected fetchval: {normalized}")

    async def execute(self, sql: str, *args: Any) -> str:
        normalized = " ".join(sql.split())
        self.statements.append((normalized, args))
        if "INSERT INTO goal_abstraction_state" in normalized:
            self.state_writes.append(str(args[0]))
            self.state[str(args[0])] = {
                "goal_id": str(args[0]),
                "abstraction_level": int(args[1]),
                "parent_count": int(args[2]),
                "direct_child_count": int(args[3]),
                "coverage_total_count": int(args[4]),
                "coverage_resolved_count": int(args[5]),
                "coverage_ratio": float(args[6]),
                "direct_resolved_at": args[7],
                "goal_status": args[8],
                "goal_version": int(args[9]),
                "visibility": args[10],
                "owner_id": args[11],
                "scope_type": args[12],
                "scope_entity_id": args[13],
                "tenant_id": str(args[14]) if args[14] else None,
                "home_shard_id": args[15],
                "computed_at": RESOLVED,
                "projected_visibility": args[10],
                "projected_owner_id": args[11],
            }
            return "INSERT 0 1"
        if "DELETE FROM goal_abstraction_state" in normalized:
            ids = {str(item) for item in args[0]}
            removable = [
                goal_id
                for goal_id, row in self.state.items()
                if goal_id not in ids and self._visible(row, normalized, args)
            ]
            for goal_id in removable:
                del self.state[goal_id]
            return f"DELETE {len(removable)}"
        return "SELECT 1"


class FakePools:
    def __init__(self, pool: FakePool):
        self.pool = pool

    async def get(self, shard_id: str):
        return self.pool


def test_basic_depth_uses_root_zero_and_one_plus_parent_depth():
    levels = derive_abstraction_levels(
        [gid(number) for number in range(1, 5)],
        [edge(2, 1), edge(3, 2), edge(4, 3)],
    )
    assert [levels[gid(number)] for number in range(1, 5)] == [0, 1, 2, 3]


def test_multiple_parents_use_one_plus_maximum_parent_level():
    relations = [edge(2, 1), edge(3, 1), edge(4, 3), edge(5, 2), edge(5, 3), edge(5, 4)]
    levels = derive_abstraction_levels([gid(number) for number in range(1, 6)], relations)
    assert levels[gid(5)] == 3
    assert levels[gid(4)] == 2


def test_accepted_cycle_rejection_is_iterative_and_cycle_safe():
    size = 1500
    nodes = [gid(number) for number in range(1, size + 1)]
    chain = [edge(number, number - 1) for number in range(2, size + 1)]
    levels = derive_abstraction_levels(nodes, chain)
    assert levels[nodes[-1]] == size - 1
    with pytest.raises(GoalRelationCycleError):
        derive_abstraction_levels(nodes, [*chain, edge(1, size)])


def test_self_edge_is_rejected_before_graph_traversal():
    with pytest.raises(GoalRelationSelfError):
        validate_goal_relation_graph(gid(1), gid(1), [])


def test_direct_edge_is_rejected_when_an_accepted_path_already_exists():
    relations = [edge(1, 2), edge(2, 3)]
    assert is_transitively_redundant(gid(1), gid(3), relations)
    with pytest.raises(GoalRelationRedundancyError):
        validate_goal_relation_graph(gid(1), gid(3), relations)


def test_multi_parent_edges_are_each_authoritative_and_not_redundant():
    relations = [edge(3, 1), edge(3, 2)]
    validate_goal_relation_graph(gid(3), gid(1), relations)
    validate_goal_relation_graph(gid(3), gid(2), relations)
    state = derive_goal_abstraction_state(
        [gid(number) for number in range(1, 4)], relations
    )
    child = next(item for item in state if item["goal_id"] == gid(3))
    assert child["parent_count"] == 2
    assert child["abstraction_level"] == 1


def test_proposed_rejected_and_malformed_edges_are_not_authoritative():
    relations = [
        edge(2, 1, "proposed"),
        edge(3, 1, "rejected"),
        {
            "specific_goal_id": gid(4),
            "abstract_goal_id": gid(1),
            "relation_type": "SPECIALIZES",
        },
    ]
    levels = derive_abstraction_levels(
        [gid(number) for number in range(1, 5)], relations
    )
    assert levels == {gid(number): 0 for number in range(1, 5)}
    assert not is_transitively_redundant(gid(2), gid(3), relations)


def test_coverage_is_aggregate_while_direct_resolution_stays_on_the_goal():
    relations = [edge(2, 1), edge(3, 2)]
    state = derive_goal_abstraction_state(
        [gid(1), gid(2), gid(3)],
        relations,
        direct_resolved_at={gid(3): RESOLVED},
    )
    by_id = {item["goal_id"]: item for item in state}
    assert by_id[gid(1)]["direct_resolved_at"] is None
    assert by_id[gid(1)]["coverage_total_count"] == 2
    assert by_id[gid(1)]["coverage_resolved_count"] == 1
    assert by_id[gid(1)]["coverage_ratio"] == 0.5


@pytest.mark.asyncio
async def test_viewer_scoped_expansion_is_direct_accepted_only_and_privacy_safe():
    pool = FakePool()
    pool.add_goal(
        goal(1, name="Anchor"),
        goal(2, name="Public parent", home_shard_id="K001"),
        goal(3, name="Private child", visibility="private", owner_id="alice"),
        goal(4, name="Proposed child"),
        goal(5, name="Rejected child"),
        goal(6, name="Grandparent"),
        goal(7, name="Wrong scope", scope_type="project", scope_entity_id="other"),
    )
    pool.add_relation(1, 2)
    pool.add_relation(3, 1)
    pool.add_relation(4, 1, status="proposed")
    pool.add_relation(5, 1, status="rejected")
    pool.add_relation(6, 2)
    pool.add_relation(7, 1)
    pools = FakePools(pool)

    anonymous = await expand_goal_neighbors(
        pool,
        gid(1),
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )
    assert anonymous["goal"] is not None, pool.statements
    owner = await expand_goal_neighbors(
        pool,
        gid(1),
        access_scope=AccessScope.for_user("alice"),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )


    assert [item["goal"]["id"] for item in anonymous["parents"]] == [gid(2)]
    assert anonymous["children"] == []
    assert {item["goal"]["id"] for item in owner["children"]} == {gid(3)}
    assert all(item["relation"]["status"] == "accepted" for item in owner["parents"] + owner["children"])
    expansion_sql = next(sql for sql, _ in pool.statements if "FROM goal_relations r JOIN" in sql)
    assert "r.status = 'accepted'" in expansion_sql
    assert "n.visibility" in expansion_sql and "r.tenant_id" in expansion_sql
    assert "n.scope_type IS NOT DISTINCT FROM $2::text" in expansion_sql
    assert "n.scope_entity_id IS NOT DISTINCT FROM $3" in expansion_sql
    assert "WITH RECURSIVE" not in expansion_sql
    assert [len(batch) for batch in pool.canonical_batches] == [1, 1, 1, 1, 1]


@pytest.mark.asyncio
async def test_legacy_null_goal_projection_tenant_resolves_to_commons():
    pool = FakePool()
    pool.add_goal(goal(1, tenant_id=None), goal(2, tenant_id=None))
    pool.add_relation(2, 1)
    pools = FakePools(pool)

    result = await expand_goal_neighbors(
        pool,
        gid(1),
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )

    assert [item["goal"]["id"] for item in result["children"]] == [gid(2)]
    assert result["children"][0]["goal"]["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_rebuild_derives_levels_removes_orphans_and_never_propagates_resolution():
    pool = FakePool()
    pool.add_goal(goal(1, name="Abstract"), goal(2, name="Specific", resolved_at=RESOLVED))
    pool.add_relation(2, 1)
    pool.add_relation(3, 4)
    pool.state[gid(99)] = {
        "goal_id": gid(99),
        "visibility": "public",
        "owner_id": None,
        "tenant_id": TENANT,
        "projected_visibility": "public",
        "projected_owner_id": None,
    }
    original_abstract = dict(pool.goals[gid(1)])

    result = await rebuild_goal_abstraction_state(
        pool,
        access_scope=AccessScope.unrestricted(),
        tenant_scope=TenantScope.commons(),
        pools=FakePools(pool),
    )

    assert result["rebuilt"] is True
    assert result["orphan_edges"] == 1
    assert result["orphans_deleted"] == 1
    assert gid(99) not in pool.state
    assert pool.state[gid(1)]["abstraction_level"] == 0
    assert pool.state[gid(1)]["direct_resolved_at"] is None
    assert pool.state[gid(1)]["coverage_resolved_count"] == 1
    assert pool.goals[gid(1)] == original_abstract
    assert not any("UPDATE goals" in sql for sql, _ in pool.statements)
    assert not any("verification" in sql.lower() for sql, _ in pool.statements)


@pytest.mark.asyncio
async def test_accepted_persistence_is_idempotent_and_keeps_decision_metadata():
    pool = FakePool()
    pool.add_goal(goal(1), goal(2))
    kwargs = {
        "status": "accepted",
        "provenance": "semantic_identity",
        "access_scope": AccessScope.anonymous(),
        "tenant_scope": TenantScope.commons(),
        "confidence": 0.91,
        "decision_id": gid(90),
        "decision_metadata": {"judge": "test", "reason": "strict specialization"},
        "decided_by": "alice",
        "pools": FakePools(pool),
    }

    adjudication = await adjudicate_goal_relation(
        pool,
        gid(2),
        gid(1),
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=FakePools(pool),
    )
    assert adjudication["specific_goal"]["id"] == gid(2), pool.statements
    first = await persist_goal_relation(pool, gid(2), gid(1), **kwargs)
    second = await persist_goal_relation(pool, gid(2), gid(1), **kwargs)

    assert first["persisted"] is True and first["created"] is True
    assert second["persisted"] is False and second["changed"] is False
    stored = pool.relations[(gid(2), gid(1))]
    assert stored["decision_metadata"]["judge"] == "test"
    assert stored["decision_id"] == gid(90)
    assert len(pool.relations) == 1
    assert first["projection"]["recomputed"] is True
    assert second["projection"]["recomputed"] is True

    pool.state_writes.clear()
    reopened = await persist_goal_relation(
        pool, gid(2), gid(1), **{**kwargs, "status": "proposed"}
    )
    assert reopened["changed"] is True
    assert reopened["projection"]["affected_goals"] == 2
    assert pool.state[gid(2)]["abstraction_level"] == 0
    assert pool.state[gid(1)]["coverage_total_count"] == 0


@pytest.mark.asyncio
async def test_rejected_relation_compare_and_set_cannot_be_upgraded_to_accepted():
    pool = FakePool()
    pool.add_goal(goal(1), goal(2))
    pool.add_relation(
        2,
        1,
        status="rejected",
        decided_by="reviewer",
        decision_metadata={"policy": "human_review"},
    )

    with pytest.raises(GoalRelationStatusConflict):
        await persist_goal_relation(
            pool,
            gid(2),
            gid(1),
            status="accepted",
            provenance="identity_resolution",
            access_scope=AccessScope.anonymous(),
            tenant_scope=TenantScope.commons(),
            confidence=0.99,
            decision_metadata={
                "policy": "goal_abstraction_auto_acceptance",
                "policy_version": "goal_abstraction_placement_v1",
            },
            decided_by="goal_abstraction_ingestion_worker",
            expected_status=None,
            pools=FakePools(pool),
        )

    assert pool.relations[(gid(2), gid(1))]["status"] == "rejected"
    assert not pool.state_writes


@pytest.mark.asyncio
async def test_accepted_write_recomputes_affected_descendants_without_touching_goals():
    pool = FakePool()
    pool.add_goal(*(goal(number) for number in range(1, 6)))
    pool.add_relation(2, 1)
    pool.add_relation(4, 2)

    result = await persist_goal_relation(
        pool,
        gid(2),
        gid(3),
        status="accepted",
        provenance="semantic_identity",
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        decision_metadata={"reason": "second valid parent"},
        decided_by="alice",
        pools=FakePools(pool),
    )

    assert result["projection"]["affected_goals"] == 3
    assert result["projection"]["goals_recomputed"] == 3
    assert set(pool.state_writes) == {gid(2), gid(3), gid(4)}
    assert gid(1) not in pool.state
    assert gid(5) not in pool.state
    assert pool.state[gid(2)]["abstraction_level"] == 1
    assert pool.state[gid(4)]["abstraction_level"] == 2
    assert pool.state[gid(3)]["coverage_total_count"] == 2
    assert not any("UPDATE goals" in sql for sql, _ in pool.statements)
    assert not any("object_routes" in sql.lower() for sql, _ in pool.statements)


@pytest.mark.asyncio
async def test_state_read_rechecks_current_goal_privacy():
    pool = FakePool()
    pool.add_goal(goal(1, visibility="private", owner_id="alice"))
    pool.state[gid(1)] = {
        "goal_id": gid(1),
        "abstraction_level": 0,
        "parent_count": 0,
        "direct_child_count": 0,
        "coverage_total_count": 0,
        "coverage_resolved_count": 0,
        "coverage_ratio": 0.0,
        "direct_resolved_at": None,
        "goal_status": "active",
        "goal_version": 1,
        "visibility": "private",
        "owner_id": "alice",
        "scope_type": "global",
        "scope_entity_id": None,
        "tenant_id": TENANT,
        "home_shard_id": "K000",
        "computed_at": RESOLVED,
        "projected_visibility": "private",
        "projected_owner_id": "alice",
    }

    assert await get_goal_abstraction_state(
        pool,
        gid(1),
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=FakePools(pool),
    ) is None
    assert await get_goal_abstraction_state(
        pool,
        gid(1),
        access_scope=AccessScope.for_user("alice"),
        tenant_scope=TenantScope.commons(),
        pools=FakePools(pool),
    ) is not None

    pool.goals[gid(1)]["owner_id"] = "bob"
    pool.projected[gid(1)]["projected_owner_id"] = "bob"
    assert await get_goal_abstraction_state(
        pool,
        gid(1),
        access_scope=AccessScope.for_user("alice"),
        tenant_scope=TenantScope.commons(),
        pools=FakePools(pool),
    ) is None


@pytest.mark.asyncio
async def test_persistence_rejects_cross_scope_cycle_and_redundant_edges():
    pool = FakePool()
    pool.add_goal(
        goal(1),
        goal(2),
        goal(3),
        goal(4),
        goal(5, scope_type="project", scope_entity_id="other"),
    )
    base = {
        "status": "accepted",
        "provenance": "semantic_identity",
        "access_scope": AccessScope.anonymous(),
        "tenant_scope": TenantScope.commons(),
        "decision_metadata": {"reason": "test"},
        "decided_by": "alice",
        "pools": FakePools(pool),
    }
    with pytest.raises(GoalRelationScopeError):
        await persist_goal_relation(pool, gid(1), gid(5), **base)

    pool.add_relation(1, 2)
    pool.add_relation(2, 3)
    with pytest.raises(GoalRelationCycleError):
        await persist_goal_relation(pool, gid(3), gid(1), **base)
    with pytest.raises(GoalRelationRedundancyError):
        await persist_goal_relation(pool, gid(1), gid(3), **base)


def test_migration_is_additive_idempotent_and_enforces_a_cycle_invariant():
    migration = (
        Path(__file__).parents[1] / "db" / "113_goal_abstraction_dag.sql"
    ).read_text(encoding="utf-8")
    assert migration.startswith("-- Migration 113")
    assert "Next free number: 115" in migration
    assert "CREATE TABLE IF NOT EXISTS goal_abstraction_state" in migration
    assert "ADD COLUMN IF NOT EXISTS decision_metadata JSONB" in migration
    assert "goal_relations_accepted_tenant_scope_chk" in migration
    assert "OR scope_type IS NOT NULL" in migration
    assert "OR (tenant_id IS NOT NULL AND scope_type IS NOT NULL)" not in migration
    assert "ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL" not in migration
    assert "UPDATE goal_relations" not in migration
    assert "idx_goal_relations_accepted_tenant_edge" in migration
    assert "idx_goal_relations_accepted_tenant_scope_edge" in migration
    assert "CREATE OR REPLACE FUNCTION sl_goal_relation_cycle_guard()" in migration
    assert "pg_advisory_xact_lock" in migration
    assert "WITH RECURSIVE ancestors" in migration
    assert "WITH RECURSIVE reach" in migration
    assert "UNION ALL" not in migration
    assert "UPDATE goals" not in migration
    lowered = migration.lower()
    assert "goal_kind" not in lowered
    assert "families" not in lowered
    assert "global transitive closure" not in lowered
