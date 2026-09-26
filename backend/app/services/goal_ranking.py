"""Central, explainable ranking for Procedures and Goals.

This module is the only implementation of the ranking statistics used by the
economy adapter.  It deliberately separates three ideas that are often mixed
in read models:

* eligibility is a hard fact, not a score;
* reliability is a posterior statistic over canonical outcome evidence; and
* opportunity/usefulness is a product of normalized signals with no fitted
  weights.

Procedure evidence is accepted only when ``app.services.evidence_trust``
classifies it as a verified success or verified failure.  The service also
pins evidence to the exact Procedure version row being scored.  A row can be
structurally valid and still be a cold-start candidate; it cannot acquire
quality from a self-report, a derivative marker, or an untrusted producer.

The Bayesian statistic is the lower endpoint of an equal-tailed 95% credible
interval for a Jeffreys Beta(1, 1) prior.  With ``s`` successes and ``f``
failures, the posterior is Beta(1+s, 1+f), and the lower endpoint is
``scipy.stats.beta.ppf(0.025, 1+s, 1+f)``.  Wilson values are retained beside
it for callers that still consume the older response fields; they are not the
ranking statistic.

All functions that do not need a database are pure.  The service methods only
read bounded candidate sets and keep the SQL aggregation on the database side
for Goal lists.  No Credits, Standing, submission, or provider data is used as
ranking evidence.  A reward ledger row is a reward, not a demand commitment;
it is inspected only as a diagnostic and never changes a factor.
"""
from __future__ import annotations

import inspect
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from uuid import UUID

import asyncpg
from scipy.stats import beta

from app.services import evidence_trust
from app.services.access import AccessScope, TenantScope, scope_predicates, visibility_predicate
from app.services.procedure_extraction.capability import wilson_interval
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
)

JEFFREYS_PRIOR_ALPHA = 1.0
JEFFREYS_PRIOR_BETA = 1.0
CREDIBLE_INTERVAL_MASS = 0.95
CREDIBLE_LOWER_QUANTILE = (1.0 - CREDIBLE_INTERVAL_MASS) / 2.0
CREDIBLE_UPPER_QUANTILE = 1.0 - CREDIBLE_LOWER_QUANTILE
MIN_TRUSTED_ATTEMPTS_FOR_CANDIDATE = 2
MAX_GOALS_PER_QUERY = 200

_CANONICAL_TRUST_STATES = frozenset({
    evidence_trust.VERIFIED_SUCCESS,
    evidence_trust.VERIFIED_FAILURE,
})
_CANONICAL_EVIDENCE_TYPES_SQL = ", ".join(
    f"'{value}'" for value in evidence_trust.OUTCOME_BEARING_EVIDENCE_TYPES
)
_TRUSTED_WRITER_STAMP = evidence_trust.OUTCOME_WRITER_STAMP
_DUPLICATE_MARKER_KEYS = frozenset({
    "duplicate",
    "duplicate_of",
    "is_duplicate",
    "derivative",
    "derivative_of",
    "is_derivative",
    "derived_from",
    "derived_from_evidence_id",
    "parent_evidence_id",
    "source_evidence_id",
    "unsupported",
    "is_unsupported",
    "support_status",
})
_FAILURE_MARKER_VALUES = frozenset({"unsupported", "duplicate", "derivative"})

ProcedureRankingChecker = Callable[..., Any]


def _id_sequence(values: Any) -> Sequence[Any]:
    if isinstance(values, (str, bytes, UUID)):
        return [values]
    return values or ()


def _nonnegative_int(value: Any, name: str) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if result < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return result


def _finite_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _iso_timestamp(value: Any) -> Optional[str]:
    result = _timestamp(value)
    return result.isoformat() if result is not None else None


def _latest_timestamp(*values: Any) -> Optional[datetime]:
    timestamps = [timestamp for timestamp in (_timestamp(value) for value in values) if timestamp is not None]
    return max(timestamps) if timestamps else None


def _json_marker_values(row: Mapping[str, Any]) -> Iterable[Any]:
    yield row
    for key in ("metadata", "properties", "tags", "provenance"):
        value = row.get(key)
        if isinstance(value, Mapping):
            yield value


def _is_duplicate_or_derivative(row: Mapping[str, Any]) -> bool:
    """Return whether a row explicitly identifies itself as derived data.

    The evidence schema has no universal duplicate flag.  The pure boundary
    therefore recognizes explicit markers used by producers and by read-model
    fixtures, while leaving ordinary ``independence_group`` and source
    references alone.  A repeated source is not automatically a duplicate;
    only an explicit derivative/unsupported marker is excluded here.
    """
    for payload in _json_marker_values(row):
        for key in _DUPLICATE_MARKER_KEYS:
            if key not in payload:
                continue
            value = payload[key]
            if value is None or value is False:
                continue
            if isinstance(value, str) and value.strip().lower() in _FAILURE_MARKER_VALUES:
                return True
            if value is True or (isinstance(value, str) and value.strip()):
                return True
    evidence_type = str(row.get("evidence_type") or "").strip().lower()
    direction = str(row.get("direction") or "").strip().lower()
    return evidence_type in _FAILURE_MARKER_VALUES or direction in _FAILURE_MARKER_VALUES


def is_duplicate_or_derivative_evidence(evidence_row: Mapping[str, Any]) -> bool:
    """Public pure predicate for explicitly derivative/unsupported evidence."""
    return _is_duplicate_or_derivative(evidence_row)


def is_canonical_outcome_evidence(
    evidence_row: Mapping[str, Any],
    *,
    procedure_row_id: Optional[str] = None,
    procedure_version: Optional[int] = None,
) -> bool:
    """Apply the canonical evidence-trust and exact-version boundary.

    The Python trust classifier is authoritative.  The SQL readers use the
    same vocabulary as a prefilter, but this predicate is what prevents a
    direct fixture, a self-report, or a stale row from entering a statistic.
    """
    if _is_duplicate_or_derivative(evidence_row):
        return False
    if evidence_trust.trust_state(dict(evidence_row)) not in _CANONICAL_TRUST_STATES:
        return False
    if evidence_row.get("target_type") != "procedure":
        return False
    if evidence_row.get("t_invalid") is not None:
        return False
    t_valid = _timestamp(evidence_row.get("t_valid"))
    if t_valid is not None and t_valid > datetime.now(timezone.utc):
        return False
    direction = evidence_row.get("direction")
    if direction is not None:
        expected_direction = "supports" if evidence_row.get("outcome_status") == "success" else "contradicts"
        if direction != expected_direction:
            return False
    if "success_criteria" in evidence_row:
        criteria = _mapping(evidence_row.get("success_criteria"))
        if evidence_row.get("outcome_status") == "failure" and criteria:
            return False
        if evidence_row.get("outcome_status") == "success":
            predicate = criteria.get("predicate")
            metrics = criteria.get("metrics")
            has_predicate = isinstance(predicate, str) and bool(predicate.strip())
            has_metrics = isinstance(metrics, Mapping) and bool(metrics)
            if not has_predicate and not has_metrics:
                return False
    if procedure_row_id is not None and str(evidence_row.get("target_id")) != str(procedure_row_id):
        return False
    if procedure_version is not None:
        try:
            if int(evidence_row.get("target_version")) != int(procedure_version):
                return False
        except (TypeError, ValueError):
            return False
    return evidence_row.get("outcome_status") in ("success", "failure")


def canonical_outcome_evidence(
    evidence_rows: Iterable[Mapping[str, Any]],
    *,
    procedure_row_id: Optional[str] = None,
    procedure_version: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Filter and de-duplicate one candidate's canonical evidence stream."""
    accepted: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, source in enumerate(evidence_rows):
        row = dict(source)
        if not is_canonical_outcome_evidence(
            row, procedure_row_id=procedure_row_id, procedure_version=procedure_version,
        ):
            continue
        identity = row.get("id")
        identity_key = f"id:{identity}" if identity is not None else f"position:{index}"
        if identity_key in seen_ids:
            continue
        seen_ids.add(identity_key)
        accepted.append(row)
    return accepted


def bayesian_reliability(
    successes: Optional[int] = None,
    failures: Optional[int] = None,
    *,
    attempts: Optional[int] = None,
    confidence: float = CREDIBLE_INTERVAL_MASS,
) -> dict[str, Any]:
    """Compute the Jeffreys-prior Bayesian reliability statistic.

    The returned lower bound is the lower endpoint of an equal-tailed
    ``confidence`` credible interval.  With the default 95% mass this is
    exactly ``beta.ppf(0.025, 1 + successes, 1 + failures)``.
    """
    if attempts is not None:
        attempt_count = _nonnegative_int(attempts, "attempts")
        if successes is None:
            successes = attempt_count
        if failures is None:
            failures = attempt_count - int(successes)
        if int(failures) < 0:
            raise ValueError("failures cannot exceed attempts")
    success_count = _nonnegative_int(successes, "successes")
    failure_count = _nonnegative_int(failures, "failures")
    if attempts is not None and success_count + failure_count != attempt_count:
        raise ValueError("attempts must equal successes + failures")
    confidence_value = _finite_float(confidence, "confidence")
    if not 0.0 < confidence_value < 1.0:
        raise ValueError("confidence must be strictly between zero and one")
    tail = (1.0 - confidence_value) / 2.0
    alpha = JEFFREYS_PRIOR_ALPHA + success_count
    beta_parameter = JEFFREYS_PRIOR_BETA + failure_count
    lower = float(beta.ppf(tail, alpha, beta_parameter))
    upper = float(beta.ppf(1.0 - tail, alpha, beta_parameter))
    return {
        "prior": "Jeffreys Beta(1,1)",
        "posterior_alpha": alpha,
        "posterior_beta": beta_parameter,
        "posterior": f"Beta({alpha:g}, {beta_parameter:g})",
        "successes": success_count,
        "failures": failure_count,
        "attempts": success_count + failure_count,
        "confidence": confidence_value,
        "lower_quantile": tail,
        "upper_quantile": 1.0 - tail,
        "credible_lower_bound": lower,
        "credible_upper_bound": upper,
        "credible_lower_95": lower if confidence_value == CREDIBLE_INTERVAL_MASS else None,
        "credible_upper_95": upper if confidence_value == CREDIBLE_INTERVAL_MASS else None,
        "credible_lower_one_sided_95": float(
            beta.ppf(1.0 - CREDIBLE_INTERVAL_MASS, alpha, beta_parameter)
        ) if confidence_value == CREDIBLE_INTERVAL_MASS else None,
        "posterior_mean": alpha / (alpha + beta_parameter),
        "method": "scipy.stats.beta.ppf",
    }


def credible_lower_bound(successes: int, failures: int) -> float:
    """Return only the canonical 95% Bayesian lower credible bound."""
    return float(bayesian_reliability(successes, failures)["credible_lower_bound"])


def _independence_key(row: Mapping[str, Any], position: int) -> str:
    group = row.get("independence_group")
    if group is not None and str(group).strip():
        return f"group:{str(group).strip()}"
    identity = row.get("id")
    return f"id:{identity}" if identity is not None else f"position:{position}"


def procedure_reliability(
    evidence_rows: Iterable[Mapping[str, Any]],
    *,
    procedure_row_id: Optional[str] = None,
    procedure_version: Optional[int] = None,
) -> dict[str, Any]:
    """Compute raw counts, Bayesian reliability, and legacy Wilson fields."""
    rows = canonical_outcome_evidence(
        evidence_rows,
        procedure_row_id=procedure_row_id,
        procedure_version=procedure_version,
    )
    successes = sum(1 for row in rows if row.get("outcome_status") == "success")
    failures = sum(1 for row in rows if row.get("outcome_status") == "failure")
    attempts = successes + failures
    contexts = sorted({
        str(row["context_key"]).strip()
        for row in rows
        if row.get("context_key") is not None and str(row["context_key"]).strip()
    })
    independent_keys = {
        _independence_key(row, index)
        for index, row in enumerate(rows)
    }
    independent_successes = len({
        _independence_key(row, index)
        for index, row in enumerate(rows)
        if row.get("outcome_status") == "success"
    })
    posterior = bayesian_reliability(successes, failures)
    wilson_lower, wilson_upper = wilson_interval(successes, attempts)
    latest_evidence = _latest_timestamp(
        *(row.get("t_created") for row in rows),
        *(row.get("created_at") for row in rows),
    )
    return {
        "successes": successes,
        "failures": failures,
        "attempts": attempts,
        "raw_successes": successes,
        "raw_failures": failures,
        "raw_attempts": attempts,
        "contexts": len(contexts),
        "raw_contexts": len(contexts),
        "context_keys": contexts,
        "raw_context_keys": contexts,
        "independent_evidence": len(independent_keys),
        "independent_attempts": len(independent_keys),
        "raw_independent_evidence": len(independent_keys),
        "independent_successes": independent_successes,
        "success_rate": successes / attempts if attempts else None,
        "failure_rate": failures / attempts if attempts else None,
        "credible_lower_bound": posterior["credible_lower_bound"],
        "credible_upper_bound": posterior["credible_upper_bound"],
        "posterior_mean": posterior["posterior_mean"],
        "wilson_lower_bound": wilson_lower,
        "wilson_upper_bound": wilson_upper,
        "wilson_lower": wilson_lower,
        "wilson_upper": wilson_upper,
        "latest_evidence_at": _iso_timestamp(latest_evidence),
        "bayesian": posterior,
        "wilson": {
            "lower": wilson_lower,
            "upper": wilson_upper,
        },
    }


def _legacy_bucket(verification_state: Optional[str], capability: Mapping[str, Any]) -> str:
    if verification_state == "verified":
        return "verified"
    evidence_count = int(capability.get("evidence_count") or 0)
    success_count = int(capability.get("success_count") or 0)
    if evidence_count == 0:
        return "needs_evidence"
    if success_count == 0:
        return "verified_failure"
    return "candidate"


def procedure_lane(
    *,
    attempts: int,
    successes: int,
    failures: int = 0,
    verification_state: Optional[str] = None,
    contexts: int = 0,
) -> str:
    """Name the central ranking lane without treating unknown as quality.

    The two-attempt presentation boundary is a named cold-start label, not a
    multiplier and not a claim that a Procedure is safe or unsafe.
    """
    attempt_count = _nonnegative_int(attempts, "attempts")
    success_count = _nonnegative_int(successes, "successes")
    failure_count = _nonnegative_int(failures, "failures")
    if attempt_count == 0:
        return "promising_needs_evidence"
    if success_count == 0:
        return "verified_failure"
    if attempt_count < MIN_TRUSTED_ATTEMPTS_FOR_CANDIDATE:
        return "promising_needs_evidence"
    if (
        verification_state == "verified"
        and failure_count == 0
        and success_count >= MIN_SUCCESSES_FOR_VERIFIED
        and contexts >= MIN_DISTINCT_CONTEXTS_FOR_VERIFIED
    ):
        return "verified"
    return "candidate"


def procedure_ineligibility_reasons(
    procedure: Mapping[str, Any], *, as_of: Optional[datetime] = None
) -> list[str]:
    """Pure hard eligibility checks that do not need project state."""
    at = as_of or datetime.now(timezone.utc)
    reasons: list[str] = []
    t_valid = _timestamp(procedure.get("t_valid"))
    t_invalid = _timestamp(procedure.get("t_invalid"))
    if t_valid is not None and t_valid > at:
        reasons.append("temporal_validity")
    if t_invalid is not None and t_invalid <= at:
        reasons.append("temporal_validity")
    if str(procedure.get("staleness") or "fresh") == "stale":
        reasons.append("staleness")
    if str(procedure.get("availability") or "active") != "active":
        reasons.append("availability")
    return reasons


def _procedure_cost(procedure: Mapping[str, Any]) -> dict[str, Any]:
    stats = _mapping(procedure.get("verification_stats"))
    attempts = int(stats.get("attempts") or 0)
    total = stats.get("match_cost_total")
    average = float(total) / attempts if total is not None and attempts else None
    return {
        "match_cost_total": total,
        "average_match_cost": average,
        "realised_savings_total": stats.get("realised_savings_total"),
        "mean_steps": stats.get("mean_steps"),
    }


def _procedure_summary(procedure: Mapping[str, Any]) -> str:
    supplied = procedure.get("applicability_summary")
    if supplied:
        return str(supplied)
    try:
        from app.services.retrieval_document import build_applicability_summary

        built = build_applicability_summary(dict(procedure))
        return str(built or "")
    except (ImportError, AttributeError, TypeError, ValueError):
        return str(procedure.get("goal") or procedure.get("name") or "")


def _context_matches(procedure: Mapping[str, Any], context_key: Optional[str]) -> bool:
    if not context_key:
        return False
    needle = str(context_key).strip().lower()
    if not needle:
        return False
    summary = str(procedure.get("applicability_summary") or "").lower()
    if needle in summary:
        return True
    scope = _mapping(procedure.get("scope"))
    for values in scope.values():
        rendered = json.dumps(values, sort_keys=True, default=str).lower()
        if needle in rendered:
            return True
    for exclusion in procedure.get("exclusions") or []:
        rendered = json.dumps(exclusion, sort_keys=True, default=str).lower()
        if needle in rendered:
            return True
    return False


def _procedure_explanation(
    *,
    procedure: Mapping[str, Any],
    reliability: Mapping[str, Any],
    lane: str,
    context_matched: bool,
) -> tuple[str, list[dict[str, str]]]:
    details: list[dict[str, str]] = []
    attempts = int(reliability["attempts"])
    if lane == "promising_needs_evidence":
        details.append({
            "code": "promising_needs_evidence",
            "message": "structurally eligible, but no canonical trusted outcome evidence establishes quality",
        })
    elif lane == "verified_failure":
        details.append({
            "code": "verified_failure",
            "message": "canonical trusted outcomes contain no success",
        })
    else:
        details.append({
            "code": "trusted_evidence",
            "message": f"{attempts} canonical trusted outcomes were used; self-reports and derivative evidence were excluded",
        })
    details.append({
        "code": "bayesian_reliability",
        "message": (
            "Jeffreys Beta(1,1) posterior lower 95% credible bound "
            f"{reliability['credible_lower_bound']:.6f}"
        ),
    })
    if context_matched:
        details.append({
            "code": "context_match",
            "message": "context match is reported for explanation and is not a score multiplier",
        })
    if str(procedure.get("availability") or "active") != "active":
        details.append({"code": "availability", "message": "availability is not active"})
    return "; ".join(item["message"] for item in details), details


def score_procedure_candidate(
    procedure: Mapping[str, Any],
    evidence_rows: Iterable[Mapping[str, Any]] = (),
    *,
    context_key: Optional[str] = None,
) -> dict[str, Any]:
    """Score one structurally valid Procedure version using canonical evidence."""
    procedure_row_id = str(procedure.get("id") or procedure.get("procedure_row_id") or "")
    version = procedure.get("version")
    reliability = procedure_reliability(
        evidence_rows,
        procedure_row_id=procedure_row_id or None,
        procedure_version=int(version) if version is not None else None,
    )
    lane = procedure_lane(
        attempts=reliability["attempts"],
        successes=reliability["successes"],
        failures=reliability["failures"],
        verification_state=procedure.get("verification_state"),
        contexts=reliability["contexts"],
    )
    legacy_bucket = _legacy_bucket(
        procedure.get("verification_state"),
        {
            "evidence_count": reliability["attempts"],
            "success_count": reliability["successes"],
        },
    )
    context_matched = _context_matches(procedure, context_key)
    quality_score = reliability["credible_lower_bound"] if reliability["attempts"] else 0.0
    explanation, details = _procedure_explanation(
        procedure=procedure,
        reliability=reliability,
        lane=lane,
        context_matched=context_matched,
    )
    freshness_at = _iso_timestamp(_latest_timestamp(
        reliability["latest_evidence_at"],
        procedure.get("updated_at"),
        procedure.get("t_created"),
    ))
    raw = {
        "successes": reliability["successes"],
        "failures": reliability["failures"],
        "attempts": reliability["attempts"],
        "contexts": reliability["contexts"],
        "context_keys": reliability["context_keys"],
        "independent_evidence": reliability["independent_evidence"],
        "independent_successes": reliability["independent_successes"],
    }
    signals = {
        "evidence_state": "unknown" if lane == "promising_needs_evidence" else "observed",
        "reliability": reliability["credible_lower_bound"],
        "volume": reliability["attempts"],
        "contexts": reliability["contexts"],
        "independence": reliability["independent_evidence"],
        "failure_rate": reliability["failure_rate"],
        "freshness_at": freshness_at,
    }
    result = {
        "procedure_row_id": procedure_row_id,
        "procedure_id": str(procedure.get("procedure_id") or ""),
        "version": version,
        "display_name": procedure.get("display_name") or procedure.get("name"),
        "display_description": procedure.get("display_description") or procedure.get("goal"),
        "name": procedure.get("name"),
        "goal": procedure.get("goal"),
        "applicability_summary": _procedure_summary(procedure),
        "created_by": procedure.get("created_by"),
        "verification_state": procedure.get("verification_state"),
        "staleness": procedure.get("staleness"),
        "availability": procedure.get("availability"),
        "scope": _mapping(procedure.get("scope")),
        "exclusions": list(procedure.get("exclusions") or []),
        "lane": lane,
        "bucket": legacy_bucket,
        "bucket_label": {
            "verified": "Verified",
            "candidate": "Candidate",
            "needs_evidence": "Needs evidence",
            "verified_failure": "Verified failure",
        }.get(legacy_bucket, lane),
        "score": quality_score,
        "quality_score": quality_score,
        "reliability_score": quality_score,
        "evidence_adjusted_reliability": quality_score,
        "ranking_lane": lane,
        "evidence_state": signals["evidence_state"],
        "credible_lower_bound": reliability["credible_lower_bound"],
        "credible_upper_bound": reliability["credible_upper_bound"],
        "bayesian_lower_bound": reliability["credible_lower_bound"],
        "bayesian_upper_bound": reliability["credible_upper_bound"],
        "credible_lower_95": reliability["credible_lower_bound"],
        "wilson_fields": {
            "lower": reliability["wilson_lower_bound"],
            "upper": reliability["wilson_upper_bound"],
        },
        "wilson_lower_bound": reliability["wilson_lower_bound"],
        "wilson_upper_bound": reliability["wilson_upper_bound"],
        "p_lower": reliability["wilson_lower_bound"],
        "p_upper": reliability["wilson_upper_bound"],
        "p_estimate": reliability["wilson_lower_bound"],
        "evidence_count": reliability["attempts"],
        "success_count": reliability["successes"],
        "failure_count": reliability["failures"],
        "attempts": reliability["attempts"],
        "raw_successes": reliability["successes"],
        "raw_failures": reliability["failures"],
        "raw_attempts": reliability["attempts"],
        "context_count": reliability["contexts"],
        "contexts": reliability["contexts"],
        "raw_contexts": reliability["contexts"],
        "context_keys": reliability["context_keys"],
        "independent_evidence": reliability["independent_evidence"],
        "raw_independent_evidence": reliability["independent_evidence"],
        "independent_groups": reliability["independent_evidence"],
        "raw": raw,
        "signals": signals,
        "reliability": reliability,
        "bayesian_reliability": reliability["bayesian"],
        "cost": _procedure_cost(procedure),
        "risk": {
            "failure_rate": reliability["failure_rate"],
            "basis": "canonical trusted failures divided by canonical attempts",
        },
        "freshness": {
            "latest_evidence_at": reliability["latest_evidence_at"],
            "basis": "most recent canonical evidence timestamp",
        },
        "context_matched": context_matched,
        "explanation": explanation,
        "explanations": details,
        "explanation_codes": [item["code"] for item in details],
        "eligibility": {
            "eligible": True,
            "gate": "hard_applicability",
            "failed_constraints": [],
        },
        "hard_applicability_gate": True,
        "ordering": [
            "lane", "evidence_adjusted_reliability", "volume",
            "independence", "contexts", "risk", "cost", "freshness", "procedure_row_id",
        ],
        "eligible": True,
    }
    return result


def _procedure_sort_key(result: Mapping[str, Any]) -> tuple[Any, ...]:
    lane_order = {
        "verified": 0,
        "candidate": 0,
        "promising_needs_evidence": 1,
        "verified_failure": 2,
    }
    cost = result.get("cost") or {}
    average_cost = cost.get("average_match_cost")
    cost_key = (1, 0.0) if average_cost is None else (0, float(average_cost))
    freshness = _timestamp((result.get("signals") or {}).get("freshness_at"))
    freshness_key = 0.0 if freshness is None else -freshness.timestamp()
    risk = (result.get("signals") or {}).get("failure_rate")
    risk_key = 1.0 if risk is None else float(risk)
    return (
        lane_order.get(str(result.get("lane")), 9),
        -float(result.get("score") or 0.0),
        -int(result.get("attempts") or 0),
        -int(result.get("independent_evidence") or 0),
        -int(result.get("contexts") or 0),
        risk_key,
        cost_key,
        freshness_key,
        str(result.get("procedure_row_id") or ""),
    )


def rank_procedure_candidates(
    procedures: Iterable[Mapping[str, Any]],
    evidence_by_procedure: Optional[Mapping[str, Iterable[Mapping[str, Any]]]] = None,
    *,
    context_key: Optional[str] = None,
    already_eligible: bool = False,
) -> list[dict[str, Any]]:
    """Pure ranking seam for callers that already have bounded rows/evidence."""
    evidence_map = evidence_by_procedure or {}
    results: list[dict[str, Any]] = []
    for procedure in procedures:
        if not already_eligible and procedure_ineligibility_reasons(procedure):
            continue
        row_id = str(procedure.get("id") or procedure.get("procedure_row_id") or "")
        procedure_id = str(procedure.get("procedure_id") or "")
        evidence = evidence_map.get(row_id, evidence_map.get(procedure_id, ()))
        results.append(score_procedure_candidate(procedure, evidence, context_key=context_key))
    results.sort(key=_procedure_sort_key)
    for index, result in enumerate(results, start=1):
        result["rank"] = index
        result["of"] = len(results)
    return results


def _normalise_procedure_row(row: Mapping[str, Any]) -> dict[str, Any]:
    procedure = dict(row)
    procedure.setdefault("t_invalid", None)
    procedure.setdefault("t_valid", None)
    procedure.setdefault("staleness", "fresh")
    procedure.setdefault("availability", "active")
    procedure.setdefault("scope", {})
    procedure.setdefault("exclusions", [])
    procedure.setdefault("preconditions", [])
    procedure.setdefault("invariants", [])
    procedure.setdefault("verification_state", "candidate")
    procedure.setdefault("approval_status", "proposed")
    procedure.setdefault("verification_stats", {})
    return procedure


async def _call_applicability_checker(
    checker: ProcedureRankingChecker,
    pool: asyncpg.Pool,
    procedure: Mapping[str, Any],
    *,
    current_scope: Optional[dict[str, Any]],
    access_scope: AccessScope,
    require_verified: bool,
    as_of: Optional[datetime],
) -> Any:
    call = checker
    if not inspect.iscoroutinefunction(checker):
        async def call(*args: Any, **kwargs: Any) -> Any:
            value = checker(*args, **kwargs)
            if inspect.isawaitable(value):
                return await value
            return value
    try:
        return await call(
            pool,
            procedure,
            current_scope=current_scope,
            access_scope=access_scope,
            require_verified=require_verified,
            as_of=as_of,
        )
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        return await call(pool, procedure)


def _applicability_result(value: Any) -> tuple[bool, list[str]]:
    if isinstance(value, bool):
        return value, [] if value else ["applicability"]
    if isinstance(value, Mapping):
        applicable = bool(value.get("applicable", value.get("eligible", False)))
        constraints = value.get("failed_constraints") or value.get("reasons") or []
        return applicable, [str(item) for item in constraints]
    applicable = bool(getattr(value, "applicable", False))
    constraints = getattr(value, "failed_constraints", []) or []
    return applicable, [str(item) for item in constraints]


class ProcedureRankingService:
    """Read and rank a bounded set of exact Procedure version rows."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        scope: AccessScope = AccessScope.unrestricted(),
        tenant_scope: TenantScope = TenantScope.unrestricted(),
        current_scope: Optional[dict[str, Any]] = None,
        context_key: Optional[str] = None,
        require_verified: bool = False,
        as_of: Optional[datetime] = None,
        applicability_checker: Optional[ProcedureRankingChecker] = None,
    ) -> None:
        self.pool = pool
        self.scope = scope or AccessScope.unrestricted()
        self.tenant_scope = tenant_scope or TenantScope.unrestricted()
        self.current_scope = current_scope
        self.context_key = context_key
        self.require_verified = require_verified
        self.as_of = as_of
        self.applicability_checker = applicability_checker

    @property
    def _effective_scope(self) -> AccessScope:
        return self.scope or AccessScope.unrestricted()

    @property
    def _effective_current_scope(self) -> dict[str, Any]:
        if self.current_scope is not None:
            return dict(self.current_scope)
        if self.context_key:
            return {"context_key": self.context_key}
        return {}

    async def _read_on_home_shards(self, ids: list[str], sql: str, *args: Any) -> list[Any]:
        """Run ``sql`` (``$1`` = the id array) once per shard that homes any of
        ``ids`` (version row ids or stable procedure ids), concurrently. Evidence
        lives with its Procedure, so the same routing serves both reads. A single
        database is one plain query."""
        from app.services.shards import HOME_SHARD, hydrate_rows, multi_shard, pools_for

        if not await multi_shard(self.pool):
            return list(await self.pool.fetch(sql, ids, *args))
        routes = await self.pool.fetch(
            "SELECT row_id::text AS id, home_shard_id FROM procedure_row_routes WHERE row_id = ANY($1::uuid[]) "
            "UNION ALL SELECT object_id::text, home_shard_id FROM object_routes "
            "WHERE object_type = 'procedure' AND object_id = ANY($1::uuid[])", ids)
        id_to_shard = {value: HOME_SHARD for value in ids}
        id_to_shard.update({str(row["id"]): str(row["home_shard_id"]) for row in routes})

        async def fetch(shard_pool: Any, shard_ids: list[str]):
            return await shard_pool.fetch(sql, shard_ids, *args)

        hydration = await hydrate_rows(pools_for(self.pool), id_to_shard, fetch)
        return list(hydration.rows.values())

    async def fetch_candidates(self, procedure_ids: Sequence[str]) -> list[dict[str, Any]]:
        ids = [str(value) for value in _id_sequence(procedure_ids) if value is not None]
        if not ids:
            return []
        p_scope_sql, p_scope_params, _ = scope_predicates(
            self._effective_scope,
            self.tenant_scope or TenantScope.unrestricted(),
            alias="p",
            param_index=2,
        )
        columns = (
            "p.id, p.procedure_id, p.version, p.family_id, p.name, p.goal, "
            "p.display_name, p.display_description, p.preconditions, p.required_state, "
            "p.expected_effects, p.postconditions, p.invariants, p.failure_conditions, "
            "p.scope, p.exclusions, p.verification_state, p.staleness, p.availability, "
            "p.verification_stats, p.evidence_refs, p.provenance, p.domain, p.created_by, "
            "p.visibility, p.owner_id, p.tenant_id, p.approval_status, p.approved_by, "
            "p.approved_at, p.capability_statement, p.extracted_by, p.achieves_goal_id, "
            "p.t_valid, p.t_invalid, p.t_created, p.t_expired, p.created_at, p.updated_at, "
            "p.scope_type, p.scope_entity_id"
        )
        sql = (
            f"SELECT {columns} FROM procedures p "
            "WHERE (p.id = ANY($1::uuid[]) OR p.procedure_id = ANY($1::uuid[])) "
            "AND p.t_valid <= now() AND p.t_invalid IS NULL "
            "AND p.staleness <> 'stale' AND p.availability = 'active' "
            f"AND {p_scope_sql} "
            "ORDER BY CASE WHEN p.id = ANY($1::uuid[]) THEN 0 ELSE 1 END, "
            "p.version DESC, p.id"
        )
        rows = await self._read_on_home_shards(ids, sql, *p_scope_params)
        selected: dict[str, dict[str, Any]] = {}
        exact_procedure_ids: set[str] = set()
        requested = set(ids)
        stable_rows: dict[str, dict[str, Any]] = {}
        for raw in rows:
            row = _normalise_procedure_row(raw)
            row_id = str(row["id"])
            procedure_id = str(row.get("procedure_id") or "")
            if row_id in requested:
                selected[row_id] = row
                if procedure_id:
                    exact_procedure_ids.add(procedure_id)
            elif procedure_id in requested and procedure_id not in exact_procedure_ids:
                current = stable_rows.get(procedure_id)
                if current is None or int(row.get("version") or 0) > int(current.get("version") or 0):
                    stable_rows[procedure_id] = row
        for procedure_id, row in stable_rows.items():
            if procedure_id not in exact_procedure_ids:
                selected[procedure_id] = row
        return list(selected.values())

    async def fetch_evidence(self, procedure_row_ids: Sequence[str]) -> list[dict[str, Any]]:
        ids = [str(value) for value in _id_sequence(procedure_row_ids) if value is not None]
        if not ids:
            return []
        e_scope_sql, e_scope_params, _ = scope_predicates(
            self._effective_scope,
            self.tenant_scope or TenantScope.unrestricted(),
            alias="e",
            param_index=3,
        )
        sql = (
            "SELECT e.id, e.evidence_type, e.target_type, e.target_id, e.target_version, "
            "e.direction, e.outcome_status, e.success_criteria, e.independence_group, e.context_key, "
            "e.created_by, e.source_id, e.content_ref, e.extractor_version, "
            "e.t_valid, e.t_created, e.t_invalid, e.visibility, e.owner_id, e.tenant_id "
            "FROM evidence e JOIN procedures p ON p.id = e.target_id AND p.version = e.target_version "
            "WHERE e.target_type = 'procedure' AND p.id = ANY($1::uuid[]) "
            f"AND e.t_valid <= now() AND e.t_invalid IS NULL AND e.evidence_type IN ({_CANONICAL_EVIDENCE_TYPES_SQL}) "
            "AND e.outcome_status IN ('success', 'failure') "
            "AND ((e.outcome_status = 'success' AND e.direction = 'supports') "
            "OR (e.outcome_status = 'failure' AND e.direction = 'contradicts')) "
            "AND e.created_by = $2 "
            f"AND {e_scope_sql} "
            "ORDER BY e.t_created ASC, e.id"
        )
        rows = await self._read_on_home_shards(ids, sql, _TRUSTED_WRITER_STAMP, *e_scope_params)
        return [dict(row) for row in rows]

    async def _eligible(self, procedure: Mapping[str, Any]) -> tuple[bool, list[str]]:
        structural = procedure_ineligibility_reasons(procedure, as_of=self.as_of)
        if structural:
            return False, structural
        checker = self.applicability_checker
        if checker is None:
            from app.services.applicability import check_hard_constraints

            checker = check_hard_constraints
        value = await _call_applicability_checker(
            checker,
            self.pool,
            procedure,
            current_scope=self._effective_current_scope,
            access_scope=self._effective_scope,
            require_verified=self.require_verified,
            as_of=self.as_of,
        )
        return _applicability_result(value)

    async def rank(
        self,
        procedure_ids: Sequence[str],
        *,
        scope: Optional[AccessScope] = None,
        current_scope: Optional[dict[str, Any]] = None,
        context_key: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        if scope is not None:
            self.scope = scope
        if current_scope is not None:
            self.current_scope = current_scope
        if context_key is not None:
            self.context_key = context_key
        candidates = await self.fetch_candidates(procedure_ids)
        eligible: list[dict[str, Any]] = []
        for candidate in candidates:
            applicable, _ = await self._eligible(candidate)
            if applicable:
                eligible.append(candidate)
        if not eligible:
            return []
        evidence = await self.fetch_evidence([str(row["id"]) for row in eligible])
        by_procedure: dict[str, list[dict[str, Any]]] = {}
        for row in evidence:
            by_procedure.setdefault(str(row.get("target_id")), []).append(row)
        ranked = rank_procedure_candidates(
            eligible,
            by_procedure,
            context_key=self.context_key,
            already_eligible=True,
        )
        return ranked

    async def rank_procedures(
        self,
        procedure_ids: Sequence[str],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return await self.rank(procedure_ids, **kwargs)

    async def rank_for_goal(
        self,
        procedure_ids: Sequence[str],
        *,
        context_key: Optional[str] = None,
        current_scope: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        return await self.rank(
            procedure_ids,
            context_key=context_key,
            current_scope=current_scope,
        )

    async def rank_procedures_for_goal(
        self,
        procedure_ids: Sequence[str],
        *,
        context_key: Optional[str] = None,
        current_scope: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        return await self.rank_for_goal(
            procedure_ids,
            context_key=context_key,
            current_scope=current_scope,
        )


def _clamp_factor(value: Any, *, default: float = 1.0, name: str = "factor") -> float:
    if value is None:
        return default
    numeric = _finite_float(value, name)
    return min(1.0, max(0.0, numeric))


def normalized_factor(value: Any, *, default: float = 1.0) -> float:
    """Normalize an already-structured numeric signal without calibration."""
    return _clamp_factor(value, default=default)


def unresolved_opportunity_score(
    demand: Any = None,
    unmet_need: Any = None,
    tractability: Any = None,
    freshness: Any = None,
) -> float:
    """Return the exact unresolved-opportunity product.

    The factor order is the product requested by the product model:
    ``demand × unmet_need × tractability × freshness``.  Missing factors are
    neutral 1.0; an explicitly observed zero remains zero.  Demand is a multiplier
    in [0, 2] (1 + strength from demand_factor_from_commitments), so community
    demand can raise an opportunity but never lower it below neutral.
    """
    demand_value = 1.0 if demand is None else min(2.0, max(0.0, _finite_float(demand, "demand")))
    return (
        demand_value
        * _clamp_factor(unmet_need, name="unmet_need")
        * _clamp_factor(tractability, name="tractability")
        * _clamp_factor(freshness, name="freshness")
    )


opportunity_score = unresolved_opportunity_score


def percentile_ranks(values: Sequence[Any], *, missing: Optional[float] = None) -> list[Optional[float]]:
    """Return scale-free average percentile ranks for a bounded cohort.

    Ties receive the same percentile.  A single positive observation receives
    1.0 and a single zero receives 0.0; no time constant or half-life enters
    this normalization.
    """
    numeric = [None if value is None else _finite_float(value, "percentile value") for value in values]
    present = [(index, value) for index, value in enumerate(numeric) if value is not None]
    if not present:
        return [missing for _ in numeric]
    if len(present) == 1:
        result: list[Optional[float]] = [None for _ in numeric]
        index, value = present[0]
        result[index] = 1.0 if value > 0 else 0.0
        return result
    ordered = sorted(present, key=lambda item: item[1])
    ranks: dict[int, float] = {}
    cursor = 0
    while cursor < len(ordered):
        end = cursor
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[cursor][1]:
            end += 1
        average_position = (cursor + end) / 2.0
        percentile = average_position / (len(ordered) - 1)
        for position in range(cursor, end + 1):
            ranks[ordered[position][0]] = percentile
        cursor = end + 1
    return [ranks.get(index, missing) for index in range(len(numeric))]


def freshness_percentile(
    timestamps: Sequence[Any], *, missing: float = 1.0
) -> list[float]:
    """Normalize meaningful activity by its percentile in the bounded set."""
    values: list[Optional[float]] = []
    for timestamp in timestamps:
        parsed = _timestamp(timestamp)
        values.append(parsed.timestamp() if parsed is not None else None)
    ranks = percentile_ranks(values, missing=missing)
    return [missing if rank is None else _clamp_factor(rank, name="freshness") for rank in ranks]


def _share_at_or_below(value: float, peers: Sequence[float]) -> float:
    """Empirical CDF: the share of `peers` (which include this value) at or below it.
    Always > 0 for a positive value, so any backing lifts a Goal above no backing."""
    if not peers:
        return 1.0
    return sum(1 for peer in peers if peer <= value) / len(peers)


def demand_factor_from_commitments(
    *,
    normalized_demand: Any = None,
    committed_credit_count: Any = None,
    supporter_count: Any = None,
    cohort: Optional[Sequence[Any]] = None,
) -> tuple[float, bool, dict[str, Any]]:
    """Demand multiplier from commitment/supporter signals: 1.0 (neutral) without
    any, 1 + strength with some, strength in (0, 1]. Demand can therefore only RAISE
    a Goal's opportunity -- backing a Goal never ranks it below an unbacked one.

    strength = geometric mean over the observed signals (aggregated quadratic
    committed Credits, supporters) of the Goal's empirical-CDF position among the
    cohort (peers without the signal count as 0). With no cohort, any positive
    signal is full strength. Credits and the reward ledger are intentionally not
    parameters; `normalized_demand` is an already-normalized strength in [0, 1].
    """
    if normalized_demand is not None:
        strength = _clamp_factor(normalized_demand, name="normalized_demand")
        return 1.0 + strength, True, {
            "normalized_demand": strength,
            "committed_credit_count": committed_credit_count,
            "supporter_count": supporter_count,
            "credits_used_as_demand": False,
            "reason": "explicit normalized demand signal",
        }
    observed = [
        (name, value) for name, value in (("committed_credit_count", committed_credit_count),
                                          ("supporter_count", supporter_count))
        if value is not None
    ]
    if not observed:
        return 1.0, False, {
            "normalized_demand": None,
            "committed_credit_count": None,
            "supporter_count": None,
            "credits_used_as_demand": False,
            "reason": "no committed-credit or supporter signal exists",
        }
    positions = []
    for name, value in observed:
        own = _finite_float(value, "commitment count")
        if own <= 0:
            positions.append(0.0)
            continue
        if cohort is None:
            positions.append(1.0)
            continue
        peers = [
            _finite_float(item.get(name) if isinstance(item, Mapping) else item, "commitment count")
            if (item.get(name) if isinstance(item, Mapping) else item) is not None else 0.0
            for item in cohort
        ]
        positions.append(_share_at_or_below(own, peers + [own]))
    strength = math.prod(positions) ** (1.0 / len(positions))
    return 1.0 + strength, True, {
        "normalized_demand": strength,
        "committed_credit_count": committed_credit_count,
        "supporter_count": supporter_count,
        "credits_used_as_demand": False,
        "reason": "strength from explicit committed-credit/supporter signals; the multiplier is 1 + strength",
    }


def unmet_need_from_procedure_coverage(
    procedure_scores: Iterable[Mapping[str, Any]],
) -> tuple[float, dict[str, Any]]:
    """Use the strongest credible Procedure reliability as current coverage."""
    credible: list[Mapping[str, Any]] = []
    for procedure in procedure_scores:
        if not procedure.get("eligible", True):
            continue
        lane = str(procedure.get("lane") or "")
        if lane in ("promising_needs_evidence", "verified_failure"):
            continue
        lower = procedure.get("credible_lower_bound")
        if lower is None:
            reliability = _mapping(procedure.get("reliability"))
            lower = reliability.get("credible_lower_bound")
        if lower is not None:
            credible.append(procedure)
    if not credible:
        return 1.0, {
            "coverage": 0.0,
            "strongest_procedure": None,
            "credible_procedure_count": 0,
            "reason": "no credible Procedure evidence covers this Goal",
        }
    strongest = max(
        credible,
        key=lambda item: (
            float(item.get("credible_lower_bound") or 0.0),
            int((item.get("raw") or {}).get("attempts") or 0),
            str(item.get("procedure_row_id") or ""),
        ),
    )
    strongest_lower = strongest.get("credible_lower_bound")
    if strongest_lower is None:
        strongest_lower = (strongest.get("reliability") or {}).get("credible_lower_bound")
    coverage = _clamp_factor(strongest_lower, default=0.0, name="coverage")
    return 1.0 - coverage, {
        "coverage": coverage,
        "strongest_procedure": strongest.get("procedure_row_id"),
        "credible_procedure_count": len(credible),
        "reason": "coverage is the strongest canonical Bayesian lower bound",
    }


def resolved_usefulness_score(
    signals: Mapping[str, Any],
    *,
    cohort: Optional[Sequence[Mapping[str, Any]]] = None,
    cohort_index: int = 0,
) -> dict[str, Any]:
    """Combine resolved-Goal signals by product, never by fitted weights."""
    if cohort is not None and not cohort:
        cohort = None
    successes = int(signals.get("successes") or 0)
    failures = int(signals.get("failures") or 0)
    attempts = int(signals.get("attempts") or (successes + failures))
    if attempts != successes + failures:
        raise ValueError("attempts must equal successes + failures")
    reliability = (
        float(signals["credible_lower_bound"])
        if signals.get("credible_lower_bound") is not None
        else credible_lower_bound(successes, failures)
    )
    if cohort is None:
        success_factor = 1.0 if successes > 0 else 0.0
        context_factor = 1.0 if int(signals.get("independent_contexts") or 0) > 0 else 0.0
        usage_factor = 1.0 if int(signals.get("verified_independent_usage") or 0) > 0 else 0.0
        freshness_factor = 1.0 if signals.get("latest_activity_at") is not None else 0.0
    else:
        index = max(0, min(int(cohort_index), len(cohort) - 1))
        success_values = [
            int(item.get("successes") or 0) for item in cohort
        ]
        context_values = [
            int(item.get("independent_contexts") or item.get("contexts") or 0)
            for item in cohort
        ]
        usage_values = [
            int(item.get("verified_independent_usage") or 0) for item in cohort
        ]
        if signals.get("successes") is not None:
            success_values[index] = successes
        if signals.get("independent_contexts") is not None:
            context_values[index] = int(signals.get("independent_contexts") or 0)
        if signals.get("verified_independent_usage") is not None:
            usage_values[index] = int(signals.get("verified_independent_usage") or 0)
        success_factor = (
            0.0 if success_values[index] <= 0 else float(
                percentile_ranks(success_values, missing=0.0)[index]
            )
        )
        context_factor = (
            0.0 if context_values[index] <= 0 else float(
                percentile_ranks(context_values, missing=0.0)[index]
            )
        )
        usage_factor = (
            0.0 if usage_values[index] <= 0 else float(
                percentile_ranks(usage_values, missing=0.0)[index]
            )
        )
        freshness_factor = freshness_percentile([
            _freshness_for_goal(item) for item in cohort
        ])[index]
    explicit = {
        "success_factor": success_factor,
        "context_factor": context_factor,
        "independent_usage_factor": usage_factor,
        "freshness_factor": freshness_factor,
    }
    factor = reliability
    for value in explicit.values():
        factor *= _clamp_factor(value, name="usefulness factor")
    return {
        "score": factor,
        "reliability_factor": _clamp_factor(reliability, name="reliability factor"),
        "success_factor": success_factor,
        "context_factor": context_factor,
        "independent_usage_factor": usage_factor,
        "freshness_factor": freshness_factor,
        "raw": {
            "successes": successes,
            "failures": failures,
            "attempts": attempts,
            "independent_contexts": int(signals.get("independent_contexts") or 0),
            "verified_independent_usage": int(signals.get("verified_independent_usage") or 0),
            "independent_users": int(signals.get("independent_users") or 0),
        },
        "method": "no-weight product of Bayesian reliability and bounded percentiles",
    }


_TRUSTED_TRACTABILITY_SOURCES = frozenset({
    "structured_assessment",
    "trusted_assessment",
    "trusted",
    "structured",
    "assessment",
    "human_review",
    "verified_evidence",
    "ranking_read_model",
    "system",
})


def _tractability_from_trusted_source(goal: Mapping[str, Any]) -> Optional[float]:
    source = goal.get("trusted_tractability")
    if source is None:
        source = goal.get("tractability_signal")
    if source is None:
        source = goal.get("tractability")
    if not isinstance(source, Mapping):
        return None
    if source.get("trusted") is False:
        return None
    source_name = str(source.get("source") or source.get("provenance") or "").strip().lower()
    if source.get("trusted") is not True and source_name not in _TRUSTED_TRACTABILITY_SOURCES:
        return None
    value = source.get("normalized", source.get("score", source.get("value")))
    if value is None:
        return None
    return _clamp_factor(value, name="tractability")


def _is_resolved(goal: Mapping[str, Any]) -> bool:
    return goal.get("resolved_at") is not None


def _procedure_count_score(row: Mapping[str, Any]) -> dict[str, Any]:
    successes = int(row.get("successes") or 0)
    failures = int(row.get("failures") or 0)
    attempts = int(row.get("attempts") or (successes + failures))
    contexts = int(row.get("contexts") or row.get("independent_contexts") or 0)
    independent = int(row.get("independent_evidence") or 0)
    if attempts == 0:
        lower = 0.0
        lane = "promising_needs_evidence"
    elif successes == 0:
        lower = credible_lower_bound(successes, failures)
        lane = "verified_failure"
    else:
        lower = credible_lower_bound(successes, failures)
        lane = "candidate"
    return {
        "procedure_row_id": str(row.get("procedure_row_id") or row.get("id") or ""),
        "procedure_id": str(row.get("procedure_id") or ""),
        "version": row.get("version"),
        "lane": lane,
        "eligible": bool(row.get("eligible", True)),
        "credible_lower_bound": lower,
        "raw": {
            "successes": successes,
            "failures": failures,
            "attempts": attempts,
            "contexts": contexts,
            "independent_evidence": independent,
        },
        "reliability": {
            "credible_lower_bound": lower,
            "credible_upper_bound": credible_lower_bound(successes, failures)
            if attempts == 0 else float(beta.ppf(CREDIBLE_UPPER_QUANTILE, successes + 1, failures + 1)),
        },
    }


def _freshness_for_goal(goal: Mapping[str, Any]) -> Optional[datetime]:
    return _latest_timestamp(
        goal.get("latest_evidence_at"),
        goal.get("latest_usage_at"),
        goal.get("latest_activity_at"),
        goal.get("latest_procedure_activity_at"),
    )


def score_goal_candidate(
    goal: Mapping[str, Any],
    *,
    procedure_scores: Optional[Sequence[Mapping[str, Any]]] = None,
    cohort: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Score one unresolved or resolved Goal using only real signals."""
    procedures = list(procedure_scores or goal.get("procedure_scores") or [])
    goal_id = str(goal.get("goal_id") or goal.get("id") or "")
    resolved = _is_resolved(goal)
    explicit_coverage = goal.get("procedure_coverage")
    if explicit_coverage is None:
        explicit_coverage = goal.get("coverage")
    if explicit_coverage is None:
        explicit_coverage = goal.get("strongest_credible_reliability")
    if isinstance(explicit_coverage, Mapping):
        explicit_coverage = explicit_coverage.get("coverage")
    if explicit_coverage is not None:
        coverage_value = _clamp_factor(explicit_coverage, default=0.0, name="coverage")
        coverage = {
            "coverage": coverage_value,
            "strongest_procedure": goal.get("strongest_procedure"),
            "credible_procedure_count": int(goal.get("credible_procedure_count") or 0),
            "reason": "coverage came from the SQL aggregate",
        }
        unmet_need = 1.0 - coverage_value
    else:
        unmet_need, coverage = unmet_need_from_procedure_coverage(procedures)
    latest = _freshness_for_goal(goal)
    cohort_index = 0
    if cohort is not None:
        for index, item in enumerate(cohort):
            if str(item.get("goal_id") or item.get("id") or "") == goal_id:
                cohort_index = index
                break
    if cohort is None:
        freshness_factor = 1.0
    else:
        freshness_factor = freshness_percentile([
            _freshness_for_goal(item) for item in cohort
        ])[cohort_index]
    tractability = _tractability_from_trusted_source(goal)
    demand, demand_available, demand_signals = demand_factor_from_commitments(
        normalized_demand=goal.get("normalized_demand"),
        committed_credit_count=goal.get("committed_credit_count"),
        supporter_count=goal.get("supporter_count"),
        cohort=cohort,
    )
    if resolved:
        successes = int(goal.get("successes") or sum(
            int((item.get("raw") or {}).get("successes") or 0) for item in procedures
        ))
        failures = int(goal.get("failures") or sum(
            int((item.get("raw") or {}).get("failures") or 0) for item in procedures
        ))
        attempts = int(goal.get("attempts") or (successes + failures))
        contexts = int(
            goal.get("independent_contexts")
            or goal.get("contexts")
            or sum(int((item.get("raw") or {}).get("contexts") or 0) for item in procedures)
        )
        usage = int(goal.get("verified_independent_usage") or 0)
        users = int(goal.get("independent_users") or 0)
        credible_procedures = [
            item for item in procedures
            if str(item.get("lane") or "") not in ("promising_needs_evidence", "verified_failure")
            and item.get("eligible", True)
        ]
        pooled_successes = sum(
            int((item.get("raw") or {}).get("successes") or 0)
            for item in credible_procedures
        )
        pooled_failures = sum(
            int((item.get("raw") or {}).get("failures") or 0)
            for item in credible_procedures
        )
        pooled_lower = (
            credible_lower_bound(pooled_successes, pooled_failures)
            if pooled_successes + pooled_failures > 0
            else credible_lower_bound(successes, failures) if attempts > 0 else 0.0
        )
        resolved_signals = resolved_usefulness_score({
            "successes": successes,
            "failures": failures,
            "attempts": attempts,
            "independent_contexts": contexts,
            "verified_independent_usage": usage,
            "independent_users": users,
            "latest_activity_at": _iso_timestamp(latest),
            "credible_lower_bound": pooled_lower,
        }, cohort=cohort, cohort_index=cohort_index)
        score = float(resolved_signals["score"])
        signals = {
            "reliability_factor": resolved_signals["reliability_factor"],
            "pooled_credible_lower_bound": pooled_lower,
            "strongest_credible_lower_bound": max(
                (float(item.get("credible_lower_bound") or 0.0) for item in credible_procedures),
                default=0.0,
            ),
            "success_factor": resolved_signals["success_factor"],
            "context_factor": resolved_signals["context_factor"],
            "independent_usage_factor": resolved_signals["independent_usage_factor"],
            "freshness_factor": resolved_signals["freshness_factor"],
            "coverage": coverage["coverage"],
            "unmet_need": unmet_need,
        }
        raw = {
            **resolved_signals["raw"],
            "latest_activity_at": _iso_timestamp(latest),
            "procedure_count": len(procedures),
        }
        method = resolved_signals["method"]
    else:
        score = unresolved_opportunity_score(demand, unmet_need, tractability, freshness_factor)
        signals = {
            "demand": demand - 1.0,              # strength (shown as a %); the score uses 1 + strength
            "unmet_need": unmet_need,
            "tractability": 1.0 if tractability is None else tractability,
            "freshness": freshness_factor,
            "coverage": coverage["coverage"],
            "strongest_procedure": coverage["strongest_procedure"],
        }
        raw = {
            "successes": int(goal.get("successes") or 0),
            "failures": int(goal.get("failures") or 0),
            "attempts": int(goal.get("attempts") or 0),
            "independent_contexts": int(goal.get("independent_contexts") or 0),
            "verified_independent_usage": int(goal.get("verified_independent_usage") or 0),
            "independent_users": int(goal.get("independent_users") or 0),
            "latest_activity_at": _iso_timestamp(latest),
            "procedure_count": len(procedures),
        }
        method = "product of normalized demand, unmet need, tractability, and freshness"
    signal_availability = {key: value is not None for key, value in signals.items()}
    if resolved:
        signal_availability.update({
            "reliability_factor": attempts > 0,
            "pooled_credible_lower_bound": pooled_lower > 0,
            "success_factor": successes > 0,
            "context_factor": contexts > 0,
            "independent_usage_factor": usage > 0,
            "freshness_factor": latest is not None,
        })
    else:
        signal_availability.update({
            "demand": demand_available,
            "tractability": tractability is not None,
            "freshness": latest is not None,
            "coverage": bool(procedures) or explicit_coverage is not None,
        })
    explanations: list[dict[str, str]] = []
    if not resolved and not demand_available:
        explanations.append({
            "code": "demand_unavailable",
            "message": "no committed-credit or supporter signal exists; Credits and Standing are not demand",
        })
    if not resolved and tractability is None:
        explanations.append({
            "code": "tractability_unavailable",
            "message": "no structured tractability signal exists; neutral factor 1.0 was used",
        })
    if latest is None:
        explanations.append({
            "code": "freshness_unavailable",
            "message": "no meaningful evidence, usage, or activity timestamp exists; neutral factor 1.0 was used",
        })
    explanations.append({
        "code": "unmet_need",
        "message": coverage["reason"],
    })
    return {
        "goal_id": goal_id,
        "canonical_name": goal.get("canonical_name"),
        "description": goal.get("description"),
        "status": goal.get("status"),
        "resolved_at": _iso_timestamp(goal.get("resolved_at")),
        "metadata": _mapping(goal.get("metadata")),
        "state": "resolved" if resolved else "unresolved",
        "lane": "resolved_usefulness" if resolved else "unresolved_opportunity",
        "score": score,
        "opportunity_score": score if not resolved else None,
        "usefulness_score": score if resolved else None,
        "opportunity": score if not resolved else None,
        "usefulness": score if resolved else None,
        "signals": signals,
        "signal_availability": signal_availability,
        "raw": raw,
        "raw_counts": raw,
        "coverage_details": coverage,
        "normalized_demand": signals.get("demand") if not resolved else None,
        "unmet_need": signals.get("unmet_need"),
        "tractability": signals.get("tractability") if not resolved else None,
        "freshness_factor": signals.get("freshness", signals.get("freshness_factor")),
        "demand_factor": demand,
        "demand": demand_signals,
        "explanation": "; ".join(item["message"] for item in explanations),
        "explanations": explanations,
        "explanation_codes": [item["code"] for item in explanations],
        "method": method,
    }


def _ranking_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _ranking_label(key: str) -> str:
    return key.replace("_", " ").strip().title()


def public_goal_ranking(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return the small, stable ranking envelope consumed by Goal clients."""
    if "resolved_at" in result or "status" in result:
        state = "resolved" if _is_resolved(result) else "unresolved"
    else:
        state = result.get("state")
        if state not in ("resolved", "unresolved"):
            state = "unresolved"
    raw_signals = result.get("signals")
    raw_signals = raw_signals if isinstance(raw_signals, Mapping) else {}
    availability = result.get("signal_availability")
    availability = availability if isinstance(availability, Mapping) else {}
    signals: dict[str, dict[str, Any]] = {}
    for key, source in raw_signals.items():
        key = str(key)
        if isinstance(source, Mapping) and "value" in source:
            value = source.get("value")
            available = bool(source.get("available", value is not None))
            label = str(source.get("label") or _ranking_label(key))
        else:
            value = source
            available = value is not None
            label = _ranking_label(key)
        if key in availability:
            available = bool(availability[key])
        numeric_value = _ranking_number(value)
        if not available or numeric_value is None:
            available = False
            numeric_value = None
        signals[key] = {
            "label": label,
            "value": numeric_value,
            "available": available,
        }
    explanation = result.get("explanation")
    if not isinstance(explanation, str):
        explanation = "; ".join(
            str(item.get("message") or "") for item in result.get("explanations") or []
            if isinstance(item, Mapping) and item.get("message")
        )
    return {
        "score": _ranking_number(result.get("score")),
        "state": state,
        "signals": signals,
        "explanation": explanation,
    }


shape_goal_ranking = public_goal_ranking
format_goal_ranking = public_goal_ranking


def _rank_goal_state(
    goals: Sequence[Mapping[str, Any]],
    procedure_map: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    cohort = [dict(goal) for goal in goals]
    result = [
        score_goal_candidate(
            goal,
            procedure_scores=procedure_map.get(
                str(goal.get("goal_id") or goal.get("id") or ""), (),
            ),
            cohort=cohort,
        )
        for goal in cohort
    ]
    result.sort(key=lambda item: (
        -float(item.get("score") or 0.0),
        -int((item.get("raw") or {}).get("successes") or 0),
        -int((item.get("raw") or {}).get("attempts") or 0),
        str(item.get("goal_id") or ""),
    ))
    for index, item in enumerate(result, start=1):
        item["rank"] = index
        item["of"] = len(result)
    return result


def rank_goal_candidates(
    goals: Iterable[Mapping[str, Any]],
    *,
    procedures_by_goal: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> list[dict[str, Any]]:
    """Pure bounded Goal ranking seam with independent resolution ranks."""
    procedure_map = procedures_by_goal or {}
    goal_list = [dict(goal) for goal in goals]
    state_groups: dict[str, list[Mapping[str, Any]]] = {
        "unresolved": [],
        "resolved": [],
    }
    for goal in goal_list:
        state = "resolved" if _is_resolved(goal) else "unresolved"
        state_groups[state].append(goal)
    return [
        *_rank_goal_state(state_groups["unresolved"], procedure_map),
        *_rank_goal_state(state_groups["resolved"], procedure_map),
    ]


def rank_unresolved_goal_candidates(
    goals: Iterable[Mapping[str, Any]],
    *,
    procedures_by_goal: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> list[dict[str, Any]]:
    """Pure unresolved-only lane."""
    return [
        item for item in rank_goal_candidates(goals, procedures_by_goal=procedures_by_goal)
        if item["state"] == "unresolved"
    ]


def rank_resolved_goal_candidates(
    goals: Iterable[Mapping[str, Any]],
    *,
    procedures_by_goal: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> list[dict[str, Any]]:
    """Pure resolved-only usefulness lane."""
    return [
        item for item in rank_goal_candidates(goals, procedures_by_goal=procedures_by_goal)
        if item["state"] == "resolved"
    ]


def _scope_for_goals(scope: AccessScope) -> tuple[str, list[Any]]:
    return visibility_predicate(scope, alias="g", param_index=2)


def build_goal_ranking_query(
    *,
    goal_ids: Optional[Sequence[str]] = None,
    limit: int = 50,
    scope: AccessScope = AccessScope.unrestricted(),
    tenant_scope: TenantScope = TenantScope.unrestricted(),
    status: Optional[str] = None,
) -> tuple[str, list[Any]]:
    """Build the bounded SQL read model used by ``GoalRankingService``.

    Goal rows are limited before procedure/evidence/usage aggregation.  The
    query returns one row per live eligible Procedure belonging to that bounded
    Goal set, plus a null Procedure row for a Goal with no Procedure.  All
    outcome counts are aggregated in SQL and pinned to the Procedure version.
    The Credit ledger is selected only by goal id as a diagnostic; its amount
    is never selected or used as a factor.
    """
    bounded_limit = max(1, min(int(limit), MAX_GOALS_PER_QUERY))
    params: list[Any] = [list(_id_sequence(goal_ids))]
    index = 2
    goal_visibility_sql, goal_visibility_params = _scope_for_goals(scope)
    params.extend(goal_visibility_params)
    index += len(goal_visibility_params)
    procedure_scope_sql, procedure_scope_params, next_procedure_index = scope_predicates(
        scope, tenant_scope, alias="p", param_index=index,
    )
    params.extend(procedure_scope_params)
    evidence_scope_sql, evidence_scope_params, next_evidence_index = scope_predicates(
        scope, tenant_scope, alias="e", param_index=next_procedure_index,
    )
    params.extend(evidence_scope_params)
    writer_index = next_evidence_index
    params.append(_TRUSTED_WRITER_STAMP)
    next_index = writer_index + 1
    status_clause = ""
    if status is not None:
        status_index = next_index
        params.append(status)
        next_index += 1
        status_clause = f"AND g.status = ${status_index} "
    limit_index = next_index
    params.append(bounded_limit)
    sql = f"""
WITH goal_base AS (
    SELECT
        g.id AS goal_id,
        g.canonical_name,
        g.description,
        g.status,
        g.resolved_at,
        g.metadata,
        g.t_created,
        g.t_invalid,
        g.visibility,
        g.owner_id
    FROM goals g
    WHERE g.t_invalid IS NULL
      AND ({goal_visibility_sql})
      AND (cardinality($1::uuid[]) = 0 OR g.id = ANY($1::uuid[]))
      {status_clause}
    ORDER BY g.t_created DESC, g.id
    LIMIT ${limit_index}
),
procedure_base AS (
    SELECT DISTINCT ON (p.id)
        gb.goal_id,
        p.id AS procedure_row_id,
        p.procedure_id,
        p.version AS procedure_version,
        p.name,
        p.display_name,
        p.display_description,
        p.goal,
        p.scope,
        p.exclusions,
        p.preconditions,
        p.invariants,
        p.verification_state,
        p.staleness,
        p.availability,
        p.created_at,
        p.updated_at,
        p.t_created
    FROM goal_base gb
    JOIN procedures p ON (
        p.achieves_goal_id = gb.goal_id
        OR EXISTS (
            SELECT 1
            FROM solutions s
            WHERE s.goal_id = gb.goal_id
              AND s.target_table = 'procedures'
              AND s.status = 'active'
              AND (s.target_id = p.procedure_id OR s.target_id = p.id)
        )
    )
    WHERE p.t_valid <= now()
      AND p.t_invalid IS NULL
      AND p.staleness <> 'stale'
      AND p.availability = 'active'
      AND ({procedure_scope_sql})
    ORDER BY p.id, p.version DESC
),
evidence_aggregate AS (
    SELECT
        pb.goal_id,
        pb.procedure_row_id,
        pb.procedure_id,
        pb.procedure_version,
        pb.name,
        pb.display_name,
        pb.display_description,
        pb.goal,
        pb.scope,
        pb.exclusions,
        pb.preconditions,
        pb.invariants,
        pb.verification_state,
        pb.staleness,
        pb.availability,
        count(e.id) FILTER (WHERE e.outcome_status = 'success') AS successes,
        count(e.id) FILTER (WHERE e.outcome_status = 'failure') AS failures,
        count(e.id) AS attempts,
        count(DISTINCT e.context_key) AS contexts,
        count(DISTINCT COALESCE(e.independence_group, e.id::text)) AS independent_evidence,
        count(DISTINCT COALESCE(e.independence_group, e.id::text))
            FILTER (WHERE e.outcome_status = 'success') AS independent_successes,
        max(e.t_created) AS latest_evidence_at,
        max(pb.updated_at) AS latest_procedure_activity_at
    FROM procedure_base pb
    LEFT JOIN evidence e ON (
        e.target_type = 'procedure'
        AND e.target_id = pb.procedure_row_id
        AND e.target_version = pb.procedure_version
        AND e.t_valid <= now()
        AND e.t_invalid IS NULL
        AND e.evidence_type IN ({_CANONICAL_EVIDENCE_TYPES_SQL})
        AND e.outcome_status IN ('success', 'failure')
        AND ((e.outcome_status = 'success' AND e.direction = 'supports')
             OR (e.outcome_status = 'failure' AND e.direction = 'contradicts'))
        AND e.created_by = ${writer_index}
        AND ({evidence_scope_sql})
    )
    GROUP BY pb.goal_id, pb.procedure_row_id, pb.procedure_id, pb.procedure_version,
             pb.name, pb.display_name, pb.display_description, pb.goal, pb.scope,
             pb.exclusions, pb.preconditions, pb.invariants, pb.verification_state,
             pb.staleness, pb.availability, pb.updated_at
),
usage_aggregate AS (
    SELECT
        pb.goal_id,
        count(u.id) AS usage_count,
        count(u.id) FILTER (
            WHERE u.outcome_state = 'verified_success' AND u.is_self_use = FALSE
        ) AS verified_independent_usage,
        count(DISTINCT u.executed_by) FILTER (
            WHERE u.outcome_state = 'verified_success' AND u.is_self_use = FALSE
        ) AS independent_users,
        max(u.created_at) AS latest_usage_at
    FROM procedure_base pb
    LEFT JOIN procedure_usage_events u ON u.procedure_row_id = pb.procedure_row_id
    GROUP BY pb.goal_id
),
credit_ledger_diagnostic AS (
    SELECT DISTINCT goal_id
    FROM credit_ledger_events
    WHERE goal_id IN (SELECT goal_id FROM goal_base)
)
SELECT
    gb.goal_id,
    gb.canonical_name,
    gb.description,
    gb.status,
    gb.resolved_at,
    gb.metadata,
    gb.t_created,
    ea.procedure_row_id,
    ea.procedure_id,
    ea.procedure_version,
    ea.name,
    ea.display_name,
    ea.display_description,
    ea.goal,
    ea.scope,
    ea.exclusions,
    ea.preconditions,
    ea.invariants,
    ea.verification_state,
    ea.staleness,
    ea.availability,
    COALESCE(ea.successes, 0) AS successes,
    COALESCE(ea.failures, 0) AS failures,
    COALESCE(ea.attempts, 0) AS attempts,
    COALESCE(ea.contexts, 0) AS contexts,
    COALESCE(ea.independent_evidence, 0) AS independent_evidence,
    COALESCE(ea.independent_successes, 0) AS independent_successes,
    ea.latest_evidence_at,
    ea.latest_procedure_activity_at,
    COALESCE(ua.usage_count, 0) AS usage_count,
    COALESCE(ua.verified_independent_usage, 0) AS verified_independent_usage,
    COALESCE(ua.independent_users, 0) AS independent_users,
    ua.latest_usage_at,
    EXISTS (SELECT 1 FROM credit_ledger_diagnostic cl WHERE cl.goal_id = gb.goal_id)
        AS credit_ledger_rows_observed
FROM goal_base gb
LEFT JOIN evidence_aggregate ea ON ea.goal_id = gb.goal_id
LEFT JOIN usage_aggregate ua ON ua.goal_id = gb.goal_id
ORDER BY gb.t_created DESC, ea.procedure_version DESC NULLS LAST, ea.procedure_row_id
"""
    return sql, params


class GoalRankingService:
    """Aggregate and rank a bounded Goal list without loading the corpus."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        scope: AccessScope = AccessScope.unrestricted(),
        tenant_scope: TenantScope = TenantScope.unrestricted(),
    ) -> None:
        self.pool = pool
        self.scope = scope or AccessScope.unrestricted()
        self.tenant_scope = tenant_scope or TenantScope.unrestricted()

    async def rank(
        self,
        goal_ids: Optional[Sequence[str]] = None,
        *,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        sql, params = build_goal_ranking_query(
            goal_ids=goal_ids,
            limit=limit,
            scope=self.scope,
            tenant_scope=self.tenant_scope,
            status=status,
        )
        rows = await self.pool.fetch(sql, *params)
        return self.shape_rows(rows)

    async def rank_goals(
        self,
        goal_ids: Optional[Sequence[str]] = None,
        *,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.rank(goal_ids, limit=limit, status=status)

    async def rank_unresolved(
        self,
        goal_ids: Optional[Sequence[str]] = None,
        *,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        items = [
            item for item in await self.rank(goal_ids, limit=limit, status=status)
            if item["state"] == "unresolved"
        ]
        for index, item in enumerate(items, start=1):
            item["rank"] = index
            item["of"] = len(items)
        return items

    async def rank_resolved(
        self,
        goal_ids: Optional[Sequence[str]] = None,
        *,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        items = [
            item for item in await self.rank(goal_ids, limit=limit, status=status)
            if item["state"] == "resolved"
        ]
        for index, item in enumerate(items, start=1):
            item["rank"] = index
            item["of"] = len(items)
        return items

    async def rank_unresolved_goals(
        self,
        goal_ids: Optional[Sequence[str]] = None,
        *,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.rank_unresolved(goal_ids, limit=limit, status=status)

    async def rank_resolved_goals(
        self,
        goal_ids: Optional[Sequence[str]] = None,
        *,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.rank_resolved(goal_ids, limit=limit, status=status)

    async def rank_goal_list(
        self,
        goal_ids: Optional[Sequence[str]] = None,
        *,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.rank(goal_ids, limit=limit, status=status)

    async def rank_for_goal(
        self,
        goal_id: str,
        *,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.rank([goal_id], limit=1, status=status)

    @staticmethod
    def shape_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        goal_rows: dict[str, dict[str, Any]] = {}
        for source in rows:
            row = dict(source)
            goal_id = str(row.get("goal_id") or row.get("id") or "")
            if not goal_id:
                continue
            goal_rows.setdefault(goal_id, row)
            if row.get("procedure_row_id") is not None:
                grouped.setdefault(goal_id, []).append(row)
        goals = list(goal_rows.values())
        ranked = rank_goal_candidates(goals, procedures_by_goal=grouped)
        for item in ranked:
            item["credit_ledger_rows_observed"] = bool(
                (goal_rows.get(str(item["goal_id"])) or {}).get("credit_ledger_rows_observed")
            )
            item["credit_ledger_is_not_commitment"] = True
        return ranked


async def rank_goals(
    pool: asyncpg.Pool,
    goal_ids: Optional[Sequence[str]] = None,
    *,
    limit: int = 50,
    scope: AccessScope = AccessScope.unrestricted(),
    tenant_scope: TenantScope = TenantScope.unrestricted(),
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Convenience async seam for future API integration."""
    return await GoalRankingService(
        pool, scope=scope, tenant_scope=tenant_scope,
    ).rank(goal_ids, limit=limit, status=status)


async def rank_procedures_for_goal(
    pool: asyncpg.Pool,
    procedure_ids: Sequence[str],
    *,
    scope: AccessScope = AccessScope.unrestricted(),
    context_key: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Convenience async seam for the per-Goal Procedure list."""
    return await ProcedureRankingService(
        pool, scope=scope, context_key=context_key,
    ).rank(procedure_ids, context_key=context_key)


def _legacy_procedure_bucket(
    verification_state: Optional[str], capability: Mapping[str, Any]
) -> str:
    return _legacy_bucket(verification_state, capability)


compute_bayesian_reliability = bayesian_reliability
bayesian_lower_bound = credible_lower_bound
calculate_bayesian_reliability = bayesian_reliability
compute_opportunity_score = unresolved_opportunity_score
compute_unmet_need = unmet_need_from_procedure_coverage
compute_freshness = freshness_percentile
compute_resolved_usefulness = resolved_usefulness_score


__all__ = [
    "CREDIBLE_INTERVAL_MASS",
    "CREDIBLE_LOWER_QUANTILE",
    "JEFFREYS_PRIOR_ALPHA",
    "JEFFREYS_PRIOR_BETA",
    "MIN_TRUSTED_ATTEMPTS_FOR_CANDIDATE",
    "GoalRankingService",
    "ProcedureRankingService",
    "_legacy_procedure_bucket",
    "bayesian_lower_bound",
    "bayesian_reliability",
    "build_goal_ranking_query",
    "calculate_bayesian_reliability",
    "canonical_outcome_evidence",
    "compute_bayesian_reliability",
    "compute_freshness",
    "compute_opportunity_score",
    "compute_resolved_usefulness",
    "compute_unmet_need",
    "credible_lower_bound",
    "demand_factor_from_commitments",
    "freshness_percentile",
    "format_goal_ranking",
    "is_canonical_outcome_evidence",
    "is_duplicate_or_derivative_evidence",
    "normalized_factor",
    "opportunity_score",
    "percentile_ranks",
    "procedure_ineligibility_reasons",
    "procedure_lane",
    "procedure_reliability",
    "public_goal_ranking",
    "rank_goal_candidates",
    "rank_goals",
    "rank_procedure_candidates",
    "rank_procedures_for_goal",
    "rank_resolved_goal_candidates",
    "rank_unresolved_goal_candidates",
    "resolved_usefulness_score",
    "score_goal_candidate",
    "score_procedure_candidate",
    "shape_goal_ranking",
    "unmet_need_from_procedure_coverage",
    "unresolved_opportunity_score",
]
