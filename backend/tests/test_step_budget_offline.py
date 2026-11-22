"""How much a step may see and write (app/execution/step_budget.py, docs/steps_sandboxes_filesystems.md §1).

The defect these pin is silent rather than loud. A real Way asked one step for a 1000-1500 word introduction and the
next step received 4,000 characters of it — so the abstract was written from a cut-off review and the run reported
success. Every test here asserts a number a Way's correctness depends on.
"""
from __future__ import annotations

from app.execution import step_budget as sb
from app.execution.way_runs import imported_bodies


# --- the budget follows what a step produces ---------------------------------------------------------------

def test_a_prose_step_keeps_the_baseline_budget():
    """Existing Ways must not change: a summary step is what 2500/4000 was sized for."""
    step = {"order": 1, "prompt": "Summarise this"}
    assert sb.output_cap_tokens(step) == sb.BASELINE_TOKENS
    assert sb.context_budget_chars(step) == sb.BASELINE_CONTEXT_CHARS


def test_a_step_that_writes_a_document_gets_room_for_one():
    """"1000-1500 words" is ~7000 characters; 2500 tokens cannot hold it."""
    step = {"order": 2, "prompt": "Write the review", "produces": ["literaturereview.md"]}
    cap = sb.output_cap_tokens(step)
    assert cap >= 8_000
    assert cap * sb.CHARS_PER_TOKEN >= 7_000 * 1.5, "a 1500-word document must fit with room to spare"


def test_a_document_step_sees_far_more_of_the_earlier_steps():
    step = {"order": 3, "produces": ["main.tex"]}
    assert sb.context_budget_chars(step) > sb.BASELINE_CONTEXT_CHARS * 10


def test_a_declared_cap_wins_over_the_default():
    step = {"produces": ["x.tex"], "output_cap_tokens": 3000}
    assert sb.output_cap_tokens(step) == 3000


def test_a_declared_cap_cannot_exceed_the_ceiling():
    """An uncapped step makes a Way unquotable: the cost estimate's bound IS this number."""
    step = {"produces": ["x.tex"], "output_cap_tokens": 10_000_000}
    assert sb.output_cap_tokens(step) == sb.MAX_STEP_TOKENS


def test_a_step_that_imports_files_also_gets_a_roomier_context():
    step = {"order": 3, "imports": ["literaturereview.md"]}
    assert sb.context_budget_chars(step) > sb.BASELINE_CONTEXT_CHARS


def test_the_budget_is_stated_in_words_a_page_can_show():
    note = sb.budget_note({"produces": ["main.tex"]})
    assert note and "file" in note and "tokens" in note


def test_a_baseline_step_has_nothing_to_explain():
    assert sb.budget_note({"prompt": "summarise"}) is None


# --- imports: the literature.md -> .tex case ----------------------------------------------------------------

def _run_with_review() -> dict:
    return {"outputs": [
        {"order": 2, "title": "Review", "actor": "ai", "output": "# Review\n\nA full body of text.",
         "files": [{"name": "literaturereview.md", "object_key": "s3://b/abc"}]},
    ]}


def test_an_import_resolves_to_the_body_an_earlier_step_wrote():
    bodies, missing = imported_bodies(_run_with_review(), {"order": 3, "imports": ["literaturereview.md"]})
    assert missing == []
    assert "A full body of text." in bodies["literaturereview.md"]


def test_an_import_nothing_produced_is_named_not_silently_empty():
    """A step handed an empty string writes a plausible document from nothing — the worst possible outcome."""
    bodies, missing = imported_bodies(_run_with_review(), {"order": 3, "imports": ["literaturereview.md",
                                                                                  "refs.bib"]})
    assert missing == ["refs.bib"]
    assert "refs.bib" not in bodies


def test_a_step_with_no_imports_asks_for_nothing():
    bodies, missing = imported_bodies(_run_with_review(), {"order": 3})
    assert bodies == {} and missing == []


def test_imports_accept_a_name_or_a_list():
    assert sb.imported_names({"imports": "a.md"}) == ["a.md"]
    assert sb.imported_names({"imports": ["a.md", "b.tex"]}) == ["a.md", "b.tex"]
    assert sb.imported_names({"imports": [{"name": "c.bib"}]}) == ["c.bib"]


def test_duplicate_imports_are_mentioned_once():
    assert sb.imported_names({"imports": ["a.md", "a.md"]}) == ["a.md"]


def test_a_file_is_not_a_file_when_the_field_is_empty():
    assert sb.produces_files({"produces": None}) is False
    assert sb.produces_files({"produces": ""}) is False
    assert sb.produces_files({"produces": []}) is False
    assert sb.produces_files({"produces": ["x.md"]}) is True
