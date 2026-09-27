"""Verified solutions on the provenance model (docs/knowledge_side_improvements.md changes 1-2, migration 124),
against a real database: extraction from a verified code solution records a 'verified_solution' source artifact
and the Procedure's source_locator -- the code itself when it has no durable home (inline without an object store),
ONLY the location when it is committed in a repo -- and find_ways returns it with the Procedure. Flags off: nothing."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.mcp_server.server as srv
from app.config import settings
from app.services import retrieval_service as rs
from app.services import search_projection as sp
from app.services import verified_solutions as vs
from tests.identity_fakes import CallbackProvider, make_judge
from tests.test_goal_abstraction_e2e import DATABASE_URL, _run_id, pool  # noqa: F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")


class _RC:
    def __init__(self, pool): self.lifespan_context = {"pool": pool}


class _Ctx:
    def __init__(self, pool): self.request_context = _RC(pool)


@pytest.fixture
def flag(monkeypatch):
    monkeypatch.setattr(settings, "knowledge_verified_examples", True)
    monkeypatch.setattr("app.services.object_storage.get_store", lambda: None)   # no object store: inline path


def _client(reply: str):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))]))))


async def _extract(pool, run: str, code: str, locator=None):
    from app.services.procedure_extraction import extract_procedure
    from app.services.procedure_extraction.evidence import AgentRunEvidenceSource

    reply = json.dumps({"capability_statement": f"Column percentages {run}",
                        "steps": [{"action": "Divide by the column sums", "apis": ["div", "sum"]}],
                        "pitfalls": ["Divide along the right axis"]})
    props = {"code": code, "verified": True, "verified_by": "unit tests", "language": "python",
             **({"locator": locator} if locator else {})}
    source = AgentRunEvidenceSource(
        goal_text=f"compute column percentages {run}", outcome="success", tool_sequence=["edit", "test"], steps_used=2,
        observations=[{"observation_type": "task_statement", "label": "task", "properties": {"text": f"Column shares {run}"}},
                      {"observation_type": "code_solution", "label": "solution", "properties": props}])
    result = await extract_procedure(pool, source, client=_client(reply), visibility="public")
    assert result.procedure_id
    row = await pool.fetchrow("SELECT source_locator, source_artifacts FROM procedures WHERE id = $1::uuid",
                              str(result.version_row_id))
    return result, row


@pytest.mark.asyncio
async def test_solution_without_a_durable_home_is_kept_once_as_an_artifact(pool, flag):
    run = _run_id()
    code = f"result = df.div(df.sum(axis=0), axis=1)  # {run}"
    result, row = await _extract(pool, run, code)
    ref = vs.solution_ref(row["source_artifacts"])
    assert ref["role"] == "verified_solution" and ref["execution_allowed"] is False and ref["note"] == f"Column shares {run}"
    assert row["source_locator"]["uri"].startswith("kel:verified-solution:")
    art = await pool.fetchrow("SELECT extraction_status, execution_allowed, content_ref FROM ingested_artifacts "
                              "WHERE id = $1::uuid", ref["artifact_id"])
    assert art["extraction_status"] == "stored" and art["execution_allowed"] is False
    got = await vs.resolve(pool, row["source_artifacts"], row["source_locator"])
    assert got["code"] == code and got["code_available"] and got["verified_by"] == "unit tests"


@pytest.mark.asyncio
async def test_solution_committed_in_a_repo_is_pointed_at_never_copied(pool, flag):
    run = _run_id()
    loc = {"repository": "acme/calc", "commit": f"abc{run}", "path": "calc/pct.py", "line_start": 3, "line_end": 9}
    result, row = await _extract(pool, run, f"def pct(df): return df.div(df.sum())  # {run}", locator=loc)
    assert row["source_locator"]["uri"] == f"https://github.com/acme/calc/blob/abc{run}/calc/pct.py"
    assert row["source_locator"]["granularity"] == "span" and row["source_locator"]["line_start"] == 3
    ref = vs.solution_ref(row["source_artifacts"])
    art = await pool.fetchrow("SELECT extraction_status, content_ref, repository, path FROM ingested_artifacts "
                              "WHERE id = $1::uuid", ref["artifact_id"])
    assert art["extraction_status"] == "metadata_only" and art["content_ref"] is None
    assert (art["repository"], art["path"]) == ("acme/calc", "calc/pct.py")
    got = await vs.resolve(pool, row["source_artifacts"], row["source_locator"])
    assert got["code"] is None and got["code_available"] is False and got["locator"]["path"] == "calc/pct.py"


@pytest.mark.asyncio
async def test_find_ways_returns_the_verified_solution_with_the_procedure(pool, flag, monkeypatch):
    run = _run_id()
    code = f"df = df.div(df.sum(axis=1), axis=0)  # {run}"
    await _extract(pool, run, code)
    await sp.drain_outbox(pool)
    marker = f"compute column percentages {run}"
    monkeypatch.setattr(rs, "default_judge", lambda: make_judge(CallbackProvider(
        lambda kind, a, b: (("matches", 0.95) if marker in b.lower() else ("unrelated", 0.99)) if kind == "task_goal"
        else (("applies", 0.9) if kind == "task_procedure" else ("distinct", 0.9)), name="jev")))
    out = json.loads(await srv.find_ways(marker, _Ctx(pool)))
    assert out["outcome"] == "resolved"
    assert out["procedures"][0]["verified_solution"]["code"] == code
    monkeypatch.setattr(settings, "knowledge_verified_examples", False)
    out = json.loads(await srv.find_ways(marker, _Ctx(pool)))
    assert "verified_solution" not in out["procedures"][0]


def test_only_a_commit_plus_path_counts_as_durable():
    assert vs.durable_locator({"repository": "a/b", "path": "x.py"}) is None
    assert vs.durable_locator({"commit": "c", "path": "x.py"}) is None
    assert vs.durable_locator({"repository": "a/b", "commit": "c", "path": "x.py"})["uri"] == "https://github.com/a/b/blob/c/x.py"
    assert vs.solution_ref([{"artifact_id": "1", "role": "documentation"}]) is None
