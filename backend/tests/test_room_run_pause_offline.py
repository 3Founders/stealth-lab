"""A room's run must pause on a human step and stay resumable; and its JSON must be JSON.

Two defects found by the owner on 2026-11-23 in room 01a208c2 (Way dd78d725, 16 steps, 5 human):

1. Pressing Start showed "Finished." The run was in fact `waiting_human` at step 1 -- `readiness()` collapsed the
   waiting states into "running", and the pane treated anything that was not "running" or "done"/"failed" as gone.
   A run paused on a person is a third state and has to be visible as one.

2. Nothing could continue it. `way_runs.report(done=True, note=...)` existed behind the MCP tool only, so a room
   could start a run and never get past its first human step.

3. Every `way_runs` row (32 of 32) stored `outputs` and `plan` as a JSON *string* (`"[]"`, not `[]`), because
   `_save` called `json.dumps` on a pool whose jsonb codec already encodes. `jsonb_array_length(outputs)` then
   raised "cannot get array length of a scalar". The codec is configured in `app/db/session.py::_init_connection`.
"""
from __future__ import annotations

import json

import pytest

from app.services import room_runs as rr


class FakePool:
    def __init__(self, *, room=None, run=None, steps=None, captured=None):
        self.room, self.run, self.steps = room, run, steps or []
        self.captured = captured if captured is not None else {}

    async def fetchrow(self, sql, *args):
        if "FROM rooms" in sql:
            return self.room
        if "goal_submissions" in sql:
            return {"parent": "proc-1"}
        return None

    async def fetch(self, sql, *args):
        return self.steps

    async def fetchval(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        self.captured["sql"], self.captured["args"] = sql, args


def _step_entry(n: int, kind: str = "ai") -> dict:
    return {"step_order": n, "title": f"Step {n}", "kind": kind, "status": "ai_approved", "inputs": None}


@pytest.mark.asyncio
async def test_a_run_paused_on_a_person_reports_waiting_not_running(monkeypatch):
    """The state the pane needs in order to show 'waiting on you' instead of 'finished'."""
    async def fake_inputs(pool, *, submission_id):
        return []

    monkeypatch.setattr(rr, "inputs_needed", fake_inputs)
    monkeypatch.setattr(rr, "_live_run", lambda pool, room_id: _async({
        "id": "run-1", "status": "waiting_human", "owner_id": "u-1",
        "waiting": {"kind": "step", "order": 1, "title": "Give the brief"},
    }))

    out = await rr.readiness(FakePool(), room_id="room-1", submission_id="way-1")

    assert out["state"] == "waiting_human"
    assert out["waiting"]["order"] == 1, "the pane needs to know WHICH step it is waiting on"


@pytest.mark.asyncio
async def test_resuming_answers_the_waiting_step_and_nothing_else(monkeypatch):
    seen = {}

    async def fake_report(pool, run_id, *, owner_id, done=False, note="", **_kw):
        seen.update(run_id=run_id, owner_id=owner_id, done=done, note=note)
        return {"status": "running"}

    monkeypatch.setattr("app.execution.way_runs.report", fake_report)
    monkeypatch.setattr(rr, "_live_run", lambda pool, room_id: _async({
        "id": "run-1", "status": "waiting_human", "owner_id": "u-1",
        "waiting": {"kind": "step", "order": 1},
    }))

    out = await rr.resume(FakePool(), room_id="room-1", note="I wrote the brief", owner_id="u-1")

    assert out["state"] == "running"
    assert seen == {"run_id": "run-1", "owner_id": "u-1", "done": True, "note": "I wrote the brief"}


@pytest.mark.asyncio
async def test_a_run_that_is_not_waiting_cannot_be_resumed(monkeypatch):
    """Resuming a finished run would replay steps and charge for them again."""
    monkeypatch.setattr(rr, "_live_run", lambda pool, room_id: _async({
        "id": "run-1", "status": "done", "owner_id": "u-1"}))
    monkeypatch.setattr("app.execution.way_runs.report", _explode)

    with pytest.raises(rr.RoomRunError) as exc:
        await rr.resume(FakePool(), room_id="room-1", note="", owner_id="u-1")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_someone_elses_run_cannot_be_resumed(monkeypatch):
    monkeypatch.setattr(rr, "_live_run", lambda pool, room_id: _async({
        "id": "run-1", "status": "waiting_human", "owner_id": "someone-else"}))
    monkeypatch.setattr("app.execution.way_runs.report", _explode)

    with pytest.raises(rr.RoomRunError) as exc:
        await rr.resume(FakePool(), room_id="room-1", note="", owner_id="u-1")
    assert exc.value.status_code == 403


# ------------------------------------------------------------------ the JSON that was being stored as a string

def test_json_is_handed_to_the_codec_as_an_object_not_as_text():
    """`_save` must pass a list/dict; the pool's jsonb codec does the encoding.

    Passing `json.dumps(x)` stored a JSON string scalar -- measured: list -> jsonb_typeof 'array',
    json.dumps(list) -> jsonb_typeof 'string'. Every row written the old way was a quoted array.
    """
    from app.execution.way_runs import _jsonable

    assert _jsonable([{"order": 1}]) == [{"order": 1}]
    assert _jsonable({"kind": "step"}) == {"kind": "step"}
    assert _jsonable(None) is None, "absent waiting must stay SQL NULL, not the JSON literal null"


def test_a_spliced_lookup_result_is_decoded_before_it_is_stored():
    """A lookup can hand back JSON text; storing that text as text would nest one level too deep."""
    from app.execution.way_runs import _jsonable

    assert _jsonable('[{"order": 1}]') == [{"order": 1}]


def test_text_that_is_not_json_is_left_alone():
    from app.execution.way_runs import _jsonable

    assert _jsonable("a note, not JSON") == "a note, not JSON"


# ---- helpers

async def _async(value):
    return value


async def _explode(*a, **k):  # noqa: ANN002, ANN003
    raise AssertionError("report() must not be reached")
