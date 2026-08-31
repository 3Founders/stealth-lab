"""Offline tests for `app/services/context_compiler.py`.

Pure module -- no DB, no LLM, no DATABASE_URL needed. Proves:
  (a) the fixed priority ordering is real -- a lower-priority section that
      would blow the budget is dropped, never a higher-priority one;
  (b) sections are never partially truncated -- all-or-nothing;
  (c) a minimal call (just task_description) works and doesn't crash;
plus a few supporting behaviors (empty/None inputs, ordering respected even
when a middle section fits and a later one doesn't, invalid budget).
"""

import pytest

from app.services.context_compiler import compile_context, _estimate_tokens


def test_minimal_call_just_task_description():
    result = compile_context("fix the failing test")
    assert result["included"] == ["task_description"]
    assert result["dropped"] == []
    assert len(result["sections"]) == 1
    assert result["sections"][0]["content"] == "fix the failing test"
    assert result["total_tokens_estimate"] > 0


def test_all_sections_fit_when_budget_generous():
    result = compile_context(
        "do the thing",
        required_state={"branch": "main"},
        dependency_outputs=[{"node": 1, "output": "ok"}],
        scoped_claims=[{"claim": "X is true"}],
        procedure={"name": "deploy-procedure"},
        evidence=[{"success": True}],
        extra={"note": "misc"},
        token_budget=100_000,
    )
    assert result["included"] == [
        "task_description",
        "required_state",
        "dependency_outputs",
        "scoped_claims",
        "procedure",
        "evidence",
        "extra",
    ]
    assert result["dropped"] == []


def test_priority_ordering_is_real_lower_priority_dropped_first():
    """A tight budget that only fits the task description plus one small
    high-priority section must drop the lower-priority ones, never a
    higher-priority section in favor of a lower one."""
    task = "small task"
    required_state = {"k": "v"}  # small, priority 2
    # Deliberately large, low-priority sections that would blow any small budget.
    big_claims = [{"claim": "x" * 2000}]
    big_procedure = {"steps": ["y" * 2000]}
    big_evidence = [{"detail": "z" * 2000}]

    task_tokens = _estimate_tokens(task)
    req_tokens = _estimate_tokens(str({"k": "v"}))
    budget = task_tokens + req_tokens + 5  # enough for task + required_state, nothing more

    result = compile_context(
        task,
        required_state=required_state,
        scoped_claims=big_claims,
        procedure=big_procedure,
        evidence=big_evidence,
        token_budget=budget,
    )

    assert "task_description" in result["included"]
    assert "required_state" in result["included"]
    assert "scoped_claims" in result["dropped"]
    assert "procedure" in result["dropped"]
    assert "evidence" in result["dropped"]


def test_smaller_lower_priority_section_can_still_fit_after_bigger_one_dropped():
    """A big mid-priority section being dropped for exceeding budget must
    not block a smaller, lower-priority section that still fits."""
    task = "task"
    huge_claims = [{"claim": "x" * 5000}]  # priority 4, will not fit
    tiny_evidence = [{"ok": True}]  # priority 6, small, should still fit

    task_tokens = _estimate_tokens(task)
    tiny_tokens = _estimate_tokens(str(tiny_evidence))
    budget = task_tokens + tiny_tokens + 5

    result = compile_context(
        task,
        scoped_claims=huge_claims,
        evidence=tiny_evidence,
        token_budget=budget,
    )

    assert "scoped_claims" in result["dropped"]
    assert "evidence" in result["included"]


def test_sections_never_partially_truncated():
    """A section that doesn't fully fit is dropped whole, not truncated --
    its full content must never appear partially in `sections`."""
    task = "task"
    big_procedure = {"steps": ["s" * 10_000]}
    task_tokens = _estimate_tokens(task)
    budget = task_tokens + 1  # not enough for the big procedure section

    result = compile_context(task, procedure=big_procedure, token_budget=budget)

    assert "procedure" in result["dropped"]
    names_included = {s["name"] for s in result["sections"]}
    assert "procedure" not in names_included
    # Confirm no truncated/partial variant of it snuck into sections either.
    for s in result["sections"]:
        assert s["content"] != big_procedure


def test_empty_and_none_inputs_are_omitted_not_included_or_dropped():
    result = compile_context(
        "task",
        required_state={},
        dependency_outputs=[],
        scoped_claims=None,
        procedure=None,
        evidence=[],
        extra=None,
    )
    assert result["included"] == ["task_description"]
    assert result["dropped"] == []


def test_task_description_always_included_even_if_it_alone_exceeds_budget():
    huge_task = "t" * 40_000  # will estimate to well over a tiny budget
    result = compile_context(huge_task, required_state={"a": "b"}, token_budget=1)

    assert result["included"] == ["task_description"]
    assert result["sections"][0]["content"] == huge_task
    assert "required_state" in result["dropped"]
    # total reflects real usage, even though it exceeds the nominal budget
    assert result["total_tokens_estimate"] >= _estimate_tokens(huge_task)


def test_negative_budget_raises():
    with pytest.raises(ValueError):
        compile_context("task", token_budget=-1)


def test_token_estimate_matches_documented_heuristic():
    text = "abcd" * 10  # 40 chars -> 10 tokens under len//4
    assert _estimate_tokens(text) == 10
    assert _estimate_tokens("") == 0
    assert _estimate_tokens("abc") == 1  # max(1, 0) floor for nonempty text
