"""
Rule 1 gate: precondition/postcondition compatibility check.

Closes the gap Experiment 2 Hypothesis B's synthetic test exposed: the
matching mechanism (reuse_detection.py, hierarchy.py, subtask_reuse.py)
is text-only -- embedding + lexical over name+description -- with
NOTHING reading io_schema/success_criteria (confirmed by direct grep,
zero references). That means an adversarial pair sharing surface
phrasing but differing in actual preconditions (confirmed synthetically:
same "validate the output" instruction, DE means schema-conformance,
SWE means test-suite-passing) gets matched or not matched based
ENTIRELY on incidental embedding geometry, not on any designed
mechanism -- confirmed to flip between "wrongly full-matched" and
"correctly rejected" purely by varying how much shared phrasing
dominated the embedding, with the actual semantic difference never
inspected either way.

Deliberately additive, not a replacement for the embedding check:
postconditions are OPTIONAL structured tags stored in success_criteria
(task_nodes) / properties (knowledge_nodes) JSONB -- no schema
migration, matching the "no new SQL schema needed" pattern established
throughout this project. When EITHER side has no stated postconditions
(the common case for a raw incoming problem statement, which won't
have structured tags unless something upstream supplies them), the
gate passes trivially -- zero regression on existing behavior. It only
activates, and only tightens matching, when both the candidate node
and the query actually supply postconditions.

v1, deliberately narrow: postconditions are short string tags, not
formal logical predicates. "Compatible" means meaningful tag overlap
(Jaccard), not semantic entailment -- a placeholder for a real
formal-methods approach, not a claim to have solved general
precondition reasoning.

NOT YET WIRED into reuse_detection.py / hierarchy.py / subtask_reuse.py:
doing so means adding success_criteria/properties to their SQL SELECT
clauses (currently they only fetch id/name/description/similarity) and
threading query-side postconditions through decompose(). That's real,
well-defined follow-up work, deliberately not rushed into three
heavily-tested, load-bearing files in the same pass this module was
written -- see EXPERIMENT_PLAN_FINAL.md's next-steps section.
"""
from __future__ import annotations

from typing import Optional

# Below this, "not enough real overlap to trust". Deliberately
# permissive default (v1: favor letting borderline cases through over
# blocking legitimate matches on an unvalidated threshold) -- but still
# catches the zero-overlap adversarial case the synthetic test used.
# Needs calibration against real data before this number means much,
# same caveat as every other threshold in this codebase.
POSTCONDITION_OVERLAP_THRESHOLD = 0.25


def _normalize_tags(tags: list[str]) -> set[str]:
    return {t.strip().lower() for t in tags if t and t.strip()}


def postconditions_compatible(
    candidate_postconditions: Optional[list[str]],
    query_postconditions: Optional[list[str]],
    threshold: float = POSTCONDITION_OVERLAP_THRESHOLD,
) -> bool:
    """
    True (gate passes) when either side has no stated postconditions --
    can't check what isn't there, so don't block on it. When both sides
    have some, require Jaccard tag overlap >= threshold.
    """
    if not candidate_postconditions or not query_postconditions:
        return True
    a = _normalize_tags(candidate_postconditions)
    b = _normalize_tags(query_postconditions)
    if not a or not b:
        return True
    overlap = len(a & b) / len(a | b)
    return overlap >= threshold


def extract_postconditions(properties: Optional[dict]) -> Optional[list[str]]:
    """
    Reads the optional 'postconditions' key from a node's
    success_criteria (task_nodes) or properties (knowledge_nodes) JSONB
    -- same field name, different column depending on table; both are
    already schema-free JSONB, so both get read the same way.
    """
    if not properties or not isinstance(properties, dict):
        return None
    tags = properties.get("postconditions")
    if not tags or not isinstance(tags, list):
        return None
    return [str(t) for t in tags]
