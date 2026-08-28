"""Band 2.4 proving tests -- the §36 failure-classification pipeline.

Offline section proves the pure surface of app/execution/failures.py:
every cause routes to its mandated update, unclassified failures route
to requires_review, non-failures refuse to classify, payloads carry
what the owning consumer needs, and Python's route vocabulary is
statically equal to migration 27's engine CHECK.

E2E section (real DATABASE_URL, skips without one -- and skips with a
documented reason where db/24/27 aren't applied, i.e. the drifted shared
instance): real failed evidence rows through the 1.9a boundary, routed
through classify_and_route(); idempotent re-routing; queue/sweep
readers; append-only engine teeth (UPDATE/DELETE refused, tombstone
legal); cleanup by RETRACTION, never DELETE -- these tables are [H].

Spec v4 §36 verbatim mapping under test:
    procedure_wrong      -> procedure_version_candidate
    implementation_wrong -> capability_demotion
    environment_changed  -> dependency_queue
    input_abnormal       -> applicability_narrowing
    verification_wrong   -> plan_revision
    external_failure     -> no_op
    false_reuse          -> applicability_narrowing (named judgment call)
    (class NULL)         -> requires_review
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path

import asyncpg
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

from app.execution.failures import (
    FAILURE_CLASSES,
    FAILURE_ROUTES,
    FALSE_REUSE_ROUTE,
    ROUTER_STAMP,
    ROUTE_VALUES,
    UNCLASSIFIED_ROUTE,
    NotClassifiable,
    build_payload,
    classify_and_route,
    classify_failure,
)

# ======================================================== offline: routing


def _row(**overrides):
    """A minimal FAILED execution_result evidence row (asyncpg-Record
    shape); overrides win."""
    row = {
        "id": str(uuid.uuid4()),
        "outcome_status": "failure",
        "failure_class": None,
        "target_type": "implementation",
        "target_id": str(uuid.uuid4()),
        "target_version": None,
        "context_key": None,
        "independence_group": None,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("failure_class,expected_route", [
    ("procedure_wrong", "procedure_version_candidate"),
    ("implementation_wrong", "capability_demotion"),
    ("environment_changed", "dependency_queue"),
    ("input_abnormal", "applicability_narrowing"),
    ("verification_wrong", "plan_revision"),
    ("external_failure", "no_op"),
])
def test_each_cause_routes_to_its_mandated_update(failure_class, expected_route):
    assert FAILURE_ROUTES[failure_class] == expected_route
    got = classify_failure(_row(failure_class=failure_class))
    assert got.route == expected_route
    assert got.failure_class == failure_class
    assert not got.unclassified


def test_unclassified_failure_routes_to_requires_review():
    got = classify_failure(_row(failure_class=None))
    assert got.route == UNCLASSIFIED_ROUTE == "requires_review"
    assert got.unclassified is True


def test_false_reuse_routes_to_the_named_judgment_call(monkeypatch):
    assert FALSE_REUSE_ROUTE == "applicability_narrowing"
    assert classify_failure(_row(failure_class="false_reuse")).route == \
        "applicability_narrowing"

    # named constant, proven retunable -- an inline literal would fail here
    import app.execution.failures as failures_module
    monkeypatch.setattr(failures_module, "FALSE_REUSE_ROUTE", "requires_review")
    assert classify_failure(_row(failure_class="false_reuse")).route == \
        "requires_review"


# ================================================ offline: refusals


@pytest.mark.parametrize("status", ["success", "needs_rework", None])
def test_only_failures_classify(status):
    with pytest.raises(NotClassifiable, match="only failed outcomes classify"):
        classify_failure(_row(outcome_status=status))


def test_unknown_failure_class_refuses_to_silently_route():
    with pytest.raises(NotClassifiable, match="unknown failure_class"):
        classify_failure(_row(failure_class="vibes"))


def test_classification_carries_target_and_aggregation_inputs():
    ctx, grp, tid = "ctx-7", "group-a", str(uuid.uuid4())
    got = classify_failure(_row(
        failure_class="input_abnormal",
        target_id=tid, context_key=ctx, independence_group=grp,
    ))
    assert (got.target_type, got.target_id) == ("implementation", tid)
    assert got.context_key == ctx
    assert got.independence_group == grp


# ============================================== offline: payloads


def test_capability_demotion_payload_carries_independence_cap():
    got = build_payload(classify_failure(_row(
        failure_class="implementation_wrong", independence_group="run-42")))
    assert got["independence_group"] == "run-42"


def test_narrowing_and_dependency_payloads_carry_failed_context():
    got = build_payload(classify_failure(
        _row(failure_class="environment_changed", context_key="ctx-drift")))
    assert got["failed_context_key"] == "ctx-drift"
    got = build_payload(classify_failure(
        _row(failure_class="input_abnormal", context_key="ctx-weird")))
    assert got["failed_context_key"] == "ctx-weird"


def test_no_op_and_review_payloads_are_empty():
    assert build_payload(classify_failure(_row(failure_class="external_failure"))) == {}
    assert build_payload(classify_failure(_row(failure_class=None))) == {}


def test_router_stamp_is_name_at_version():
    assert ROUTER_STAMP == "failure_router@1"


# ============================ offline: python/ddl vocabulary parity


def _ddl() -> str:
    p = Path(__file__).resolve().parents[1] / "db" / "27_failure_routing.sql"
    return p.read_text(encoding="utf-8")


def test_python_route_vocabulary_equals_engine_check():
    ddl = _ddl()
    block = re.search(r"CHECK \(route IN \((.*?)\)\)", ddl, re.DOTALL).group(1)
    ddl_values = set(re.findall(r"'([a-z_]+)'", block))
    assert ddl_values == set(ROUTE_VALUES), (
        "failures.py and db/27 must name the same seven routes"
    )


def test_migration27_links_to_evidence_and_is_idempotent():
    ddl = _ddl()
    assert "REFERENCES evidence(id)" in ddl
    assert "UNIQUE (evidence_id, route)" in ddl, \
        "re-routing the same failure must be a no-op, not a duplicate mandate"


def test_migration27_null_class_pairs_only_with_requires_review():
    # §36's honest distinction, as ENGINE teeth: an unclassified failure
    # may ONLY become requires_review; a classified one may never.
    assert "(failure_class IS NULL) = (route = 'requires_review')" in _ddl()


def test_migration27_is_append_only_with_tombstone_exception():
    ddl = _ddl()
    assert "tg_failure_routes_append_only" in ddl
    assert "sl_failure_routes_only_tombstone" in ddl
    assert "DELETE rejected" in ddl
    assert "only the t_invalid retraction tombstone may change" in ddl


def test_migration27_has_no_backfill_statements():
    # A backfill STATEMENT is SQL, not prose: comment lines are inert,
    # and the append-only trigger's own BEFORE UPDATE OR DELETE clause
    # is required DDL (db/24's freeze carries it too). Everything else
    # containing an UPDATE keyword would be a data mutation.
    suspicious = [
        line for line in _ddl().splitlines()
        if not line.lstrip().startswith("--")
        and "UPDATE" in line.upper()
        and "BEFORE UPDATE OR DELETE" not in line.upper()
    ]
    assert not suspicious, f"fresh-start ruling: possible backfill(s): {suspicious}"


def test_record_routing_is_conflict_idempotent_at_the_sql_level():
    import inspect

    import app.execution.failures as failures_module
    sql = inspect.getsource(failures_module.record_routing)
    assert "ON CONFLICT (evidence_id, route) DO NOTHING" in sql
    assert "RETURNING id" in sql


def test_route_queue_reader_rejects_unknown_routes():
    asyncio.run(_queue_check())


async def _queue_check():
    from app.execution.failures import fetch_route_queue

    class _FakePool:
        async def fetch(self, *a, **k):  # pragma: no cover - never reached
            raise AssertionError("unknown route must be rejected before SQL")

    with pytest.raises(ValueError, match="unknown route"):
        await fetch_route_queue(_FakePool(), "vibes")  # type: ignore[arg-type]


# ====================================================== e2e (live DB)

e2e = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database integration test"
)


async def _schema_ready(pool) -> bool:
    return await pool.fetchval(
        "SELECT count(*) = 2 FROM information_schema.tables "
        "WHERE table_schema = 'public' "
        "AND table_name IN ('evidence', 'failure_routes')"
    )


def _insert_evidence_columns() -> tuple[str, ...]:
    from app.models.evidence import Evidence

    return tuple(Evidence.model_fields.keys())


async def _seed(pool, *, failure_class, status, context_key=None):
    from app.execution.evidence import validate_evidence

    kwargs = dict(
        evidence_type="execution_result",
        target={"target_type": "implementation", "target_id": str(uuid.uuid4())},
        direction="supports" if status == "success" else "contradicts",
        strength_score=1.0,
        strength_method="recorded_outcome",
        outcome_status=status,
        context_key=context_key,
        created_by="band24-e2e",
    )
    if status == "success":
        kwargs["success_criteria"] = {"predicate": "exit_code == 0"}
    if failure_class is not None:
        kwargs["failure_class"] = failure_class
    ev = validate_evidence(**kwargs)
    row = ev.to_row()
    cols = list(row.keys())
    jsonb_cols = {"success_criteria"}
    col_sql = ", ".join(cols)
    placeholders = ", ".join(
        f"${i+1}::jsonb" if c in jsonb_cols else f"${i+1}"
        for i, c in enumerate(cols)
    )
    await pool.execute(
        f"INSERT INTO evidence ({col_sql}) VALUES ({placeholders})",
        *[row[c] for c in cols],
    )
    return ev.id, row


async def _retract_all(pool) -> None:
    # These tables are [H]: cleanup means RETRACT, never DELETE --
    # exactly the discipline the production code is under.
    await pool.execute(
        "UPDATE failure_routes SET t_invalid = now() "
        "WHERE created_by IS NULL AND evidence_id IN "
        "(SELECT id FROM evidence WHERE created_by = 'band24-e2e') "
        "AND t_invalid IS NULL"
    )
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE created_by = 'band24-e2e' "
        "AND t_invalid IS NULL"
    )


@e2e
def test_failures_classify_route_and_land_in_queryable_queues():
    from app.db.session import create_pool
    from app.execution.failures import (
        fetch_route_queue,
        fetch_unrouted_failures,
    )

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            if not await _schema_ready(pool):
                pytest.skip(
                    "evidence/failure_routes tables absent (shared-instance "
                    "drift: migrations >=24 unapplied) -- routing proof needs "
                    "the migrated schema (CI / post-queue-2 DB)"
                )

            await _retract_all(pool)

            classified_id, classified_row = await _seed(
                pool, failure_class="environment_changed",
                status="failure", context_key="ctx-env-drift",
            )
            unclassified_id, unclassified_row = await _seed(
                pool, failure_class=None, status="failure",
                context_key="ctx-mystery",
            )
            await _seed(pool, failure_class=None, status="success")
            stray_failure_id, _ = await _seed(
                pool, failure_class="verification_wrong", status="failure",
            )

            # --- successes refuse to classify --------------------------
            success_rows = await pool.fetch(
                "SELECT * FROM evidence WHERE created_by = 'band24-e2e' "
                "AND outcome_status = 'success'"
            )
            with pytest.raises(NotClassifiable):
                await classify_and_route(pool, dict(success_rows[0]))

            # --- classified failure -> dependency_queue ---------------
            outcome = await classify_and_route(pool, classified_row)
            assert outcome.already_routed is False
            assert outcome.classification.route == "dependency_queue"
            assert outcome.route_row_id is not None

            # --- unclassified failure -> requires_review --------------
            outcome2 = await classify_and_route(pool, unclassified_row)
            assert outcome2.classification.route == "requires_review"
            assert outcome2.route_row_id is not None

            # --- idempotence: re-routing is a visible no-op -----------
            again = await classify_and_route(pool, classified_row)
            assert again.already_routed is True and again.route_row_id is None
            count = await pool.fetchval(
                "SELECT count(*) FROM failure_routes WHERE evidence_id = $1::uuid",
                classified_id,
            )
            assert count == 1

            # --- queues are readable; sweep finds only the stray ------
            dep_queue = await fetch_route_queue(pool, "dependency_queue")
            assert any(str(r["evidence_id"]) == str(classified_id) for r in dep_queue)
            review_queue = await fetch_route_queue(pool, "requires_review")
            assert any(str(r["evidence_id"]) == str(unclassified_id) for r in review_queue)
            unrouted = {
                str(r["id"]) for r in await fetch_unrouted_failures(pool)
            }
            assert str(stray_failure_id) in unrouted
            assert str(classified_id) not in unrouted
            assert str(unclassified_id) not in unrouted

            # --- engine teeth: this log is [H] ------------------------
            with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
                await pool.execute(
                    "UPDATE failure_routes SET route = 'no_op' "
                    "WHERE evidence_id = $1::uuid", classified_id,
                )
            with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
                await pool.execute(
                    "DELETE FROM failure_routes WHERE evidence_id = $1::uuid",
                    classified_id,
                )

            # --- the ONE legal mutation: retraction tombstone ---------
            await pool.execute(
                "UPDATE failure_routes SET t_invalid = now() "
                "WHERE evidence_id = $1::uuid AND t_invalid IS NULL",
                classified_id,
            )
            assert all(
                str(r["evidence_id"]) != str(classified_id)
                for r in await fetch_route_queue(pool, "dependency_queue")
            ), "retracted decisions leave the live queue"
        finally:
            await _retract_all(pool)
            await pool.close()

    asyncio.run(_run())
