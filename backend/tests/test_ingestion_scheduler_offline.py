"""
Offline tests for app/services/ingestion_scheduler.py -- the in-process
background loop that turns normal agent work into procedure candidates.

mode="global" is the shared-substrate path (process_ingestion), tested
here with a monkeypatched process_ingestion. P5 removed the old
mode="local" (trace files -> private SQLite LocalProcedureStore); a
non-"global" mode is now a recorded no-op tick.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services import ingestion_scheduler
from app.services.ingestion_scheduler import IngestionSchedulerState, _loop, start, stop


def _fake_app() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(pool=object()))


def _state(**over):
    base = dict(
        enabled=True, mode="local", interval_seconds=0,
        promote_limit=5, extract_limit=5, job_limit=500,
        workspace=None, trace_dir=None, max_sessions=5,
    )
    base.update(over)
    return IngestionSchedulerState(**base)


def _set_settings(monkeypatch, **overrides):
    from app.config import settings

    defaults = dict(
        ingestion_auto_enabled=True,
        ingestion_auto_mode="local",
        ingestion_auto_interval_seconds=60,
        ingestion_auto_workspace=None,
        ingestion_auto_trace_dir=None,
        ingestion_auto_max_sessions=5,
        ingestion_auto_promote_limit=5,
        ingestion_auto_extract_limit=5,
        ingestion_auto_job_limit=500,
    )
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(settings, name, value, raising=False)


def _trace_dir(tmp_path):
    d = tmp_path / ".claude" / "traces"
    d.mkdir(parents=True)
    (d / "sess-1.jsonl").write_text("\n".join([
        json.dumps({"type": "user", "message": {"content": "run the migration and tests"}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "python scripts/migrate.py"}}]}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}}),
    ]), encoding="utf-8")
    return str(tmp_path)


def test_start_disabled_returns_none_but_still_publishes_state(monkeypatch):
    _set_settings(monkeypatch, ingestion_auto_enabled=False)
    app = _fake_app()
    task = start(app)
    assert task is None
    assert isinstance(app.state.ingestion_scheduler, IngestionSchedulerState)
    assert app.state.ingestion_scheduler.mode == "local"


def test_start_enabled_creates_a_task_and_stop_cancels_it(monkeypatch):
    _set_settings(monkeypatch, ingestion_auto_enabled=True, ingestion_auto_interval_seconds=60)
    app = _fake_app()

    async def scenario():
        task = start(app)
        assert task is not None and not task.done()
        await stop(app)
        assert task.done()

    asyncio.run(scenario())


def test_stop_is_a_noop_when_no_task(monkeypatch):
    _set_settings(monkeypatch, ingestion_auto_enabled=False)
    app = _fake_app()
    start(app)
    asyncio.run(stop(app))  # must not raise


def test_state_as_dict_matches_the_admin_status_response_shape():
    from app.api.admin import IngestionAutoStatusResponse

    d = _state().as_dict()
    assert set(d) == set(IngestionAutoStatusResponse.model_fields)
    IngestionAutoStatusResponse(**d)


def test_non_global_mode_tick_is_a_recorded_noop(tmp_path, monkeypatch):
    """P5: the old mode="local" trace -> private SQLite sweep was removed
    with the local store. A non-"global" ingestion_auto_mode now produces
    a no-op tick that records WHY (not a silent skip, not a crash), and
    process_ingestion is never called."""
    workspace = _trace_dir(tmp_path)
    monkeypatch.setattr(
        "app.api.admin.process_ingestion",
        lambda **kw: (_ for _ in ()).throw(AssertionError("global path must not be hit")),
    )
    state = _state(mode="local", workspace=workspace, interval_seconds=0)
    app = _fake_app()

    async def scenario():
        task = asyncio.create_task(_loop(app, state))
        for _ in range(200):
            if state.run_count >= 1 and state.last_result is not None:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert state.last_error is None
    assert "skipped" in state.last_result
    assert "local-store sweep removed" in state.last_result["skipped"]


def test_global_mode_tick_calls_process_ingestion(tmp_path, monkeypatch):
    async def fake_process_ingestion(*, promote_limit, extract_limit, job_limit, pool):
        return SimpleNamespace(model_dump=lambda: {"promoted": 2})

    monkeypatch.setattr("app.api.admin.process_ingestion", fake_process_ingestion)
    state = _state(mode="global", interval_seconds=0)
    app = _fake_app()

    async def scenario():
        task = asyncio.create_task(_loop(app, state))
        for _ in range(200):
            if state.last_result is not None:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert state.last_result == {"promoted": 2}


def test_loop_iteration_failure_is_recorded_and_does_not_kill_the_loop(monkeypatch):
    box = {"n": 0}

    async def flaky(*, promote_limit, extract_limit, job_limit, pool):
        box["n"] += 1
        if box["n"] == 1:
            raise RuntimeError("transient")
        return SimpleNamespace(model_dump=lambda: {"ok": True})

    monkeypatch.setattr("app.api.admin.process_ingestion", flaky)
    state = _state(mode="global", interval_seconds=0)
    app = _fake_app()

    async def scenario():
        task = asyncio.create_task(_loop(app, state))
        for _ in range(400):
            if state.run_count >= 2:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert state.run_count >= 2
    assert state.last_error_at is not None
    assert state.last_result == {"ok": True}
    assert state.last_error is None
