"""Proving tests for app/execution/implementations.py -- the closed
implementation-kind vocabulary + real registry, no DB, no LLM."""
from __future__ import annotations

import pytest

from app.execution.implementations import (
    IMPLEMENTATION_KINDS,
    ImplementationViolation,
    resolve_implementation,
    validate_implementation_hint,
)


def test_absent_hint_validates_to_none():
    assert validate_implementation_hint(None) is None


def test_single_string_hint_normalizes_to_a_one_tuple():
    assert validate_implementation_hint("deterministic") == ("deterministic",)


def test_list_hint_normalizes_to_an_ordered_tuple():
    assert validate_implementation_hint(["slm", "frontier"]) == ("slm", "frontier")


def test_every_closed_kind_validates():
    for kind in IMPLEMENTATION_KINDS:
        assert validate_implementation_hint(kind) == (kind,)


def test_unknown_kind_is_rejected_not_silently_accepted():
    with pytest.raises(ImplementationViolation):
        validate_implementation_hint("quantum_hivemind")


def test_unknown_kind_inside_a_list_is_also_rejected():
    with pytest.raises(ImplementationViolation):
        validate_implementation_hint(["frontier", "quantum_hivemind"])


def test_empty_list_hint_is_rejected():
    with pytest.raises(ImplementationViolation):
        validate_implementation_hint([])


def test_no_hint_resolves_to_the_default_supported_frontier_kind():
    resolution = resolve_implementation(None)
    assert resolution.kind == "frontier"
    assert resolution.supported is True
    assert resolution.strategy == "frontier_model_call"


def test_explicit_frontier_hint_resolves_supported():
    resolution = resolve_implementation(("frontier",))
    assert resolution.supported is True
    assert resolution.strategy is not None


def test_unimplemented_kind_reports_honest_not_supported_not_a_crash_not_a_silent_run():
    resolution = resolve_implementation(("slm",))
    assert resolution.kind == "slm"
    assert resolution.supported is False
    assert resolution.strategy is None
    assert "slm" in resolution.reason


def test_preference_list_falls_through_to_the_first_supported_candidate():
    """An ordered preference list picks the first REAL candidate, skipping
    unsupported ones earlier in the list -- proves the registry actually
    inspects each candidate rather than only ever looking at index 0."""
    resolution = resolve_implementation(("slm", "deterministic", "frontier"))
    assert resolution.kind == "frontier"
    assert resolution.supported is True


def test_preference_list_with_no_supported_candidate_reports_the_first_as_unsupported():
    resolution = resolve_implementation(("slm", "deterministic", "human"))
    assert resolution.supported is False
    assert resolution.kind == "slm"
