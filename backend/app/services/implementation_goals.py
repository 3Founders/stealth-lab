"""
Implementation goal/verification-contract classification for bundled
skill-package scripts -- deterministic first (the founder directive's own
§10 rule: "parse deterministically where possible... never use an LLM to
rediscover deterministic structure unnecessarily"), a real bounded LLM
call second, for exactly the resources the deterministic pass could not
classify from the filename alone.

NO SILENT FALLBACK, same discipline `procedure_extraction/strategies.py`'s
GroundedHybridExtractor already established this session: an LLM call
that errors or returns something that doesn't parse raises
`ImplementationClassificationTransientFailure` (caught by the one real
caller, `_persist_package_relations`, and recorded as
`classification="needs_enrichment"` -- a real, later-revisitable state,
never a silently degraded guess). An explicit `{"abstain": true}` is a
real, final answer (`classification="unclassified"`), never conflated
with a failure.

The LLM is given ONLY the skill's own declared name/purpose and the step
text (if any) that names the resource -- never the script's own file
content. Two reasons, both real: (1) `_persist_package_relations`'s
caller (`compile_skill_artifact`) never fetches resource bytes in the
first place (`SourceResource.content` defaults to `b""` and no adapter in
this codebase populates it), and (2) even if it did, feeding an untrusted
script's own body to a classification prompt is exactly the widened
attack surface §16 of the founder directive warns against -- classifying
a mechanism's ROLE from its own declared context is enough for this real,
narrow slice; deeper content-aware classification is out of scope here.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

# Ordered: first match wins, so a name matching multiple keywords (rare)
# gets a stable, deterministic classification rather than one that depends
# on dict iteration order. Kept intentionally small and literal -- this is
# the "keep goal types broad enough for contextual ranking later, do not
# encode environment into the goal name" vocabulary the directive asks for,
# not an attempt at completeness.
_GOAL_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\btest"), "test_execution"),
    (re.compile(r"\b(check|verify|validate)"), "verification"),
    (re.compile(r"\b(scan|audit|lint)"), "static_analysis"),
    (re.compile(r"\bmigrat"), "schema_migration"),
    (re.compile(r"\b(deploy|release)"), "deployment"),
    (re.compile(r"\bbuild"), "build"),
    (re.compile(r"\b(generate|codegen|scaffold)"), "code_generation"),
    (re.compile(r"\b(backfill|reindex)"), "data_backfill"),
)

# The founder directive's own §7 vocabulary. `human`/`llm`/`external_system`
# have no real deterministic writer in this codebase yet -- only
# `deterministic` (an arbitrary script/CLI, the ONLY kind this module's
# real caller, _persist_package_relations, ever creates) is populated here.
VERIFICATION_CONTRACT_TYPES: tuple[str, ...] = (
    "deterministic", "test", "external_system", "llm", "human",
)


_SEPARATOR_RE = re.compile(r"[_\-.]")


def normalize_goal_from_path(resource_path: str) -> Optional[str]:
    """A real, narrow, deterministic classification from a resource's own
    path -- never a guess. Matches against the basename only (directory
    segments like `scripts/` carry no goal signal, per the directive's own
    "do not encode environment into the goal name" rule). Separators
    (`_`, `-`, `.`) are normalized to spaces first so `\\b` finds a real
    word boundary at `run_tests.py` -> `run tests py`, not just at the
    string's own start/end -- `_`/`-`/`.` are word-adjacent to `\\w` in
    Python's regex engine, so `\\btest` alone would miss "run_tests.py"."""
    basename = resource_path.rsplit("/", 1)[-1].lower()
    normalized = _SEPARATOR_RE.sub(" ", basename)
    for pattern, goal in _GOAL_PATTERNS:
        if pattern.search(normalized):
            return goal
    return None


def default_verification_contract(kind: str) -> Optional[dict]:
    """The one real, non-fabricated default this module can offer: a
    `kind='deterministic'` Implementation (an arbitrary script/CLI
    invocation) has, BY CONSTRUCTION of that kind, exactly one universally
    true success signal -- it ran without erroring. This is not a guess
    about what the script DOES; it is the honest floor every deterministic
    mechanism already satisfies. Any richer contract (a specific exit code
    meaning, a file it must produce) needs real evidence this module does
    not have and therefore never invents."""
    if kind == "deterministic":
        return {"type": "deterministic", "check": "exit_code_zero"}
    return None


class ImplementationClassificationTransientFailure(Exception):
    """Raised when the LLM classification call errors or returns something
    that doesn't parse. The caller must NOT fabricate a goal/expected_outcome
    to paper over this -- it records `classification="needs_enrichment"`
    and moves on; ingestion of the real Procedure/Implementation/Claim
    objects this resource belongs to must never be blocked by a best-effort
    enrichment failure."""


_CLASSIFICATION_SYSTEM_PROMPT = """You classify one bundled script from a skill package. You are given \
the skill's own declared name and purpose, and (when known) the exact instruction step that names this \
script.

Reply with ONLY a JSON object, no other text, matching exactly this shape:
{"goal": "<a short, general, lowercase_with_underscores label for what KIND of action this script \
performs -- e.g. test_execution, verification, static_analysis, schema_migration, deployment, build, \
code_generation, data_backfill, or another equally general label if none of those fit. Never include \
a project name, language, or environment detail.>",
 "expected_outcome": "<one short sentence describing the semantic state or result this script produces \
when it succeeds>"}

If the given context genuinely does not tell you what this script does, reply with exactly this JSON
object instead: {"abstain": true}
"""


class _ScriptClassificationResponse(BaseModel):
    goal: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)

    @field_validator("goal", "expected_outcome")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


_ABSTAIN = object()


def _parse_classification_response(text: str) -> Any:
    """Same three-outcome contract as procedure_extraction/strategies.py's
    own `_parse_abstraction_response`: a valid dict, the `_ABSTAIN`
    sentinel, or `None` for anything that doesn't parse (a real failure,
    never silently accepted)."""
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
        return _ScriptClassificationResponse.model_validate(parsed)
    except ValidationError:
        return None


async def _classify_via_llm(
    client: Any, model: str, *, resource_path: str,
    skill_name: Optional[str], skill_purpose: Optional[str], step_text: Optional[str],
) -> Optional[_ScriptClassificationResponse]:
    """None means a genuine abstain. Raises
    ImplementationClassificationTransientFailure on any infra/parse
    failure -- never returns a fabricated result."""
    basename = resource_path.rsplit("/", 1)[-1]
    context_lines = [f"Script: {basename}"]
    if skill_name:
        context_lines.append(f"Skill name: {skill_name}")
    if skill_purpose:
        context_lines.append(f"Skill purpose: {skill_purpose}")
    if step_text:
        context_lines.append(f"Instruction step naming this script: {step_text}")
    user_prompt = "\n".join(context_lines)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CLASSIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=150,
        )
        text = response.choices[0].message.content.strip()
    except Exception as exc:  # noqa: BLE001 -- any client/transport failure is real
        raise ImplementationClassificationTransientFailure(
            f"LLM call failed: {exc!r}",
        ) from exc

    parsed = _parse_classification_response(text)
    if parsed is _ABSTAIN:
        return None
    if parsed is None:
        raise ImplementationClassificationTransientFailure(
            f"LLM response did not parse into the expected shape: {text[:200]!r}",
        )
    return parsed


async def classify_skill_package_script(
    resource_path: str, *, kind: str = "deterministic",
    client: Any = None, model: str = "gemma-4-31B-it",
    skill_name: Optional[str] = None, skill_purpose: Optional[str] = None,
    step_text: Optional[str] = None,
) -> dict:
    """The one real entry point `_persist_package_relations()` calls.
    Returns exactly the five new `implementations` columns (migration 80)
    for one bundled script resource.

    Deterministic first: a filename match short-circuits before any model
    call (§10's own rule). Only when nothing matches AND a real client is
    given does this attempt the LLM path -- `client=None` (no LLM
    configured for this ingestion run) leaves the resource
    'unclassified', exactly as before this pass, never blocking on a
    call that was never going to happen."""
    goal = normalize_goal_from_path(resource_path)
    if goal:
        return {
            "goal": goal, "goal_spec": None, "expected_outcome": None,
            "verification_contract": default_verification_contract(kind),
            "classification": "heuristic",
        }

    verification_contract = default_verification_contract(kind)
    if client is None:
        return {
            "goal": None, "goal_spec": None, "expected_outcome": None,
            "verification_contract": verification_contract,
            "classification": "unclassified",
        }

    try:
        result = await _classify_via_llm(
            client, model, resource_path=resource_path,
            skill_name=skill_name, skill_purpose=skill_purpose, step_text=step_text,
        )
    except ImplementationClassificationTransientFailure:
        return {
            "goal": None, "goal_spec": None, "expected_outcome": None,
            "verification_contract": verification_contract,
            "classification": "needs_enrichment",
        }
    if result is None:
        return {
            "goal": None, "goal_spec": None, "expected_outcome": None,
            "verification_contract": verification_contract,
            "classification": "unclassified",
        }
    return {
        "goal": result.goal, "goal_spec": None, "expected_outcome": result.expected_outcome,
        "verification_contract": verification_contract,
        "classification": "llm_classified",
    }
