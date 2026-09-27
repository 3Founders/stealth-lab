"""docs/knowledge_side_improvements.md against a real database (migration 124): verified examples
are stored on the Procedure and returned with it (changes 1-2); `related_examples` returns verified
solved examples of close variants that the applicability gate rejects, and drops firmly unrelated
ones (changes 3-4) -- all through the real `find_ways`, with a fake judge."""
from __future__ import annotations

import json

import pytest

import app.mcp_server.server as srv
from app.config import settings
from app.services import retrieval_service as rs
from app.services import search_projection as sp
from app.services import verified_examples as ve
from app.services.procedures import capture_procedure
from tests.identity_fakes import CallbackProvider, make_judge
from tests.test_goal_abstraction_e2e import DATABASE_URL, _run_id, pool  # noqa: F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")


class _RC:
    def __init__(self, pool): self.lifespan_context = {"pool": pool}


class _Ctx:
    def __init__(self, pool): self.request_context = _RC(pool)


def _judge(verdicts: dict):
    """verdicts: substring of the candidate text -> (task_goal relation, confidence, applies?).
    Markers carry the run id: the test database keeps earlier runs' Goals."""
    def verdict(kind, a, b):
        for marker, (relation, conf, applies) in verdicts.items():
            if marker in b.lower():
                if kind == "task_goal":
                    return (relation, conf)
                if kind == "task_procedure":
                    return ("applies", 0.9) if applies else ("not_applicable", 0.9)
        return ("unrelated", 0.99) if kind == "task_goal" else ("not_applicable", 0.99)
    return make_judge(CallbackProvider(verdict, name="jev"))


async def _setup(pool, run: str):
    rows = {}
    for key, goal, code in (("rows", f"compute row-wise percentages of a table {run}",
                             "df = df.div(df.sum(axis=1), axis=0)"),
                            ("clf", f"train a text classifier {run}", "clf = LogisticRegression().fit(X, y)")):
        proc = await capture_procedure(
            pool, name=f"{goal} way", goal=goal, steps=[{"description": "do it"}],
            provenance="prior_library", scope_type="global")
        await ve.attach(pool, str(proc["id"]), ve.build_example(task=f"Task: {goal}", code=code, verified_by="tests"))
        rows[key] = str(proc["procedure_id"])
    await sp.drain_outbox(pool)
    return rows


@pytest.fixture
def flags(monkeypatch):
    monkeypatch.setattr(settings, "knowledge_verified_examples", True)
    monkeypatch.setattr(settings, "knowledge_related_examples", True)


@pytest.mark.asyncio
async def test_close_variant_rejected_by_the_gate_still_arrives_as_a_related_example(pool, monkeypatch, flags):
    run = _run_id()
    procs = await _setup(pool, run)
    monkeypatch.setattr(rs, "default_judge", lambda: _judge({
        f"row-wise percentages of a table {run}": ("unrelated", 0.49, False),   # the close variant (problem 116)
        f"text classifier {run}": ("unrelated", 0.99, False),                   # firmly unrelated
    }))
    out = json.loads(await srv.find_ways(f"compute column percentages of a table {run}", _Ctx(pool)))
    assert out["outcome"] == "no_match"
    related = out["related_examples"]
    assert [r["procedure_id"] for r in related] == [procs["rows"]]
    assert related[0]["code"] == "df = df.div(df.sum(axis=1), axis=0)"
    assert "NOT verified to apply" in related[0]["label"]


@pytest.mark.asyncio
async def test_resolved_procedure_carries_its_verified_example_and_is_not_repeated(pool, monkeypatch, flags):
    run = _run_id()
    procs = await _setup(pool, run)
    monkeypatch.setattr(rs, "default_judge", lambda: _judge({
        f"row-wise percentages of a table {run}": ("matches", 0.95, True),
        f"text classifier {run}": ("unrelated", 0.99, False),
    }))
    out = json.loads(await srv.find_ways(f"compute row-wise percentages of a table {run}", _Ctx(pool)))
    assert out["outcome"] == "resolved"
    assert out["procedures"][0]["procedure_id"] == procs["rows"]
    assert out["procedures"][0]["verified_example"]["code"].startswith("df = df.div")
    assert all(r["procedure_id"] != procs["rows"] for r in out["related_examples"])


@pytest.mark.asyncio
async def test_flags_off_leave_find_ways_unchanged(pool, monkeypatch):
    run = _run_id()
    await _setup(pool, run)
    monkeypatch.setattr(rs, "default_judge", lambda: _judge({f"row-wise percentages of a table {run}": ("matches", 0.95, True)}))
    out = json.loads(await srv.find_ways(f"compute row-wise percentages of a table {run}", _Ctx(pool)))
    assert out["outcome"] == "resolved"
    assert "related_examples" not in out and "verified_example" not in out["procedures"][0]


@pytest.mark.asyncio
async def test_extraction_from_a_verified_solution_stores_the_example(pool, monkeypatch, flags):
    from types import SimpleNamespace

    from app.services.procedure_extraction import extract_procedure
    from app.services.procedure_extraction.evidence import AgentRunEvidenceSource

    run = _run_id()
    code = "result = df.div(df.sum(axis=0), axis=1)"
    reply = json.dumps({"capability_statement": f"Column percentages {run}",
                        "steps": [{"action": "Divide by the column sums", "apis": ["div", "sum"]}],
                        "pitfalls": ["Divide along the right axis"]})
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))]))))
    source = AgentRunEvidenceSource(
        goal_text=f"compute column percentages {run}", outcome="success", tool_sequence=["write_code", "run_tests"],
        steps_used=2, observations=[
            {"observation_type": "task_statement", "label": "task", "properties": {"text": f"Column shares {run}"}},
            {"observation_type": "code_solution", "label": "solution",
             "properties": {"code": code, "verified": True, "verified_by": "unit tests"}}])
    result = await extract_procedure(pool, source, client=client, visibility="public")
    assert result.procedure_id
    stored = await pool.fetchval("SELECT verified_example FROM procedures WHERE id = $1::uuid", str(result.version_row_id))
    assert stored["code"] == code and stored["task"] == f"Column shares {run}" and stored["verified_by"] == "unit tests"
