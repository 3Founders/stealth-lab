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
    pass


async def extract(client: Any, model: str, *, goal: str, repo: str, language: Optional[str], issue: str,
                  hints: str, patch: str, tests: tuple[str, ...]) -> Extraction:
    from app.services import ingest_budget
    from app.services.llm_json import parse_json_object
    from app.utils.aio import run_blocking

    await ingest_budget.guard(EXTRACT_OP)
    resp = await run_blocking(
        client.chat.completions.create, model=model, temperature=0, max_tokens=2500,
        messages=[{"role": "system", "content": _PROMPT},
                  {"role": "user", "content": _user(goal, repo, language, issue, hints, patch, tests)}])
    await ingest_budget.record_completion(model, EXTRACT_OP, getattr(resp, "usage", None))
    raw = (resp.choices[0].message.content or "").strip()
    payload = parse_json_object(raw)
    if payload is None:
        raise ExtractionFailed(f"reply was not JSON: {raw[:200]!r}")
    try:
        result = Extraction.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionFailed(f"reply did not match the schema: {exc.errors()[:3]}") from exc
    if result.steps[-1].role != "verify":
        raise ExtractionFailed("the last step is not the verification")
    return result
