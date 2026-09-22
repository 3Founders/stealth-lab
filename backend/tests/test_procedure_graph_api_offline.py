"""
DB-free coverage for app/services/procedure_graph_api.py and its two
routers (app/api/procedures.py, app/api/solutions.py).

Same hand-rolled-FakePool idiom as test_claim_traversal_offline.py /
test_applicability_hard_constraints_offline.py: a fake that inspects the
SQL text of the exact, already-real queries this module issues -- no
shared fake, per this repo's own convention that each offline test file
hand-rolls its own.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_scope
from app.api.procedures import router as procedures_router
from app.api.solutions import router as solutions_router
from app.execution.procedure_graph import UnresolvedSubprocedureRef
from app.services.access import AccessScope
from app.services.procedures import OUTCOME_WRITER_STAMP
from app.services.procedure_graph_api import (
    get_procedure_claims,
    get_procedure_detail,
    get_procedure_evidence,
    get_procedure_graph,
    get_procedure_versions,
    get_solution_view,
)

NOW = datetime.now(timezone.utc)

PROC_ROW_ID = str(uuid4())
PROC_ID = str(uuid4())
CLAIM_ID = str(uuid4())
OTHER_CLAIM_ID = str(uuid4())


def _procedure(**overrides):
    row = {
        "id": PROC_ROW_ID,
        "procedure_id": PROC_ID,
        "family_id": None,
        "version": 1,
        "name": "fix the flaky test",
        "goal": "make CI green",
        "steps": [{"order": 0, "goal": "run pytest"}],
        "preconditions": [
            {"subject": "project:p", "predicate": "uses", "object": "pytest", "claim_id": CLAIM_ID},
        ],
        "invariants": [],
        "verification_state": "verified",
        "staleness": "fresh",
        "availability": "active",
        "approval_status": "approved",
        "scope_type": "global",
        "scope_entity_id": None,
        "provenance": "system_pending_review",
        "domain": None,
        "created_by": "tester",
        "owner_id": None,
        "visibility": "public",
        "t_valid": NOW - timedelta(days=1),
        "t_invalid": None,
        "t_created": NOW - timedelta(days=1),
        "verification_stats": {
            "attempts": 4, "successes": 3, "match_cost_total": 2.0,
            "realised_savings_total": 10.0, "mean_steps": 5,
        },
    }
    row.update(overrides)
    return row


def _claim(claim_id, **overrides):
    row = {
        "id": claim_id,
        "node_type": "claim",
        "properties": {"subject": "project:p", "predicate": "uses", "object": "pytest"},
        "visibility": "public",
        "owner_id": None,
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "t_invalid": None,
    }
    row.update(overrides)
    return row


def _evidence(evidence_id, **overrides):
    row = {
        "id": evidence_id,
        "evidence_type": "execution_result",
        "target_type": "procedure",
        "target_id": PROC_ROW_ID,
        "target_version": 1,
        "direction": "supports",
        "outcome_status": "success",
        "independence_group": None,
        "visibility": "public",
        "owner_id": None,
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "t_valid": NOW,
        "t_invalid": None,
        # Real execution_result evidence is only ever written by the
        # trusted execution-outcome pipeline (app.services.procedures.
        # record_execution_outcome) -- app.services.evidence_trust now
        # requires this to count as VERIFIED, not just CLAIMED. Default
        # here matches what a real row of this evidence_type looks like;
        # a test asserting the CLAIMED-only path overrides it explicitly.
        "created_by": OUTCOME_WRITER_STAMP,
    }
    row.update(overrides)
    return row


def _passes_visibility(row: dict, sql: str) -> bool:
    """Real predicate text comes from `visibility_predicate()`/
    `scope_predicates()` -- this fake only needs to recognize the two
    shapes this test file's scopes actually produce: `TRUE` (unrestricted,
    always passes) and a bare `visibility = 'public'` (anonymous scope,
    passes only public rows). The owner_id-OR branch (signed-in scope) is
    not exercised by this file's tests."""
    if "visibility = 'public'" in sql:
        return row.get("visibility") == "public"
    return True


class FakePool:
    def __init__(self, *, procedures=(), claims=(), evidence=(), executions=()):
        self._procedures = list(procedures)
        self._claims = list(claims)
        self._evidence = list(evidence)
        self._executions = list(executions)
        self.fetch_calls = []
        self.fetchrow_calls = []

    async def fetchrow(self, sql, *params):
        self.fetchrow_calls.append((" ".join(sql.split()), params))
        norm = " ".join(sql.split())
        if "FROM procedures WHERE id" in norm:
            target = str(params[0])
            for row in self._procedures:
                if str(row["id"]) == target and _passes_visibility(row, norm):
                    return dict(row)
            return None
        if "FROM procedures WHERE procedure_id" in norm and "version" in norm:
            pid, version = str(params[0]), params[1]
            for row in self._procedures:
                if str(row["procedure_id"]) == pid and row["version"] == version:
                    return dict(row)
            return None
        raise AssertionError(f"unexpected fetchrow: {norm}")

    async def fetch(self, sql, *params):
        self.fetch_calls.append((" ".join(sql.split()), params))
        norm = " ".join(sql.split())
        if "FROM knowledge_nodes" in norm:
            wanted = {str(x) for x in params[0]}
            return [dict(c) for c in self._claims if str(c["id"]) in wanted and c["t_invalid"] is None]
        if "FROM evidence" in norm:
            target = str(params[0])
            return [
                dict(e) for e in self._evidence
                if str(e["target_id"]) == target and e["t_invalid"] is None
            ]
        if "FROM procedures WHERE procedure_id" in norm:
            pid = str(params[0])
            return sorted(
                (dict(r) for r in self._procedures if str(r["procedure_id"]) == pid),
                key=lambda r: r["version"],
            )
        raise AssertionError(f"unexpected fetch: {norm}")


def _run(coro):
    return asyncio.run(coro)


UNRESTRICTED = AccessScope.unrestricted()


# --- get_procedure_detail ---------------------------------------------


def test_get_procedure_detail_composes_claims_and_evidence_summary():
    pool = FakePool(
        procedures=[_procedure()],
        claims=[_claim(CLAIM_ID)],
        evidence=[_evidence("ev-1", outcome_status="success"), _evidence("ev-2", outcome_status="failure")],
    )
    detail = _run(get_procedure_detail(pool, PROC_ROW_ID, scope=UNRESTRICTED))

    assert detail["id"] == PROC_ROW_ID
    assert detail["procedure_id"] == PROC_ID
    assert len(detail["claims"]) == 1
    assert detail["claims"][0]["id"] == CLAIM_ID
    # app.services.evidence_trust.summarize's canonical shape (knowledge/
    # verification consolidation pass): success_count/failure_count now
    # mean VERIFIED specifically -- both fixture rows are evidence_type
    # 'execution_result' written by OUTCOME_WRITER_STAMP (see _evidence's
    # default), so both count as verified here.
    assert detail["evidence_summary"] == {
        "total": 2, "outcome_bearing_total": 2,
        "unknown": 0, "claimed_success": 0, "verified_success": 1, "verified_failure": 1,
        "success_count": 1, "failure_count": 1,
    }
    assert detail["executor_kinds"] == []


def test_get_procedure_detail_returns_none_for_missing_row():
    pool = FakePool()
    detail = _run(get_procedure_detail(pool, str(uuid4()), scope=UNRESTRICTED))
    assert detail is None


def test_get_procedure_detail_returns_none_for_invisible_private_row():
    """Fails without a real visibility filter: a private procedure owned
    by someone else must be invisible to an anonymous viewer."""
    private = _procedure(visibility="private", owner_id="someone-else")
    pool = FakePool(procedures=[private])

    detail = _run(get_procedure_detail(pool, PROC_ROW_ID, scope=AccessScope.anonymous()))
    assert detail is None


def test_get_procedure_detail_lists_executor_kinds_named_by_step_bindings():
    proc = _procedure(steps=[
        {"order": 0, "goal": "lint", "binding": {"kind": "command", "command": "make lint"}},
        {"order": 1, "goal": "call the model", "binding": {"kind": "model", "model": "gemma"}},
        {"order": 2, "goal": "small model", "binding": {"kind": "slm_artifact", "slm_artifact": "tiny"}},
    ])
    pool = FakePool(procedures=[proc])

    detail = _run(get_procedure_detail(pool, PROC_ROW_ID, scope=UNRESTRICTED))
    assert detail["executor_kinds"] == ["deterministic", "frontier", "slm"]


# --- get_procedure_claims ----------------------------------------------


def test_get_procedure_claims_reads_via_precondition_claim_id():
    pool = FakePool(procedures=[_procedure()], claims=[_claim(CLAIM_ID)])
    claims = _run(get_procedure_claims(pool, PROC_ROW_ID, scope=UNRESTRICTED))
    assert [c["id"] for c in claims] == [CLAIM_ID]


def test_get_procedure_claims_empty_when_no_precondition_carries_claim_id():
    proc = _procedure(preconditions=[{"subject": "x", "predicate": "y", "object": "z"}])
    pool = FakePool(procedures=[proc])
    claims = _run(get_procedure_claims(pool, PROC_ROW_ID, scope=UNRESTRICTED))
    assert claims == []
    assert not any("FROM knowledge_nodes" in sql for sql, _ in pool.fetch_calls)


def test_get_procedure_claims_empty_for_missing_procedure():
    pool = FakePool()
    claims = _run(get_procedure_claims(pool, str(uuid4()), scope=UNRESTRICTED))
    assert claims == []


def test_get_procedure_claims_dedupes_repeated_claim_ids():
    proc = _procedure(preconditions=[
        {"subject": "a", "predicate": "p", "object": "o", "claim_id": CLAIM_ID},
        {"subject": "b", "predicate": "p", "object": "o", "claim_id": CLAIM_ID},
    ])
    pool = FakePool(procedures=[proc], claims=[_claim(CLAIM_ID)])
    claims = _run(get_procedure_claims(pool, PROC_ROW_ID, scope=UNRESTRICTED))
    assert len(claims) == 1


# --- get_procedure_evidence ---------------------------------------------


def test_get_procedure_evidence_returns_live_rows_for_this_row():
    pool = FakePool(
        procedures=[_procedure()],
        evidence=[_evidence("ev-1"), _evidence("ev-2", target_id=str(uuid4()))],
    )
    evidence = _run(get_procedure_evidence(pool, PROC_ROW_ID, scope=UNRESTRICTED))
    assert [e["id"] for e in evidence] == ["ev-1"]


def test_get_procedure_evidence_excludes_retracted_rows():
    pool = FakePool(
        procedures=[_procedure()],
        evidence=[_evidence("ev-1", t_invalid=NOW)],
    )
    evidence = _run(get_procedure_evidence(pool, PROC_ROW_ID, scope=UNRESTRICTED))
    assert evidence == []


def test_get_procedure_evidence_empty_for_missing_procedure():
    pool = FakePool()
    evidence = _run(get_procedure_evidence(pool, str(uuid4()), scope=UNRESTRICTED))
    assert evidence == []


# --- get_procedure_versions ---------------------------------------------


def test_get_procedure_versions_walks_the_family_ordered():
    v1 = _procedure(version=1, id=PROC_ROW_ID)
    v2_id = str(uuid4())
    v2 = _procedure(version=2, id=v2_id, t_invalid=None)
    pool = FakePool(procedures=[v2, v1])  # deliberately out of order

    versions = _run(get_procedure_versions(pool, PROC_ID, scope=UNRESTRICTED))
    assert [v["version"] for v in versions] == [1, 2]


def test_get_procedure_versions_empty_for_unknown_procedure_id():
    pool = FakePool()
    versions = _run(get_procedure_versions(pool, str(uuid4()), scope=UNRESTRICTED))
    assert versions == []


# --- get_procedure_graph -------------------------------------------------


def test_get_procedure_graph_returns_linear_nodes_for_a_simple_procedure():
    proc = _procedure(steps=[
        {"order": 0, "goal": "first"},
        {"order": 1, "goal": "second"},
    ])
    pool = FakePool(procedures=[proc])

    graph = _run(get_procedure_graph(pool, PROC_ROW_ID, depth=2, scope=UNRESTRICTED))
    assert graph["procedure_id"] == PROC_ID
    assert graph["version"] == 1
    assert [n["goal"] for n in graph["nodes"]] == ["first", "second"]
    assert graph["nodes"][1]["deps"] == [0]


def test_get_procedure_graph_returns_none_for_missing_procedure():
    pool = FakePool()
    graph = _run(get_procedure_graph(pool, str(uuid4()), scope=UNRESTRICTED))
    assert graph is None


def test_get_procedure_graph_raises_on_unresolved_subprocedure_ref():
    """Fails without real wiring: a step pinning a nonexistent
    sub-procedure version must raise, never silently drop the step."""
    missing_ref_id = str(uuid4())
    proc = _procedure(steps=[
        {"order": 0, "goal": "delegate",
         "subprocedure_ref": {"procedure_id": missing_ref_id, "version": 1}},
    ])
    pool = FakePool(procedures=[proc])

    with pytest.raises(UnresolvedSubprocedureRef):
        _run(get_procedure_graph(pool, PROC_ROW_ID, scope=UNRESTRICTED))


# --- get_solution_view ----------------------------------------------------


def test_get_solution_view_composes_capability_cost_and_executors():
    proc = _procedure(steps=[{"order": 0, "goal": "lint", "binding": {"kind": "command", "command": "make lint"}}])
    pool = FakePool(
        procedures=[proc],
        claims=[_claim(CLAIM_ID)],
        evidence=[
            _evidence("ev-1", outcome_status="success", independence_group="g1"),
            _evidence("ev-2", outcome_status="success", independence_group="g2"),
            _evidence("ev-3", outcome_status="failure"),
        ],
    )
    solution = _run(get_solution_view(pool, PROC_ROW_ID, scope=UNRESTRICTED))

    assert solution["procedure_row_id"] == PROC_ROW_ID
    assert solution["executors"]["deterministic"]["supported"] is False
    assert solution["capability"]["evidence_count"] == 3
    assert solution["capability"]["success_count"] == 2
    assert solution["capability"]["independent_groups"] == 2
    assert solution["capability"]["level_gated"] is None
    assert solution["cost"]["average_match_cost"] == 0.5
    assert solution["cost"]["latency_ms"] is None
    assert "license" not in solution


def test_get_solution_view_defaults_to_frontier_when_no_binding_advertised():
    pool = FakePool(procedures=[_procedure()])
    solution = _run(get_solution_view(pool, PROC_ROW_ID, scope=UNRESTRICTED))
    assert "frontier" in solution["executors"]
    assert solution["executors"]["frontier"]["supported"] is True


def test_get_solution_view_returns_none_for_missing_procedure():
    pool = FakePool()
    solution = _run(get_solution_view(pool, str(uuid4()), scope=UNRESTRICTED))
    assert solution is None


# --- routers (TestClient) -------------------------------------------------


def _make_app(pool: FakePool) -> FastAPI:
    app = FastAPI()
    app.include_router(procedures_router)
    app.include_router(solutions_router)
    app.state.pool = pool
    app.dependency_overrides[get_scope] = lambda: UNRESTRICTED
    return app


def test_router_get_procedure_returns_200_and_composed_body():
    pool = FakePool(procedures=[_procedure()], claims=[_claim(CLAIM_ID)])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/procedures/{PROC_ROW_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == PROC_ROW_ID
    assert len(body["claims"]) == 1


def test_router_get_procedure_404s_for_missing_row():
    pool = FakePool()
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/procedures/{uuid4()}")
    assert resp.status_code == 404


def test_router_get_procedure_versions():
    v2_id = str(uuid4())
    pool = FakePool(procedures=[_procedure(version=1, id=PROC_ROW_ID), _procedure(version=2, id=v2_id)])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/procedures/{PROC_ROW_ID}/versions")
    assert resp.status_code == 200
    assert [v["version"] for v in resp.json()] == [1, 2]


def test_router_get_procedure_graph():
    pool = FakePool(procedures=[_procedure()])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/procedures/{PROC_ROW_ID}/graph?depth=3")
    assert resp.status_code == 200
    body = resp.json()
    assert body["depth"] == 3
    assert len(body["nodes"]) == 1


def test_router_get_procedure_graph_422s_on_composition_error():
    missing_ref_id = str(uuid4())
    proc = _procedure(steps=[
        {"order": 0, "goal": "delegate",
         "subprocedure_ref": {"procedure_id": missing_ref_id, "version": 1}},
    ])
    pool = FakePool(procedures=[proc])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/procedures/{PROC_ROW_ID}/graph")
    assert resp.status_code == 422


def test_router_get_procedure_claims():
    pool = FakePool(procedures=[_procedure()], claims=[_claim(CLAIM_ID)])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/procedures/{PROC_ROW_ID}/claims")
    assert resp.status_code == 200
    assert [c["id"] for c in resp.json()] == [CLAIM_ID]


def test_router_get_procedure_evidence():
    pool = FakePool(procedures=[_procedure()], evidence=[_evidence("ev-1")])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/procedures/{PROC_ROW_ID}/evidence")
    assert resp.status_code == 200
    assert [e["id"] for e in resp.json()] == ["ev-1"]


def test_router_get_solution():
    pool = FakePool(procedures=[_procedure()])
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/solutions/{PROC_ROW_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["procedure_row_id"] == PROC_ROW_ID
    assert "capability" in body and "cost" in body


def test_router_get_solution_404s_for_missing_row():
    pool = FakePool()
    client = TestClient(_make_app(pool))

    resp = client.get(f"/v1/solutions/{uuid4()}")
    assert resp.status_code == 404
