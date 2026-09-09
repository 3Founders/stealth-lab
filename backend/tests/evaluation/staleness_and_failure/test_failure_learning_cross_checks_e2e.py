"""
Task spec §15 -- failure-learning cross-checks, against real PostgreSQL.

Three of the five required properties are ALREADY proven, thoroughly, by
Phase 3's durable-execution work -- re-testing them here would be a
pointless duplicate, so this file states them as explicit cross-references
(the task's own word: "cross-checks") rather than silently skipping them
or re-deriving them from scratch:

  - "retry does not count as independent success":
    tests/evaluation/durable/test_gold_durable_e2e.py::
    test_full_chain_a_succeeds_b_crashes_resume_c_waits_terminal_lineage
    asserts `len(rows) == 1` on the `executions` table after a node that
    needed a real crash+retry to succeed -- exactly one terminal outcome
    is ever recorded, never one row per attempt.
  - "repeated retry does not inflate evidence":
    the same test's exactly-one-executions-row assertion, plus
    test_duplicate_resume_produces_no_duplicate_evidence (a second resume
    call after terminal is a verified no-op).
  - "failure classification survives durable resume":
    test_retry_exhaustion_leaves_node_failed_without_operator_intervention
    reads back `error_class == 'timeout'` after two real attempts and
    after a subsequent resume/retry_node call -- the classification is a
    persisted column, not a return value.

This file's own value-add -- genuinely not covered elsewhere -- is at the
capabilities.py layer, which durable execution's own tests never exercise:
whether a recorded FAILURE can ever increase a Wilson-lower-bound capability
estimate (real cross-check: it cannot, by construction of the math, but this
proves it against the real function rather than asserting it from reading
the formula), and whether a recorded failure remains genuinely inspectable
after the fact rather than being silently dropped from the evidence stream.

Skips itself when DATABASE_URL is unset, matching every other _e2e.py file.
"""
from __future__ import annotations

import uuid
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.db.session import create_pool  # noqa: E402
from app.execution.implementation_registry import activate, register  # noqa: E402
from app.services.capabilities import (  # noqa: E402
    get_implementation_capability,
    record_implementation_outcome,
)

PREFIX = "failure-learning-gold-e2e"


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _cleanup(pool, name_like: str) -> None:
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE target_type = 'implementation' "
        "AND t_invalid IS NULL AND target_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{name_like}%",
    )
    await pool.execute(
        "DELETE FROM implementation_tasks WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{name_like}%",
    )
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{name_like}%")


@pytest.mark.asyncio
async def test_recorded_failure_never_increases_the_capability_estimate():
    """A real, additional failure recorded against an implementation with
    an already-established success streak must never raise its Wilson-
    lower-bound estimate -- proven against the real
    record_implementation_outcome/get_implementation_capability pair, not
    asserted from reading wilson_interval's formula. This is genuinely new
    coverage: no existing test in this codebase records a failure AFTER an
    established streak and re-checks the estimate moved the right way."""
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    name_like = f"{PREFIX}-{tag}"
    try:
        impl = await register(
            pool, name=f"{name_like}-target", kind="tool", provider="graphify", created_by=PREFIX,
        )
        await activate(pool, impl["id"])

        for i in range(8):
            await record_implementation_outcome(
                pool, implementation_id=impl["id"], outcome_status="success",
                success_criteria={"predicate": f"run {i} completed"}, created_by=PREFIX,
            )
        before = await get_implementation_capability(pool, impl["id"])
        assert before["evidence_count"] == 8 and before["success_count"] == 8
        assert before["p_estimate"] > 0.0

        await record_implementation_outcome(
            pool, implementation_id=impl["id"], outcome_status="failure",
            failure_class="environment_changed", created_by=PREFIX,
        )
        after = await get_implementation_capability(pool, impl["id"])
        assert after["evidence_count"] == 9 and after["success_count"] == 8, (
            "the failure is a real outcome-bearing attempt (denominator grows) "
            "without becoming a phantom success (numerator does not)"
        )
        assert after["p_estimate"] < before["p_estimate"], (
            "a recorded failure must never increase the capability estimate -- "
            "adding a failure to the same success count can only lower or hold "
            "the Wilson lower bound, and with 8/8 -> 8/9 it strictly lowers it"
        )

        # And repeating the same check from a position that is ALREADY
        # imperfect (so "hold steady" is also a live possibility, not just
        # "always strictly decreases") -- still never an increase.
        mid = after
        await record_implementation_outcome(
            pool, implementation_id=impl["id"], outcome_status="failure",
            failure_class="verification_wrong", created_by=PREFIX,
        )
        after2 = await get_implementation_capability(pool, impl["id"])
        assert after2["p_estimate"] <= mid["p_estimate"]
    finally:
        await _cleanup(pool, name_like)
        await pool.close()


@pytest.mark.asyncio
async def test_recorded_failure_remains_inspectable_not_silently_dropped():
    """A recorded failure's failure_class and outcome_status must remain
    genuinely readable after the fact -- failure-learning requires the
    failure to stay visible, not be excluded from the evidence stream the
    way an over-eager 'only show supporting evidence' filter might.
    Verified directly against _get_implementation_evidence's real query
    (target_type/target_id/t_invalid filter only -- no direction or
    outcome_status filter that could hide a failure)."""
    pool = await create_pool(statement_cache_size=0)
    tag = _tag()
    name_like = f"{PREFIX}-{tag}"
    try:
        impl = await register(
            pool, name=f"{name_like}-inspectable", kind="tool", provider="graphify", created_by=PREFIX,
        )
        await activate(pool, impl["id"])

        await record_implementation_outcome(
            pool, implementation_id=impl["id"], outcome_status="failure",
            failure_class="procedure_wrong", created_by=PREFIX,
        )
        row = await pool.fetchrow(
            "SELECT outcome_status, failure_class, success_criteria, t_invalid FROM evidence "
            "WHERE target_type='implementation' AND target_id=$1::uuid", impl["id"],
        )
        assert row is not None, "the failure evidence row is not silently dropped -- it is a real, queryable row"
        assert row["outcome_status"] == "failure"
        assert row["failure_class"] == "procedure_wrong"
        assert row["t_invalid"] is None, "a fresh failure is live, not pre-tombstoned"

        capability = await get_implementation_capability(pool, impl["id"])
        assert capability["evidence_count"] == 1, (
            "the failure is counted in the capability's own evidence stream, "
            "not excluded from it -- failure-learning requires it stay visible "
            "at both the raw-row level and the aggregated-capability level"
        )
    finally:
        await _cleanup(pool, name_like)
        await pool.close()
