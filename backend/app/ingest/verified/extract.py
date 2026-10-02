"""One model call per task: the way from the issue to the fix, written for someone facing the same Goal.

Input: the task's Goal (already named, shared), the issue, maintainer hints, the gold patch and the names of the tests
that must flip. Output (strict JSON, validated): a Procedure name, ordered steps -- each with its role (plan | edit |
verify | other) and, where one exists, a concrete check --, preconditions, pitfalls, and a few facts.

Claims are natural-language sentences (no subject/predicate slots), formed the way the leaderboard-extraction
lineage (Singh et al., arXiv:1802.04538) says reusable facts must be: taken from the clean signal (the patch and the
tests, not paraphrased prose), each naming exactly what it is about and the conditions under which it holds, nothing
unbound ("this fixes it"), nothing hypothetical or promotional. The last step is always the verification against
the task's own failing tests.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

EXTRACT_OP = "verified_solution_extraction"
MAX_ISSUE, MAX_HINTS, MAX_PATCH, MAX_TESTS = 5000, 1500, 9000, 20


class Step(BaseModel):
    model_config = {"extra": "forbid"}
    do: str = Field(min_length=3, max_length=400)
    role: Literal["plan", "edit", "verify", "other"]
    check: str = Field(default="", max_length=2000)   # a verify step names the exact tests; 300 cut real commands (2026-09-30)


class Extraction(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=4, max_length=140)
    steps: list[Step] = Field(min_length=2, max_length=12)
    preconditions: list[str] = Field(default_factory=list, max_length=6)
    pitfalls: list[str] = Field(default_factory=list, max_length=6)
    facts: list[str] = Field(default_factory=list, max_length=5)
    model_used: Optional[str] = None      # set from the response: the model that answered (Vertex failover)


_PROMPT = """You turn a solved software task into reusable knowledge for coding agents.

You get: the GOAL (fixed), the issue, maintainer hints, the accepted fix (a unified diff) and the tests that failed
before the fix and pass after it. Reply with ONE JSON object and nothing else:

{"name": "...", "steps": [{"do": "...", "role": "plan|edit|verify|other", "check": "..."}],
 "preconditions": ["..."], "pitfalls": ["..."], "facts": ["..."]}

- name: what this way does, reusable beyond this one repository (no repo names, no file paths), 4-14 words.
- steps: 3-8 ordered steps a developer or agent would follow to reach this fix FROM THE ISSUE: find where the
  behaviour lives, reproduce it, make the change, verify. Describe the kind of change the diff makes precisely
  (what logic changes and why), not line numbers. role: plan (locate/understand/reproduce), edit (change code),
  verify (run a check), other. check: a concrete way to tell the step worked (a command, a test id, an observable
  result) or "" when there is none. The LAST step must be role "verify" and its check must run the failing tests.
- preconditions: what must be true for this way to apply (versions, configuration, the kind of code involved).
- pitfalls: mistakes that would look plausible but fail here, grounded in the diff or the issue.
- facts: at most 5 plain sentences worth knowing beyond this task. Each names exactly what it is about (the
  function, option, library or behaviour) and the conditions under which it holds. Only what the diff, the tests
  or the issue show. No "it"/"this" without a name, no guesses, no hypotheticals, no praise.
Everything in plain language. Do not invent anything the inputs do not show.
"""


def _user(goal: str, repo: str, language: Optional[str], issue: str, hints: str, patch: str,
          tests: tuple[str, ...]) -> str:
    shown = "\n".join(tests[:MAX_TESTS]) + (f"\n... and {len(tests) - MAX_TESTS} more" if len(tests) > MAX_TESTS else "")
    return (f"GOAL: {goal}\nREPOSITORY: {repo} ({language or 'unknown language'})\n\nISSUE:\n{issue[:MAX_ISSUE]}\n\n"
            f"HINTS:\n{hints[:MAX_HINTS] or '(none)'}\n\nACCEPTED FIX (diff):\n{patch[:MAX_PATCH]}\n\n"
            f"TESTS THAT MUST PASS AFTER THE FIX:\n{shown}")


class ExtractionFailed(RuntimeError):
    def __init__(self, message: str, *, raw: str = "", problems: tuple[str, ...] = ()):
        super().__init__(message)
        self.raw = raw              # the reply that failed, so a retry can show it back
        self.problems = problems    # what to fix, in words the model can act on ("steps[2].do is 512 characters ...")


def _problems(errors: list) -> tuple[str, ...]:
    """Pydantic validation errors as instructions: where, and what is wrong."""
    out = []
    for e in errors:
        loc = "".join(f"[{p}]" if isinstance(p, int) else (f".{p}" if i else str(p))
                      for i, p in enumerate(e.get("loc", ()))) or "the object"
        kind, ctx, value = e.get("type"), (e.get("ctx") or {}), e.get("input")
        if kind in ("string_too_long", "too_long") and isinstance(value, (str, list)):
            unit = "characters" if kind == "string_too_long" else "items"
            out.append(f"{loc} is {len(value)} {unit} (at most {ctx.get('max_length')})")
        elif kind == "missing":
            out.append(f"{loc} is missing")
        elif kind == "extra_forbidden":
            out.append(f"{loc} is not an allowed field (remove it)")
        else:
            out.append(f"{loc}: {e.get('msg')}")
    return tuple(out)


async def extract(client: Any, model: str, *, goal: str, repo: str, language: Optional[str], issue: str,
                  hints: str, patch: str, tests: tuple[str, ...]) -> Extraction:
    """One extraction; a reply that breaks the contract is redone ONCE with EXTRACT_RETRY_MODEL when set (2026-10-02:
    gemini-2.5-flash, the cheapest model that passed the quality comparison, wrote a step over the 400-character
    limit on ~8% of items; gemini-3.6-flash keeps to it)."""
    import os

    args = dict(goal=goal, repo=repo, language=language, issue=issue, hints=hints, patch=patch, tests=tests)
    try:
        return await _extract_once(client, model, **args)
    except ExtractionFailed as first:
        failed = first
    # A reply that breaks the format is shown back with exactly what to fix (2026-10-02: on SWE-rebench-V2 even
    # gemini-3.6-flash wrote steps over 400 characters, and ~0.5% of replies missed or added fields, failing for good).
    feedback = _fix_feedback(failed)
    if feedback is not None:
        try:
            return await _extract_once(client, model, feedback=feedback, **args)
        except ExtractionFailed as again:
            failed = again
            feedback = _fix_feedback(again)
    retry = (os.environ.get("EXTRACT_RETRY_MODEL") or "").strip()
    if not retry or retry == model:
        raise failed
    return await _extract_once(client, retry, feedback=feedback, **args)


def _fix_feedback(failed: "ExtractionFailed") -> Optional[tuple[str, str]]:
    if not failed.problems or not failed.raw:
        return None
    return failed.raw[:8000], ("Your reply broke the required format: " + "; ".join(failed.problems) + ". Reply with "
                               "the corrected, complete JSON object, changing only what these problems need and "
                               "keeping the meaning. Nothing else.")


async def _extract_once(client: Any, model: str, *, goal: str, repo: str, language: Optional[str], issue: str,
                        hints: str, patch: str, tests: tuple[str, ...],
                        feedback: Optional[tuple[str, str]] = None) -> Extraction:
    from app.services import ingest_budget
    from app.services.llm_json import parse_json_object
    from app.utils.aio import run_blocking

    await ingest_budget.guard(EXTRACT_OP)
    resp = await run_blocking(
        client.chat.completions.create, model=model, temperature=0, max_tokens=2500,
        messages=[{"role": "system", "content": _PROMPT},
                  {"role": "user", "content": _user(goal, repo, language, issue, hints, patch, tests)},
                  *([{"role": "assistant", "content": feedback[0]}, {"role": "user", "content": feedback[1]}]
                    if feedback else [])])
    used = getattr(resp, "model", None) or model
    await ingest_budget.record_completion(used, EXTRACT_OP, getattr(resp, "usage", None))
    raw = (resp.choices[0].message.content or "").strip()
    payload = parse_json_object(raw)
    if payload is None:
        raise ExtractionFailed(f"reply was not JSON: {raw[:200]!r}", raw=raw,
                               problems=("the reply was not one complete JSON object (it may have been cut off; "
                                         "keep it within the limits)",) if raw else ())
    try:
        result = Extraction.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors()
        raise ExtractionFailed(f"reply did not match the schema: {errors[:3]}", raw=raw,
                               problems=_problems(errors)) from exc
    if result.steps[-1].role != "verify":
        raise ExtractionFailed("the last step is not the verification", raw=raw, problems=(
            "the last step must have role \"verify\" and its check must run the failing tests",))
    result.model_used = used
    return result
