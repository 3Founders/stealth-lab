"""
Procedure and Benchmark contribution submissions (§1, §2): the review
workflow in front of the existing procedures/benchmarks tables.

A submission's status (candidate/needs_review/accepted/rejected) is
DELIBERATELY separate from procedures.verification_state
(candidate/verified/retired) and procedures.approval_status
(proposed/approved/rejected) -- those already exist and mean something
else (evidence-based promotion, and a narrower admin flag respectively).
"Accepted" here answers "is this a real candidate the Goal page should
list and that can earn Credits", not "has this been proven to work."

HARDENING PASS (audit findings D1/D6/D9):

- `actor_subject` (renamed from `submitted_by`/`reviewed_by`) is now a
  required parameter that MUST be the server-derived authenticated
  principal's subject. This module no longer trusts a caller-supplied
  identity string for anything written to `submitted_by`, `owner_id`,
  `created_by`, or `reviewed_by`. The API router (app/api/economy.py) is
  the ONLY place identity is resolved (via `require_authenticated_user`),
  and it must pass that value in -- never a request-body field.

- `create_procedure_submission` now writes the `procedure_submissions`
  row FIRST (with `procedure_row_id = NULL`), then calls
  `capture_procedure`, then links the two. `capture_procedure` manages its
  own internal transaction (app.services.procedures.tenant_transaction)
  and cannot be merged into a single outer transaction without changing
  that shared, widely-used function -- out of scope for this pass. This
  ordering instead makes the failure mode SAFE: if `capture_procedure`
  raises, the submission row still exists, visibly incomplete
  (`procedure_row_id IS NULL`, `status='needs_review'`), not a silent gap.
  The previous ordering could leave an orphaned, unreviewable Procedure
  with no submission record at all if the second write failed.

- `review_procedure_submission`/`review_benchmark_submission` now perform
  `associate_solution` + the reward call BEFORE the status UPDATE. If
  either raises, the submission is left in its PRIOR (candidate/
  needs_review) status -- never "accepted" without its solution
  association and reward having actually happened. A reviewer can safely
  retry the review call; `associate_solution`'s own `ON CONFLICT ...
  DO UPDATE` and the reward ledger's idempotent unique constraints
  (migration 103) make the retry produce no duplicate effects.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg

from app.economy import constants as c
from app.economy import credits as credits_service
from app.economy import verification as verification_service
from app.economy.duplicates import embed_submission_text, score_against_parent, score_procedure_duplicate, score_benchmark_duplicate, submission_dedup_text
from app.services.embeddings import Embedder, to_pgvector
from app.services.procedures import capture_procedure
from app.services.product_model import associate_solution, create_benchmark, get_goal_for_product
from app.services.access import AccessScope
from app.services.v0_gate import validate_provenance, validate_scope
from app.utils.ids import uuid7


async def create_procedure_submission(
    pool: asyncpg.Pool, *, goal_id: str, submission_type: str, name: str, steps: list,
    actor_subject: str,
    rationale: Optional[str] = None, applicability_context: Optional[dict] = None,
    constraints: Optional[list] = None, implementation_requirements: Optional[dict] = None,
    supporting_evidence: Optional[list] = None, parent_procedure_row_id: Optional[str] = None,
    provenance: Optional[str] = None, scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None, visibility: str = "public",
    embedder: Optional[Embedder] = None, llm_evaluator=None,
) -> dict[str, Any]:
    if submission_type not in ("new", "improvement"):
        raise ValueError("submission_type must be 'new' or 'improvement'")
    if submission_type == "improvement" and not parent_procedure_row_id:
        raise ValueError("an 'improvement' submission requires parent_procedure_row_id")

    goal = await get_goal_for_product(pool, goal_id, scope=AccessScope.unrestricted())
    if goal is None:
        raise ValueError(f"goal {goal_id} not found")

    validate_provenance(provenance)
    resolved_scope_type, resolved_scope_entity_id = validate_scope(scope_type, scope_entity_id, allow_global_entity_id=bool(scope_type == "global" and scope_entity_id))

    layer1 = verification_service.evaluate_layer1(name=name, steps=steps, rationale=rationale)

    dedup_text = submission_dedup_text(name=name, rationale=rationale, steps=steps)
    embedding = await embed_submission_text(embedder, dedup_text)
    duplicate = await score_procedure_duplicate(pool, goal_id=goal_id, embedding=embedding)

    parent_similarity = None
    if submission_type == "improvement":
        parent_similarity = await score_against_parent(pool, parent_procedure_row_id=parent_procedure_row_id, embedding=embedding)

    layer2 = await verification_service.evaluate_layer2(
        submission={"name": name, "rationale": rationale, "goal": goal},
        duplicate=duplicate, layer1=layer1, parent_similarity=parent_similarity, llm_evaluator=llm_evaluator,
    )
    # A Layer 1/2 "reject" recommendation still lands as needs_review (never
    # a final 'rejected' -- only a human reviewer can set that, per the
    # anti-fabrication CHECK on this table), but is flagged loudly.
    status = "candidate" if layer2["decision"] == "candidate" else "needs_review"
    status_reason = None
    if layer2["decision"] == "reject":
        status_reason = "; ".join(layer2.get("notes", [])) or "flagged by automated review"

    # §9, symmetric with create_benchmark_submission: the same contributor
    # authoring both a Procedure and the Benchmark that evaluates its own
    # Goal is not auto-rejected, but always flagged for review.
    conflicting_benchmark = await pool.fetchval(
        "SELECT id FROM benchmark_submissions WHERE goal_id = $1 AND submitted_by = $2 AND status <> 'rejected' LIMIT 1",
        goal_id, actor_subject,
    )
    if conflicting_benchmark is not None:
        status = "needs_review"
        conflict_note = f"submitter also has a Benchmark submission ({conflicting_benchmark}) for this same Goal -- review for independence before acceptance"
        status_reason = f"{status_reason}; {conflict_note}" if status_reason else conflict_note

    row_id = str(uuid7())
    row = await pool.fetchrow(
        """
        INSERT INTO procedure_submissions (
            id, goal_id, submission_type, parent_procedure_row_id, procedure_row_id, name, content,
            rationale, applicability_context, constraints, implementation_requirements, supporting_evidence,
            status, status_reason, layer1_result, layer2_result, embedding, duplicate_of_submission_id, duplicate_score,
            parent_similarity_score,
            submitted_by, provenance, scope_type, scope_entity_id, owner_id, visibility
        ) VALUES ($1,$2,$3,$4,NULL,$5,$6::jsonb,
                  $7,$8::jsonb,$9::jsonb,$10::jsonb,$11::jsonb,
                  $12,$13,$14::jsonb,$15::jsonb,$16::vector,$17,$18,
                  $19,
                  $20,$21,$22,$23,$24,$25)
        RETURNING *
        """,
        row_id, goal_id, submission_type, parent_procedure_row_id, name,
        json.dumps({"steps": steps}),
        rationale, json.dumps(applicability_context or {}), json.dumps(constraints or []),
        json.dumps(implementation_requirements or {}), json.dumps(supporting_evidence or []),
        status, status_reason, json.dumps(layer1), json.dumps(layer2),
        to_pgvector(embedding) if embedding else None,
        duplicate.get("best_match_id") if duplicate.get("best_match_kind") == "submission" else None,
        duplicate.get("score"),
        (parent_similarity or {}).get("score"),
        actor_subject, provenance, resolved_scope_type, resolved_scope_entity_id, actor_subject, visibility,
    )

    # The Procedure exists from here on (ticket 13's rule: always born
    # candidate) -- submission status governs goal-page listing and reward
    # eligibility, not whether this row exists at all. If this fails, the
    # submission row above still exists (procedure_row_id NULL, visibly
    # incomplete) rather than an orphaned Procedure with no submission trail.
    try:
        captured = await capture_procedure(
            pool, name=name, goal=goal.get("title") or name, steps=steps,
            preconditions=(applicability_context or {}).get("preconditions") or [],
            invariants=constraints or [], provenance=provenance, domain=resolved_scope_entity_id,
            family_id=parent_procedure_row_id, created_by=actor_subject, owner_id=actor_subject,
            visibility=visibility if visibility in ("public", "private") else "public",
            embedding=embedding, scope_type=resolved_scope_type, scope_entity_id=resolved_scope_entity_id,
            display_name=name,
        )
    except Exception:
        await pool.execute(
            "UPDATE procedure_submissions SET status = 'needs_review', "
            "status_reason = COALESCE(status_reason, 'procedure capture failed') WHERE id = $1",
            row_id,
        )
        raise

    updated = await pool.fetchrow(
        "UPDATE procedure_submissions SET procedure_row_id = $2 WHERE id = $1 RETURNING *",
        row_id, captured["id"],
    )
    return dict(updated)


async def review_procedure_submission(
    pool: asyncpg.Pool, *, submission_id: str, decision: str, actor_subject: str, note: Optional[str] = None,
) -> dict[str, Any]:
    if decision not in ("accepted", "rejected", "needs_review"):
        raise ValueError("decision must be 'accepted', 'rejected' or 'needs_review'")

    submission = await get_procedure_submission(pool, submission_id)
    if submission is None:
        raise ValueError(f"procedure submission {submission_id} not found")
    if decision == "accepted" and not submission.get("procedure_row_id"):
        raise ValueError("cannot accept a submission whose procedure was never captured (procedure_row_id is null)")

    # Do the hard-to-reverse part FIRST: if it fails, the submission stays
    # in its prior status (never "accepted" without its solution
    # association and reward). Both associate_solution and the reward
    # ledger are safe to retry (ON CONFLICT DO UPDATE / idempotent unique
    # constraints), so a reviewer can just call this again on failure.
    if decision == "accepted" and submission.get("procedure_row_id"):
        await associate_solution(
            pool, goal_id=str(submission["goal_id"]), solution_type="procedure",
            target_id=str(submission["procedure_row_id"]), status="active",
            proposer=submission["submitted_by"], provenance=submission.get("provenance"),
            owner_id=submission["submitted_by"], scope_type=submission.get("scope_type"),
            scope_entity_id=submission.get("scope_entity_id"),
        )
        if submission["submission_type"] == "new":
            await credits_service.reward_new_procedure(pool, submission=submission)
        else:
            await credits_service.reward_improvement(pool, submission=submission)

    row = await pool.fetchrow(
        """
        UPDATE procedure_submissions
        SET status = $2, status_reason = COALESCE($3, status_reason),
            reviewed_by = CASE WHEN $2 IN ('accepted','rejected') THEN $4 ELSE reviewed_by END,
            reviewed_at = CASE WHEN $2 IN ('accepted','rejected') THEN now() ELSE reviewed_at END
        WHERE id = $1
        RETURNING *
        """,
        submission_id, decision, note, actor_subject,
    )
    return dict(row)


async def get_procedure_submission(pool: asyncpg.Pool, submission_id: str) -> Optional[dict[str, Any]]:
    row = await pool.fetchrow("SELECT * FROM procedure_submissions WHERE id = $1", submission_id)
    return dict(row) if row else None


async def list_procedure_submissions(pool: asyncpg.Pool, *, goal_id: Optional[str] = None, status: Optional[str] = None, submitted_by: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    clauses, params = [], []
    for col, val in (("goal_id", goal_id), ("status", status), ("submitted_by", submitted_by)):
        if val is not None:
            params.append(val)
            clauses.append(f"{col} = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = await pool.fetch(f"SELECT * FROM procedure_submissions {where} ORDER BY created_at DESC LIMIT ${len(params)}", *params)  # noqa: S608 -- columns/values are whitelisted above
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Benchmark submissions (§2)
# ---------------------------------------------------------------------------
async def create_benchmark_submission(
    pool: asyncpg.Pool, *, goal_id: str, name: str, actor_subject: str, description: Optional[str] = None,
    success_criteria: Optional[dict] = None, invariants: Optional[list] = None,
    verification_method: Optional[dict] = None, provenance: Optional[str] = None,
    scope_type: Optional[str] = None, scope_entity_id: Optional[str] = None, visibility: str = "public",
    embedder: Optional[Embedder] = None, llm_evaluator=None,
) -> dict[str, Any]:
    goal = await get_goal_for_product(pool, goal_id, scope=AccessScope.unrestricted())
    if goal is None:
        raise ValueError(f"goal {goal_id} not found")

    validate_provenance(provenance)
    resolved_scope_type, resolved_scope_entity_id = validate_scope(scope_type, scope_entity_id, allow_global_entity_id=bool(scope_type == "global" and scope_entity_id))

    layer1 = verification_service.evaluate_layer1(name=name, steps=[verification_method or {}], rationale=description)
    dedup_text = " ".join(part for part in (name, description or "", json.dumps(success_criteria or {})) if part)
    embedding = await embed_submission_text(embedder, dedup_text)
    duplicate = await score_benchmark_duplicate(pool, goal_id=goal_id, embedding=embedding)
    layer2 = await verification_service.evaluate_layer2(
        submission={"name": name, "rationale": description, "goal": goal},
        duplicate=duplicate, layer1=layer1, llm_evaluator=llm_evaluator,
    )
    status = "candidate" if layer2["decision"] == "candidate" else "needs_review"
    status_reason = "; ".join(layer2.get("notes", [])) if status == "needs_review" else None

    # §9: a Benchmark describes how the GOAL's success is evaluated -- it
    # must not become a Procedure quietly proving itself. Not an automatic
    # rejection (a contributor legitimately writing both a way and its
    # verification for a niche Goal is plausible), but always flagged for
    # review rather than silently auto-accepted later.
    conflicting_procedure = await pool.fetchval(
        "SELECT id FROM procedure_submissions WHERE goal_id = $1 AND submitted_by = $2 AND status <> 'rejected' LIMIT 1",
        goal_id, actor_subject,
    )
    if conflicting_procedure is not None:
        status = "needs_review"
        conflict_note = f"submitter also has a Procedure submission ({conflicting_procedure}) for this same Goal -- review for independence before acceptance"
        status_reason = f"{status_reason}; {conflict_note}" if status_reason else conflict_note

    # Materialized as a draft Benchmark immediately (mirrors create_benchmark's
    # own default status='draft') so it is inspectable during review; it is
    # NOT frozen/activated until accepted below.
    benchmark = await create_benchmark(
        pool, goal_id=goal_id, name=name, description=description,
        success_criteria=success_criteria, environment_specification={}, comparison_policy={},
        status="draft", provenance=provenance,
        metadata={"invariants": invariants or [], "verification_method": verification_method or {}, "submitted_by": actor_subject},
    )

    row_id = str(uuid7())
    row = await pool.fetchrow(
        """
        INSERT INTO benchmark_submissions (
            id, goal_id, benchmark_id, name, description, success_criteria, invariants, verification_method,
            status, status_reason, layer1_result, layer2_result, embedding, duplicate_of_submission_id, duplicate_score,
            submitted_by, provenance, scope_type, scope_entity_id, owner_id, visibility
        ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10,$11::jsonb,$12::jsonb,$13::vector,$14,$15,$16,$17,$18,$19,$20,$21)
        RETURNING *
        """,
        row_id, goal_id, benchmark["id"], name, description, json.dumps(success_criteria or {}),
        json.dumps(invariants or []), json.dumps(verification_method or {}),
        status, status_reason, json.dumps(layer1), json.dumps(layer2),
        to_pgvector(embedding) if embedding else None,
        duplicate.get("best_match_id") if duplicate.get("best_match_kind") == "submission" else None,
        duplicate.get("score"),
        actor_subject, provenance, resolved_scope_type, resolved_scope_entity_id, actor_subject, visibility,
    )
    return dict(row)


async def review_benchmark_submission(
    pool: asyncpg.Pool, *, submission_id: str, decision: str, actor_subject: str, note: Optional[str] = None,
) -> dict[str, Any]:
    if decision not in ("accepted", "rejected", "needs_review"):
        raise ValueError("decision must be 'accepted', 'rejected' or 'needs_review'")

    submission = await get_benchmark_submission(pool, submission_id)
    if submission is None:
        raise ValueError(f"benchmark submission {submission_id} not found")

    if decision == "accepted" and submission.get("benchmark_id"):
        await pool.execute("UPDATE benchmarks SET status = 'active' WHERE id = $1 AND status = 'draft'", submission["benchmark_id"])

    row = await pool.fetchrow(
        """
        UPDATE benchmark_submissions
        SET status = $2, status_reason = COALESCE($3, status_reason),
            reviewed_by = CASE WHEN $2 IN ('accepted','rejected') THEN $4 ELSE reviewed_by END,
            reviewed_at = CASE WHEN $2 IN ('accepted','rejected') THEN now() ELSE reviewed_at END
        WHERE id = $1
        RETURNING *
        """,
        submission_id, decision, note, actor_subject,
    )
    return dict(row)


async def _benchmark_lifecycle(pool: asyncpg.Pool, submission: dict[str, Any]) -> dict[str, Any]:
    """
    §8: submitted -> evaluated -> candidate/needs_review/rejected ->
    accepted -> used -> validated. The first four are exactly
    `benchmark_submissions.status` plus the fact that layer1/layer2 always
    ran before a row exists at all (nothing here is a new state machine).
    `used`/`validated` are NOT new columns -- both are computed live from
    the existing `evaluations` table (already linked to a benchmark via
    `evaluations.benchmark_id`, migration 35), the same way
    app.services.contributors.contribution_counts computes its own metrics
    live rather than storing a second copy. "Accepted" never implies
    "used" or "validated" -- those require real recorded evaluations,
    exactly per the directive's own "accepted does not mean proven useful."
    """
    if not submission.get("benchmark_id"):
        return {"used": False, "validated": False, "evaluation_count": 0, "distinguishes_outcomes": False}
    rows = await pool.fetch(
        "SELECT aggregate_result FROM evaluations WHERE benchmark_id = $1 AND status = 'completed'",
        submission["benchmark_id"],
    )
    results = {r["aggregate_result"] for r in rows if r["aggregate_result"]}
    used = len(rows) > 0
    # "Validated" per §8: the benchmark has demonstrably distinguished a
    # successful outcome from an unsuccessful one -- not just been run.
    validated = "pass" in results and "fail" in results
    return {"used": used, "validated": validated, "evaluation_count": len(rows), "distinguishes_outcomes": validated}


async def get_benchmark_submission(pool: asyncpg.Pool, submission_id: str) -> Optional[dict[str, Any]]:
    row = await pool.fetchrow("SELECT * FROM benchmark_submissions WHERE id = $1", submission_id)
    if row is None:
        return None
    submission = dict(row)
    submission["lifecycle"] = await _benchmark_lifecycle(pool, submission)
    return submission


async def list_benchmark_submissions(pool: asyncpg.Pool, *, goal_id: Optional[str] = None, status: Optional[str] = None, submitted_by: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    clauses, params = [], []
    for col, val in (("goal_id", goal_id), ("status", status), ("submitted_by", submitted_by)):
        if val is not None:
            params.append(val)
            clauses.append(f"{col} = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = await pool.fetch(f"SELECT * FROM benchmark_submissions {where} ORDER BY created_at DESC LIMIT ${len(params)}", *params)  # noqa: S608
    return [dict(r) for r in rows]
