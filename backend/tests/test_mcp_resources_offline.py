"""
Offline (no DB, no network) tests for the additive MCP Resource surface
(backend/app/mcp_server/resources.py).

These do NOT re-test the underlying service functions (product_model /
procedure_graph_api / claim_graph_api / implementation_registry /
durable_resume each have their own suites). They prove the RESOURCE-LAYER
contract:
  * all 8 canonical URIs register as templates on the server;
  * each handler dispatches to the real underlying service function with a
    visibility scope (never AccessScope.unrestricted());
  * an unknown / out-of-scope id yields a clean "# Not found" body, not an
    exception;
  * a `candidate` / unverified object is rendered with that status, never
    as "verified";
  * the 29 existing @server.tool() functions still register.

Same import-time os.environ guard as test_mcp_check_procedure_offline.py:
importing app.mcp_server.server runs a module-level load_dotenv().
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

_ENV_BEFORE = dict(os.environ)

import pytest

import app.mcp_server.server as srv
import app.mcp_server.resources as res
from app.services.access import AccessScope

for _k in set(os.environ) - set(_ENV_BEFORE):
    del os.environ[_k]
for _k, _v in _ENV_BEFORE.items():
    if os.environ.get(_k) != _v:
        os.environ[_k] = _v


# --------------------------------------------------------------------------
class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = FakeRequestContext(pool)


def _run(coro):
    return asyncio.run(coro)


PROC_HANDLE = str(uuid4())
PROC_ROW_ID = str(uuid4())


@pytest.fixture(autouse=True)
def _anon_scope(monkeypatch):
    """Pin the caller scope to a deterministic, non-unrestricted value and
    record what the resource passes down."""
    seen = {}
    real_anon = AccessScope.anonymous()

    def fake_caller_scope():
        seen["scope"] = real_anon
        return real_anon

    monkeypatch.setattr(srv, "_caller_access_scope", fake_caller_scope)
    return seen


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------
def _templates():
    rm = srv.server._resource_manager
    tmpl = getattr(rm, "_templates", None) or getattr(rm, "templates", None) or {}
    return set(tmpl.keys())


def test_all_eight_resource_uris_registered():
    assert _templates() == {
        "stealth://procedures/{procedure_id}",
        "stealth://problems/{problem_id}",
        "stealth://problems/{problem_id}/solutions",
        "stealth://claims/{claim_id}",
        "stealth://evaluations/{evaluation_id}",
        "stealth://implementations/{implementation_id}",
        "stealth://tasks/{task_node_id}/implementations",
        "stealth://runs/{run_id}",
    }


def test_existing_29_tools_still_registered():
    assert len(srv.server._tool_manager.list_tools()) == 29


# --------------------------------------------------------------------------
# procedure resource
# --------------------------------------------------------------------------
def test_procedure_resource_dispatches_with_scope_and_renders_candidate(monkeypatch, _anon_scope):
    captured = {}

    async def fake_resolve(pool, procedure_id):
        return {"id": PROC_ROW_ID, "procedure_id": PROC_HANDLE}

    async def fake_detail(pool, procedure_row_id, *, scope):
        captured["scope"] = scope
        captured["row_id"] = procedure_row_id
        return {
            "id": PROC_ROW_ID, "procedure_id": PROC_HANDLE, "version": 1,
            "name": "pandas-append-fix", "display_name": "Pandas append fix",
            "goal": "fix removed DataFrame.append",
            "display_description": "Replace DataFrame.append with pd.concat",
            "applicability_summary": "when a repo pins pandas>=2.0",
            "failure_modes": [], "steps": [{"goal": "use pd.concat"}],
            "preconditions": [], "invariants": [],
            "verification_state": "candidate", "approval_status": None,
            "staleness": "fresh", "availability": "active",
            "evidence_summary": {"total": 0, "success_count": 0, "failure_count": 0},
            "claims": [], "implementation_kinds": [], "provenance": "prior_library",
        }

    monkeypatch.setattr(srv, "_resolve_live_procedure", fake_resolve)
    monkeypatch.setattr(res._pg, "get_procedure_detail", fake_detail)

    md = _run(res.procedure_resource(PROC_HANDLE, FakeContext("pool")))

    assert captured["row_id"] == PROC_ROW_ID
    assert isinstance(captured["scope"], AccessScope)
    # the resource must pass the caller scope from _caller_access_scope(),
    # not a fresh AccessScope.unrestricted()
    assert captured["scope"] is _anon_scope["scope"]
    assert captured["scope"] != AccessScope.unrestricted()
    # candidate must be surfaced as candidate, never "verified"
    assert "Verification state:** candidate" in md
    assert "verified" not in md.lower()
    assert "Pandas append fix" in md


def test_procedure_resource_unknown_id_is_clean_not_found(monkeypatch, _anon_scope):
    from app.services.applicability import ProcedureNotFound

    async def fake_resolve(pool, procedure_id):
        raise ProcedureNotFound("no live procedure")

    monkeypatch.setattr(srv, "_resolve_live_procedure", fake_resolve)
    md = _run(res.procedure_resource(str(uuid4()), FakeContext("pool")))
    assert md.startswith("# Not found")


def test_procedure_resource_invisible_row_is_clean_not_found(monkeypatch, _anon_scope):
    async def fake_resolve(pool, procedure_id):
        return {"id": PROC_ROW_ID, "procedure_id": PROC_HANDLE}

    async def fake_detail(pool, procedure_row_id, *, scope):
        return None  # visible to nobody in this scope

    monkeypatch.setattr(srv, "_resolve_live_procedure", fake_resolve)
    monkeypatch.setattr(res._pg, "get_procedure_detail", fake_detail)
    md = _run(res.procedure_resource(PROC_HANDLE, FakeContext("pool")))
    assert md.startswith("# Not found")


# --------------------------------------------------------------------------
# problem / solutions resources
# --------------------------------------------------------------------------
def test_problem_resource_dispatches_with_scope(monkeypatch, _anon_scope):
    seen = {}

    async def fake_get_problem(pool, pid, *, scope, **kw):
        seen["scope"] = scope
        return {"id": pid, "title": "Beat SWE-bench-lite", "status": "open",
                "objective": "raise pass@1", "description": "..."}

    async def fake_benchmarks(pool, pid, *, scope, **kw):
        return []

    async def fake_solutions(pool, pid, *, scope, **kw):
        return []

    async def fake_leaderboard(pool, pid, *, scope, **kw):
        return {"current_best": [], "leaderboard": []}

    monkeypatch.setattr(res._pm, "get_problem", fake_get_problem)
    monkeypatch.setattr(res._pm, "list_problem_benchmarks", fake_benchmarks)
    monkeypatch.setattr(res._pm, "list_problem_solutions", fake_solutions)
    monkeypatch.setattr(res._pm, "problem_leaderboard", fake_leaderboard)

    md = _run(res.problem_resource("prob-1", FakeContext("pool")))
    assert isinstance(seen["scope"], AccessScope)
    assert "Beat SWE-bench-lite" in md
    assert "no verified solution yet" in md


def test_problem_resource_unknown_id_not_found(monkeypatch, _anon_scope):
    async def fake_get_problem(pool, pid, *, scope, **kw):
        return None

    monkeypatch.setattr(res._pm, "get_problem", fake_get_problem)
    md = _run(res.problem_resource("nope", FakeContext("pool")))
    assert md.startswith("# Not found")


def test_problem_solutions_resource_gates_on_problem_visibility(monkeypatch, _anon_scope):
    async def fake_get_problem(pool, pid, *, scope, **kw):
        return None

    called = {"list": False}

    async def fake_list(pool, pid, *, scope, **kw):
        called["list"] = True
        return []

    monkeypatch.setattr(res._pm, "get_problem", fake_get_problem)
    monkeypatch.setattr(res._pm, "list_problem_solutions", fake_list)
    md = _run(res.problem_solutions_resource("prob-x", FakeContext("pool")))
    assert md.startswith("# Not found")
    assert called["list"] is False  # never lists solutions of an invisible problem


# --------------------------------------------------------------------------
# claim / evaluation / implementation / task-impls / run
# --------------------------------------------------------------------------
def test_claim_resource_dispatches_and_not_found(monkeypatch, _anon_scope):
    seen = {}

    async def fake_get_claim(pool, cid, *, scope):
        seen["scope"] = scope
        return None

    monkeypatch.setattr(res._claims, "get_claim", fake_get_claim)
    md = _run(res.claim_resource("claim-1", FakeContext("pool")))
    assert isinstance(seen["scope"], AccessScope)
    assert md.startswith("# Not found")


def test_claim_resource_renders_truth_state(monkeypatch, _anon_scope):
    async def fake_get_claim(pool, cid, *, scope):
        return {"id": cid, "statement": "pandas 2.0 removed DataFrame.append",
                "subject": "pandas", "predicate": "removed", "object": "DataFrame.append",
                "truth_state": "IN", "epistemic_status": "supported"}

    async def fake_ev(pool, cid, *, scope):
        return []

    monkeypatch.setattr(res._claims, "get_claim", fake_get_claim)
    monkeypatch.setattr(res._claims, "get_claim_evidence_api", fake_ev)
    md = _run(res.claim_resource("claim-1", FakeContext("pool")))
    assert "Truth state:** IN" in md
    assert "supported" in md


def test_evaluation_resource_dispatches_and_not_found(monkeypatch, _anon_scope):
    seen = {}

    async def fake_get_eval(pool, eid, *, scope, **kw):
        seen["scope"] = scope
        return None

    monkeypatch.setattr(res._pm, "get_evaluation", fake_get_eval)
    md = _run(res.evaluation_resource("eval-1", FakeContext("pool")))
    assert isinstance(seen["scope"], AccessScope)
    assert md.startswith("# Not found")


def test_implementation_resource_uses_secret_free_descriptor(monkeypatch, _anon_scope):
    seen = {}

    async def fake_desc(pool, iid, *, scope):
        seen["scope"] = scope
        return {"implementation_id": iid, "kind": "mcp_tool", "provider": "acme",
                "version": 2, "status": "active", "verification_status": "unverified",
                "protocol": "mcp"}

    monkeypatch.setattr(res._impl, "get_descriptor", fake_desc)
    md = _run(res.implementation_resource("impl-1", FakeContext("pool")))
    assert isinstance(seen["scope"], AccessScope)
    assert "sanitised to references only" in md
    assert "unverified" in md


def test_task_implementations_resource_empty(monkeypatch, _anon_scope):
    seen = {}

    async def fake_for_task(pool, tid, *, scope, **kw):
        seen["scope"] = scope
        return []

    monkeypatch.setattr(res._impl, "get_for_task", fake_for_task)
    md = _run(res.task_implementations_resource("node-1", FakeContext("pool")))
    assert isinstance(seen["scope"], AccessScope)
    assert "no active implementations linked" in md


def test_run_resource_not_found_and_render(monkeypatch, _anon_scope):
    async def fake_status_none(pool, rid):
        return None

    monkeypatch.setattr(res._dres, "run_status_by_id", fake_status_none)
    md = _run(res.run_resource("run-1", FakeContext("pool")))
    assert md.startswith("# Not found")

    async def fake_status(pool, rid):
        return {"status": "failed"}

    async def fake_hist(pool, rid):
        return [{"node_order": 0, "status": "failed", "attempt_count": 3}]

    monkeypatch.setattr(res._dres, "run_status_by_id", fake_status)
    monkeypatch.setattr(res._dres, "node_history_by_id", fake_hist)
    md = _run(res.run_resource("run-1", FakeContext("pool")))
    assert "Status:** failed" in md
    assert "Per-node history" in md


# --------------------------------------------------------------------------
# Phase 5 seam
# --------------------------------------------------------------------------
def test_resolve_node_resources_is_best_effort(monkeypatch):
    """Every leg fails -> empty structure, no exception."""
    async def boom(*a, **k):
        raise RuntimeError("leg down")

    monkeypatch.setattr("app.services.applicability.find_applicable_procedures", boom, raising=False)
    monkeypatch.setattr(res._impl, "get_for_task", boom)
    out = _run(res.resolve_node_resources(
        "pool", {"id": "n1", "goal": "do X"}, {}, scope=AccessScope.anonymous(),
    ))
    assert out == {"procedures": [], "implementations": [], "claims": []}


# --------------------------------------------------------------------------
# real SDK dispatch: server.read_resource(uri) -- proves URI-template
# matching + param extraction + Context injection, not just a direct call
# --------------------------------------------------------------------------
from mcp.server.mcpserver import Context as _MCPContext


def _sdk_ctx(pool="pool"):
    c = _MCPContext(mcp_server=srv.server)
    object.__setattr__(c, "_request_context", FakeRequestContext(pool))
    return c


def test_read_resource_dispatches_template_and_injects_context(monkeypatch, _anon_scope):
    async def fake_resolve(pool, procedure_id):
        return {"id": PROC_ROW_ID, "procedure_id": PROC_HANDLE}

    async def fake_detail(pool, row_id, *, scope):
        return {
            "id": row_id, "procedure_id": PROC_HANDLE, "version": 1, "name": "n",
            "display_name": "Demo proc", "goal": "g", "display_description": "does g",
            "applicability_summary": "when X", "failure_modes": [],
            "steps": [{"goal": "s1"}], "preconditions": [], "invariants": [],
            "verification_state": "candidate", "approval_status": None,
            "staleness": "fresh", "availability": "active",
            "evidence_summary": {"total": 0}, "claims": [],
            "implementation_kinds": [], "provenance": "prior_library",
        }

    monkeypatch.setattr(srv, "_resolve_live_procedure", fake_resolve)
    monkeypatch.setattr(res._pg, "get_procedure_detail", fake_detail)

    out = _run(srv.server.read_resource(
        f"stealth://procedures/{uuid4()}", _sdk_ctx(),
    ))
    body = list(out)[0].content
    assert body.startswith("# Procedure: Demo proc")
    assert "candidate" in body and "verified" not in body.lower()


def test_read_resource_unknown_uri_raises_not_found():
    from mcp.server.mcpserver.exceptions import ResourceNotFoundError

    with pytest.raises(ResourceNotFoundError):
        _run(srv.server.read_resource("stealth://bogus/x", _sdk_ctx()))


def test_read_resource_not_found_body_is_clean(monkeypatch, _anon_scope):
    async def fake_get_problem(pool, pid, *, scope, **kw):
        return None

    monkeypatch.setattr(res._pm, "get_problem", fake_get_problem)
    out = _run(srv.server.read_resource(
        f"stealth://problems/{uuid4()}", _sdk_ctx(),
    ))
    assert list(out)[0].content.startswith("# Not found")


def test_resolve_node_resources_aggregates_uris(monkeypatch):
    async def fake_find(pool, *, goal_text, require_verified, limit, access_scope):
        return [{"procedure_id": "p-1", "display_name": "Do X well"}]

    async def fake_for_task(pool, tid, *, scope, **kw):
        return [{"id": "i-1", "kind": "mcp_tool", "provider": "acme", "version": 1}]

    async def fake_resolve(pool, pid):
        return {"id": "row-1"}

    async def fake_claims(pool, row_id, *, scope):
        return [{"id": "c-1", "statement": "X holds"}]

    monkeypatch.setattr("app.services.applicability.find_applicable_procedures", fake_find, raising=False)
    monkeypatch.setattr(res._impl, "get_for_task", fake_for_task)
    monkeypatch.setattr(srv, "_resolve_live_procedure", fake_resolve)
    monkeypatch.setattr(res._pg, "get_procedure_claims", fake_claims)

    out = _run(res.resolve_node_resources(
        "pool", {"id": "n1", "goal": "do X"}, {}, scope=AccessScope.anonymous(),
    ))
    assert out["procedures"] == [{"uri": "stealth://procedures/p-1", "label": "Do X well"}]
    assert out["implementations"] == [
        {"uri": "stealth://implementations/i-1", "label": "mcp_tool/acme v1"}
    ]
    assert out["claims"] == [{"uri": "stealth://claims/c-1", "label": "X holds"}]
