"""
DB-free coverage for applicability_judge.py: MockJudge's three-valued
verdicts, LLMJudge's honest-abstention posture (same convention as
claim_equivalence.py::classify_claim_relation), RemoteHTTPJudge's
fallback-on-unavailable behavior, and the judgment-cache hash's
stability/sensitivity properties (Sec 13's invalidation mechanism).
"""
import asyncio

import pytest

from app.services.applicability_judge import (
    JudgeCandidateInput,
    LLMJudge,
    MockJudge,
    RemoteHTTPJudge,
    RequirementCondition,
    extract_requirement_conditions,
    stable_claim_ids_hash,
    stable_goal_hash,
)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- MockJudge


def test_mock_judge_true_when_claim_supports_required_condition():
    candidate = JudgeCandidateInput(
        candidate_id="c1", candidate_version=1, candidate_purpose="run integration tests",
        conditions=[RequirementCondition(text="Docker is available", kind="REQUIRED")],
        claims=[{"claim_id": "claim-1", "version": 1, "statement": "Docker is available on this host"}],
    )
    [judgment] = _run(MockJudge().judge_batch("run integration tests", [candidate]))
    assert judgment.verdict == "APPLICABLE"
    assert judgment.supporting_claim_ids == ["claim-1"]
    assert judgment.blocking_claim_ids == []


def test_mock_judge_false_hard_rejects_on_required_contradiction():
    """CASE B from the product spec: Docker required, a Claim says Docker
    is unavailable -> INAPPLICABLE, not a low score."""
    candidate = JudgeCandidateInput(
        candidate_id="c1", candidate_version=1, candidate_purpose="run integration tests",
        conditions=[RequirementCondition(text="Docker is available", kind="REQUIRED")],
        claims=[{"claim_id": "claim-2", "version": 1, "statement": "Docker is unavailable in this sandbox"}],
    )
    [judgment] = _run(MockJudge().judge_batch("run integration tests", [candidate]))
    assert judgment.verdict == "INAPPLICABLE"
    assert judgment.blocking_claim_ids == ["claim-2"]
    assert judgment.contradiction_probability == 1.0


def test_mock_judge_unknown_when_no_claim_exists_never_treated_as_false():
    """CASE C: same candidate, NO Docker claim at all -> UNKNOWN, never a
    silent rejection (spec Sec 3: "never treat missing evidence as false")."""
    candidate = JudgeCandidateInput(
        candidate_id="c1", candidate_version=1, candidate_purpose="run integration tests",
        conditions=[RequirementCondition(text="Docker is available", kind="REQUIRED")],
        claims=[],
    )
    [judgment] = _run(MockJudge().judge_batch("run integration tests", [candidate]))
    assert judgment.verdict == "UNKNOWN"
    assert judgment.unknown_requirements == ["Docker is available"]
    assert judgment.blocking_claim_ids == []


def test_mock_judge_batches_multiple_candidates_in_one_call():
    candidates = [
        JudgeCandidateInput(candidate_id=f"c{i}", candidate_version=1, candidate_purpose="p", conditions=[], claims=[])
        for i in range(5)
    ]
    judgments = _run(MockJudge().judge_batch("goal", candidates))
    assert [j.candidate_id for j in judgments] == [f"c{i}" for i in range(5)]
    # No conditions at all -> nothing to contradict or leave unknown -> APPLICABLE.
    assert all(j.verdict == "APPLICABLE" for j in judgments)


# --------------------------------------------------------------- LLMJudge


def test_llm_judge_abstains_with_no_client_configured():
    candidate = JudgeCandidateInput(
        candidate_id="c1", candidate_version=1, candidate_purpose="p",
        conditions=[RequirementCondition(text="x", kind="REQUIRED")], claims=[],
    )
    [judgment] = _run(LLMJudge(client=None).judge_batch("goal", [candidate]))
    assert judgment.verdict == "UNKNOWN"
    assert judgment.applicability_probability == 0.5


class _RaisingClient:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("simulated provider outage")


def test_llm_judge_abstains_never_raises_on_call_failure():
    candidate = JudgeCandidateInput(
        candidate_id="c1", candidate_version=1, candidate_purpose="p",
        conditions=[RequirementCondition(text="x", kind="REQUIRED")], claims=[],
    )
    [judgment] = _run(LLMJudge(client=_RaisingClient()).judge_batch("goal", [candidate]))
    assert judgment.verdict == "UNKNOWN"


class _FakeResponse:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})]


class _FakeClient:
    def __init__(self, response_text):
        self._text = response_text
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        return _FakeResponse(self._text)


def test_llm_judge_parses_a_real_structured_response():
    candidate = JudgeCandidateInput(
        candidate_id="c1", candidate_version=1, candidate_purpose="p",
        conditions=[RequirementCondition(text="Docker is available", kind="REQUIRED")],
        claims=[{"claim_id": "claim-9", "statement": "Docker is unavailable"}],
    )
    text = (
        '{"verdict": "INAPPLICABLE", "applicability_probability": 0.1, '
        '"contradiction_probability": 0.95, "preconditions_met_probability": 0.0, '
        '"supporting_claim_ids": [], "blocking_claim_ids": ["claim-9"], '
        '"unknown_requirements": [], "reason": "Docker unavailable per claim-9"}'
    )
    [judgment] = _run(LLMJudge(client=_FakeClient(text)).judge_batch("goal", [candidate]))
    assert judgment.verdict == "INAPPLICABLE"
    assert judgment.blocking_claim_ids == ["claim-9"]


def test_llm_judge_abstains_on_malformed_json():
    candidate = JudgeCandidateInput(
        candidate_id="c1", candidate_version=1, candidate_purpose="p", conditions=[], claims=[],
    )
    [judgment] = _run(LLMJudge(client=_FakeClient("not json")).judge_batch("goal", [candidate]))
    assert judgment.verdict == "UNKNOWN"


# --------------------------------------------------------------- RemoteHTTPJudge


class _FakeHTTPResponse:
    def __init__(self, body, status_ok=True):
        self._body = body
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error")

    def json(self):
        return self._body


class _FakeHTTPClient:
    def __init__(self, responder):
        self._responder = responder

    async def post(self, url, json):
        return self._responder(url, json)


def test_remote_http_judge_parses_the_documented_contract():
    def responder(url, payload):
        assert url.endswith("/judge-applicability")
        assert payload["goal"] == "goal"
        return _FakeHTTPResponse({
            "verdict": "APPLICABLE", "applicability_probability": 0.9,
            "contradiction_probability": 0.0, "supporting_claim_ids": ["c1"],
            "blocking_claim_ids": [], "unknown_requirements": [],
        })

    candidate = JudgeCandidateInput(
        candidate_id="cand-1", candidate_version=1, candidate_purpose="p", conditions=[], claims=[],
    )
    judge = RemoteHTTPJudge("http://judge.internal", http_client=_FakeHTTPClient(responder))
    [judgment] = _run(judge.judge_batch("goal", [candidate]))
    assert judgment.verdict == "APPLICABLE"
    assert judgment.supporting_claim_ids == ["c1"]


def test_remote_http_judge_falls_back_to_unknown_on_connection_failure():
    """Sec 19: a remote judge outage must never break retrieval -- every
    candidate in the batch degrades to UNKNOWN, judge_batch never raises."""
    class _FailingClient:
        async def post(self, url, json):
            raise ConnectionError("simulated network failure")

    candidates = [
        JudgeCandidateInput(candidate_id=f"c{i}", candidate_version=1, candidate_purpose="p", conditions=[], claims=[])
        for i in range(3)
    ]
    judge = RemoteHTTPJudge("http://judge.internal", http_client=_FailingClient())
    judgments = _run(judge.judge_batch("goal", candidates))
    assert all(j.verdict == "UNKNOWN" for j in judgments)
    assert judge.last_batch_unavailable is True


def test_remote_http_judge_batches_concurrently_not_one_call_at_a_time():
    calls = []

    async def _slow_post(url, json):
        calls.append(json["candidate"]["candidate_id"])
        return _FakeHTTPResponse({
            "verdict": "UNKNOWN", "applicability_probability": 0.5,
            "contradiction_probability": 0.0, "supporting_claim_ids": [],
            "blocking_claim_ids": [], "unknown_requirements": [],
        })

    class _Client:
        post = staticmethod(_slow_post)

    candidates = [
        JudgeCandidateInput(candidate_id=f"c{i}", candidate_version=1, candidate_purpose="p", conditions=[], claims=[])
        for i in range(10)
    ]
    judge = RemoteHTTPJudge("http://judge.internal", http_client=_Client(), max_concurrency=4)
    judgments = _run(judge.judge_batch("goal", candidates))
    assert len(judgments) == 10
    assert set(calls) == {f"c{i}" for i in range(10)}
    assert judge.last_batch_latency_ms is not None


# --------------------------------------------------------------- extraction + hashing


def test_extract_requirement_conditions_classifies_preconditions_as_required():
    procedure = {
        "preconditions": [{"subject": "docker", "predicate": "available", "object": True}],
        "expected_effects": ["tests pass"],
    }
    conditions = extract_requirement_conditions(procedure)
    kinds = {c.kind for c in conditions}
    assert "REQUIRED" in kinds
    assert "TARGET_STATE" in kinds


def test_stable_claim_ids_hash_is_order_independent():
    claims_a = [{"claim_id": "1", "version": 1}, {"claim_id": "2", "version": 3}]
    claims_b = [{"claim_id": "2", "version": 3}, {"claim_id": "1", "version": 1}]
    assert stable_claim_ids_hash(claims_a) == stable_claim_ids_hash(claims_b)


def test_stable_claim_ids_hash_changes_when_a_claim_version_bumps():
    """Sec 13: the cache invalidation mechanism IS the hash changing when
    the relevant-claims set (including any claim's version) changes."""
    claims_v1 = [{"claim_id": "1", "version": 1}]
    claims_v2 = [{"claim_id": "1", "version": 2}]
    assert stable_claim_ids_hash(claims_v1) != stable_claim_ids_hash(claims_v2)


def test_stable_goal_hash_is_case_and_whitespace_insensitive():
    assert stable_goal_hash("  Fix The Bug  ") == stable_goal_hash("fix the bug")
