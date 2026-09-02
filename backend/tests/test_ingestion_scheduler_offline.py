"""
Offline tests for app/services/ingestion_scheduler.py -- the in-process
background loop that turns normal agent work into procedure candidates.

V1 default is mode="local" (P0-1): local trace files -> PRIVATE
LocalProcedureStore candidates, DB-free. mode="global" is the
shared-substrate path (process_ingestion). Both are tested here with the
real sweep functions and a monkeypatched process_ingestion respectively.
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


def test_local_mode_tick_writes_private_candidates_from_real_traces(tmp_path, monkeypatch):
    """P0-1: the DEFAULT loop reads local traces and writes into the
    workspace LocalProcedureStore -- no DB, no global write."""
    workspace = _trace_dir(tmp_path)
    # process_ingestion must NOT be called in local mode.
    called = []
    monkeypatch.setattr(
        "app.api.admin.process_ingestion",
        lambda **kw: called.append(kw) or (_ for _ in ()).throw(AssertionError("global path hit")),
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

    assert called == []
    assert state.last_error is None
    res = state.last_result
    assert res["sessions_seen"] == 1
    assert res["captured"] == 1

    from app.local_agent.local_store import LocalProcedureStore
    store = LocalProcedureStore(workspace)
    rows = store.list_local_procedures()
    assert len(rows) == 1
    row = store.get_local_procedure(rows[0]["id"])
    assert row["verification_state"] == "candidate"
    assert all(r["privacy"] == "local" for r in row["evidence_refs"])


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
