"""
Offline (no real DB, no real network) tests for the 5-tool minimal MCP
surface (search_procedures, get_procedure, check_applicability,
report_execution, submit_procedure) -- server.py.

These are NOT re-testing find_applicable_procedures/check_hard_constraints/
record_execution_outcome/capture_procedure's own decision logic (already
covered by their own test files) -- they test the thin-wrapper behavior
each tool adds: JSON parsing, error->REFUSED translation, and correct
pass-through of the underlying function's result. The real end-to-end
loop (submit -> search -> check -> report x10 -> verified -> approve) is
proven live, against real Postgres and a real embedding API, in
test_five_tool_mcp_surface_live.py -- these tests exist for fast,
DB-free regression coverage of the wrapper layer itself.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.services.applicability import ApplicabilityResult, ProcedureNotFound
from app.services.v0_gate import V0Violation


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool):
        self.request_context = FakeRequestContext(pool)


class FakePool:
    """Answers the one query _resolve_live_procedure issues."""

    def __init__(self, procedure_row=None):
        self._row = procedure_row

    async def fetchrow(self, sql, *params):
        if "FROM procedures WHERE procedure_id" in sql:
            return self._row
        return None


PROC_ID = str(uuid4())
ROW_ID = str(uuid4())
PROCEDURE_ROW = {
    "id": ROW_ID, "procedure_id": PROC_ID, "version": 1,
    "name": "pandas-append-fix", "goal": "fix removed DataFrame.append",
    "verification_state": "candidate", "steps": [],
}


# --------------------------------------------------------------- search


@pytest.mark.asyncio
async def test_search_procedures_refuses_bad_state_json():
    ctx = FakeContext(FakePool())
    result = await srv.search_procedures(task="fix a bug", ctx=ctx, state="not json")
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_search_procedures_returns_real_matches(monkeypatch):
    async def fake_embed_one(self, text, input_type="query"):
        return [0.1] * 1024

    async def fake_find(pool, *, goal_embedding, current_scope, require_verified, limit,
                         invariant_bindings=None):
        return [dict(PROCEDURE_ROW, _similarity_score=0.9)]

    import app.services.embeddings as emb_mod
    monkeypatch.setattr(emb_mod.Embedder, "embed_one", fake_embed_one)
    monkeypatch.setattr("app.services.applicability.find_applicable_procedures", fake_find)

    ctx = FakeContext(FakePool())
    result = json.loads(await srv.search_procedures(task="fix a bug", ctx=ctx))
    assert result == [{
        "id": ROW_ID, "procedure_id": PROC_ID, "version": 1,
        "name": "pandas-append-fix", "goal": "fix removed DataFrame.append",
        "verification_state": "candidate", "similarity": 0.9,
    }]


@pytest.mark.asyncio
async def test_search_procedures_threads_invariant_bindings_through(monkeypatch):
    """Phase 3's remaining real gap: LocalAgentRunner (the actual local
    MCP client) calls search_procedures, not find_best_way -- so the
    invariant_bindings a local probe computes must reach
    find_applicable_procedures from THIS tool too, not just find_best_way's
    server-side repo_path path."""
    async def fake_embed_one(self, text, input_type="query"):
        return [0.1] * 1024

    captured = {}

    async def fake_find(pool, *, goal_embedding, current_scope, require_verified, limit,
                         invariant_bindings=None):
        captured["invariant_bindings"] = invariant_bindings
        return [dict(PROCEDURE_ROW, _similarity_score=0.9)]

    import app.services.embeddings as emb_mod
    monkeypatch.setattr(emb_mod.Embedder, "embed_one", fake_embed_one)
    monkeypatch.setattr("app.services.applicability.find_applicable_procedures", fake_find)

    ctx = FakeContext(FakePool())
    await srv.search_procedures(
        task="migrate pandas append", ctx=ctx,
        invariant_bindings=json.dumps({"pandas_version": 2.1}),
    )
    assert captured["invariant_bindings"] == {"pandas_version": 2.1}


@pytest.mark.asyncio
async def test_search_procedures_refuses_bad_invariant_bindings_json():
    ctx = FakeContext(FakePool())
    result = await srv.search_procedures(
        task="fix a bug", ctx=ctx, invariant_bindings="not json",
    )
    assert result.startswith("REFUSED:")


# --------------------------------------------------------------- get_procedure


@pytest.mark.asyncio
async def test_get_procedure_refuses_unknown_id():
    ctx = FakeContext(FakePool(procedure_row=None))
    result = await srv.get_procedure(procedure_id=str(uuid4()), ctx=ctx)
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_get_procedure_refuses_malformed_id():
    ctx = FakeContext(FakePool(procedure_row=None))
    result = await srv.get_procedure(procedure_id="not-a-uuid", ctx=ctx)
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_get_procedure_returns_the_real_row():
    ctx = FakeContext(FakePool(procedure_row=PROCEDURE_ROW))
    result = json.loads(await srv.get_procedure(procedure_id=PROC_ID, ctx=ctx))
    assert result["name"] == "pandas-append-fix"
    assert result["verification_state"] == "candidate"


# --------------------------------------------------------------- check_applicability


@pytest.mark.asyncio
async def test_check_applicability_refuses_unknown_id():
    ctx = FakeContext(FakePool(procedure_row=None))
    result = await srv.check_applicability(procedure_id=str(uuid4()), ctx=ctx)
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_check_applicability_refuses_bad_state_json():
    ctx = FakeContext(FakePool(procedure_row=PROCEDURE_ROW))
    result = await srv.check_applicability(procedure_id=PROC_ID, ctx=ctx, state="{bad")
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_check_applicability_passes_require_verified_through(monkeypatch):
    seen = {}

    async def fake_check(pool, procedure, *, current_scope, require_verified):
        seen["require_verified"] = require_verified
        return ApplicabilityResult(ROW_ID, True, [], 0.5)

    monkeypatch.setattr("app.services.applicability.check_hard_constraints", fake_check)
    ctx = FakeContext(FakePool(procedure_row=PROCEDURE_ROW))

    result = json.loads(await srv.check_applicability(
        procedure_id=PROC_ID, ctx=ctx, require_verified=False,
    ))
    assert seen["require_verified"] is False
    assert result == {"applicable": True, "failed_constraints": [], "similarity_score": 0.5}


# --------------------------------------------------------------- report_execution


@pytest.mark.asyncio
async def test_report_execution_refuses_unknown_id():
    ctx = FakeContext(FakePool(procedure_row=None))
    result = await srv.report_execution(
        procedure_id=str(uuid4()), success=True, context_key="ctx-a", ctx=ctx,
    )
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_report_execution_refuses_bad_success_criteria_json():
    ctx = FakeContext(FakePool(procedure_row=PROCEDURE_ROW))
    result = await srv.report_execution(
        procedure_id=PROC_ID, success=True, context_key="ctx-a", ctx=ctx,
        success_criteria="not json",
    )
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_report_execution_translates_contract_violations_to_refused(monkeypatch):
    """invariant #13's bare-success refusal (or any other real contract
    violation record_execution_outcome raises) must reach the caller as
    a REFUSED string, not an unhandled exception."""
    async def fake_record(pool, **kwargs):
        raise ValueError("V-EVD: a success outcome requires explicit success criteria")

    monkeypatch.setattr("app.services.procedures.record_execution_outcome", fake_record)
    ctx = FakeContext(FakePool(procedure_row=PROCEDURE_ROW))

    result = await srv.report_execution(
        procedure_id=PROC_ID, success=True, context_key="ctx-a", ctx=ctx,
    )
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_report_execution_returns_the_updated_state(monkeypatch):
    async def fake_record(pool, *, procedure_row_id, success, context_key, steps_used,
                           success_criteria, failure_class):
        assert procedure_row_id == ROW_ID
        assert success is True
        return {
            "verification_state": "verified", "availability": "active",
            "verification_stats": {"successes": 10, "distinct_contexts": 3},
        }

    monkeypatch.setattr("app.services.procedures.record_execution_outcome", fake_record)
    ctx = FakeContext(FakePool(procedure_row=PROCEDURE_ROW))

    result = json.loads(await srv.report_execution(
        procedure_id=PROC_ID, success=True, context_key="ctx-a", ctx=ctx,
        success_criteria=json.dumps({"predicate": "real check"}),
    ))
    assert result["verification_state"] == "verified"
    assert result["verification_stats"]["successes"] == 10


# --------------------------------------------------------------- submit_procedure


@pytest.mark.asyncio
async def test_submit_procedure_refuses_bad_steps_json():
    ctx = FakeContext(FakePool())
    result = await srv.submit_procedure(
        name="x", goal="y", steps_json="not json", ctx=ctx,
    )
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_submit_procedure_refuses_v0_violations(monkeypatch):
    async def fake_embed_one(self, text, input_type="document"):
        return [0.1] * 1024

    async def fake_capture(pool, **kwargs):
        raise V0Violation("V0: provenance is required")

    import app.services.embeddings as emb_mod
    monkeypatch.setattr(emb_mod.Embedder, "embed_one", fake_embed_one)
    monkeypatch.setattr("app.services.procedures.capture_procedure", fake_capture)

    ctx = FakeContext(FakePool())
    result = await srv.submit_procedure(
        name="x", goal="y", steps_json="[]", ctx=ctx, provenance="",
    )
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_submit_procedure_always_computes_a_real_embedding(monkeypatch):
    """REAL BUG this session's own live test found: a procedure captured
    with no embedding can be completely starved out of search results
    once the corpus has any real size. This pins that submit_procedure
    always computes one before calling capture_procedure -- regression
    coverage for a bug that was invisible offline the first time."""
    seen = {}

    async def fake_embed_one(self, text, input_type="query"):
        seen["embed_input_type"] = input_type
        seen["embed_text"] = text
        return [0.42] * 1024

    async def fake_capture(pool, **kwargs):
        seen["embedding_passed"] = kwargs.get("embedding")
        return {"id": ROW_ID, "procedure_id": PROC_ID}

    import app.services.embeddings as emb_mod
    monkeypatch.setattr(emb_mod.Embedder, "embed_one", fake_embed_one)
    monkeypatch.setattr("app.services.procedures.capture_procedure", fake_capture)

    ctx = FakeContext(FakePool())
    await srv.submit_procedure(
        name="pandas-append-fix", goal="fix removed DataFrame.append",
        steps_json=json.dumps([{"order": 0, "goal": "use pd.concat"}]),
        ctx=ctx,
    )
    assert seen["embedding_passed"] == [0.42] * 1024, (
        "submit_procedure must pass a real, non-null embedding to capture_procedure"
    )
    assert seen["embed_input_type"] == "document", (
        "storage-time embedding must use input_type='document', matching "
        "the convention method_library.py's persist_plan() already uses"
    )


# --------------------------------------------------------------- decide_procedure
# The 6th primitive, found missing by this file's own live counterpart
# (test_six_tool_mcp_surface_live.py): a real human sign-off action,
# deliberately orthogonal to verification_state.


@pytest.mark.asyncio
async def test_decide_procedure_rejects_invalid_decision():
    ctx = FakeContext(FakePool(procedure_row=PROCEDURE_ROW))
    result = await srv.decide_procedure(
        procedure_id=PROC_ID, approver_id="reviewer", decision="maybe", ctx=ctx,
    )
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_decide_procedure_refuses_unknown_id():
    ctx = FakeContext(FakePool(procedure_row=None))
    result = await srv.decide_procedure(
        procedure_id=str(uuid4()), approver_id="reviewer", decision="approved", ctx=ctx,
    )
    assert result.startswith("REFUSED:")


@pytest.mark.asyncio
async def test_decide_procedure_approved_calls_the_real_approve_function(monkeypatch):
    calls = []

    async def fake_approve(pool, *, procedure_row_id, approved_by):
        calls.append(("approve", procedure_row_id, approved_by))

    async def fake_reject(pool, *, procedure_row_id, approved_by):
        calls.append(("reject", procedure_row_id, approved_by))

    monkeypatch.setattr("app.services.procedures.approve_procedure", fake_approve)
    monkeypatch.setattr("app.services.procedures.reject_procedure", fake_reject)

    approved_row = dict(PROCEDURE_ROW, approval_status="approved", approved_by="reviewer")
    ctx = FakeContext(FakePool(procedure_row=approved_row))

    result = json.loads(await srv.decide_procedure(
        procedure_id=PROC_ID, approver_id="reviewer", decision="approved", ctx=ctx,
    ))
    assert calls == [("approve", ROW_ID, "reviewer")], (
        "decision='approved' must call approve_procedure(), never reject_procedure()"
    )
    assert result["approval_status"] == "approved"


@pytest.mark.asyncio
async def test_decide_procedure_rejected_calls_the_real_reject_function(monkeypatch):
    calls = []

    async def fake_approve(pool, *, procedure_row_id, approved_by):
        calls.append(("approve", procedure_row_id, approved_by))

    async def fake_reject(pool, *, procedure_row_id, approved_by):
        calls.append(("reject", procedure_row_id, approved_by))

    monkeypatch.setattr("app.services.procedures.approve_procedure", fake_approve)
    monkeypatch.setattr("app.services.procedures.reject_procedure", fake_reject)

    rejected_row = dict(PROCEDURE_ROW, approval_status="rejected", approved_by="reviewer")
    ctx = FakeContext(FakePool(procedure_row=rejected_row))

    result = json.loads(await srv.decide_procedure(
        procedure_id=PROC_ID, approver_id="reviewer", decision="rejected", ctx=ctx,
    ))
    assert calls == [("reject", ROW_ID, "reviewer")], (
        "decision='rejected' must call reject_procedure(), never approve_procedure()"
    )
    assert result["approval_status"] == "rejected"
