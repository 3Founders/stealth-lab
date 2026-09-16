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

# Public export of the same taxonomy -- reused (not redefined) by
# step_grounding.py as the vocabulary hint in its own grounding prompt, so
# a grounded ProcedureStep.goal and an Implementation.goal have a real
# chance of landing on the same normalized string. Deliberately NOT a
# closed enum enforced anywhere (goal stays free TEXT, migration 80's own
# choice, "so the real vocabulary can grow without a migration per new
# goal") -- this tuple is a hint/starting point, never a hard constraint.
KNOWN_GOAL_CATEGORIES: tuple[str, ...] = tuple(goal for _, goal in _GOAL_PATTERNS)

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


def _find_step_text(steps: Any, resource_path: str) -> Optional[str]:
    """SAME real matching rule `_persist_package_relations`
    (skill_ingestion.py) already uses at ingestion time -- a plain text
    match against the resource's full path or bare filename. `steps` is
    `procedures.steps` as stored (a list of `{"order", "goal"}` dicts, the
    real shape `_parsed_skill_procedure_shape` writes)."""
    if not steps:
        return None
    if isinstance(steps, str):
        steps = json.loads(steps)
    basename = resource_path.rsplit("/", 1)[-1]
    for step in steps:
        text = step.get("goal") if isinstance(step, dict) else None
        if text and (resource_path in text or basename in text):
            return text
    return None


async def enrich_pending_skill_package_implementations(
    pool: Any, *, limit: int = 50, client: Any = None, model: str = "gemma-4-31B-it",
    goal_embedder: Any = None,
) -> dict:
    """Real, resumable enrichment pass (backend/scripts/enrich_implementations.py's
    own backing function -- same "service function does the work, script is
    a thin CLI" convention `backfill_procedure_embeddings.py` already
    established) over `implementations` rows THIS module's classifier can
    meaningfully improve: `provider='skill-package'` (the only shape
    `classify_skill_package_script` knows how to interpret -- a bare
    resource path plus optional skill context) still sitting at
    `classification IN ('needs_enrichment', 'unclassified')`.

    Skill context (name/purpose/step text) is read from THIS DATABASE's
    own `procedures` row via `procedure_implementations` -- never a
    network refetch of the original SKILL.md (which would silently
    reintroduce this session's own confirmed `raw.githubusercontent.com`
    connectivity gap as a hidden dependency of a routine maintenance job).
    A row with no resolvable procedure link (rare -- the relation itself
    is bi-temporally versioned and could have been superseded) gets
    `skill_name=None`/`skill_purpose=None`/`step_text=None`: the
    classifier already handles that honestly (a likely abstain), never a
    crash.

    Returns real counts: {"attempted", "heuristic", "llm_classified",
    "unclassified", "needs_enrichment", "errors"} -- `errors` counts a
    per-row exception this function itself catches (e.g. a malformed
    `locator`), never silently dropped from the report.
    """
    rows = await pool.fetch(
        """
        SELECT i.id, i.locator, i.kind, i.scope_type, i.scope_entity_id,
               p.name AS skill_name, p.goal AS skill_purpose, p.steps AS steps
        FROM implementations i
        LEFT JOIN procedure_implementations pi
               ON pi.implementation_id = i.id AND pi.t_invalid IS NULL
        LEFT JOIN procedures p
               ON p.procedure_id = pi.procedure_id AND p.t_invalid IS NULL
        WHERE i.provider = 'skill-package'
          AND i.classification IN ('needs_enrichment', 'unclassified')
        ORDER BY i.t_created
        LIMIT $1
        """,
        limit,
    )

    counts = {
        "attempted": 0, "heuristic": 0, "llm_classified": 0,
        "unclassified": 0, "needs_enrichment": 0, "errors": 0,
    }
    for row in rows:
        counts["attempted"] += 1
        try:
            locator = row["locator"]
            if isinstance(locator, str):
                locator = json.loads(locator)
            resource_path = (locator or {}).get("path")
            if not resource_path:
                counts["errors"] += 1
                continue
            step_text = _find_step_text(row["steps"], resource_path)
            fields = await classify_skill_package_script(
                resource_path, kind=row["kind"], client=client, model=model,
                skill_name=row["skill_name"], skill_purpose=row["skill_purpose"],
                step_text=step_text,
            )
            goal_id = None
            if fields.get("goal"):
                # ingestion.md Sec 2-3 / migration 83: this is the one
                # real writer of `implementations.goal` today (per the
                # module's own docstring above) -- now also resolves a
                # real Goal row, additive alongside the existing TEXT
                # write. Falls back to scope_type='global' when the
                # implementation row itself carries no scope (common for
                # older rows, migration 33's scope_type is nullable) --
                # "prefer generalized global Goals" (ingestion.md Sec 9).
                from app.services.goals import find_or_create_goal

                resolved_goal = await find_or_create_goal(
                    pool,
                    canonical_name=fields["goal"],
                    scope_type=row["scope_type"] or "global",
                    scope_entity_id=row["scope_entity_id"],
                    provenance="prior_library",
                    created_from="skill_package_enrichment",
                    embedder=goal_embedder, client=client, adjudication_model=model,
                )
                goal_id = resolved_goal["id"]
            await pool.execute(
                "UPDATE implementations SET goal=$2, goal_spec=$3::jsonb, "
                "expected_outcome=$4, verification_contract=$5::jsonb, classification=$6, "
                "goal_id=$7::uuid "
                "WHERE id=$1::uuid",
                row["id"], fields["goal"], fields["goal_spec"], fields["expected_outcome"],
                fields["verification_contract"], fields["classification"], goal_id,
            )
            counts[fields["classification"]] = counts.get(fields["classification"], 0) + 1
        except Exception:  # noqa: BLE001 -- one malformed row must never
            # abort the whole enrichment pass; the real count is what
            # makes this diagnosable, not a raised exception mid-batch.
            counts["errors"] += 1

    return counts
