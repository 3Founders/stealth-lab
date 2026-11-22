"""Inputs and per-step questions for a Way (app/submissions/way_inputs.py).

The rules tested here are the ones that decide whether a person can actually start or continue a run:
optional-by-default inputs, a default satisfying a required input, and an answer that must be one the question
offers rather than anything at all.
"""
from __future__ import annotations

import pytest

from app.submissions.way_inputs import (
    Option,
    Question,
    WayInput,
    answers_valid,
    first_unanswered,
    missing_required,
)


def test_an_input_is_optional_unless_it_says_otherwise():
    """The default matters: most Ways run with no input, and requiring one by omission would block a run."""
    assert WayInput(name="topic").required is False


def test_a_required_input_with_no_value_blocks_the_run():
    assert missing_required([WayInput(name="topic", required=True)], {}) == ["topic"]


def test_a_blank_required_input_blocks_the_run():
    assert missing_required([WayInput(name="topic", required=True)], {"topic": "   "}) == ["topic"]


def test_a_default_satisfies_a_required_input():
    """Asking a person to confirm a value the author already chose is friction with no benefit."""
    assert missing_required([WayInput(name="fmt", required=True, default="pdf")], {}) == []


def test_an_optional_input_is_never_reported_missing():
    assert missing_required([WayInput(name="notes")], {}) == []


def test_a_choice_input_must_list_choices():
    with pytest.raises(ValueError):
        WayInput(name="fmt", type="choice").validate()


def test_a_choice_default_must_be_one_of_its_choices():
    with pytest.raises(ValueError):
        WayInput(name="fmt", type="choice", choices=["a", "b"], default="c").validate()


def _q(**kw) -> Question:
    base = dict(
        key="which_paper",
        question="Which paper did you mean?",
        options=[Option("Attention Is All You Need", "the transformer paper"),
                 Option("Sparse Attention", "the one about long context")],
    )
    base.update(kw)
    return Question(**base)


def test_a_question_needs_two_to_four_options():
    with pytest.raises(ValueError):
        _q(options=[Option("only one")]).validate()
    with pytest.raises(ValueError):
        _q(options=[Option(str(i)) for i in range(5)]).validate()


def test_a_question_cannot_repeat_a_label():
    with pytest.raises(ValueError):
        _q(options=[Option("a"), Option("a")]).validate()


def test_a_valid_question_passes_and_serialises_with_the_fields_the_ui_needs():
    q = _q(header="Paper")
    q.validate()
    d = q.as_dict()
    assert d["multiSelect"] is False
    assert len(d["options"]) == 2
    assert d["options"][0]["label"] == "Attention Is All You Need"


def test_questions_are_asked_one_at_a_time():
    """Four at once spends the person's attention; a fifth discovered later spends it twice."""
    a, b = _q(key="one"), _q(key="two")
    assert first_unanswered([a, b], {}) is a
    assert first_unanswered([a, b], {"one": "Sparse Attention"}) is b
    assert first_unanswered([a, b], {"one": "x", "two": "y"}) is None


def test_an_answer_must_be_one_the_question_offers():
    q = _q()
    assert answers_valid(q, "Sparse Attention")
    assert not answers_valid(q, 42)


def test_free_text_is_allowed_only_where_the_question_says_so():
    q = _q()
    assert answers_valid(q, "neither, it was a different paper")
    strict = _q(allow_free_text=False)
    assert not answers_valid(strict, "neither, it was a different paper")
    assert answers_valid(strict, "Sparse Attention")


def test_multi_select_answers_must_all_be_labels():
    q = _q(multi_select=True)
    assert answers_valid(q, ["Sparse Attention", "Attention Is All You Need"])
    assert not answers_valid(q, ["Sparse Attention", "something else"])
    assert not answers_valid(q, [])
