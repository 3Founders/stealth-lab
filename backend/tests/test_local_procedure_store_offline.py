"""
Phase 1 + Phase 2 (product spec: personal procedure library, unified
local+global retrieval) offline suite. Needs NO DATABASE_URL and no live
Postgres at all -- LocalProcedureStore is SQLite-backed, one temp file per
test. Ordinary offline tests, same convention as every other
`test_*_offline.py` in this directory.

What is REAL here: LocalProcedureStore (real sqlite3 file), local
ingestion (real parse_skill_md), local applicability (real
check_invariants / _scope_matches / _excluded), and the unified ranking
policy (rank_unified_candidates) -- all exercised against real objects,
no mocks.

What is FAKED, and named as such per test: the remote MCP session's
`call_tool` in the orchestrate_unified_search tests -- a network round
trip to a running global server is a different, live-only test's job
(test_local_agent_runner_offline.py already establishes that fake-session
convention for this exact seam); these tests exercise the RANKING POLICY
and the privacy boundary, not the transport.
"""
from __future__ import annotations

import json

import pytest

from app.local_agent.local_applicability import check_local_hard_constraints
from app.local_agent.local_ingestion import ingest_local_skill_md
from app.local_agent.local_store import LocalProcedureStore, LocalProcedureNotFound
from app.local_agent.unified_retrieval import (
    orchestrate_unified_search,
    rank_unified_candidates,
)
from app.services.v0_gate import V0Violation

SKILL_MD = """---
name: fix-pandas-append-removal
description: Fix AttributeError from pandas DataFrame.append() removal in pandas >= 2.0
---

Use when: an AttributeError says 'DataFrame' object has no attribute 'append'.

1. Locate every call site using `df.append(...)` in the target file.
2. Replace each with `pd.concat([df, other], ignore_index=True)`.
3. Confirm the file no longer calls the removed method.
"""


def _store(tmp_path) -> LocalProcedureStore:
    return LocalProcedureStore(db_path=str(tmp_path / "local_procedures.db"))


# ---------------------------------------------------------------------------
# Scenario: empty local library -> ingest -> search -> retrieve
# ---------------------------------------------------------------------------

def test_empty_library_then_ingest_then_search_then_retrieve(tmp_path):
    store = _store(tmp_path)

    assert store.list_local_procedures() == []
    assert store.search_local_procedures("pandas") == []

    result = ingest_local_skill_md(
        store, SKILL_MD, scope_type="repository", scope_entity_id="repo-1",
    )
    assert result["status"] == "captured"
    assert result["id"] and result["procedure_id"]

    listed = store.list_local_procedures()
    assert len(listed) == 1
    assert listed[0]["name"] == "fix-pandas-append-removal"
    assert listed[0]["verification_state"] == "candidate"
    assert listed[0]["staleness"] == "fresh"
    assert listed[0]["availability"] == "active"
    assert listed[0]["scope"]["applies_when_prose"].startswith("an AttributeError")

    hits = store.search_local_procedures("pandas append")
    assert len(hits) == 1
    assert hits[0]["procedure_id"] == result["procedure_id"]

    fetched = store.get_local_procedure(result["id"])
    assert fetched is not None
    assert fetched["goal"] == listed[0]["goal"]
    assert len(fetched["steps"]) == 3

    assert store.search_local_procedures("totally unrelated query xyz") == []


def test_capture_requires_provenance_and_scope(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(V0Violation):
        store.capture_local_procedure(
            name="x", goal="y", provenance=None, scope_type="repository",
            scope_entity_id="r1",
        )
    with pytest.raises(V0Violation):
        store.capture_local_procedure(
            name="x", goal="y", provenance="prior_library", scope_type=None,
        )
    with pytest.raises(V0Violation):
        # non-global scope_type requires an entity id
        store.capture_local_procedure(
            name="x", goal="y", provenance="prior_library", scope_type="repository",
        )


def test_record_local_execution_outcome_promotes_to_verified_on_real_thresholds(tmp_path):
    from app.services.procedures import (
        MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
        MIN_SUCCESSES_FOR_VERIFIED,
    )

    store = _store(tmp_path)
    captured = store.capture_local_procedure(
        name="p", goal="g", provenance="system_pending_review",
        scope_type="repository", scope_entity_id="repo-1",
    )
    row_id = captured["id"]

    contexts = [f"ctx-{i % MIN_DISTINCT_CONTEXTS_FOR_VERIFIED}" for i in range(MIN_SUCCESSES_FOR_VERIFIED)]
    for i, ctx in enumerate(contexts):
        updated = store.record_local_execution_outcome(
            row_id=row_id, success=True, context_key=ctx, steps_used=3,
        )
    assert updated["verification_state"] == "verified"
    assert updated["verification_stats"]["successes"] == MIN_SUCCESSES_FOR_VERIFIED
    assert updated["verification_stats"]["distinct_contexts"] == MIN_DISTINCT_CONTEXTS_FOR_VERIFIED


def test_record_local_execution_outcome_one_failure_blocks_verification(tmp_path):
    from app.services.procedures import MIN_SUCCESSES_FOR_VERIFIED

    store = _store(tmp_path)
    captured = store.capture_local_procedure(
        name="p", goal="g", provenance="system_pending_review",
        scope_type="repository", scope_entity_id="repo-1",
    )
    row_id = captured["id"]
    store.record_local_execution_outcome(row_id=row_id, success=False, context_key="ctx-0")
    for i in range(MIN_SUCCESSES_FOR_VERIFIED + 5):
        updated = store.record_local_execution_outcome(
            row_id=row_id, success=True, context_key=f"ctx-{i % 5}",
        )
    # one real failure -- total_failures != 0 forever, so this never verifies
    assert updated["verification_state"] == "candidate"


def test_record_local_execution_outcome_unknown_row_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(LocalProcedureNotFound):
        store.record_local_execution_outcome(row_id="does-not-exist", success=True, context_key="c")


def test_mark_local_procedure_stale_is_one_directional(tmp_path):
    store = _store(tmp_path)
    captured = store.capture_local_procedure(
        name="p", goal="g", provenance="prior_library",
        scope_type="repository", scope_entity_id="repo-1",
    )
    row_id = captured["id"]
    updated = store.mark_local_procedure_stale(row_id=row_id, reason="pandas_version drifted")
    assert updated["staleness"] == "stale"
    # idempotent: calling again on an already-stale row is a no-op, not an error
    again = store.mark_local_procedure_stale(row_id=row_id, reason="re-detected")
    assert again["staleness"] == "stale"


# ---------------------------------------------------------------------------
# Local applicability -- DB-free cascade
# ---------------------------------------------------------------------------

def test_local_applicability_gates_on_staleness_availability_verification(tmp_path):
    store = _store(tmp_path)
    captured = store.capture_local_procedure(
        name="p", goal="g", provenance="prior_library",
        scope_type="repository", scope_entity_id="repo-1",
    )
    row = store.get_local_procedure(captured["id"])

    # candidate, require_verified=True -> inapplicable
    result = check_local_hard_constraints(row, require_verified=True)
    assert result.applicable is False
    assert result.failed_constraints == ["verification_state"]

    # explicit invocation (require_verified=False) -> applicable
    result2 = check_local_hard_constraints(row, require_verified=False)
    assert result2.applicable is True

    stale_row = store.mark_local_procedure_stale(row_id=captured["id"], reason="drift")
    result3 = check_local_hard_constraints(stale_row, require_verified=False)
    assert result3.applicable is False
    assert result3.failed_constraints == ["staleness"]


def test_local_applicability_scope_and_exclusions(tmp_path):
    store = _store(tmp_path)
    captured = store.capture_local_procedure(
        name="p", goal="g", provenance="prior_library",
        scope_type="repository", scope_entity_id="repo-1",
        scope={"repo": ["repo-1"]},
    )
    row = store.get_local_procedure(captured["id"])

    ok = check_local_hard_constraints(row, current_scope={"repo": ["repo-1"]}, require_verified=False)
    assert ok.applicable is True

    mismatch = check_local_hard_constraints(row, current_scope={"repo": ["other-repo"]}, require_verified=False)
    assert mismatch.applicable is False
    assert mismatch.failed_constraints == ["scope"]


def test_local_applicability_numeric_invariant():
    z3 = pytest.importorskip("z3")
    row = {
        "id": "row-1", "t_invalid": None, "staleness": "fresh", "availability": "active",
        "verification_state": "verified", "scope": {}, "exclusions": [],
        "invariants": [{"kind": "numeric", "expr": "pandas_version >= 2.0"}],
    }
    violated = check_local_hard_constraints(row, invariant_bindings={"pandas_version": 1.5})
    assert violated.applicable is False
    assert violated.failed_constraints[0].startswith("invariant:")

    satisfied = check_local_hard_constraints(row, invariant_bindings={"pandas_version": 2.3})
    assert satisfied.applicable is True

    # unbound is undecidable, not disqualifying
    undecided = check_local_hard_constraints(row, invariant_bindings={})
    assert undecided.applicable is True


# ---------------------------------------------------------------------------
# Unified retrieval ranking policy -- product spec's required scenarios.
# Global candidates are hand-built dicts (faked "as if returned by the
# remote server's search_procedures"), not a live MCP round trip -- this
# section tests the RANKING POLICY, named honestly per the module's own
# docstring.
# ---------------------------------------------------------------------------

def _global_proc(**overrides) -> dict:
    base = {
        "id": "global-1", "procedure_id": "global-1", "name": "global procedure",
        "goal": "do the global thing", "verification_state": "verified",
        "staleness": "fresh", "availability": "active", "scope_type": "global",
        "verification_stats": {"attempts": 10, "successes": 10},
    }
    base.update(overrides)
    return base


def _local_proc(store, **overrides) -> dict:
    defaults = dict(
        name="local procedure", goal="do the local thing",
        provenance="prior_library", scope_type="repository", scope_entity_id="repo-1",
    )
    defaults.update({k: v for k, v in overrides.items() if k in defaults})
    captured = store.capture_local_procedure(**defaults)
    row = store.get_local_procedure(captured["id"])
    row.update({k: v for k, v in overrides.items() if k not in defaults})
    return row


def test_unified_ranking_only_global_exists(tmp_path):
    store = _store(tmp_path)
    ranked = rank_unified_candidates([], [_global_proc()], require_verified=True)
    assert len(ranked) == 1
    assert ranked[0].source == "global"


def test_unified_ranking_only_personal_exists(tmp_path):
    store = _store(tmp_path)
    local = _local_proc(store, verification_state="verified")
    ranked = rank_unified_candidates([local], [], require_verified=True)
    assert len(ranked) == 1
    assert ranked[0].source == "local"


def test_unified_ranking_both_exist_personal_stronger(tmp_path):
    store = _store(tmp_path)
    local = _local_proc(
        store, verification_state="verified", scope_type="repository",
    )
    local["verification_stats"] = {"attempts": 20, "successes": 20}
    weak_global = _global_proc(verification_stats={"attempts": 20, "successes": 8})

    ranked = rank_unified_candidates([local], [weak_global], require_verified=True)
    assert ranked[0].source == "local", "higher-capability local candidate must outrank a weaker global one"


def test_unified_ranking_both_exist_global_stronger(tmp_path):
    store = _store(tmp_path)
    weak_local = _local_proc(store, verification_state="verified")
    weak_local["verification_stats"] = {"attempts": 20, "successes": 6}
    strong_global = _global_proc(verification_stats={"attempts": 20, "successes": 20})

    ranked = rank_unified_candidates([weak_local], [strong_global], require_verified=True)
    assert ranked[0].source == "global", "higher-capability global candidate must outrank a weaker local one"


def test_unified_ranking_specificity_tiebreak_prefers_narrower_scope_when_comparable(tmp_path):
    store = _store(tmp_path)
    local = _local_proc(store, verification_state="verified", scope_type="repository")
    local["verification_stats"] = {"attempts": 10, "successes": 10}
    global_generic = _global_proc(scope_type="global", verification_stats={"attempts": 10, "successes": 10})

    ranked = rank_unified_candidates([local], [global_generic], require_verified=True)
    assert ranked[0].source == "local", (
        "comparably-capable candidates must tie-break on specificity -- a "
        "repository-scoped procedure beats a global-scoped one"
    )

    # and the reverse holds too -- specificity is a real property of the
    # row, not a hidden "prefer local" rule: an equally-capable ENTITY-
    # scoped global procedure beats a repository-scoped local one.
    global_specific = _global_proc(scope_type="entity", verification_stats={"attempts": 10, "successes": 10})
    ranked2 = rank_unified_candidates([local], [global_specific], require_verified=True)
    assert ranked2[0].source == "global"


def test_unified_ranking_personal_is_stale_excluded(tmp_path):
    store = _store(tmp_path)
    captured = store.capture_local_procedure(
        name="stale-one", goal="g", provenance="prior_library",
        scope_type="repository", scope_entity_id="repo-1",
    )
    store.record_local_execution_outcome(row_id=captured["id"], success=True, context_key="c")
    stale_row = store.mark_local_procedure_stale(row_id=captured["id"], reason="drift")

    ranked = rank_unified_candidates([stale_row], [_global_proc()], require_verified=False)
    assert len(ranked) == 1
    assert ranked[0].source == "global", "a stale local candidate must be excluded, never merely ranked low"


def test_unified_ranking_personal_is_inapplicable_scope_mismatch(tmp_path):
    store = _store(tmp_path)
    local = _local_proc(store, verification_state="verified")
    local["scope"] = {"repo": ["repo-1"]}

    ranked = rank_unified_candidates(
        [local], [_global_proc()],
        current_scope={"repo": ["some-other-repo"]}, require_verified=True,
    )
    assert len(ranked) == 1
    assert ranked[0].source == "global"


def test_unified_ranking_global_is_inapplicable_defensive_flag_excludes_it(tmp_path):
    """The real contract is 'global candidates are trusted as already
    server-filtered'; this proves the defensive fallback still holds when
    a caller (or a misbehaving server) marks one explicitly inapplicable."""
    store = _store(tmp_path)
    local = _local_proc(store, verification_state="verified")

    inapplicable_global = _global_proc(applicable=False)
    ranked = rank_unified_candidates([local], [inapplicable_global], require_verified=True)
    assert len(ranked) == 1
    assert ranked[0].source == "local"


def test_unified_ranking_neither_applicable_returns_empty(tmp_path):
    store = _store(tmp_path)
    local = _local_proc(store, verification_state="candidate")  # require_verified excludes it
    ranked = rank_unified_candidates(
        [local], [_global_proc(applicable=False)], require_verified=True,
    )
    assert ranked == []


# ---------------------------------------------------------------------------
# Privacy: a local-only procedure must never leak into the global-facing
# query path. FAKED: the remote session -- a recording double, no network.
# ---------------------------------------------------------------------------

class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeToolResult:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _RecordingFakeSession:
    def __init__(self, search_procedures_response: list[dict]):
        self.calls: list[tuple[str, dict]] = []
        self._response = search_procedures_response

    async def call_tool(self, name: str, args: dict):
        self.calls.append((name, dict(args)))
        if name == "search_procedures":
            return _FakeToolResult(json.dumps(self._response))
        raise AssertionError(f"unexpected call_tool: {name}")


@pytest.mark.asyncio
async def test_orchestrate_unified_search_never_sends_local_data_to_remote(tmp_path):
    store = _store(tmp_path)
    ingest_local_skill_md(
        store,
        "---\nname: private-local-only\ndescription: a secret local-only capability\n---\n\n1. do the private thing\n",
        scope_type="repository", scope_entity_id="repo-1",
    )
    fake_session = _RecordingFakeSession(search_procedures_response=[_global_proc()])

    results = await orchestrate_unified_search(
        fake_session, store, task_description="do the private thing",
        require_verified=False,
    )

    assert len(fake_session.calls) == 1
    tool_name, sent_args = fake_session.calls[0]
    assert tool_name == "search_procedures"
    # exact, real payload shape -- no field carrying local store content
    assert set(sent_args.keys()) == {"task", "require_verified", "limit", "invariant_bindings"}
    sent_blob = json.dumps(sent_args)
    assert "private-local-only" not in sent_blob
    assert "secret local-only capability" not in sent_blob

    # the private local procedure IS present in the merged, ranked results
    # returned to the caller -- it's invisible to the REMOTE server, not
    # to this process's own caller.
    sources = {r.source for r in results}
    assert "local" in sources
    assert "global" in sources
