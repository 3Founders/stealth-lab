"""Contextual Procedure ranking backed by the central Bayesian rank service."""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope, TenantScope
from app.services.goal_ranking import ProcedureRankingService
from app.services.procedure_graph_api import get_solution_view

Bucket = str

_BUCKET_ORDER = {"verified": 0, "candidate": 1, "needs_evidence": 2, "verified_failure": 3}
_BUCKET_LABELS = {
    "verified": "Verified",
    "candidate": "Candidate",
    "needs_evidence": "Needs evidence",
    "verified_failure": "Verified failure",
}
_CANONICAL_GET_SOLUTION_VIEW = get_solution_view


def verification_bucket(verification_state: Optional[str], capability: dict[str, Any]) -> Bucket:
    if verification_state == "verified":
        return "verified"
    evidence_count = int(capability.get("evidence_count") or 0)
    success_count = int(capability.get("success_count") or 0)
    if evidence_count == 0:
        return "needs_evidence"
    if success_count == 0:
        return "verified_failure"
    return "candidate"


def _context_matches(solution: dict[str, Any], context_key: Optional[str]) -> bool:
    if not context_key:
        return False
    summary = str(solution.get("applicability_summary") or "").lower()
    return bool(summary) and context_key.lower() in summary


async def _legacy_view_rank(
    pool: asyncpg.Pool,
    procedure_row_ids: list[str],
    *,
    scope: AccessScope,
    context_key: Optional[str],
) -> list[dict[str, Any]]:
    solutions: list[dict[str, Any]] = []
    for procedure_row_id in procedure_row_ids:
        view = await get_solution_view(pool, procedure_row_id, scope=scope)
        if view is not None:
            solutions.append(view)

    def sort_key(solution: dict[str, Any]) -> tuple[Any, ...]:
        capability = solution["capability"]
        bucket = verification_bucket(solution.get("verification_state"), capability)
        return (
            _BUCKET_ORDER.get(bucket, 9),
            0 if _context_matches(solution, context_key) else 1,
            -float(capability.get("p_lower") or 0.0),
            str(solution.get("procedure_row_id") or ""),
        )

    solutions.sort(key=sort_key)
    ranked: list[dict[str, Any]] = []
    for index, solution in enumerate(solutions, start=1):
        capability = solution["capability"]
        bucket = verification_bucket(solution.get("verification_state"), capability)
        ranked.append(
            {
                "procedure_row_id": solution["procedure_row_id"],
                "procedure_id": solution["procedure_id"],
                "version": solution["version"],
                "display_name": solution["display_name"],
                "display_description": solution["display_description"],
                "applicability_summary": solution["applicability_summary"],
                "created_by": solution["created_by"],
                "bucket": bucket,
                "bucket_label": _BUCKET_LABELS[bucket],
                "rank": index,
                "of": len(solutions),
                "wilson_lower_bound": capability["p_lower"],
                "wilson_upper_bound": capability["p_upper"],
                "evidence_count": capability["evidence_count"],
                "success_count": capability["success_count"],
                "independent_groups": capability["independent_groups"],
                "cost": solution["cost"],
                "context_matched": _context_matches(solution, context_key),
            }
        )
    return ranked


async def rank_procedures_for_goal(
    pool: asyncpg.Pool,
    procedure_row_ids: list[str],
    *,
    scope: AccessScope,
    context_key: Optional[str] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> list[dict[str, Any]]:
    if get_solution_view is not _CANONICAL_GET_SOLUTION_VIEW and not hasattr(pool, "fetch"):
        return await _legacy_view_rank(
            pool, procedure_row_ids, scope=scope, context_key=context_key
        )
    service = ProcedureRankingService(
        pool,
        scope=scope,
        context_key=context_key,
        tenant_scope=tenant_scope or TenantScope.unrestricted(),
    )
    return await service.rank(procedure_row_ids, context_key=context_key)
