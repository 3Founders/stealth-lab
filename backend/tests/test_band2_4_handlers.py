"""
Offline proving tests for failure_handlers.py (Band 2.4 completion --
the four §36 mandated-update handlers consuming CORE-A's routing queue).

A recording fake pool proves three things a live DB cannot isolate:

  EXACT UPDATE   -- each handler consumes one queued route and issues
                    precisely its mandate's statements, with SQL CONTENT
                    pinned (filters, casts, vocabulary) and ChangeSet
                    attribution stamped failure_handlers@1;
  IDEMPOTENCY    -- consuming the SAME queue row twice performs exactly
                    one update (the append-only change_sets.reason
                    ledger answers "already fired");
  UNROUTED SAFETY -- failures with NO routing decision are visible to
                    unclassified_backlog() and can never reach a handler:
                    the dispatcher provably never even reads the sweep.

failures.py itself is read-imported, never executed against anything
here beyond its two pure readers -- this lane must not edit it.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path

import pytest

from app.services.applicability import _excluded
from app.services.procedure_extraction.capability import (
    OutcomeRecord,
    CapabilityScope,
    compute_capability,
    capability_trajectory,
)
from app.services.procedure_extraction.failure_handlers import (
    DEPENDENT_CLAIMS_LIMIT,
    DEMOTION_EVIDENCE_TYPES,
    HANDLER_STAMP,
    HANDLED_ROUTES,
    LEDGER_PREFIX,
    REVIEW_CLAIM_STATUS,
    REVALIDATION_CLAIM_STATUS,
    NARROWING_SCOPE_KEY,
    _DERIVED_CLAIMS_SQL,
    capability_for_stream,
    handle_applicability_narrowing,
    handle_capability_demotion,
    handle_dependency_queue,
    handle_requires_review,
    ledger_reason,
    run_failure_handlers,
    unclassified_backlog,
)

FR_ID = "00000000-0000-4000-8000-0000000000f1"
EVIDENCE_ID = "00000000-0000-4000-8000-0000000000e1"
IMPL_ID = "00000000-0000-4000-8000-000000000111"
PROC_ID = "00000000-0000-4000-8000-000000000222"
CLAIM_A = "00000000-0000-4000-8000-00000000000a"
CLAIM_B = "00000000-0000-4000-8000-00000000000b"


# ---------------------------------------------------------------------
# recording fake pool (house style)
# ---------------------------------------------------------------------


class FakePool:
    """Records every statement; answers reads from registered rules
    (first matching fragment wins, matched against whitespace-normalized
    SQL). Writes are always recorded, never answered -- nothing here may
    depend on a write's result."""

    def __init__(self):
        self.rules: list[tuple[str, object]] = []
        self.reads: list[tuple[str, tuple]] = []
        self.writes: list[tuple[str, str, tuple]] = []

    def rule(self, fragment: str, result) -> None:
        self.rules.append((fragment, result))

    def set_ledger(self, applied: bool) -> None:
        """Flip the change_sets.reason ledger answer (idempotency tests)."""
        self.rules = [
            (f, (lambda a, r=applied: r) if f == "WHERE reason = $1" else res)
            for f, res in self.rules
        ]

    def _answer(self, sql: str, args: tuple):
        norm = " ".join(sql.split())
        for fragment, result in self.rules:
            if fragment in norm:
                return result(args) if callable(result) else result
        return None

    def _note(self, op: str, sql: str, args: tuple) -> None:
        norm = " ".join(sql.split())
        head = norm.split()[0].upper() if norm else ""
        if op in ("execute", "executemany") or head in ("INSERT", "UPDATE", "DELETE"):
            self.writes.append((op, norm, args))
        else:
            self.reads.append((norm, args))

    async def fetch(self, sql, *args):
        self._note("fetch", sql, args)
        value = self._answer(sql, args)
        return list(value or [])

    async def fetchrow(self, sql, *args):
        self._note("fetchrow", sql, args)
        return self._answer(sql, args)

    async def fetchval(self, sql, *args):
        self._note("fetchval", sql, args)
        return self._answer(sql, args)

    async def execute(self, sql, *args):
        self._note("execute", sql, args)
        return "UPDATE 1"

    async def executemany(self, sql, args_seq):
        rows = [tuple(a) for a in args_seq]
        self._note("executemany", sql, tuple(rows))
        return


def install_changeset_rules(pool: FakePool) -> None:
    pool.rule("INSERT INTO change_sets", lambda args: uuid.uuid4())


def queue_row(
    route,
    *,
    failure_class="implementation_wrong",
    target_type="implementation",
    target_id=IMPL_ID,
    payload=None,
    fr_id=FR_ID,
    evidence_id=EVIDENCE_ID,
):
    return {
        "id": fr_id,
        "evidence_id": evidence_id,
        "failure_class": failure_class,
        "route": route,
        "payload": payload if payload is not None else {},
        "routed_by": "failure_router@1",
        "t_created": "2026-08-26T00:00:00+00:00",
        "target_type": target_type,
        "target_id": target_id,
        "target_version": 3 if target_type == "procedure" else None,
    }


def changeset_inserts(pool: FakePool) -> list[tuple]:
    return [args for op, _, args in pool.writes if op == "fetchval"]

def changeset_ops(pool: FakePool) -> list[tuple]:
    out = []
    for op, _, args in pool.writes:
        if op == "executemany":
            out.extend(args)
    return out


# ===================================================== wiring / vocab


def test_exactly_the_four_owned_routes_are_handled():
    assert set(HANDLED_ROUTES) == {
        "capability_demotion",
        "applicability_narrowing",
        "dependency_queue",
        "requires_review",
    }
    from app.execution.failures import ROUTE_VALUES
    assert set(HANDLED_ROUTES) <= set(ROUTE_VALUES)


def test_dispatcher_consumes_each_queue_once_and_never_writes_when_empty():
    pool = FakePool()
    applied = asyncio.run(run_failure_handlers(pool))
    assert applied == {route: 0 for route in HANDLED_ROUTES}
    queue_reads = [args for sql, args in pool.reads if "FROM failure_routes fr" in sql]
    assert [args[0] for args in queue_reads] == list(HANDLED_ROUTES)
    assert pool.writes == []


# ============================================ 1. capability_demotion


def _stream_rows(*statuses):
    return [
        {
            "outcome_status": status,
            "context_key": f"ctx-{i}",
            "independence_group": f"g{i}" if i % 2 == 0 else None,
        }
        for i, status in enumerate(statuses)
    ]


def test_demotion_handler_performs_exactly_its_mandated_update():
    pool = FakePool()
    install_changeset_rules(pool)
    stream = _stream_rows("success", "success", "failure")
    pool.rule("FROM evidence WHERE target_type", lambda args: stream)
    pool.rule("WHERE reason = $1", False)

    done = asyncio.run(handle_capability_demotion(pool, queue_row("capability_demotion")))
    assert done is True

    # -- the stream read carries db/24's attempt discipline verbatim ----
    stream_reads = [
        (sql, args) for sql, args in pool.reads if "FROM evidence WHERE target_type" in sql
    ]
    assert len(stream_reads) == 1
    sql, args = stream_reads[0]
    assert "target_type = $1" in sql and "$2::uuid" in sql
    assert "t_invalid IS NULL" in sql
    assert "direction = 'supports'" in sql
    for evidence_type in DEMOTION_EVIDENCE_TYPES:
        assert f"'{evidence_type}'" in sql
    assert "outcome_status IN ('success', 'failure')" in sql
    assert "ORDER BY t_created ASC" in sql
    assert args == ("implementation", IMPL_ID)

    # -- exactly ONE durable verdict record, correctly attributed -------
    inserts = changeset_inserts(pool)
    assert len(inserts) == 1
    author, reason, _, _ = inserts[0]
    assert author == HANDLER_STAMP == "failure_handlers@1"
    assert reason == ledger_reason("capability_demotion", FR_ID)
    assert reason == f"{LEDGER_PREFIX}capability_demotion:{FR_ID}"

    ops = changeset_ops(pool)
    assert len(ops) == 1
    cs_id, operation, target_table, target_id, detail_json = ops[0]
    assert operation == "status_change"
    assert target_table == "evidence"          # no implementations table exists
    assert str(target_id) == EVIDENCE_ID       # ...so the verdict rides the trigger row
    detail = json.loads(detail_json)
    expected = capability_for_stream("implementation", IMPL_ID, stream)
    verdict = detail["capability_verdict"]
    assert verdict["p_lower"] == expected.p_lower
    assert verdict["level"] == expected.level
    assert verdict["routing"] == expected.routing.value
    assert verdict["subject"] == {"target_type": "implementation", "target_id": IMPL_ID}
    assert detail["trigger"]["failure_class"] == "implementation_wrong"


def test_demotion_verdict_actually_demos_the_level():
    """The pure half: appending the classified failure to the stream is
    what lowers P -- recomputation IS the demotion trajectory, and the
    D1 routing tiers follow P down across their named boundary."""
    scope = CapabilityScope(
        task="impl", state_signature="s", environment="e",
        input_signature="i", evaluation_criterion="exit 0",
    )
    ok = OutcomeRecord(success=True, environment="e")
    bad = OutcomeRecord(success=False, environment="e")
    stream = [ok] * 9 + [bad]          # 9/0 Wilson-lower crosses the offer tier...
    trajectory = capability_trajectory(stream, scope)
    assert trajectory[-1] == compute_capability(stream, scope)
    from app.services.procedure_extraction.capability import (
        ROUTE_AUTO_THRESHOLD,
        ROUTE_OFFER_THRESHOLD,
        RoutingDecision,
    )

    pre, post = trajectory[8], trajectory[9]
    assert pre.routing == RoutingDecision.OFFER_AS_CANDIDATE   # P >= offer tier
    assert post.routing == RoutingDecision.REFUSE_REUSE        # ...demoted below it
    assert post.p_lower < pre.p_lower                          # the evidence did it
    assert ROUTE_OFFER_THRESHOLD <= pre.p_lower < ROUTE_AUTO_THRESHOLD


# ====================================== 2. applicability_narrowing


def _narrowing_pool(exclusions=None, *, ledger=False):
    pool = FakePool()
    install_changeset_rules(pool)
    pool.rule("WHERE reason = $1", ledger)
    pool.rule(
        "SELECT exclusions FROM procedures",
        lambda args: {"exclusions": exclusions if exclusions is not None else []},
    )
    return pool


def test_narrowing_edits_rule_detail_for_input_abnormal():
    pool = _narrowing_pool(exclusions=[{"key": "repo", "values": ["orig"]}])
    row = queue_row(
        "applicability_narrowing",
        failure_class="input_abnormal",
        target_type="procedure",
        target_id=PROC_ID,
        payload={"failed_context_key": "ctx-weird"},
    )
    done = asyncio.run(handle_applicability_narrowing(pool, row))
    assert done is True

    updates = [(sql, args) for _, sql, args in pool.writes if sql.startswith("UPDATE procedures")]
    assert len(updates) == 1
    sql, args = updates[0]
    assert "$1::uuid" in sql and "exclusions = $2::jsonb" in sql and "updated_at = now()" in sql
    assert args[0] == PROC_ID
    merged = json.loads(args[1])
    assert merged[0] == {"key": "repo", "values": ["orig"]}          # existing rules untouched
    added = merged[1]
    assert added["key"] == NARROWING_SCOPE_KEY == "context_key"
    assert added["values"] == ["ctx-weird"]
    assert added["_source_route_id"] == FR_ID

    # THE TOOTH: the written rule disqualifies in applicability.py's own
    # cascade -- machine-writable rule detail, consumed unchanged.
    assert _excluded([added], {"context_key": ["ctx-weird"]}) is True
    assert _excluded([added], {"context_key": ["other"]}) is False

    ops = changeset_ops(pool)
    assert len(ops) == 1 and ops[0][1] == "revise" and ops[0][2] == "procedures"
    assert json.loads(ops[0][4])["added_exclusion"]["values"] == ["ctx-weird"]
    author, reason, _, _ = changeset_inserts(pool)[0]
    assert author == HANDLER_STAMP
    assert reason == ledger_reason("applicability_narrowing", FR_ID)


def test_narrowing_skips_non_procedure_targets_and_contextless_payloads():
    pool = _narrowing_pool()
    done = asyncio.run(handle_applicability_narrowing(
        pool, queue_row("applicability_narrowing", target_type="implementation"),
    ))
    assert done is False
    done = asyncio.run(handle_applicability_narrowing(
        pool,
        queue_row("applicability_narrowing", target_type="procedure",
                  target_id=PROC_ID, payload={}),
    ))
    assert done is False
    assert pool.writes == []          # no invented blank bans, no records


# ============================================== 3. dependency_queue


def test_dependency_queue_flags_derived_claims_for_environment_changed():
    pool = FakePool()
    install_changeset_rules(pool)
    pool.rule("WHERE reason = $1", False)
    pool.rule(
        "FROM claim_sources",
        lambda args: [{"claim_id": CLAIM_A}, {"claim_id": CLAIM_B}],
    )
    row = queue_row(
        "dependency_queue",
        failure_class="environment_changed",
        target_type="implementation",
        payload={"failed_context_key": "ctx-drift"},
    )
    done = asyncio.run(handle_dependency_queue(pool, row))
    assert done is True

    reads = [(sql, args) for sql, args in pool.reads if "FROM claim_sources" in sql]
    assert len(reads) == 1
    sql, read_args = reads[0]
    assert sql == " ".join(_DERIVED_CLAIMS_SQL.split()), \
        "the executed dependents resolution must BE the module's pinned SQL"
    assert read_args == ("ctx-drift", DEPENDENT_CLAIMS_LIMIT)
    assert "properties->>'context_key' = $1" in sql
    assert "kn.t_invalid IS NULL" in sql
    assert "IS DISTINCT FROM 'OUT'" in sql, "truth_state OUT claims have nothing left to stale"
    assert "LIMIT $2" in sql

    updates = [(sql, args) for _, sql, args in pool.writes if sql.startswith("UPDATE knowledge_nodes")]
    assert len(updates) == 1
    sql, args = updates[0]
    assert "claim_status = $2" in sql and "properties || $3::jsonb" in sql
    assert "id = ANY($1::uuid[])" in sql
    assert sorted(args[0]) == sorted([CLAIM_A, CLAIM_B])
    assert args[1] == REVALIDATION_CLAIM_STATUS == "stale"
    marker = json.loads(args[2])
    assert marker["revalidation"]["failure_class"] == "environment_changed"
    assert marker["revalidation"]["context_key"] == "ctx-drift"
    assert marker["revalidation"]["route_id"] == FR_ID

    ops = changeset_ops(pool)
    assert [op[2] for op in ops] == ["knowledge_nodes", "knowledge_nodes"]
    assert {op[3] for op in ops} == {CLAIM_A, CLAIM_B}
    assert all(json.loads(op[4])["reason"] == "environment_changed" for op in ops)


def test_dependency_queue_with_no_resolvable_dependents_stays_queued():
    pool = FakePool()
    install_changeset_rules(pool)
    pool.rule("WHERE reason = $1", False)
    pool.rule("FROM claim_sources", [])          # promotion lag: nothing derivable yet
    done = asyncio.run(handle_dependency_queue(
        pool,
        queue_row("dependency_queue", failure_class="environment_changed",
                  payload={"failed_context_key": "ctx-drift"}),
    ))
    assert done is False
    assert pool.writes == []


# ============================================== 4. requires_review


def test_requires_review_stamps_claim_uncertain_for_unclassified_failures():
    pool = FakePool()
    install_changeset_rules(pool)
    pool.rule("WHERE reason = $1", False)
    pool.rule(
        "SELECT claim_status FROM knowledge_nodes",
        lambda args: {"claim_status": "supported"},
    )
    row = queue_row(
        "requires_review",
        failure_class=None,
        target_type="claim",
        target_id=CLAIM_A,
    )
    done = asyncio.run(handle_requires_review(pool, row))
    assert done is True

    updates = [(sql, args) for _, sql, args in pool.writes if sql.startswith("UPDATE knowledge_nodes")]
    assert len(updates) == 1
    sql, args = updates[0]
    assert "claim_status = $2" in sql and "$1::uuid" in sql and "t_invalid IS NULL" in sql
    assert args[0] == CLAIM_A
    assert args[1] == REVIEW_CLAIM_STATUS == "uncertain"
    marker = json.loads(args[2])
    assert marker["review"]["reason"] == "unclassified_failure"
    assert marker["review"]["evidence_id"] == EVIDENCE_ID

    ops = changeset_ops(pool)
    assert len(ops) == 1
    detail = json.loads(ops[0][4])
    assert detail["prior_claim_status"] == "supported"
    assert detail["claim_status"] == "uncertain"


def test_requires_review_skips_non_claim_targets_and_missing_claims():
    pool = FakePool()
    install_changeset_rules(pool)
    pool.rule("WHERE reason = $1", False)
    done = asyncio.run(handle_requires_review(
        pool, queue_row("requires_review", failure_class=None,
                        target_type="implementation"),
    ))
    assert done is False
    assert pool.writes == []

    done = asyncio.run(handle_requires_review(
        pool, queue_row("requires_review", failure_class=None,
                        target_type="claim", target_id=CLAIM_A),
    ))          # no claim row registered -> fetchrow None
    assert done is False
    assert pool.writes == []


# ==================================================== idempotency


@pytest.mark.parametrize("route,kw", [
    ("capability_demotion", {}),
    ("applicability_narrowing", {
        "failure_class": "input_abnormal", "target_type": "procedure",
        "target_id": PROC_ID, "payload": {"failed_context_key": "ctx-x"}}),
    ("dependency_queue", {
        "failure_class": "environment_changed",
        "payload": {"failed_context_key": "ctx-drift"}}),
    ("requires_review", {"failure_class": None, "target_type": "claim",
                         "target_id": CLAIM_A}),
])
def test_same_route_consumed_twice_performs_one_update(route, kw):
    handler = HANDLED_ROUTES[route]

    pool = FakePool()
    install_changeset_rules(pool)
    pool.rule("WHERE reason = $1", False)                       # not yet applied
    pool.rule("FROM evidence WHERE target_type", lambda args: _stream_rows("success", "failure"))
    pool.rule("SELECT exclusions FROM procedures", lambda args: {"exclusions": []})
    pool.rule("FROM claim_sources", lambda args: [{"claim_id": CLAIM_A}])
    pool.rule("SELECT claim_status FROM knowledge_nodes",
              lambda args: {"claim_status": "candidate"})

    row = queue_row(route, **kw)
    assert asyncio.run(handler(pool, row)) is True
    writes_after_first = len(pool.writes)
    assert writes_after_first > 0

    pool.set_ledger(True)                                       # mandate already fired
    assert asyncio.run(handler(pool, row)) is False
    assert len(pool.writes) == writes_after_first, \
        "the ledger answer must gate EVERY write, not just some"


def test_dispatcher_rerun_over_unchanged_queues_does_zero_new_work():
    pool = FakePool()
    install_changeset_rules(pool)
    pool.rule("WHERE reason = $1", False)
    pool.rule("FROM evidence WHERE target_type", lambda args: _stream_rows("success"))
    pool.rule("SELECT exclusions FROM procedures", lambda args: {"exclusions": []})
    pool.rule("FROM claim_sources", lambda args: [])
    pool.rule("SELECT claim_status FROM knowledge_nodes",
              lambda args: {"claim_status": "candidate"})
    pool.rule("FROM failure_routes fr", lambda args: (
        [queue_row(args[0])] if args[0] == "capability_demotion" else []))

    first = asyncio.run(run_failure_handlers(pool))
    assert first["capability_demotion"] == 1
    pool.set_ledger(True)
    second = asyncio.run(run_failure_handlers(pool))
    assert second == {r: 0 for r in HANDLED_ROUTES}


# ================================ unrouted failures never fire handlers


def test_unrouted_failures_are_visible_but_never_trigger_handlers():
    pool = FakePool()
    install_changeset_rules(pool)
    sweep_hits = []

    def _sweep(args):
        sweep_hits.append(1)
        return [{
            "id": EVIDENCE_ID, "evidence_type": "execution_result",
            "target_type": "implementation", "target_id": IMPL_ID,
            "target_version": None, "outcome_status": "failure",
            "failure_class": None, "context_key": None,
            "independence_group": None,
        }]

    pool.rule("LEFT JOIN failure_routes", _sweep)               # the unrouted-sweep SQL

    applied = asyncio.run(run_failure_handlers(pool))
    assert applied == {r: 0 for r in HANDLED_ROUTES}
    assert sweep_hits == [], "the dispatcher must never read the unrouted sweep"
    assert pool.writes == []

    backlog = asyncio.run(unclassified_backlog(pool))
    assert len(backlog) == 1 and backlog[0]["id"] == EVIDENCE_ID
    assert sum(sweep_hits) == 1
    assert pool.writes == [], "visibility is read-only: no handler fired for the unclassified row"


# ==================== engine-vocabulary parity (migration 21 teeth)


def test_claim_status_stamps_are_legal_migration21_vocabulary():
    ddl = Path(__file__).resolve().parents[1].joinpath(
        "db", "21_band1_contracts.sql").read_text(encoding="utf-8")
    block = ddl.split("kn_claim_status_chk", 1)[1].split(");", 1)[0]
    allowed = set(re.findall(r"'([a-z]+)'", block))
    assert {REVIEW_CLAIM_STATUS, REVALIDATION_CLAIM_STATUS} <= allowed
