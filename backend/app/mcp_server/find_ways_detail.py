"""`find_ways(detail="summary")`: the same answer with the bulk shortened.

Why: a find_ways result lands in the calling agent's context and is re-read on every later turn. In real
Claude Code sessions cache reads were ~97-99% of input tokens (~80% of the spend), so what the result
weighs costs more than how long it took. The summary keeps what the agent needs to *choose* (which Goal,
which Procedure, why, repo fit, alternatives, preconditions, how many steps and what each is about) and
shortens what it only needs when it *executes* (step text, check commands, bindings, the verified solution
and example bodies). Nothing is invented or reworded: strings are cut at a length and marked `clipped`.

The agent gets the rest with the same call and `detail="full"`. On a hosted server that repeat is served
from the governor's cache (the full reply is what is cached; shaping happens after), so it costs no second
lookup. This module is pure: reply string in, reply string out, and anything it does not understand comes
back unchanged.
"""
from __future__ import annotations

import json
from typing import Any

DETAILS = ("full", "summary")
STEP_CHARS = 140
CLIP_CHARS = 200
CLIP_ITEMS = 3
EXAMPLES_KEPT = 5

EXPAND_HINT = ("Steps and bodies are shortened. Call find_ways again with the same query (and repo_claims) and "
               "detail=\"full\" for every step in full; a hosted server answers the repeat from cache.")


def _cut(text: Any, chars: int) -> Any:
    if not isinstance(text, str) or len(text) <= chars:
        return text
    return text[:chars].rstrip() + "…"


def _clip(value: Any, chars: int = CLIP_CHARS, items: int = CLIP_ITEMS) -> Any:
    """Cut every string and every list to a bound, recursively, keeping the keys."""
    if isinstance(value, str):
        return _cut(value, chars)
    if isinstance(value, dict):
        return {k: _clip(v, chars, items) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, chars, items) for v in value[:items]]
    return value


def _step(step: Any) -> Any:
    if not isinstance(step, dict):
        return step
    out: dict[str, Any] = {"order": step.get("order"), "kind": step.get("kind"),
                           "do": _cut(step.get("do") or step.get("description"), STEP_CHARS)}
    for key in ("role", "subgoal_id"):
        if step.get(key):
            out[key] = step[key]
    if step.get("note"):
        out["note"] = _cut(step["note"], STEP_CHARS)
    out["has_check"] = bool(step.get("check"))
    out["has_binding"] = bool(step.get("binding"))
    return out


def _procedure(proc: Any) -> Any:
    if not isinstance(proc, dict):
        return proc
    out = {k: v for k, v in proc.items() if k not in ("steps", "verified_solution")}
    out["steps"] = [_step(s) for s in proc.get("steps") or []]
    if proc.get("verified_solution"):
        out["verified_solution"] = {**_clip(proc["verified_solution"]), "clipped": True}
    return out


def summarize(reply: str) -> str:
    try:
        body = json.loads(reply)
    except (TypeError, ValueError):
        return reply                                              # a REFUSED: ... text
    if not isinstance(body, dict):
        return reply
    shaped = False
    if isinstance(body.get("procedures"), list) and body["procedures"]:
        body["procedures"] = [_procedure(p) for p in body["procedures"]]
        shaped = True
    if isinstance(body.get("related_examples"), list) and body["related_examples"]:
        body["related_examples"] = _clip(body["related_examples"], items=EXAMPLES_KEPT)
        shaped = True
    if isinstance(body.get("suggested"), dict):
        body["suggested"] = {**_clip(body["suggested"]), "clipped": True}
        shaped = True
    if not shaped:
        return reply                                              # ambiguous / no_match / refused: already small
    body["detail"] = "summary"
    body["expand"] = EXPAND_HINT
    out = json.dumps(body, default=str)
    body["shortened_from_chars"] = len(reply)
    return json.dumps(body, default=str) if len(out) < len(reply) else reply
