"""
Claim-conditioned NLI/JEV retrieval orchestrator: the second-stage
contextual-reasoning layer over `applicability.py`'s existing broad
retrieval. Per the product spec: "NLI/JEV must NOT replace broad
retrieval. It is a second-stage contextual reasoning layer."

PIPELINE (reuses, does not duplicate, every existing stage):
    find_applicable_procedures()          [applicability.py -- UNCHANGED]
        hard cascade (disqualify) -> RRF(similarity, capability) survivors
    -> top-N survivors
    -> get_relevant_claims() per survivor  [relevant_claims.py -- UNCHANGED]
    -> ApplicabilityJudge.judge_batch()    [applicability_judge.py]
    -> hard filter: reject only REQUIRED conditions strongly contradicted
    -> rerank: component scores -> final_policy_score
    -> top-k + explanation (supporting/blocking claim ids)

The existing hard-constraint cascade (temporal validity, staleness,
availability, verification/approval, scope/exclusions, structured
preconditions, invariants) is NEVER touched or re-implemented here -- this
module only ever narrows or reorders its SURVIVORS. A candidate the cascade
already disqualified never reaches this module at all.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import asyncpg

from app.services.access import AccessScope
from app.services.applicability import _capability_ranked_hits, find_applicable_procedures
from app.services.applicability_judge import (
    ApplicabilityJudge,
    ApplicabilityJudgment,
    DEFAULT_CLAIMS_PER_CANDIDATE_MAX,
    DEFAULT_CLAIMS_PER_CANDIDATE_MIN,
    DEFAULT_HARD_REJECT_CONTRADICTION_THRESHOLD,
    DEFAULT_JUDGE_BATCH_SIZE,
    JudgeCandidateInput,
    extract_requirement_conditions,
    get_cached_judgment,
    put_cached_judgment,
    stable_claim_ids_hash,
    stable_goal_hash,
)
from app.services.relevant_claims import get_relevant_claims

log = logging.getLogger(__name__)


@dataclass
class RankedCandidate:
    """Per-candidate result: the underlying procedure row, its judgment,
    and EXPLICIT component scores (product spec Sec 9: "Do NOT collapse
    everything into one opaque permanent score. Return component scores.").
    `final_policy_score` is deliberately the only derived/opinionated
    field -- everything else is a plain, independently-inspectable number."""
    procedure: dict
    judgment: Optional[ApplicabilityJudgment]
    semantic_relevance: Optional[float]
    claim_fit: Optional[float]
    evidence_strength: Optional[float]
    verified_success: Optional[float]
    cost_estimate: Optional[float]
    latency_estimate: Optional[float]
    risk: Optional[float]
    final_policy_score: float

    def explanation(self) -> dict:
        j = self.judgment
        return {
            "procedure_id": str(self.procedure.get("procedure_id")),
            "id": str(self.procedure.get("id")),
            "name": self.procedure.get("name"),
            "verdict": j.verdict if j else "UNKNOWN",
            "supporting_claim_ids": j.supporting_claim_ids if j else [],
            "blocking_claim_ids": j.blocking_claim_ids if j else [],
            "unknown_requirements": j.unknown_requirements if j else [],
            "reason": j.reason if j else "no judgment available",
            "scores": {
                "semantic_relevance": self.semantic_relevance,
                "claim_fit": self.claim_fit,
                "evidence_strength": self.evidence_strength,
                "verified_success": self.verified_success,
                "cost_estimate": self.cost_estimate,
                "latency_estimate": self.latency_estimate,
                "risk": self.risk,
                "final_policy_score": self.final_policy_score,
            },
        }


@dataclass
class ObservabilityCounters:
    """Sec 18's tracked signals, returned to the caller rather than pushed
    into a new telemetry system -- this repo has none, and adding one is
    out of scope for this pass."""
    candidates_before_filter: int = 0
    candidates_after_filter: int = 0
    claims_retrieved_total: int = 0
    judge_latency_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    verdict_counts: dict = field(default_factory=dict)
    provider: str = ""
    prompt_version: str = ""


@dataclass
class ClaimConditionedResult:
    candidates: list[RankedCandidate]
    contextual_judgment_status: str  # "ok" | "unavailable"
    observability: ObservabilityCounters


def compute_policy_score(candidate: RankedCandidate, *, weights: Optional[dict] = None) -> float:
    """One small, pure, independently-testable/swappable function (per the
    plan's own Rule-6-style reasoning: fuse_rrf already owns rank fusion of
    ORDINAL signals upstream; these are calibrated [0,1] probabilities/
    estimates, not ranks, so a second rank-fusion call would silently
    discard their magnitude). `weights` lets a caller define a different
    policy without touching this pipeline's other stages."""
    w = weights or {
        "semantic_relevance": 0.25, "claim_fit": 0.30, "evidence_strength": 0.20,
        "verified_success": 0.15, "risk": -0.10,
    }
    total = 0.0
    total += w.get("semantic_relevance", 0.0) * (candidate.semantic_relevance or 0.0)
    total += w.get("claim_fit", 0.0) * (candidate.claim_fit or 0.0)
    total += w.get("evidence_strength", 0.0) * (candidate.evidence_strength or 0.0)
    total += w.get("verified_success", 0.0) * (candidate.verified_success or 0.0)
    total += w.get("risk", 0.0) * (candidate.risk or 0.0)
    return total


def _survivor_to_judge_candidate(
    procedure: dict, claims: list[dict],
) -> JudgeCandidateInput:
    conditions = extract_requirement_conditions(procedure)
    return JudgeCandidateInput(
        candidate_id=str(procedure["id"]),
        candidate_version=procedure.get("version"),
        candidate_purpose=str(procedure.get("goal") or procedure.get("name") or ""),
        conditions=conditions,
        claims=claims,
    )


def _is_hard_rejected(
    judgment: ApplicabilityJudgment, conditions: list, *, threshold: float,
) -> bool:
    """Sec 8's hard-reject rule, and ONLY this rule: a REQUIRED condition
    strongly contradicted. UNKNOWN never rejects (Sec 3); PREFERRED/
    SATISFIABLE/IMPLEMENTATION_BINDING/TARGET_STATE contradictions demote
    ranking (via `risk`) but never disqualify."""
    if judgment.verdict != "INAPPLICABLE":
        return False
    has_required = any(c.kind == "REQUIRED" for c in conditions)
    return has_required and judgment.contradiction_probability >= threshold


async def find_applicable_candidates(
    pool: asyncpg.Pool,
    *,
    goal_text: str,
    goal_embedding: Optional[list[float]] = None,
    judge: Optional[ApplicabilityJudge] = None,
    current_scope: Optional[dict] = None,
    access_scope: Optional[AccessScope] = None,
    require_verified: bool = True,
    limit: int = 10,
    candidate_pool_size: int = 200,
    survivor_fanout: int = 30,
    claims_per_candidate_min: int = DEFAULT_CLAIMS_PER_CANDIDATE_MIN,
    claims_per_candidate_max: int = DEFAULT_CLAIMS_PER_CANDIDATE_MAX,
    hard_reject_contradiction_threshold: float = DEFAULT_HARD_REJECT_CONTRADICTION_THRESHOLD,
    judge_batch_size: int = DEFAULT_JUDGE_BATCH_SIZE,
    invariant_bindings: Optional[dict[str, float]] = None,
    embedding_model_id: Optional[str] = None,
    excluded_procedure_ids: Optional[list[str]] = None,
    use_cache: bool = True,
) -> ClaimConditionedResult:
    """The full pipeline described in this module's docstring.

    `judge=None` is a legitimate, first-class input -- not an error -- and
    is treated identically to a judge that raised on every call (Sec 19):
    hard-cascade survivors are returned unranked-by-claims, in their
    existing find_applicable_procedures() order, with
    contextual_judgment_status='unavailable'. Retrieval never breaks.
    """
    counters = ObservabilityCounters(provider=judge.__class__.__name__ if judge else "none")

    survivors = await find_applicable_procedures(
        pool, goal_embedding=goal_embedding, current_scope=current_scope,
        access_scope=access_scope, require_verified=require_verified,
        limit=survivor_fanout, candidate_pool_size=candidate_pool_size,
        invariant_bindings=invariant_bindings, embedding_model_id=embedding_model_id,
        goal_text=goal_text, excluded_procedure_ids=excluded_procedure_ids,
    )
    counters.candidates_before_filter = len(survivors)

    if not survivors:
        return ClaimConditionedResult([], "ok" if judge is not None else "unavailable", counters)

    if judge is None:
        ranked = [
            RankedCandidate(
                procedure=p, judgment=None,
                semantic_relevance=p.get("_similarity_score"), claim_fit=None,
                evidence_strength=None, verified_success=None,
                cost_estimate=None, latency_estimate=None, risk=None,
                final_policy_score=p.get("_similarity_score") or 0.0,
            )
            for p in survivors[:limit]
        ]
        counters.candidates_after_filter = len(ranked)
        return ClaimConditionedResult(ranked, "unavailable", counters)

    # Per-candidate bounded Claim retrieval (Sec 4) -- reuses
    # relevant_claims.py::get_relevant_claims verbatim, narrowed by this
    # candidate's own goal + requirement text, never the whole Claim graph.
    per_candidate_claims: dict[str, list[dict]] = {}
    for procedure in survivors:
        conditions = extract_requirement_conditions(procedure)
        condition_text = " ".join(c.text for c in conditions)
        query = f"{goal_text}\n{procedure.get('goal') or ''}\n{condition_text}".strip()
        claims = await get_relevant_claims(
            pool, goal=query, top_k=claims_per_candidate_max, access_scope=access_scope,
        )
        per_candidate_claims[str(procedure["id"])] = claims
        counters.claims_retrieved_total += len(claims)

    goal_hash = stable_goal_hash(goal_text)
    judge_model = getattr(judge, "model", judge.__class__.__name__)
    judge_model_version = getattr(judge, "model_version", "v1")
    from app.services.applicability_judge import PROMPT_VERSION
    counters.prompt_version = PROMPT_VERSION

    judgments: dict[str, ApplicabilityJudgment] = {}
    to_judge: list[JudgeCandidateInput] = []
    for procedure in survivors:
        pid = str(procedure["id"])
        claims = per_candidate_claims[pid]
        claim_ids_hash = stable_claim_ids_hash(claims)
        cached = None
        if use_cache and hasattr(pool, "fetchrow"):
            try:
                cached = await get_cached_judgment(
                    pool, goal_hash=goal_hash, candidate_id=pid,
                    candidate_version=procedure.get("version"), claim_ids_hash=claim_ids_hash,
                    model=judge_model, model_version=judge_model_version, prompt_version=PROMPT_VERSION,
                )
            except Exception:  # noqa: BLE001 -- a cache-layer failure must not break judgment, only skip caching
                log.warning("claim_conditioned_retrieval: cache lookup failed, judging fresh", exc_info=True)
        if cached is not None:
            counters.cache_hits += 1
            judgments[pid] = cached
        else:
            counters.cache_misses += 1
            to_judge.append(_survivor_to_judge_candidate(procedure, claims))

    contextual_judgment_status = "ok"
    if to_judge:
        start = time.monotonic()
        try:
            for i in range(0, len(to_judge), judge_batch_size):
                batch = to_judge[i : i + judge_batch_size]
                batch_judgments = await judge.judge_batch(goal_text, batch)
                for jc, jg in zip(batch, batch_judgments):
                    judgments[jc.candidate_id] = jg
                    if use_cache and hasattr(pool, "execute"):
                        claims = per_candidate_claims[jc.candidate_id]
                        try:
                            await put_cached_judgment(
                                pool, goal_hash=goal_hash, candidate_id=jc.candidate_id,
                                candidate_version=jc.candidate_version,
                                claim_ids_hash=stable_claim_ids_hash(claims), judgment=jg,
                            )
                        except Exception:  # noqa: BLE001 -- caching is best-effort, never fatal
                            log.warning("claim_conditioned_retrieval: cache write failed", exc_info=True)
        except Exception:  # noqa: BLE001 -- Sec 19: a judge failure must never break retrieval
            log.warning("claim_conditioned_retrieval: judge unavailable, falling back", exc_info=True)
            contextual_judgment_status = "unavailable"
        finally:
            counters.judge_latency_ms += (time.monotonic() - start) * 1000

    survivor_ids = {str(p["id"]) for p in survivors}
    unjudged = survivor_ids - set(judgments.keys())
    if unjudged:
        contextual_judgment_status = "unavailable"
        for procedure in survivors:
            pid = str(procedure["id"])
            if pid in unjudged:
                conditions = extract_requirement_conditions(procedure)
                judgments[pid] = ApplicabilityJudgment(
                    candidate_id=pid, goal_or_query=goal_text,
                    applicability_probability=0.5, contradiction_probability=0.0,
                    preconditions_met_probability=0.5, verdict="UNKNOWN",
                    unknown_requirements=[c.text for c in conditions],
                    reason="judge unavailable for this candidate",
                    model=judge_model, model_version=judge_model_version,
                )

    # Hard filter -- REQUIRED-condition strong contradictions only (Sec 8).
    survived_filter = []
    for procedure in survivors:
        pid = str(procedure["id"])
        judgment = judgments[pid]
        conditions = extract_requirement_conditions(procedure)
        counters.verdict_counts[judgment.verdict] = counters.verdict_counts.get(judgment.verdict, 0) + 1
        if _is_hard_rejected(judgment, conditions, threshold=hard_reject_contradiction_threshold):
            continue
        survived_filter.append(procedure)

    # Rerank: component scores, reusing the SAME capability signal
    # applicability.py's own _capability_ranked_hits computes (Sec 9:
    # "historical verified success" -- not a second capability computation).
    capability_hits = await _capability_ranked_hits(pool, survived_filter)
    capability_rank = {str(pid): i for pid, _table, i in capability_hits}
    capability_score_by_id = {
        pid: (1.0 - (rank / max(1, len(capability_hits) - 1))) if len(capability_hits) > 1 else 1.0
        for pid, rank in capability_rank.items()
    }

    ranked: list[RankedCandidate] = []
    for procedure in survived_filter:
        pid = str(procedure["id"])
        judgment = judgments[pid]
        candidate = RankedCandidate(
            procedure=procedure, judgment=judgment,
            semantic_relevance=procedure.get("_similarity_score"),
            claim_fit=judgment.applicability_probability,
            evidence_strength=capability_score_by_id.get(pid),
            verified_success=capability_score_by_id.get(pid),
            cost_estimate=None, latency_estimate=None,
            risk=judgment.contradiction_probability,
            final_policy_score=0.0,
        )
        candidate.final_policy_score = compute_policy_score(candidate)
        ranked.append(candidate)

    ranked.sort(key=lambda c: c.final_policy_score, reverse=True)
    ranked = ranked[:limit]
    counters.candidates_after_filter = len(ranked)
    return ClaimConditionedResult(ranked, contextual_judgment_status, counters)
