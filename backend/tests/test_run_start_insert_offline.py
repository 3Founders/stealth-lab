"""A run started outside a room still inserts a valid row, and a Way's per-step price reads its real steps.

2026-11-28: start() passed NULL for way_runs.step_inputs (NOT NULL DEFAULT '{}') whenever there were no step inputs,
so every run started outside a room failed with a 500. step_costs read goal_submissions.steps, a column that does not
exist (steps live in goal_submission_steps).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.execution import way_runs


class _Stop(Exception):
    pass


def _start_until_insert(monkeypatch, **kw):
    captured: dict = {}

    async def fake_way(pool, way_id, owner_id):
        return {"id": way_id}

    async def fake_expand(pool, way, owner_id):
        return [{"order": 1, "kind": "prompt", "prompt": "do it"}]

    async def no_licence(pool, **_):
        return None

    from app.services import procedure_billing
    monkeypatch.setattr(way_runs, "_way", fake_way)
    monkeypatch.setattr(way_runs, "expand", fake_expand)
    monkeypatch.setattr(procedure_billing, "check_entitlement", no_licence)

    pool = AsyncMock()

    async def fetchval(sql, *args):
        if "INSERT INTO way_runs" in sql:
            captured["args"] = args
            raise _Stop
        return None

    pool.fetchval.side_effect = fetchval
    with pytest.raises(_Stop):
        asyncio.run(way_runs.start(pool, "11111111-1111-1111-1111-111111111111", owner_id="u1", **kw))
    return captured["args"]


def test_a_run_outside_a_room_inserts_empty_step_inputs_not_null(monkeypatch):
    args = _start_until_insert(monkeypatch, mode="session")
    room_id, step_inputs = args[5], args[6]
    assert room_id is None
    assert step_inputs is not None and step_inputs == {}


def test_a_room_run_keeps_its_step_inputs(monkeypatch):
    args = _start_until_insert(monkeypatch, mode="session", room_id="22222222-2222-2222-2222-222222222222",
                               step_inputs={"1": {"topic": "x"}})
    assert args[6] == {"1": {"topic": "x"}}


def test_step_costs_reads_the_steps_table():
    from app.submissions import api

    pool = AsyncMock()
    pool.fetchval.return_value = 1
    pool.fetch.return_value = [
        {"order": 1, "kind": "automated", "title": "Research", "prompt": "Find the market size " * 20,
         "description": None, "locator": None, "produces": None},
        {"order": 2, "kind": "automated", "title": "Write", "prompt": "Write the report " * 20,
         "description": None, "locator": None, "produces": "report.md"},
    ]
    out = asyncio.run(api.step_costs(pool, "33333333-3333-3333-3333-333333333333"))
    sql = pool.fetch.await_args.args[0]
    assert "goal_submission_steps" in sql
    assert [s["order"] for s in out["steps"]] == [1, 2]
    assert out["total_price_high_paise"] >= out["total_price_low_paise"] > 0


def test_step_costs_404s_an_unknown_way():
    from app.submissions import api

    pool = AsyncMock()
    pool.fetchval.return_value = None
    with pytest.raises(Exception) as exc:
        asyncio.run(api.step_costs(pool, "44444444-4444-4444-4444-444444444444"))
    assert "no such Way" in str(exc.value)
