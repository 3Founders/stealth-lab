"""
Semantic grounding of an abstract ProcedureStep into the current
repo/task context (meta-harness directive Sec 6-7) -- "what does this
abstract step mean HERE?"

This is the directive's own named exception to "no LLM in every stage"
(Sec 4/5/6: retrieval and grouping stay structured lookup): grounding is
the one real semantic interpretation stage, and it exists to feed
`implementation_selection.select_implementation_for_goal()` a normalized
`goal` string that actually has a chance of matching real
`implementations.goal` values (`implementation_goals.py::
KNOWN_GOAL_CATEGORIES`, reused here as a vocabulary hint, not redefined),
instead of relying on a raw step's free-text description happening to
already equal one verbatim -- confirmed this session to be the common
failure mode (`ProcedureStep.goal` today is almost always prose like
"Identify existing auth architecture", not a normalized category).

NEVER INVENTS. Same discipline `procedure_extraction/strategies.py`'s
GroundedHybridExtractor and `claim_extraction.py` both already establish
in this codebase:
  - `goal` (the normalized category) is a real classification judgment
    the model is allowed to make -- bounded by the vocabulary hint, but
    not containment-checked against evidence text, exactly like
    `implementation_goals.py`'s own classifier.
  - `parameters` (file paths, commands, identifiers) are NOT classification
    judgments -- they are claimed FACTS, and Sec 7 is explicit: "must not
    invent file paths / commands / tools / Claims. Unsupported values
    must remain unresolved." Every parameter value is containment-checked
    against the exact text handed to the model (task description +
    relevant Claims' own statements) before being kept; a value that
    doesn't literally appear there is dropped, not trusted.

No silent degradation, but a DIFFERENT failure posture than
GroundedHybridExtractor's (which raises so no procedure gets written
under a strategy tag that didn't really run): a compile pipeline still
needs SOMETHING to build a node from even when grounding fails, so
`ground_step()` never raises -- an LLM call failure, malformed response,
or missing client all produce a `GroundedStep` with `used_fallback=True`
and the ORIGINAL raw goal text preserved unchanged (byte-identical to
what `procedure_graph.py::_step_goal()` already produces today), never a
fabricated category.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.services.goal_categories import KNOWN_GOAL_CATEGORIES

_GROUNDING_SYSTEM_PROMPT = f"""You ground one abstract step of a procedure into the concrete repo/task it is being run against.

Given the overall task, one abstract step, and a short list of known facts about this specific repo (local Claims), answer:
- goal_category: a short, broad, reusable category name for what KIND of work this step does (e.g. {", ".join(KNOWN_GOAL_CATEGORIES)}, or another short snake_case category if none of those fit). Broad and reusable, never overly specific (bad: "reference_search_typescript_large_monorepo"; good: "reference_search").
- parameters: a small JSON object of concrete values this step needs (e.g. file paths, commands, tool names) -- ONLY values that are DIRECTLY STATED in the task description or the given Claims. Never invent a plausible-looking path or command that was not actually given to you.
- unresolved: a list of short names for anything the step needs that you could NOT find a supported value for (e.g. "source_of_truth_file" if no Claim names it).
- abstain: true ONLY if the step text itself is too vague to categorize at all (e.g. empty or nonsensical).

Respond with JSON only, matching exactly:
{{"goal_category": "...", "parameters": {{"key": "value"}}, "unresolved": ["..."], "abstain": false}}
or {{"abstain": true}}
"""


class GroundingTransientFailure(Exception):
    """A real infrastructure failure (LLM call errored, response didn't
    parse) -- distinct from a genuine, final `abstain`. `ground_step()`
    itself never raises this outward (see module docstring on its
    honest-fallback posture); it exists so `_call_and_parse` can
    distinguish the two outcomes internally and so a future caller that
    DOES want to fail loudly (unlike a compile pipeline that needs
    something to build from) has a real exception to catch."""


class GroundedStep(BaseModel):
    goal: str
    parameters: dict[str, str] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    used_fallback: bool = False
    rationale: str = ""


class _GroundingResponse(BaseModel):
    """Enforced schema for the one grounding LLM call. `parameters`
    values are coerced to str (a model may return a number/bool for a
    parameter; every real Implementation-binding consumer downstream
    expects string parameters, same convention PlanNode.parameters
    already uses elsewhere in this codebase)."""

    goal_category: str = Field(min_length=1)
    parameters: dict[str, str] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)

    @field_validator("goal_category")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("goal_category must not be blank")
        return v

    @field_validator("parameters", mode="before")
    @classmethod
    def _coerce_param_values_to_str(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        return {str(k): str(val) for k, val in v.items()}


_ABSTAIN = object()  # sentinel: a genuine {"abstain": true}, distinct from a parse failure (None)


def _format_claims(relevant_claims: list[dict]) -> str:
    lines = []
    for c in relevant_claims or []:
        statement = (c.get("statement") or "").strip()
        if not statement:
            continue
        claim_id = c.get("claim_id", "?")
        lines.append(f"- {statement} (claim={claim_id})")
    return "\n".join(lines) if lines else "(no relevant local claims found)"


def _parse_grounding_response(text: str) -> Optional[_GroundingResponse]:  # or the _ABSTAIN sentinel
    """Pure, testable without a client. Same code-fence/prose stripping
    convention `procedure_extraction/strategies.py::
    _parse_abstraction_response` already establishes in this codebase."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("abstain") is True:
        return _ABSTAIN
    try:
        return _GroundingResponse.model_validate(payload)
    except ValidationError:
        return None


def _drop_unsupported_parameters(parameters: dict[str, str], evidence_text: str) -> tuple[dict[str, str], list[str]]:
    """Directive Sec 7's own rule, enforced in code, not just prompted
    for: a parameter value that is not a verbatim substring of the exact
    text handed to the model (task description + Claim statements) is
    dropped, never trusted -- the model cannot fabricate a file path or
    command that nothing in the given evidence actually named. Returns
    the surviving parameters plus the names of any that were dropped
    (folded into `unresolved`, since a dropped value is exactly an
    unsupported one)."""
    kept: dict[str, str] = {}
    dropped: list[str] = []
    for key, value in parameters.items():
        if value and value in evidence_text:
            kept[key] = value
        else:
            dropped.append(key)
    return kept, dropped


async def ground_step(
    step: dict,
    *,
    task_description: str,
    relevant_claims: Optional[list[dict]] = None,
    client: Any = None,
    model: str = "gemma-4-31B-it",
    temperature: float = 0.1,
) -> GroundedStep:
    """The real Sec 7 pipeline for one step. Never raises (see module
    docstring) -- every failure path returns a `GroundedStep` with
    `used_fallback=True` and the original raw goal text, exactly what
    `procedure_graph.py::_step_goal()` already produces today, so a
    caller that ignores grounding entirely (or whose grounding attempt
    fails) sees byte-identical behavior to before grounding existed."""
    raw_goal = (step.get("goal") or step.get("action") or "").strip()
    if not raw_goal:
        return GroundedStep(goal=str(step), used_fallback=True, rationale="step has no goal/action text to ground")
    if client is None:
        return GroundedStep(goal=raw_goal, used_fallback=True, rationale="no LLM client configured")

    relevant_claims = relevant_claims or []
    claims_block = _format_claims(relevant_claims)
    evidence_text = f"{task_description}\n{claims_block}"
    user_prompt = (
        f"Task: {task_description}\n\n"
        f"Abstract step: {raw_goal}\n\n"
        f"Relevant local facts about this repo (Claims):\n{claims_block}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _GROUNDING_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=300,
        )
        text = response.choices[0].message.content.strip()
    except Exception as exc:  # noqa: BLE001 -- any client/transport failure
        return GroundedStep(goal=raw_goal, used_fallback=True, rationale=f"LLM call failed: {exc!r}")

    parsed = _parse_grounding_response(text)
    if parsed is _ABSTAIN or parsed is None:
        reason = "model abstained" if parsed is _ABSTAIN else f"LLM response did not parse: {text[:200]!r}"
        return GroundedStep(goal=raw_goal, used_fallback=True, rationale=reason)

    parameters, dropped = _drop_unsupported_parameters(parsed.parameters, evidence_text)
    unresolved = sorted(set(parsed.unresolved) | set(dropped))

    return GroundedStep(
        goal=parsed.goal_category,
        parameters=parameters,
        unresolved=unresolved,
        used_fallback=False,
        rationale=(
            f"grounded to goal_category={parsed.goal_category!r}"
            + (f"; dropped unsupported parameter(s) {dropped!r}" if dropped else "")
        ),
    )


async def ground_procedure_steps(
    steps: list[dict],
    *,
    task_description: str,
    relevant_claims: Optional[list[dict]] = None,
    client: Any = None,
    model: str = "gemma-4-31B-it",
) -> list[dict]:
    """Batch entry point for a compile pipeline: grounds every step and
    returns a NEW steps list (never mutates the input) where a step whose
    grounding succeeded gets its `goal` replaced with the normalized
    category, plus a `grounding` sub-key carrying the full
    `GroundedStep` (parameters/unresolved/rationale) for downstream
    provenance -- e.g. a compiled `PlanNode.parameters` can be seeded
    from `step["grounding"]["parameters"]`. A step whose grounding fell
    back keeps its ORIGINAL `goal` untouched (no `grounding` key added at
    all) -- byte-identical to ungrounded compilation for that one step,
    exactly the module's own fallback contract extended to the batch
    case.

    ONE relevant-Claims fetch, shared across every step in this call
    (directive Sec 6: "typical count should remain small" -- claims
    relevant to the overall task, not refetched per step) -- the caller
    passes `relevant_claims` in, this function never queries the DB
    itself, keeping it pool-free and independently testable like the rest
    of this module.
    """
    grounded_steps: list[dict] = []
    for step in steps:
        grounded = await ground_step(
            step, task_description=task_description, relevant_claims=relevant_claims,
            client=client, model=model,
        )
        new_step = dict(step)
        if not grounded.used_fallback:
            new_step["goal"] = grounded.goal
            new_step["grounding"] = grounded.model_dump()
        grounded_steps.append(new_step)
    return grounded_steps
