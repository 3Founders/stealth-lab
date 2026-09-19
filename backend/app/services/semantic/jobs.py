"""
Requeue of semantic work on the EXISTING `ingestion_jobs` queue (no second
scheduler). Two job types:

    semantic_judgment  a claim-conditioned applicability judgment that no
                       provider could serve. The handler re-runs the chain and
                       writes each judgment into `applicability_judgment_cache`,
                       so the caller's next find_applicable_candidates() is a
                       cache hit and completes. NEVER computes a fallback verdict.
    compact_context    a compaction that was skipped because no provider was
                       available (context was retained untouched).

Round accounting: the interactive attempt is round 0. Each job row is one
round; on failure the handler enqueues round+1 (with `run_after` backoff) until
`semantic_job_max_retries`, then raises -> the row ends `failed` with
last_error SEMANTIC_JUDGMENT_UNAVAILABLE. All rows of one request share
`root_job_id`; the newest row is the request's state.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.services.semantic.errors import SemanticJudgmentUnavailable
from app.services.semantic.policy import METRICS, RetryPolicy, policy_from_settings

log = logging.getLogger(__name__)

SEMANTIC_JUDGMENT_JOB = "semantic_judgment"
COMPACT_CONTEXT_JOB = "compact_context"

PENDING_SEMANTIC_JUDGMENT = "PENDING_SEMANTIC_JUDGMENT"
SEMANTIC_JUDGMENT_UNAVAILABLE = "SEMANTIC_JUDGMENT_UNAVAILABLE"
SEMANTIC_JUDGMENT_OK = "OK"


async def enqueue_semantic_job(
    pool: Any, job_type: str, payload: dict, *, dedup_key: str,
    delay_s: float = 0.0, round_no: int = 1, root_job_id: Optional[int] = None,
) -> int:
    """Insert one queue row, deduplicated on `dedup_key` while a copy is still
    pending/processing (repeated interactive calls must not stack jobs)."""
    existing = await pool.fetchval(
        "SELECT id FROM ingestion_jobs WHERE job_type = $1 AND status IN ('pending','processing') "
        "AND payload->>'dedup_key' = $2 ORDER BY id LIMIT 1",
        job_type, dedup_key,
    )
    if existing is not None:
        return int(existing)
    body = {**payload, "dedup_key": dedup_key, "round": round_no}
    if root_job_id is not None:
        body["root_job_id"] = root_job_id
    job_id = await pool.fetchval(
        "INSERT INTO ingestion_jobs (job_type, payload, run_after) "
        "VALUES ($1, $2::jsonb, now() + ($3 || ' seconds')::interval) RETURNING id",
        job_type, json.dumps(body, default=str), str(max(0.0, delay_s)),
    )
    if root_job_id is None:  # first round is its own root
        await pool.execute(
            "UPDATE ingestion_jobs SET payload = payload || jsonb_build_object('root_job_id', id) WHERE id = $1",
            job_id,
        )
    METRICS.inc("semantic.requeues")
    METRICS.event("requeue", job_type=job_type, job_id=int(job_id), round=round_no)
    return int(job_id)


EXHAUSTED_COOLDOWN_MINUTES = 15


async def recently_exhausted(pool: Any, job_type: str, dedup_key: str) -> bool:
    """True when this exact request already exhausted its retry rounds within
    the cooldown -- callers report SEMANTIC_JUDGMENT_UNAVAILABLE instead of
    starting another round loop."""
    row = await pool.fetchval(
        "SELECT 1 FROM ingestion_jobs WHERE job_type = $1 AND status = 'failed' "
        "AND payload->>'dedup_key' LIKE $2 || '%' AND last_error LIKE '%' || $3 || '%' "
        "AND completed_at > now() - ($4 || ' minutes')::interval LIMIT 1",
        job_type, dedup_key, SEMANTIC_JUDGMENT_UNAVAILABLE, str(EXHAUSTED_COOLDOWN_MINUTES),
    )
    return row is not None


async def get_semantic_job_state(pool: Any, job_id: int) -> dict:
    """PENDING_SEMANTIC_JUDGMENT | OK | SEMANTIC_JUDGMENT_UNAVAILABLE for the
    request `job_id` belongs to (looks at its newest round)."""
    row = await pool.fetchrow(
        "SELECT id, status, attempts, last_error, payload->>'round' AS round FROM ingestion_jobs "
        "WHERE id = $1 OR payload->>'root_job_id' = $1::text ORDER BY id DESC LIMIT 1",
        job_id,
    )
    if row is None:
        return {"state": None, "job_id": job_id}
    status = row["status"]
    state = (
        PENDING_SEMANTIC_JUDGMENT if status in ("pending", "processing")
        else SEMANTIC_JUDGMENT_OK if status == "done"
        else SEMANTIC_JUDGMENT_UNAVAILABLE
    )
    return {"state": state, "job_id": job_id, "latest_job_id": int(row["id"]),
            "round": int(row["round"] or 1), "last_error": row["last_error"]}


async def _requeue_or_exhaust(pool: Any, job_type: str, payload: dict, policy: RetryPolicy, reason: str) -> None:
    round_no = int(payload.get("round", 1))
    if round_no >= policy.job_max_retries:
        METRICS.inc("semantic.retry_exhausted")
        METRICS.event("retry_exhausted", job_type=job_type, round=round_no)
        raise SemanticJudgmentUnavailable(f"{SEMANTIC_JUDGMENT_UNAVAILABLE}: {reason} after {round_no} rounds")
    base = {k: v for k, v in payload.items() if k not in ("dedup_key", "round")}
    await enqueue_semantic_job(
        pool, job_type, base, dedup_key=payload["dedup_key"] + f"#r{round_no + 1}",
        delay_s=policy.requeue_delay(round_no), round_no=round_no + 1,
        root_job_id=payload.get("root_job_id"),
    )


# ------------------------------------------------------------- applicability


def candidate_to_payload(c: Any) -> dict:
    return {
        "candidate_id": c.candidate_id, "candidate_version": c.candidate_version,
        "candidate_purpose": c.candidate_purpose,
        "conditions": [{"text": x.text, "kind": x.kind, "claim_id": x.claim_id} for x in c.conditions],
        "claims": c.claims,
    }


def candidate_from_payload(d: dict) -> Any:
    from app.services.applicability_judge import JudgeCandidateInput, RequirementCondition

    return JudgeCandidateInput(
        candidate_id=d["candidate_id"], candidate_version=d.get("candidate_version"),
        candidate_purpose=d.get("candidate_purpose", ""),
        conditions=[RequirementCondition(text=x["text"], kind=x.get("kind", "REQUIRED"),
                                         claim_id=x.get("claim_id")) for x in d.get("conditions", [])],
        claims=d.get("claims", []),
    )


async def handle_semantic_judgment(pool: Any, payload: dict, *, judge: Any = None,
                                   policy: Optional[RetryPolicy] = None) -> None:
    from app.services.applicability_judge import (
        put_cached_judgment, stable_claim_ids_hash, stable_goal_hash,
    )
    from app.services.semantic.applicability import ChainedApplicabilityJudge

    policy = policy or policy_from_settings()
    judge = judge or ChainedApplicabilityJudge.from_settings()
    goal = payload["goal"]
    candidates = [candidate_from_payload(c) for c in payload["candidates"]]
    try:
        judgments = await judge.judge_batch(goal, candidates)
    except SemanticJudgmentUnavailable as exc:
        await _requeue_or_exhaust(pool, SEMANTIC_JUDGMENT_JOB, payload, policy, str(exc))
        return
    goal_hash = stable_goal_hash(goal)
    for cand, jg in zip(candidates, judgments):
        await put_cached_judgment(
            pool, goal_hash=goal_hash, candidate_id=cand.candidate_id,
            candidate_version=cand.candidate_version,
            claim_ids_hash=stable_claim_ids_hash(cand.claims), judgment=jg,
            cache_model=judge.model, cache_model_version=judge.model_version,
        )


# ---------------------------------------------------------------- compaction


async def handle_compact_context(pool: Any, payload: dict, *, judge: Any = None,
                                 policy: Optional[RetryPolicy] = None) -> None:
    from app.services.context_compaction.engine import run_compaction_job

    policy = policy or policy_from_settings()
    ok = await run_compaction_job(pool, payload, judge=judge)
    if not ok:
        await _requeue_or_exhaust(pool, COMPACT_CONTEXT_JOB, payload, policy, "compaction providers unavailable")


HANDLERS = {
    SEMANTIC_JUDGMENT_JOB: handle_semantic_judgment,
    COMPACT_CONTEXT_JOB: handle_compact_context,
}
