"""
DB-free coverage for app/services/personal_contributions.py + the /v1/me
router (app/api/me.py). Same FakePool idiom as test_task_api_offline.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_scope
from app.api.me import router as me_router
from app.services.access import AccessScope
from app.services.personal_contributions import get_personal_contributions


def _run(coro):
    return asyncio.run(coro)


ACTOR = "actor-subject-123"


def _procedure_row(**overrides):
    row = {
        "id": str(uuid4()),
        "procedure_id": str(uuid4()),
        "version": 1,
        "name": "a-procedure",
        "goal": "do a thing",
        "verification_state": "candidate",
        "staleness": "fresh",
        "availability": "active",
        "approval_status": "pending",
        "t_created": datetime.now(timezone.utc),
        "created_by": ACTOR,
    }
    row.update(overrides)
    return row


def _claim_row(**overrides):
    row = {
        "id": str(uuid4()),
        "name": "some claim statement"[:200],
        "properties": {"truth_state": "IN"},
        "t_created": datetime.now(timezone.utc),
        "created_by": ACTOR,
    }
    row.update(overrides)
    return row


def _execution_row(**overrides):
    row = {
        "id": str(uuid4()),
        "execution_plan_id": str(uuid4()),
        "task_graph_id": str(uuid4()),
        "procedure_id": str(uuid4()),
        "procedure_version": 1,
        "outcome": "success",
        "started_at": datetime.now(timezone.utc),
        "ended_at": datetime.now(timezone.utc),
        "trace_id": "trace-1",
        "actor_id": ACTOR,
    }
    row.update(overrides)
    return row


class FakePool:
    def __init__(self, *, procedures=(), claims=(), executions=()):
        self._procedures = list(procedures)
        self._claims = list(claims)
        self._executions = list(executions)
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *params):
        flat = " ".join(sql.split())
        self.fetch_calls.append((flat, params))
        if "FROM procedures" in flat:
            return self._procedures
        if "FROM knowledge_nodes" in flat:
            return self._claims
        if "FROM executions" in flat:
            return self._executions
        raise AssertionError(f"unexpected fetch: {flat}")


# --------------------------------------------------------------- service


def test_returns_actor_and_all_three_real_collections():
    pool = FakePool(
        procedures=[_procedure_row()], claims=[_claim_row()], executions=[_execution_row()],
    )
    result = _run(get_personal_contributions(pool, ACTOR, scope=AccessScope.unrestricted()))
    assert result["actor"] == ACTOR
    assert len(result["submitted_procedures"]) == 1
    assert len(result["submitted_claims"]) == 1
    assert len(result["executions"]) == 1


def test_empty_when_actor_has_contributed_nothing():
    pool = FakePool(procedures=[], claims=[], executions=[])
    result = _run(get_personal_contributions(pool, ACTOR, scope=AccessScope.unrestricted()))
    assert result == {
        "actor": ACTOR,
        "submitted_procedures": [],
        "submitted_claims": [],
        "executions": [],
    }


def test_no_fabricated_fields_in_the_response_shape():
    """Directive-named fields with no real underlying data source
    (reputation/evidence score, forks, accepted improvements) must never
    appear -- not even as null placeholders."""
    pool = FakePool(procedures=[_procedure_row()], claims=[], executions=[])
    result = _run(get_personal_contributions(pool, ACTOR, scope=AccessScope.unrestricted()))
    assert set(result.keys()) == {"actor", "submitted_procedures", "submitted_claims", "executions"}
    for forbidden in ("reputation", "evidence_score", "forks", "accepted_improvements", "score"):
        assert forbidden not in result


def test_procedures_query_filters_by_created_by():
    pool = FakePool(procedures=[], claims=[], executions=[])
    _run(get_personal_contributions(pool, ACTOR, scope=AccessScope.unrestricted()))
    proc_call = next(c for c in pool.fetch_calls if "FROM procedures" in c[0])
    assert "created_by = $1" in proc_call[0]
    assert proc_call[1][0] == ACTOR


def test_claims_query_filters_by_created_by_and_node_type():
    pool = FakePool(procedures=[], claims=[], executions=[])
    _run(get_personal_contributions(pool, ACTOR, scope=AccessScope.unrestricted()))
    claim_call = next(c for c in pool.fetch_calls if "FROM knowledge_nodes" in c[0])
    assert "created_by = $1" in claim_call[0]
    assert "node_type = 'claim'" in claim_call[0]


def test_executions_query_filters_by_actor_id_not_created_by():
    pool = FakePool(procedures=[], claims=[], executions=[])
    _run(get_personal_contributions(pool, ACTOR, scope=AccessScope.unrestricted()))
    exec_call = next(c for c in pool.fetch_calls if "FROM executions" in c[0])
    assert "actor_id = $1" in exec_call[0]


# ------------------------------------------------------------------ router


def _make_app(pool: FakePool, *, scope: AccessScope) -> FastAPI:
    app = FastAPI()
    app.include_router(me_router)
    app.state.pool = pool
    app.dependency_overrides[get_scope] = lambda: scope
    return app


def test_router_401_for_anonymous_caller():
    pool = FakePool()
    app = _make_app(pool, scope=AccessScope.anonymous())
    client = TestClient(app)
    resp = client.get("/v1/me")
    assert resp.status_code == 401
    # anonymous must never see a fabricated identity or empty-but-200 body
    assert pool.fetch_calls == []


def test_router_200_for_identified_caller():
    pool = FakePool(
        procedures=[_procedure_row()], claims=[_claim_row()], executions=[_execution_row()],
    )
    app = _make_app(pool, scope=AccessScope.for_user(ACTOR))
    client = TestClient(app)
    resp = client.get("/v1/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["actor"] == ACTOR
    assert len(body["submitted_procedures"]) == 1
    assert len(body["submitted_claims"]) == 1
    assert len(body["executions"]) == 1
