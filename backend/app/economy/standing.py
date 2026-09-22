"""
Contributor Standing (§6): trust/reputation, separate from Credits, never
spendable. Computed live on every read, nothing stored -- the same
philosophy app/services/contributors.py already states outright for its
own leaderboard metrics ("there is no per-actor score... cannot be
re-derived from provenance"). Standing here is exactly that kind of
re-derivable number, so it gets no new table.

Simple and explainable by design (§6): a linear weighted sum of real,
countable facts. No decay curve, no graph propagation, no EigenTrust.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.economy import constants as c


async def compute_standing(pool: asyncpg.Pool, contributor_id: str) -> dict[str, Any]:
    accepted_procedure_submissions = await pool.fetchval(
        "SELECT count(*) FROM procedure_submissions WHERE submitted_by = $1 AND status = 'accepted' AND submission_type = 'new'",
        contributor_id,
    )
    accepted_improvements = await pool.fetchval(
        "SELECT count(*) FROM procedure_submissions WHERE submitted_by = $1 AND status = 'accepted' AND submission_type = 'improvement'",
        contributor_id,
    )
    accepted_benchmark_submissions = await pool.fetchval(
        "SELECT count(*) FROM benchmark_submissions WHERE submitted_by = $1 AND status = 'accepted'",
        contributor_id,
    )
    verified_outcomes = await pool.fetchval(
        """
        SELECT count(*) FROM procedure_usage_events
        WHERE contributor_id = $1 AND outcome_state = 'verified_success' AND is_self_use = FALSE
        """,
        contributor_id,
    )
    reliable_evidence = await pool.fetchval(
        "SELECT count(*) FROM evidence WHERE created_by = $1 AND direction = 'supports' AND strength_score >= $2",
        contributor_id, c.RELIABLE_EVIDENCE_MIN_STRENGTH,
    )

    accepted_procedure_submissions = int(accepted_procedure_submissions or 0)
    accepted_improvements = int(accepted_improvements or 0)
    accepted_benchmark_submissions = int(accepted_benchmark_submissions or 0)
    verified_outcomes = int(verified_outcomes or 0)
    reliable_evidence = int(reliable_evidence or 0)

    score = (
        (accepted_procedure_submissions + accepted_benchmark_submissions) * c.STANDING_WEIGHT_ACCEPTED_SUBMISSION
        + accepted_improvements * c.STANDING_WEIGHT_ACCEPTED_IMPROVEMENT
        + verified_outcomes * c.STANDING_WEIGHT_VERIFIED_OUTCOME
        + reliable_evidence * c.STANDING_WEIGHT_RELIABLE_EVIDENCE
    )

    return {
        "contributor_id": contributor_id,
        "standing_score": round(score, 2),
        "accepted_new_procedures": accepted_procedure_submissions,
        "accepted_improvements": accepted_improvements,
        "accepted_benchmarks": accepted_benchmark_submissions,
        "verified_independent_outcomes": verified_outcomes,
        "reliable_evidence_count": reliable_evidence,
        "method": "v1_linear_weights",
    }
