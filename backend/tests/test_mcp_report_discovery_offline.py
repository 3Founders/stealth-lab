"""
Offline tests for the MCP v1 contribution path: report_discovery (stores a
private candidate claim linked to a Procedure step) and the
stealth://procedures/{id}/claims resource that returns it, grouped into
contiguous CLAIM blocks. No DB: the claim store, source registry, procedure
resolver and caller identity are faked.
"""
from __future__ import annotations

import asyncio
import json

import app.mcp_server.resources as res
import app.mcp_server.server as srv
from app.services.access import AccessScope


def _run(coro):
    return asyncio.run(coro)


class _RC:
    def __init__(self, pool=None):
        self.lifespan_context = {"pool": pool}


class _Ctx:
    def __init__(self, pool=None):
        self.request_context = _RC(pool)


PROC = {"id": "row-1", "procedure_id": "P-1", "name": "Create a New DOCX Document", "goal": "make a docx"}


def _wire(monkeypatch, *, viewer="user-1", proc=PROC):
    captured = {}
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope(viewer_id=viewer))

    async def fake_resolve(pool, procedure_id, access_scope=None):
        from app.services.applicability import ProcedureNotFound
        if proc is None:
            raise ProcedureNotFound(f"no live procedure for {procedure_id}")
        return proc

    async def fake_source(pool, **kw):
        captured["source"] = kw
        return {"id": "S-1"}

    async def fake_capture(pool, **kw):
        captured["claim"] = kw
        return "C-9"

    monkeypatch.setattr(srv, "_resolve_live_procedure", fake_resolve)
    monkeypatch.setattr("app.services.sources.register_source", fake_source)
    monkeypatch.setattr("app.services.claims.capture_claim", fake_capture)
    return captured


def _report(**kw):
    args = dict(kind="fix", procedure_id="P-1", problem="Document is not a constructor on docx 9",
                solution="use new Document({sections:[...]})", ctx=_Ctx(), step_order=4,
                proof="node build.js -> out.docx 11KB")
    args.update(kw)
    return _run(srv.report_discovery(**args))


def test_rejects_unknown_kind(monkeypatch):
    _wire(monkeypatch)
    assert _report(kind="vibes").startswith("REFUSED:")


def test_rejects_empty_problem_or_solution(monkeypatch):
    _wire(monkeypatch)
    assert _report(problem="  ").startswith("REFUSED:")


def test_requires_a_real_user_identity(monkeypatch):
    _wire(monkeypatch, viewer=None)
    out = _report()
    assert out.startswith("REFUSED:") and "sign in" in out


def test_refuses_an_unknown_or_invisible_procedure(monkeypatch):
    _wire(monkeypatch, proc=None)
    assert _report().startswith("REFUSED:")


def test_stores_a_private_candidate_claim_linked_to_the_step(monkeypatch):
    captured = _wire(monkeypatch)
    out = json.loads(_report())
    assert out["claim_id"] == "C-9" and out["visibility"] == "private" and out["status"] == "candidate"
    claim = captured["claim"]
    assert claim["visibility"] == "private" and claim["owner_id"] == "user-1"
    assert claim["predicate"] == "fix"
    assert claim["subject"] == "procedure:P-1#step4"
    props = claim["properties"]
    assert props["source"] == "report_discovery" and props["procedure_id"] == "P-1"
    assert props["step_order"] == 4 and props["share"] is False
    assert claim["scope_type"] == "global" and claim["scope_entity_id"] is None


def test_repo_specific_discovery_is_repository_scoped(monkeypatch):
    captured = _wire(monkeypatch)
    _report(repo="github.com/acme/app")
    assert captured["claim"]["scope_type"] == "repository"
    assert captured["claim"]["scope_entity_id"] == "github.com/acme/app"


def test_source_locator_carries_no_local_path(monkeypatch):
    captured = _wire(monkeypatch)
    _report()
    assert captured["source"]["locator"] == "stealth-discovery:P-1:user-1"


def test_source_row_is_private_to_the_reporter(monkeypatch):
    # A shared public source would publicly record who reported on which Procedure.
    captured = _wire(monkeypatch)
    _report()
    assert captured["source"]["visibility"] == "private"
    assert captured["source"]["owner_id"] == "user-1"


def test_secrets_in_proof_are_redacted(monkeypatch):
    captured = _wire(monkeypatch)
    out = json.loads(_report(proof="export GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789"))
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in captured["claim"]["properties"]["proof"]
    assert out["redacted"] is True


def test_report_discovery_is_write_scoped():
    assert srv._TOOL_SCOPES["report_discovery"] == srv._acx.KNOWLEDGE_WRITE


# --------------------------------------------------------------------------
# stealth://procedures/{procedure_id}/claims
# --------------------------------------------------------------------------


def test_procedure_claims_resource_groups_discoveries_preconditions_related(monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope(viewer_id="user-1"))

    async def fake_resolve(pool, procedure_id, access_scope=None):
        return PROC

    async def fake_discoveries(pool, procedure_id, scope):
        assert procedure_id == "P-1"
        return [{"id": "C-9", "properties": {"statement": "[fix] step 4: use new Document"}, "scope_type": "global"}]

    async def fake_pre(pool, row_id, *, scope):
        return [{"id": "C-2", "properties": {"statement": "needs Node >= 18"}}]

    async def fake_related(pool, *, goal, access_scope=None, top_k=10, context=None):
        assert access_scope is not None  # never unrestricted
        return [{"claim_id": "C-2", "statement": "dup of precondition"},
                {"claim_id": "C-5", "statement": "docx 9 renamed Packer", "status": "current",
                 "scope": {"scope_type": "global"}, "version": 1}]

    monkeypatch.setattr(srv, "_resolve_live_procedure", fake_resolve)
    monkeypatch.setattr(res, "_discovery_claims", fake_discoveries)
    monkeypatch.setattr(res._pg, "get_procedure_claims", fake_pre)
    monkeypatch.setattr("app.services.relevant_claims.get_relevant_claims", fake_related)

    md = _run(res.procedure_claims_resource("P-1", _Ctx()))
    lines = [ln for ln in md.splitlines() if ln.startswith("CLAIM|")]
    assert [ln.split("|")[1] for ln in lines] == ["C-9", "C-2", "C-5"]  # contiguous, deduped
    assert [ln.split("|")[3] for ln in lines] == ["discovery", "precondition", "related"]


def test_procedure_claims_resource_not_found(monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope(viewer_id=None))

    async def fake_resolve(pool, procedure_id, access_scope=None):
        from app.services.applicability import ProcedureNotFound
        raise ProcedureNotFound("nope")

    monkeypatch.setattr(srv, "_resolve_live_procedure", fake_resolve)
    assert _run(res.procedure_claims_resource("P-x", _Ctx())) == res._NOT_FOUND
