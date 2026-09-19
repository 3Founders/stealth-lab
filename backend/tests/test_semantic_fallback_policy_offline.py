"""
JEV -> Gemini -> Gemma fallback policy, retries, and the STRICT claim-
conditioned NLI contract (never a deterministic stand-in; PENDING + requeue;
bounded retry; explicit unavailable). All providers are mocked -- no network.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.services.applicability_judge import (
    ApplicabilityJudgment, JudgeCandidateInput, MockJudge, RequirementCondition,
)
from app.services.claim_conditioned_retrieval import find_applicable_candidates
from app.services.semantic import jobs
from app.services.semantic.applicability import ChainedApplicabilityJudge
from app.services.semantic.errors import ErrorKind, ProviderError, SemanticJudgmentUnavailable, classify_exception
from app.services.semantic.policy import RetryPolicy
from app.services.semantic.providers import CAP_APPLICABILITY, CAP_RETENTION, build_provider_chain
from tests.semantic_fakes import QueuePool, ScriptedProvider, make_judge, permanent, transient


def _run(coro):
    return asyncio.run(coro)


def _judgment(cid="p1", verdict="APPLICABLE", model="m"):
    return ApplicabilityJudgment(
        candidate_id=cid, goal_or_query="g", applicability_probability=0.9, contradiction_probability=0.0,
        preconditions_met_probability=0.9, verdict=verdict, model=model)


def _cand(cid="p1"):
    return JudgeCandidateInput(cid, 1, "purpose", [RequirementCondition("docker available")], [])


def _ok_for(name):
    return lambda goal, cands: [_judgment(c.candidate_id, model=name) for c in cands]


# --------------------------------------------------------------- A / B / C


def test_A_jev_success_does_not_invoke_fallbacks():
    jev, gem, gma = (ScriptedProvider("jev", [_ok_for("jev")]), ScriptedProvider("gemini", [_ok_for("gemini")]),
                     ScriptedProvider("gemma", [_ok_for("gemma")]))
    res = _run(make_judge(jev, gem, gma).judge_applicability("g", [_cand()]))
    assert res.ok and res.provider == "jev" and not res.fallback_used
    assert gem.calls == [] and gma.calls == []


def test_B_jev_fails_gemini_is_invoked_after_bounded_retry_with_backoff():
    jev = ScriptedProvider("jev", [transient("timeout")])          # fails every time
    gem = ScriptedProvider("gemini", [_ok_for("gemini")])
    gma = ScriptedProvider("gemma", [_ok_for("gemma")])
    judge = make_judge(jev, gem, gma, attempts=2)
    res = _run(judge.judge_applicability("g", [_cand()]))
    assert res.ok and res.provider == "gemini" and res.fallback_used
    assert len(jev.calls) == 2 and len(gem.calls) == 1 and gma.calls == []
    assert judge.sleeps == [0.75]  # base 1.0 * (0.5 + 0.5*0.5) -- jittered, one backoff between 2 attempts
    c = judge.metrics.counters
    assert c["semantic.fallback_used"] == 1 and c["semantic.provider_failures.jev.transient"] == 2


def test_C_jev_and_gemini_fail_gemma_is_invoked():
    jev = ScriptedProvider("jev", [transient()])
    gem = ScriptedProvider("gemini", [transient("429")])
    gma = ScriptedProvider("gemma", [_ok_for("gemma")])
    res = _run(make_judge(jev, gem, gma).judge_applicability("g", [_cand()]))
    assert res.ok and res.provider == "gemma" and res.fallback_used
    assert [a.provider for a in res.attempts if not a.ok] == ["jev", "jev", "gemini", "gemini"]


def test_permanent_errors_move_on_immediately_without_retries():
    jev = ScriptedProvider("jev", [permanent("invalid api key")])
    gem = ScriptedProvider("gemini", [_ok_for("gemini")])
    judge = make_judge(jev, gem, attempts=3)
    res = _run(judge.judge_applicability("g", [_cand()]))
    assert res.provider == "gemini" and len(jev.calls) == 1 and judge.sleeps == []


def test_unsupported_operation_is_skipped_not_counted_as_failure():
    """JEV exposes applicability only: a summary request never reaches it."""
    jev = ScriptedProvider("jev", [None], caps={CAP_APPLICABILITY, CAP_RETENTION})
    gem = ScriptedProvider("gemini", [{"u1": {"kind": "x"}}])
    res = _run(make_judge(jev, gem).summarize_context({}, [{"unit_id": "u1"}]))
    assert res.ok and res.provider == "gemini" and jev.calls == []
    assert res.fallback_used is False  # gemini IS the first eligible provider for summaries


def test_interactive_deadline_bounds_total_retrying():
    jev = ScriptedProvider("jev", [transient()])
    judge = make_judge(jev, attempts=5, deadline_s=0.0)
    res = _run(judge.judge_applicability("g", [_cand()]))
    assert not res.ok and res.reason == "deadline exceeded" and jev.calls == []


def test_backoff_is_exponential_capped_and_jittered():
    p = RetryPolicy(backoff_base_s=1.0, backoff_max_s=4.0)
    assert p.backoff(1, lambda: 1.0) == 1.0 and p.backoff(2, lambda: 1.0) == 2.0
    assert p.backoff(9, lambda: 1.0) == 4.0                    # capped
    assert p.backoff(3, lambda: 0.0) == 2.0                    # jitter floor = 50%


def test_error_classification():
    class RateLimitError(Exception):
        status_code = 429

    class Boom(Exception):
        status_code = 401
    assert classify_exception(RateLimitError()) is ErrorKind.TRANSIENT
    assert classify_exception(Boom()) is ErrorKind.PERMANENT
    assert classify_exception(asyncio.TimeoutError()) is ErrorKind.TRANSIENT
    assert classify_exception(ConnectionError()) is ErrorKind.TRANSIENT
    assert classify_exception(RuntimeError("?")) is ErrorKind.TRANSIENT  # unknown => never silently permanent


def test_provider_chain_config_order_and_unconfigured_providers_are_skipped():
    s = SimpleNamespace(
        semantic_provider_primary="jev", semantic_provider_fallbacks="gemini,gemma", jev_base_url="http://jev",
        jev_api_key=None, jev_capabilities="applicability,retention,summary", gemini_api_key="k1",
        gemini_api_keys="k2,k3", semantic_gemini_model="gemini-x", semantic_gemini_base_url="http://g/",
        local_model_name="gemma-local", use_local_models=False, local_judge_model="gemma2",
        local_base_url="http://localhost:11434/v1", semantic_provider_timeout_ms=1000)
    chain = build_provider_chain(s)
    assert [p.name for p in chain] == ["jev", "gemini", "gemma"]
    assert len(chain[1]._clients) == 3 and chain[2].model == "gemma-local"
    assert "summary" not in chain[0].capabilities            # JEV never claims text synthesis
    s.jev_base_url, s.gemini_api_key, s.gemini_api_keys, s.local_model_name = None, None, None, None
    assert build_provider_chain(s) == []                     # nothing configured => empty chain (=> unavailable)


# --------------------------------------------------------------- NLI orchestration


def _patch_pipeline(monkeypatch, survivors):
    async def fake_find(pool, **kw):
        return survivors
    async def fake_claims(pool, *, goal, top_k, access_scope=None):
        return []
    async def fake_cap(pool, s):
        return [(x["id"], "procedures", i) for i, x in enumerate(s)]
    monkeypatch.setattr("app.services.claim_conditioned_retrieval.find_applicable_procedures", fake_find)
    monkeypatch.setattr("app.services.claim_conditioned_retrieval.get_relevant_claims", fake_claims)
    monkeypatch.setattr("app.services.claim_conditioned_retrieval._capability_ranked_hits", fake_cap)


def _procedure(pid="p1"):
    return {"id": pid, "procedure_id": pid, "version": 1, "name": pid, "goal": "do the thing",
            "preconditions": [{"subject": "docker", "predicate": "available", "object": True}],
            "expected_effects": [], "_similarity_score": 0.8}


def _all_down_judge():
    return ChainedApplicabilityJudge(make_judge(
        ScriptedProvider("jev", [transient()]), ScriptedProvider("gemini", [transient()]),
        ScriptedProvider("gemma", [permanent("model not pulled")])))


class _NoCachePool(QueuePool):
    """QueuePool has no applicability_judgment_cache reader; NLI cache misses."""
    async def fetchrow(self, sql, *a):
        if "applicability_judgment_cache" in sql:
            return None
        return await super().fetchrow(sql, *a)

    async def fetch(self, sql, *a):
        return []


def test_E_all_providers_fail_nli_is_pending_queued_and_never_deterministic(monkeypatch):
    _patch_pipeline(monkeypatch, [_procedure("p1"), _procedure("p2")])
    used_mock = []
    monkeypatch.setattr(MockJudge, "_judge_one", lambda self, *a: used_mock.append(1))  # RETRIEVAL SAFETY INVARIANT
    pool = _NoCachePool()
    result = _run(find_applicable_candidates(pool, goal_text="fix bug", judge=_all_down_judge(), limit=5))
    assert result.contextual_judgment_status == "PENDING_SEMANTIC_JUDGMENT"
    assert result.candidates == []                                   # no similarity/keyword ranking presented as final
    assert result.pending_job_id is not None and result.observability.pending_judgments == 2
    [job] = pool.pending(jobs.SEMANTIC_JUDGMENT_JOB)
    assert {c["candidate_id"] for c in job["payload"]["candidates"]} == {"p1", "p2"}
    assert used_mock == []
    # repeated interactive call does not stack duplicate jobs
    _run(find_applicable_candidates(pool, goal_text="fix bug", judge=_all_down_judge(), limit=5))
    assert len(pool.pending(jobs.SEMANTIC_JUDGMENT_JOB)) == 1


def test_F_retry_rounds_are_bounded_then_explicitly_unavailable(monkeypatch):
    _patch_pipeline(monkeypatch, [_procedure("p1")])
    pool = _NoCachePool()
    _run(find_applicable_candidates(pool, goal_text="fix bug", judge=_all_down_judge(), limit=5))
    policy = RetryPolicy(job_max_retries=3, requeue_delay_s=10.0)
    rounds = 0
    while True:
        [row] = pool.pending(jobs.SEMANTIC_JUDGMENT_JOB)
        row["status"] = "processing"
        rounds += 1
        try:
            _run(jobs.handle_semantic_judgment(pool, row["payload"], judge=_all_down_judge(), policy=policy))
            row["status"] = "done"           # round handled: next round was enqueued
            assert pool.rows[-1]["delay"] > 0   # backed off via run_after
        except SemanticJudgmentUnavailable as exc:
            row["status"], row["last_error"] = "failed", repr(exc)   # what process_pending_jobs records
            break
        assert rounds < 10
    assert rounds == 3 and "SEMANTIC_JUDGMENT_UNAVAILABLE" in pool.rows[-1]["last_error"]
    state = _run(jobs.get_semantic_job_state(_StatePool(pool), 1))
    assert state["state"] == "SEMANTIC_JUDGMENT_UNAVAILABLE"
    # a fresh interactive call reports UNAVAILABLE and does NOT restart the retry loop
    again = _run(find_applicable_candidates(pool, goal_text="fix bug", judge=_all_down_judge(), limit=5))
    assert again.contextual_judgment_status == "SEMANTIC_JUDGMENT_UNAVAILABLE"
    assert again.candidates == [] and again.observability.retry_exhausted == 1
    assert pool.pending(jobs.SEMANTIC_JUDGMENT_JOB) == []


class _StatePool:
    def __init__(self, pool):
        self.pool = pool

    async def fetchrow(self, sql, job_id):
        r = [x for x in self.pool.rows if x["id"] == job_id or x["payload"].get("root_job_id") == job_id][-1]
        return {"id": r["id"], "status": r["status"], "attempts": 1, "last_error": r["last_error"],
                "round": str(r["payload"].get("round", 1))}


def test_requeued_job_success_populates_cache_so_next_call_completes(monkeypatch):
    pool = _NoCachePool()
    payload = {"goal": "fix bug", "round": 1, "dedup_key": "k", "root_job_id": 1,
               "candidates": [jobs.candidate_to_payload(_cand("p1"))]}
    judge = ChainedApplicabilityJudge(make_judge(ScriptedProvider("jev", [_ok_for("jev")])))
    _run(jobs.handle_semantic_judgment(pool, payload, judge=judge, policy=RetryPolicy()))
    [args] = pool.cache_rows
    assert args[1] == "p1" and args[4] == "semantic-chain"      # keyed on the CHAIN identity used at lookup


def test_G_unknown_is_a_model_verdict_not_a_failure_and_not_inapplicable(monkeypatch):
    _patch_pipeline(monkeypatch, [_procedure("p1")])
    unknown = lambda goal, cands: [_judgment(c.candidate_id, verdict="UNKNOWN") for c in cands]
    judge = ChainedApplicabilityJudge(make_judge(ScriptedProvider("jev", [unknown])))
    result = _run(find_applicable_candidates(_NoCachePool(), goal_text="fix bug", judge=judge, limit=5))
    assert result.contextual_judgment_status == "ok"
    assert [c.judgment.verdict for c in result.candidates] == ["UNKNOWN"]   # survives; not INAPPLICABLE


def test_unjudged_failure_is_never_reported_as_unknown_success(monkeypatch):
    _patch_pipeline(monkeypatch, [_procedure("p1")])
    result = _run(find_applicable_candidates(_NoCachePool(), goal_text="g", judge=_all_down_judge(), limit=5))
    assert result.contextual_judgment_status != "ok" and not any(c.judgment for c in result.candidates)


def test_tripwire_offline_suite_never_builds_a_live_provider_chain():
    """conftest's autouse guard: from_settings() is empty under pytest even
    though a developer's .env may carry real Gemini/JEV credentials."""
    from app.services.semantic.chain import SemanticJudge
    assert SemanticJudge.from_settings().providers == []
    assert ChainedApplicabilityJudge.from_settings().judge.providers == []


def test_mock_judge_is_refused_in_production(monkeypatch):
    from app.services.applicability_judge import default_judge_from_env
    monkeypatch.setenv("APPLICABILITY_JUDGE_PROVIDER", "mock")
    monkeypatch.setenv("STEALTHLAB_ENV", "production")
    assert isinstance(default_judge_from_env(), ChainedApplicabilityJudge)
    monkeypatch.setenv("STEALTHLAB_ENV", "test")
    assert isinstance(default_judge_from_env(), MockJudge)


def test_claim_relation_via_chain_reports_unavailable_never_a_guess():
    from app.services.claim_equivalence import classify_claim_relation_via_chain
    ok = make_judge(ScriptedProvider("jev", [{"relation": "equivalent", "confidence": 0.9}]))
    assert _run(classify_claim_relation_via_chain(ok, "a", "b"))["relation"] == "equivalent"
    down = make_judge(ScriptedProvider("jev", [transient()]))
    r = _run(classify_claim_relation_via_chain(down, "a", "b"))
    assert r["status"] == "unavailable" and r["confidence"] == 0.0
