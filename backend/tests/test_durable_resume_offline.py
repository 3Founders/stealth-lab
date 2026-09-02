"""
Offline unit coverage for the pure helpers in
`app/execution/durable_resume.py` (final-V1 §2). No DB, no network.
"""
from __future__ import annotations

import pytest

from app.execution import durable_resume as dres
from app.execution.durable_run import DurableRunError


@pytest.mark.parametrize("ev,expected", [
    ("find_best_way_plan_compiler@1", True),
    ("reproduce_procedure_plan_compiler@2", True),
    ("find_best_way_plan_compiler", True),
    ("durable_resume_e2e@1", False),
    ("procedure_graph@3", False),
    ("", False),
    (None, False),
])
def test_is_coding_agent_plan(ev, expected):
    assert dres._is_coding_agent_plan(ev) is expected


def test_authorize_run_mutation_allows_when_identity_unknown_on_either_side():
    dres.authorize_run_mutation(None, None)
    dres.authorize_run_mutation("alice", None)      # caller unresolved -> allow
    dres.authorize_run_mutation(None, "bob")        # run has no creator -> allow
    dres.authorize_run_mutation("alice", "alice")   # match -> allow


def test_authorize_run_mutation_refuses_a_different_resolved_identity():
    with pytest.raises(dres.NotYourRun) as ei:
        dres.authorize_run_mutation("alice", "mallory")
    assert "may not mutate" in str(ei.value)
    assert isinstance(ei.value, DurableRunError)  # so routers' DurableRunError catch still sees it


def test_needs_product_context_shape():
    out = dres._needs_product_context("run-123")
    assert out["status"] == "needs_product_context"
    assert out["run_id"] == "run-123"
    assert "find_best_way" in out["detail"] and "reproduce_procedure" in out["detail"]


def test_resolved_caller_identity_is_none_outside_any_auth_context():
    # No MCP access token, no OIDC actor contextvar populated in a plain
    # test process -> the gate must see "unknown", never a fabricated id.
    assert dres.resolved_caller_identity_or_none() is None
