"""
DB-free coverage proving the ongoing-sync hook (app.stealth.ongoing_sync.
sync_delta_if_synced) is actually wired into the additional local write
points beyond record_stealth_edit: record_run_update and
close_exploration (docs/local_project_sync_security.md §G). The heavy
DB-backed internals of each tool are stubbed out here -- this test proves
the WIRING, not those tools' own unrelated behavior (which their own
DB-backed e2e suites already cover).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.authn import Actor, reset_current_actor, set_current_actor


def _run(coro):
    return asyncio.run(coro)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


class _actor_on_cv:
    def __init__(self, subject):
        self.actor = Actor(subject=subject) if subject else None

    def __enter__(self):
        self._tok = set_current_actor(self.actor)

    def __exit__(self, *exc):
        reset_current_actor(self._tok)


def test_record_run_update_calls_the_ongoing_sync_hook(tmp_path, monkeypatch):
    import app.mcp_server.server as srv

    async def fake_record_run_update(pool, **kwargs):
        return {
            "id": "rec-1", "execution_run_id": kwargs["execution_run_id"], "kind": kwargs["kind"],
            "node_order": kwargs.get("node_order"), "actor_agent_id": "me", "answers_id": kwargs.get("answers_id"),
            "target_agent_id": kwargs.get("target_agent_id"),
        }

    monkeypatch.setattr("app.execution.run_collaboration.record_run_update", fake_record_run_update)

    async def fake_generate_projection(pool, *, workspace_root, procedure_run_id):
        return {}

    monkeypatch.setattr("app.execution.stealth_projection.generate_projection", fake_generate_projection)
    monkeypatch.setattr("app.stealth.journal.append_event", lambda *a, **k: 1)

    calls = []

    async def fake_sync_delta(repo_path, *, stable_project_id, file_path, summary, actor):
        calls.append((repo_path, stable_project_id, file_path, summary, actor))
        return "not_synced_on_this_machine"

    monkeypatch.setattr("app.stealth.ongoing_sync.sync_delta_if_synced", fake_sync_delta)

    pool = SimpleNamespace()
    with _actor_on_cv("me"):
        result_json = _run(srv.record_run_update(
            "run-1", "NOTE", "made progress", _FakeContext(pool), repo_path=str(tmp_path),
        ))

    assert len(calls) == 1
    repo_path, stable_id, file_path, summary, actor = calls[0]
    assert repo_path == str(tmp_path)
    assert file_path == "run.md"
    assert "made progress" in summary
    assert '"ongoing_sync"' in result_json


def test_record_run_update_without_repo_path_never_calls_the_hook(tmp_path, monkeypatch):
    import app.mcp_server.server as srv

    async def fake_record_run_update(pool, **kwargs):
        return {
            "id": "rec-1", "execution_run_id": kwargs["execution_run_id"], "kind": kwargs["kind"],
            "node_order": kwargs.get("node_order"), "actor_agent_id": "me", "answers_id": kwargs.get("answers_id"),
            "target_agent_id": kwargs.get("target_agent_id"),
        }

    monkeypatch.setattr("app.execution.run_collaboration.record_run_update", fake_record_run_update)

    calls = []
    monkeypatch.setattr("app.stealth.ongoing_sync.sync_delta_if_synced", lambda *a, **k: calls.append(1))

    pool = SimpleNamespace()
    with _actor_on_cv("me"):
        _run(srv.record_run_update("run-1", "NOTE", "no repo_path given", _FakeContext(pool)))

    assert calls == []


def test_close_exploration_calls_the_ongoing_sync_hook(tmp_path, monkeypatch):
    import app.mcp_server.server as srv

    async def fake_close_exploration(repo_path, exploration_id, *, status, resolution, pool, created_by, owner_id):
        return None  # no claim captured for this test

    monkeypatch.setattr("app.stealth.exploration.close_exploration", fake_close_exploration)

    calls = []

    async def fake_sync_delta(repo_path, *, stable_project_id, file_path, summary, actor):
        calls.append((repo_path, stable_project_id, file_path, summary, actor))
        return "not_synced_on_this_machine"

    monkeypatch.setattr("app.stealth.ongoing_sync.sync_delta_if_synced", fake_sync_delta)

    pool = SimpleNamespace()
    with _actor_on_cv("me"):
        result_json = _run(srv.close_exploration(
            str(tmp_path), "E-abc123", _FakeContext(pool), status="RESOLVED", resolution="it was a config issue",
        ))

    assert len(calls) == 1
    repo_path, stable_id, file_path, summary, actor = calls[0]
    assert repo_path == str(tmp_path)
    assert file_path == "exploration.md"
    assert "config issue" in summary
    assert '"ongoing_sync"' in result_json
