"""Offline tests for economy/moderation.py rules that need no database: who may withdraw a way, what a report
must carry, and that a verified way is never auto-hidden. The full hide -> find_ways -> restore path runs against
real databases in tests/test_verified_write_sharded_e2e.py."""
from __future__ import annotations

import asyncio

import pytest

from app.economy import moderation as m


def _run(coro):
    return asyncio.run(coro)


class _Pool:
    def __init__(self, sub=None, reporters=1, inserted=True):
        self.sub, self.reporters, self.inserted, self.sql = sub, reporters, inserted, []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return self.sub

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if sql.startswith("INSERT INTO way_reports"):
            return "r1" if self.inserted else None
        return self.reporters

    async def execute(self, sql, *a):
        self.sql.append(sql)


PROC = {"id": "row-1", "procedure_id": "p-1", "achieves_goal_id": "g-1", "name": "w", "steps": [],
        "availability": "active", "verification_state": "candidate"}


def test_only_the_submitter_can_withdraw(monkeypatch):
    pool = _Pool(sub={"id": "s1", "submitted_by": "alice", "procedure_row_id": "row-1", "status": "accepted"})
    with pytest.raises(m.ModerationError):
        _run(m.withdraw_way(pool, submission_id="s1", actor="bob"))

    hidden = []

    async def fake_set(pool, proc, action, **kw):
        hidden.append((proc["id"], action, kw["actor"]))

    async def fake_get(pool, row_id):
        return dict(PROC)

    monkeypatch.setattr(m, "_set_availability", fake_set)
    monkeypatch.setattr("app.services.procedures.get_procedure", fake_get)
    out = _run(m.withdraw_way(pool, submission_id="s1", actor="alice"))
    assert out["withdrawn"] and hidden == [("row-1", "withdrawn", "alice")]
    assert any("status = 'rejected'" in q for q in pool.sql)


def test_a_report_needs_a_known_category_and_a_user():
    with pytest.raises(m.ModerationError):
        _run(m.report_way(_Pool(), proc=PROC, reporter="u", category="rude"))
    with pytest.raises(m.ModerationError):
        _run(m.report_way(_Pool(), proc=PROC, reporter="", category="spam"))


def test_a_verified_way_is_never_auto_hidden(monkeypatch):
    hidden = []

    async def fake_set(*a, **k):
        hidden.append(a)

    monkeypatch.setattr(m, "_set_availability", fake_set)
    out = _run(m.report_way(_Pool(reporters=99), proc={**PROC, "verification_state": "verified"},
                            reporter="u", category="spam"))
    assert not out["hidden"] and hidden == [] and "admin" in out["note"]


def test_an_unscreenable_report_does_not_hide_on_its_own(monkeypatch):
    """If the re-screen can't run (no model), a malicious report counts toward the threshold but does not hide
    the way by itself -- 'could not check' is not 'flagged'."""
    from app.economy.content_screen import ScreenVerdict

    hidden = []

    async def fake_set(*a, **k):
        hidden.append(a)

    async def unscreened(sub, **kw):
        return ScreenVerdict(False, "screening unavailable", ["unscreened"])

    monkeypatch.setattr(m, "_set_availability", fake_set)
    out = _run(m.report_way(_Pool(reporters=1), proc=PROC, reporter="u", category="malicious", screen=unscreened))
    assert not out["hidden"] and hidden == []
