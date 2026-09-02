"""
Offline tests for app/services/ingestion_scheduler.py -- the in-process
background loop that calls app.api.admin.process_ingestion on a timer so
normal agent work enters the learning pipeline without a human curling
the endpoint.

No DB: process_ingestion is monkeypatched. The loop's real contract is
(1) start() honors INGESTION_AUTO_ENABLED and always publishes a
readable state object, (2) one iteration's failure is recorded but never
kills the loop, (3) stop() cancels cleanly.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import ingestion_scheduler
from app.services.ingestion_scheduler import IngestionSchedulerState, _loop, start, stop


def _fake_app() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(pool=object()))


def _set_settings(monkeypatch, **overrides):
    from app.config import settings

    defaults = dict(
        ingestion_auto_enabled=True,
        ingestion_auto_interval_seconds=60,
        ingestion_auto_promote_limit=5,
        ingestion_auto_extract_limit=5,
        ingestion_auto_job_limit=500,
    )
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(settings, name, value, raising=False)


def test_start_disabled_returns_none_but_still_publishes_state(monkeypatch):
    _set_settings(monkeypatch, ingestion_auto_enabled=False)
    app = _fake_app()

    task = start(app)

    assert task is None
    assert app.state.ingestion_scheduler_task is None
    state = app.state.ingestion_scheduler
    assert isinstance(state, IngestionSchedulerState)
    assert state.enabled is False
    assert state.run_count == 0


def test_start_enabled_creates_a_task_and_stop_cancels_it(monkeypatch):
    _set_settings(monkeypatch, ingestion_auto_enabled=True, ingestion_auto_interval_seconds=60)
    app = _fake_app()

    async def scenario():
        task = start(app)
        assert task is not None
        assert app.state.ingestion_scheduler_task is task
        assert not task.done()
        await stop(app)
        assert task.done()
        assert task.cancelled() or task.exception() is None

    asyncio.run(scenario())


def test_stop_is_a_noop_when_no_task(monkeypatch):
    _set_settings(monkeypatch, ingestion_auto_enabled=False)
    app = _fake_app()
    start(app)

    asyncio.run(stop(app))  # must not raise


def test_state_as_dict_matches_the_admin_status_response_shape(monkeypatch):
    from app.api.admin import IngestionAutoStatusResponse

    state = IngestionSchedulerState(
        enabled=True, interval_seconds=60, promote_limit=5,
        extract_limit=5, job_limit=500,
    )
    d = state.as_dict()
    assert set(d) == set(IngestionAutoStatusResponse.model_fields)
    # constructs without error -> shapes agree
    IngestionAutoStatusResponse(**d)


def test_loop_records_a_successful_iteration(monkeypatch):
    calls = []

    async def fake_process_ingestion(*, promote_limit, extract_limit, job_limit, pool):
        calls.append((promote_limit, extract_limit, job_limit))
        return SimpleNamespace(model_dump=lambda: {"promoted": 1, "extracted": 0})

    monkeypatch.setattr("app.api.admin.process_ingestion", fake_process_ingestion)
    state = IngestionSchedulerState(
        enabled=True, interval_seconds=0, promote_limit=3,
        extract_limit=2, job_limit=99,
    )
    app = _fake_app()

    async def scenario():
        task = asyncio.create_task(_loop(app, state))
        for _ in range(50):
            if state.run_count >= 1 and state.last_result is not None:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert state.run_count >= 1
    assert calls and calls[0] == (3, 2, 99)
    assert state.last_result == {"promoted": 1, "extracted": 0}
    assert state.last_error is None
    assert state.last_run_completed_at is not None


def test_loop_iteration_failure_is_recorded_and_does_not_kill_the_loop(monkeypatch):
    state_box = {"n": 0}

    async def flaky_process_ingestion(*, promote_limit, extract_limit, job_limit, pool):
        state_box["n"] += 1
        if state_box["n"] == 1:
            raise RuntimeError("transient DB hiccup")
        return SimpleNamespace(model_dump=lambda: {"ok": True})

    monkeypatch.setattr("app.api.admin.process_ingestion", flaky_process_ingestion)
    state = IngestionSchedulerState(
        enabled=True, interval_seconds=0, promote_limit=1,
        extract_limit=1, job_limit=1,
    )
    app = _fake_app()

    async def scenario():
        task = asyncio.create_task(_loop(app, state))
        for _ in range(200):
            if state.run_count >= 2:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    # first iteration failed -> recorded; loop kept going -> second succeeded
    assert state.run_count >= 2
    assert state.last_error_at is not None
    assert state.last_result == {"ok": True}
    assert state.last_error is None  # cleared by the successful iteration
