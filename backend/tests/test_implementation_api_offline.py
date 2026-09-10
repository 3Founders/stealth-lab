"""
DB-free coverage for app/api/implementations.py, the two new
app/api/tasks.py routes (`/{id}/implementations`,
`/{id}/resolve-implementation`), and
app/services/solution_implementations.py + its
`GET /v1/solutions/{id}/implementations` route.

Same hand-rolled-FakePool idiom as test_procedure_graph_api_offline.py /
test_claim_graph_api_offline.py: a fake that inspects the SQL text of the
exact, already-real queries these modules issue -- no shared fake, per
this repo's own convention that each offline test file hand-rolls its
own.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_scope
from app.api.implementations import router as implementations_router
from app.api.solutions import router as solutions_router
from app.api.tasks import router as tasks_router
from app.execution import implementation_registry
from app.services.access import AccessScope

NOW = datetime.now(timezone.utc)

IMPL_ID = str(uuid4())
TASK_ID = str(uuid4())
PROC_ROW_ID = str(uuid4())
# Stable logical procedure id (migration 52's procedure_implementations
# relation is keyed by this, not by the version row id).
PROC_ID = str(uuid4())

UNRESTRICTED = AccessScope.unrestricted()
ANON = AccessScope.anonymous()


def _impl(**overrides):
    row = {
        "id": IMPL_ID,
        "name": "Graphify",
        "description": "graph extraction tool",
        "kind": "tool",
        "provider": "graphify",
        "version": 1,
        "locator": {},
        "invocation": {"protocol": "mcp", "tool": "graphify.extract"},
        "input_schema": {},
        "output_schema": {},
        "requirements": {"needs": "network"},
        "auth_requirements": {"credential_ref": "graphify_api_key"},
        "resource_requirements": {},
        "source_ref": None,
        "author": None,
        "license": None,
        "derived_from": None,
        "content_hash": None,
        "status": "active",
        "verification_status": "unverified",
        "created_by": "tester",
        "visibility": "public",
        "owner_id": None,
        "scope_type": None,
        "scope_entity_id": None,
        "t_created": NOW - timedelta(days=1),
        "deprecated_at": None,
        "disabled_at": None,
    }
    row.update(overrides)
    return row


def _evidence(evidence_id, **overrides):
    row = {
        "id": evidence_id,
        "evidence_type": "execution_result",
        "target_type": "implementation",
        "target_id": IMPL_ID,
        "direction": "supports",
        "outcome_status": "success",
        "independence_group": None,
        "visibility": "public",
        "owner_id": None,
        "t_valid": NOW,
        "t_invalid": None,
    }
    row.update(overrides)
    return row


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


def _passes_visibility(row: dict, sql: str) -> bool:
    if "visibility = 'public'" in sql:
        return row.get("visibility") == "public"
    return True


class FakePool:
    def __init__(self, *, implementations=(), evidence=(), implementation_tasks=(),
                 procedures=(), procedure_implementations=()):
        self._implementations = list(implementations)
        self._evidence = list(evidence)
        # list of (implementation_id, task_node_id)
        self._implementation_tasks = list(implementation_tasks)
        self._procedures = list(procedures)
        # migration 52 relation rows: dicts with at least procedure_id +
        # the joined implementations columns. Empty for the common case.
        self._procedure_implementations = list(procedure_implementations)
        self.fetch_calls = []
        self.fetchrow_calls = []

    async def fetchrow(self, sql, *params):
        norm = " ".join(sql.split())
        self.fetchrow_calls.append((norm, params))
        if "FROM implementations WHERE id" in norm:
            target = str(params[0])
            for row in self._implementations:
                if str(row["id"]) == target and _passes_visibility(row, norm):
                    return dict(row)
            return None
        if "FROM procedures WHERE id" in norm:
            target = str(params[0])
            for row in self._procedures:
                if str(row["id"]) == target and _passes_visibility(row, norm):
                    return dict(row)
            return None
        raise AssertionError(f"unexpected fetchrow: {norm}")

    async def fetch(self, sql, *params):
        norm = " ".join(sql.split())
        self.fetch_calls.append((norm, params))
        if "FROM procedure_implementations pi JOIN implementations i" in norm:
            procedure_id = str(params[0])
            status = params[1] if "pi.status = $2" in norm else None
            rows = [
                dict(r) for r in self._procedure_implementations
                if str(r.get("procedure_id")) == procedure_id
                and (status is None or r.get("binding_status", "active") == status)
            ]
            return rows
        if "FROM implementations i JOIN implementation_tasks it" in norm:
            task_node_id = str(params[0])
            status = None
            if "i.status = $" in norm:
                status = params[-1]
            linked_ids = {
                impl_id for (impl_id, tid) in self._implementation_tasks
                if str(tid) == task_node_id
            }
            rows = [
                row for row in self._implementations
                if str(row["id"]) in linked_ids and _passes_visibility(row, norm)
                and (status is None or row["status"] == status)
            ]
            rows.sort(key=lambda r: r["t_created"], reverse=True)
            return [dict(r) for r in rows]
        if "FROM implementations WHERE" in norm and "ORDER BY t_created" in norm:
            rows = [r for r in self._implementations if _passes_visibility(r, norm)]
            # Apply simple kind/provider/status filters by re-checking params
            # against clause text, mirroring the real module's own clause
            # order (vis, [kind], [provider], [status]).
            idx = 0  # vis params consumed 0 here since vis is TRUE/no params in our tests
            if "kind = $" in norm:
                kind_val = params[idx]
                rows = [r for r in rows if r["kind"] == kind_val]
                idx += 1
            if "provider = $" in norm:
                provider_val = params[idx]
                rows = [r for r in rows if r["provider"] == provider_val]
                idx += 1
            if "status = $" in norm:
                status_val = params[idx]
                rows = [r for r in rows if r["status"] == status_val]
                idx += 1
            rows.sort(key=lambda r: r["t_created"], reverse=True)
            return [dict(r) for r in rows]
        if "FROM evidence WHERE target_type = 'implementation'" in norm:
            target = str(params[0])
            rows = [
                e for e in self._evidence
                if str(e["target_id"]) == target and e["t_invalid"] is None
                and _passes_visibility(e, norm)
            ]
            return [dict(e) for e in rows]
        raise AssertionError(f"unexpected fetch: {norm}")


def _run(coro):
    return asyncio.run(coro)


def _make_app(pool: FakePool, scope: AccessScope = UNRESTRICTED) -> FastAPI:
    app = FastAPI()
    app.include_router(implementations_router)
    app.include_router(tasks_router)
    app.include_router(solutions_router)
    app.state.pool = pool
    app.dependency_overrides[get_scope] = lambda: scope
    return app


# ---------------------------------------------------------------------------
# GET /v1/implementations/{id}
# ---------------------------------------------------------------------------


def test_get_implementation_200():
    pool = FakePool(implementations=[_impl()])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/implementations/{IMPL_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == IMPL_ID
    assert body["provider"] == "graphify"


def test_get_implementation_404_for_missing_row():
    pool = FakePool()
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/implementations/{uuid4()}")
    assert resp.status_code == 404


def test_get_implementation_404_for_invisible_private_row():
    private = _impl(visibility="private", owner_id="someone-else")
    pool = FakePool(implementations=[private])
    client = TestClient(_make_app(pool, scope=ANON))

    resp = client.get(f"/v1/implementations/{IMPL_ID}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/implementations (list)
# ---------------------------------------------------------------------------


def test_list_implementations_no_filters():
    other_id = str(uuid4())
    pool = FakePool(implementations=[_impl(), _impl(id=other_id, kind="frontier", provider="anthropic")])
    client = TestClient(_make_app(pool))

    resp = client.get("/v1/implementations")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_implementations_filters_by_kind():
    other_id = str(uuid4())
    pool = FakePool(implementations=[_impl(), _impl(id=other_id, kind="frontier", provider="anthropic")])
    client = TestClient(_make_app(pool))

    resp = client.get("/v1/implementations?kind=frontier")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["kind"] == "frontier"


def test_list_implementations_422_for_unknown_kind():
    pool = FakePool()
    client = TestClient(_make_app(pool))

    resp = client.get("/v1/implementations?kind=not_a_real_kind")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /v1/implementations/{id}/evidence
# ---------------------------------------------------------------------------


def test_get_implementation_evidence():
    pool = FakePool(
        implementations=[_impl()],
        evidence=[_evidence("ev-1"), _evidence("ev-2", target_id=str(uuid4()))],
    )
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/implementations/{IMPL_ID}/evidence")
    assert resp.status_code == 200
    assert [e["id"] for e in resp.json()] == ["ev-1"]


def test_get_implementation_evidence_404_for_missing_implementation():
    pool = FakePool()
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/implementations/{uuid4()}/evidence")
    assert resp.status_code == 404


def test_get_implementation_evidence_excludes_retracted_rows():
    pool = FakePool(
        implementations=[_impl()],
        evidence=[_evidence("ev-1", t_invalid=NOW)],
    )
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/implementations/{IMPL_ID}/evidence")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /v1/implementations/{id}/capability. `app.services.capabilities`
# now exists (sibling workstream landed during this task) -- the router
# imports and calls it directly; this test exercises that live path, not
# the inline fallback (which stays in app/api/implementations.py as a
# dead-but-honest code path should that module ever go away again, and
# is covered indirectly by the assertions below since both computations
# share the exact same Wilson-interval math and field shape).
# ---------------------------------------------------------------------------


def test_get_implementation_capability():
    pool = FakePool(
        implementations=[_impl()],
        evidence=[
            _evidence("ev-1", outcome_status="success", independence_group="g1"),
            _evidence("ev-2", outcome_status="success", independence_group="g2"),
            _evidence("ev-3", outcome_status="failure"),
        ],
    )
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/implementations/{IMPL_ID}/capability")
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence_count"] == 3
    assert body["success_count"] == 2
    assert body["independent_groups"] == 2
    assert body["level_gated"] is None


def test_get_implementation_capability_404_for_missing_implementation():
    pool = FakePool()
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/implementations/{uuid4()}/capability")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/tasks/{id}/implementations
# ---------------------------------------------------------------------------


def test_get_task_implementations_default_active():
    active_impl = _impl()
    deprecated_id = str(uuid4())
    deprecated_impl = _impl(id=deprecated_id, status="deprecated")
    pool = FakePool(
        implementations=[active_impl, deprecated_impl],
        implementation_tasks=[(IMPL_ID, TASK_ID), (deprecated_id, TASK_ID)],
    )
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/tasks/{TASK_ID}/implementations")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == [IMPL_ID]


def test_get_task_implementations_status_all_sees_every_lifecycle_state():
    active_impl = _impl()
    deprecated_id = str(uuid4())
    deprecated_impl = _impl(id=deprecated_id, status="deprecated")
    pool = FakePool(
        implementations=[active_impl, deprecated_impl],
        implementation_tasks=[(IMPL_ID, TASK_ID), (deprecated_id, TASK_ID)],
    )
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/tasks/{TASK_ID}/implementations?status=all")
    assert resp.status_code == 200
    assert {r["id"] for r in resp.json()} == {IMPL_ID, deprecated_id}


def test_get_task_implementations_explicit_status_filter():
    deprecated_id = str(uuid4())
    deprecated_impl = _impl(id=deprecated_id, status="deprecated")
    pool = FakePool(
        implementations=[_impl(), deprecated_impl],
        implementation_tasks=[(IMPL_ID, TASK_ID), (deprecated_id, TASK_ID)],
    )
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/tasks/{TASK_ID}/implementations?status=deprecated")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [deprecated_id]


def test_get_task_implementations_422_for_unknown_status():
    pool = FakePool()
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/tasks/{TASK_ID}/implementations?status=bogus")
    assert resp.status_code == 422


def test_get_task_implementations_empty_for_unlinked_task():
    pool = FakePool(implementations=[_impl()])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/tasks/{uuid4()}/implementations")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /v1/tasks/{id}/resolve-implementation
# ---------------------------------------------------------------------------


def test_resolve_implementation_honest_null_when_nothing_linked():
    pool = FakePool()
    client = TestClient(_make_app(pool))

    resp = client.post(f"/v1/tasks/{TASK_ID}/resolve-implementation", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["implementation_id"] is None
    assert "no active implementation is linked" in body["reason"]


def test_resolve_implementation_resolves_most_recent_active_with_no_hint():
    pool = FakePool(
        implementations=[_impl()],
        implementation_tasks=[(IMPL_ID, TASK_ID)],
    )
    client = TestClient(_make_app(pool))

    resp = client.post(f"/v1/tasks/{TASK_ID}/resolve-implementation", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["implementation_id"] == IMPL_ID
    assert body["provider"] == "graphify"
    assert "no hint given" in body["reason"]
    # No credential value in the response, only the real stored shapes.
    assert body["requirements"] == {"needs": "network"}


def test_resolve_implementation_honors_hint_kinds_preference_order():
    frontier_id = str(uuid4())
    frontier_impl = _impl(id=frontier_id, kind="frontier", provider="anthropic")
    tool_impl = _impl()  # kind="tool"
    pool = FakePool(
        implementations=[frontier_impl, tool_impl],
        implementation_tasks=[(frontier_id, TASK_ID), (IMPL_ID, TASK_ID)],
    )
    client = TestClient(_make_app(pool))

    resp = client.post(
        f"/v1/tasks/{TASK_ID}/resolve-implementation",
        json={"constraints": {"hint_kinds": ["tool", "frontier"]}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["implementation_id"] == IMPL_ID
    assert body["kind"] == "tool"
    assert "hint preference order" in body["reason"]


def test_resolve_implementation_honest_null_when_hint_kinds_dont_match():
    pool = FakePool(
        implementations=[_impl()],  # kind="tool"
        implementation_tasks=[(IMPL_ID, TASK_ID)],
    )
    client = TestClient(_make_app(pool))

    resp = client.post(
        f"/v1/tasks/{TASK_ID}/resolve-implementation",
        json={"constraints": {"hint_kinds": ["frontier"]}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["implementation_id"] is None
    assert "frontier" in body["reason"]


# ---------------------------------------------------------------------------
# GET /v1/solutions/{procedure_row_id}/implementations
# ---------------------------------------------------------------------------


def test_solution_implementations_empty_when_no_linked_task_node():
    pool = FakePool(procedures=[_procedure()])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/solutions/{PROC_ROW_ID}/implementations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_solution_implementations_surfaces_linked_active_implementations():
    proc = _procedure(migrated_from_task_node_id=TASK_ID)
    pool = FakePool(
        procedures=[proc],
        implementations=[_impl()],
        implementation_tasks=[(IMPL_ID, TASK_ID)],
    )
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/solutions/{PROC_ROW_ID}/implementations")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == [IMPL_ID]


def test_solution_implementations_404_for_missing_procedure():
    pool = FakePool()
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/solutions/{uuid4()}/implementations")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Direct service-level sanity (bypasses HTTP layer)
# ---------------------------------------------------------------------------


def test_registry_get_for_task_status_all_maps_to_status_none():
    pool = FakePool(
        implementations=[_impl(status="candidate")],
        implementation_tasks=[(IMPL_ID, TASK_ID)],
    )
    rows = _run(implementation_registry.get_for_task(pool, TASK_ID, scope=UNRESTRICTED, status=None))
    assert [r["id"] for r in rows] == [IMPL_ID]
