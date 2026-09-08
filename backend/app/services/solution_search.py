"""
Solution Search (directive Phase 7 -- "Google for how to do something").

A single, blended, ranked list over `procedure` and `task` hits, where a
directly-matching Task CAN outrank a more complex Procedure. This module
is PURE PRESENTATION-LAYER COMPOSITION over `app.services.domain_search.
search_global` (reused verbatim, unmodified, uncalled-into differently
than any other caller) -- it adds no retrieval mechanism, no applicability
logic, and no new storage of its own.

=============================================================================
THE CENTRAL DESIGN DECISION -- READ THIS BEFORE CHANGING THE MERGE LOGIC
=============================================================================

CLAUDE.md's hard rule: "Retrieval fuses by RRF; applicability is a
non-compensatory cascade. A violated precondition is a disqualification,
not a low score. These two composition rules are deliberately opposite --
keep them apart." `domain_search.py`'s own docstring explains exactly why
it never cross-ranks procedure/task/claim results: a procedure's score
(RRF-fused similarity, folded through `find_applicable_procedures`' own
capability-aware ranking of CASCADE SURVIVORS ONLY) and a task's score
(plain RRF over vector+lexical hits, no cascade at all) are not the same
kind of number. One is "how good is this candidate, given it already
passed a hard pass/fail gate"; the other is "how well did this text match,
with no gate at all." There is no principled conversion between them.
Building a formula that max()-es or weights the two together would
FABRICATE a comparability that does not exist -- exactly the violation
the rule warns against, and exactly what this module refuses to do.

WHAT THIS MODULE DOES INSTEAD: it takes the two lists domain_search.py
ALREADY produces, each already correctly, honestly ordered by its own
real mechanism (`_search_procedures`'s cascade+RRF for procedures,
`_rrf_search_leg`'s RRF for tasks) -- and interleaves them by RANK
POSITION, never by comparing their score VALUES.

STRATEGY CHOSEN: round-robin by rank position across types --
1st-of-procedure, 1st-of-task, 2nd-of-procedure, 2nd-of-task, ... Each
type list is walked in its own already-correct order; the two walks are
simply zipped. No score from either list is ever read, compared, or
combined -- the merge key is a list INDEX, not a relevance number, so
there is nothing here that resembles a fabricated cross-type score.

WHY ROUND-ROBIN, NOT A TIERING RULE: a tiering rule ("an applicability-
verified procedure or a task with a strong lexical/semantic match both
count as 'strong'") was considered and rejected. Defining "strong" for a
task requires picking a threshold on its RRF score, and defining the same
threshold for a procedure requires picking one on ITS similarity/
capability-fused score -- two DIFFERENT numeric scales again, so the
threshold pair itself would be an implicit, unproven claim that "task RRF
score >= T1" and "procedure fused score >= T2" mean the same thing in
matching strength. That is the same fabrication one level down, just
hidden behind a threshold instead of a formula. Round-robin makes no such
claim: it says "each type's own honest #1 pick is worth showing before
either type's #2 pick," which is a presentation preference about
diversity of the results shown, not a relevance judgment between the two
picks. This is the more conservative of the two options this module's
own directive offered, chosen deliberately for that reason -- flagged
prominently here and in the final report for review.

HOW A TASK "OUTRANKS" A PROCEDURE UNDER THIS RULE: the directive's ask
("a directly matching Task CAN outrank a more complex Procedure") is
satisfied structurally: task[0] is always interleaved ahead of
procedure[1], procedure[2], ... regardless of how those procedures'
internal fused scores compare to task[0]'s RRF score -- because rank
POSITION, not score VALUE, drives the merge. A task that is its type's
single best hit is never buried behind a long tail of procedures the way
a naive "procedures always come first" grouping would bury it.

WHAT THIS MODULE WILL NEVER DO: assign the same result a numeric field
with a name like "relevance" or "combined_score" computed FROM both
legs' native scores. The `native_score` field on each result is carried
through UNCHANGED from its own leg, explicitly labelled as
NOT-cross-comparable, for transparency/debugging only -- never used by
the merge itself.

=============================================================================
SCOPE: procedure + task only, no claims
=============================================================================

Claims are propositions ("X is true of Y"), not "ways to do something" --
the directive's own SS10-11 framing is procedures/tasks/compositions.
Including claims in a blended "how do I do X" list would put assertions
next to actions, which is a category error this module avoids by
construction: `search_global` is always called with
`object_types=["procedure", "task"]`, never `"claim"`.

A claim CAN still genuinely constrain a specific procedure result --
`domain_search`'s own procedure leg reuses `find_applicable_procedures`,
whose survivors already passed a precondition cascade that may reference
real claims. This module surfaces those as a per-result `claims`
side-channel (see HYDRATION below) rather than folding them into the
ranked list itself.

=============================================================================
HYDRATION: get_solution_view for a bounded top-N of procedure hits
=============================================================================

A blended result's `capability`/`claims` fields are richer than what
`domain_search`'s own procedure leg dict carries (that leg intentionally
returns only the RRF/cascade shape, not a full capability estimate --
computing one is a real query, not free). Rather than fetch that for
EVERY procedure hit (N+1 for a large result set), only the first
`_HYDRATE_CAP` procedure hits IN BLENDED ORDER get a real, targeted
`procedure_graph_api.get_solution_view` lookup. Procedure hits beyond the
cap keep `capability=None`/`claims=[]` -- an honest "not computed for
this hit", never a fabricated zero or a silently reused value from a
different hit. Task hits are never hydrated this way: `task_api.py`'s own
docstring establishes there is no first-class task-level capability
signal in this schema at all (capability would have to be aggregated over
a task's dependent procedures, a materially heavier lookup out of
proportion for a search result list) -- task hits keep
`capability=None`/`claims=[]` unconditionally, named as "no task-level
capability signal exists", not "unknown."

=============================================================================
HONEST EMPTY / LOW-CONFIDENCE RESULT
=============================================================================

When both legs return nothing, this returns `results: []` with a real
`reason` string -- never a fabricated "best" pick, matching
`domain_search.py`'s own posture (`find_best_way`'s honest-empty
contract) and `search_global`'s own honest-underfill behavior.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope
from app.services.domain_search import search_global
from app.services.embeddings import Embedder
from app.services.procedure_graph_api import get_solution_view

# Only the first N procedure hits, IN BLENDED ORDER, get the heavier
# get_solution_view() hydrate (real capability estimate + precondition
# claims). A fixed, small, documented cap -- proportionate to a search
# result list a human actually reads, not N+1 heavy for a large `limit`.
_HYDRATE_CAP = 5

INTERLEAVE_STRATEGY = "round_robin_by_rank_position"


def _round_robin(type_lists: dict[str, list[dict]], order: list[str]) -> list[tuple[str, dict, int]]:
    """Zips each type's own already-correctly-ordered list by INDEX, never
    by score. Returns (object_type, item, native_rank) triples, native_rank
    1-based within that type's own list. See module docstring's central
    design section for why index-based zipping, not a score comparison."""
    merged: list[tuple[str, dict, int]] = []
    max_len = max((len(type_lists.get(t, [])) for t in order), default=0)
    for i in range(max_len):
        for t in order:
            items = type_lists.get(t, [])
            if i < len(items):
                merged.append((t, items[i], i + 1))
    return merged


def _base_result(object_type: str, item: dict, native_rank: int) -> dict[str, Any]:
    """Shapes one blended-list entry, reusing domain_search's own per-type
    fields verbatim rather than inventing new field names (see task
    instructions). `applicable`/`verification`/`capability`/`provenance`
    are populated where a real value exists and left honestly `None`
    where it does not (see module docstring's HYDRATION section for the
    task-capability case)."""
    if object_type == "procedure":
        return {
            "type": "procedure",
            "id": item.get("id"),
            # Human-facing first (plan Part 12/13): the display name/
            # description, not the machine slug. `name` is carried as the
            # secondary technical label.
            "title": item.get("display_name") or item.get("name"),
            "name": item.get("name"),
            "goal": item.get("display_description") or item.get("goal"),
            "applicability_summary": item.get("applicability_summary"),
            "relevance_label": item.get("relevance_label"),
            "relevance_reason": item.get("relevance_reason"),
            "evidence_summary": item.get("evidence_summary"),
            "failure_modes": item.get("failure_modes") or [],
            # A procedure hit here only exists because it survived
            # find_applicable_procedures' hard-constraint cascade AND the
            # relevance gate (domain_search._search_procedures) -- so
            # `applicable` is honestly True for every procedure result,
            # not a fabricated pass/fail this module re-derives.
            "applicable": True,
            "verification": {
                "verification_state": item.get("verification_state"),
                "staleness": item.get("staleness"),
                "availability": item.get("availability"),
                "approval_status": item.get("approval_status"),
            },
            "capability": None,  # filled in by _hydrate_procedure for the top-N cap
            "provenance": item.get("provenance"),  # enriched by _hydrate_procedure
            "claims": [],  # filled in by _hydrate_procedure for the top-N cap
            "scope": item.get("scope") or {},
            "scope_type": item.get("scope_type"),
            "scope_entity_id": item.get("scope_entity_id"),
            "version": item.get("version"),
            # NOT cross-comparable to a task's native_score -- see module
            # docstring. Debug-only; never the user-facing meaning of match.
            "native_score": item.get("similarity_score"),
            "native_rank": native_rank,
        }
    if object_type == "task":
        return {
            "type": "task",
            "id": item.get("id"),
            "title": item.get("name"),
            "goal": item.get("description"),
            # Tasks carry no applicability cascade in this schema at all
            # (no preconditions/invariants gate on a task_nodes row) --
            # `None` honestly means "not applicability-gated", never a
            # fabricated True/False implying a check that never ran.
            "applicable": None,
            "verification": None,
            # No first-class task-level capability signal exists
            # (task_api.py's own docstring) -- never computed here, see
            # module docstring's HYDRATION section.
            "capability": None,
            "provenance": None,
            "claims": [],
            "scope_type": item.get("scope_type"),
            "scope_entity_id": item.get("scope_entity_id"),
            # NOT cross-comparable to a procedure's native_score.
            "native_score": item.get("score"),
            "native_rank": native_rank,
            "matched_by": item.get("matched_by"),
        }
    raise ValueError(f"solution_search: unexpected object_type {object_type!r}")  # pragma: no cover


async def _hydrate_procedure(pool: asyncpg.Pool, result: dict, *, scope: AccessScope) -> None:
    """In-place enrich ONE blended procedure result with a real
    get_solution_view() lookup -- capability estimate + precondition
    claims + provenance. Only called for the first `_HYDRATE_CAP`
    procedure hits in blended order (see module docstring). Honest no-op
    (leaves capability=None/claims=[]) if the row went invisible/missing
    between the search leg's fetch and this lookup -- a real, benign
    race, not an error."""
    view = await get_solution_view(pool, result["id"], scope=scope)
    if view is None:
        return
    result["capability"] = view.get("capability")
    result["provenance"] = view.get("provenance")
    result["claims"] = view.get("claims") or []


async def search_solutions(
    pool: asyncpg.Pool,
    query: str,
    *,
    scope: AccessScope,
    project_id: Optional[str] = None,
    repository_id: Optional[str] = None,
    constraints: Optional[dict[str, Any]] = None,
    limit: int = 20,
    embedder: Optional[Embedder] = None,
) -> dict[str, Any]:
    """
    "Google for how to do something": ONE blended, ranked list of
    procedure + task hits. See the module docstring for the full design
    rationale -- this function's own body is a thin composition, all the
    real decisions are documented above.

    `constraints` is a small passthrough to `search_global`'s own
    `filters` escape hatch (`current_scope`, `require_verified`,
    `invariant_bindings`) plus an optional `scope_type` key (domain_search
    accepts `scope_type` as its own top-level kwarg; this module folds it
    into `constraints` rather than adding a fourth top-level scope
    parameter, since `project_id`/`repository_id` already cover the two
    scope_type values callers overwhelmingly want).

    `limit`: the size of the FINAL BLENDED list. Each underlying leg is
    asked for up to `limit` of its own results (domain_search's own
    per-type limit) so that, whichever type contributes more to the first
    `limit` round-robin slots, enough candidates exist for the merge to
    fill them honestly -- an under-fill (fewer than `limit` total) is
    still possible and never padded, same posture domain_search itself
    documents for its own per-type limit.
    """
    constraints = constraints or {}
    scope_type = constraints.get("scope_type")
    filters = {
        k: v for k, v in constraints.items()
        if k in ("current_scope", "require_verified", "invariant_bindings")
    }

    raw = await search_global(
        pool, query,
        object_types=["procedure", "task"],
        scope_type=scope_type,
        repository_id=repository_id,
        project_id=project_id,
        filters=filters,
        limit=limit,
        scope=scope,
        embedder=embedder,
    )

    procedure_hits = raw["results"].get("procedure", [])
    task_hits = raw["results"].get("task", [])

    merged = _round_robin(
        {"procedure": procedure_hits, "task": task_hits},
        order=["procedure", "task"],
    )
    merged = merged[:limit]

    results = [_base_result(object_type, item, native_rank) for object_type, item, native_rank in merged]

    hydrated_count = 0
    for result in results:
        if result["type"] != "procedure":
            continue
        if hydrated_count >= _HYDRATE_CAP:
            break
        await _hydrate_procedure(pool, result, scope=scope)
        hydrated_count += 1

    response: dict[str, Any] = {
        "query": query,
        "results": results,
        "counts": {
            "procedure": len(procedure_hits),
            "task": len(task_hits),
            "blended": len(results),
        },
        "interleave_strategy": INTERLEAVE_STRATEGY,
        "note": (
            "results are interleaved by rank POSITION within each type's own "
            "honest ordering (round-robin), never by comparing a procedure's "
            "fused similarity+capability score against a task's plain RRF "
            "score on one numeric scale -- CLAUDE.md forbids fabricating that "
            "comparability, and this module does not attempt it. See "
            "app/services/solution_search.py's module docstring."
        ),
    }
    if not results:
        response["reason"] = (
            "no procedure or task matched this query under either leg's own "
            "real ranking mechanism -- honest empty result, not a fabricated "
            "pick"
        )
    return response
