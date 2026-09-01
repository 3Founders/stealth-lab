"""
Offline tests for repository_knowledge.py + its two routers
(app/api/repositories.py, app/api/projects.py).

FakeDB mirrors the pool/connection idiom already used in test_claims.py
(itself following test_failure_capture.py / test_knowledge_conflict_and_
supersession.py) -- scoped to the exact queries repository_knowledge.py
and claims.py::get_claim_lifecycle_state issue. Rows are inserted
directly into the fake tables rather than via capture_claim(), because
these tests need full control over scope_type/scope_entity_id/
visibility/owner_id/t_valid without going through claims.py's embedder
dependency.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.projects import router as projects_router
from app.api.repositories import router as repositories_router
from app.services.access import AccessScope
from app.services.repository_knowledge import (
    get_project_knowledge,
    get_repository_knowledge,
)


class FakeDB:
    def __init__(self):
        self.knowledge_nodes: dict[str, dict] = {}
        self.procedures: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.triggers: dict[str, dict] = {}
        self.debates: dict[str, dict] = {}

    # -- fixture helpers ---------------------------------------------

    def add_claim(
        self,
        *,
        statement: str,
        scope_type: str | None,
        scope_entity_id: str | None,
        truth_state: str = "IN",
        visibility: str = "public",
        owner_id: str | None = None,
        t_valid: datetime | None = None,
        confidence: float | None = None,
    ) -> str:
        cid = str(uuid4())
        self.knowledge_nodes[cid] = {
            "id": UUID(cid), "node_type": "claim", "name": statement[:200],
            "properties": {
                "statement": statement, "truth_state": truth_state,
                **({"confidence": confidence} if confidence is not None else {}),
            },
            "scope_type": scope_type, "scope_entity_id": scope_entity_id,
            "t_invalid": None,
            "t_valid": t_valid or datetime.now(timezone.utc),
            "visibility": visibility, "owner_id": owner_id,
        }
        return cid

    def add_procedure(
        self,
        *,
        name: str,
        goal: str,
        scope_type: str | None,
        scope_entity_id: str | None,
        verification_state: str = "verified",
        staleness: str = "fresh",
        availability: str = "active",
        approval_status: str | None = "approved",
        visibility: str = "public",
        owner_id: str | None = None,
        t_valid: datetime | None = None,
        version: int = 1,
    ) -> str:
        pid = str(uuid4())
        self.procedures[pid] = {
            "id": UUID(pid), "procedure_id": UUID(pid), "name": name, "goal": goal,
            "verification_state": verification_state, "staleness": staleness,
            "availability": availability, "approval_status": approval_status,
            "version": version, "t_valid": t_valid or datetime.now(timezone.utc),
            "t_invalid": None, "scope_type": scope_type,
            "scope_entity_id": scope_entity_id, "visibility": visibility,
            "owner_id": owner_id,
        }
        return pid

    def add_conflict_trigger(self, claim_id: str) -> None:
        """Makes claim_id 'disputed' per claims.py's own real detection:
        a task_node -CONFLICTS_WITH-> claim edge with an unresolved
        trigger. We don't model task_nodes here at all -- the
        _DISPUTED_CLAIM_SQL join only cares about edge.source_id
        matching a triggers.task_node_id, so a bare uuid stands in."""
        fake_task_node_id = str(uuid4())
        self.edges.append({
            "edge_type": "VALIDATED_BY", "custom_edge_type": "CONFLICTS_WITH",
            "source_id": UUID(fake_task_node_id), "source_table": "task_nodes",
            "target_id": UUID(claim_id), "target_table": "knowledge_nodes",
        })
        trig_id = str(uuid4())
        self.triggers[trig_id] = {"task_node_id": fake_task_node_id}

    # -- pool interface used directly (no .acquire()) -----------------

    async def fetch(self, query: str, *params):
        q = query.strip()

        if "FROM knowledge_nodes k WHERE k.node_type = 'claim'" in q and "scope_type" in q:
            return self._claims_query(q, params)

        if "FROM procedures p WHERE p.scope_type" in q:
            return self._procedures_query(q, params)

        if q.startswith("SELECT e.id, e.source_id, e.target_id, e.custom_edge_type"):
            # get_claim_relations() -- no relation edges modeled in these
            # tests, always empty (no claim here is SUPPORTED/etc.).
            return []

        raise AssertionError(f"FakeDB.fetch: unrecognized query\n{q}")

    async def fetchrow(self, query: str, *params):
        q = query.strip()
        if q.startswith("SELECT properties, t_valid FROM knowledge_nodes"):
            (claim_id,) = params
            node = self.knowledge_nodes.get(str(claim_id))
            if node is None or node["node_type"] != "claim":
                return None
            return {"properties": node["properties"], "t_valid": node["t_valid"]}
        raise AssertionError(f"FakeDB.fetchrow: unrecognized query\n{q}")

    async def fetchval(self, query: str, *params):
        q = query.strip()
        if "triggers t" in q:
            # has_open_conflict_trigger()
            (claim_id,) = params
            for e in self.edges:
                if not (
                    e["custom_edge_type"] == "CONFLICTS_WITH"
                    and e["target_table"] == "knowledge_nodes"
                    and str(e["target_id"]) == str(claim_id)
                ):
                    continue
                task_node_id = str(e["source_id"])
                for trig_id, trig in self.triggers.items():
                    if trig["task_node_id"] != task_node_id:
                        continue
                    debates = [d for d in self.debates.values() if d["trigger_id"] == trig_id]
                    if not debates:
                        return True
                    if any(d["state"] not in ("APPROVED", "REJECTED") for d in debates):
                        return True
            return False
        if q.startswith("SELECT EXISTS (SELECT 1 FROM edges WHERE source_table = 'knowledge_nodes'"):
            (claim_id,) = params
            wanted_relation = "SUPERSEDES" if "'SUPERSEDES'" in q else "CONTRADICTS"
            return any(
                e.get("source_table") == "knowledge_nodes"
                and e.get("target_table") == "knowledge_nodes"
                and str(e.get("target_id")) == str(claim_id)
                and e.get("custom_edge_type") == wanted_relation
                for e in self.edges
            )
        raise AssertionError(f"FakeDB.fetchval: unrecognized query\n{q}")

    # -- query interpreters ---------------------------------------------

    def _split_scope_params(self, q: str, params: tuple):
        """Common param-layout parser for both scoped queries: optional
        leading viewer_id (for_user scope), then scope_type,
        scope_entity_id, optional focus pattern, then limit last."""
        idx = 0
        viewer_id = None
        if "owner_id = $" in q:
            viewer_id = params[idx]
            idx += 1
        scope_type = params[idx]; idx += 1
        scope_entity_id = params[idx]; idx += 1
        focus_pattern = None
        if "ILIKE" in q:
            focus_pattern = params[idx].strip("%").lower()
            idx += 1
        limit = params[idx]
        return viewer_id, scope_type, scope_entity_id, focus_pattern, limit

    def _visible(self, q: str, viewer_id, row: dict) -> bool:
        if "AND TRUE " in q or q.rstrip().endswith("TRUE"):
            return True
        if "owner_id = $" in q:
            return row["visibility"] == "public" or row["owner_id"] == viewer_id
        return row["visibility"] == "public"

    def _claims_query(self, q: str, params: tuple):
        viewer_id, scope_type, scope_entity_id, focus_pattern, limit = (
            self._split_scope_params(q, params)
        )
        rows = []
        for node in self.knowledge_nodes.values():
            if node["node_type"] != "claim":
                continue
            if node["t_invalid"] is not None:
                continue
            if node["scope_type"] != scope_type or node["scope_entity_id"] != scope_entity_id:
                continue
            if node["properties"].get("truth_state", "IN") == "OUT":
                continue
            if focus_pattern and focus_pattern not in node["properties"].get("statement", "").lower():
                continue
            if not self._visible(q, viewer_id, node):
                continue
            rows.append(node)
        rows.sort(key=lambda r: r["t_valid"], reverse=True)
        rows = rows[:limit]
        return [
            {"id": r["id"], "name": r["name"], "properties": r["properties"], "t_valid": r["t_valid"]}
            for r in rows
        ]

    def _procedures_query(self, q: str, params: tuple):
        viewer_id, scope_type, scope_entity_id, focus_pattern, limit = (
            self._split_scope_params(q, params)
        )
        rows = []
        for proc in self.procedures.values():
            if proc["t_invalid"] is not None:
                continue
            if proc["scope_type"] != scope_type or proc["scope_entity_id"] != scope_entity_id:
                continue
            if focus_pattern and (
                focus_pattern not in proc["name"].lower()
                and focus_pattern not in proc["goal"].lower()
            ):
                continue
            if not self._visible(q, viewer_id, proc):
                continue
            rows.append(proc)
        rows.sort(key=lambda r: r["t_valid"], reverse=True)
        rows = rows[:limit]
        return [
            {
                "id": r["id"], "procedure_id": r["procedure_id"], "name": r["name"],
                "goal": r["goal"], "verification_state": r["verification_state"],
                "staleness": r["staleness"], "availability": r["availability"],
                "approval_status": r["approval_status"], "version": r["version"],
                "t_valid": r["t_valid"],
            }
            for r in rows
        ]


class TestGetRepositoryKnowledge:
    def test_returns_only_claims_and_procedures_scoped_to_this_repository(self):
        db = FakeDB()
        db.add_claim(statement="repo A uses pandas 2.0", scope_type="repository", scope_entity_id="org/repo-a")
        db.add_claim(statement="repo B uses numpy", scope_type="repository", scope_entity_id="org/repo-b")
        db.add_procedure(
            name="build repo A", goal="build it",
            scope_type="repository", scope_entity_id="org/repo-a",
        )
        db.add_procedure(
            name="build repo B", goal="build it",
            scope_type="repository", scope_entity_id="org/repo-b",
        )

        result = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", scope=AccessScope.unrestricted(),
        ))

        assert result["repository_id"] == "org/repo-a"
        assert len(result["claims"]) == 1
        assert result["claims"][0]["statement"] == "repo A uses pandas 2.0"
        assert len(result["relevant_procedures"]) == 1
        assert result["relevant_procedures"][0]["name"] == "build repo A"

    def test_unknown_repository_id_returns_empty_not_an_error(self):
        db = FakeDB()
        db.add_claim(statement="something", scope_type="repository", scope_entity_id="org/real-repo")

        result = asyncio.run(get_repository_knowledge(
            db, "org/nonexistent", scope=AccessScope.unrestricted(),
        ))
        assert result["claims"] == []
        assert result["relevant_procedures"] == []
        assert result["conflicts"] == []

    def test_claims_carry_computed_lifecycle_state(self):
        db = FakeDB()
        db.add_claim(statement="a plain current claim", scope_type="repository", scope_entity_id="org/repo-a")

        result = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", scope=AccessScope.unrestricted(),
        ))
        assert result["claims"][0]["lifecycle_state"] == "current"

    def test_disputed_claims_surface_as_conflicts(self):
        db = FakeDB()
        cid = db.add_claim(
            statement="a disputed claim", scope_type="repository", scope_entity_id="org/repo-a",
        )
        db.add_conflict_trigger(cid)
        db.add_claim(statement="an undisputed claim", scope_type="repository", scope_entity_id="org/repo-a")

        result = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", scope=AccessScope.unrestricted(),
        ))
        assert len(result["claims"]) == 2
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["id"] == cid
        assert result["conflicts"][0]["lifecycle_state"] == "disputed"

    def test_superseded_out_claims_are_excluded(self):
        db = FakeDB()
        db.add_claim(
            statement="an old, superseded claim", scope_type="repository",
            scope_entity_id="org/repo-a", truth_state="OUT",
        )
        result = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", scope=AccessScope.unrestricted(),
        ))
        assert result["claims"] == []

    def test_focus_filters_claims_and_procedures_by_substring(self):
        db = FakeDB()
        db.add_claim(statement="pandas requires python 3.9+", scope_type="repository", scope_entity_id="org/repo-a")
        db.add_claim(statement="the linter runs on every commit", scope_type="repository", scope_entity_id="org/repo-a")
        db.add_procedure(name="upgrade pandas", goal="bump the pandas pin", scope_type="repository", scope_entity_id="org/repo-a")
        db.add_procedure(name="run linter", goal="lint the codebase", scope_type="repository", scope_entity_id="org/repo-a")

        result = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", focus="pandas", scope=AccessScope.unrestricted(),
        ))
        assert len(result["claims"]) == 1
        assert "pandas" in result["claims"][0]["statement"]
        assert len(result["relevant_procedures"]) == 1
        assert result["relevant_procedures"][0]["name"] == "upgrade pandas"

    def test_depth_bounds_the_number_of_results_returned(self):
        db = FakeDB()
        for i in range(60):
            db.add_claim(
                statement=f"claim {i}", scope_type="repository", scope_entity_id="org/repo-a",
                t_valid=datetime.now(timezone.utc) - timedelta(minutes=i),
            )

        result_depth1 = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", depth=1, scope=AccessScope.unrestricted(),
        ))
        result_depth2 = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", depth=2, scope=AccessScope.unrestricted(),
        ))
        assert len(result_depth1["claims"]) == 25
        assert len(result_depth2["claims"]) == 50

    def test_confidence_summary_counts_by_real_state(self):
        db = FakeDB()
        db.add_claim(statement="current claim", scope_type="repository", scope_entity_id="org/repo-a")
        disputed_id = db.add_claim(statement="disputed claim", scope_type="repository", scope_entity_id="org/repo-a")
        db.add_conflict_trigger(disputed_id)
        db.add_procedure(
            name="verified proc", goal="g", scope_type="repository",
            scope_entity_id="org/repo-a", verification_state="verified",
        )
        db.add_procedure(
            name="candidate proc", goal="g", scope_type="repository",
            scope_entity_id="org/repo-a", verification_state="candidate",
        )

        result = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", scope=AccessScope.unrestricted(),
        ))
        summary = result["confidence_summary"]
        assert summary["total_claims"] == 2
        assert summary["claims_by_lifecycle_state"] == {"current": 1, "disputed": 1}
        assert summary["total_procedures"] == 2
        assert summary["procedures_by_verification_state"] == {"verified": 1, "candidate": 1}

    def test_private_claim_hidden_from_anonymous_viewer(self):
        db = FakeDB()
        db.add_claim(
            statement="secret claim", scope_type="repository", scope_entity_id="org/repo-a",
            visibility="private", owner_id="alice",
        )
        db.add_claim(statement="public claim", scope_type="repository", scope_entity_id="org/repo-a")

        result = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", scope=AccessScope.anonymous(),
        ))
        assert len(result["claims"]) == 1
        assert result["claims"][0]["statement"] == "public claim"

    def test_private_claim_visible_to_its_owner(self):
        db = FakeDB()
        db.add_claim(
            statement="alice's private claim", scope_type="repository", scope_entity_id="org/repo-a",
            visibility="private", owner_id="alice",
        )

        result = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", scope=AccessScope.for_user("alice"),
        ))
        assert len(result["claims"]) == 1

        result_other = asyncio.run(get_repository_knowledge(
            db, "org/repo-a", scope=AccessScope.for_user("bob"),
        ))
        assert result_other["claims"] == []

    def test_invalid_depth_is_rejected(self):
        db = FakeDB()
        with pytest.raises(ValueError):
            asyncio.run(get_repository_knowledge(
                db, "org/repo-a", depth=0, scope=AccessScope.unrestricted(),
            ))


class TestGetProjectKnowledge:
    def test_reads_project_scoped_claims_only_no_repository_rollup(self):
        db = FakeDB()
        db.add_claim(statement="a project-level claim", scope_type="project", scope_entity_id="proj-1")
        db.add_claim(statement="a repo-level claim under the same project", scope_type="repository", scope_entity_id="org/repo-a")

        result = asyncio.run(get_project_knowledge(
            db, "proj-1", scope=AccessScope.unrestricted(),
        ))
        # Only the project-scoped claim comes back -- no aggregation
        # across repository-scoped claims, exactly as documented.
        assert len(result["claims"]) == 1
        assert result["claims"][0]["statement"] == "a project-level claim"
        assert result["project_id"] == "proj-1"

    def test_project_procedures_scoped_separately_from_repository_procedures(self):
        db = FakeDB()
        db.add_procedure(name="project-wide release process", goal="g", scope_type="project", scope_entity_id="proj-1")
        db.add_procedure(name="repo build", goal="g", scope_type="repository", scope_entity_id="org/repo-a")

        result = asyncio.run(get_project_knowledge(
            db, "proj-1", scope=AccessScope.unrestricted(),
        ))
        assert len(result["relevant_procedures"]) == 1
        assert result["relevant_procedures"][0]["name"] == "project-wide release process"


def _make_app(pool: FakeDB) -> FastAPI:
    app = FastAPI()
    app.include_router(repositories_router)
    app.include_router(projects_router)
    app.state.pool = pool
    return app


class TestRepositoriesRouter:
    def test_get_repository_returns_composed_knowledge(self):
        db = FakeDB()
        db.add_claim(statement="router-visible claim", scope_type="repository", scope_entity_id="repo-a")
        db.add_procedure(name="router proc", goal="g", scope_type="repository", scope_entity_id="repo-a")
        app = _make_app(db)
        client = TestClient(app)

        resp = client.get("/v1/repositories/repo-a")
        assert resp.status_code == 200
        body = resp.json()
        assert body["repository_id"] == "repo-a"
        assert len(body["claims"]) == 1
        assert body["claims"][0]["statement"] == "router-visible claim"
        assert len(body["relevant_procedures"]) == 1
        assert "confidence_summary" in body
        assert "conflicts" in body

    def test_get_repository_empty_for_unknown_id(self):
        db = FakeDB()
        app = _make_app(db)
        client = TestClient(app)

        resp = client.get("/v1/repositories/repo-ghost")
        assert resp.status_code == 200
        body = resp.json()
        assert body["claims"] == []
        assert body["relevant_procedures"] == []

    def test_focus_query_param_is_threaded_through(self):
        db = FakeDB()
        db.add_claim(statement="pandas needs a pin", scope_type="repository", scope_entity_id="repo-a")
        db.add_claim(statement="unrelated claim", scope_type="repository", scope_entity_id="repo-a")
        app = _make_app(db)
        client = TestClient(app)

        resp = client.get("/v1/repositories/repo-a", params={"focus": "pandas"})
        body = resp.json()
        assert len(body["claims"]) == 1


class TestProjectsRouter:
    def test_get_project_returns_composed_knowledge(self):
        db = FakeDB()
        db.add_claim(statement="project claim", scope_type="project", scope_entity_id="proj-1")
        app = _make_app(db)
        client = TestClient(app)

        resp = client.get("/v1/projects/proj-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == "proj-1"
        assert len(body["claims"]) == 1
