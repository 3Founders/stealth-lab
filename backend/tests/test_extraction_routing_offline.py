"""Offline tests for cost-aware extraction model routing
(trajectory-ingestion-hardening task, Sec 14). `choose_extraction_model`
is pure -- no I/O, no LLM call, no database."""
from __future__ import annotations

from app.config import settings
from app.services.extraction_routing import choose_extraction_model


def test_default_case_stays_on_the_cheap_model():
    choice = choose_extraction_model(event_count=10)
    assert choice.tier == "cheap"
    assert choice.model == settings.trajectory_extraction_cheap_model
    assert choice.escalated is False
    assert choice.escalation_reason is None


def test_failure_trajectory_escalates():
    choice = choose_extraction_model(event_count=10, outcome="failure")
    assert choice.escalated is True
    assert choice.model == settings.trajectory_extraction_strong_model
    assert "failure_trajectory" in choice.escalation_reason


def test_large_trajectory_escalates():
    choice = choose_extraction_model(
        event_count=settings.trajectory_extraction_escalation_max_events + 1,
    )
    assert choice.escalated is True
    assert "large_trajectory" in choice.escalation_reason


def test_trajectory_at_exactly_the_threshold_does_not_escalate():
    choice = choose_extraction_model(
        event_count=settings.trajectory_extraction_escalation_max_events,
    )
    assert choice.escalated is False


def test_malformed_prior_attempt_escalates():
    choice = choose_extraction_model(event_count=5, malformed_prior_attempt=True)
    assert choice.escalated is True
    assert "prior_attempt_malformed" in choice.escalation_reason


def test_ambiguous_goal_matching_escalates():
    choice = choose_extraction_model(event_count=5, goal_ambiguous=True)
    assert choice.escalated is True
    assert "ambiguous_goal_matching" in choice.escalation_reason


def test_conflicting_claims_escalates():
    choice = choose_extraction_model(event_count=5, claim_conflict=True)
    assert choice.escalated is True
    assert "conflicting_claims" in choice.escalation_reason


def test_low_prior_confidence_escalates():
    low = settings.trajectory_extraction_escalation_min_confidence - 0.1
    choice = choose_extraction_model(
        event_count=5, prior_confidence_summary={"avg_confidence": low},
    )
    assert choice.escalated is True
    assert "low_prior_confidence" in choice.escalation_reason


def test_high_prior_confidence_does_not_escalate():
    high = min(settings.trajectory_extraction_escalation_min_confidence + 0.3, 1.0)
    choice = choose_extraction_model(
        event_count=5, prior_confidence_summary={"avg_confidence": high},
    )
    assert choice.escalated is False


def test_missing_confidence_summary_is_not_treated_as_low_confidence():
    choice = choose_extraction_model(event_count=5, prior_confidence_summary=None)
    assert choice.escalated is False


def test_multiple_reasons_are_all_recorded():
    choice = choose_extraction_model(event_count=5, outcome="failure", claim_conflict=True)
    assert choice.escalated is True
    assert "failure_trajectory" in choice.escalation_reason
    assert "conflicting_claims" in choice.escalation_reason


def test_never_hardcodes_a_vendor_name_in_the_chosen_model_strings():
    """Provider-neutral: the model strings come entirely from settings,
    never a hardcoded vendor literal inside this module."""
    import inspect
    import app.services.extraction_routing as er

    src = inspect.getsource(er)
    for vendor_literal in ("anthropic.Anthropic(", "openai.OpenAI(", "AsyncAnthropic(", "AsyncOpenAI("):
        assert vendor_literal not in src
