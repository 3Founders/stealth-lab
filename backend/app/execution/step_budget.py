"""
How much of the earlier steps a step sees, and how much it is allowed to write.

Two constants used to decide this for every step in every Way (`way_runs.py`: `MAX_OUTPUT_IN_CONTEXT = 4000` and
`max_tokens=2500`). Both are right for a summary step and wrong for a step that writes a document, and both fail
silently: the run finishes, the page shows a plausible result, and the result was built from a fragment.

Measured against a real Way (docs/steps_sandboxes_filesystems.md §1): step 2 asks for a 1000-1500 word introduction
(~7,000 characters); step 3 then received 4,000 characters of it and wrote the abstract from the cut.

The rule here is one number per step, derived from what the step *produces*:

    a step that produces a file      gets a budget big enough to hold that file
    a step that produces prose       keeps the old budget, because that is what it was sized for

and the context a step receives is sized so that a step's own declared input is never cut — a `.tex` writer that
receives a truncated `.md` is not doing the step it was written for.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

# The old constants, kept as floors so a Way that ran yesterday runs the same way today.
BASELINE_TOKENS = 2500            # one step's reply, when the step declares no output cap
BASELINE_CONTEXT_CHARS = 4000     # one earlier output, for a step that produces prose

# How many characters we allow per token when deciding whether a budget can hold a file. Deliberately pessimistic
# (English prose runs nearer 4): a budget that is too small truncates a document, and truncation is the failure this
# module exists to prevent.
CHARS_PER_TOKEN = 3.5

# A ceiling. A step may not ask for an unbounded reply: the cost estimate's upper bound IS this number
# (docs/pricingestimation.md §4.3), and an uncapped step makes a Way unquotable.
MAX_STEP_TOKENS = 32_000
# How much of the earlier steps one step may be handed. Large enough for a full paper's worth of context; small
# enough that a ten-step Way does not send a million characters.
MAX_CONTEXT_CHARS = 240_000

# Files a step declares it reads are never cut to fit: they are the step's input, not a summary of it.
DEFAULT_FILE_CHARS = 24_000       # ~7,000 tokens, about a 12-page document


def produces_files(step: Mapping[str, Any]) -> bool:
    """Does this step hand back a file? (`produces`, db/222_step_produces.sql.)"""
    p = step.get("produces")
    if not p:
        return False
    if isinstance(p, str):
        return bool(p.strip())
    if isinstance(p, Mapping):
        return bool(p.get("name"))
    return bool(list(p)) if hasattr(p, "__iter__") else False


def output_cap_tokens(step: Mapping[str, Any]) -> int:
    """How many tokens this step's reply may use: its declared cap, else a document-sized default for a file step.

    This number is used three times, on purpose — as `max_tokens` for the call, as the upper bound of the cost
    estimate, and as the threshold at which `artifacts.looks_truncated` refuses to store a file. If they disagreed,
    a Way could be priced for one size of reply and produce another.
    """
    declared = step.get("output_cap_tokens")
    if isinstance(declared, int) and declared > 0:
        return min(declared, MAX_STEP_TOKENS)
    if produces_files(step):
        # Room for a real document: a literature review, a paper section, a source file with markup.
        return 8_000
    return BASELINE_TOKENS


def context_budget_chars(step: Mapping[str, Any]) -> int:
    """How many characters of earlier output this step may see.

    A prose step keeps the old 4,000: that is what it was sized for and changing it would change every existing
    Way's input. A step that produces a file sees far more, because it is assembling a document from earlier
    documents and a cut input is a corrupted output.
    """
    if produces_files(step):
        return MAX_CONTEXT_CHARS
    # A step that only *imports* files also needs room to read them.
    if step.get("imports"):
        return max(BASELINE_CONTEXT_CHARS, MAX_CONTEXT_CHARS // 2)
    return BASELINE_CONTEXT_CHARS


def imported_names(step: Mapping[str, Any]) -> list[str]:
    """The files this step reads from earlier steps, by the name an earlier step produced them under."""
    raw = step.get("imports")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    out: list[str] = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, Mapping) and isinstance(item.get("name"), str):
            out.append(item["name"].strip())
    return [n for n in dict.fromkeys(out) if n]


def never_cut(names: list[str]) -> set[str]:
    """The outputs a step wants whole. The caller keeps these out of the truncating path."""
    return {n for n in names if n}


def budget_note(step: Mapping[str, Any]) -> Optional[str]:
    """One sentence explaining a non-default budget, for the page. None when the step uses the baseline."""
    cap = output_cap_tokens(step)
    if cap == BASELINE_TOKENS:
        return None
    if produces_files(step):
        return (f"this step writes a file, so it gets {cap:,} tokens (~{int(cap * CHARS_PER_TOKEN):,} characters) "
                f"and sees up to {context_budget_chars(step):,} characters of the earlier steps")
    return f"this step is allowed {cap:,} tokens"
