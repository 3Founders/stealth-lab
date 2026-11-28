"""A session (the desktop app) reports a step's output with the files it wrote; they are stored with the step.

The desktop runs a Way's AI steps on the person's computer (mode "session"), so the files a step writes exist there.
POST /v1/goals/runs/{id}/report carries them back as [{name, b64}]; this pins the decoding, its refusals, and that the
runner stores them on the step's own entry like a sandboxed step's.
"""
from __future__ import annotations

import asyncio
import base64

import pytest

from app.execution import way_runs as wr
from app.submissions import api
from app.submissions.runs import RunError


def _run():
    return {"id": "11111111-1111-1111-1111-111111111111", "way_id": "w", "owner_id": "o", "mode": "session",
            "input": "", "status": "waiting_session", "next_step": 1, "error": "", "waiting": {"step": 1},
            "plan": [{"order": 1, "title": "Write the outline", "kind": "prompt", "prompt": "Outline it.",
                      "produces": "outline.md", "model": None, "strict": False},
                     {"order": 2, "title": "Approve", "kind": "human", "description": "Check it.", "prompt": "",
                      "model": None, "strict": False}],
            "outputs": [], "step_inputs": None, "sandbox": None}


@pytest.fixture()
def runner(monkeypatch):
    state = {"run": _run(), "stored": []}

    async def load(pool, run_id, owner_id):
        return state["run"]

    async def save(pool, run):
        state["saved"] = dict(run)

    async def collect(step, run, output, entry):
        cap = run.pop("_captured", None)
        state["stored"].append((step["order"], cap, output))
        if cap:
            entry["files"] = [{"name": n, "size_bytes": len(d)} for n, d in cap]

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(wr, "_load", load)
    monkeypatch.setattr(wr, "_save", save)
    monkeypatch.setattr(wr, "_collect_artifacts", collect)
    monkeypatch.setattr(wr, "record_outcome", nothing)
    monkeypatch.setattr(wr, "_settle_purchase", nothing)
    monkeypatch.setattr(wr, "_cancelled_meanwhile", lambda pool, run: asyncio.sleep(0, result=False))
    return state


def test_a_reported_step_keeps_the_files_the_session_wrote(runner):
    b64 = base64.b64encode(b"# Outline\n\n1. Problem\n").decode()
    out = asyncio.run(api.report_step(None, "o", runner["run"]["id"], output="Wrote the outline.",
                                      files=[{"name": "outline.md", "b64": b64}]))
    assert runner["stored"] == [(1, [("outline.md", b"# Outline\n\n1. Problem\n")], "Wrote the outline.")]
    assert out["outputs"][0]["actor"] == "session" and out["outputs"][0]["files"][0]["name"] == "outline.md"
    assert out["status"] == "waiting_human"                  # on to the person's step, which waits for them


def test_a_step_reported_without_files_still_has_its_declared_file_kept_from_text(runner):
    asyncio.run(api.report_step(None, "o", runner["run"]["id"], output="# Outline\n\nall of it"))
    assert runner["stored"] == [(1, None, "# Outline\n\nall of it")]


@pytest.mark.parametrize("files, why", [
    ([{"name": "a.md"}], "name, b64"),
    ([{"name": "a.md", "b64": "not base64!!"}], "not valid base64"),
    ([{"name": "a.md", "b64": "eA=="}] * 21, "at most 20"),
])
def test_a_bad_report_is_refused_whole(runner, files, why):
    with pytest.raises(RunError, match=why):
        asyncio.run(api.report_step(None, "o", runner["run"]["id"], output="x", files=files))
    assert runner["stored"] == []                             # nothing half-stored


def test_your_turn_names_the_files_to_write_and_to_read():
    step = {"order": 3, "title": "TeX", "kind": "prompt", "prompt": "Write the paper.", "produces": "main.tex",
            "imports": ["outline.md"], "model": None, "strict": False}
    turn = asyncio.run(wr.your_turn(step, {**_run(), "outputs": []}))
    assert turn["produces"] == ["main.tex"] and turn["imports"] == ["outline.md"]
    assert "main.tex" in turn["do"]
