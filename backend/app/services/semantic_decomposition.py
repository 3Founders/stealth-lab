"""
Semantic decomposition of a SKILL.md's own parsed steps -- the founder
directive's own §9 vocabulary (PROPOSITION / ABSTRACT_ACTION /
CONCRETE_IMPLEMENTATION / VERIFICATION / CONTEXT_ONLY), applied to filter
CONTEXT_ONLY and PROPOSITION entries out of `parsed.steps` before they are
ever stored as a fake `ProcedureStep`.

WHY THIS EXISTS, with real, observed corpus content (not a hypothetical):
`parse_skill_md()`'s step extraction is a structural (numbered/bulleted
list) parse -- it has no way to tell a real instruction ("Design units
with clear boundaries") from documentation ABOUT how to write good
instructions (obra/superpowers' own writing-plans SKILL.md lists
`'TBD', 'TODO', 'implement later', 'fill in details'` as BAD phrases to
avoid -- an anti-pattern warning, not an instruction) or from a literal
template placeholder (`Create: `exact/path/to/file.py``). Both shapes
were confirmed, this session, stored as literal procedure steps before
this pass existed.

Deterministic-first (the founder directive's own §10 rule), LLM for the
rest, abstain-safe -- and NO SILENT FALLBACK in the direction that
matters most here: an LLM failure or abstain never DROPS real content.
The safe default on any failure/abstain/no-client is `ABSTRACT_ACTION`
(keep the step exactly as it would have been stored before this pass
existed). Only a CONFIDENT, successfully-parsed CONTEXT_ONLY or
PROPOSITION result changes behavior -- the same asymmetric-risk posture
`_persist_package_relations`'s own `supported_steps` computation already
uses ("[] never fabricated... a real, if narrow, signal").

CONCRETE_IMPLEMENTATION and VERIFICATION are classified and reported but
currently kept as steps unchanged: rewriting a step's own text into an
abstracted goal (the founder directive's own substitution-test example,
"Use rg to find callers" -> goal=reference_search + a separate
Implementation=ripgrep) is a real text-transformation task, deliberately
out of scope for this pass.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, ValidationError, field_validator

STEP_SEMANTIC_KINDS: tuple[str, ...] = (
    "PROPOSITION", "ABSTRACT_ACTION", "CONCRETE_IMPLEMENTATION", "VERIFICATION", "CONTEXT_ONLY",
)

# Filtered OUT of the stored step list. A PROPOSITION belongs in Claims --
# the separate, already-real claim-extraction pipeline reads the WHOLE
# document text (not per-step), so dropping it here loses nothing; a
# CONTEXT_ONLY step was never an instruction at all.
_FILTERED_KINDS = frozenset({"PROPOSITION", "CONTEXT_ONLY"})


class SemanticDecompositionTransientFailure(Exception):
    """Raised when the LLM classification call errors or returns something
    that doesn't parse. The caller (classify_step_semantics) NEVER lets
    this drop real step content -- it records a safe ABSTRACT_ACTION
    default with classification='needs_enrichment' instead."""


# A narrow, real, deterministic signal -- the clearest template-placeholder
# shape actually observed in the real corpus (writing-plans' own
# `exact/path/to/file.py`, `tests/exact/path/to/test.py`). Kept narrow on
# purpose: a broader pattern risks a false positive on a real path that
# merely LOOKS templated, and this module's whole design already treats
# "not confident" as "keep the step" -- there is no cost to leaving the
# harder cases to the LLM pass instead of overreaching here.
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"exact/path/to")


def classify_step_deterministic(step_text: str) -> Optional[str]:
    """None means 'no deterministic signal' -- never a guess."""
    if _TEMPLATE_PLACEHOLDER_RE.search(step_text.lower()):
        return "CONTEXT_ONLY"
    return None


_SYSTEM_PROMPT = """You classify ONE step extracted from a procedural skill document into exactly \
one of these five categories:

PROPOSITION: a factual claim or hypothesis about the world (belongs in a knowledge base, not a \
procedure's own step list).
ABSTRACT_ACTION: a real, general instruction to actually do something as part of the procedure.
CONCRETE_IMPLEMENTATION: an instruction that names a SPECIFIC tool, command, or mechanism to \
accomplish an action (the action itself would still make sense with a different tool).
VERIFICATION: an instruction whose purpose is to CHECK or CONFIRM that something is true or that \
an earlier step succeeded.
CONTEXT_ONLY: not an instruction to execute at all -- background information, an example, a \
template placeholder, or documentation ABOUT how to write good instructions (e.g. a list of phrases \
to avoid).

Reply with ONLY a JSON object, no other text: {"kind": "<one of the five category names above>"}

If you cannot confidently classify this step, reply with exactly this JSON object instead: \
{"abstain": true}
"""


class _StepClassificationResponse(BaseModel):
    kind: str

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, v: str) -> str:
        v = v.strip()
        if v not in STEP_SEMANTIC_KINDS:
            raise ValueError(f"kind must be one of {STEP_SEMANTIC_KINDS}, got {v!r}")
        return v


_ABSTAIN = object()


def _parse_step_classification_response(text: str) -> Any:
    """Same three-outcome contract as implementation_goals.py's own
    `_parse_classification_response`: a valid `_StepClassificationResponse`,
    the `_ABSTAIN` sentinel, or `None` for anything that doesn't parse (a
    real failure, never silently accepted as a category)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("abstain"):
        return _ABSTAIN
    try:
        return _StepClassificationResponse.model_validate(parsed)
    except ValidationError:
        return None


async def _classify_step_via_llm(
    client: Any, model: str, *, step_text: str, skill_purpose: Optional[str],
) -> Optional[_StepClassificationResponse]:
    """None means a genuine abstain. Raises
    SemanticDecompositionTransientFailure on any infra/parse failure --
    never returns a fabricated category."""
    context_lines = [f"Step: {step_text}"]
    if skill_purpose:
        context_lines.append(f"Overall skill purpose: {skill_purpose}")
    user_prompt = "\n".join(context_lines)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=50,
        )
        text = response.choices[0].message.content.strip()
    except Exception as exc:  # noqa: BLE001 -- any client/transport failure is real
        raise SemanticDecompositionTransientFailure(f"LLM call failed: {exc!r}") from exc

    parsed = _parse_step_classification_response(text)
    if parsed is _ABSTAIN:
        return None
    if parsed is None:
        raise SemanticDecompositionTransientFailure(
            f"LLM response did not parse into the expected shape: {text[:200]!r}",
        )
    return parsed


async def classify_step_semantics(
    step_text: str, *, skill_purpose: Optional[str] = None,
    client: Any = None, model: str = "gemma-4-31B-it",
) -> dict:
    """Returns {"kind", "classification"}. `classification` mirrors
    implementation_goals.py's own vocabulary (`heuristic` /
    `llm_classified` / `unclassified` / `needs_enrichment`) for the same
    reason: a consistent, greppable signal for how confident/attempted
    each real classification was, across both modules this session added."""
    det = classify_step_deterministic(step_text)
    if det:
        return {"kind": det, "classification": "heuristic"}

    if client is None:
        return {"kind": "ABSTRACT_ACTION", "classification": "unclassified"}

    try:
        result = await _classify_step_via_llm(
            client, model, step_text=step_text, skill_purpose=skill_purpose,
        )
    except SemanticDecompositionTransientFailure:
        return {"kind": "ABSTRACT_ACTION", "classification": "needs_enrichment"}
    if result is None:
        return {"kind": "ABSTRACT_ACTION", "classification": "unclassified"}
    return {"kind": result.kind, "classification": "llm_classified"}


async def decompose_steps(
    steps: list[str], *, skill_purpose: Optional[str] = None,
    client: Any = None, model: str = "gemma-4-31B-it",
) -> tuple[list[str], dict]:
    """The real entry point `compile_skill_artifact()` calls. Returns
    `(filtered_steps, report)`:

      filtered_steps: the real list to persist as `ProcedureStep`s --
        original order preserved, CONTEXT_ONLY/PROPOSITION entries removed.
      report: real, observable counts -- {"total", "kept", "filtered",
        "by_kind": {kind: count}, "errors"} -- never silently dropped from
        a caller's own logging (§19's own "make failures diagnosable" rule).

    GUARD, not a fabrication: if classification somehow filtered EVERY
    step from a non-empty input, that is almost certainly a
    misclassification for a real procedural skill (an
    all-CONTEXT_ONLY/PROPOSITION document has no procedure in it at all,
    which V5/steps_not_empty would refuse downstream anyway) -- this
    function falls back to the ORIGINAL, unfiltered list rather than
    letting an overzealous classifier silently reject a legitimate skill.
    """
    kept: list[str] = []
    by_kind: dict[str, int] = {k: 0 for k in STEP_SEMANTIC_KINDS}
    errors = 0
    for step_text in steps:
        try:
            result = await classify_step_semantics(
                step_text, skill_purpose=skill_purpose, client=client, model=model,
            )
        except Exception:  # noqa: BLE001 -- one step's own unexpected
            # error must never lose that step's real content, and must
            # never abort classifying the rest of the document.
            errors += 1
            kept.append(step_text)
            continue
        kind = result["kind"]
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if kind not in _FILTERED_KINDS:
            kept.append(step_text)

    if steps and not kept:
        kept = list(steps)

    report = {
        "total": len(steps), "kept": len(kept), "filtered": len(steps) - len(kept),
        "by_kind": by_kind, "errors": errors,
    }
    return kept, report
