"""
Claim-conditioned NLI/JEV applicability judge -- the provider-neutral second-
stage contextual-reasoning layer this module's own docstring in
`applicability.py` and `precondition_gate.py:29-31` name as a placeholder
("Jaccard tag overlap... not semantic entailment") and `claim_equivalence.py`
already establishes the house convention for (LLM-as-judge, honest
abstention, no transformers/NLI dependency added).

WHAT THIS ANSWERS, AND WHAT IT DOES NOT
    Embeddings (retrieval.py, applicability.py's own similarity ranking)
    answer "what looks relevant?". This module answers "does this candidate
    actually fit the local Claims I have right now?" -- a THREE-VALUED
    question (TRUE/FALSE/UNKNOWN), deliberately NOT the same as
    check_hard_constraints' existing two-valued, fail-closed CWA cascade.
    The two must not be unified: a candidate this module judges UNKNOWN on
    a requirement is NOT disqualified (missing evidence is never treated as
    a contradiction) -- only a REQUIRED condition judged FALSE with high
    confidence is a hard rejection. See claim_conditioned_retrieval.py for
    where that hard-filter line is actually drawn.

PROVIDERS AND THE FALLBACK CONTRACT
    RemoteHTTPJudge is the "JEV" transport (operator-hosted POST
    /judge-applicability). LLMJudge is a single OpenAI-compatible client.
    Production wiring is the shared chain JEV -> Gemini -> Gemma in
    app/services/semantic (see default_judge_from_env). MockJudge is a
    deterministic string-overlap scorer for tests/local demos ONLY.

    STRICT RULE: a provider failure RAISES (ProviderError /
    SemanticJudgmentUnavailable). It is never converted into a fabricated
    UNKNOWN verdict and there is no deterministic fallback -- UNKNOWN means
    only "a model judged: no supporting Claim". Callers
    (claim_conditioned_retrieval.py) turn an exhausted chain into
    PENDING_SEMANTIC_JUDGMENT + requeue, then SEMANTIC_JUDGMENT_UNAVAILABLE.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from app.services.semantic.errors import (
    ErrorKind,
    ProviderError,
    SemanticJudgmentUnavailable,
    classify_exception,
)

log = logging.getLogger(__name__)

PROMPT_VERSION = "applicability_judge@v1"

VERDICTS = ("APPLICABLE", "INAPPLICABLE", "UNKNOWN", "PARTIALLY_APPLICABLE")
CONDITION_KINDS = (
    "REQUIRED", "PREFERRED", "SATISFIABLE", "IMPLEMENTATION_BINDING", "TARGET_STATE",
)

# Configurable thresholds (Sec 8 of the product spec: "make thresholds
# configurable" -- module constants a caller can override per-call, not
# hidden inline literals).
DEFAULT_HARD_REJECT_CONTRADICTION_THRESHOLD = 0.75
DEFAULT_CLAIMS_PER_CANDIDATE_MIN = 5
DEFAULT_CLAIMS_PER_CANDIDATE_MAX = 20
DEFAULT_JUDGE_BATCH_SIZE = 10


@dataclass
class RequirementCondition:
    """One precondition/requirement extracted from a candidate, classified
    into the hard/soft axis the product spec's Sec 7 names. v1 heuristic
    classification only (HONEST GAP, not a real requirements-extraction
    model): a structured `preconditions` entry defaults REQUIRED (matches
    check_hard_constraints' own treatment of preconditions as
    disqualifying), a `postconditions`/`expected_effects` entry defaults
    TARGET_STATE (spec's own "tests should pass" example -- must never be
    hard-rejected for not yet being true), everything else passed in
    explicitly by a caller keeps its given `kind`."""
    text: str
    kind: str = "REQUIRED"
    claim_id: Optional[str] = None  # provenance narrowing, mirrors applicability.py's own claim_id-on-precondition convention

    def __post_init__(self) -> None:
        if self.kind not in CONDITION_KINDS:
            raise ValueError(f"RequirementCondition.kind must be one of {CONDITION_KINDS}, got {self.kind!r}")


def extract_requirement_conditions(procedure: dict) -> list[RequirementCondition]:
    """Pure, DB-free extraction from an existing `procedures` row shape --
    reuses the SAME fields check_hard_constraints() already reads
    (`preconditions`, `expected_effects`/`postconditions`), no new schema.
    """
    conditions: list[RequirementCondition] = []
    for precondition in (procedure.get("preconditions") or []):
        subject = precondition.get("subject")
        predicate = precondition.get("predicate")
        expected_object = precondition.get("object")
        if not subject:
            continue
        text = f"{subject} {predicate} {expected_object}".strip()
        conditions.append(RequirementCondition(
            text=text, kind="REQUIRED", claim_id=precondition.get("claim_id"),
        ))
    for effect in (procedure.get("expected_effects") or procedure.get("postconditions") or []):
        text = effect if isinstance(effect, str) else json.dumps(effect, default=str)
        conditions.append(RequirementCondition(text=text, kind="TARGET_STATE"))
    return conditions


@dataclass
class JudgeCandidateInput:
    """One candidate's judge-facing payload -- goal/query, the candidate's
    own purpose text, its requirement conditions, and the bounded relevant
    Claims (from relevant_claims.py::get_relevant_claims) already narrowed
    to this candidate. Mirrors the POST /judge-applicability request body
    (Sec 11) field-for-field so RemoteHTTPJudge needs no reshaping."""
    candidate_id: str
    candidate_version: Any
    candidate_purpose: str
    conditions: list[RequirementCondition]
    claims: list[dict] = field(default_factory=list)  # compact refs, same shape get_relevant_claims returns


@dataclass
class ApplicabilityJudgment:
    """The strict, structured judgment contract (product spec Sec 5). The
    canonical decision a caller acts on is this dataclass's fields -- never
    free-form model prose, which is confined to `reason` (an explanation,
    not a decision input)."""
    candidate_id: str
    goal_or_query: str
    applicability_probability: float
    contradiction_probability: float
    preconditions_met_probability: float
    verdict: str
    supporting_claim_ids: list[str] = field(default_factory=list)
    blocking_claim_ids: list[str] = field(default_factory=list)
    unknown_requirements: list[str] = field(default_factory=list)
    reason: str = ""
    model: str = "mock"
    model_version: str = "v1"
    prompt_version: str = PROMPT_VERSION
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"ApplicabilityJudgment.verdict must be one of {VERDICTS}, got {self.verdict!r}")

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "goal_or_query": self.goal_or_query,
            "applicability_probability": self.applicability_probability,
            "contradiction_probability": self.contradiction_probability,
            "preconditions_met_probability": self.preconditions_met_probability,
            "verdict": self.verdict,
            "supporting_claim_ids": self.supporting_claim_ids,
            "blocking_claim_ids": self.blocking_claim_ids,
            "unknown_requirements": self.unknown_requirements,
            "reason": self.reason, "model": self.model, "model_version": self.model_version,
            "prompt_version": self.prompt_version, "timestamp": self.timestamp,
        }


class ApplicabilityJudge(Protocol):
    """Provider-neutral interface (Sec 10). Every provider below implements
    exactly this shape; a caller (claim_conditioned_retrieval.py) never
    branches on which provider it holds."""

    async def judge_batch(
        self, goal: str, candidates: list[JudgeCandidateInput],
    ) -> list[ApplicabilityJudgment]:
        ...


def parse_judgment_dict(
    goal: str, candidate: JudgeCandidateInput, parsed: Any, *, model: str,
) -> ApplicabilityJudgment:
    """Strict contract parse shared by every LLM/remote provider. Raises
    ValueError on ANY violation -- a malformed reply is a provider failure
    (retry / next provider), NEVER converted into a verdict. UNKNOWN is a
    legitimate model verdict only; it is not an error sentinel."""
    if not isinstance(parsed, dict):
        raise ValueError("judge reply is not a JSON object")
    verdict = parsed.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"invalid verdict {verdict!r}")
    known_claims = {str(c.get("claim_id") or c.get("id")) for c in candidate.claims}
    supporting = [str(x) for x in parsed.get("supporting_claim_ids", [])]
    blocking = [str(x) for x in parsed.get("blocking_claim_ids", [])]
    invented = [x for x in supporting + blocking if x not in known_claims]
    if invented:
        raise ValueError(f"judge cited claim ids not in the input: {invented[:3]}")
    return ApplicabilityJudgment(
        candidate_id=candidate.candidate_id, goal_or_query=goal,
        applicability_probability=float(parsed.get("applicability_probability", 0.5)),
        contradiction_probability=float(parsed.get("contradiction_probability", 0.0)),
        preconditions_met_probability=float(parsed.get("preconditions_met_probability", 0.5)),
        verdict=verdict, supporting_claim_ids=supporting, blocking_claim_ids=blocking,
        unknown_requirements=[str(x) for x in parsed.get("unknown_requirements", [])],
        reason=str(parsed.get("reason", "")), model=model,
    )


def judge_user_payload(goal: str, candidate: JudgeCandidateInput) -> dict:
    return {
        "goal": goal, "candidate_purpose": candidate.candidate_purpose,
        "conditions": [{"text": c.text, "kind": c.kind} for c in candidate.conditions],
        "claims": [
            {"claim_id": str(cl.get("claim_id") or cl.get("id")), "statement": cl.get("statement")}
            for cl in candidate.claims
        ],
    }


class MockJudge:
    """Deterministic, rule-based provider -- the default for tests/CI
    (matches the "tests use mocked inference, not paid live calls"
    acceptance criterion). Judges purely on string containment between a
    Claim's statement and a requirement's text: a Claim mentioning the
    requirement's key terms alongside a negation word ("unavailable", "not",
    "cannot", "missing", "no ") contradicts; alongside no negation word,
    supports; no matching Claim at all is UNKNOWN for that requirement.
    This is intentionally simple and legible, not a claim to real NLI
    quality -- it exists so this module's contract (batching, hard-filter
    wiring, caching, explanation) is exercised deterministically."""

    NEGATION_MARKERS = ("unavailable", "not ", "cannot", "can't", "missing", "no ", "isn't", "doesn't", "won't")

    def __init__(self, model: str = "mock", model_version: str = "v1"):
        self.model = model
        self.model_version = model_version

    async def judge_batch(
        self, goal: str, candidates: list[JudgeCandidateInput],
    ) -> list[ApplicabilityJudgment]:
        return [self._judge_one(goal, c) for c in candidates]

    def _judge_one(self, goal: str, candidate: JudgeCandidateInput) -> ApplicabilityJudgment:
        supporting: list[str] = []
        blocking: list[str] = []
        unknown: list[str] = []
        required_blocked = False

        for condition in candidate.conditions:
            terms = [t.lower() for t in condition.text.split() if len(t) > 2]
            matched_claim = None
            contradicts = False
            for claim in candidate.claims:
                statement = str(claim.get("statement") or "").lower()
                if not statement or not terms:
                    continue
                if any(term in statement for term in terms):
                    matched_claim = claim
                    contradicts = any(marker in statement for marker in self.NEGATION_MARKERS)
                    break
            if matched_claim is None:
                unknown.append(condition.text)
                continue
            claim_id = str(matched_claim.get("claim_id") or matched_claim.get("id") or "")
            if contradicts:
                blocking.append(claim_id)
                if condition.kind == "REQUIRED":
                    required_blocked = True
            else:
                supporting.append(claim_id)

        total = len(candidate.conditions) or 1
        contradiction_probability = 1.0 if required_blocked else (len(blocking) / total)
        preconditions_met_probability = len(supporting) / total
        applicability_probability = max(0.0, preconditions_met_probability - contradiction_probability + 0.5)
        applicability_probability = min(1.0, applicability_probability)

        if required_blocked:
            verdict = "INAPPLICABLE"
        elif unknown and not blocking:
            verdict = "PARTIALLY_APPLICABLE" if supporting else "UNKNOWN"
        elif blocking:
            verdict = "PARTIALLY_APPLICABLE"
        else:
            verdict = "APPLICABLE"

        return ApplicabilityJudgment(
            candidate_id=candidate.candidate_id, goal_or_query=goal,
            applicability_probability=applicability_probability,
            contradiction_probability=contradiction_probability,
            preconditions_met_probability=preconditions_met_probability,
            verdict=verdict, supporting_claim_ids=supporting, blocking_claim_ids=blocking,
            unknown_requirements=unknown,
            reason=(
                f"{len(supporting)} supporting, {len(blocking)} blocking, "
                f"{len(unknown)} unknown of {len(candidate.conditions)} conditions"
            ),
            model=self.model, model_version=self.model_version,
        )


_JUDGE_SYSTEM_PROMPT = (
    "You judge whether a candidate procedure is applicable given a goal and a "
    "bounded set of local Claims (facts about the current repository/environment "
    "state). For EACH requirement condition, decide whether local Claims make it "
    "TRUE (supported), FALSE (contradicted), or UNKNOWN (no relevant Claim). "
    "Never treat a missing Claim as FALSE. Respond with EXACTLY one JSON object: "
    '{"verdict": "APPLICABLE"|"INAPPLICABLE"|"UNKNOWN"|"PARTIALLY_APPLICABLE", '
    '"applicability_probability": <0.0-1.0>, "contradiction_probability": <0.0-1.0>, '
    '"preconditions_met_probability": <0.0-1.0>, "supporting_claim_ids": [...], '
    '"blocking_claim_ids": [...], "unknown_requirements": [...], "reason": "<short>"}. '
    "Only mark INAPPLICABLE when a REQUIRED condition is strongly contradicted by "
    "a specific Claim -- cite its id in blocking_claim_ids. Never invent Claim ids "
    "not present in the input."
)


class LLMJudge:
    """Single-provider LLM-as-judge over the OpenAI-compatible
    `client.chat.completions.create` convention. A missing client or ANY
    call/parse failure RAISES SemanticJudgmentUnavailable -- it no longer
    degrades to a fabricated UNKNOWN (UNKNOWN now only ever means "the model
    judged: no supporting Claim"). The provider chain (app.services.semantic)
    is the production path; this stays for callers that hold one client."""

    def __init__(self, client: Any = None, model: str = "gemma-4-31B-it", temperature: float = 0.0):
        self.client = client
        self.model = model
        self.temperature = temperature

    async def judge_batch(
        self, goal: str, candidates: list[JudgeCandidateInput],
    ) -> list[ApplicabilityJudgment]:
        return [self._judge_one(goal, c) for c in candidates]

    def _judge_one(self, goal: str, candidate: JudgeCandidateInput) -> ApplicabilityJudgment:
        if self.client is None:
            raise SemanticJudgmentUnavailable("no judge client configured")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(judge_user_payload(goal, candidate), default=str)},
                ],
                temperature=self.temperature, max_tokens=500,
            )
            text = (response.choices[0].message.content or "").strip()
            return parse_judgment_dict(goal, candidate, json.loads(text), model=self.model)
        except Exception as exc:  # noqa: BLE001
            log.warning("applicability_judge: LLMJudge call failed", exc_info=True)
            raise SemanticJudgmentUnavailable(f"LLMJudge call failed: {exc!r}") from exc


SYSTEMONE_PATH = "/v1/systemone"


class RemoteHTTPJudge:
    """TypeSafe AI's hosted "Jev" System One model (this is the "JEV"
    transport): ONE endpoint, `POST {base_url}/v1/systemone`, body
    `{state, model, questions}` where each question is typed
    (choice/score/noul); the reply is `{model, answers: {qid: {...}},
    usage}` -- never free text (https://docs.typesafe.ai/concepts/system-one).
    Every operation below is built from that one primitive: a fixed set of
    typed questions per call, parsed back into this module's/prompts.py's
    strict contracts. ANY transport/HTTP/contract failure RAISES
    ProviderError (classified TRANSIENT/PERMANENT) -- it never returns
    fabricated UNKNOWN judgments. The provider chain owns retry and
    fallback."""

    def __init__(
        self, base_url: str, *, http_client: Any = None,
        model: str = "jev-latest", timeout_seconds: float = 10.0,
        max_concurrency: int = 8, api_key: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._http_client = http_client  # injected httpx.AsyncClient-like object; tests supply a fake
        self.model = model  # both the "jev" chain label AND the TypeSafe `model` request field (e.g. "jev-latest")
        self.timeout_seconds = timeout_seconds
        self.max_concurrency = max_concurrency
        self.api_key = api_key
        self.last_batch_latency_ms: Optional[float] = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def post_json(self, path: str, payload: dict) -> dict:
        """One POST with the shared error classification; raises ProviderError."""
        client = self._http_client
        owned = False
        if client is None:
            import httpx
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
            owned = True
        try:
            response = await client.post(f"{self.base_url}{path}", json=payload, headers=self._headers())
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("remote judge reply is not a JSON object")
            return body
        except ProviderError:
            raise
        except ValueError as exc:  # bad JSON / contract violation -> retryable
            raise ProviderError(ErrorKind.TRANSIENT, f"invalid reply: {exc}", provider=self.model) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(classify_exception(exc), repr(exc), provider=self.model) from exc
        finally:
            if owned:
                await client.aclose()

    async def systemone(self, state: str, questions: dict) -> dict:
        """POST /v1/systemone; returns the `answers` map, one entry per
        question key, keyed the same as the request's `questions`."""
        body = await self.post_json(SYSTEMONE_PATH, {"state": state, "model": self.model, "questions": questions})
        answers = body.get("answers")
        if not isinstance(answers, dict) or answers.keys() != questions.keys():
            raise ProviderError(ErrorKind.TRANSIENT, f"systemone reply missing answers for {questions.keys()}",
                               provider=self.model)
        return answers

    def _applicability_questions(self, candidate: JudgeCandidateInput) -> dict:
        conditions = "; ".join(f"[{c.kind}] {c.text}" for c in candidate.conditions) or "(none given)"
        questions: dict = {
            "verdict": {
                "type": "choice",
                "instructions": "Whether the candidate applies to the goal, given its purpose, its "
                                 f"requirement conditions ({conditions}), and the known claims below.",
                "criteria": {
                    "APPLICABLE": "Clearly fits the goal; every REQUIRED condition is met by the claims.",
                    "PARTIALLY_APPLICABLE": "Fits the goal but only in part, or some REQUIRED conditions are unmet.",
                    "INAPPLICABLE": "Does not fit the goal, or a claim contradicts a REQUIRED condition.",
                    "UNKNOWN": "The claims given do not settle it either way.",
                },
            },
            "contradiction": {
                "type": "noul",
                "instructions": "A given claim directly contradicts a REQUIRED condition or the candidate's purpose.",
            },
            "preconditions_met": {
                "type": "noul",
                "instructions": "Every REQUIRED condition is satisfied by the given claims.",
            },
        }
        for i, claim in enumerate(candidate.claims):
            statement = claim.get("statement") or ""
            questions[f"claim_{i}"] = {
                "type": "choice",
                "instructions": f'Whether the claim "{statement}" supports or contradicts the candidate '
                                 "applying to the goal.",
                "criteria": {"supports": "Makes the candidate more likely to apply.",
                            "contradicts": "Makes the candidate less likely to apply.",
                            "unrelated": "Neither."},
            }
        return questions

    async def judge_batch(
        self, goal: str, candidates: list[JudgeCandidateInput],
    ) -> list[ApplicabilityJudgment]:
        import asyncio

        if not candidates:
            return []
        semaphore = asyncio.Semaphore(self.max_concurrency)
        start = time.monotonic()

        async def _call_one(candidate: JudgeCandidateInput) -> ApplicabilityJudgment:
            async with semaphore:
                state = json.dumps({
                    "goal": goal, "candidate_purpose": candidate.candidate_purpose,
                    "claims": [{"claim_id": str(cl.get("claim_id") or cl.get("id")), "statement": cl.get("statement")}
                              for cl in candidate.claims],
                }, default=str, ensure_ascii=False)
                answers = await self.systemone(state, self._applicability_questions(candidate))
                supporting, blocking = [], []
                for i, claim in enumerate(candidate.claims):
                    claim_id = str(claim.get("claim_id") or claim.get("id"))
                    choice = answers[f"claim_{i}"].get("choice")
                    if choice == "supports":
                        supporting.append(claim_id)
                    elif choice == "contradicts":
                        blocking.append(claim_id)
                verdict_answer = answers["verdict"]
                probabilities = verdict_answer.get("probabilities") or {}
                parsed = {
                    "verdict": verdict_answer.get("choice"),
                    "applicability_probability": float(probabilities.get("APPLICABLE", 0.0))
                                                  + 0.5 * float(probabilities.get("PARTIALLY_APPLICABLE", 0.0)),
                    "contradiction_probability": answers["contradiction"].get("noul", 0.0),
                    "preconditions_met_probability": answers["preconditions_met"].get("noul", 0.0),
                    "supporting_claim_ids": supporting,
                    "blocking_claim_ids": blocking,
                    "unknown_requirements": [],
                    "reason": "",  # System One is typed-only; it never synthesizes prose (honest scope limit)
                }
                try:
                    return parse_judgment_dict(goal, candidate, parsed, model=self.model)
                except (ValueError, KeyError) as exc:
                    raise ProviderError(ErrorKind.TRANSIENT, f"invalid judgment: {exc}", provider=self.model) from exc

        try:
            return list(await asyncio.gather(*[_call_one(c) for c in candidates]))
        finally:
            self.last_batch_latency_ms = (time.monotonic() - start) * 1000


def default_judge_from_env() -> "ApplicabilityJudge":
    """The production applicability judge: the shared JEV -> Gemini -> Gemma
    provider chain (app.services.semantic). Always returns a judge -- an
    unconfigured/unreachable chain surfaces as SemanticJudgmentUnavailable at
    judge time (=> PENDING_SEMANTIC_JUDGMENT + requeue), never as "no judge,
    rank by similarity".

    `APPLICABILITY_JUDGE_PROVIDER=mock` returns the deterministic MockJudge
    for local demos ONLY and is refused when settings.environment is
    PRODUCTION: a keyword scorer must never stand in for semantic NLI."""
    import os

    from app.config import settings

    if os.environ.get("APPLICABILITY_JUDGE_PROVIDER", "").strip().lower() == "mock":
        if settings.environment == "PRODUCTION":
            log.error("APPLICABILITY_JUDGE_PROVIDER=mock refused in PRODUCTION; using the semantic chain")
        else:
            log.warning("applicability_judge: MockJudge active (non-production demo only, not semantic NLI)")
            return MockJudge()
    from app.services.semantic.applicability import ChainedApplicabilityJudge
    from app.services.semantic.policy import policy_from_settings

    # Interactive callers (MCP tools) get a wall-clock deadline: bounded short
    # retries, then PENDING + requeue rather than blocking the tool call.
    return ChainedApplicabilityJudge.from_settings(policy=policy_from_settings().interactive())

def stable_claim_ids_hash(claims: list[dict]) -> str:
    """Sec 13's cache key ingredient: a stable hash of the (claim_id,
    version) pairs actually used, sorted so retrieval order never changes
    the hash. A claim's version bumping (or the relevant-claims set
    changing at all) changes this hash, which IS the invalidation
    mechanism -- no separate invalidation logic needed."""
    pairs = sorted(
        (str(c.get("claim_id") or c.get("id") or ""), str(c.get("version", 1)))
        for c in claims
    )
    digest_input = json.dumps(pairs, sort_keys=True).encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


def stable_goal_hash(goal: str) -> str:
    return hashlib.sha256(goal.strip().lower().encode("utf-8")).hexdigest()


async def get_cached_judgment(
    pool: Any, *, goal_hash: str, candidate_id: str, candidate_version: Any,
    claim_ids_hash: str, model: str, model_version: str, prompt_version: str,
) -> Optional[ApplicabilityJudgment]:
    row = await pool.fetchrow(
        "SELECT judgment FROM applicability_judgment_cache WHERE "
        "goal_hash = $1 AND candidate_id = $2 AND candidate_version = $3 "
        "AND claim_ids_hash = $4 AND judge_model = $5 AND judge_model_version = $6 "
        "AND prompt_version = $7",
        goal_hash, candidate_id, str(candidate_version), claim_ids_hash,
        model, model_version, prompt_version,
    )
    if row is None:
        return None
    data = row["judgment"]
    if isinstance(data, str):
        data = json.loads(data)
    return ApplicabilityJudgment(**data)


async def put_cached_judgment(
    pool: Any, *, goal_hash: str, candidate_id: str, candidate_version: Any,
    claim_ids_hash: str, judgment: ApplicabilityJudgment,
    cache_model: Optional[str] = None, cache_model_version: Optional[str] = None,
) -> None:
    """`cache_model[_version]` override the key components when the judge is a
    provider CHAIN: lookups use the chain identity, while `judgment.model`
    records which provider actually produced the verdict."""
    await pool.execute(
        "INSERT INTO applicability_judgment_cache "
        "(goal_hash, candidate_id, candidate_version, claim_ids_hash, "
        " judge_model, judge_model_version, prompt_version, judgment) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb) "
        "ON CONFLICT (goal_hash, candidate_id, candidate_version, claim_ids_hash, "
        "  judge_model, judge_model_version, prompt_version) DO NOTHING",
        goal_hash, candidate_id, str(candidate_version), claim_ids_hash,
        cache_model or judgment.model, cache_model_version or judgment.model_version,
        judgment.prompt_version, json.dumps(judgment.to_dict(), default=str),
    )
