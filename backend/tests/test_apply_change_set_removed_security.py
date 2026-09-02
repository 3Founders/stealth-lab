"""
Post-freeze security hardening (v1-final-2026-09-03.1): the ungated
`apply_change_set` MCP tool was removed.

`apply_change_set` was the ONLY ungated public write to the knowledge
graph. It took an arbitrary caller-supplied change_set JSON string and
called `KnowledgeUpdater(pool).apply(...)` directly -- no approval gate,
no persisted decision, no audit row.

After the removal, graph mutation goes ONLY through two gated paths, each
of which loads a PERSISTED proposal and applies THAT row's stored
change_set (never a caller-supplied one):

  * submit_approval      -> app/api/approval.py::decide
        requires the debate to be PENDING_APPROVAL, applies the
        scorecard's stored change_set, INSERTs an `approvals` audit row.
  * decide_decomposition -> app/api/decompose.py::decide
        requires the decompositions row to be status='proposed', applies
        its stored change_set, UPDATEs status/approver_id/decided_at.

These offline tests prove the removal and the "no second hidden ungated
route" guard. The live-DB counterparts (a real scorecard / decomposition
driven end-to-end, audit trail intact) live in
test_apply_change_set_removed_e2e.py.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
from uuid import UUID, uuid4

import pytest

import app.mcp_server.server as srv
from app.api import approval, decompose
from app.services.authn import Actor, reset_current_actor, set_current_actor

BACKEND_APP = pathlib.Path(srv.__file__).resolve().parents[1]

EXPECTED_29 = sorted(
    [
        "check_applicability", "check_procedure", "compare_solutions",
        "decide_decomposition", "decide_procedure", "decompose_task",
        "detect_conflict_trigger", "find_best_solution", "find_best_way",
        "find_problem", "get_claim_graph", "get_implementation_capability",
        "get_procedure", "inspect_evaluation", "inspect_implementation",
        "inspect_problem", "inspect_run", "list_problem_solutions",
        "list_task_implementations", "propose_synthesis", "report_execution",
        "reproduce_procedure", "resolve_implementation", "resume_execution_run",
        "retrieve_precedent", "retry_run_node", "search_procedures",
        "submit_approval", "submit_procedure",
    ]
)


def _registered_tool_names() -> list[str]:
    return sorted(t.name for t in srv.server._tool_manager.list_tools())


# ---------------------------------------------------------------------
# 1 + 10: the tool surface no longer exposes apply_change_set; total 29;
#          the expected authorized non-write tools still register.
# ---------------------------------------------------------------------


def test_apply_change_set_not_in_registered_tools():
    names = _registered_tool_names()
    assert "apply_change_set" not in names
    assert len(names) == 29
    assert names == EXPECTED_29


def test_tools_list_method_also_omits_apply_change_set():
    """Whatever `tools/list`-equivalent the tool manager exposes must
    agree with list_tools() -- the tool is gone from every enumeration,
    not just one accessor."""
    mgr = srv.server._tool_manager
    seen = set()
    for accessor in ("list_tools",):
        seen.update(t.name for t in getattr(mgr, accessor)())
    # dict-shaped registries, if present on this SDK version
    for attr in ("_tools", "tools"):
        reg = getattr(mgr, attr, None)
        if isinstance(reg, dict):
            seen.update(reg.keys())
    assert "apply_change_set" not in seen
    assert seen == set(EXPECTED_29)


def test_expected_authorized_tools_still_present():
    names = set(_registered_tool_names())
    for expected in (
        "retrieve_precedent", "search_procedures", "get_procedure",
        "check_applicability", "check_procedure", "report_execution",
        "submit_procedure", "decide_procedure", "propose_synthesis",
        "submit_approval", "decide_decomposition", "decompose_task",
        "find_best_way", "get_claim_graph",
    ):
        assert expected in names, expected


# ---------------------------------------------------------------------
# 2: no module-level callable remains.
# ---------------------------------------------------------------------


def test_no_module_level_apply_change_set_callable():
    assert getattr(srv, "apply_change_set", None) is None


def test_server_module_does_not_import_raw_mutation_symbols():
    """server.py must not import KnowledgeUpdater / ChangeApplicationError /
    ChangeSet / the apply_debate_result preflight helpers any more -- their
    only real use was inside the deleted tool."""
    for banned in (
        "KnowledgeUpdater", "ChangeApplicationError",
        "auto_preserve_missing_keys", "preflight_validate",
    ):
        assert getattr(srv, banned, None) is None, banned
    # `ChangeSet` name must not be bound as an importable symbol either
    assert getattr(srv, "ChangeSet", None) is None


# ---------------------------------------------------------------------
# 3: KnowledgeUpdater is imported by EXACTLY approval.py and decompose.py.
#    "no second hidden ungated route" guard -- fails if anyone re-adds a
#    raw mutation import anywhere in backend/app/ (server.py included).
# ---------------------------------------------------------------------


def _modules_importing(symbol_module: str, symbol_name: str) -> set[str]:
    hits: set[str] = set()
    for path in BACKEND_APP.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == symbol_module or mod.endswith("." + symbol_module.split(".")[-1]):
                    if any(alias.name == symbol_name for alias in node.names):
                        hits.add(str(path.relative_to(BACKEND_APP)).replace("\\", "/"))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == symbol_module:
                        hits.add(str(path.relative_to(BACKEND_APP)).replace("\\", "/"))
    return hits


def test_knowledge_updater_imported_only_by_the_two_gated_endpoints():
    hits = _modules_importing("app.services.knowledge_update", "KnowledgeUpdater")
    assert hits == {"api/approval.py", "api/decompose.py"}, (
        f"KnowledgeUpdater must be imported only by the two gated approval "
        f"endpoints; found: {sorted(hits)}"
    )


def test_mcp_server_does_not_import_knowledge_updater():
    server_src = (BACKEND_APP / "mcp_server" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(server_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("knowledge_update"):
            names = [a.name for a in node.names]
            raise AssertionError(f"server.py must not import from knowledge_update: {names}")
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("apply_debate_result"):
            raise AssertionError("server.py must not import apply_debate_result (deleted tool's preflight)")


# ---------------------------------------------------------------------
# Fakes for the two gated endpoints (mirrors
# test_approval_decompose_actor_precedence_offline.py's shapes).
# ---------------------------------------------------------------------

SCORECARD_ID = UUID("00000000-0000-4000-8000-0000000005a1")
DEBATE_ID = UUID("00000000-0000-4000-8000-0000000006a1")
CANDIDATE_ID = UUID("00000000-0000-4000-8000-0000000007a1")
DECOMP_ID = UUID("00000000-0000-4000-8000-0000000008a1")

STORED_EDGE_SOURCE = str(uuid4())
STORED_EDGE_TARGET = str(uuid4())
STORED_CHANGE_SET = {
    "ops": [
        {
            "op_type": "create_edge",
            "edge_type": "REQUIRES",
            "source_id": STORED_EDGE_SOURCE,
            "source_table": "task_nodes",
            "target_id": STORED_EDGE_TARGET,
            "target_table": "task_nodes",
        }
    ]
}


class _ApprovalPool:
    def __init__(self, *, scorecard_row):
        self._scorecard_row = scorecard_row
        self.fetchrow_calls: list[tuple] = []

    async def fetchrow(self, sql: str, *params):
        self.fetchrow_calls.append((sql, params))
        if "FROM scorecards" in sql:
            return self._scorecard_row
        if "INSERT INTO approvals" in sql:
            return {"id": UUID("00000000-0000-4000-8000-0000000009a1")}
        raise AssertionError(f"unexpected fetchrow: {sql}")


class _StateMachine:
    def __init__(self, state: str):
        self._state = state
        self.transition_calls: list[dict] = []

    async def current_state(self, debate_id):
        return self._state

    async def transition(self, debate_id, to_state, *, reason, actor):
        self.transition_calls.append({"actor": actor, "to_state": to_state})


class _RecordingUpdater:
    def __init__(self, *, raises: Exception | None = None):
        self._raises = raises
        self.apply_calls: list = []
        self.apply_generated_calls: list = []

    async def apply(self, change_set, approver_id, at=None):
        self.apply_calls.append({"change_set": change_set, "approver_id": approver_id})
        if self._raises is not None:
            raise self._raises
        return [{"op": op.op_type} for op in change_set.ops]

    async def apply_generated(self, change_set, approver_id):
        self.apply_generated_calls.append({"change_set": change_set, "approver_id": approver_id})
        if self._raises is not None:
            raise self._raises
        return {"applied": [{"op": op.op_type} for op in change_set.ops], "refs": {}}


def _scorecard_row(**over):
    base = {
        "id": SCORECARD_ID, "debate_id": DEBATE_ID, "candidate_id": CANDIDATE_ID,
        "layer1_passed": True, "blast_radius": 1, "reversible": True,
        "recommendation": "approve", "summary": "s", "change_set": STORED_CHANGE_SET,
        "supporters": [],
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clean_actor():
    yield
    reset_current_actor(set_current_actor(None))


# ---------------------------------------------------------------------
# 4: approval.decide does not mutate for a missing / non-PENDING_APPROVAL
#    scorecard.
# ---------------------------------------------------------------------


def test_approval_decide_missing_scorecard_does_not_mutate(monkeypatch):
    updater = _RecordingUpdater()
    machine = _StateMachine("PENDING_APPROVAL")
    monkeypatch.setattr(approval, "KnowledgeUpdater", lambda pool: updater)
    monkeypatch.setattr(approval, "DebateStateMachine", lambda pool: machine)

    pool = _ApprovalPool(scorecard_row=None)
    body = approval.ApprovalRequest(approver_id="anyone", decision="approved")

    with pytest.raises(approval.HTTPException) as exc:
        asyncio.run(approval.decide(SCORECARD_ID, body, pool=pool))
    assert exc.value.status_code == 404
    assert updater.apply_calls == []


def test_approval_decide_not_pending_approval_does_not_mutate(monkeypatch):
    updater = _RecordingUpdater()
    machine = _StateMachine("APPROVED")  # already decided -> illegal to approve again
    monkeypatch.setattr(approval, "KnowledgeUpdater", lambda pool: updater)
    monkeypatch.setattr(approval, "DebateStateMachine", lambda pool: machine)

    pool = _ApprovalPool(scorecard_row=_scorecard_row())
    body = approval.ApprovalRequest(approver_id="anyone", decision="approved")

    with pytest.raises(approval.HTTPException) as exc:
        asyncio.run(approval.decide(SCORECARD_ID, body, pool=pool))
    assert exc.value.status_code == 409
    assert updater.apply_calls == [], "no write may happen when the debate is not PENDING_APPROVAL"
    assert machine.transition_calls == []
    # no approvals audit row inserted
    assert not any("INSERT INTO approvals" in sql for sql, _ in pool.fetchrow_calls)


# ---------------------------------------------------------------------
# 5: decompose.decide -- status != 'proposed' -> 409, nothing applied;
#    the request model exposes NO ops/change_set field.
# ---------------------------------------------------------------------


class _DecomposePool:
    def __init__(self, *, row):
        self._row = row
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, sql: str, *params):
        return self._row

    async def execute(self, sql: str, *params):
        self.execute_calls.append((sql, params))


def test_decompose_decide_request_model_has_no_ops_or_change_set_field():
    fields = set(decompose.DecideRequest.model_fields)
    assert fields == {"approver_id", "decision"}, fields
    for forbidden in ("ops", "change_set", "change_set_json", "changes"):
        assert forbidden not in fields


def test_decompose_decide_non_proposed_status_is_409_and_applies_nothing(monkeypatch):
    updater = _RecordingUpdater()
    monkeypatch.setattr(decompose, "KnowledgeUpdater", lambda pool: updater)

    pool = _DecomposePool(row={
        "id": DECOMP_ID, "change_set": STORED_CHANGE_SET,
        "status": "approved", "feasible": True,
    })
    body = decompose.DecideRequest(approver_id="anyone", decision="approved")

    with pytest.raises(decompose.HTTPException) as exc:
        asyncio.run(decompose.decide(DECOMP_ID, body, pool=pool))
    assert exc.value.status_code == 409
    assert updater.apply_generated_calls == []
    assert pool.execute_calls == [], "no status update for an already-decided proposal"


def test_decompose_decide_missing_row_is_404(monkeypatch):
    updater = _RecordingUpdater()
    monkeypatch.setattr(decompose, "KnowledgeUpdater", lambda pool: updater)
    pool = _DecomposePool(row=None)
    body = decompose.DecideRequest(approver_id="anyone", decision="approved")
    with pytest.raises(decompose.HTTPException) as exc:
        asyncio.run(decompose.decide(DECOMP_ID, body, pool=pool))
    assert exc.value.status_code == 404
    assert updater.apply_generated_calls == []


# ---------------------------------------------------------------------
# 6: the gated paths apply the STORED change_set, never a caller-supplied
#    one. Proven by construction (ApprovalRequest carries no change_set)
#    plus: the ChangeSet handed to the updater carries exactly the ops
#    from the persisted row.
# ---------------------------------------------------------------------


def test_approval_request_model_cannot_carry_ops():
    fields = set(approval.ApprovalRequest.model_fields)
    assert fields == {"approver_id", "approver_role", "decision", "note"}, fields
    for forbidden in ("ops", "change_set", "change_set_json", "changes"):
        assert forbidden not in fields


def test_approval_decide_applies_exactly_the_stored_change_set(monkeypatch):
    updater = _RecordingUpdater()
    machine = _StateMachine("PENDING_APPROVAL")
    monkeypatch.setattr(approval, "KnowledgeUpdater", lambda pool: updater)
    monkeypatch.setattr(approval, "DebateStateMachine", lambda pool: machine)

    pool = _ApprovalPool(scorecard_row=_scorecard_row())
    # A caller trying to smuggle extra ops via unknown kwargs gets them
    # dropped on the floor -- the model has no field to hold them, so the
    # value never becomes an attribute and decide() has nothing to read.
    smuggled = {"ops": [{"op_type": "create_edge", "edge_type": "BLOCKS",
                         "source_id": str(uuid4()), "target_id": str(uuid4())}]}
    body = approval.ApprovalRequest(
        approver_id="x", decision="approved", change_set=smuggled,
    )
    assert not hasattr(body, "change_set")
    assert "change_set" not in body.model_dump()

    asyncio.run(approval.decide(SCORECARD_ID, body, pool=pool))

    assert len(updater.apply_calls) == 1
    applied_cs = updater.apply_calls[0]["change_set"]
    got = [(op.op_type, str(op.source_id), str(op.target_id)) for op in applied_cs.ops]
    assert got == [("create_edge", STORED_EDGE_SOURCE, STORED_EDGE_TARGET)], (
        "approval.decide must apply the persisted scorecard's stored change_set verbatim"
    )


def test_decompose_decide_applies_exactly_the_stored_change_set(monkeypatch):
    updater = _RecordingUpdater()
    monkeypatch.setattr(decompose, "KnowledgeUpdater", lambda pool: updater)

    pool = _DecomposePool(row={
        "id": DECOMP_ID, "change_set": STORED_CHANGE_SET,
        "status": "proposed", "feasible": True,
    })
    body = decompose.DecideRequest(approver_id="x", decision="approved")
    asyncio.run(decompose.decide(DECOMP_ID, body, pool=pool))

    assert len(updater.apply_generated_calls) == 1
    applied_cs = updater.apply_generated_calls[0]["change_set"]
    got = [(op.op_type, str(op.source_id), str(op.target_id)) for op in applied_cs.ops]
    assert got == [("create_edge", STORED_EDGE_SOURCE, STORED_EDGE_TARGET)]


# ---------------------------------------------------------------------
# 7: a self-asserted approver_id cannot override a resolved OIDC actor in
#    either gated path.
# ---------------------------------------------------------------------


def test_approval_decide_resolved_actor_beats_spoofed_approver_id(monkeypatch):
    updater = _RecordingUpdater()
    machine = _StateMachine("PENDING_APPROVAL")
    monkeypatch.setattr(approval, "KnowledgeUpdater", lambda pool: updater)
    monkeypatch.setattr(approval, "DebateStateMachine", lambda pool: machine)

    pool = _ApprovalPool(scorecard_row=_scorecard_row())
    body = approval.ApprovalRequest(approver_id="attacker-claims-admin", decision="approved")

    tok = set_current_actor(Actor(subject="real-oidc-user"))
    try:
        asyncio.run(approval.decide(SCORECARD_ID, body, pool=pool))
    finally:
        reset_current_actor(tok)

    assert updater.apply_calls[0]["approver_id"] == "real-oidc-user"
    assert machine.transition_calls[0]["actor"] == "real-oidc-user"
    insert = next(p for sql, p in pool.fetchrow_calls if "INSERT INTO approvals" in sql)
    assert insert[2] == "real-oidc-user"
    assert "attacker-claims-admin" not in {
        updater.apply_calls[0]["approver_id"], machine.transition_calls[0]["actor"], insert[2],
    }


def test_decompose_decide_resolved_actor_beats_spoofed_approver_id(monkeypatch):
    updater = _RecordingUpdater()
    monkeypatch.setattr(decompose, "KnowledgeUpdater", lambda pool: updater)

    pool = _DecomposePool(row={
        "id": DECOMP_ID, "change_set": STORED_CHANGE_SET,
        "status": "proposed", "feasible": True,
    })
    body = decompose.DecideRequest(approver_id="attacker-claims-admin", decision="approved")

    tok = set_current_actor(Actor(subject="real-oidc-user"))
    try:
        asyncio.run(decompose.decide(DECOMP_ID, body, pool=pool))
    finally:
        reset_current_actor(tok)

    assert updater.apply_generated_calls[0]["approver_id"] == "real-oidc-user"
    update_sql, update_params = pool.execute_calls[0]
    assert "UPDATE decompositions" in update_sql
    assert update_params[2] == "real-oidc-user"


# ---------------------------------------------------------------------
# 11: a failed mutation surfaces a plain reason and leaks no
#     secret-shaped material.
# ---------------------------------------------------------------------

_SECRET_SHAPES = ("token", "password", "api_key", "apikey", "secret",
                  "postgres://", "postgresql://", "DATABASE_URL", "@db.", "sslmode=")


def _assert_no_secret_shapes(text: str):
    low = text.lower()
    for shape in _SECRET_SHAPES:
        assert shape.lower() not in low, f"error text leaked secret-shaped fragment {shape!r}: {text!r}"


def test_approval_decide_apply_failure_is_plain_and_leaks_nothing(monkeypatch):
    from app.services.knowledge_update import ChangeApplicationError

    err = ChangeApplicationError("edge REQUIRES already open between those two task_nodes")
    updater = _RecordingUpdater(raises=err)
    machine = _StateMachine("PENDING_APPROVAL")
    monkeypatch.setattr(approval, "KnowledgeUpdater", lambda pool: updater)
    monkeypatch.setattr(approval, "DebateStateMachine", lambda pool: machine)

    pool = _ApprovalPool(scorecard_row=_scorecard_row())
    body = approval.ApprovalRequest(approver_id="x", decision="approved")

    with pytest.raises(approval.HTTPException) as exc:
        asyncio.run(approval.decide(SCORECARD_ID, body, pool=pool))
    assert exc.value.status_code == 409
    _assert_no_secret_shapes(str(exc.value.detail))
    # the debate stays undecided; no audit row
    assert machine.transition_calls == []
    assert not any("INSERT INTO approvals" in sql for sql, _ in pool.fetchrow_calls)


def test_decompose_decide_apply_failure_is_plain_and_leaks_nothing(monkeypatch):
    from app.services.knowledge_update import ChangeApplicationError

    updater = _RecordingUpdater(raises=ChangeApplicationError("target node not found"))
    monkeypatch.setattr(decompose, "KnowledgeUpdater", lambda pool: updater)

    pool = _DecomposePool(row={
        "id": DECOMP_ID, "change_set": STORED_CHANGE_SET,
        "status": "proposed", "feasible": True,
    })
    body = decompose.DecideRequest(approver_id="x", decision="approved")

    with pytest.raises(decompose.HTTPException) as exc:
        asyncio.run(decompose.decide(DECOMP_ID, body, pool=pool))
    assert exc.value.status_code == 409
    _assert_no_secret_shapes(str(exc.value.detail))
    assert pool.execute_calls == [], "a failed apply must not mark the proposal decided"


# ---------------------------------------------------------------------
# 12: idempotency -- re-deciding an already-decided proposal does not
#     double-apply.
# ---------------------------------------------------------------------


def test_decompose_redecide_is_409_not_double_apply(monkeypatch):
    updater = _RecordingUpdater()
    monkeypatch.setattr(decompose, "KnowledgeUpdater", lambda pool: updater)

    # second decision arrives after the row is already 'approved'
    pool = _DecomposePool(row={
        "id": DECOMP_ID, "change_set": STORED_CHANGE_SET,
        "status": "approved", "feasible": True,
    })
    body = decompose.DecideRequest(approver_id="x", decision="approved")
    with pytest.raises(decompose.HTTPException) as exc:
        asyncio.run(decompose.decide(DECOMP_ID, body, pool=pool))
    assert exc.value.status_code == 409
    assert updater.apply_generated_calls == []


def test_approval_redecide_blocked_by_pending_approval_guard(monkeypatch):
    updater = _RecordingUpdater()
    machine = _StateMachine("APPROVED")  # first decision already moved it here
    monkeypatch.setattr(approval, "KnowledgeUpdater", lambda pool: updater)
    monkeypatch.setattr(approval, "DebateStateMachine", lambda pool: machine)

    pool = _ApprovalPool(scorecard_row=_scorecard_row())
    body = approval.ApprovalRequest(approver_id="x", decision="approved")
    with pytest.raises(approval.HTTPException) as exc:
        asyncio.run(approval.decide(SCORECARD_ID, body, pool=pool))
    assert exc.value.status_code == 409
    assert updater.apply_calls == []
