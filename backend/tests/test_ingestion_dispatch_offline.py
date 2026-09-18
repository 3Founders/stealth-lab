"""Offline proving tests for the trajectory dispatcher
(trajectory-ingestion-hardening task, Sec 5/20-D). Uses REAL
`OpenHandsTrajectorySource`/`normalize_openhands_trajectory` against a
temp directory (real filesystem, no database), with the DB-touching
writers (`open_ingestion_context`, `complete_ingestion_context`,
`write_normalized_trajectory`, `write_trajectory_episodes`)
monkeypatched -- same fake-service-layer convention
`test_promote_observation_job_offline.py` already established. Proves
the ORCHESTRATION: quarantine vs success routing, and that episode
assembly always follows a successful write.
"""
from __future__ import annotations

import json

import pytest

import app.services.ingestion_sources.dispatch as dispatch


def _patch_writers(monkeypatch, *, ingestion_context_id="ctx-1"):
    calls = {
        "opened": [], "completed": [], "quarantined": [],
        "written": [], "episodes": [],
    }

    async def fake_open_ingestion_context(pool, **kwargs):
        calls["opened"].append(kwargs)
        return ingestion_context_id

    async def fake_complete_ingestion_context(pool, ctx_id, *, status):
        calls["completed"].append((ctx_id, status))

    async def fake_write_normalized_trajectory(pool, trajectory, **kwargs):
        calls["written"].append(trajectory.trace_id)
        return {"trace_id": trajectory.trace_id, "records_seen": len(trajectory.events),
                "inserted": len(trajectory.events), "skipped_duplicate": 0,
                "skipped_malformed_events": trajectory.skipped_malformed_events}

    async def fake_write_trajectory_episodes(pool, *, session_id, trajectory, **kwargs):
        calls["episodes"].append(session_id)
        return {"parents_inserted": 1, "children_inserted": 0, "skipped_existing": 0}

    async def fake_quarantine(pool, **kwargs):
        calls["quarantined"].append(kwargs)

    monkeypatch.setattr(dispatch, "open_ingestion_context", fake_open_ingestion_context)
    monkeypatch.setattr(dispatch, "complete_ingestion_context", fake_complete_ingestion_context)
    monkeypatch.setattr(dispatch, "write_normalized_trajectory", fake_write_normalized_trajectory)
    monkeypatch.setattr(dispatch, "write_trajectory_episodes", fake_write_trajectory_episodes)
    monkeypatch.setattr(dispatch, "_quarantine", fake_quarantine)
    return calls


class FakePool:
    pass  # never touched directly -- every DB call goes through the monkeypatched functions


@pytest.mark.asyncio
async def test_good_trajectory_is_written_and_episoded(tmp_path, monkeypatch):
    (tmp_path / "good.json").write_text(json.dumps({
        "instance_id": "good-1",
        "history": [
            {"action": "run", "args": {"command": "pytest"}, "id": 1},
            {"observation": "run", "content": "1 passed", "extras": {"exit_code": 0}, "cause": 1},
        ],
    }), encoding="utf-8")
    calls = _patch_writers(monkeypatch)

    result = await dispatch.ingest_openhands_trajectories(FakePool(), str(tmp_path))

    assert result["trajectories_ingested"] == 1
    assert result["objects_quarantined"] == 0
    assert calls["written"] == ["good-1"]
    assert calls["episodes"] == ["good-1"]
    assert calls["completed"] == [("ctx-1", "completed")]
    assert calls["quarantined"] == []


@pytest.mark.asyncio
async def test_malformed_trajectory_is_quarantined_not_written(tmp_path, monkeypatch):
    (tmp_path / "bad.json").write_text("{not valid json", encoding="utf-8")
    calls = _patch_writers(monkeypatch)

    result = await dispatch.ingest_openhands_trajectories(FakePool(), str(tmp_path))

    assert result["trajectories_ingested"] == 0
    assert result["objects_quarantined"] == 1
    assert calls["written"] == []
    assert calls["episodes"] == []
    assert len(calls["quarantined"]) == 1
    assert calls["completed"] == [("ctx-1", "rejected")]


@pytest.mark.asyncio
async def test_one_bad_file_does_not_abort_the_rest_of_the_batch(tmp_path, monkeypatch):
    """The whole point of quarantine-not-raise: a batch of N files with
    one malformed entry still ingests the other N-1."""
    (tmp_path / "a_good.json").write_text(json.dumps({
        "instance_id": "good-a",
        "history": [{"action": "read", "args": {"path": "x.py"}, "id": 1},
                     {"observation": "read", "content": "ok", "extras": {}, "cause": 1}],
    }), encoding="utf-8")
    (tmp_path / "b_bad.json").write_text("not json at all {{{", encoding="utf-8")
    (tmp_path / "c_good.json").write_text(json.dumps({
        "instance_id": "good-c",
        "history": [{"action": "read", "args": {"path": "y.py"}, "id": 1},
                     {"observation": "read", "content": "ok", "extras": {}, "cause": 1}],
    }), encoding="utf-8")
    calls = _patch_writers(monkeypatch)

    result = await dispatch.ingest_openhands_trajectories(FakePool(), str(tmp_path))

    assert result["trajectories_ingested"] == 2
    assert result["objects_quarantined"] == 1
    assert sorted(calls["written"]) == ["good-a", "good-c"]


@pytest.mark.asyncio
async def test_empty_directory_ingests_nothing_without_error(tmp_path, monkeypatch):
    calls = _patch_writers(monkeypatch)
    result = await dispatch.ingest_openhands_trajectories(FakePool(), str(tmp_path))
    assert result == {
        "trajectories_ingested": 0, "events_normalized": 0,
        "objects_quarantined": 0, "results": [],
    }
    assert calls["opened"] == []
