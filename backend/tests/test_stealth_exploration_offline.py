"""
G13 P4 -- `.stealth/exploration.md` (`app.stealth.exploration`): open
unknowns folded from the journal, pure filesystem, no database.
"""
from __future__ import annotations

import asyncio

import pytest

from app.stealth.exploration import (
    close_exploration,
    exploration_id,
    list_explorations,
    open_exploration,
    render_exploration_page,
)
from app.stealth.format import parse_idx


def test_open_close_list_round_trip_via_journal(tmp_path):
    ws = str(tmp_path)
    eid = open_exploration(ws, owner="agent-C", question="Does the registry invoke charge()?",
                           scope="src/plugins/**")
    assert eid.startswith("E-")
    rows = list_explorations(ws)
    assert len(rows) == 1 and rows[0]["status"] == "ACTIVE"
    assert rows[0]["owner"] == "agent-C" and rows[0]["scope"] == "src/plugins/**"

    claim_id = asyncio.run(close_exploration(ws, eid, status="RESOLVED", resolution="no, it is static"))
    assert claim_id is None  # no pool given -> journal-only, no claim capture
    rows = list_explorations(ws)
    assert rows[0]["status"] == "RESOLVED" and rows[0]["resolution"] == "no, it is static"
    assert list_explorations(ws, include_closed=False) == []


def test_close_without_pool_never_touches_the_database(tmp_path, monkeypatch):
    """Pool-based claim capture is opt-in: omitting `pool` (every existing
    caller) must not even import app.services.claims, let alone call it."""
    ws = str(tmp_path)
    eid = open_exploration(ws, owner="a", question="q", scope="s")
    assert asyncio.run(close_exploration(ws, eid, status="RESOLVED", resolution="r")) is None
    assert asyncio.run(close_exploration(ws, eid, status="ABANDONED", pool=object())) is None
    assert asyncio.run(close_exploration(ws, eid, status="RESOLVED", resolution="", pool=object())) is None


def test_id_is_stable_for_same_question_and_scope(tmp_path):
    ws = str(tmp_path)
    a = open_exploration(ws, owner="x", question="Q one", scope="s")
    b = open_exploration(ws, owner="y", question="Q one", scope="s")   # re-open, same id
    assert a == b == exploration_id("Q one", "s")
    assert len(list_explorations(ws)) == 1                              # not duplicated


def test_render_exploration_page_is_addressable(tmp_path):
    ws = str(tmp_path)
    open_exploration(ws, owner="a1", question="unknown one", scope="pkg/a/**")
    open_exploration(ws, owner="a2", question="unknown two", scope="pkg/b/**")
    md, rows = render_exploration_page(ws)
    assert len(rows) == 2
    lines = md.splitlines()
    for r in rows:
        window = lines[r.start - 1:r.end]
        assert window[0] == f"## EXPLORATION {r.obj_id}"
        assert any("unknown" in ln for ln in window)
    # index parses cleanly, 9 fields
    for fields in parse_idx("\n".join(r.render() for r in rows)):
        assert len(fields) == 9 and fields[5] == "exploration.md"


def test_empty_is_honest(tmp_path):
    md, rows = render_exploration_page(str(tmp_path))
    assert rows == []
    assert "no open explorations" in md
