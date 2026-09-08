"""
Relevance gate (plan Part 6 / Part 7).

WHERE THIS SITS IN THE PIPELINE
------------------------------
    query
      -> access filtering            (visibility_predicate, already upstream)
      -> retrieval                   (_fetch_candidate_pool: cost + vector + lexical RRF)
      -> applicability / hard gate   (check_hard_constraints -- disqualifies)
      -> RELEVANCE GATE              (this module -- drops weak matches, never re-ranks)
      -> capability / evidence rank  (find_applicable_procedures' survivor fusion)
      -> presentation

The applicability cascade answers "is this procedure *allowed* for this
context". It does NOT answer "is this procedure actually *about* what the
user asked". A procedure can pass every hard constraint and still be a
vocabulary coincidence -- "rotate the Postgres connection secret" for a
query about rotating an AWS IAM key. This gate removes those.

IT IS A FILTER, NOT A SCORE. It never changes the order of what survives;
it only removes hits whose best relevance signal is below a measured
cutoff, and it is allowed to return zero. "No good match" is a
first-class outcome (Part 6) -- callers must not pad.

THE CUTOFF IS MEASURED, NOT GUESSED
----------------------------------
RELEVANCE_GATE_MIN_SIMILARITY and RELEVANCE_LABEL_STRONG_SIMILARITY come
from scripts/eval_retrieval_quality.py run against
backend/tests/data/retrieval_eval_v1.jsonl (a labelled retrieval set
spanning exact / paraphrase / vocab-mismatch / near-domain / generic /
overlapping-terms-wrong-intent / no-match queries). The sweep picks the
similarity cutoff that maximises F1 (relevant := human label >= 2)
subject to the no-match bucket returning zero results in >= 90% of its
queries. The chosen value, the sweep table, and the measured
precision/recall at that point are recorded in
backend/tests/data/retrieval_eval_v1.report.json and summarised in the
module CHANGELOG below. Re-running the harness after a corpus or
representation change is how this number is revised -- never by taste.

CHANGELOG
  relgate_v1 (2026-09-08): cutoff 0.6839, strong band 0.7317, set from
    retrieval_eval_v1.report.json. Corpus: 2478 procedures embedded with
    local:mxbai-embed-large @ retrieval-document procdoc_v1 (paid
    Gemini/Voyage quotas exhausted; the repo's built-in local-model path
    was used so query and corpus share one space). Eval set: 57 queries /
    855 labelled candidates across 8 buckets. Selection rule: max F1 s.t.
    the no-match bucket returns zero results in >= 90% of its queries
    (it does so at 100% for every cutoff >= 0.55 -- the fixture-junk
    similarity floor tops out at 0.53). At 0.6839: precision 0.695,
    recall 0.525, F1 0.598, P@3 0.766, nDCG@10 0.579. Recall is bounded
    by mxbai-embed-large's weaker vocabulary-shift handling and will rise
    if the corpus is re-embedded with a stronger model -- re-run
    scripts/eval_retrieval_quality.py --measure and update these two
    numbers from the new report, never by taste.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

RELEVANCE_GATE_VERSION = "relgate_v1"

# --- measured parameters (scripts/eval_retrieval_quality.py) --------------
# Operates on cosine similarity in [0, 1] == 1 - (embedding <=> query),
# which find_applicable_procedures already attaches as `_similarity_score`.
#
# Measured 2026-09-08 -- see retrieval_eval_v1.report.json and the module
# CHANGELOG. Set None only to deliberately disable the gate (it then fails
# open, inert); never set a hand-picked number here.
RELEVANCE_GATE_MIN_SIMILARITY: Optional[float] = 0.6839
RELEVANCE_LABEL_STRONG_SIMILARITY: Optional[float] = 0.7317


_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "to", "of", "for", "and", "or", "in", "on", "with",
    "how", "do", "i", "my", "is", "it", "this", "that", "when", "use",
    "using", "can", "should", "need", "want", "get", "make", "run",
})


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS and len(w) > 2}


def relevance_label(similarity: Optional[float]) -> Optional[str]:
    """Human-facing label for a hit that PASSED the gate, or None when the
    label bands are not yet measured. Never invents confidence language:
    only "strong" / "relevant", both backed by the measured bands.
    """
    if similarity is None or RELEVANCE_GATE_MIN_SIMILARITY is None:
        return None
    if (
        RELEVANCE_LABEL_STRONG_SIMILARITY is not None
        and similarity >= RELEVANCE_LABEL_STRONG_SIMILARITY
    ):
        return "strong"
    if similarity >= RELEVANCE_GATE_MIN_SIMILARITY:
        return "relevant"
    return None


def passes_relevance_gate(similarity: Optional[float]) -> bool:
    """True if a hit with this similarity should be presented. Fail OPEN
    when the cutoff is not yet measured (the gate is inert, not a guessed
    0.x) -- this is the only safe default and it is loud in the code, not
    hidden."""
    if RELEVANCE_GATE_MIN_SIMILARITY is None:
        return True
    if similarity is None:
        # A survivor with no vector to score (embedding-less row ranked on
        # capability alone) is NOT dropped by a gate it cannot be measured
        # against -- capability ranking already vouched for it.
        return True
    return similarity >= RELEVANCE_GATE_MIN_SIMILARITY


def apply_relevance_gate(
    hits: Iterable[Mapping[str, Any]],
    *,
    score_key: str = "similarity_score",
) -> list[dict]:
    """Return only the hits at or above the measured similarity cutoff,
    order preserved. Zero results is a valid return. Never pads, never
    re-ranks, never lowers the bar to hit a count."""
    kept: list[dict] = []
    for hit in hits:
        h = dict(hit)
        if passes_relevance_gate(h.get(score_key)):
            kept.append(h)
    return kept


def relevance_reason(query: str, procedure: Mapping[str, Any]) -> Optional[str]:
    """A deterministic, non-LLM explanation of WHY a surviving hit matched:
    the meaningful words the query shares with the procedure's own
    human-facing text (display_name / display_description / goal /
    applicability). Built only from the query and the procedure's OWN
    fields -- it cannot leak anything the caller could not already see for
    this (already access-filtered) row. Returns None when there is no
    honest overlap to point at (e.g. a pure-semantic paraphrase match)."""
    q = _tokens(query)
    if not q:
        return None
    proc_text = " ".join(
        str(procedure.get(k) or "")
        for k in ("display_name", "display_description", "goal", "applicability_summary", "name")
    )
    overlap = sorted(q & _tokens(proc_text))
    if not overlap:
        return None
    shown = ", ".join(overlap[:4])
    return f"Your query and this procedure both mention {shown}."
