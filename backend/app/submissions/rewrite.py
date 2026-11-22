"""Rewrite a submitted Way's steps from one sentence, the way the canvas's "Change it" edits a Way (2026-11-18).

A rejected submission could only be fixed by hand, one step at a time. Here the person says what to change ("make step 2
use the arXiv API instead of scraping", or just "fix what the reviewer rejected"), and kell proposes the steps rewritten,
with the reviewer's reasons in view. Nothing is stored: the proposal comes back to the form, the person reads and edits it,
and sending it is an ordinary fix (api.fix_steps), reviewed again like any other.

The proposal keeps the number of steps and their order: adding or removing a step is the person's call, made with the
form's own buttons. A step the model leaves as it was is not sent again, so an approved step is not re-reviewed for nothing.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.submissions import intake

MAX_INSTRUCTION = 2000

PROMPT = """You rewrite the steps of a "Way": a goal and the ordered steps that achieve it. Each step is done either by a
person (kind "human": a description of what the person does) or by kell, an AI agent (kind "automated": a prompt saying
what kell does, and optionally a locator: a public https link to a website, a file or a script, or a tool's name).

An AI reviewer has reviewed every step. A rejected step carries the reviewer's reason. You get the Way, each step's status
and reason, and the person's instruction. Rewrite the steps so that they follow the instruction and answer the reviewer's
reasons. If the instruction is empty or only says to fix it, fix exactly what the reviewer rejected.

Rules:
- Return every step, in the same order and the same number. Never add or remove a step.
- Change only what the instruction or a rejection asks for. Leave an approved step word for word unless told otherwise.
- Keep a step's kind unless the instruction says who should do it.
- Never invent a link, a tool, a file or a fact. A locator stays as it was, is removed, or is one the instruction gives.
- A local path (C:\\..., /home/..., ~/...) is never a locator: kell cannot open the person's computer.
- Titles are short (a few words). Descriptions and prompts are concrete and say what "done" looks like.

Answer with JSON only:
{"steps": [{"order": 1, "kind": "human" | "automated", "title": "...", "description": "...", "prompt": "...",
            "locator": "..." }], "note": "one sentence on what you changed"}
"description" is for a human step, "prompt" (and "locator", if any) for an automated one; leave the others out."""


def _locator_text(loc: Any) -> str:
    if isinstance(loc, dict):
        return str(loc.get("value") or "")
    return str(loc or "")


def _current(sub: dict) -> list[dict]:
    out = []
    for s in sub.get("steps") or []:
        entry = {"order": s["order"], "kind": s["kind"], "title": s.get("title") or "", "status": s.get("status") or ""}
        if s["kind"] == "human":
            entry["description"] = s.get("description") or ""
        else:
            entry["prompt"] = s.get("prompt") or ""
            if _locator_text(s.get("locator")):
                entry["locator"] = _locator_text(s.get("locator"))
        if s.get("status") == "rejected" and s.get("reason"):
            entry["reviewer_reason"] = s["reason"]
        out.append(entry)
    return out


def _wire(step: dict) -> dict:
    """A step as the submit form and fix_step take it."""
    out = {"kind": step["kind"], "title": step.get("title") or ""}
    if step["kind"] == "human":
        out["description"] = step.get("description") or ""
    else:
        if step.get("prompt"):
            out["prompt"] = step["prompt"]
        if step.get("locator"):
            out["locator"] = step["locator"]
    return out


def _same(a: dict, b: dict) -> bool:
    keys = ("kind", "title", "description", "prompt", "locator")
    return all(str(a.get(k) or "").strip() == str(b.get(k) or "").strip() for k in keys)


def parse_proposal(text: str, current: list[dict]) -> dict:
    """The model's answer -> {steps, changed, note}. Each proposed step is checked with intake's own rules; a step that
    does not parse, or a reply with the wrong number of steps, keeps the step as it was (and says so)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0) if m else text)
    except (TypeError, ValueError) as exc:
        raise intake.SubmissionError("kell could not rewrite these steps; try saying the change another way") from exc
    raw = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(raw, list) or len(raw) != len(current):
        raise intake.SubmissionError("kell's rewrite did not keep the steps; try again, or fix the step by hand")
    by_order = {int(s.get("order") or i + 1): s for i, s in enumerate(raw) if isinstance(s, dict)}
    steps: list[dict] = []
    changed: list[int] = []
    kept: list[int] = []
    for cur in current:
        order = cur["order"]
        new = by_order.get(order)
        before = _wire(cur)
        if not isinstance(new, dict):
            steps.append({"order": order, **before}); kept.append(order); continue
        kind = "human" if str(new.get("kind") or cur["kind"]) == "human" else "automated"
        cand = {"kind": kind, "title": str(new.get("title") or cur["title"] or "")[: intake.MAX_NAME]}
        if kind == "human":
            cand["description"] = str(new.get("description") or new.get("prompt") or "")[: intake.MAX_TEXT]
        else:
            if new.get("prompt"):
                cand["prompt"] = str(new["prompt"])[: intake.MAX_TEXT]
            if new.get("locator"):
                cand["locator"] = _locator_text(new["locator"])[:2000]
        try:
            intake.parse({"goal": "-", "steps": [cand]})
        except intake.SubmissionError:
            steps.append({"order": order, **before}); kept.append(order); continue
        steps.append({"order": order, **cand})
        if not _same(cand, before):
            changed.append(order)
    note = str(data.get("note") or "").strip()[:400] if isinstance(data, dict) else ""
    if kept:
        note = (note + " " if note else "") + f"Step {', '.join(map(str, kept))} kept as it was (the rewrite was not usable)."
    return {"steps": steps, "changed": changed, "note": note}


async def propose(sub: dict, instruction: str, *, client: Optional[Any] = None) -> dict:
    """-> {steps, changed, note, model}. One model call (the reviewer's own models); nothing is written."""
    from app.services.untrusted import wrap_untrusted
    from app.submissions.review import complete

    current = _current(sub)
    if not current:
        raise intake.SubmissionError("this submission has no steps to rewrite")
    body = json.dumps({"goal": sub.get("goal_name") or "", "goal_description": sub.get("description") or "",
                       "steps": current}, ensure_ascii=False, indent=1)
    ask = (instruction or "").strip()[:MAX_INSTRUCTION] or "Fix what the reviewer rejected."
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": wrap_untrusted(body, label="submitted_way")
                 + "\n\nThe person's instruction:\n" + wrap_untrusted(ask, label="instruction")}]
    try:
        text, model = await complete(messages, json_mode=True, max_tokens=3000, client=client)
    except RuntimeError as exc:
        raise intake.SubmissionError(f"kell could not reach its model to rewrite the steps ({exc}); try again") from exc
    out = parse_proposal(text, current)
    out["model"] = model
    return out
