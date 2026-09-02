"""
DB-free coverage for app/services/claim_graph_api.py (the six composed
Claim Graph API functions) and app/api/claims.py (the /v1/claims router).

Same FakePool idiom as test_claim_traversal_offline.py/
test_claim_evidence_offline.py: a hand-rolled pool that inspects the SQL
text of the exact, already-real queries each composed dependency issues
(claims.get_claim_relations' `fetch` against edges,
claim_evidence.get_claim_evidence's `fetch` against evidence,
claim_impact.find_procedures_referencing_claim's `fetch` against
procedures, claims.get_claim_version_chain's fetchrow+fetch against
knowledge_nodes, claim_temporal.get_claim_commit_history's follow-up
fetches) plus claim_graph_api.py's own new queries (`get_claim`,
`_visible_ids`) -- no shared fake, per this repo's own convention that
each offline test file hand-rolls its own.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.claims import router as claims_router
from app.services import claim_graph_api
from app.services.access import AccessScope

NOW = datetime.now(timezone.utc)

C1 = "00000000-0000-4000-8000-000000000001"  # public root claim
C2 = "00000000-0000-4000-8000-000000000002"  # public, supersedes C1 (chain v2)
C3 = "00000000-0000-4000-8000-000000000003"  # public, hop-2 from C1 via C2
C_PRIVATE = "00000000-0000-4000-8000-0000000000aa"  # private, owned by "someone-else"
MISSING = "00000000-0000-4000-8000-000000000fff"

PROC_PUBLIC = "00000000-0000-4000-8000-000000000010"
PROC_PRIVATE = "00000000-0000-4000-8000-000000000011"

EV_PUBLIC = "00000000-0000-4000-8000-000000000020"
EV_PRIVATE = "00000000-0000-4000-8000-000000000021"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _run(coro):
    return asyncio.run(coro)


def _claim_row(claim_id, *, visibility="public", owner_id=None, properties=None):
    return {
        "id": claim_id,
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "node_type": "claim",
        "name": "a claim",
        "properties": properties or {"statement": "a claim"},
        "created_by": "test",
        "t_valid": NOW - timedelta(days=1),
        "t_invalid": None,
        "scope_type": None,
        "scope_entity_id": None,
        "visibility": visibility,
        "owner_id": owner_id,
    }


class FakePool:
    """
    Backs every real query claim_graph_api.py and its composed
    dependencies issue. Visibility is honored by inspecting the actual
    SQL fragment access.py's visibility_predicate() produced (the same
    three shapes it can ever emit: 'TRUE', "visibility = 'public'", or
    "(visibility = 'public' OR owner_id = $N)") rather than re-deriving
    scope logic independently.
    """

    def __init__(self, *, claims=(), edges=(), evidence=(), procedures=()):
        self.claims = {c["id"]: c for c in claims}
        self.edges = list(edges)
        self.evidence = {e["id"]: e for e in evidence}
        self.procedures = {p["id"]: p for p in procedures}
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    @staticmethod
    def _owner_param(sql: str, params: tuple):
        if "owner_id = $" in sql:
            return params[-1]
        return "__no_owner_filter__"

    def _row_visible(self, sql: str, params: tuple, row: dict) -> bool:
        if "owner_id = $" in sql:
            viewer = self._owner_param(sql, params)
            return row.get("visibility") == "public" or row.get("owner_id") == viewer
        if "visibility = 'public'" in sql:
            return row.get("visibility") == "public"
        return True  # TRUE -- unrestricted

    def _claim_rows_visible(self, norm: str, params: tuple):
        return [c for c in self.claims.values() if self._row_visible(norm, params, c)]

    async def fetchval(self, sql, *params):
        norm = _norm(sql)
        self.fetch_calls.append((norm, params))
        if norm.startswith("SELECT count(*) FROM knowledge_nodes WHERE node_type = 'claim'"):
            rows = self._claim_rows_visible(norm, params)
            if "truth_state" in norm and "= 'IN'" in norm:
                rows = [c for c in rows if c["properties"].get("truth_state", "IN") == "IN"]
            return len(rows)
        raise AssertionError(f"unexpected fetchval: {norm}")

    async def fetchrow(self, sql, *params):
        norm = _norm(sql)
        self.fetchrow_calls.append((norm, params))

        if norm.startswith("SELECT * FROM knowledge_nodes WHERE id = $1::uuid"):
            row = self.claims.get(params[0])
            if row is None or not self._row_visible(norm, params, row):
                return None
            return dict(row)

        if norm.startswith("SELECT properties FROM knowledge_nodes WHERE id = $1::uuid"):
            row = self.claims.get(params[0])
            return {"properties": dict(row["properties"])} if row else None

        raise AssertionError(f"unexpected fetchrow: {norm}")

    async def fetch(self, sql, *params):
        norm = _norm(sql)
        self.fetch_calls.append((norm, params))

        if norm.startswith(
            "SELECT id, name, properties, t_valid, created_by, scope_type, scope_entity_id "
            "FROM knowledge_nodes WHERE node_type = 'claim'"
        ):
            limit = params[0]
            rows = self._claim_rows_visible(norm, params)
            if "truth_state" in norm and "= 'IN'" in norm:
                rows = [c for c in rows if c["properties"].get("truth_state", "IN") == "IN"]
            if "name ILIKE $" in norm:
                needle = str(params[-1]).strip("%").lower()
                rows = [c for c in rows if needle in (c["name"] or "").lower()]
            rows = sorted(rows, key=lambda c: c["t_valid"], reverse=True)[:limit]
            return [
                {
                    "id": c["id"], "name": c["name"], "properties": dict(c["properties"]),
                    "t_valid": c["t_valid"], "created_by": c["created_by"],
                    "scope_type": c["scope_type"], "scope_entity_id": c["scope_entity_id"],
                }
                for c in rows
            ]

        if norm.startswith(
            "SELECT id, source_id, target_id, custom_edge_type AS relation, created_by, t_valid "
            "FROM edges WHERE source_table = 'knowledge_nodes'"
        ):
            wanted, ids = set(params[0]), set(params[1])
            return [
                {
                    "id": e["id"], "source_id": e["source_id"], "target_id": e["target_id"],
                    "relation": e["relation"], "created_by": e["created_by"],
                    "t_valid": e["t_valid"],
                }
                for e in self.edges
                if e["relation"] in wanted
                and e["source_id"] in ids and e["target_id"] in ids
            ]

        if "FROM edges e WHERE" in norm:
            claim_id, wanted = params[0], set(params[1])
            return [
                dict(e) for e in self.edges
                if e["relation"] in wanted
                and (e["source_id"] == claim_id or e["target_id"] == claim_id)
            ]

        if "FROM evidence WHERE target_type = 'claim'" in norm:
            claim_id = params[0]
            return [dict(e) for e in self.evidence.values() if e["target_id"] == claim_id]

        if "FROM evidence WHERE id = ANY" in norm:
            ids = set(params[0])
            return [
                {"id": e["id"]} for e in self.evidence.values()
                if e["id"] in ids and self._row_visible(norm, params, e)
            ]

        if "FROM procedures WHERE t_invalid IS NULL AND preconditions" in norm:
            probe = params[0]
            wanted_claim_id = probe[0]["claim_id"]
            return [
                {"id": p["id"], "name": p["name"]} for p in self.procedures.values()
                if any(pc.get("claim_id") == wanted_claim_id for pc in p["preconditions"])
            ]

        if "FROM procedures WHERE id = ANY" in norm:
            ids = set(params[0])
            return [
                {"id": p["id"]} for p in self.procedures.values()
                if p["id"] in ids and self._row_visible(norm, params, p)
            ]

        if norm.startswith("SELECT id, properties FROM knowledge_nodes WHERE node_type = 'claim'"):
            family_id = params[0]
            return [
                {"id": c["id"], "properties": dict(c["properties"])}
                for c in self.claims.values()
                if c["id"] == family_id or c["properties"].get("claim_family_id") == family_id
            ]

        if norm.startswith("SELECT id, t_valid FROM knowledge_nodes WHERE id = ANY"):
            ids = set(params[0])
            return [
                {"id": c["id"], "t_valid": c["t_valid"]}
                for c in self.claims.values() if c["id"] in ids
            ]

        if "FROM episode_links el" in norm:
            return []  # no commit history wired in these fixtures -- honest empty

        if norm.startswith("SELECT id FROM knowledge_nodes WHERE id = ANY"):
            ids = set(params[0])
            return [
                {"id": c["id"]} for c in self.claims.values()
                if c["id"] in ids and self._row_visible(norm, params, c)
            ]

        if "CROSS JOIN LATERAL" in norm and "a.embedding <=> b.embedding" in norm:
            # embedding-similarity k-NN edge query -- offline fixtures carry no
            # real pgvector embeddings, so it honestly returns nothing. The
            # link_mode='relations' tests exercise the relation-edge path; the
            # similarity path is covered live in test_claim_graph_overview_e2e.
            return []

        raise AssertionError(f"unexpected fetch: {norm}")


def _edge(edge_id, source_id, target_id, relation):
    return {
        "id": edge_id, "source_id": source_id, "target_id": target_id,
        "relation": relation, "created_by": "test", "t_valid": NOW - timedelta(days=1),
        "properties": {},
    }


E1 = "00000000-0000-4000-8000-0000000000e1"
E2 = "00000000-0000-4000-8000-0000000000e2"
E3 = "00000000-0000-4000-8000-0000000000e3"


def _base_pool() -> FakePool:
    claims = [
        _claim_row(C1, properties={"statement": "c1"}),
        _claim_row(C2, properties={"statement": "c2", "claim_family_id": C1, "claim_version": 2}),
        _claim_row(C3, properties={"statement": "c3"}),
        _claim_row(C_PRIVATE, visibility="private", owner_id="someone-else",
                   properties={"statement": "private"}),
    ]
    edges = [
        _edge(E1, C1, C2, "SUPPORTS"),
        _edge(E2, C2, C3, "SUPPORTS"),
        _edge(E3, C1, C_PRIVATE, "CONTRADICTS"),
    ]
    evidence = [
        {"id": EV_PUBLIC, "target_type": "claim", "target_id": C1,
         "outcome_status": "success", "visibility": "public", "owner_id": None},
        {"id": EV_PRIVATE, "target_type": "claim", "target_id": C1,
         "outcome_status": "success", "visibility": "private", "owner_id": "someone-else"},
    ]
    procedures = [
        {"id": PROC_PUBLIC, "name": "public-proc", "visibility": "public", "owner_id": None,
         "preconditions": [{"claim_id": C1}]},
        {"id": PROC_PRIVATE, "name": "private-proc", "visibility": "private", "owner_id": "someone-else",
         "preconditions": [{"claim_id": C1}]},
    ]
    return FakePool(claims=claims, edges=edges, evidence=evidence, procedures=procedures)


ANON = AccessScope.anonymous()


# ---------------------------------------------------------------------------
# get_claim
# ---------------------------------------------------------------------------


def test_get_claim_returns_the_row_for_a_visible_claim():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim(pool, C1, scope=ANON))
    assert result is not None
    assert result["id"] == C1
    assert result["properties"]["statement"] == "c1"


def test_get_claim_returns_none_for_a_missing_claim():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim(pool, MISSING, scope=ANON))
    assert result is None


def test_get_claim_returns_none_for_a_claim_the_scope_cannot_see():
    """Same None as 'does not exist' -- no existence leak."""
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim(pool, C_PRIVATE, scope=ANON))
    assert result is None


def test_get_claim_returns_private_claim_to_its_owner():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim(pool, C_PRIVATE, scope=AccessScope.for_user("someone-else")))
    assert result is not None


# ---------------------------------------------------------------------------
# get_claim_neighbors
# ---------------------------------------------------------------------------


def test_get_claim_neighbors_composes_get_claim_relations():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_neighbors(pool, C1, scope=ANON))
    relations = {r["relation"] for r in result}
    assert "SUPPORTS" in relations


def test_get_claim_neighbors_omits_edges_to_an_invisible_claim():
    """e3 (C1 -CONTRADICTS-> C_PRIVATE) must not surface to an anonymous
    viewer -- the neighbor claim isn't visible to them."""
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_neighbors(pool, C1, scope=ANON))
    assert all(r["relation"] != "CONTRADICTS" for r in result)


def test_get_claim_neighbors_returns_empty_for_a_claim_the_scope_cannot_see():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_neighbors(pool, C_PRIVATE, scope=ANON))
    assert result == []


def test_get_claim_neighbors_returns_empty_for_missing_claim():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_neighbors(pool, MISSING, scope=ANON))
    assert result == []


# ---------------------------------------------------------------------------
# traverse_claim_graph
# ---------------------------------------------------------------------------


def test_traverse_claim_graph_reaches_hop_two():
    pool = _base_pool()
    result = _run(claim_graph_api.traverse_claim_graph(pool, C1, scope=ANON))
    edge_ids = {h["edge_id"] for h in result["supporting"]}
    assert edge_ids == {E1, E2}


def test_traverse_claim_graph_drops_hops_touching_an_invisible_claim():
    pool = _base_pool()
    result = _run(claim_graph_api.traverse_claim_graph(pool, C1, scope=ANON))
    all_hops = result["supporting"] + result["contradicting"] + result["other"]
    assert not any(h["to_claim_id"] == C_PRIVATE or h["from_claim_id"] == C_PRIVATE for h in all_hops)


def test_traverse_claim_graph_honest_empty_for_invisible_root():
    pool = _base_pool()
    result = _run(claim_graph_api.traverse_claim_graph(pool, C_PRIVATE, scope=ANON))
    assert result == {
        "claim_id": C_PRIVATE, "supporting": [], "contradicting": [], "other": [],
    }


# ---------------------------------------------------------------------------
# get_claim_evidence_api
# ---------------------------------------------------------------------------


def test_get_claim_evidence_api_returns_visible_evidence_only():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_evidence_api(pool, C1, scope=ANON))
    ids = {r["id"] for r in result}
    assert ids == {EV_PUBLIC}


def test_get_claim_evidence_api_empty_for_invisible_claim():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_evidence_api(pool, C_PRIVATE, scope=ANON))
    assert result == []


# ---------------------------------------------------------------------------
# get_claim_dependents
# ---------------------------------------------------------------------------


def test_get_claim_dependents_returns_visible_procedures_only():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_dependents(pool, C1, scope=ANON))
    ids = {p["id"] for p in result}
    assert ids == {PROC_PUBLIC}


def test_get_claim_dependents_empty_for_invisible_claim():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_dependents(pool, C_PRIVATE, scope=ANON))
    assert result == []


# ---------------------------------------------------------------------------
# get_claim_history
# ---------------------------------------------------------------------------


def test_get_claim_history_walks_the_version_chain():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_history(pool, C1, scope=ANON))
    ids = [r["claim_id"] for r in result]
    assert ids == [C1, C2]
    versions = {r["claim_id"]: r["version"] for r in result}
    assert versions[C1] == 1
    assert versions[C2] == 2


def test_get_claim_history_empty_for_invisible_claim():
    pool = _base_pool()
    result = _run(claim_graph_api.get_claim_history(pool, C_PRIVATE, scope=ANON))
    assert result == []


# ---------------------------------------------------------------------------
# get_claim_graph_overview
# ---------------------------------------------------------------------------

C_OUT = "00000000-0000-4000-8000-0000000000b0"
E_SUP = "00000000-0000-4000-8000-0000000000e9"
E_NONREL = "00000000-0000-4000-8000-0000000000ea"
UNRESTRICTED = AccessScope.unrestricted()


def _overview_pool() -> FakePool:
    def row(cid, stmt, days_old, *, visibility="public", owner_id=None, props_extra=None):
        r = _claim_row(cid, visibility=visibility, owner_id=owner_id,
                       properties={"statement": stmt, **(props_extra or {})})
        r["name"] = stmt
        r["t_valid"] = NOW - timedelta(days=days_old)
        return r

    claims = [
        row(C1, "alpha cache invariant", 1),
        row(C2, "alpha supporting note", 2),
        row(C3, "alpha newer rule", 3),
        row(C_OUT, "alpha older rule", 4, props_extra={"truth_state": "OUT"}),
        row(C_PRIVATE, "alpha private thing", 1, visibility="private", owner_id="someone-else"),
    ]
    edges = [
        _edge(E1, C2, C1, "SUPPORTS"),          # both shown
        _edge(E3, C1, C_PRIVATE, "CONTRADICTS"),  # endpoint not in the (anon) node set
        _edge(E_SUP, C3, C_OUT, "SUPERSEDES"),   # only in the node set when include_retired
        _edge(E_NONREL, C1, C2, "PRODUCES"),     # not a claim relation -> never an edge here
    ]
    return FakePool(claims=claims, edges=edges)


def test_overview_believed_only_by_default_and_scope_filtered():
    pool = _overview_pool()
    g = _run(claim_graph_api.get_claim_graph_overview(pool, scope=ANON, with_status=False))
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {C1, C2, C3}                 # OUT excluded, private excluded (anon)
    assert g["counts"]["claims_total"] == 3
    assert g["counts"]["claims_shown"] == 3
    assert all(n["status"] is None for n in g["nodes"])   # with_status=False
    assert [n["id"] for n in g["nodes"]] == [C1, C2, C3]   # newest t_valid first


def test_overview_unrestricted_scope_sees_private_claim():
    pool = _overview_pool()
    g = _run(claim_graph_api.get_claim_graph_overview(pool, scope=UNRESTRICTED, with_status=False))
    assert C_PRIVATE in {n["id"] for n in g["nodes"]}


def test_overview_include_retired_adds_out_claims():
    pool = _overview_pool()
    g = _run(claim_graph_api.get_claim_graph_overview(
        pool, scope=ANON, include_retired=True, with_status=False,
    ))
    ids = {n["id"] for n in g["nodes"]}
    assert C_OUT in ids
    node_out = next(n for n in g["nodes"] if n["id"] == C_OUT)
    assert node_out["truth_state"] == "OUT"
    assert g["include_retired"] is True


def test_overview_edges_only_among_shown_nodes_and_only_claim_relations():
    pool = _overview_pool()
    g = _run(claim_graph_api.get_claim_graph_overview(pool, scope=ANON, with_status=False))
    rels = {(e["source"], e["target"], e["relation"]) for e in g["edges"]}
    assert rels == {(C2, C1, "SUPPORTS")}     # E3 endpoint invisible, E_SUP node OUT, E_NONREL not a relation

    g2 = _run(claim_graph_api.get_claim_graph_overview(
        pool, scope=ANON, include_retired=True, with_status=False,
    ))
    rels2 = {(e["source"], e["target"], e["relation"]) for e in g2["edges"]}
    assert (C3, C_OUT, "SUPERSEDES") in rels2   # now both endpoints are in the node set


def test_overview_q_filters_on_statement():
    pool = _overview_pool()
    g = _run(claim_graph_api.get_claim_graph_overview(
        pool, scope=ANON, q="supporting", with_status=False,
    ))
    assert {n["id"] for n in g["nodes"]} == {C2}
    assert g["query"] == "supporting"


def test_overview_truncation_is_honest():
    pool = _overview_pool()
    g = _run(claim_graph_api.get_claim_graph_overview(
        pool, scope=ANON, limit=2, with_status=False,
    ))
    assert len(g["nodes"]) == 2
    assert g["truncated"] is True
    # limit is clamped, never trusted raw
    g2 = _run(claim_graph_api.get_claim_graph_overview(
        pool, scope=ANON, limit=99999, with_status=False,
    ))
    assert g2["truncated"] is False


def test_overview_router_graph_route_is_registered_before_claim_id():
    client = _client(_overview_pool())
    resp = client.get("/v1/claims/graph", params={"with_status": "false"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {n["id"] for n in body["nodes"]} == {C1, C2, C3}
    assert body["counts"]["edges"] == 1


# ---------------------------------------------------------------------------
# Router (/v1/claims/*)
# ---------------------------------------------------------------------------


def _client(pool) -> TestClient:
    app = FastAPI()
    app.include_router(claims_router)
    app.state.pool = pool
    return TestClient(app)


def test_router_get_claim_200_for_visible_claim():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{C1}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == C1


def test_router_get_claim_404_for_missing_claim():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{MISSING}")
    assert resp.status_code == 404


def test_router_get_claim_404_for_invisible_claim():
    """Same 404 as missing -- no existence leak through the HTTP layer."""
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{C_PRIVATE}")
    assert resp.status_code == 404


def test_router_get_claim_200_for_owner_viewing_their_own_private_claim():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{C_PRIVATE}", headers={"X-Viewer-Id": "someone-else"})
    assert resp.status_code == 200


def test_router_neighbors_200_and_omits_invisible_neighbor():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{C1}/neighbors")
    assert resp.status_code == 200
    relations = {r["relation"] for r in resp.json()}
    assert "CONTRADICTS" not in relations


def test_router_neighbors_404_for_missing_claim():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{MISSING}/neighbors")
    assert resp.status_code == 404


def test_router_neighbors_422_for_unknown_relation():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{C1}/neighbors", params={"relation": "NOT_A_RELATION"})
    assert resp.status_code == 422


def test_router_traverse_200():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{C1}/traverse")
    assert resp.status_code == 200
    body = resp.json()
    assert body["claim_id"] == C1
    assert {h["edge_id"] for h in body["supporting"]} == {E1, E2}


def test_router_evidence_200_omits_invisible_row():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{C1}/evidence")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert ids == {EV_PUBLIC}


def test_router_dependents_200_omits_invisible_procedure():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{C1}/dependents")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert ids == {PROC_PUBLIC}


def test_router_history_200():
    client = _client(_base_pool())
    resp = client.get(f"/v1/claims/{C1}/history")
    assert resp.status_code == 200
    ids = [r["claim_id"] for r in resp.json()]
    assert ids == [C1, C2]
