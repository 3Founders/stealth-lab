"""
Layer 1 (deterministic) and Layer 2 (LLM/heuristic) evaluation for a
contribution submission, plus usage-event recording -- the self-use vs
independent-reuse signal (§3, §5).

Layer 2 IS NOT wired to a live LLM call in V1. There is real LLM-provider
infrastructure elsewhere in this codebase
(app.services.semantic.providers.build_provider_chain), but wiring a new
judge prompt/schema for submission review is real, non-trivial work this
migration does not attempt to fake. Instead `evaluate_layer2` runs an
honest, clearly-labelled heuristic (`method: "heuristic_v1"`) that can only
ever recommend reject / candidate / needs_review -- never "accepted" (that
stays a human decision, §3) -- and accepts an optional injected
`llm_evaluator` callable so a real judge can be dropped in later without
changing any caller.

HARDENING PASS (audit findings D2/D3/D12/D13): `record_usage_event` no
longer accepts `outcome_state`/`verification_layer`/`executed_by` as
trusted client input. The caller supplies only `executor_subject` (the
server-derived authenticated principal -- the router, never the request
body, is responsible for this) and, optionally, `execution_run_id` +
`evidence_id`. When both are given, `_verify_execution_chain` establishes
-- from the real, persisted `execution_runs`/`evidence`/`procedures` rows,
not from anything the client asserts -- that:
  - the execution run reached a real terminal outcome,
  - it was created by the same authenticated executor now reporting it,
  - it corresponds to the exact Procedure version referenced,
  - the evidence belongs to that same Procedure version,
  - the evidence was written by the trusted execution-outcome writer
    (`procedures.OUTCOME_WRITER_STAMP` -- a fixed system constant no
    client-facing endpoint can set; see app/services/procedures.py's
    `record_execution_outcome`, the only writer of execution_result
    evidence),
  - the evidence and the run agree on what actually happened.
Only then does the event become `verified_success`/`verified_failure` at
verification_layer 3. Any mismatch raises ValueError (the router turns
this into a 422, never a silent downgrade) -- "reject mismatches, don't
accept and trust," per the hardening spec.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

import asyncpg

from app.economy import constants as c
from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.procedures import OUTCOME_WRITER_STAMP
from app.utils.ids import uuid7

LLMEvaluator = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def evaluate_layer1(
    *,
    name: str,
    steps: Optional[list],
    rationale: Optional[str],
    preconditions: Optional[list] = None,
    expected_outcome: Optional[dict] = None,
) -> dict[str, Any]:
    """Deterministic/schema checks only -- no network call, no judgment call."""
    issues: list[str] = []
    if not name or not name.strip():
        issues.append("missing_name")
    if not steps or len(steps) < c.MIN_STEPS_FOR_CANDIDATE:
        issues.append("missing_steps")
    if not rationale or not rationale.strip():
        issues.append("missing_rationale")
    if preconditions is not None and (not isinstance(preconditions, list) or not preconditions):
        issues.append("missing_preconditions")
    if expected_outcome is not None and (not isinstance(expected_outcome, dict) or not expected_outcome):
        issues.append("missing_expected_outcome")
    return {"passed": not issues, "issues": issues, "method": "deterministic_v1"}


async def evaluate_layer2(
    *, submission: dict[str, Any], duplicate: dict[str, Any], layer1: dict[str, Any],
    parent_similarity: Optional[dict[str, Any]] = None,
    llm_evaluator: Optional[LLMEvaluator] = None,
) -> dict[str, Any]:
    """
    Returns {"decision": "reject"|"candidate"|"needs_review", "notes": [...],
    "method": "llm"|"heuristic_v1"}. `decision` never includes "accepted" --
    per the founder directive, LLM evaluation alone must not create strong
    reward or verified success; only a human reviewer (Layer 5, via
    submissions.review_*) can accept a submission.

    `parent_similarity` (§3 of the harden+consolidate directive), when
    given, is the submission's cosine similarity against its DECLARED
    parent specifically (app.economy.duplicates.score_against_parent) --
    a near-identical rewrite of the parent is flagged here even if it
    happens not to be the single nearest match in the whole goal's corpus
    (score_procedure_duplicate's broader scan). This is what actually
    distinguishes "meaningful improvement" from "superficial paraphrase":
    the comparison a reviewer needs is against the thing being improved,
    not the nearest neighbour in general.
    """
    if llm_evaluator is not None:
        try:
            result = await llm_evaluator({"submission": submission, "duplicate": duplicate, "layer1": layer1, "parent_similarity": parent_similarity})
            decision = result.get("decision")
            if decision in ("reject", "candidate", "needs_review"):
                return {"decision": decision, "notes": result.get("notes", []), "method": "llm"}
        except Exception as exc:  # noqa: BLE001 -- LLM failure must degrade, not crash the submission
            return {"decision": "needs_review", "notes": [f"llm_evaluator_failed: {exc}"], "method": "heuristic_v1"}

    notes: list[str] = []
    if not layer1.get("passed", True):
        return {"decision": "needs_review", "notes": layer1.get("issues", []), "method": "heuristic_v1"}

    parent_score = (parent_similarity or {}).get("score")
    if parent_score is not None:
        if parent_score >= c.DUPLICATE_REJECT_THRESHOLD:
            return {"decision": "reject", "notes": [f"near-identical to its declared parent (similarity {parent_score:.3f}) -- not a substantive improvement"], "method": "heuristic_v1"}
        if parent_score >= c.DUPLICATE_REVIEW_THRESHOLD:
            notes.append(f"very similar to its declared parent (similarity {parent_score:.3f}) -- reviewer should confirm a substantive change (mechanism, applicability, constraints, correctness, performance, edge cases, or verification)")

    score = duplicate.get("score")
    if score is not None:
        if score >= c.DUPLICATE_REJECT_THRESHOLD:
            return {"decision": "reject", "notes": [f"near-identical to existing {duplicate.get('best_match_kind')} (similarity {score:.3f})"], "method": "heuristic_v1"}
        if score >= c.DUPLICATE_REVIEW_THRESHOLD:
            notes.append(f"possible duplicate of existing {duplicate.get('best_match_kind')} (similarity {score:.3f})")

    if notes:
        return {"decision": "needs_review", "notes": notes, "method": "heuristic_v1"}
    return {"decision": "candidate", "notes": notes, "method": "heuristic_v1"}


class VerificationMismatch(ValueError):
    """Raised when a client-supplied execution_run_id/evidence_id does not
    establish the verified outcome it implies. Never silently downgraded."""


async def _verify_execution_chain(
    pool: asyncpg.Pool, *, procedure_row_id: str, executor_subject: str,
    execution_run_id: str, evidence_id: str,
) -> tuple[str, int]:
    """
    Establishes a verified outcome from real, persisted rows -- never from
    anything the client merely asserts. Returns
    (outcome_state, verification_layer); raises VerificationMismatch on any
    inconsistency. See module docstring for the exact chain.
    """
    proc = await pool.fetchrow("SELECT procedure_id, version FROM procedures WHERE id = $1", procedure_row_id)
    if proc is None:
        raise VerificationMismatch(f"procedure {procedure_row_id} not found")

    run = await pool.fetchrow(
        "SELECT id, procedure_id, procedure_version, status, final_outcome, created_by "
        "FROM execution_runs WHERE id = $1",
        execution_run_id,
    )
    if run is None:
        raise VerificationMismatch(f"execution_run {execution_run_id} not found")
    if run["status"] not in ("succeeded", "failed") or run["final_outcome"] is None:
        raise VerificationMismatch(f"execution_run {execution_run_id} has not reached a terminal outcome")
    if run["created_by"] != executor_subject:
        raise VerificationMismatch("execution_run does not belong to the authenticated executor")
    if str(run["procedure_id"]) != str(proc["procedure_id"]) or run["procedure_version"] != proc["version"]:
        raise VerificationMismatch("execution_run does not correspond to the specified procedure version")

    ev = await pool.fetchrow(
        "SELECT id, target_type, target_id, target_version, outcome_status, created_by "
        "FROM evidence WHERE id = $1",
        evidence_id,
    )
    if ev is None:
        raise VerificationMismatch(f"evidence {evidence_id} not found")
    if ev["target_type"] != "procedure" or str(ev["target_id"]) != str(procedure_row_id) or ev["target_version"] != proc["version"]:
        raise VerificationMismatch("evidence does not belong to the specified procedure version")
    if ev["outcome_status"] not in ("success", "failure"):
        raise VerificationMismatch("evidence has no terminal outcome to verify against")
    if ev["created_by"] != OUTCOME_WRITER_STAMP:
        # This is the load-bearing check: only evidence written by the
        # trusted internal execution pipeline (a fixed constant no
        # client-facing endpoint can set as `created_by`) counts as real
        # verification. A user cannot manufacture this evidence row
        # themselves -- there is no public API that writes `evidence` with
        # this `created_by` value.
        raise VerificationMismatch("evidence was not produced by the trusted execution-outcome writer")
    if ev["outcome_status"] != run["final_outcome"]:
        raise VerificationMismatch("evidence outcome does not agree with the execution run's own recorded outcome")

    outcome_state = "verified_success" if ev["outcome_status"] == "success" else "verified_failure"
    return outcome_state, c.LAYER_EXECUTION


async def record_usage_event(
    pool: asyncpg.Pool, *, procedure_row_id: str, executor_subject: str,
    execution_run_id: Optional[str] = None, evidence_id: Optional[str] = None,
    benchmark_id: Optional[str] = None, context_key: Optional[str] = None,
    metadata: Optional[dict] = None,
    access_scope: AccessScope = AccessScope.unrestricted(),
    tenant_scope: Optional[TenantScope] = None,
) -> Optional[dict[str, Any]]:
    """
    Records one real execution of a Procedure. Never called for a view,
    copy or bookmark (§5) -- the caller is the execution/run pathway, not
    the read API. `executor_subject` MUST be the server-derived
    authenticated principal (see app/api/economy.py) -- this function does
    not accept a client-supplied executor identity. `contributor_id`/
    `is_self_use` are derived here from the live procedure row, so neither
    side of the self-use comparison is client-controlled.

    `outcome_state`/`verification_layer` are computed here, never accepted
    as input -- see `_verify_execution_chain`. Raises VerificationMismatch
    (a ValueError subclass; the router maps it to 422) if
    execution_run_id/evidence_id are given but don't establish what they
    claim.
    """
    access_scope = access_scope or AccessScope.unrestricted()
    visibility_sql, visibility_params, _ = scope_predicates(
        access_scope, tenant_scope or TenantScope.unrestricted(), alias="p", param_index=2
    )
    proc = await pool.fetchrow(
        f"SELECT p.procedure_id, p.created_by, p.achieves_goal_id FROM procedures p "
        f"WHERE p.id = $1 AND p.t_invalid IS NULL AND {visibility_sql}",
        procedure_row_id,
        *visibility_params,
    )
    if proc is None:
        return None
    contributor_id = proc["created_by"] or "unknown"
    is_self_use = executor_subject == contributor_id

    outcome_state, verification_layer = "unknown", c.LAYER_NONE
    if execution_run_id or evidence_id:
        if not (execution_run_id and evidence_id):
            raise VerificationMismatch("a verified outcome requires both execution_run_id and evidence_id")
        outcome_state, verification_layer = await _verify_execution_chain(
            pool, procedure_row_id=procedure_row_id, executor_subject=executor_subject,
            execution_run_id=execution_run_id, evidence_id=evidence_id,
        )

    goal_id = proc["achieves_goal_id"] if "achieves_goal_id" in proc else None
    if goal_id is not None:
        goal_id = str(goal_id)
    if benchmark_id is not None:
        benchmark_goal_id = await pool.fetchval(
            "SELECT goal_id FROM benchmarks WHERE id = $1", benchmark_id
        )
        if benchmark_goal_id is not None:
            benchmark_goal_id = str(benchmark_goal_id)
            if goal_id is not None and goal_id != benchmark_goal_id:
                raise VerificationMismatch("benchmark does not belong to the procedure's Goal")
            goal_id = benchmark_goal_id
    if goal_id is None:
        goal_id = await pool.fetchval(
            "SELECT goal_id FROM solutions WHERE target_id = $1 "
            "AND target_table = 'procedures' AND status = 'active' LIMIT 1",
            str(proc["procedure_id"]),
        )
        if goal_id is not None:
            goal_id = str(goal_id)
    if goal_id is None:
        goal_id = await pool.fetchval(
            "SELECT goal_id FROM solutions WHERE target_id = $1 "
            "AND target_table = 'procedures' AND status = 'active' LIMIT 1",
            procedure_row_id,
        )
        if goal_id is not None:
            goal_id = str(goal_id)

    row_id = str(uuid7())
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO procedure_usage_events (
                id, procedure_row_id, procedure_id, goal_id, benchmark_id,
                executed_by, contributor_id, is_self_use,
                outcome_state, verification_layer, execution_run_id, evidence_id,
                context_key, metadata
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb)
            RETURNING *
            """,
            row_id, procedure_row_id, proc["procedure_id"], goal_id, benchmark_id,
            executor_subject, contributor_id, is_self_use,
            outcome_state, verification_layer, execution_run_id, evidence_id,
            context_key, json.dumps(metadata or {}),
        )
    except asyncpg.exceptions.UniqueViolationError:
        # The same evidence/execution_run already backs a usage event
        # (uq_procedure_usage_events_evidence / _execution_run, migration
        # 103) -- idempotent: return the existing event rather than
        # creating a second one or erroring the retry.
        existing = await pool.fetchrow(
            "SELECT * FROM procedure_usage_events WHERE evidence_id = $1 OR execution_run_id = $2 "
            "ORDER BY created_at ASC LIMIT 1",
            evidence_id, execution_run_id,
        )
        return dict(existing) if existing else None
    return dict(row)
