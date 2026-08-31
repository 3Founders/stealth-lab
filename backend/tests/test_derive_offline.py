"""
DB-free coverage for derive.py's two pool-touching functions
(derive_preconditions, derive_scope) via a FakePool matching
project_state()'s own row shape -- the same technique test_state_offline.py
and test_registry_offline.py already use, not a new pattern. The prior
board entry deferred this as needing "a FakePool matching several real
queries at once," but each function only ever issues ONE project_state()
call, so one FakePool.fetch stub covers both; there was no larger harness
actually required.

test_procedure_extraction.py already proves the no-project_id/no-started_at
early-return branch of derive_preconditions (pool=None, never touched);
this file covers the remaining branch of both functions -- the real
project_state() round trip -- plus two small pure-function sub-branches in
load_bearing_predicates/derive_failure_conditions that no existing test
happened to exercise (a test_run observation that also carries a command
string, and a command_executed observation with a genuine nonzero exit
code).
"""
import asyncio
from datetime import datetime, timezone

from app.services.procedure_extraction.derive import (
    derive_failure_conditions,
    derive_preconditions,
    derive_scope,
    load_bearing_predicates,
    precondition_with_claim,
)
from app.services.procedure_extraction.evidence import ProcedureEvidence

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
SUBJECT = f"project:{PROJECT_ID}"


def _run(coro):
    return asyncio.run(coro)


def _row(row_id, predicate, obj, subject=SUBJECT):
    return {
        "id": row_id,
        "properties": {"subject": subject, "predicate": predicate, "object": obj},
        "t_valid": None,
        "t_invalid": None,
    }


class FakePool:
    def __init__(self, rows=()):
        self._rows = list(rows)
        self.fetch_calls = 0

    async def fetch(self, sql, *params):
        self.fetch_calls += 1
        return self._rows


def _evidence(observations, project_id=PROJECT_ID):
    return ProcedureEvidence(
        goal_text="g", outcome="success", project_id=project_id,
        started_at=datetime.now(timezone.utc), observations=observations,
    )


# --- derive_preconditions: the real project_state() round trip ---

def test_derive_preconditions_keeps_only_load_bearing_claims_from_a_real_query():
    ev = _evidence([{"observation_type": "test_run", "properties": {"passed": True}}])
    pool = FakePool(rows=[
        _row("c1", "has_test_runner", "pytest"),
        _row("c2", "has_dev_server", "vite"),  # not exercised by this episode -- must be dropped
    ])
    preconditions = _run(derive_preconditions(pool, ev))
    assert pool.fetch_calls == 1
    assert [(p.predicate, p.object) for p in preconditions] == [("has_test_runner", "pytest")]


def test_derive_preconditions_empty_when_nothing_survives_the_filter():
    ev = _evidence([])  # no observations -- nothing is load-bearing
    pool = FakePool(rows=[_row("c1", "has_test_runner", "pytest")])
    assert _run(derive_preconditions(pool, ev)) == []


# --- derive_scope: the real project_state() round trip ---

def test_derive_scope_returns_empty_without_ids_and_never_touches_the_pool():
    class PoolThatMustNotBeCalled:
        async def fetch(self, *a, **kw):
            raise AssertionError("must not reach the pool with no project_id/started_at")

    ev = ProcedureEvidence(goal_text="g", outcome="success")
    assert _run(derive_scope(PoolThatMustNotBeCalled(), ev)) == {}


def test_derive_scope_extracts_language_from_a_real_query():
    ev = _evidence([])
    pool = FakePool(rows=[_row("c1", "language", "python")])
    assert _run(derive_scope(pool, ev)) == {"language": ["python"]}
    assert pool.fetch_calls == 1


def test_derive_scope_empty_when_no_language_claim_is_live():
    ev = _evidence([])
    pool = FakePool(rows=[_row("c1", "has_test_runner", "pytest")])
    assert _run(derive_scope(pool, ev)) == {}


# --- small pure-function sub-branches no existing test hit ---

def test_test_run_observations_own_command_also_feeds_the_regex_checks():
    """A test_run observation's command is appended to the same commands
    list command_executed observations feed -- so a test runner invoked
    via a package-manager wrapper (`npm test`) must promote BOTH
    predicates, not just has_test_runner."""
    ev = ProcedureEvidence(
        goal_text="g", outcome="success",
        observations=[
            {"observation_type": "test_run",
             "properties": {"passed": True, "command": "npm test"}},
        ],
    )
    predicates = load_bearing_predicates(ev)
    assert "has_test_runner" in predicates
    assert "package_manager" in predicates


# --- precondition_with_claim: the new optional claim_id shape helper ---

def test_precondition_with_claim_omits_claim_id_entirely_when_not_given():
    """Backward-compatibility proof (task 31, part 1): a caller that
    doesn't pass claim_id gets back the exact legacy
    {subject, predicate, object} shape -- no claim_id key at all, not
    claim_id=None -- byte-identical to what every existing caller
    (derive_preconditions below) already produces."""
    precondition = precondition_with_claim("project:p", "has_test_runner", "pytest")
    assert precondition == {"subject": "project:p", "predicate": "has_test_runner", "object": "pytest"}
    assert "claim_id" not in precondition


def test_precondition_with_claim_includes_claim_id_when_given():
    precondition = precondition_with_claim(
        "project:p", "has_test_runner", "pytest", claim_id="c1",
    )
    assert precondition == {
        "subject": "project:p", "predicate": "has_test_runner", "object": "pytest", "claim_id": "c1",
    }


def test_precondition_with_claim_default_object_is_none():
    precondition = precondition_with_claim("project:p", "language")
    assert precondition == {"subject": "project:p", "predicate": "language", "object": None}


def test_command_executed_with_a_real_nonzero_exit_becomes_a_failure_condition():
    ev = ProcedureEvidence(
        goal_text="g", outcome="success",
        observations=[
            {"observation_type": "command_executed",
             "properties": {"command": "pytest -q", "exit_code": 1}},
        ],
    )
    conditions = derive_failure_conditions(ev)
    assert conditions == [
        "does not apply if `pytest -q` itself is expected to fail",
    ]
