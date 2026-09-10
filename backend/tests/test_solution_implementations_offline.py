"""
DB-free coverage for app/services/solution_implementations.py after
migration 53 taught `get_solution_implementation_detail` to UNION the
generalized `procedure_implementations` relation with the original
`migrated_from_task_node_id -> implementation_tasks` path.

Asserts:
  - a directly-captured procedure (`migrated_from_task_node_id IS NULL`)
    with `procedure_implementations` rows now returns a NON-EMPTY list
    (pre-52 this was always `[]`);
  - both sources are merged and de-duped by `implementation_id`;
  - every row carries `source` + `role`;
  - the task_node-sourced rows keep their `implementation_registry` keys
    (only additions);
  - `None` still propagates for a missing/invisible procedure.

Hand-rolled FakePool records the SQL it is asked for and serves rows for
each of the three queries the resolver can emit.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from app.services.access import AccessScope
from app.services.solution_implementations import get_solution_implementation_detail

PROC_ROW_ID = str(uuid4())
PROC_ID = str(uuid4())
TASK_ID = str(uuid4())
IMPL_A = str(uuid4())   # reachable only via procedure_implementations
IMPL_B = str(uuid4())   # reachable via BOTH sources
UNRESTRICTED = AccessScope.unrestricted()


def _run(coro):
    return asyncio.run(coro)


class FakePool:
    def __init__(self, *, procedure=None, relation_rows=(), task_rows=()):
        self._procedure = procedure
        self._relation_rows = list(relation_rows)
        self._task_rows = list(task_rows)
        self.seen: list[str] = []

    async def fetchrow(self, sql, *params):
        norm = " ".join(sql.split())
        self.seen.append(norm)
        if "FROM procedures WHERE id" in norm:
            return dict(self._procedure) if self._procedure is not None else None
        raise AssertionError(f"unexpected fetchrow: {norm}")

    async def fetch(self, sql, *params):
        norm = " ".join(sql.split())
        self.seen.append(norm)
        if "FROM procedure_implementations pi JOIN implementations i" in norm:
            assert str(params[0]) == PROC_ID
            return [dict(r) for r in self._relation_rows]
        if "FROM implementations i JOIN implementation_tasks it" in norm:
            assert str(params[0]) == TASK_ID
            return [dict(r) for r in self._task_rows]
        raise AssertionError(f"unexpected fetch: {norm}")


def _procedure(**overrides):
    row = {
        "id": PROC_ROW_ID,
        "procedure_id": PROC_ID,
        "migrated_from_task_node_id": None,
        "visibility": "public",
        "owner_id": None,
    }
    row.update(overrides)
    return row


def _relation_row(impl_id, role="primary", **overrides):
    row = {
        "id": impl_id,
        "implementation_id": impl_id,
        "name": f"impl-{impl_id[:6]}",
        "kind": "tool",
        "provider": "acme",
        "version": 1,
        "role": role,
        "binding_id": str(uuid4()),
        "binding_status": "active",
        "evidence_refs": [],
    }
    row.update(overrides)
    return row


def _task_row(impl_id, **overrides):
    row = {
        "id": impl_id,
        "name": f"impl-{impl_id[:6]}",
        "kind": "deterministic",
        "provider": "legacy",
        "version": 1,
        "status": "active",
        "verification_status": "unverified",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------


def test_returns_none_for_missing_or_invisible_procedure():
    pool = FakePool(procedure=None)
    assert _run(get_solution_implementation_detail(
        pool, PROC_ROW_ID, scope=UNRESTRICTED,
    )) is None


def test_non_empty_when_only_procedure_implementations_rows_exist():
    """The whole point of migration 53: a directly-captured procedure
    (no migrated_from_task_node_id) with a real relation binding is no
    longer an empty result."""
    pool = FakePool(
        procedure=_procedure(),
        relation_rows=[_relation_row(IMPL_A, role="supporting")],
    )
    out = _run(get_solution_implementation_detail(
        pool, PROC_ROW_ID, scope=UNRESTRICTED,
    ))
    assert [r["implementation_id"] for r in out] == [IMPL_A]
    assert out[0]["source"] == "procedure_implementations"
    assert out[0]["role"] == "supporting"
    # the task-node path was never queried (no migrated_from_task_node_id)
    assert not any("implementation_tasks" in s for s in pool.seen)


def test_unions_both_sources_and_dedupes_by_implementation_id():
    pool = FakePool(
        procedure=_procedure(migrated_from_task_node_id=TASK_ID),
        relation_rows=[
            _relation_row(IMPL_A, role="primary"),
            _relation_row(IMPL_B, role="verification"),
        ],
        task_rows=[
            _task_row(IMPL_B),   # duplicate of the relation row -> merged
            _task_row(IMPL_A),   # also duplicate
        ],
    )
    out = _run(get_solution_implementation_detail(
        pool, PROC_ROW_ID, scope=UNRESTRICTED,
    ))
    ids = sorted(r["implementation_id"] for r in out)
    assert ids == sorted({IMPL_A, IMPL_B})
    by_id = {r["implementation_id"]: r for r in out}
    # relation source wins the merge; task path recorded under also_via
    assert by_id[IMPL_B]["source"] == "procedure_implementations"
    assert by_id[IMPL_B]["role"] == "verification"
    assert by_id[IMPL_B]["also_via"] == ["implementation_tasks"]


def test_task_only_implementation_keeps_registry_keys_plus_annotations():
    pool = FakePool(
        procedure=_procedure(migrated_from_task_node_id=TASK_ID),
        relation_rows=[],
        task_rows=[_task_row(IMPL_A)],
    )
    out = _run(get_solution_implementation_detail(
        pool, PROC_ROW_ID, scope=UNRESTRICTED,
    ))
    assert len(out) == 1
    row = out[0]
    # existing implementation_registry keys preserved
    assert row["id"] == IMPL_A
    assert row["kind"] == "deterministic"
    assert row["verification_status"] == "unverified"
    # additive annotations
    assert row["source"] == "implementation_tasks"
    assert row["role"] == "primary"
    assert row["implementation_id"] == IMPL_A


def test_empty_list_when_neither_source_has_rows():
    pool = FakePool(
        procedure=_procedure(migrated_from_task_node_id=TASK_ID),
        relation_rows=[],
        task_rows=[],
    )
    assert _run(get_solution_implementation_detail(
        pool, PROC_ROW_ID, scope=UNRESTRICTED,
    )) == []
