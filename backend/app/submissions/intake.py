"""What a submission is, checked before anything is stored.

    {"goal": "ML research", "description": "...",
     "steps": [{"kind": "human", "description": "Search 'xyz' on abc.com and copy the top 5 links"},
               {"kind": "automated", "prompt": "Summarise the papers behind those links", "locator": "https://..."}]}

A step is `human` (a description of what the person does) or `automated` (a prompt to kell, a locator, or both). The
kind can be left out: a step with only a description is human, one with a prompt or a locator is automated. A locator
is a website (http/https), a file, a script, or a tool; a bare string is classified, or pass {"type", "value"}.

The mode is never asked for: fully_human (every step human), fully_ai (every step automated), human_ai (both).
`preview()` says which before the person submits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

MAX_STEPS = 20
MAX_TEXT = 4000
MAX_NAME = 200
LOCATOR_TYPES = ("url", "file", "script", "tool")
SCRIPT_EXT = (".py", ".sh", ".bash", ".ps1", ".js", ".mjs", ".ts", ".rb", ".pl", ".bat", ".cmd", ".ipynb")
MODE_LABEL = {"fully_human": "Fully Human", "human_ai": "Human+AI", "fully_ai": "Fully AI"}


class SubmissionError(ValueError):
    """The submission cannot be stored as sent; the message says what to change."""


@dataclass
class Step:
    order: int
    kind: str                                   # human | automated
    title: str
    description: str = ""
    prompt: str = ""
    locator: Optional[dict] = None              # {type, value}

    def as_dict(self) -> dict:
        return {"order": self.order, "kind": self.kind, "title": self.title, "description": self.description,
                "prompt": self.prompt, "locator": self.locator}


@dataclass
class Submission:
    goal: str
    description: str
    steps: list[Step] = field(default_factory=list)

    @property
    def mode(self) -> str:
        return mode_of([s.kind for s in self.steps])


def mode_of(kinds: list[str]) -> str:
    kinds_set = set(kinds)
    if kinds_set == {"human"}:
        return "fully_human"
    if kinds_set == {"automated"}:
        return "fully_ai"
    return "human_ai"


def _text(value: Any, where: str, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SubmissionError(f"{where} must be text")
    text = value.strip()
    if len(text) > limit:
        raise SubmissionError(f"{where} is longer than {limit} characters")
    return text


def classify_locator(raw: Any, where: str) -> Optional[dict]:
    """A locator as {type, value}: a website, a file, a script, or a tool."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        kind = str(raw.get("type") or "").strip().lower()
        value = _text(raw.get("value"), f"{where}.value", 2000)
        if kind not in LOCATOR_TYPES:
            raise SubmissionError(f"{where}.type must be one of {', '.join(LOCATOR_TYPES)}")
        if not value:
            raise SubmissionError(f"{where}.value is empty")
        if kind == "url" and not re.match(r"^https?://", value, re.I):
            raise SubmissionError(f"{where}: a website locator starts with http:// or https://")
        return {"type": kind, "value": value}
    value = _text(raw, where, 2000)
    if not value:
        return None
    path = value.split("?", 1)[0].split("#", 1)[0].lower()
    if re.match(r"^https?://", value, re.I):
        return {"type": "script" if path.endswith(SCRIPT_EXT) else "url", "value": value}
    if path.endswith(SCRIPT_EXT):
        return {"type": "script", "value": value}
    if "/" in value or "\\" in value or re.search(r"\.[a-z0-9]{1,8}$", path):
        return {"type": "file", "value": value}
    if re.fullmatch(r"[A-Za-z0-9_.:@-]+(/[A-Za-z0-9_.:@-]+)?", value):
        return {"type": "tool", "value": value}
    raise SubmissionError(f"{where}: not a website, file, script or tool name; pass {{\"type\", \"value\"}}")


def _title(text: str) -> str:
    first = re.split(r"(?<=[.!?])\s|\n", text.strip(), maxsplit=1)[0].strip().rstrip(".")
    return first[:120] if len(first) <= 120 else first[:117].rstrip() + "..."


def parse(payload: Any) -> Submission:
    """The submission as sent, checked. Raises SubmissionError with a sentence the person can act on."""
    if not isinstance(payload, dict):
        raise SubmissionError("send an object: {goal, description, steps}")
    goal = _text(payload.get("goal"), "goal", MAX_NAME)
    if not goal:
        raise SubmissionError("goal is required (what this Way achieves, e.g. \"ML research\")")
    description = _text(payload.get("description"), "description")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise SubmissionError("add at least one step")
    if len(raw_steps) > MAX_STEPS:
        raise SubmissionError(f"at most {MAX_STEPS} steps; split the goal into smaller goals")
    steps: list[Step] = []
    for i, raw in enumerate(raw_steps, 1):
        where = f"step {i}"
        if isinstance(raw, str):
            raw = {"description": raw}
        if not isinstance(raw, dict):
            raise SubmissionError(f"{where} must be an object")
        description_i = _text(raw.get("description"), f"{where} description")
        prompt = _text(raw.get("prompt"), f"{where} prompt")
        locator = classify_locator(raw.get("locator"), f"{where} locator")
        kind = str(raw.get("kind") or "").strip().lower()
        kind = {"ai": "automated", "auto": "automated", "automatic": "automated", "person": "human"}.get(kind, kind)
        if not kind:
            kind = "automated" if (prompt or locator) else "human"
        if kind not in ("human", "automated"):
            raise SubmissionError(f"{where}: kind is human or automated")
        if kind == "human":
            if not description_i:
                raise SubmissionError(f"{where} is done by a person: describe what they do")
            if prompt or locator:
                raise SubmissionError(f"{where} is done by a person, so it takes a description only; make it automated "
                                      "to give kell a prompt or a locator")
        elif not (prompt or locator):
            raise SubmissionError(f"{where} is automated: give kell a prompt, a locator (a script, website, file or "
                                  "tool), or both")
        title = _text(raw.get("title"), f"{where} title", MAX_NAME) or _title(description_i or prompt or locator["value"])
        steps.append(Step(i, kind, title, description_i, prompt, locator))
    return Submission(goal, description, steps)


def preview(sub: Submission) -> dict:
    """What the person is shown before submitting: the mode it will be filed under, and the steps."""
    return {"goal": sub.goal, "mode": sub.mode, "mode_label": MODE_LABEL[sub.mode],
            "message": f"This will be submitted as {MODE_LABEL[sub.mode]}.",
            "steps": [s.as_dict() for s in sub.steps]}
