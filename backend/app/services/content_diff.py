"""
Line-level content diffing, computed in real Python (difflib), not left
for the LLM to discover unaided while reasoning in prose.

Same fix pattern as temporal_conflict.py's compute_overlap(), applied to
a different, confirmed real failure: the debate panel was asked to
compare two knowledge nodes' content and identify genuine differences
for a SYNTHESIS/MERGE resolution. Real result, three separate attempts:
every one produced a confident, specific, but FALSE claim about what
differed -- "word-for-word identical" when it wasn't, "lacks status
filtering" (twice) when the exact same filtering language was present
in both. All three failures were about WHAT differs, not about
correctly judging whether a real difference matters -- exactly the
shape temporal_conflict.py already fixed once for date comparison.

This module computes the actual line-level diff once, in code, and the
caller injects it into the debate prompt as a stated fact (see
knowledge_conflict.py's MECHANICALLY_COMPUTED_CONTENT_DIFF). The panel's
job becomes: given a verified-correct diff, decide what it MEANS (is
this addition worth merging in, is this omission a real gap) -- not:
correctly notice what differs in the first place, which is exactly
where it failed three times.
"""
from __future__ import annotations

import difflib
import re


def _normalize_line(line: str) -> str:
    """Collapse whitespace, lowercase -- so trivial formatting
    differences (extra space, a period) don't register as a real
    content difference."""
    return re.sub(r"\s+", " ", line).strip().lower()


def compute_content_diff(text_a: str, text_b: str) -> dict:
    """
    Returns a structured, real diff: which normalized SENTENCES appear
    only in A, only in B, or in both.

    Sentence-level, not line-level: a real bug was caught testing this
    against the actual Task A/B trajectories -- the same sentence
    (e.g. the status-filtering step) soft-wraps across DIFFERENT
    physical lines in each text (different surrounding word counts
    shift the wrap point), so line-level diffing falsely reported
    genuinely shared content as unique to one side -- ironically the
    same class of false-difference error this module exists to
    prevent, just moved into the mechanical layer instead of the LLM.
    Collapsing whitespace and splitting on sentence boundaries first
    fixes this.

    Sentences under 15 chars are dropped from the only-in-X lists
    (fragments, list markers aren't meaningful content differences).
    """
    def _sentences(text: str) -> list[str]:
        collapsed = re.sub(r"\s+", " ", (text or "")).strip()
        # Split on sentence-ending punctuation followed by a space and
        # a capital/digit -- conservative, avoids splitting on "e.g."
        # or decimal numbers mid-sentence.
        parts = re.split(r"(?<=[.!?:])\s+(?=[A-Z0-9])", collapsed)
        return [p.strip() for p in parts if p.strip()]

    sents_a = _sentences(text_a)
    sents_b = _sentences(text_b)
    norm_a = {_normalize_line(s): s for s in sents_a}
    norm_b = {_normalize_line(s): s for s in sents_b}

    only_a_keys = set(norm_a) - set(norm_b)
    only_b_keys = set(norm_b) - set(norm_a)
    common_keys = set(norm_a) & set(norm_b)

    only_in_a = [norm_a[k] for k in only_a_keys if len(k) >= 15]
    only_in_b = [norm_b[k] for k in only_b_keys if len(k) >= 15]

    # Preserve original order (set iteration order isn't source order)
    only_in_a = [s for s in sents_a if s in only_in_a]
    only_in_b = [s for s in sents_b if s in only_in_b]

    similarity_ratio = difflib.SequenceMatcher(None, text_a or "", text_b or "").ratio()

    return {
        "identical": not only_a_keys and not only_b_keys,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
        "n_common_sentences": len(common_keys),
        "similarity_ratio": round(similarity_ratio, 4),
    }
