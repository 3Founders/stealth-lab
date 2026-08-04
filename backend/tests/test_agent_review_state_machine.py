"""
Tests for the agent review transition table (pure logic, no database).
"""
from __future__ import annotations

import pytest

from app.services.agent_review_state_machine import (
    IllegalTransition,
    TERMINAL,
    assert_transition,
    can_transition,
)


def test_ingested_is_the_only_true_initial_state():
    assert can_transition("ingested", "under_review")
    # ingested itself has no legal predecessor -- it's where an agent starts
    with pytest.raises(IllegalTransition):
        assert_transition("under_review", "ingested")


def test_happy_path_is_legal():
    assert can_transition("ingested", "under_review")
    assert can_transition("under_review", "pending_human_approval")
    assert can_transition("pending_human_approval", "approved")


def test_cannot_skip_under_review():
    """An agent must actually be reviewed before it can reach human approval."""
    assert not can_transition("ingested", "pending_human_approval")
    with pytest.raises(IllegalTransition):
        assert_transition("ingested", "pending_human_approval")


def test_cannot_skip_human_approval():
    """Passing automated review is not the same as being approved."""
    assert not can_transition("under_review", "approved")
    with pytest.raises(IllegalTransition):
        assert_transition("under_review", "approved")


def test_rejected_is_reachable_from_every_pre_decision_state():
    """
    Automated review can reject outright (Layer 1 failure), or a human
    can reject after seeing it -- both are the same terminal state,
    reachable from anywhere before a decision is made.
    """
    for state in ("ingested", "under_review", "pending_human_approval"):
        assert can_transition(state, "rejected")


def test_terminal_states_have_no_legal_successor():
    for terminal in TERMINAL:
        for target in ("under_review", "pending_human_approval", "approved", "rejected"):
            if target == terminal:
                continue
            assert not can_transition(terminal, target), (
                f"{terminal} should not be able to move to {target}"
            )


def test_approved_and_rejected_are_the_only_terminal_states():
    assert TERMINAL == frozenset({"approved", "rejected"})


def test_assert_transition_message_names_the_legal_predecessors():
    """The error should tell you what *would* have worked, not just fail silently."""
    with pytest.raises(IllegalTransition, match="under_review"):
        assert_transition("ingested", "pending_human_approval")
