"""
MCP hardening B34/B32: the verification ladder.

The core invariant, enforced HERE (not just documented): the host cannot
directly write the strongest state. Every `record_*` function below is
named after one evidence class and computes the resulting `state`
itself from what that class can actually support -- none of them accept
`state` as a caller-supplied parameter. `evaluate_run_completion` (the
`verify_completion` MCP tool's engine) only ever READS results these
functions produced; it never grades anything itself.

State ladder (weakest to strongest, non-failure states):
    CLAIMED_DONE < CHECKED < VERIFIED < INDEPENDENTLY_VERIFIED
`FAILED_VERIFICATION` and `INCONCLUSIVE` are outcomes, not rungs on this
ladder -- a criterion can fail or stay inconclusive from any method.

Criteria themselves are NOT a new stored entity -- see migration 55's
own comment: `procedures.postconditions` (a plain array of statement
strings, confirmed live -- 2 of 2625 procedures even have any yet) is
already the durable definition. `derive_criteria` below only computes a
stable `criterion_id` for each one; it never copies or mutates the
Procedure row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import asyncpg

METHODS: tuple[str, ...] = (
    "self_report", "artifact_inspection", "deterministic_check",
    "independent_agent", "human_review", "real_world_outcome",
)
STATES: tuple[str, ...] = (
    "claimed_done", "checked", "verified", "independently_verified",
    "failed_verification", "inconclusive",
)
# Ladder rank for the non-failure states only -- used to report "the
# strongest state actually reached", never to let a caller pick a rank.
_LADDER_RANK = {"claimed_done": 0, "checked": 1, "verified": 2, "independently_verified": 3}


class VerificationError(Exception):
    """Raised on a malformed verification request -- never silently coerced."""


@dataclass
class Criterion:
    criterion_id: str
    statement: str
    required: bool = True


def derive_criteria(procedure: dict) -> list[Criterion]:
    """One Criterion per `postconditions` entry, in order. A postcondition
    may be a plain string (every real one today) or a dict with a
    `statement` key and an optional `required` flag (`required=False` is
    the one thing an author can declare today; there is no `method`
    field to read yet -- see this module's own docstring for why that is
    a real, current limitation, not an oversight)."""
    criteria: list[Criterion] = []
    for i, raw in enumerate(procedure.get("postconditions") or []):
        if isinstance(raw, str):
            criteria.append(Criterion(criterion_id=f"postcondition:{i}", statement=raw))
        elif isinstance(raw, dict) and raw.get("statement"):
            criteria.append(Criterion(
                criterion_id=f"postcondition:{i}", statement=raw["statement"],
                required=bool(raw.get("required", True)),
            ))
        # A malformed entry (empty dict, no statement) is skipped, not
        # fabricated into a criterion with empty text.
    return criteria


def compute_verification_plan_id(procedure: dict) -> Optional[str]:
    """B3's literal `verification_plan_id` context field. A STABLE
    fingerprint of the ordered (criterion_id, statement, required) tuples
    `derive_criteria(procedure)` would produce for this exact procedure
    payload -- NOT a new stored entity (this module's own docstring:
    "postconditions ARE the plan"). `None` for a procedure with no real
    postconditions -- nothing to fingerprint, never a fabricated id for
    an empty plan."""
    criteria = derive_criteria(procedure)
    if not criteria:
        return None
    import hashlib
    import json

    fingerprint_input = json.dumps(
        [(c.criterion_id, c.statement, c.required) for c in criteria], sort_keys=True,
    )
    return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()


async def _upsert_result(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    method: str, required: bool, state: str, evidence_refs: list,
    reviewer: Optional[str] = None, reviewed_targets: Optional[list] = None,
    criterion_answers: Optional[dict] = None, detail: Optional[str] = None,
    created_by: Optional[str] = None,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO verification_results (
            execution_run_id, criterion_id, statement, method, required, state,
            evidence_refs, reviewer, reviewed_targets, criterion_answers, detail, created_by
        ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (execution_run_id, criterion_id) DO UPDATE SET
            statement = EXCLUDED.statement, method = EXCLUDED.method,
            required = EXCLUDED.required, state = EXCLUDED.state,
            evidence_refs = EXCLUDED.evidence_refs, reviewer = EXCLUDED.reviewer,
            reviewed_targets = EXCLUDED.reviewed_targets,
            criterion_answers = EXCLUDED.criterion_answers, detail = EXCLUDED.detail
        RETURNING *
        """,
        execution_run_id, criterion_id, statement, method, required, state,
        evidence_refs or [], reviewer, reviewed_targets or [], criterion_answers or {},
        detail, created_by,
    )
    from app.execution.recorder import record_verification_started
    await record_verification_started(pool, execution_run_id, criterion_id=criterion_id, method=method)
    return dict(row)


async def record_self_report(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    claimed_success: bool, required: bool = True, detail: Optional[str] = None,
    created_by: Optional[str] = None,
) -> dict:
    """SELF_REPORT can reach `claimed_done` on success or
    `failed_verification` on an admitted failure -- it can NEVER reach
    `checked`/`verified`/`independently_verified` (B34's own rule: "A
    self-report MUST NOT satisfy an independently verifiable
    deterministic criterion" -- generalized here to every stronger
    state, not just the deterministic one)."""
    state = "claimed_done" if claimed_success else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="self_report", required=required, state=state, evidence_refs=[],
        detail=detail, created_by=created_by,
    )


async def record_artifact_inspection(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    passed: bool, evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
) -> dict:
    """A real artifact was inspected (e.g. artifact_validation.py's own
    gate) -- reaches `checked` on success, one rung above a bare claim,
    still below a deterministic check's `verified`."""
    state = "checked" if passed else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="artifact_inspection", required=required, state=state,
        evidence_refs=evidence_refs or [], detail=detail, created_by=created_by,
    )


async def record_deterministic_check(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    passed: bool, evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
) -> dict:
    """A real, repeatable, machine-executed check (a command/probe) --
    reaches `verified` on success."""
    state = "verified" if passed else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="deterministic_check", required=required, state=state,
        evidence_refs=evidence_refs or [], detail=detail, created_by=created_by,
    )


async def record_independent_agent(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    passed: bool, evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
) -> dict:
    """A SEPARATE agent/model (not the one that performed the work)
    checked it -- reaches `independently_verified`, the strongest
    non-human-review state, on success."""
    state = "independently_verified" if passed else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="independent_agent", required=required, state=state,
        evidence_refs=evidence_refs or [], detail=detail, created_by=created_by,
    )


async def record_human_review(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    reviewer: str, reviewed_targets: list, criterion_answers: dict, verdict: bool,
    evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
) -> dict:
    """B34: "Do not accept approved=true without reviewer identity,
    reviewed targets, criterion answers, timestamp, and Evidence
    linkage." `reviewer`/`reviewed_targets` are required parameters
    here (no default), and migration 55's own CHECK constraint enforces
    the same rule at the DB layer as a backstop. `verdict=True` reaches
    `independently_verified` (a human reviewer is at least as strong a
    signal as an independent agent); `verdict=False` fails."""
    if not reviewer or not reviewed_targets:
        raise VerificationError(
            "human review requires a real reviewer identity and at least one reviewed target"
        )
    state = "independently_verified" if verdict else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="human_review", required=required, state=state, evidence_refs=evidence_refs or [],
        reviewer=reviewer, reviewed_targets=reviewed_targets, criterion_answers=criterion_answers,
        detail=detail, created_by=created_by,
    )


async def record_real_world_outcome(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    passed: bool, evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
) -> dict:
    """An observed real-world effect (e.g. a production metric moved,
    a downstream system behaved as expected) -- reaches `verified`, the
    same rung as a deterministic check (both are real, observed outcomes;
    neither is inherently stronger than the other in general)."""
    state = "verified" if passed else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="real_world_outcome", required=required, state=state,
        evidence_refs=evidence_refs or [], detail=detail, created_by=created_by,
    )


async def evaluate_run_completion(
    pool: asyncpg.Pool, *, execution_run_id: str, procedure: dict,
) -> dict:
    """
    `verify_completion`'s engine: derives the real criteria from
    `procedure["postconditions"]`, loads whatever `verification_results`
    already exist for this run, and reports -- per criterion -- the
    strongest state reached, the evidence/method behind it, and which
    required criteria have NO result at all yet (`inconclusive`, never
    silently treated as passing). Never grades anything itself; a
    criterion with no recorded result stays `inconclusive` until a
    `record_*` call above is made for it.
    """
    criteria = derive_criteria(procedure)
    rows = await pool.fetch(
        "SELECT * FROM verification_results WHERE execution_run_id = $1::uuid", execution_run_id,
    )
    results_by_criterion = {r["criterion_id"]: dict(r) for r in rows}

    per_criterion = []
    for c in criteria:
        result = results_by_criterion.get(c.criterion_id)
        if result is None:
            per_criterion.append({
                "criterion_id": c.criterion_id, "statement": c.statement,
                "required": c.required, "state": "inconclusive", "method": None,
                "evidence_refs": [],
            })
        else:
            per_criterion.append({
                "criterion_id": c.criterion_id, "statement": c.statement,
                "required": c.required, "state": result["state"], "method": result["method"],
                "evidence_refs": result["evidence_refs"],
            })

    required_unmet = [
        c for c in per_criterion
        if c["required"] and c["state"] in ("inconclusive", "failed_verification")
    ]
    if any(c["state"] == "failed_verification" for c in per_criterion if c["required"]):
        overall = "failed_verification"
    elif required_unmet:
        overall = "inconclusive"
    elif per_criterion:
        # Every required criterion has a real, non-failing result --
        # the run's overall state is the WEAKEST rung among them (a
        # chain is only as strong as its weakest verified link).
        ranked = [c for c in per_criterion if c["required"]]
        overall = min(ranked, key=lambda c: _LADDER_RANK.get(c["state"], 0))["state"]
    else:
        # No criteria at all (the overwhelmingly common case today,
        # postconditions being almost universally empty) -- honestly
        # inconclusive, never silently "verified".
        overall = "inconclusive"

    from app.execution.recorder import record_verification_completed
    await record_verification_completed(
        pool, execution_run_id, overall_state=overall, criteria_count=len(per_criterion),
    )

    return {
        "execution_run_id": execution_run_id,
        "overall_state": overall,
        "criteria": per_criterion,
    }
