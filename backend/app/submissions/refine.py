"""Improve, specialize or abstract a Way's goal and its steps, before the author submits it.

A Way is written by a person, often from a half-formed idea: the goal is vague, one step does two things, or the goal
names one narrow case that they will want again for the next repository. This asks a model for one of three rewrites of
the WHOLE draft -- the goal, its description and every step -- and hands it back for review. Nothing is stored here and
nothing is submitted: the caller shows the proposal, and the person submits it through the normal route, so the same
intake rules, the same reviewer and the same rate limits apply to what they finally send.

    improve     the same job, said precisely: a goal that names the outcome, steps that each do one thing
    specialize  narrower: one concrete case (a repository, language, format, audience), named in the goal
    abstract    broader: the family of jobs this draft is an instance of, with the case details taken out

The model is the submission reviewer's own (`review.complete`: DeepSeek V4.1 Flash, then GLM-5.3 Flash), so this needs no
new provider or key. Its answer is parsed by `intake.parse`, so a rewrite that breaks a submission rule -- a human step
carrying a prompt, an automated step with nothing to do, a locator that is not a website/file/script/tool -- is refused
here instead of surfacing as a broken submission later.

Honest limit: the model cannot see the person's repository, accounts or files. The prompt forbids inventing a URL, a
tool or a path; a step whose locator was empty stays without one, and the proposal is a suggestion the person reviews,
not a fact about their machine.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from app.submissions import intake, review

log = logging.getLogger(__name__)

MODES = ("improve", "specialize", "abstract")
MODE_LABEL = {
    "improve": "Improved",
    "specialize": "Specialized",
    "abstract": "Abstracted",
}
MODE_NOTE = {
    "improve": "the same job, said precisely",
    "specialize": "narrower: one concrete case, named in the goal",
    "abstract": "broader: the family of jobs this draft belongs to",
}
MAX_INSTRUCTION = 500

SYSTEM = """You rewrite a kell Way -- a goal and the ordered steps that reach it -- before its author submits it.

Reply with ONLY a JSON object, no prose and no code fences:
{"goal": "...", "description": "...", "steps": [{"kind": "human", "title": "...", "description": "..."},
 {"kind": "automated", "title": "...", "prompt": "...", "locator": "..."}]}

Rules for every rewrite:
- goal: a short name for the outcome, at most 120 characters, no trailing period.
- description: one or two sentences on what the Way is for. It may be empty.
- 1 to 8 steps, in order. Each step does ONE thing.
- A human step has "description": exactly what the person does. It must not have a prompt or a locator.
- An automated step has "prompt" (what kell does) and/or "locator" (a website, file, script or tool name). Give at
  least one of the two.
- "title" is a short heading for the step; it may be left out and will be written from the words.
- Never invent a URL, a file path, a script, a tool name or a fact about the author's machine, repository or accounts.
  Keep an existing locator on the step it belongs to; if a step had none, do not add one.
- Keep the author's language. Keep the same number of steps unless the mode below allows splitting or merging.
- Write for the person who will run the Way: concrete verbs, no filler, no marketing.

The mode of this rewrite:
"""


def _step(s: intake.Step) -> dict:
    """One step in the form's shape: empty fields are left out, a locator is the classified {type, value}."""
    out: dict[str, Any] = {"kind": s.kind, "title": s.title}
    if s.description:
        out["description"] = s.description
    if s.prompt:
        out["prompt"] = s.prompt
    if s.locator:
        out["locator"] = s.locator
    return out


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("the answer is not an object")
    return data


async def refine(payload: Any, *, mode: str, instruction: str = "",
                 client: Optional[httpx.AsyncClient] = None) -> dict:
    """The draft as the person sent it -> the same Way, rewritten in `mode`. Raises SubmissionError with a sentence."""
    if mode not in MODES:
        raise intake.SubmissionError(f"mode is one of {', '.join(MODES)}")
    draft = intake.parse(payload)
    instruction = (instruction or "").strip()[:MAX_INSTRUCTION]
    body = {
        "goal": draft.goal,
        "description": draft.description,
        "steps": [{"kind": s.kind, "title": s.title, "description": s.description, "prompt": s.prompt,
                   "locator": s.locator} for s in draft.steps],
    }
    user = (f"Rewrite this Way ({mode}: {MODE_NOTE[mode]}).\n\n"
            f"Way now (JSON):\n{json.dumps(body, ensure_ascii=False, indent=2)}")
    if instruction:
        user += f"\n\nWhat the author wants changed, in their words: {instruction}"
    try:
        text, model = await review.complete(
            [{"role": "system", "content": SYSTEM + f"- {mode}: {MODE_NOTE[mode]}"},
             {"role": "user", "content": user}],
            json_mode=True, max_tokens=2000, client=client)
    except RuntimeError as exc:
        log.warning("refine: no model answered (%s)", exc)
        raise intake.SubmissionError("kell could not rewrite this Way just now; try again") from exc
    try:
        data = _json(text)
        out = intake.parse({"goal": data.get("goal") or draft.goal,
                            "description": data.get("description") or "",
                            "steps": data.get("steps")})
    except (ValueError, intake.SubmissionError) as exc:
        # The proposal is the model's, so a rule it broke is our problem, not a sentence to show the person as if
        # they had written it. One line, and the draft they wrote is untouched.
        log.warning("refine: the model's rewrite was not a valid Way (%s)", exc)
        raise intake.SubmissionError("kell's rewrite did not come back as a valid Way; nothing changed. Try again.") from exc
    return {
        "goal": out.goal,
        "description": out.description,
        "mode": mode,
        "mode_label": MODE_LABEL[mode],
        "mode_note": MODE_NOTE[mode],
        "steps": [_step(s) for s in out.steps],
        "model": model,
    }
