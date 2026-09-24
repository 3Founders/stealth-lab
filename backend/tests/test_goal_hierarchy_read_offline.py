from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timezone
from typing import Any

import pytest

from app.services.access import AccessScope, TenantScope
from app.services.goal_hierarchy_read import enrich_goal, enrich_goals
from app.services.shards import ShardUnavailable

TENANT = "00000000-0000-0000-0000-000000000001"
RESOLVED = datetime(2026, 9, 24, tzinfo=timezone.utc)


def gid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def bid(number: int) -> str:
    return f"00000000-0000-4000-9000-{number:012d}"


def goal(
    number: int,
    *,
    name: str,
    shard: str = "K000",
    visibility: str = "public",
    owner: str | None = None,
    resolved_at: datetime | None = None,
    scope_type: str = "global",
    scope_entity: str | None = None,
    tenant: str = TENANT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    goal_id = gid(number)
    canonical = {
        "id": goal_id,
        "canonical_name": name,
        "description": f"Description {number}",
        "status": "active",
        "visibility": visibility,
        "owner_id": owner,
        "scope_type": scope_type,
        "scope_entity_id": scope_entity,
        "resolved_at": resolved_at,
        "version": 1,
        "t_invalid": None,
    }
    projected = {
        "id": goal_id,
        "canonical_name": name,
        "home_shard_id": shard,
        "projected_status": "active",
        "projected_version": 1,
        "projected_scope_type": scope_type,
        "projected_scope_entity_id": scope_entity,
        "projected_visibility": visibility,
        "projected_owner_id": owner,
        "tenant_id": tenant,
    }
    return projected, canonical


def edge(specific: int, abstract: int, *, status: str = "accepted", **values: Any) -> dict[str, Any]:
    row = {
        "specific_goal_id": gid(specific),
        "abstract_goal_id": gid(abstract),
        "relation_type": "SPECIALIZES",
        "status": status,
        "scope_type": "global",
        "scope_entity_id": None,
        "tenant_id": TENANT,
    }
    row.update(values)
    return row


class FakePools:
    def __init__(self, control: "FakePool", **pools: Any):
        self._pools = {"K000": control, **pools}

    async def get(self, shard_id: str) -> Any:
        pool = self._pools[shard_id]
        if isinstance(pool, Exception):
            raise pool
        return pool


class FakePool:
    def __init__(self, shard_id: str = "K000"):
        self.shard_id = shard_id
        self.projected: dict[str, dict[str, Any]] = {}
        self.goals: dict[str, dict[str, Any]] = {}
        self.relations: list[dict[str, Any]] = []
        self.benchmarks: list[dict[str, Any]] = []
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.canonical_batches: list[list[str]] = []

    def add_goal(self, *records: tuple[dict[str, Any], dict[str, Any]]) -> None:
        for projected, canonical in records:
            self.projected[canonical["id"]] = projected
            self.goals[canonical["id"]] = canonical

    def add_relation(self, *rows: dict[str, Any]) -> None:
        self.relations.extend(dict(row) for row in rows)

    def add_benchmark(self, goal_number: int, number: int, name: str) -> None:
        self.benchmarks.append(
            {
                "id": bid(number),
                "goal_id": gid(goal_number),
                "name": name,
                "version": 1,
                "status": "active",
            }
        )

    @staticmethod
    def _index(sql: str, column: str) -> int | None:
        match = re.search(rf"\b{re.escape(column)}\s*=\s*\$(\d+)", sql)
        return int(match.group(1)) - 1 if match else None

    def _tenant_matches(self, row: MappingLike, sql: str, args: tuple[Any, ...]) -> bool:
        index = self._index(sql, "tenant_id")
        return index is None or str(row.get("tenant_id") or TENANT) == str(args[index])

    def _visible(self, row: MappingLike, sql: str, args: tuple[Any, ...]) -> bool:
        if not self._tenant_matches(row, sql, args):
            return False
        if "visibility = 'public'" not in sql and "owner_id =" not in sql:
            return True
        visibility = row.get("visibility") or row.get("projected_visibility")
        owner = row.get("owner_id") if "owner_id" in row else row.get("projected_owner_id")
        if visibility == "public":
            return True
        owner_index = self._index(sql, "owner_id")
        return owner_index is not None and owner is not None and owner == args[owner_index]

    @staticmethod
    def _scope(row: MappingLike) -> tuple[str, str | None]:
        scope_type = str(row.get("projected_scope_type") or row.get("scope_type") or "global")
        return scope_type, None if scope_type == "global" else row.get("projected_scope_entity_id")

    def _edge_visible(
        self,
        row: MappingLike,
        sql: str,
        args: tuple[Any, ...],
        ids: set[str],
    ) -> bool:
        specific = self.projected.get(str(row["specific_goal_id"]))
        abstract = self.projected.get(str(row["abstract_goal_id"]))
        if specific is None or abstract is None:
            return False
        return (
            str(specific["id"]) in ids
            and str(abstract["id"]) in ids
            and row.get("relation_type") == "SPECIALIZES"
            and row.get("status") == "accepted"
            and self._tenant_matches(row, sql, args)
            and self._visible(specific, sql, args)
            and self._visible(abstract, sql, args)
            and str(row.get("scope_type") or "global")
            == str(specific.get("projected_scope_type") or "global")
            == str(abstract.get("projected_scope_type") or "global")
            and row.get("scope_entity_id") == specific.get("projected_scope_entity_id")
            == abstract.get("projected_scope_entity_id")
        )

    def _component(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        requested = {str(value) for value in args[0]}
        component = {
            goal_id
            for goal_id, row in self.projected.items()
            if goal_id in requested and row.get("projected_status") in {"active", "candidate"}
            and self._visible(row, sql, args)
        }
        pending = deque(component)
        while pending:
            current = pending.popleft()
            for row in self.relations:
                if row.get("relation_type") != "SPECIALIZES" or row.get("status") != "accepted":
                    continue
                if not self._tenant_matches(row, sql, args):
                    continue
                if str(row["specific_goal_id"]) == current:
                    neighbor_id = str(row["abstract_goal_id"])
                elif str(row["abstract_goal_id"]) == current:
                    neighbor_id = str(row["specific_goal_id"])
                else:
                    continue
                neighbor = self.projected.get(neighbor_id)
                if neighbor is None or neighbor_id in component:
                    continue
                if neighbor.get("projected_status") not in {"active", "candidate"}:
                    continue
                if not self._visible(neighbor, sql, args):
                    continue
                if self._scope(self.projected[current]) != (
                    str(row.get("scope_type") or "global"),
                    row.get("scope_entity_id"),
                ) or self._scope(neighbor) != (
                    str(row.get("scope_type") or "global"),
                    row.get("scope_entity_id"),
                ):
                    continue
                component.add(neighbor_id)
                pending.append(neighbor_id)
        return [dict(self.projected[goal_id]) for goal_id in sorted(component)]

    async def fetch(self, sql: str, *args: Any):
        normalized = " ".join(sql.split())
        captured = (normalized, args)
        self.statements.append(captured)
        if "WITH RECURSIVE" in normalized and "component(goal_id)" in normalized:
            return self._component(normalized, args)
        if "FROM goal_relations r" in normalized and "r.specific_goal_id" in normalized:
            ids = {str(value) for value in args[0]}
            return [
                dict(row)
                for row in self.relations
                if self._edge_visible(row, normalized, args, ids)
            ]
        if "FROM benchmarks" in normalized:
            ids = {str(value) for value in args[0]}
            return [dict(row) for row in self.benchmarks if str(row["goal_id"]) in ids]
        if "FROM goals" in normalized and "WHERE id = ANY" in normalized:
            ids = [str(value) for value in args[0]]
            self.canonical_batches.append(ids)
            return [dict(self.goals[goal_id]) for goal_id in ids if goal_id in self.goals]
        raise AssertionError(f"unexpected fetch: {normalized}")

    async def execute(self, sql: str, *args: Any):
        raise AssertionError(f"read-only enrichment attempted a write: {sql}")


MappingLike = dict[str, Any]


def fixture() -> tuple[FakePool, FakePool, FakePool, FakePools]:
    control = FakePool()
    remote_one = FakePool("K001")
    remote_two = FakePool("K002")
    for shard_pool in (control, remote_one, remote_two):
        shard_pool.add_goal(
            goal(1, name="Root", shard="K000"),
            goal(2, name="Child", shard="K001", resolved_at=RESOLVED),
            goal(3, name="Grandchild", shard="K002", resolved_at=RESOLVED),
            goal(4, name="Other child", shard="K000"),
            goal(5, name="Secret child", shard="K001", visibility="private", owner="alice"),
            goal(6, name="Proposed child", shard="K000"),
            goal(7, name="Orphan", shard="K002"),
             goal(9, name="Other scope", shard="K000", scope_type="project", scope_entity="other"),
             goal(10, name="Other tenant", shard="K000", tenant="ffffffff-ffff-4fff-8fff-ffffffffffff"),

        )
    control.add_relation(
        edge(2, 1),
        edge(3, 2),
        edge(4, 1),
        edge(5, 1),
         edge(6, 1, status="proposed"),
         edge(9, 1),
         edge(10, 1, tenant_id="ffffffff-ffff-4fff-8fff-ffffffffffff"),

    )
    control.add_benchmark(1, 1, "Direct one")
    control.add_benchmark(1, 2, "Direct two")
    control.add_benchmark(3, 3, "Grandchild only")
    control.add_benchmark(5, 4, "Private direct only")
    pools = FakePools(control, K001=remote_one, K002=remote_two)
    return control, remote_one, remote_two, pools


@pytest.mark.asyncio
async def test_enrichment_is_direct_accepted_visible_batched_and_non_mutating():
    control, remote_one, remote_two, pools = fixture()
    inputs = [
        {"id": gid(1), "canonical_name": "Root", "resolved_at": None},
        {"id": gid(3), "canonical_name": "Grandchild", "resolved_at": RESOLVED},
        {"id": gid(7), "canonical_name": "Orphan", "resolved_at": None},
    ]
    before = [dict(goal) for goal in inputs]

    result = await enrich_goals(
        control,
        inputs,
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )

    assert inputs == before
    assert [goal["id"] for goal in result] == [gid(1), gid(3), gid(7)]
    assert set(result[0]) - set(inputs[0]) == {
        "specializes",
        "abstracts",
        "abstraction_level",
        "benchmarks",
        "coverage",
    }
    root, grandchild, orphan = result
    assert [goal["id"] for goal in root["specializes"]] == [gid(2), gid(4)]
    assert root["abstracts"] == []
    assert root["abstraction_level"] == 0
    assert root["resolved_at"] is None
    assert root["coverage"] == {
        "total_count": 3,
        "resolved_count": 2,
        "ratio": 2 / 3,
    }
    assert [goal["id"] for goal in grandchild["abstracts"]] == [gid(2)]
    assert grandchild["abstraction_level"] == 2
    assert grandchild["coverage"] == {
        "total_count": 0,
        "resolved_count": 0,
        "ratio": 0.0,
    }
    assert orphan["specializes"] == []
    assert orphan["abstracts"] == []
    assert orphan["abstraction_level"] == 0
    assert orphan["coverage"] == {
        "total_count": 0,
        "resolved_count": 0,
        "ratio": 0.0,
    }
    assert [benchmark["name"] for benchmark in root["benchmarks"]] == [
        "Direct one",
        "Direct two",
    ]
    assert [benchmark["name"] for benchmark in grandchild["benchmarks"]] == ["Grandchild only"]
    assert "Secret child" not in repr(result)
    assert "Other scope" not in repr(result)
    assert len(control.canonical_batches) == 1
    assert len(remote_one.canonical_batches) == 1
    assert len(remote_two.canonical_batches) == 1
    assert set(control.canonical_batches[0]) == {gid(1), gid(4)}
    assert set(remote_one.canonical_batches[0]) == {gid(2)}
    assert set(remote_two.canonical_batches[0]) == {gid(3), gid(7)}
    relation_sql = next(sql for sql, _ in control.statements if "FROM goal_relations r" in sql)
    component_sql = next(sql for sql, _ in control.statements if "WITH RECURSIVE" in sql)
    benchmark_sql = next(sql for sql, _ in control.statements if "FROM benchmarks" in sql)
    assert "r.status = 'accepted'" in relation_sql
    assert "r.relation_type = 'SPECIALIZES'" in component_sql
    assert "problem_id" not in benchmark_sql
    assert "goal_id = ANY($1::uuid[])" in benchmark_sql


@pytest.mark.asyncio
async def test_cross_tenant_relation_and_endpoint_rows_are_excluded():
    control, _, _, pools = fixture()

    result = await enrich_goals(
        control,
        [{"id": gid(1), "canonical_name": "Root", "resolved_at": None}],
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )

    assert [row["id"] for row in result] == [gid(1)]
    assert "Other tenant" not in repr(result)
    component_sql = next(sql for sql, _ in control.statements if "WITH RECURSIVE" in sql)
    edge_sql = next(sql for sql, _ in control.statements if "FROM goal_relations r" in sql)
    assert "r.tenant_id =" in component_sql
    assert "g.tenant_id =" not in component_sql
    assert "r.tenant_id =" in edge_sql
    assert "s.tenant_id =" not in edge_sql
    assert "a.tenant_id =" not in edge_sql
    assert "visibility" in component_sql
    assert "visibility" in edge_sql


@pytest.mark.asyncio
async def test_private_neighbors_and_resolution_coverage_appear_only_for_their_viewer():
    control, _, _, pools = fixture()

    anonymous = await enrich_goal(
        control,
        {"id": gid(1), "canonical_name": "Root", "resolved_at": None},
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )
    owner = await enrich_goal(
        control,
        {"id": gid(1), "canonical_name": "Root", "resolved_at": None},
        access_scope=AccessScope.for_user("alice"),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )
    mixed = await enrich_goals(
        control,
        [
            {"id": gid(1), "canonical_name": "Root", "resolved_at": None},
            {"id": gid(5), "canonical_name": "Secret child", "resolved_at": None},
        ],
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )

    assert anonymous is not None and owner is not None
    assert [goal["id"] for goal in anonymous["specializes"]] == [gid(2), gid(4)]
    assert [goal["id"] for goal in owner["specializes"]] == [gid(2), gid(4), gid(5)]
    assert anonymous["coverage"] == {
        "total_count": 3,
        "resolved_count": 2,
        "ratio": 2 / 3,
    }
    assert owner["coverage"] == {
        "total_count": 4,
        "resolved_count": 2,
        "ratio": 0.5,
    }
    assert anonymous["resolved_at"] is None
    assert owner["resolved_at"] is None
    assert [goal["id"] for goal in mixed] == [gid(1)]
    benchmark_call = next(
        (args for sql, args in reversed(control.statements) if "FROM benchmarks" in sql)
    )
    assert benchmark_call[0] == [gid(1)]


@pytest.mark.asyncio
async def test_unavailable_component_shard_does_not_turn_unknown_hierarchy_into_a_root():
    control, remote_one, remote_two, _ = fixture()
    pools = FakePools(
        control,
        K001=ShardUnavailable("K001", "offline test outage"),
        K002=remote_two,
    )

    result = await enrich_goals(
        control,
        [{"id": gid(1), "canonical_name": "Root", "resolved_at": None}],
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )

    assert result == []
    assert remote_one.canonical_batches == []


@pytest.mark.asyncio
async def test_every_added_key_is_exact_and_projection_staleness_fails_closed():
    control, remote_one, remote_two, pools = fixture()
    control.projected[gid(2)]["projected_version"] = 2

    result = await enrich_goals(
        control,
        [{"id": gid(1), "canonical_name": "Root", "resolved_at": None}],
        access_scope=AccessScope.anonymous(),
        tenant_scope=TenantScope.commons(),
        pools=pools,
    )

    assert result == []
    assert len(remote_one.canonical_batches) == 1
    assert len(remote_two.canonical_batches) == 1
