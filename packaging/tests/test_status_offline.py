"""
Offline tests for the P2 minimal status surface (packaging/status_server).

No database anywhere: the app is built with a FakePool injected as the
pool factory, and every SQL statement it issues is captured for content
proofs (SELECT-only teeth, builder-produced scope fragments,
parameterized LIMITs). Capability-score expectations live in
test_status_capability_offline.py.
"""

import asyncio
import datetime as dt
import uuid
from decimal import Decimal

import asyncpg
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import stealthlab_connect as slc  # noqa: E402

# Fronts the real backend checkout on sys.path BEFORE any app.* import,
# exactly like the package's other entry points do at runtime.
slc.get_backend_root()

from fastapi.testclient import TestClient  # noqa: E402

from stealthlab_connect import status_server  # noqa: E402

COMMONS_TENANT = "00000000-0000-0000-0000-000000000001"

PROC_ID = uuid.uuid4()
PROC_STABLE_ID = uuid.uuid4()
CLAIM_ID = uuid.uuid4()
EPISODE_ID = uuid.uuid4()
KNODE_ID = uuid.uuid4()
OBS_ID = uuid.uuid4()

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


def ev_row(**over):
    """One procedure-outcome evidence row shaped like the real SELECT."""
    row = {
        "id": uuid.uuid4(),
        "target_id": PROC_ID,
        "target_version": 3,
        "evidence_type": "execution_result",
        "direction": "supports",
        "outcome_status": "success",
        "strength_score": 0.9,
        "strength_method": "recorded_outcome",
        "independence_group": None,
        "context_key": None,
        "failure_class": None,
        "t_valid": NOW,
    }
    row.update(over)
    return row


FAKE_ROWS = {
    # marker -> rows returned for any query containing that marker
    "FROM procedures p": [
        {
            "id": PROC_ID,
            "procedure_id": PROC_STABLE_ID,
            "version": 3,
            "name": "Deploy the service",
            "goal": "ship it",
            "verification_state": "candidate",
            "staleness": "fresh",
            "availability": "active",
            "t_valid": NOW,
            "created_at": NOW,
        }
    ],
    # procedure outcome stream: 2 attempts, 1 success, 2 independence groups;
    # the contradicts/document rows must be dropped by the converter.
    "evidence_type IN ('execution_result', 'reproduction')": [
        ev_row(context_key="env-a", independence_group="g1"),
        ev_row(outcome_status="failure", context_key="env-b", independence_group="g2"),
        ev_row(direction="contradicts"),
        ev_row(evidence_type="document"),
    ],
    "FROM knowledge_nodes k": [
        {
            "id": CLAIM_ID,
            "name": "claim row",
            "statement": "tests pass after deploy",
            "truth_state": "IN",
            "epistemic_status": "observed",
            "extraction_version": "claim_promotion@1",
            "subject": "deploy",
            "predicate": "passes-tests",
            "object": "service",
            "t_valid": NOW,
        }
    ],
    "FROM claim_sources cs": [
        {
            "claim_id": CLAIM_ID,
            "observation_id": OBS_ID,
            "observation_type": "test_run",
            "label": "pytest -q",
            "extractor_kind": "deterministic",
            "extractor_name": "deterministic_v1",
            "code_version": "abc123",
            "model_id": None,
        }
    ],
    "GROUP BY e.target_id": [{"target_id": CLAIM_ID, "n": 2}],
    "FROM episodes ep": [
        {
            "id": EPISODE_ID,
            "episode_type": "trace",
            "session_id": "sess-42",
            "project_id": "proj-7",
            "parent_episode_id": None,
            "start_ts": NOW,
            "end_ts": NOW,
            "timestamp": NOW,
            "metadata": {
                "segmenter": "trace_worker/episode_assembly.v1",
                "flags": [],
                "n_events": 42,
            },
        }
    ],
    "FROM episode_links el": [
        {"episode_id": EPISODE_ID, "target_id": KNODE_ID, "target_table": "knowledge_nodes"}
    ],
    "FROM knowledge_nodes n": [{"id": KNODE_ID, "name": "the linked node"}],
    "FROM task_nodes n": [],
    "e.target_type = $1": [
        {
            "id": uuid.uuid4(),
            "evidence_type": "execution_result",
            "target_type": "claim",
            "target_id": CLAIM_ID,
            "target_version": None,
            "direction": "supports",
            "outcome_status": "success",
            "success_criteria": {"tests_green": True},
            "strength_score": 1.0,
            "strength_method": "recorded_outcome",
            "independence_group": None,
            "context_key": "ci",
            "failure_class": None,
            "extractor_version": "outcome_recorder@1",
            "created_by": "system",
            "scope_type": None,
            "scope_entity_id": None,
            "t_valid": NOW,
            "t_invalid": None,
        }
    ],
}


class FakePool:
    def __init__(self, rows=None):
        self.rows = FAKE_ROWS if rows is None else rows
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        for marker, rows in self.rows.items():
            if marker in sql:
                return [dict(r) for r in rows]
        return []

    async def fetchrow(self, sql, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def close(self):
        pass


@pytest.fixture()
def pool(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://offline:offline@127.0.0.1:1/offline")
    return FakePool()


@pytest.fixture()
def client(pool):
    from stealthlab_connect.status_server import create_status_app

    app = create_status_app(pool_factory=lambda: asyncio.sleep(0, result=pool))
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


def test_root_serves_the_single_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert "StealthLab status" in body
    assert "/api/overview" in body
    assert "renderProcedure" in body


def test_health_needs_no_database(client, pool):
    pool.rows = {}  # even a totally silent pool answers health
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_meta_reports_posture_and_commons_tenant(client):
    data = client.get("/api/meta").json()
    assert data["viewer_id"] is None  # anonymous public posture
    assert data["tenant_id"] == COMMONS_TENANT
    assert data["api_base"] == status_server.DEFAULT_API_BASE
    assert data["posture"]["oidc_configured"] is False


def test_meta_api_base_is_env_overridable(client, monkeypatch):
    monkeypatch.setenv(status_server.API_BASE_ENV_VAR, "https://api.example.com")
    assert client.get("/api/meta").json()["api_base"] == "https://api.example.com"


def test_overview_maps_rows_to_json_shapes(client):
    data = client.get("/api/overview").json()
    assert data["counts"] == {"episodes": 1, "claims": 1, "procedures": 1}
    assert data["episodes"][0]["id"] == str(EPISODE_ID)
    assert data["episodes"][0]["metadata"]["n_events"] == 42
    assert data["episodes"][0]["links"] == [
        {"target_id": str(KNODE_ID), "target_table": "knowledge_nodes", "name": "the linked node"}
    ]
    claim = data["claims"][0]
    assert claim["id"] == str(CLAIM_ID)
    assert claim["truth_state"] == "IN"
    assert claim["evidence_count"] == 2
    assert claim["sources"] == [
        {
            "observation_id": str(OBS_ID),
            "observation_type": "test_run",
            "label": "pytest -q",
            "extractor_kind": "deterministic",
            "extractor_name": "deterministic_v1",
            "code_version": "abc123",
            "model_id": None,
        }
    ]
    proc = data["procedures"][0]
    assert proc["id"] == str(PROC_ID)
    assert proc["verification_state"] == "candidate"
    # canned stream = 2 attempts / 1 success / 2 groups -> level 1, refuse
    cap = proc["capability"]
    assert cap["evidence_count"] == 2
    assert cap["success_count"] == 1
    assert cap["independent_groups"] == 2
    assert cap["level"] == 1
    assert cap["routing"] == "refuse_reuse"
    assert cap["gates_reported"] == {
        "verification_plan_satisfied": False,
        "completed_review": False,
    }


def test_overview_limit_param_bounds(client):
    assert client.get("/api/overview", params={"limit": 0}).status_code == 422
    assert client.get("/api/overview", params={"limit": 501}).status_code == 422
    assert client.get("/api/overview", params={"limit": 5}).status_code == 200


# ---------------------------------------------------------------------------
# Evidence trail endpoint
# ---------------------------------------------------------------------------


def test_evidence_trail_returns_rows_for_claim(client):
    res = client.get(f"/api/evidence/claim/{CLAIM_ID}")
    assert res.status_code == 200
    data = res.json()
    assert data["target_type"] == "claim"
    assert data["target_id"] == str(CLAIM_ID)
    assert len(data["evidence"]) == 1
    row = data["evidence"][0]
    assert row["success_criteria"] == {"tests_green": True}
    assert row["t_valid"] == NOW.isoformat()


def test_evidence_trail_rejects_unknown_target_types(client):
    res = client.get(f"/api/evidence/bogus/{CLAIM_ID}")
    assert res.status_code == 400
    assert "claim" in res.json()["detail"]


def test_evidence_trail_rejects_malformed_uuid(client):
    assert client.get("/api/evidence/claim/not-a-uuid").status_code == 422


# ---------------------------------------------------------------------------
# Read-only SQL teeth (FakePool content proofs)
# ---------------------------------------------------------------------------


def _all_sql(pool):
    return [sql for sql, _ in pool.calls]


def test_statements_are_select_only_and_scoped(client, pool):
    client.get("/api/overview")
    client.get(f"/api/evidence/procedure/{PROC_ID}")
    assert pool.calls, "the endpoints should have issued queries"
    list_queries = 0
    for sql, args in pool.calls:
        assert sql.lstrip().startswith(("SELECT", "WITH")), f"non-read statement: {sql[:120]}"
        assert "INSERT" not in sql and "UPDATE" not in sql and "DELETE" not in sql
        # Scope fragments come from the access builders, never hand-written.
        # The split follows each table's REAL schema: knowledge_nodes /
        # task_nodes / episodes are tenant-bearing (scope_predicates ->
        # both fragments); procedures / evidence carry visibility only --
        # a tenant fragment there would name a nonexistent column.
        if "FROM knowledge_nodes" in sql or "FROM episodes ep" in sql or "FROM task_nodes n" in sql:
            assert "visibility" in sql, sql[:200]
            assert "tenant_id" in sql, sql[:200]
        if "FROM procedures p" in sql or "FROM evidence e" in sql:
            assert "visibility" in sql, sql[:200]
            assert "tenant_id" not in sql, f"procedures/evidence have no tenant_id: {sql[:200]}"
        if "LIMIT $" in sql:
            list_queries += 1
            assert "$" in sql and args, "LIMIT must be parameterized"
    assert list_queries >= 3  # procedures, claims, episodes lists


def test_viewer_header_threads_owner_parameter(client, pool):
    client.get("/api/overview", headers={"X-Viewer-Id": "viewer-42"})
    owner_bound = any(any(a == "viewer-42" for a in args) for _, args in pool.calls)
    assert owner_bound, "X-Viewer-Id should reach the visibility predicate params"


def test_limit_is_clamped_into_query_not_response_only(client, pool):
    client.get("/api/overview", params={"limit": 7})
    limited = [args for sql, args in pool.calls if "LIMIT $" in sql]
    assert limited and all(7 in args for args in limited)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_jsonable_converts_backend_types():
    value = {
        "id": CLAIM_ID,
        "when": NOW,
        "score": Decimal("0.75"),
        "nested": {"ids": [KNODE_ID], "flag": True},
    }
    out = status_server.jsonable(value)
    assert out == {
        "id": str(CLAIM_ID),
        "when": NOW.isoformat(),
        "score": 0.75,
        "nested": {"ids": [str(KNODE_ID)], "flag": True},
    }


def test_outcome_converter_filters_population_like_the_stats_view():
    rows = [
        ev_row(outcome_status="failure"),                       # kept
        ev_row(direction="contradicts"),                        # dropped: direction
        ev_row(evidence_type="human_review"),                   # dropped: kind
        ev_row(),                                               # kept
    ]
    records = status_server.outcome_records_from_evidence(rows)
    assert [r.success for r in records] == [False, True]


def test_outcome_converter_falls_back_when_context_key_missing():
    records = status_server.outcome_records_from_evidence([ev_row(context_key=None)])
    assert records[0].environment == status_server.UNRECORDED_ENVIRONMENT


def test_scope_resolution_reuses_deps_get_scope_unchanged():
    from app.api.deps import get_scope as deps_get_scope

    assert status_server.get_scope is deps_get_scope


# ---------------------------------------------------------------------------
# Console entry point
# ---------------------------------------------------------------------------


def test_entry_preflight_names_missing_database_url(monkeypatch):
    from stealthlab_connect.status_entry import preflight
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", None, raising=False)
    problems = preflight()
    assert problems and "DATABASE_URL" in problems[0]

    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@h/db", raising=False)
    assert preflight() == []


def test_entry_main_fails_loud_on_bad_backend_root(tmp_path):
    from stealthlab_connect.status_entry import main

    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["--backend-root", str(empty)]) == 1


# ---------------------------------------------------------------------------
# Pre-migration database drift: degrade honestly, never fake data
# ---------------------------------------------------------------------------


class MissingEvidencePool(FakePool):
    """The documented shared-instance drift: no evidence/claim_sources-era
    tables (migrations 24+ absent). Only UndefinedTableError degrades."""

    async def fetch(self, sql, *args):
        if "FROM evidence e" in sql:
            raise asyncpg.UndefinedTableError("relation \"evidence\" does not exist")
        if "FROM claim_sources cs" in sql:
            raise asyncpg.UndefinedTableError("relation \"claim_sources\" does not exist")
        return await super().fetch(sql, *args)


@pytest.fixture()
def drifted_client(monkeypatch):
    from stealthlab_connect.status_server import create_status_app

    pool = MissingEvidencePool()
    monkeypatch.setenv("DATABASE_URL", "postgresql://offline:offline@127.0.0.1:1/offline")
    app = create_status_app(pool_factory=lambda: asyncio.sleep(0, result=pool))
    with TestClient(app) as c:
        yield c, pool


def test_overview_degrades_on_missing_evidence_table(drifted_client):
    client, pool = drifted_client
    res = client.get("/api/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["outcome_evidence_available"] is False
    proc = data["procedures"][0]
    assert proc["capability"]["level"] == 0
    assert proc["capability"]["level_label"] == "unknown"
    assert proc["capability"]["routing"] == "refuse_reuse"
    assert data["claims"][0]["evidence_count"] == 0
    assert data["claims"][0]["sources"] == []


def test_trail_degrades_on_missing_evidence_table(drifted_client):
    client, _ = drifted_client
    res = client.get(f"/api/evidence/procedure/{PROC_ID}")
    assert res.status_code == 200
    data = res.json()
    assert data["evidence"] == []
    assert "pre-migration-24" in data["note"]
