"""
What a Way needs before it can run: inputs, and questions it must ask.

Two things, kept apart on purpose.

**Inputs** are what the *author* declares a Way takes — a name, a type, whether it is required. They are part of
the Way, stored with it, and shown before anyone runs it.

**Questions** are what a *step* asks when it genuinely cannot proceed — "which of these two papers do you mean",
"should the report be 5 pages or 15". They are not inputs: an input is known before the run, a question is raised
during it, by the step that discovers it cannot continue.

The shape is deliberately the same one Claude Code settled on for `AskUserQuestion`, because that team reported
trying two other designs first and finding them unreliable:

  - **Attempt 1**: a `questions` array on the plan tool — confused the model, which had to both plan and ask.
  - **Attempt 2**: a markdown output format to parse — "Claude could usually produce this format, but not reliably.
    It would append extra sentences, drop options, or abandon the structure altogether."
  - **Attempt 3**: a **tool the model calls**, with a structured schema, which blocks the loop until answered.

We take attempt 3's lesson literally: a question is a structured object with 2–4 labelled options, never prose to
be parsed. A model that must also invent the *format* fails at the format.

Why options and not free text: the research on elicitation is explicit that the goal is "lower this friction and
increase the bandwidth of communication". A blank box asks the user to compose an answer; four labelled choices
ask them to recognise one. Free text stays available as the last option, not the only one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

InputType = Literal["text", "number", "url", "file", "choice"]

# Bounds copied from the tool this is modelled on, and worth keeping: a question with one option is not a
# question (there is nothing to choose), and more than four cannot be scanned at a glance.
MIN_OPTIONS = 2
MAX_OPTIONS = 4
MAX_HEADER = 12


@dataclass(frozen=True)
class Option:
    """One choice. `label` is what is returned; `description` is why a person would pick it."""

    label: str
    description: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"label": self.label, "description": self.description}


@dataclass(frozen=True)
class Question:
    """One clarifying question a step can ask mid-run.

    `key` is the stable handle answers come back under. It is NOT the question text: the text is display copy
    and may be edited; a stored answer must keep pointing at the same thing.
    """

    key: str
    question: str
    options: list[Option]
    header: str = ""
    multi_select: bool = False
    allow_free_text: bool = True

    def validate(self) -> None:
        if not self.key.strip():
            raise ValueError("a question needs a key")
        if not self.question.strip():
            raise ValueError(f"question {self.key!r} has no text")
        if not (MIN_OPTIONS <= len(self.options) <= MAX_OPTIONS):
            raise ValueError(f"question {self.key!r} needs {MIN_OPTIONS}-{MAX_OPTIONS} options, "
                             f"has {len(self.options)}")
        labels = [o.label for o in self.options]
        if len(set(labels)) != len(labels):
            raise ValueError(f"question {self.key!r} repeats an option label")
        if self.header and len(self.header) > MAX_HEADER:
            raise ValueError(f"question {self.key!r} header is over {MAX_HEADER} characters")

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "question": self.question, "header": self.header,
                "options": [o.as_dict() for o in self.options],
                "multiSelect": self.multi_select, "allowFreeText": self.allow_free_text}


@dataclass(frozen=True)
class WayInput:
    """One declared input of a Way. Everything except `name` is optional by design — an author who does not care
    should not have to say so three times."""

    name: str
    description: str = ""
    required: bool = False          # OPTIONAL BY DEFAULT: a Way that runs with no input is the normal case
    type: InputType = "text"
    default: Optional[str] = None
    choices: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("an input needs a name")
        if self.type == "choice" and not self.choices:
            raise ValueError(f"input {self.name!r} is a choice but lists no choices")
        if self.default is not None and self.type == "choice" and self.default not in self.choices:
            raise ValueError(f"input {self.name!r} defaults to {self.default!r}, which is not one of its choices")

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "required": self.required,
                "type": self.type, "default": self.default, "choices": list(self.choices)}


def missing_required(inputs: list[WayInput], provided: dict[str, Any]) -> list[str]:
    """Which required inputs have no value. Empty means the run may start.

    A default counts as provided: asking a person to confirm what the author already chose is friction with no
    information gained.
    """
    out: list[str] = []
    for i in inputs:
        if not i.required:
            continue
        value = provided.get(i.name)
        if (value is None or (isinstance(value, str) and not value.strip())) and i.default is None:
            out.append(i.name)
    return out


def first_unanswered(questions: list[Question], answers: dict[str, Any]) -> Optional[Question]:
    """The next question with no answer, or None when every one is settled.

    Returning them one at a time is deliberate: a run that asks four things at once and then reveals it needed a
    fifth has spent the person's attention twice.
    """
    for q in questions:
        if q.key not in answers:
            return q
    return None


def answers_valid(question: Question, answer: Any) -> bool:
    """Whether an answer is one this question accepts, so a malformed reply is refused rather than stored."""
    if answer is None:
        return not question.multi_select and False
    if question.multi_select:
        if not isinstance(answer, list) or not answer:
            return False
        labels = {o.label for o in question.options}
        return all(a in labels for a in answer)
    if isinstance(answer, str) and answer in {o.label for o in question.options}:
        return True
    # Free text, when the question allows it: a non-empty string that is not a label.
    return bool(question.allow_free_text and isinstance(answer, str) and answer.strip())
