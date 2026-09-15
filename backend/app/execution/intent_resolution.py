"""
Fuzzy human intent -> canonical Goal resolution (Prompt 2 Sec 1-3,
2026-09-15). The actual gap this closes, confirmed by audit before
writing anything: `app.services.goals.search_goals` (peer "ingestion",
migration 83/84) already does real lexical+semantic RRF-fused Goal
search, and `goal_resolution.py::resolve_goal_id_for_text` (this
session) only does exact normalized-name matching. Neither takes a
vague human sentence like "make checkout faster" and turns it into a
resolved Goal -- this module is exactly that pipeline, reusing both of
the above rather than reimplementing either:

    user input
        |
    normalize_intent()      -- LLM structured extraction, honest fallback
        |                       (same discipline as step_grounding.py)
    search_goals()           -- peer's real lexical+semantic search, unchanged
        |
    _rank_candidates()       -- real re-ranking over search_goals's REAL
        |                        results: lexical/entity overlap against the
        |                        normalized intent, scope match, status,
        |                        search-fusion position (Prompt 2 Sec 2's
        |                        own list of ranking dimensions)
        |
    resolved / ambiguous / no_match

NEVER FABRICATES a confident match: `search_goals` returns real rows in
real fused order but no numeric score, so "confidence" here is a real,
disclosed, deterministic score computed from fields `search_goals`
already returned (canonical_name/description text, status, scope_type)
plus the candidate's own real rank position in that fused order -- never
an invented probability. When the top two candidates' scores are close,
or when nothing clears the minimum floor, this module says so honestly
(`outcome="ambiguous"` or `"no_match"`) rather than silently picking one,
exactly what Prompt 2 Sec 2/3/18 require ("The system should return
multiple candidates or request clarification rather than confidently
selecting the wrong Goal").

`outcome="no_match"` proposes a Goal SKELETON derived from the normalized
intent (canonical_name/description) for the caller to review and
optionally create via the EXISTING `create_goal`/`create_goal_from_user`
flow -- this module never writes to the `goals` table itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import asyncpg
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.services.access import AccessScope, TenantScope
from app.services.goals import goal_embedding_text, normalize_goal_name, search_goals

_INTENT_SYSTEM_PROMPT = """You turn a vague, colloquial request about a software repository into a structured statement of intent. The request may be a symptom ("why is this flaky?"), a command ("deploy it"), or underspecified ("make it faster").

Extract, where inferable from the text ALONE (never invent specifics the text does not support):
- outcome: the desired end state, as a short imperative phrase (e.g. "reduce checkout request latency"). This is your own honest paraphrase/classification, not a verbatim copy requirement.
- object: the system/component/service the request is about, if named or clearly implied (e.g. "checkout service"). Empty string if not inferable.
- action: the kind of action requested, a short snake_case category (e.g. "improve_performance", "fix_bug", "diagnose", "deploy").
- constraints: a list of any explicit constraints/limits mentioned (empty list if none).
- verification: a short phrase for how success could plausibly be checked (e.g. "measure request latency"), or "" if not inferable.
- entities: any concrete files/services/identifiers literally named in the text (empty list if none -- do not guess a specific file/service name that was not said).
- uncertainty: a list of short phrases naming what is genuinely ambiguous or missing (e.g. "which service is 'this'"). Empty list only if the request is genuinely unambiguous.
- alternative_interpretations: 0-3 short alternate (outcome, object) readings when the request is genuinely ambiguous (e.g. "it" could refer to more than one thing). Empty list when there is really only one reading.

Respond with JSON only, matching exactly:
{"outcome": "...", "object": "...", "action": "...", "constraints": ["..."], "verification": "...", "entities": ["..."], "uncertainty": ["..."], "alternative_interpretations": [{"outcome": "...", "object": "..."}]}
"""


class _IntentResponse(BaseModel):
    outcome: str = Field(min_length=1)
    object: str = ""
    action: str = ""
    constraints: list[str] = Field(default_factory=list)
    verification: str = ""
    entities: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    alternative_interpretations: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("outcome")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("outcome must not be blank")
        return v


@dataclass
class NormalizedIntent:
    """Prompt 2 Sec 1's structured representation. `used_fallback=True`
    means no real LLM interpretation happened -- `outcome` is then just
    the raw user input, byte-identical to what a plain lexical search on
    the raw text would have used anyway (never a fabricated
    interpretation standing in for a real one)."""

    raw_input: str
    outcome: str
    object: str = ""
    action: str = ""
    constraints: list[str] = field(default_factory=list)
    verification: str = ""
    entities: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    alternative_interpretations: list[dict[str, str]] = field(default_factory=list)
    used_fallback: bool = False
    rationale: str = ""


def _parse_intent_response(text: str) -> Optional[_IntentResponse]:
    """Pure, testable without a client -- same code-fence-stripping
    convention step_grounding.py's own parser uses."""
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
    try:
        return _IntentResponse.model_validate(payload)
    except ValidationError:
        return None


async def normalize_intent(
    user_input: str, *, client: Any = None, model: str = "gemma-4-31B-it", temperature: float = 0.1,
) -> NormalizedIntent:
    """Prompt 2 Sec 1: fuzzy text -> structured intent. Never raises --
    a missing client, a transport failure, or a response that fails to
    parse/validate all produce a `NormalizedIntent` with
    `used_fallback=True` and `outcome=user_input` unchanged, the same
    honest-degrade posture `step_grounding.py::ground_step` already
    established in this codebase. This is NOT merely rewriting the
    sentence (Prompt 2 Sec 1's own prohibition): a successful call
    returns real classification judgments (action category,
    constraints, uncertainty) the raw text does not literally contain."""
    raw = (user_input or "").strip()
    if not raw:
        return NormalizedIntent(raw_input=raw, outcome="", used_fallback=True, rationale="empty input")
    if client is None:
        return NormalizedIntent(raw_input=raw, outcome=raw, used_fallback=True, rationale="no LLM client configured")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": raw},
            ],
            temperature=temperature,
            max_tokens=500,
        )
        text = response.choices[0].message.content.strip()
    except Exception as exc:  # noqa: BLE001 -- any client/transport failure
        return NormalizedIntent(raw_input=raw, outcome=raw, used_fallback=True, rationale=f"LLM call failed: {exc!r}")

    parsed = _parse_intent_response(text)
    if parsed is None:
        return NormalizedIntent(
            raw_input=raw, outcome=raw, used_fallback=True,
            rationale=f"LLM response did not parse: {text[:200]!r}",
        )

    return NormalizedIntent(
        raw_input=raw, outcome=parsed.outcome, object=parsed.object, action=parsed.action,
        constraints=parsed.constraints, verification=parsed.verification, entities=parsed.entities,
        uncertainty=parsed.uncertainty, alternative_interpretations=parsed.alternative_interpretations,
        used_fallback=False, rationale="normalized via LLM extraction",
    )


@dataclass
class GoalCandidate:
    goal: dict
    score: float
    lexical_overlap: float
    scope_match: float
    status_score: float
    fusion_position_score: float
    rationale: str


_MIN_CANDIDATE_SCORE = 0.15  # below this, a candidate is not worth surfacing at all -- real, disclosed judgment call
_AMBIGUITY_MARGIN = 0.12  # top-vs-second score gap below this is "too close to call" -- same posture


def _tokens(*parts: str) -> set[str]:
    out: set[str] = set()
    for p in parts:
        out |= set(normalize_goal_name(p or "").split())
    return {t for t in out if t}


def _rank_candidates(
    candidates: list[dict], normalized: NormalizedIntent, *, context: Optional[dict] = None,
) -> list[GoalCandidate]:
    """Prompt 2 Sec 2's own ranking dimensions, computed for real over
    fields `search_goals` already returned -- not a new search, a
    re-rank. `fusion_position_score` reuses `search_goals`'s own RRF
    ordering (it already fused lexical+semantic) as one real signal
    among several, rather than discarding that work and starting over."""
    context = context or {}
    intent_tokens = _tokens(normalized.outcome, normalized.object, normalized.action)
    want_scope_type = context.get("scope_type")
    want_scope_entity_id = context.get("scope_entity_id")

    ranked: list[GoalCandidate] = []
    n = max(len(candidates), 1)
    for i, g in enumerate(candidates):
        goal_tokens = _tokens(g.get("canonical_name", ""), g.get("description") or "")
        lexical_overlap = (
            len(intent_tokens & goal_tokens) / len(intent_tokens) if intent_tokens else 0.0
        )

        scope_match = 0.0
        if g.get("scope_type") == "global":
            scope_match = 0.5
        if want_scope_type and g.get("scope_type") == want_scope_type and (
            want_scope_entity_id is None or g.get("scope_entity_id") == want_scope_entity_id
        ):
            scope_match = 1.0

        status = g.get("status")
        status_score = {"active": 1.0, "candidate": 0.5}.get(status, 0.2)

        fusion_position_score = 1.0 - (i / n)

        score = (
            0.40 * lexical_overlap + 0.20 * scope_match + 0.15 * status_score + 0.25 * fusion_position_score
        )
        ranked.append(GoalCandidate(
            goal=g, score=round(score, 4), lexical_overlap=round(lexical_overlap, 4),
            scope_match=scope_match, status_score=status_score,
            fusion_position_score=round(fusion_position_score, 4),
            rationale=(
                f"lexical_overlap={lexical_overlap:.2f}, scope_match={scope_match:.2f}, "
                f"status={status!r}, fusion_position={i+1}/{n}"
            ),
        ))
    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked


@dataclass
class IntentResolution:
    raw_input: str
    normalized: NormalizedIntent
    outcome: Literal["resolved", "ambiguous", "no_match"]
    selected_goal: Optional[dict] = None
    candidates: list[GoalCandidate] = field(default_factory=list)
    proposed_goal: Optional[dict] = None
    rationale: str = ""


async def resolve_intent(
    pool: asyncpg.Pool,
    user_input: str,
    *,
    context: Optional[dict] = None,
    client: Any = None,
    embedder: Any = None,
    scope: Optional[AccessScope] = None,
    tenant_scope: Optional[TenantScope] = None,
    status: Optional[str] = None,
    top_k: int = 5,
) -> IntentResolution:
    """Prompt 2 Sec 1-3's full pipeline, real and tested end to end:
    normalize -> search (peer's real search_goals) -> re-rank -> decide.

    `outcome="resolved"`: exactly one candidate cleared
    `_MIN_CANDIDATE_SCORE` AND beat the runner-up by at least
    `_AMBIGUITY_MARGIN` -- `selected_goal` is that candidate's real row.

    `outcome="ambiguous"`: two or more candidates are plausible and too
    close to call -- `candidates` carries all of them (Prompt 2 Sec 3/18:
    "support multiple candidate interpretations"), `selected_goal` stays
    None. Never guesses.

    `outcome="no_match"`: nothing cleared the floor -- `proposed_goal` is
    a real, disclosed skeleton (canonical_name/description/scope) built
    from the normalized intent for the caller to review/edit before
    calling the existing `create_goal` flow. Nothing is written here.
    """
    raw = (user_input or "").strip()
    normalized = await normalize_intent(user_input, client=client)

    query_text = normalized.outcome or raw
    query_embedding = None
    if embedder is not None and query_text:
        query_embedding, _meta = await embedder.embed_one_with_metadata(query_text, input_type="query")

    if not query_text:
        return IntentResolution(
            raw_input=raw, normalized=normalized, outcome="no_match",
            proposed_goal=None, rationale="empty input -- nothing to resolve",
        )

    raw_candidates = await search_goals(
        pool, query_text=query_text, query_embedding=query_embedding,
        scope=scope, tenant_scope=tenant_scope, status=status, limit=max(top_k, 5),
    )
    ranked = _rank_candidates(raw_candidates, normalized, context=context)[:top_k]

    if not ranked or ranked[0].score < _MIN_CANDIDATE_SCORE:
        proposed = {
            "canonical_name": (normalized.outcome or raw)[:200],
            "description": normalized.verification or None,
            "scope_type": (context or {}).get("scope_type", "global"),
            "scope_entity_id": (context or {}).get("scope_entity_id"),
        }
        return IntentResolution(
            raw_input=raw, normalized=normalized, outcome="no_match", candidates=ranked,
            proposed_goal=proposed,
            rationale=(
                "no existing goal cleared the minimum relevance floor "
                f"({_MIN_CANDIDATE_SCORE}) -- proposing a new goal for review, not creating it"
            ),
        )

    if len(ranked) >= 2 and (ranked[0].score - ranked[1].score) < _AMBIGUITY_MARGIN:
        return IntentResolution(
            raw_input=raw, normalized=normalized, outcome="ambiguous", candidates=ranked,
            rationale=(
                f"top candidates too close to call (scores {ranked[0].score} vs {ranked[1].score}, "
                f"margin < {_AMBIGUITY_MARGIN}) -- returning all candidates rather than guessing"
            ),
        )

    return IntentResolution(
        raw_input=raw, normalized=normalized, outcome="resolved", candidates=ranked,
        selected_goal=ranked[0].goal,
        rationale=f"top candidate cleared the floor and margin: {ranked[0].rationale}",
    )
