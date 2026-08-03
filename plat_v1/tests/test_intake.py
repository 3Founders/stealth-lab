"""
The auto-match gate.

Similarity alone is not enough, and this is the test that says so: two tasks
whose descriptions an embedding model cannot tell apart are distinguished by
whether the caller's inputs fit their contracts.
"""
from __future__ import annotations

from uuid import uuid4


from app.models.task import TaskNode
from app.services.criteria import evaluate_criteria
from app.services.intake import IntakeService
from app.services.matching import Match
from tests.helpers import obj


class FakeMatcher:
    def __init__(self, matches: list[Match]):
        self._matches = matches

    async def search(self, query: str, top_k: int = 5) -> list[Match]:
        return self._matches[:top_k]


def task(name: str, inputs: dict[str, str]) -> TaskNode:
    return TaskNode(
        id=uuid4(),
        name=name,
        description=f"{name} description",
        input_schema=obj(inputs),
        output_schema=obj({"result": "string"}),
    )


async def test_a_strong_match_with_fitting_inputs_is_accepted():
    match = Match(task=task("extract_tables", {"pdf_path": "string"}), score=0.05)
    intake = await IntakeService(FakeMatcher([match]), threshold=0.03).assess(
        "get the tables out of this pdf", {"pdf_path": "/tmp/x.pdf"}
    )

    assert intake.matched
    assert intake.accepted.task.name == "extract_tables"


async def test_a_weak_match_falls_through_to_decomposition():
    match = Match(task=task("extract_tables", {"pdf_path": "string"}), score=0.01)
    intake = await IntakeService(FakeMatcher([match]), threshold=0.03).assess(
        "something quite different", {"pdf_path": "/tmp/x.pdf"}
    )

    assert not intake.matched
    assert "below the auto-match threshold" in intake.reason


async def test_schema_validation_is_the_real_gate():
    """
    "extract tables from a PDF" and "extract text from a PDF" are the same
    sentence to an embedding model. They are not the same task, and the
    contract is what says so.
    """
    text_task = task("extract_text", {"pdf_path": "string", "encoding": "string"})
    intake = await IntakeService(FakeMatcher([Match(task=text_task, score=0.9)])).assess(
        "extract the tables from this pdf", {"pdf_path": "/tmp/x.pdf"}
    )

    assert not intake.matched
    assert intake.schema_problems
    assert "encoding" in intake.reason


async def test_no_candidates_falls_through():
    intake = await IntakeService(FakeMatcher([])).assess("anything", {})
    assert not intake.matched
    assert "no existing task resembled" in intake.reason


# --- success criteria ------------------------------------------------------


def test_criteria_pass_when_everything_holds():
    assert evaluate_criteria(
        {"required_keys": ["rows"], "non_empty": ["rows"], "max_count": {"errors": 0}},
        {"rows": [1, 2], "errors": []},
    ) == []


def test_missing_required_key_fails():
    assert evaluate_criteria({"required_keys": ["rows"]}, {}) == [
        "required output 'rows' is missing"
    ]


def test_max_count_bounds_a_list():
    failures = evaluate_criteria({"max_count": {"errors": 0}}, {"errors": ["ragged row"]})
    assert "allowed at most 0" in failures[0]


def test_min_count_bounds_a_list():
    failures = evaluate_criteria({"min_count": {"regions": 1}}, {"regions": []})
    assert "need at least 1" in failures[0]


def test_file_exists_checks_the_filesystem(tmp_path):
    good = tmp_path / "out.xlsx"
    good.write_text("x")
    assert evaluate_criteria({"file_exists": ["path"]}, {"path": str(good)}) == []
    assert evaluate_criteria({"file_exists": ["path"]}, {"path": str(tmp_path / "nope")})


def test_an_unknown_criterion_is_reported_not_ignored():
    """A criterion nothing evaluates reads as a check that is happening."""
    failures = evaluate_criteria({"looks_good": True}, {})
    assert "unknown success criterion" in failures[0]


def test_no_criteria_means_no_failures():
    assert evaluate_criteria({}, {"anything": 1}) == []
