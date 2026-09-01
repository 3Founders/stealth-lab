"""
DB-free coverage for app/services/task_api.py + the /v1/tasks router
(app/api/tasks.py). Same FakePool idiom test_evidence_offline.py and
test_claim_evidence_offline.py already use: a hand-rolled pool that
dispatches on a SQL substring and records every call, proving this
module's own orchestration (which queries run, with which params, in
which order, composed into which response shape) without asserting
Postgres semantics a fake cannot actually evaluate -- that correctness
is test_task_api_e2e.py's job, against real Postgres.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_scope
from app.api.tasks import router as tasks_router
from app.services.access import AccessScope
from app.services.task_api import get_task_detail


def _run(coro):
    return asyncio.run(coro)


TASK_ID = str(uuid4())
PROC_ROW_ID = str(uuid4())
PROC_FAMILY_ID = str(uuid4())


def _task_row(**overrides):
    row = {
        "id": TASK_ID,
        "name": "fix-flaky-test",
        "description": "stabilize a flaky CI test",
        "io_schema": {},
        "skill_ref": "fix_flaky_test",
        "success_criteria": {},
        "cost_estimate": None,
        "latency_estimate_ms": None,
        "pert_optimistic_ms": None,
        "pert_likely_ms": None,
        "pert_pessimistic_ms": None,
        "provenance": "company_ingested",
        "scope_type": "global",
        "scope_entity_id": None,
        "t_created": datetime.now(timezone.utc),
        "t_invalid": None,
        "visibility": "public",
        "owner_id": None,
        "tenant_id": "00000000-0000-0000-0000-000000000001",
    }
    row.update(overrides)
    return row


def _procedure_row(**overrides):
    row = {
        "id": PROC_ROW_ID,
        "procedure_id": PROC_FAMILY_ID,
        "version": 1,
        "name": "flaky-test-fix-procedure",
        "goal": "stabilize the test",
        "verification_state": "verified",
        "staleness": "fresh",
        "availability": "active",
        "approval_status": "approved",
        "t_created": datetime.now(timezone.utc),
        "t_invalid": None,
        "visibility": "public",
        "owner_id": None,
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "migrated_from_task_node_id": TASK_ID,
    }
    row.update(overrides)
    return row


class FakePool:
    def __init__(self, *, task_row=None, procedure_rows=(), evidence_stream=(), failure_classes=()):
        self._task_row = task_row
        self._procedure_rows = list(procedure_rows)
        self._evidence_stream = list(evidence_stream)
        self._failure_classes = [{"failure_class": f} for f in failure_classes]
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *params):
        flat = " ".join(sql.split())
        self.fetchrow_calls.append((flat, params))
        if "FROM task_nodes" in flat:
            return self._task_row
        raise AssertionError(f"unexpected fetchrow: {flat}")

    async def fetch(self, sql, *params):
        flat = " ".join(sql.split())
        self.fetch_calls.append((flat, params))
        if "FROM procedures p" in flat:
            return self._procedure_rows
        if "outcome_status, context_key, independence_group" in flat:
            return self._evidence_stream
        if "DISTINCT failure_class" in flat:
            return self._failure_classes
        raise AssertionError(f"unexpected fetch: {flat}")


# --------------------------------------------------------------- service


def test_task_not_found_returns_none():
    pool = FakePool(task_row=None)
    result = _run(get_task_detail(pool, TASK_ID, scope=AccessScope.unrestricted()))
    assert result is None
    # no procedure/evidence queries should ever run once the task itself
    # 404s -- nothing to compose capability over.
    assert pool.fetch_calls == []


def test_task_with_no_dependent_procedures_is_honestly_empty():
    pool = FakePool(task_row=_task_row(), procedure_rows=[])
    result = _run(get_task_detail(pool, TASK_ID, scope=AccessScope.unrestricted()))
    assert result is not None
    assert result["id"] == TASK_ID
    assert result["skill_ref"] == "fix_flaky_test"
    assert result["dependent_procedures"] == []
    assert result["capability_statistics"] == []
    assert result["known_failure_modes"] == []


def test_task_own_fields_are_returned_verbatim():
    pool = FakePool(
        task_row=_task_row(io_schema={"in": "str"}, cost_estimate=1.5), procedure_rows=[],
    )
    result = _run(get_task_detail(pool, TASK_ID, scope=AccessScope.unrestricted()))
    assert result["io_schema"] == {"in": "str"}
    assert result["cost_estimate"] == 1.5
    assert result["skill_ref"] == "fix_flaky_test"
    assert result["success_criteria"] == {}
    # internal-only columns (t_invalid, visibility, owner_id, tenant_id)
    # are not part of the response shape.
    assert "t_invalid" not in result
    assert "tenant_id" not in result


def test_dependent_procedure_carries_capability_and_failure_modes():
    stream = [
        {"outcome_status": "success", "context_key": "ctx-a", "independence_group": "g1"},
        {"outcome_status": "success", "context_key": "ctx-b", "independence_group": "g2"},
        {"outcome_status": "failure", "context_key": "ctx-c", "independence_group": "g3"},
    ]
    pool = FakePool(
        task_row=_task_row(),
        procedure_rows=[_procedure_row()],
        evidence_stream=stream,
        failure_classes=["environment_mismatch", "wrong_procedure"],
    )
    result = _run(get_task_detail(pool, TASK_ID, scope=AccessScope.unrestricted()))

    assert len(result["dependent_procedures"]) == 1
    assert result["dependent_procedures"][0]["id"] == PROC_ROW_ID
    assert result["dependent_procedures"][0]["procedure_id"] == PROC_FAMILY_ID

    assert len(result["capability_statistics"]) == 1
    cap = result["capability_statistics"][0]
    assert cap["procedure_row_id"] == PROC_ROW_ID
    assert cap["evidence_count"] == 3
    assert cap["success_count"] == 2
    assert 0.0 < cap["p_estimate"] < 1.0
    assert cap["routing"] in ("auto_route", "offer_as_candidate", "refuse_reuse")

    # sorted, deduplicated, real values -- never fabricated.
    assert result["known_failure_modes"] == ["environment_mismatch", "wrong_procedure"]

    # exact (target_id, target_version) pin, same discipline
    # applicability.py's own capability call site uses.
    cap_call = next(c for c in pool.fetch_calls if "outcome_status, context_key" in c[0])
    assert cap_call[1] == (PROC_ROW_ID, 1)


def test_multiple_dependent_procedures_each_get_their_own_capability_query():
    pool = FakePool(
        task_row=_task_row(),
        procedure_rows=[_procedure_row(), _procedure_row(id=str(uuid4()), version=2)],
        evidence_stream=[],
        failure_classes=[],
    )
    result = _run(get_task_detail(pool, TASK_ID, scope=AccessScope.unrestricted()))
    assert len(result["capability_statistics"]) == 2
    capability_queries = [c for c in pool.fetch_calls if "outcome_status, context_key" in c[0]]
    assert len(capability_queries) == 2


def test_no_evidence_stream_degrades_to_level_zero_unknown():
    pool = FakePool(
        task_row=_task_row(), procedure_rows=[_procedure_row()], evidence_stream=[], failure_classes=[],
    )
    result = _run(get_task_detail(pool, TASK_ID, scope=AccessScope.unrestricted()))
    cap = result["capability_statistics"][0]
    assert cap["evidence_count"] == 0
    assert cap["level"] == 0
    assert cap["level_label"] == "unknown"


# ------------------------------------------------------------------ router


def _make_app(pool: FakePool, *, scope: AccessScope) -> FastAPI:
    app = FastAPI()
    app.include_router(tasks_router)
    app.state.pool = pool
    app.dependency_overrides[get_scope] = lambda: scope
    return app


def test_router_404_for_missing_task():
    pool = FakePool(task_row=None)
    app = _make_app(pool, scope=AccessScope.unrestricted())
    client = TestClient(app)
    resp = client.get(f"/v1/tasks/{TASK_ID}")
    assert resp.status_code == 404


def test_router_200_with_composed_body():
    pool = FakePool(task_row=_task_row(), procedure_rows=[], evidence_stream=[], failure_classes=[])
    app = _make_app(pool, scope=AccessScope.unrestricted())
    client = TestClient(app)
    resp = client.get(f"/v1/tasks/{TASK_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == TASK_ID
    assert body["dependent_procedures"] == []
    assert body["capability_statistics"] == []
    assert body["known_failure_modes"] == []


def test_router_rejects_non_uuid_task_id():
    pool = FakePool(task_row=None)
    app = _make_app(pool, scope=AccessScope.unrestricted())
    client = TestClient(app)
    resp = client.get("/v1/tasks/not-a-uuid")
    assert resp.status_code == 422
