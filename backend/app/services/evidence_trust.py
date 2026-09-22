"""
THE canonical mapping from a raw `evidence` row to the outcome-trust
vocabulary the whole product shares: UNKNOWN / CLAIMED_SUCCESS /
VERIFIED_SUCCESS / VERIFIED_FAILURE (keळ V1 behavioral spec, Part 9).

Before this module, the same question ("did this evidence actually prove
something?") was answered three different ways: the frontend read
`evidence_summary.success_count` and displayed it as "Verified runs" for
ANY evidence row with `outcome_status='success'`, regardless of
`evidence_type` or who wrote it; `procedure_graph_api._capability_estimate`
filtered by `evidence_type` (a real, more careful check) but not by
`created_by`; and `app.economy.verification._verify_execution_chain`
correctly required both, but only for a single new usage event, with no
shared vocabulary a caller elsewhere could reuse. This module is the one
place that decision lives now.

"Verified" requires BOTH:
  - `evidence_type` in the outcome-bearing set
    (`procedure_graph_api._OUTCOME_BEARING_EVIDENCE_TYPES`, reused here
    verbatim as `OUTCOME_BEARING_EVIDENCE_TYPES` -- not a `document`/
    `human_review`/`observation` row that merely SAYS success),
  - `created_by` equals the trusted execution-outcome writer stamp
    (`app.services.procedures.OUTCOME_WRITER_STAMP`) -- the one constant
    that answers "did this come from a real, system-recorded execution,"
    since no public endpoint anywhere in this codebase writes `evidence`
    with that `created_by` value (confirmed: no `app/api/*.py` file
    inserts into `evidence` at all).

Anything else with `outcome_status='success'` is CLAIMED_SUCCESS, never
VERIFIED_SUCCESS. There is no CLAIMED_FAILURE in the spec's own four-state
vocabulary; an unverified failure claim folds into UNKNOWN -- nothing is
ever rewarded for a failure claim, so precision matters far less there
than for a success claim, and inventing a fifth state the spec didn't ask
for would be exactly the kind of parallel model this module exists to end.
"""
from __future__ import annotations

from typing import Any

from app.services.procedures import OUTCOME_WRITER_STAMP

# Reused verbatim from procedure_graph_api -- not redefined. That module
# imports this constant rather than keeping its own copy (see the edit
# there); this is the single source.
OUTCOME_BEARING_EVIDENCE_TYPES = ("execution_result", "reproduction")

UNKNOWN = "unknown"
CLAIMED_SUCCESS = "claimed_success"
VERIFIED_SUCCESS = "verified_success"
VERIFIED_FAILURE = "verified_failure"

ALL_STATES = (UNKNOWN, CLAIMED_SUCCESS, VERIFIED_SUCCESS, VERIFIED_FAILURE)


def has_recorded_outcome(evidence_row: dict[str, Any]) -> bool:
    return evidence_row.get("outcome_status") in ("success", "failure")


def is_outcome_bearing(evidence_row: dict[str, Any]) -> bool:
    """Matches procedure_graph_api._capability_estimate's own filter
    criteria exactly (evidence_type + a recorded outcome) -- this is "does
    this row count as real execution evidence at all for the capability
    estimator", a narrower question than "is there a success/failure claim
    here at all" (has_recorded_outcome)."""
    return evidence_row.get("evidence_type") in OUTCOME_BEARING_EVIDENCE_TYPES and has_recorded_outcome(evidence_row)


def is_trusted_writer(evidence_row: dict[str, Any]) -> bool:
    return evidence_row.get("created_by") == OUTCOME_WRITER_STAMP


def trust_state(evidence_row: dict[str, Any]) -> str:
    """The one function every consumer (Procedure detail, Goal page,
    ranking, usage events, any future API response) must call instead of
    reading `outcome_status` directly and assuming it means "verified".

    ANY row with a recorded success/failure outcome_status is at least a
    CLAIM (someone -- a human reviewer, an LLM judgment, a self-report --
    is asserting an outcome). It only escalates to VERIFIED when it is
    ALSO an outcome-bearing evidence_type (execution_result/reproduction,
    not a document/human_review/observation) written by the trusted
    execution-outcome writer."""
    if not has_recorded_outcome(evidence_row):
        return UNKNOWN
    verified = is_outcome_bearing(evidence_row) and is_trusted_writer(evidence_row)
    if evidence_row["outcome_status"] == "success":
        return VERIFIED_SUCCESS if verified else CLAIMED_SUCCESS
    return VERIFIED_FAILURE if verified else UNKNOWN


def summarize(evidence_rows: list[dict[str, Any]]) -> dict[str, int]:
    """The canonical `evidence_summary` shape. `success_count`/
    `failure_count` are kept as the field names existing callers
    (procedure_graph_api.get_procedure_detail, the frontend) already read
    -- but they now MEAN verified_success/verified_failure specifically,
    not "any row that says success," which is the actual fix: every
    existing reader becomes honest without needing its own code changed."""
    counts = {UNKNOWN: 0, CLAIMED_SUCCESS: 0, VERIFIED_SUCCESS: 0, VERIFIED_FAILURE: 0}
    outcome_bearing = 0
    for row in evidence_rows:
        counts[trust_state(row)] += 1
        if is_outcome_bearing(row):
            outcome_bearing += 1
    return {
        "total": len(evidence_rows),
        "outcome_bearing_total": outcome_bearing,
        "unknown": counts[UNKNOWN],
        "claimed_success": counts[CLAIMED_SUCCESS],
        "verified_success": counts[VERIFIED_SUCCESS],
        "verified_failure": counts[VERIFIED_FAILURE],
        # Legacy-compatible aliases (existing readers, now honest).
        "success_count": counts[VERIFIED_SUCCESS],
        "failure_count": counts[VERIFIED_FAILURE],
    }
