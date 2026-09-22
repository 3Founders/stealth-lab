"""
Contextual, per-Goal Procedure ranking. ONE canonical implementation.

Before this module was rewritten, THREE independent rankings existed:
`app.services.procedure_graph_api._capability_estimate`/`get_solution_view`
(the pre-existing, more rigorous Wilson-interval estimator, already
exposed at `GET /v1/solutions/{id}`), this module's own second Wilson
computation over an unfiltered evidence count, and the frontend's own
client-side linear heuristic (`rankScore` in prod_frontend/lib/kel-api.ts).
None of the three agreed with each other, and the one users actually saw
(the frontend one) was the least rigorous.

This module is now a thin wrapper around `get_solution_view` -- it does
not compute a Wilson interval itself. `verified/candidate/needs_evidence/
verified_failure` buckets and the sort order are the only things added
here; the underlying statistics come from the one existing estimator. The
frontend's `rankScore`/`verificationBucket` are removed (see
prod_frontend/lib/kel-api.ts) and replaced with a fetch of this endpoint's
output -- the frontend no longer computes rank, it displays it.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope
from app.services.procedure_graph_api import get_solution_view

Bucket = str  # "verified" | "candidate" | "needs_evidence" | "verified_failure"

_BUCKET_ORDER = {"verified": 0, "candidate": 1, "needs_evidence": 2, "verified_failure": 3}

_BUCKET_LABELS = {
    "verified": "Verified",
    "candidate": "Candidate",
    "needs_evidence": "Needs evidence",
    "verified_failure": "Verified failure",
}


def verification_bucket(verification_state: Optional[str], capability: dict[str, Any]) -> Bucket:
    """
    A Procedure already promoted to `verified` (procedures.verification_state,
    via app.services.procedures.record_execution_outcome's own strict
    threshold -- 10 successes / 3 distinct contexts / zero failures) is
    always labelled Verified. Otherwise: no real evidence yet ->
    needs_evidence (still discoverable, never hidden, never penalized to
    invisibility); evidence exists but every outcome-bearing row is a
    failure -> verified_failure (proven not to work, ranks below "unknown"
    on purpose -- an unproven Candidate is a better bet than a disproven
    one); anything else -> candidate.
    """
    if verification_state == "verified":
        return "verified"
    evidence_count = capability.get("evidence_count") or 0
    success_count = capability.get("success_count") or 0
    if evidence_count == 0:
        return "needs_evidence"
    if success_count == 0:
        return "verified_failure"
    return "candidate"


async def rank_procedures_for_goal(
    pool: asyncpg.Pool, procedure_row_ids: list[str], *, scope: AccessScope, context_key: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Fetches each Procedure's real Solution view (capability/cost/
    applicability -- app.services.procedure_graph_api.get_solution_view)
    and ranks by Wilson lower bound within an honest bucket, contextual to
    THIS goal only. `context_key`, when given, is a light applicability
    signal (an exact-match bonus only -- true context matching is
    applicability.py's job at retrieval time, not duplicated here).

    Never claims a universal "best": the response's own `note` field (set
    by the caller, app/api/economy.py) says so explicitly, and a
    specialized Procedure whose applicability_summary matches the given
    context is bonus-weighted UP, not penalized for narrowness.
    """
    solutions = []
    for pid in procedure_row_ids:
        sv = await get_solution_view(pool, pid, scope=scope)
        if sv is not None:
            solutions.append(sv)

    def matches_context(sv: dict[str, Any]) -> bool:
        if not context_key:
            return False
        summary = (sv.get("applicability_summary") or "").lower()
        return bool(summary) and context_key.lower() in summary

    def sort_key(sv: dict[str, Any]):
        capability = sv["capability"]
        bucket = verification_bucket(sv.get("verification_state"), capability)
        score = capability["p_lower"] * (1.05 if matches_context(sv) else 1.0)
        if sv.get("staleness") == "stale":
            score *= 0.95
        return (_BUCKET_ORDER.get(bucket, 9), -score)

    solutions.sort(key=sort_key)

    ranked: list[dict[str, Any]] = []
    for i, sv in enumerate(solutions):
        capability = sv["capability"]
        bucket = verification_bucket(sv.get("verification_state"), capability)
        ranked.append({
            "procedure_row_id": sv["procedure_row_id"],
            "procedure_id": sv["procedure_id"],
            "version": sv["version"],
            "display_name": sv["display_name"],
            "display_description": sv["display_description"],
            "applicability_summary": sv["applicability_summary"],
            "created_by": sv["created_by"],
            "bucket": bucket,
            "bucket_label": _BUCKET_LABELS[bucket],
            "rank": i + 1,
            "of": len(solutions),
            "wilson_lower_bound": capability["p_lower"],
            "wilson_upper_bound": capability["p_upper"],
            "evidence_count": capability["evidence_count"],
            "success_count": capability["success_count"],
            "independent_groups": capability["independent_groups"],
            "cost": sv["cost"],
            "context_matched": matches_context(sv),
        })
    return ranked
