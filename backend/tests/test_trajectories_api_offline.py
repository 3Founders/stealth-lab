"""Offline tests for the trajectory ingestion API surface
(trajectory-ingestion-hardening task, Sec 19). Mounts `trajectories.router`
alone on a small standalone FastAPI app (not the full `app.main:app`,
which needs a real DB pool for its lifespan) with `get_pool` overridden to
a fake pool -- same `httpx.ASGITransport` in-process testing pattern
`test_cross_user_privacy_e2e.py` already established for a REST router.
No database, no network, no LLM call.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import trajectories
from app.api.admin import get_pool


class FakePool:
    def __init__(self, row=None, rows=None, val=None, vals=None):
        self._row = row
        self._rows = rows or []
        self._val = val
        self._vals = list(vals) if vals is not None else None

    async def fetchrow(self, sql, *args):
        return self._row

    async def fetch(self, sql, *args):
        return self._rows

    async def fetchval(self, sql, *args):
        if self._vals is not None:
            return self._vals.pop(0) if self._vals else None
        return self._val


def _make_app(pool: FakePool) -> FastAPI:
    app = FastAPI()
    app.include_router(trajectories.router)
    app.dependency_overrides[get_pool] = lambda: pool
    return app


ADMIN_HEADERS = {"X-Admin-Api-Key": "test-admin-key"}


@pytest.mark.asyncio
async def test_missing_admin_key_is_rejected(monkeypatch):
    from app import config as config_module
    monkeypatch.setattr(config_module.settings, "admin_api_key", "test-admin-key")

    app = _make_app(FakePool())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/v1/trajectories/some-trace-id")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unsupported_source_type_is_rejected(monkeypatch):
    from app import config as config_module
    monkeypatch.setattr(config_module.settings, "admin_api_key", "test-admin-key")

    app = _make_app(FakePool())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post(
            "/v1/trajectories/ingest",
            json={"source_type": "some_future_source", "root": "/tmp/x"},
            headers=ADMIN_HEADERS,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_inspect_missing_trajectory_returns_404(monkeypatch):
    from app import config as config_module
    monkeypatch.setattr(config_module.settings, "admin_api_key", "test-admin-key")

    app = _make_app(FakePool(row=None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/v1/trajectories/nonexistent-trace", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_inspect_found_trajectory_returns_its_fields(monkeypatch):
    from app import config as config_module
    monkeypatch.setattr(config_module.settings, "admin_api_key", "test-admin-key")

    row = {
        "trace_id": "t1", "session_id": "s1", "provider": "openhands",
        "provider_version": "openhands_eval_wrapper_v1", "model": "claude-sonnet-4-6",
        "token_usage": {"prompt_tokens": 1}, "cost_usd": 0.1, "outcome": "success",
        "started_at": None, "ended_at": None, "metadata": {},
    }
    app = _make_app(FakePool(row=row))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/v1/trajectories/t1", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["provider"] == "openhands"


@pytest.mark.asyncio
async def test_provenance_unknown_object_type_is_rejected(monkeypatch):
    from app import config as config_module
    monkeypatch.setattr(config_module.settings, "admin_api_key", "test-admin-key")

    app = _make_app(FakePool())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get(
            "/v1/trajectories/provenance/not_a_real_type/00000000-0000-0000-0000-000000000000",
            headers=ADMIN_HEADERS,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_provenance_missing_lineage_returns_404(monkeypatch):
    from app import config as config_module
    monkeypatch.setattr(config_module.settings, "admin_api_key", "test-admin-key")

    app = _make_app(FakePool(rows=[]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get(
            "/v1/trajectories/provenance/claim/00000000-0000-0000-0000-000000000000",
            headers=ADMIN_HEADERS,
        )
    assert resp.status_code == 404


class _StatsFakePool:
    """Routes .fetch()/.fetchval() by a SQL substring, since the stats
    endpoint issues several distinctly-shaped queries in one call."""

    def __init__(self):
        self._vals = iter([5, 42, 8.4, 1, 3, 0, 0.02])

    async def fetchval(self, sql, *args):
        return next(self._vals)

    async def fetch(self, sql, *args):
        if "trajectory_extractions" in sql:
            return [{"status": "completed", "n": 3}]
        if "trajectory_extraction_objects" in sql:
            return [{"object_type": "goal", "n": 2}, {"object_type": "claim", "n": 1}]
        raise AssertionError(f"unexpected fetch: {sql}")


@pytest.mark.asyncio
async def test_stats_endpoint_returns_real_aggregate_shape(monkeypatch):
    from app import config as config_module
    monkeypatch.setattr(config_module.settings, "admin_api_key", "test-admin-key")

    app = _make_app(_StatsFakePool())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/v1/trajectories/stats", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["trajectories_ingested"] == 5
    assert body["events_normalized"] == 42
    assert body["semantic_extractions_succeeded"] == 3
    assert body["goals_extracted"] == 2
    assert body["claims_extracted"] == 1
    assert 0.0 <= body["escalation_rate"] <= 1.0
