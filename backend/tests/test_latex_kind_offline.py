"""LaTeX as a canvas kind: a document can be created as latex, an agent's ```canvas ... kind=latex block is read as one, and when the agent has
the cheap model write a paper it is given the LaTeX brief and the result is fenced as kind=latex. No network, no database."""
from __future__ import annotations

import json
import pathlib

import pytest

from app.services import room_agent, room_docs as rd
from tests.test_room_agent_offline import Model, _never, _reply, _some_tools
from tests.test_room_docs_offline import ROOM, Pool, run


def test_a_latex_document_keeps_its_kind():
    pool = Pool()
    made = run(rd.create_doc(pool, "alice", str(ROOM), "Paper", "latex", "\\documentclass{article}"))
    assert made["kind"] == "latex"
    assert "latex" in rd.KINDS


def test_an_unknown_kind_is_still_a_document():
    pool = Pool()
    assert run(rd.create_doc(pool, "alice", str(ROOM), "Odd", "pdf", "x"))["kind"] == "document"


def test_a_canvas_block_with_kind_latex_is_read_as_latex():
    body = "Here:\n```canvas title=\"Proof\" kind=latex\n\\section{A}\n$x$\n```\n"
    assert rd.canvas_blocks(body) == [("Proof", "latex", "\\section{A}\n$x$")]


def test_the_migration_widens_the_kind_check():
    sql = (pathlib.Path(__file__).parent.parent / "db" / "175_room_docs_latex.sql").read_text(encoding="utf-8")
    assert "'latex'" in sql and "DROP CONSTRAINT IF EXISTS room_docs_kind_check" in sql


@pytest.mark.asyncio
async def test_the_cheap_model_is_briefed_for_latex_and_the_result_is_a_latex_canvas():
    call = {"id": "c", "type": "function", "function": {"name": "call_model", "arguments": json.dumps(
        {"prompt": "a short note on the Basel problem", "canvas_title": "Basel", "kind": "latex"})}}
    model = Model(_reply(tool_calls=[call]), _reply("Written."))
    seen: list[str] = []

    async def cheap(prompt):
        seen.append(prompt)
        return "```latex\n\\documentclass{article}\n\\begin{document}Hi $x$\\end{document}\n```"

    out = await room_agent.converse("write a note with equations on Basel", [], model_call=model, find_tools=_some_tools, use_tool=_never, call_cheap=cheap)
    assert "LaTeX document" in seen[0] and "Basel problem" in seen[0]
    assert '```canvas title="Basel" kind=latex\n\\documentclass{article}' in out.text
