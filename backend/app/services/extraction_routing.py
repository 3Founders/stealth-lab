"""
Cost-aware model routing for trajectory semantic extraction
(trajectory-ingestion-hardening task, Sec 14). "Do not require the
strongest model for every trace" -- cheap model first, escalate only
when a real signal justifies the extra spend.

Provider-neutral by construction: this module never talks to a vendor
directly. It only decides WHICH model name to ask for; the caller passes
that model name into whatever `client.chat.completions.create(...)`-
shaped object it already has configured (the same interface
`trajectory_semantics.py`, `procedure_extraction/strategies.py`, and
`app/debate/panel.py` all use) -- General Compute, OpenRouter, or local,
whichever this deployment has configured. No new SDK, no hardcoded
vendor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config import settings


@dataclass(frozen=True)
class ModelChoice:
    model: str
    tier: str  # "cheap" | "strong"
    escalated: bool
    escalation_reason: Optional[str] = None


def _average_confidence(confidence_summary: Optional[dict]) -> Optional[float]:
    if not confidence_summary:
        return None
    value = confidence_summary.get("avg_confidence")
    return value if isinstance(value, (int, float)) else None


def choose_extraction_model(
    *,
    event_count: int,
    outcome: Optional[str] = None,
    prior_confidence_summary: Optional[dict] = None,
    goal_ambiguous: bool = False,
    claim_conflict: bool = False,
    malformed_prior_attempt: bool = False,
) -> ModelChoice:
    """
    Pure decision function -- no I/O, trivially testable, retunable at
    call time via `app.config.settings` (never an inlined literal, same
    discipline `trace_worker.TRIVIAL_MERGE_MAX_EVENTS`/
    `OVERSIZE_SUBDIVIDE_EVENTS` already established).

    Escalates to the strong model when ANY of:
      - a prior attempt at this same episode produced malformed/
        unparseable output (a fresh attempt at the same model is unlikely
        to do better);
      - the episode is unusually large (more tool calls -> more room for
        a cheap model to lose the thread);
      - the trajectory's outcome is 'failure' (task Sec 16: failure
        trajectories are first-class and worth extracting correctly, not
        the ones to economize on);
      - Goal matching landed in the ambiguous LLM-adjudication tier for
        2+ candidate goals (the caller passes this in -- this module does
        not itself talk to goals.py);
      - a produced Claim conflicted with an existing one (same: caller-
        supplied signal);
      - a prior pass's own `confidence_summary.avg_confidence` was below
        the configured floor.

    Returns the CHEAP model when none of the above apply -- the common
    case, so most extractions stay inexpensive by default.
    """
    reasons: list[str] = []

    if malformed_prior_attempt:
        reasons.append("prior_attempt_malformed")
    if event_count > settings.trajectory_extraction_escalation_max_events:
        reasons.append(f"large_trajectory_{event_count}_events")
    if outcome == "failure":
        reasons.append("failure_trajectory")
    if goal_ambiguous:
        reasons.append("ambiguous_goal_matching")
    if claim_conflict:
        reasons.append("conflicting_claims")

    avg_confidence = _average_confidence(prior_confidence_summary)
    if avg_confidence is not None and avg_confidence < settings.trajectory_extraction_escalation_min_confidence:
        reasons.append(f"low_prior_confidence_{avg_confidence:.2f}")

    if reasons:
        return ModelChoice(
            model=settings.trajectory_extraction_strong_model,
            tier="strong",
            escalated=True,
            escalation_reason=";".join(reasons),
        )
    return ModelChoice(
        model=settings.trajectory_extraction_cheap_model,
        tier="cheap",
        escalated=False,
    )
