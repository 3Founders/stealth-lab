"""Control project B (search/log database) selection -- offline.

Without SEARCH_DATABASE_URL every search/log table is used on the pool the caller
already has (single-database deployments unchanged, no extra pool). With it, one
process-wide pool per event loop for project B serves exactly those tables; Goal
projections, the outbox and canonical rows stay on the control database."""
from __future__ import annotations

import asyncio

import pytest

from app.services import governance
from app.services import identity_resolution as ir
from app.services import search_projection as sp
from app.services import shards

B_DSN = "postgresql://search-db.invalid/b"


class RecordingPool:
    def __init__(self, name: str, rows: dict | None = None):
        self.name = name
        self.rows = rows or {}
        self.statements: list[str] = []

    async def execute(self, sql, *args):
        self.statements.append(" ".join(sql.split()))
        return "INSERT 0 1"

    async def fetch(self, sql, *args):
        self.statements.append(" ".join(sql.split()))
        return []          # lookup_routes: no route -> home shard

    async def fetchrow(self, sql, *args):
        compact = " ".join(sql.split())
        self.statements.append(compact)
        for table, row in self.rows.items():
            if f"FROM {table}" in compact:
                return row
        return None

    async def fetchval(self, sql, *args):
        self.statements.append(" ".join(sql.split()))
        return 0


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    shards._SEARCH_POOLS.clear()
    shards._SEARCH_POOL_LOCKS.clear()
    monkeypatch.delenv("SEARCH_DATABASE_URL", raising=False)
    monkeypatch.setattr("app.config.settings.search_database_url", None, raising=False)
    yield
    shards._SEARCH_POOLS.clear()
    shards._SEARCH_POOL_LOCKS.clear()


@pytest.fixture
def search_db(monkeypatch):
    b = RecordingPool("B")
    created = []

    async def fake_create_pool(dsn, **kwargs):
        created.append(dsn)
        return b

    monkeypatch.setenv("SEARCH_DATABASE_URL", B_DSN)
    monkeypatch.setattr("app.db.session.create_pool", fake_create_pool)
    return b, created


def test_without_search_database_the_callers_own_pool_is_used():
    a = RecordingPool("A")
    assert shards.search_database_url() is None
    assert asyncio.run(shards.search_pool(a)) is a


def test_with_search_database_one_pool_serves_every_caller(search_db):
    b, created = search_db

    async def run():
        first = await shards.search_pool(RecordingPool("A1"))
        second = await shards.search_pool(RecordingPool("A2"))
        return first, second

    first, second = asyncio.run(run())
    assert first is b and second is b
    assert created == [B_DSN]          # created once, lazily


def _procedure_row():
    return {
        "id": "00000000-0000-4000-8000-0000000000a1", "procedure_id": "00000000-0000-4000-8000-0000000000a2",
        "name": "export docx", "goal": "export a document", "achieves_goal_id": "00000000-0000-4000-8000-0000000000a3",
        "display_description": None, "capability_statement": None, "retrieval_document": None,
        "preconditions": [], "postconditions": [], "verification_state": "unverified",
        "verification_stats": {}, "availability": "active", "version": 1, "visibility": "public",
        "owner_id": None, "scope_type": "global", "scope_entity_id": None, "tenant_id": None,
        "home_shard_id": "K000", "embedding": None, "embedding_model_id": None, "embedding_provider": None,
    }


def _goal_row():
    return {
        "id": "00000000-0000-4000-8000-0000000000a3", "canonical_name": "export a document", "description": None,
        "aliases": [], "status": "active", "version": 1, "visibility": "public", "owner_id": None,
        "scope_type": "global", "scope_entity_id": None, "home_shard_id": "K000", "embedding": None,
        "embedding_model_id": None, "embedding_provider": None, "resolved_at": None, "t_created": None,
    }


def test_procedure_projection_is_written_to_the_search_database_and_goal_projection_is_not(search_db):
    b, _ = search_db
    a = RecordingPool("A", rows={"procedures": _procedure_row(), "goals": _goal_row()})

    asyncio.run(sp.project_object(a, "procedure", _procedure_row()["procedure_id"]))
    asyncio.run(sp.project_object(a, "goal", _goal_row()["id"]))

    assert any("INSERT INTO procedure_search_index" in sql for sql in b.statements)
    assert not any("procedure_search_index" in sql for sql in a.statements)
    assert any("INSERT INTO goal_search_index" in sql for sql in a.statements)      # Goal index stays with the hierarchy
    assert not any("goal_search_index" in sql for sql in b.statements)
    assert any("pg_advisory_xact_lock" in sql for sql in a.statements)              # serialised on the control database


def test_without_search_database_procedure_projection_stays_on_the_callers_connection():
    a = RecordingPool("A", rows={"procedures": _procedure_row()})
    asyncio.run(sp.project_object(a, "procedure", _procedure_row()["procedure_id"]))
    assert any("INSERT INTO procedure_search_index" in sql for sql in a.statements)


def test_identity_decisions_and_llm_spend_use_the_search_database(search_db):
    b, _ = search_db
    a = RecordingPool("A")
    asyncio.run(ir._load_prior_decision(a, "goal", "some-key"))
    asyncio.run(governance.CostGovernor(a).record("gemini", "m", "judge:x", 10, 5))
    assert any("FROM identity_decisions" in sql for sql in b.statements)
    assert any("INSERT INTO llm_spend" in sql for sql in b.statements)
    assert a.statements == []
