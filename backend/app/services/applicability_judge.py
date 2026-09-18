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

HONEST SCOPE
    No real NLI/transformers model is wired up in this pass (matches
    claim_equivalence.py's own disclosed state). Three providers exist:
    MockJudge (deterministic, string-overlap; the default for tests and
    CI -- no paid/live calls), LLMJudge (same OpenAI-compatible `client`
    convention as claim_equivalence.py, honest abstention on any failure),
    and RemoteHTTPJudge (calls an operator-configured POST /judge-
    applicability endpoint -- the real "hosting assumption" the product
    spec calls for, so deployment can move to Modal/HF Endpoints/etc
    without this module or its callers changing). None of the three is
    exercised against a live paid endpoint by this repo's own test suite.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

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


def _unknown_judgment(
    goal: str, candidate: JudgeCandidateInput, *, model: str, reason: str,
) -> ApplicabilityJudgment:
    """The shared honest-abstention fallback every provider below returns on
    a real failure -- UNKNOWN, never a fabricated APPLICABLE/INAPPLICABLE
    verdict (Sec 3: "never treat missing evidence as false")."""
    return ApplicabilityJudgment(
        candidate_id=candidate.candidate_id, goal_or_query=goal,
        applicability_probability=0.5, contradiction_probability=0.0,
        preconditions_met_probability=0.5, verdict="UNKNOWN",
        unknown_requirements=[c.text for c in candidate.conditions],
        reason=reason, model=model,
    )


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
    """LLM-as-judge provider, same honest-abstention posture as
    claim_equivalence.py::classify_claim_relation: no `client`, or any call/
    parse failure, degrades to UNKNOWN -- never a fabricated verdict. Uses
    the same OpenAI-compatible `client.chat.completions.create` convention
    already established at that call site and in skill_ingestion.py, so
    this adds no new external dependency."""

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
            return _unknown_judgment(goal, candidate, model=self.model, reason="no judge client configured")
        try:
            user_content = json.dumps({
                "goal": goal, "candidate_purpose": candidate.candidate_purpose,
                "conditions": [{"text": c.text, "kind": c.kind} for c in candidate.conditions],
                "claims": [
                    {"claim_id": str(cl.get("claim_id") or cl.get("id")), "statement": cl.get("statement")}
                    for cl in candidate.claims
                ],
            }, default=str)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=self.temperature, max_tokens=500,
            )
            text = (response.choices[0].message.content or "").strip()
            parsed = json.loads(text)
            verdict = parsed.get("verdict")
            if verdict not in VERDICTS:
                return _unknown_judgment(goal, candidate, model=self.model, reason="model returned an invalid verdict")
            return ApplicabilityJudgment(
                candidate_id=candidate.candidate_id, goal_or_query=goal,
                applicability_probability=float(parsed.get("applicability_probability", 0.5)),
                contradiction_probability=float(parsed.get("contradiction_probability", 0.0)),
                preconditions_met_probability=float(parsed.get("preconditions_met_probability", 0.5)),
                verdict=verdict,
                supporting_claim_ids=[str(x) for x in parsed.get("supporting_claim_ids", [])],
                blocking_claim_ids=[str(x) for x in parsed.get("blocking_claim_ids", [])],
                unknown_requirements=[str(x) for x in parsed.get("unknown_requirements", [])],
                reason=str(parsed.get("reason", "")), model=self.model,
            )
        except Exception:  # noqa: BLE001 -- a judge call's own failure degrades to abstention, never a fabricated verdict
            log.warning("applicability_judge: LLMJudge call failed, abstaining", exc_info=True)
            return _unknown_judgment(goal, candidate, model=self.model, reason="judge call failed")


class RemoteHTTPJudge:
    """Remote inference service provider (Sec 11): POSTs to
    `{base_url}/judge-applicability`, one candidate per request body
    exactly matching the documented contract. Batches via bounded
    concurrent requests (Sec 12) rather than one-at-a-time sequential
    calls, tracks latency. On ANY connection failure/timeout (Sec 19: "DO
    NOT break retrieval"), returns UNKNOWN judgments for the whole batch --
    the caller (claim_conditioned_retrieval.py) is expected to also set
    contextual_judgment_status='unavailable' at that point, this class only
    guarantees it never raises out of judge_batch()."""

    def __init__(
        self, base_url: str, *, http_client: Any = None,
        model: str = "remote-judge", timeout_seconds: float = 10.0,
        max_concurrency: int = 8,
    ):
        self.base_url = base_url.rstrip("/")
        self._http_client = http_client  # injected httpx.AsyncClient-like object; tests supply a fake
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_concurrency = max_concurrency
        self.last_batch_latency_ms: Optional[float] = None
        self.last_batch_unavailable = False

    async def judge_batch(
        self, goal: str, candidates: list[JudgeCandidateInput],
    ) -> list[ApplicabilityJudgment]:
        import asyncio

        if not candidates:
            return []
        client = self._http_client
        if client is None:
            try:
                import httpx
                client = httpx.AsyncClient(timeout=self.timeout_seconds)
            except ImportError:
                self.last_batch_unavailable = True
                return [
                    _unknown_judgment(goal, c, model=self.model, reason="no HTTP client available")
                    for c in candidates
                ]

        semaphore = asyncio.Semaphore(self.max_concurrency)
        start = time.monotonic()

        async def _call_one(candidate: JudgeCandidateInput) -> ApplicabilityJudgment:
            async with semaphore:
                try:
                    payload = {
                        "goal": goal, "candidate": {
                            "candidate_id": candidate.candidate_id,
                            "candidate_version": candidate.candidate_version,
                            "purpose": candidate.candidate_purpose,
                        },
                        "claims": candidate.claims,
                        "preconditions": [
                            {"text": c.text, "kind": c.kind} for c in candidate.conditions
                        ],
                    }
                    response = await client.post(f"{self.base_url}/judge-applicability", json=payload)
                    response.raise_for_status()
                    body = response.json()
                    verdict = body.get("verdict")
                    if verdict not in VERDICTS:
                        return _unknown_judgment(goal, candidate, model=self.model, reason="remote judge returned invalid verdict")
                    return ApplicabilityJudgment(
                        candidate_id=candidate.candidate_id, goal_or_query=goal,
                        applicability_probability=float(body.get("applicability_probability", 0.5)),
                        contradiction_probability=float(body.get("contradiction_probability", 0.0)),
                        preconditions_met_probability=float(body.get("preconditions_met_probability", 0.5)),
                        verdict=verdict,
                        supporting_claim_ids=[str(x) for x in body.get("supporting_claim_ids", [])],
                        blocking_claim_ids=[str(x) for x in body.get("blocking_claim_ids", [])],
                        unknown_requirements=[str(x) for x in body.get("unknown_requirements", [])],
                        reason=str(body.get("reason", "")), model=self.model,
                    )
                except Exception:  # noqa: BLE001 -- Sec 19: a remote failure must never break retrieval
                    log.warning("applicability_judge: RemoteHTTPJudge call failed, abstaining", exc_info=True)
                    self.last_batch_unavailable = True
                    return _unknown_judgment(goal, candidate, model=self.model, reason="remote judge call failed")

        try:
            results = await asyncio.gather(*[_call_one(c) for c in candidates])
        finally:
            self.last_batch_latency_ms = (time.monotonic() - start) * 1000
        return list(results)


def default_judge_from_env() -> Optional["ApplicabilityJudge"]:
    """Provider selection via config (Sec 10). `APPLICABILITY_JUDGE_PROVIDER`
    defaults to unset -- meaning None (no judge), the safe production
    default until an operator explicitly opts in, matching Sec 19's own
    fallback contract (no judge configured is treated exactly like a judge
    that is unavailable, never like "judge everything TRUE"). Values:
    "mock" (MockJudge, deterministic -- safe for demos/tests, NOT a real
    NLI claim), "llm" (LLMJudge -- requires APPLICABILITY_JUDGE_CLIENT to
    be wired by the caller; this factory cannot construct a real client
    itself), "remote_http" (RemoteHTTPJudge against
    APPLICABILITY_JUDGE_REMOTE_URL)."""
    import os

    provider = os.environ.get("APPLICABILITY_JUDGE_PROVIDER", "").strip().lower()
    if not provider or provider == "none":
        return None
    if provider == "mock":
        return MockJudge()
    if provider == "remote_http":
        base_url = os.environ.get("APPLICABILITY_JUDGE_REMOTE_URL")
        if not base_url:
            log.warning("APPLICABILITY_JUDGE_PROVIDER=remote_http but APPLICABILITY_JUDGE_REMOTE_URL is unset; no judge")
            return None
        return RemoteHTTPJudge(base_url)
    log.warning("APPLICABILITY_JUDGE_PROVIDER=%r not recognized (mock|remote_http|none); no judge", provider)
    return None


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
) -> None:
    await pool.execute(
        "INSERT INTO applicability_judgment_cache "
        "(goal_hash, candidate_id, candidate_version, claim_ids_hash, "
        " judge_model, judge_model_version, prompt_version, judgment) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb) "
        "ON CONFLICT (goal_hash, candidate_id, candidate_version, claim_ids_hash, "
        "  judge_model, judge_model_version, prompt_version) DO NOTHING",
        goal_hash, candidate_id, str(candidate_version), claim_ids_hash,
        judgment.model, judgment.model_version, judgment.prompt_version,
        json.dumps(judgment.to_dict(), default=str),
    )
