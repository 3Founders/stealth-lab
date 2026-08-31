"""Bounded, ranked context digest for a single task.

`compile_context()` is the missing piece a caller (a future MCP tool, or
`find_best_way`) needs when it wants to hand an LLM a *task-scoped* slice of
everything StealthLab knows, instead of dumping full history/transcripts at
it. It mirrors `app/execution/plans.py::compile_plan`'s shape: pure,
synchronous, deterministic over caller-supplied data. It does not call an
LLM and does not touch the database -- fetching the real claims/evidence/
procedure is the caller's job (e.g. via `retrieval.py`'s search or a claim
lookup); this module only ranks and budgets what's handed to it.

RANKING (fixed, not configurable -- this ordering is the point):
    1. current task description   (always included; never dropped)
    2. hard prerequisites (`required_state`)
    3. direct dependency outputs (`dependency_outputs`)
    4. relevant scoped claims (`scoped_claims`)
    5. the matched procedure (`procedure`)
    6. relevant evidence (`evidence`)
    7. anything else optional (`extra`)

Sections are filled in that order until the next one would exceed
`token_budget`; it is then dropped whole and reported in `dropped`, and
lower-priority sections are still tried afterward (a big claims section
doesn't block a small evidence section from fitting). No section is ever
partially truncated to fit -- it's all-or-nothing per section.

TOKEN ESTIMATE IS APPROXIMATE. `_estimate_tokens` uses `len(text) // 4`,
a rough characters-per-token heuristic, not a real tokenizer. Good enough
to keep a prompt roughly bounded; do not treat `total_tokens_estimate` as
an exact count.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# Rough chars-per-token heuristic (not a real tokenizer -- see module docstring).
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Honest approximation, not exact: `len(text) // 4`. Real tokenization
    varies by model and content; this is a cheap stand-in good enough to
    keep a compiled context roughly bounded without pulling in a tokenizer
    dependency for a first version."""
    return max(1, len(text) // _CHARS_PER_TOKEN) if text else 0


def _render(value: Any) -> str:
    """Turn arbitrary caller-supplied data (str, dict, list of dicts, ...)
    into the text that would actually be sent to an LLM, for estimation
    and inclusion purposes."""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, default=str, sort_keys=True)


def _make_section(name: str, priority: int, value: Any) -> Optional[dict[str, Any]]:
    """Build a candidate section dict, or None if there's nothing to include
    (an empty/None value at this slot contributes nothing and is never
    reported as included or dropped)."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    text = _render(value)
    return {
        "name": name,
        "priority": priority,
        "content": value,
        "text": text,
        "tokens_estimate": _estimate_tokens(text),
    }


def compile_context(
    task_description: str,
    *,
    required_state: Optional[dict] = None,
    dependency_outputs: Optional[list[dict]] = None,
    scoped_claims: Optional[list[dict]] = None,
    procedure: Optional[dict] = None,
    evidence: Optional[list[dict]] = None,
    extra: Optional[dict] = None,
    token_budget: int = 4000,
) -> dict:
    """Assemble a bounded, ranked context digest for `task_description`.

    Pure function: no LLM calls, no DB access. Callers fetch the real
    claims/evidence/procedure data (e.g. via retrieval.py's search, or a
    claim lookup) and hand it in already scoped to this task; this function
    only ranks and budgets what it's given.

    Returns:
        {
            "sections": [ {name, priority, content, tokens_estimate}, ... ]
                # in the fixed priority order, included sections only
            "included": [ "task_description", "required_state", ... ],
            "dropped": [ "evidence", ... ],
            "total_tokens_estimate": int,
        }

    Sections are filled strictly in priority order (1 highest) until the
    next candidate section would push the running total over `token_budget`;
    that section is dropped whole (never truncated) and the ranking
    continues to the next, lower-priority candidate -- a section that
    doesn't fit does not block a smaller, lower-priority one from fitting
    afterward.

    `task_description` (priority 1) is always included in full and is never
    subject to the budget -- a caller that can't even afford to state the
    task has nothing useful to compile. If it alone exceeds `token_budget`,
    the budget is treated as exhausted for every other section (all of them
    report as dropped), but `task_description` itself still comes back
    whole, and `total_tokens_estimate` reflects the real total, even though
    it exceeds the nominal budget.
    """
    if token_budget < 0:
        raise ValueError("token_budget must be >= 0")

    candidates: list[Optional[dict[str, Any]]] = [
        _make_section("task_description", 1, task_description),
        _make_section("required_state", 2, required_state),
        _make_section("dependency_outputs", 3, dependency_outputs),
        _make_section("scoped_claims", 4, scoped_claims),
        _make_section("procedure", 5, procedure),
        _make_section("evidence", 6, evidence),
        _make_section("extra", 7, extra),
    ]

    sections: list[dict[str, Any]] = []
    included: list[str] = []
    dropped: list[str] = []
    running_tokens = 0

    for candidate in candidates:
        if candidate is None:
            continue
        name = candidate["name"]
        cost = candidate["tokens_estimate"]

        if name == "task_description":
            # Always included, never dropped, never truncated -- see docstring.
            sections.append(
                {
                    "name": name,
                    "priority": candidate["priority"],
                    "content": candidate["content"],
                    "tokens_estimate": cost,
                }
            )
            included.append(name)
            running_tokens += cost
            continue

        if running_tokens + cost > token_budget:
            dropped.append(name)
            continue

        sections.append(
            {
                "name": name,
                "priority": candidate["priority"],
                "content": candidate["content"],
                "tokens_estimate": cost,
            }
        )
        included.append(name)
        running_tokens += cost

    return {
        "sections": sections,
        "included": included,
        "dropped": dropped,
        "total_tokens_estimate": running_tokens,
    }
