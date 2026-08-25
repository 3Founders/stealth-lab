"""
Band 1.9a -- evidence boundary: validate, build outcomes-as-evidence,
gate the verified transition.

The storage half is db/24_evidence.sql (evidence [H] append-only with a
tombstone exception, procedure_evidence_stats view over it); this module
is the boundary half that produces and checks what those tables hold,
mirroring app/execution/plans.py's role for migration 23:

  invariant #3   every verified procedure has evidence ->
                 assert_verified_requires_evidence() refuses the
                 candidate->verified transition unless the evidence
                 view reports at least one live supporting row of the
                 required types. This is the contract-level gate: the
                 engine trigger lands in the same change that wires
                 evidence writes into the lifecycle path (see the
                 sequencing note closing db/24_evidence.sql -- gate and
                 writer arrive together, never a half-gate).
  invariant #12  capability is evidence-based, not model-brand-based ->
                 nothing in these builders reads producer identity:
                 brand/model strings may ride on created_by provenance
                 and can never enter a statistic -- db/24's view SELECTs
                 only type/direction/outcome/group/context columns.
  invariant #13  outcomes distinguishable from self-reports ->
                 outcome_to_evidence() refuses a success whose
                 success_criteria carry no explicit predicate/metrics;
                 the engine refuses the empty-dict form outright
                 (evidence_success_criteria_chk).
  invariant #19  historical records append-only -> the DDL freezes the
                 table except the t_invalid tombstone; this module
                 simply has no mutation API to misuse.

Pure functions only: nothing here touches a pool or connection, which
is what makes the whole contract provable offline
(tests/test_band1_9a_evidence.py).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, get_args
from uuid import UUID

from app.models.evidence import (
    Direction,
    Evidence,
    EvidenceType,
    OutcomeStatusLabel,
    Strength,
    TargetRef,
    TargetType,
    _FailureClassValue,
)

# Named configuration, not literals scattered through logic (same rule
# procedures.py applied to ticket 13's thresholds): the evidence types a
# verified procedure must stand on. Ticket 13's statistics are outcome-
# stream statistics -- runs and reproductions -- so witness types
# (human_review, documents...) corroborate but never substitute.
REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED: tuple[str, ...] = (
    "execution_result",
    "reproduction",
)

# Outcome-bearing types must declare what happened; the rest testify
# without a terminal status.
OUTCOME_BEARING_TYPES: tuple[str, ...] = ("execution_result", "reproduction")

EVIDENCE_TYPES: tuple[str, ...] = get_args(EvidenceType)
TARGET_TYPES: tuple[str, ...] = get_args(TargetType)
DIRECTIONS: tuple[str, ...] = get_args(Direction)
FAILURE_CLASSES: tuple[str, ...] = get_args(_FailureClassValue)
OUTCOME_STATUSES: tuple[str, ...] = get_args(OutcomeStatusLabel)


class EvidenceViolation(ValueError):
    """A payload would store unattributed strength, an unexplained
    success, or otherwise violate the evidence contracts. Callers
    surface this verbatim: it is a producer-side contract violation,
    not an internal error."""


# ------------------------------------------------------------ validators


def _require_target(target: TargetRef | Mapping[str, Any]) -> TargetRef:
    try:
        ref = target if isinstance(target, TargetRef) else TargetRef(**target)
    except Exception as exc:
        raise EvidenceViolation(f"V-EVD: malformed target reference ({exc})") from exc
    if ref.target_type not in TARGET_TYPES:
        raise EvidenceViolation(
            f"V-EVD: unknown target_type {ref.target_type!r} (valid: {TARGET_TYPES})"
        )
    # Invariant #2's discipline applied to evidence: procedures are
    # addressed as exact versions everywhere else in this repo; evidence
    # about them is no exception. Claims are row-id-addressed
    # (knowledge_nodes has no version column), implementations have no
    # version chain yet -- only procedure targets carry the requirement.
    if ref.target_type == "procedure" and ref.target_version is None:
        raise EvidenceViolation(
            "V-EVD: evidence targeting a procedure must pin its exact "
            "version -- versionless references are rejected"
        )
    return ref


def _check_success_criteria(success_criteria: Mapping[str, Any]) -> None:
    """Invariant #13's semantic half (the engine's half is the '{}' ban):
    a recorded success must say WHY it counted -- an explicit predicate
    expression and/or concrete criteria metrics. 'The model said it
    worked' is not criteria."""
    predicate = success_criteria.get("predicate")
    metrics = success_criteria.get("metrics")
    has_predicate = isinstance(predicate, str) and bool(predicate.strip())
    has_metrics = isinstance(metrics, Mapping) and len(metrics) > 0
    if not (has_predicate or has_metrics):
        raise EvidenceViolation(
            "V-EVD: a success outcome requires explicit success criteria -- "
            "success_criteria must carry a non-empty 'predicate' expression "
            "and/or a non-empty 'metrics' mapping; bare model-asserted "
            "success is rejected (invariant #13)"
        )


def validate_evidence(
    *,
    evidence_type: str,
    target: TargetRef | Mapping[str, Any],
    direction: str,
    strength_score: float,
    strength_method: Optional[str],
    outcome_status: Optional[str] = None,
    success_criteria: Optional[Mapping[str, Any]] = None,
    failure_class: Optional[str] = None,
    independence_group: Optional[str] = None,
    context_key: Optional[str] = None,
    extractor_version: Optional[str] = None,
    source_id: Optional[UUID] = None,
    content_ref: Optional[UUID] = None,
    created_by: Optional[str] = None,
    visibility: str = "public",
    owner_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    **passthrough: Any,
) -> Evidence:
    """Validate one evidence payload end to end and return the inert
    model ready for to_row()/INSERT. Raises EvidenceViolation with the
    boundary's own message before storage ever sees the payload.

    Scope/provenance columns ride along unvalidated here exactly as the
    V0 gate's stated split allows -- callers that ingest through the
    gate run validate_scope/validate_provenance themselves; this module
    owns the evidence-specific contracts."""
    if visibility not in ("public", "private"):
        raise EvidenceViolation(
            f"V-EVD: visibility must be 'public' or 'private', got {visibility!r}"
        )
    if evidence_type not in EVIDENCE_TYPES:
        raise EvidenceViolation(
            f"V-EVD: unknown evidence_type {evidence_type!r} (valid: {EVIDENCE_TYPES})"
        )
    if direction not in DIRECTIONS:
        raise EvidenceViolation(
            f"V-EVD: unknown direction {direction!r} (valid: {DIRECTIONS})"
        )

    ref = _require_target(target)

    try:
        strength = Strength(score=float(strength_score), method=(strength_method or "").strip())
    except Exception as exc:
        raise EvidenceViolation(
            f"V-EVD: strength must be {{score in [0,1], named method}} ({exc})"
        ) from exc

    if independence_group is not None and not independence_group.strip():
        # A blank group would silently merge every blank-grouped row
        # into one non-corroborating bucket -- worse than no group at
        # all (NULL = self-grouped). Engine teeth: same-named CHECK.
        raise EvidenceViolation(
            "V-EVD: independence_group must be NULL (self-grouped) or a "
            "named, non-blank group"
        )

    if outcome_status is not None and outcome_status not in OUTCOME_STATUSES:
        raise EvidenceViolation(
            f"V-EVD: unknown outcome_status {outcome_status!r} (valid: {OUTCOME_STATUSES})"
        )
    if evidence_type in OUTCOME_BEARING_TYPES and outcome_status is None:
        raise EvidenceViolation(
            f"V-EVD: {evidence_type} evidence must record its terminal status "
            "-- an outcome without one is a rumor about an outcome"
        )
    if evidence_type not in OUTCOME_BEARING_TYPES and outcome_status is not None:
        raise EvidenceViolation(
            f"V-EVD: {evidence_type} evidence carries no execution outcome -- "
            "only execution_result/reproduction rows do"
        )

    if failure_class is not None:
        if failure_class not in FAILURE_CLASSES:
            raise EvidenceViolation(
                f"V-EVD: unknown failure_class {failure_class!r} (valid: {FAILURE_CLASSES})"
            )
        if outcome_status != "failure":
            raise EvidenceViolation(
                "V-EVD: failure_class is null until a failure -- it cannot "
                "decorate a non-failure row (spec §36)"
            )

    criteria = dict(success_criteria or {})
    if outcome_status == "success":
        _check_success_criteria(criteria)
    elif criteria:
        raise EvidenceViolation(
            "V-EVD: success_criteria belong to successes -- failures classify "
            "via failure_class instead (spec §36)"
        )

    if extractor_version is not None and not extractor_version.strip():
        raise EvidenceViolation("V-EVD: extractor_version, when stamped, must be non-blank")
    if passthrough:
        unknown = ", ".join(sorted(passthrough))
        raise EvidenceViolation(f"V-EVD: unknown field(s) {unknown}")

    return Evidence(
        evidence_type=evidence_type,  # type: ignore[arg-type]
        target=ref,
        direction=direction,  # type: ignore[arg-type]
        strength=strength,
        independence_group=independence_group,
        context_key=context_key,
        source_id=source_id,
        content_ref=content_ref,
        outcome_status=outcome_status,  # type: ignore[arg-type]
        success_criteria=criteria,
        failure_class=failure_class,  # type: ignore[arg-type]
        extractor_version=extractor_version or None,
        created_by=created_by,
        visibility=visibility,  # type: ignore[arg-type]
        owner_id=owner_id,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
    )


# ------------------------------------------------- outcomes as evidence


def outcome_to_evidence(
    *,
    evidence_type: str,
    target: TargetRef | Mapping[str, Any],
    outcome_status: str,
    success_criteria: Optional[Mapping[str, Any]] = None,
    direction: Optional[str] = None,
    strength_score: float = 1.0,
    strength_method: str = "recorded_outcome",
    independence_group: Optional[str] = None,
    context_key: Optional[str] = None,
    failure_class: Optional[str] = None,
    extractor_version: Optional[str] = None,
    source_id: Optional[UUID] = None,
    content_ref: Optional[UUID] = None,
    created_by: Optional[str] = None,
    visibility: str = "public",
    owner_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
) -> Evidence:
    """Build validated execution_result / reproduction evidence from one
    recorded outcome.

    Direction defaults honestly rather than optimistically: a success
    SUPPORTS the procedure's claim to work under these conditions; a
    failure CONTRADICTS it. needs_rework states neither and must be
    directed explicitly -- silently counting rework as either support
    or contradiction would be the kind of lazy bookkeeping the trust
    layer exists to prevent.

    Default strength is maximal-with-method ('recorded_outcome'): a
    genuinely recorded run is the strongest witness this system has --
    weaker claims should say so via their own method string, not by
    under-reporting a real run.

    Invariant #12 note: NOTHING here accepts a brand/model argument.
    Producer identity belongs to created_by provenance and is invisible
    to every statistic computed over these rows.
    """
    if evidence_type not in OUTCOME_BEARING_TYPES:
        raise EvidenceViolation(
            f"V-EVD: outcome_to_evidence builds {OUTCOME_BEARING_TYPES} rows, "
            f"not {evidence_type!r}"
        )
    if direction is None:
        if outcome_status == "success":
            direction = "supports"
        elif outcome_status == "failure":
            direction = "contradicts"
        else:
            raise EvidenceViolation(
                "V-EVD: needs_rework evidence must state its direction "
                "explicitly -- it is neither support nor contradiction by default"
            )
    return validate_evidence(
        evidence_type=evidence_type,
        target=target,
        direction=direction,
        strength_score=strength_score,
        strength_method=strength_method,
        outcome_status=outcome_status,
        success_criteria=success_criteria,
        failure_class=failure_class,
        independence_group=independence_group,
        context_key=context_key,
        extractor_version=extractor_version,
        source_id=source_id,
        content_ref=content_ref,
        created_by=created_by,
        visibility=visibility,
        owner_id=owner_id,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
    )


# -------------------------------------------------- the verified gate


def assert_verified_requires_evidence(
    stats_row: Optional[Mapping[str, Any]],
) -> None:
    """Invariant #3's transition gate: moving a procedure version to
    `verified` stands on evidence, or it does not happen.

    Pass the procedure's procedure_evidence_stats row (db/24's view over
    live evidence); None means the view returned no row at all -- no
    live required-type evidence exists for this version. The gate counts
    INDEPENDENT supporting rows of the required types: two rows sharing
    an independence_group are one piece of evidence wearing two ids, and
    this gate refuses to be flattered by repetition (§9b capping applied
    at the earliest possible moment).
    """
    if stats_row is None:
        raise EvidenceViolation(
            "V-EVD: cannot verify -- the evidence view returns zero rows of "
            f"required types {REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED} for this "
            "procedure version (invariant #3: every verified procedure has "
            "evidence)"
        )
    try:
        supporting = int(stats_row["independent_supporting_required"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceViolation(
            f"V-EVD: stats row lacks a readable independent_supporting_required "
            f"count ({exc}) -- pass the procedure_evidence_stats row verbatim"
        ) from exc
    if supporting < 1:
        raise EvidenceViolation(
            "V-EVD: cannot verify -- zero independent supporting rows of "
            f"required types {REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED} "
            "(invariant #3: every verified procedure has evidence)"
        )
