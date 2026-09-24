from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import app.api.goals as goals_api
from app.api.deps import AuthenticatedPrincipal
from app.ingestion.config import WorkerConfig
from app.ingestion.worker import Worker
from app.services import auth_context as _ac
from app.services import benchmark_transfer as transfer_service
from app.services.access import AccessScope


def _run(coro):
    return asyncio.run(coro)


def test_worker_default_handlers_route_benchmark_transfer_to_service_handler():
    from app.services.ingestion_jobs import JOB_HANDLERS

    worker = Worker(object(), WorkerConfig(lease_seconds=10, job_timeout_seconds=10))

    assert worker.handlers is JOB_HANDLERS
    assert JOB_HANDLERS[transfer_service.JOB_TYPE] is transfer_service.handle_benchmark_transfer


def test_process_pending_jobs_registers_transfer_handler_for_cli_and_admin_paths(monkeypatch):
    from app.services import ingestion_jobs

    async def process(*args, **kwargs):
        return {"claimed": 0, "done": 0, "failed": 0, "unknown_type": 0, "worker_id": None}

    monkeypatch.setattr(ingestion_jobs, "_process_pending_jobs", process)
    ingestion_jobs.JOB_HANDLERS.pop(transfer_service.JOB_TYPE, None)

    result = _run(ingestion_jobs.process_pending_jobs(object()))

    assert result["claimed"] == 0
    assert ingestion_jobs.JOB_HANDLERS[transfer_service.JOB_TYPE] is transfer_service.handle_benchmark_transfer


    captured = {}

    async def get_source(pool, source_benchmark_id, *, access_scope):
        captured["source_pool"] = pool
        captured["source_id"] = source_benchmark_id
        captured["source_scope"] = access_scope
        return {"benchmark_id": "canonical-source", "status": "frozen"}

    async def enqueue(pool, source_benchmark_id, *, access_scope):
        captured["enqueue_pool"] = pool
        captured["enqueue_id"] = source_benchmark_id
        captured["enqueue_scope"] = access_scope
        return [{"target_goal_id": "derived-target", "created": True}]

    monkeypatch.setattr(goals_api._benchmark_transfer, "get_benchmark_transfer_source", get_source)
    monkeypatch.setattr(goals_api._benchmark_transfer, "enqueue_benchmark_transfers", enqueue)
    principal = AuthenticatedPrincipal(
        user_id="reviewer-user",
        subject="reviewer-subject",
        email="reviewer@example.test",
        scopes=frozenset({_ac.KNOWLEDGE_PUBLISH}),
    )
    pool = object()

    result = _run(goals_api.enqueue_benchmark_transfers_route(
        benchmark_id="requested-source",
        pool=pool,
        principal=principal,
        scope_key="reviewer:reviewer-subject",
    ))

    assert result == {
        "source_benchmark_id": "canonical-source",
        "transfers": [{"target_goal_id": "derived-target", "created": True}],
    }
    assert captured == {
        "source_pool": pool,
        "source_id": "requested-source",
        "source_scope": AccessScope.for_user("reviewer-subject"),
        "enqueue_pool": pool,
        "enqueue_id": "canonical-source",
        "enqueue_scope": AccessScope.for_user("reviewer-subject"),
    }
    parameters = inspect.signature(goals_api.enqueue_benchmark_transfers_route).parameters
    assert not {"body", "scope", "target_goal_id", "targets", "verdict", "verdicts"}.intersection(parameters)


def test_reviewer_transfer_route_rejects_non_frozen_source(monkeypatch):
    async def get_source(pool, source_benchmark_id, *, access_scope):
        return {"benchmark_id": source_benchmark_id, "status": "draft"}

    enqueue_called = False

    async def enqueue(pool, source_benchmark_id, *, access_scope):
        nonlocal enqueue_called
        enqueue_called = True
        return []

    monkeypatch.setattr(goals_api._benchmark_transfer, "get_benchmark_transfer_source", get_source)
    monkeypatch.setattr(goals_api._benchmark_transfer, "enqueue_benchmark_transfers", enqueue)
    principal = AuthenticatedPrincipal(
        user_id="reviewer-user",
        subject="reviewer-subject",
        email="reviewer@example.test",
        scopes=frozenset({_ac.KNOWLEDGE_PUBLISH}),
    )

    with pytest.raises(HTTPException) as excinfo:
        _run(goals_api.enqueue_benchmark_transfers_route(
            benchmark_id="draft-source",
            pool=object(),
            principal=principal,
            scope_key="reviewer:reviewer-subject",
        ))

    assert excinfo.value.status_code == 409
    assert enqueue_called is False
