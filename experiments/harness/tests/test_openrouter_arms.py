"""Offline proving tests for the real-model arms (MEASURE-WAVE item 0).

House style: every coroutine is driven via asyncio.run against injected
fake transports/clients - zero network, zero wall-clock sleeps, no key
required anywhere in this file.
"""
import asyncio
import json

import openrouter_arms as ora
import run_harness
import scripted_arms
from conftest import MICRO_FIXTURES

import pytest


def dec(resolved=True, reuse=None, refuse=None, notes="ok"):
    return {"resolved": resolved, "reuse": reuse or [],
            "refuse": refuse or [], "notes": notes}


class FakeClient:
    """FrontierClient double: pops canned chat responses (or exceptions)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, *, task_id="", arm=""):
        self.calls.append({"messages": messages, "task_id": task_id,
                           "arm": arm})
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def reply(content="{}", model="ox-alpha", tin=100, tout=20):
    return {"content": content, "model": model, "tokens_in": tin,
            "tokens_out": tout, "latency_s": 0.5, "attempts": []}


def json_reply(d, **kw):
    return reply(json.dumps(d), **kw)


class TestKeyResolution:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        assert ora.resolve_api_key(
            env={"OPENROUTER_API_KEY": "from-env"},
            env_path="/nonexistent/.env") == "from-env"

    def test_env_file_fallback(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text('OTHER=1\nOPENROUTER_API_KEY="from-file"\n',
                     encoding="utf-8")
        assert ora.resolve_api_key(env={}, env_path=p) == "from-file"

    def test_nowhere_is_none_never_raises(self, tmp_path):
        assert ora.resolve_api_key(
            env={}, env_path=tmp_path / "nope") is None

    def test_primary_model_pin(self):
        # Board item text: ox-alpha primary.
        assert ora.DEFAULT_MODEL_CHAIN[0] == "ox-alpha"
        assert len(ora.DEFAULT_MODEL_CHAIN) >= 2  # alternates documented

    def test_headers_shape(self):
        h = ora.build_headers("sk-test")
        assert h["Authorization"] == "Bearer sk-test"
        assert h["Content-Type"] == "application/json"


class TestParseDecision:
    def test_plain_fenced_and_prose_wrapped(self):
        d = dec(resolved=False)
        for text in (json.dumps(d),
                     f"```json\n{json.dumps(d)}\n```",
                     f"Sure! {json.dumps(d)} - hope that helps"):
            got = ora.parse_decision(text)
            assert got["resolved"] is False
            assert got["notes"] == "ok"

    def test_garbage_and_wrong_shapes_return_none(self):
        assert ora.parse_decision("no json here") is None
        assert ora.parse_decision('{"resolved": "yes"}') is None  # not bool
        assert ora.parse_decision('[1,2,3]') is None

    def test_refuse_string_entries_coerced(self):
        got = ora.parse_decision(json.dumps(
            dec(refuse=[{"procedure_id": "p1", "reason": "r"}, "p2"])))
        assert got["refuse"][0]["reason"] == "r"
        assert got["refuse"][1] == {"procedure_id": "p2", "reason": ""}

    def test_reuse_nonstrings_dropped(self):
        got = ora.parse_decision(json.dumps(dec(reuse=["a", 5, None])))
        assert got["reuse"] == ["a"]


class _TimedSleep:
    def __init__(self):
        self.sleeps = []

    async def __call__(self, s):
        self.sleeps.append(s)


class TestClientBackoffAndFallback:
    def _client(self, transport_script, sleep_rec, rng=None,
                models=ora.DEFAULT_MODEL_CHAIN[:2]):
        calls = {"n": 0}

        async def transport(payload):
            i = min(calls["n"], len(transport_script) - 1)
            outcome = transport_script[i]
            calls["n"] += 1
            if isinstance(outcome, Exception):
                raise outcome
            status, body = outcome
            if status == 200:
                return status, {"choices": [{"message": {"content": "{}"}}],
                                "usage": {"prompt_tokens": 10,
                                          "completion_tokens": 5}}
            return status, {}

        rng = rng or (lambda: 0.5)
        return ora.OpenRouterClient("k", models=models, transport=transport,
                                    sleep=sleep_rec, rng=rng), calls

    def test_429_backs_off_then_succeeds(self):
        sleep_rec = _TimedSleep()
        client, calls = self._client([(429, {}), (429, {}), (200, {})],
                                     sleep_rec)
        out = asyncio.run(client.chat([{"role": "user", "content": "x"}]))
        assert out["model"] == "ox-alpha"
        assert out["tokens_in"] == 10 and out["tokens_out"] == 5
        assert len(sleep_rec.sleeps) == 2
        ceilings = [min(ora.BACKOFF_CAP_S,
                        ora.BACKOFF_BASE_S * 2 ** a) * 1.0 for a in (0, 1)]
        assert all(0 <= s <= c for s, c in zip(sleep_rec.sleeps, ceilings))
        assert sleep_rec.sleeps[0] <= sleep_rec.sleeps[1]

    def test_full_jitter_bounds_with_random_rng(self):
        client, _ = self._client([], _TimedSleep(), rng=None)
        client._rng = __import__("random").random
        delays = [client.backoff_delay(a) for a in range(8)]
        assert max(delays) <= ora.BACKOFF_CAP_S
        assert any(d > 0 for d in delays)
        assert all(d < min(ora.BACKOFF_CAP_S,
                           ora.BACKOFF_BASE_S * 2 ** a)
                   for a, d in enumerate(delays))

    def test_network_error_is_retryable(self):
        sleep_rec = _TimedSleep()
        client, _ = self._client([ConnectionError("boom"), (200, {})],
                                 sleep_rec)
        out = asyncio.run(client.chat([{"role": "user", "content": "x"}]))
        assert out["content"] == "{}"

    def test_nonretryable_4xx_falls_through_to_next_model_immediately(self):
        sleep_rec = _TimedSleep()
        alt = ora.DEFAULT_MODEL_CHAIN[1]
        client, calls = self._client([(404, {"error": "no such model"}),
                                      (200, {})], sleep_rec)
        out = asyncio.run(client.chat([{"role": "user", "content": "x"}]))
        assert out["model"] == alt
        assert len(sleep_rec.sleeps) == 0  # no retries burned on the dead one

    def test_chain_exhaustion_raises_with_attempt_trail(self):
        sleep_rec = _TimedSleep()
        models = ora.DEFAULT_MODEL_CHAIN[:2]
        client, _ = self._client([(429, {})], sleep_rec, models=models)
        client.max_attempts = 3
        with pytest.raises(ora.AllModelsFailedError) as ei:
            asyncio.run(client.chat([{"role": "user", "content": "x"}]))
        attempts = ei.value.attempts
        assert len(attempts) == len(models) * 3
        assert {a["model"] for a in attempts} == set(models)

    def test_payload_shape_sent_to_transport(self):
        seen = {}

        async def transport(payload):
            seen.update(payload)
            return 200, {"choices": [{"message": {"content": "{}"}}],
                         "usage": {}}

        client = ora.OpenRouterClient("k", transport=transport,
                                      sleep=_TimedSleep(),
                                      rng=lambda: 0.5)
        asyncio.run(client.chat([{"role": "user", "content": "hi"}],
                                task_id="t1", arm="A"))
        assert seen["model"] == ora.DEFAULT_MODEL_CHAIN[0]
        assert seen["max_tokens"] == ora.MAX_COMPLETION_TOKENS
        assert seen["messages"] == [{"role": "user", "content": "hi"}]

    def test_built_messages_lead_with_system(self):
        msgs = ora.build_messages("A", {"task_id": "t", "domain": "d"}, {})
        assert msgs[0]["role"] == "system"
        assert ora.DECISION_SCHEMA in msgs[0]["content"]
        assert msgs[1]["role"] == "user"


class TestSpendLedger:
    def test_rows_per_attempt_success_and_failure_math(self, tmp_path):
        path = tmp_path / "spend.jsonl"
        log = ora.SpendLog(path)
        log.record(task_id="t", arm="A", model="m", attempt=0, status=200,
                   latency_s=1.0, tokens_in=1_000_000, tokens_out=1_000_000)
        log.record(task_id="t", arm="A", model="m", attempt=1, status=429,
                   latency_s=0.2)
        s = log.summarize()
        assert s["attempts"] == 2 and s["billed_calls"] == 1
        assert s["failed_attempts"] == 1
        assert s["tokens_in"] == 1_000_000 and s["tokens_out"] == 1_000_000
        assert s["cost_usd"] == 12.5  # default table 2.50 + 10.00 per Mtok
        rows = [json.loads(l) for l in
                path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2 and rows[1]["status"] == 429
        assert rows[1]["tokens_in"] == 0 and rows[1]["cost_usd"] == 0.0

    def test_free_suffix_prices_zero_regardless_of_provider(self, tmp_path):
        log = ora.SpendLog(tmp_path / "spend.jsonl")
        log.record(task_id="t", arm="EX", model="liquid/lfm-2.5-2.6b:free",
                   attempt=0, status=200, latency_s=1.0,
                   tokens_in=1_000_000, tokens_out=1_000_000)
        assert log.summarize()["cost_usd"] == 0.0
        row = json.loads((tmp_path / "spend.jsonl").read_text("utf-8"))
        assert row["cost_usd"] == 0.0

    def test_render_names_models(self):
        log = ora.SpendLog(None)
        log.record(task_id="t", arm="C", model="ox-alpha", attempt=0,
                   status=200, latency_s=1.0, tokens_in=10, tokens_out=5)
        text = log.render()
        assert "ox-alpha" in text and "SPEND" in text


EPISODE_KEYS = set(scripted_arms._base_episode({"task_id": "t"}, "A"))


class TestRealSoloAgent:
    def _agent(self, client_replies):
        client = FakeClient(client_replies)
        return (ora.RealSoloAgent(client, MICRO_FIXTURES,
                                  {"mic-dep-003": {"situation": "SIT"}}),
                client)

    def test_episode_schema_parity_and_fields(self):
        agent, client = self._agent([json_reply(dec(True))])
        task = {"task_id": "mic-dep-003", "domain": "deps", "unseen": True,
                "stale_offer": "some-stale"}
        ep = asyncio.run(agent.arun(task))
        assert EPISODE_KEYS <= set(ep)  # superset of the scripted schema
        assert ep["resolved"] is True and ep["valid"] is True
        assert ep["arm"] == "A" and ep["unseen_task"] is True
        assert ep["stale_offered"] == []  # arm A sees nothing (parity)
        assert ep["tokens_in"] == 100 and ep["tool_calls"] == 1
        assert ep["served_by_model"] == "ox-alpha"
        # prompt carries the situation text, never fixture ground truth keys
        user = client.calls[0]["messages"][1]["content"]
        assert "SIT" in user and "solo_outcome" not in user

    def test_situation_leak_sanitized(self):
        # mic-dep-003's fixture narrative states the scripted verdict
        # outright; the prompt must carry the situation, not the answer.
        situations = ora.load_situations(MICRO_FIXTURES)
        assert "Honest outcome" in situations["mic-dep-003"]["situation"]
        agent = ora.RealSoloAgent(FakeClient([]), MICRO_FIXTURES, situations)
        text = ora.situation_text({"task_id": "mic-dep-003",
                                   "domain": "deps"}, situations)
        assert "Honest outcome" not in text
        assert "unseen toolchain conflict" in text

    def test_task_embedded_situation_used_when_no_scenario_entry(self):
        # model-decides tier tasks carry their own `situation` field and
        # have no scenarios.json row at all (that file's 8-12 count cap
        # belongs to the micro pack, not this tier) - situation_text must
        # still find their prose.
        task = {"task_id": "dec-refund-101", "domain": "refunds",
                "situation": "Customer requests a $540 refund."}
        assert ora.situation_text(task, {}) == \
            "Customer requests a $540 refund."

    def test_scenario_situation_wins_over_task_embedded_one(self):
        # Existing micro-pack behavior is unchanged: when BOTH exist, the
        # scenarios.json entry (the richer, evidence_requirements-linked
        # authoring surface) still takes precedence.
        task = {"task_id": "t", "situation": "task-level text"}
        scenarios = {"t": {"situation": "scenario-level text"}}
        assert ora.situation_text(task, scenarios) == "scenario-level text"

    def test_unparseable_after_repair_is_invalid_retryable(self, tmp_path):
        agent, client = self._agent([reply("I think maybe?"),
                                     reply("still not json")])
        ep = asyncio.run(agent.arun({"task_id": "t", "domain": "d"}))
        assert ep["valid"] is False
        assert ep["invalid_reason"] == "unparseable_decision_after_repair"
        assert ep["resolved"] is False
        assert len(client.calls) == 2  # exactly one repair round-trip
        # ...and the runner's resume rule retries it (never counts as done).
        path = tmp_path / "rows.jsonl"
        path.write_text(json.dumps({
            "task_id": "t", "A": ep, "B": {"valid": True},
            "C": {"valid": True}}), encoding="utf-8")
        assert "t" not in run_harness.load_done(path)

    def test_repair_second_round_succeeds_and_bills_both_calls(self):
        agent, client = self._agent([reply("oops"), json_reply(dec(False))])
        ep = asyncio.run(agent.arun({"task_id": "t", "domain": "d"}))
        assert ep["valid"] is True and ep["resolved"] is False
        assert ep["tokens_in"] == 200  # both calls accumulated
        repair = client.calls[1]["messages"]
        assert repair[-1]["role"] == "user" and "JSON" in repair[-1]["content"]

    def test_all_models_failed_propagates(self):
        agent, _ = self._agent([ora.AllModelsFailedError([])])
        with pytest.raises(ora.AllModelsFailedError):
            asyncio.run(agent.arun({"task_id": "t", "domain": "d"}))


class TestRealMemoryAgent:
    def _agent(self, client_replies):
        client = FakeClient(client_replies)
        return (ora.RealMemoryAgent(client, MICRO_FIXTURES), client)

    def test_blob_injected_when_rag_present(self):
        agent, client = self._agent([json_reply(dec(True))])
        ep = asyncio.run(agent.arun(
            {"task_id": "t", "domain": "refunds", "rag": "misleading"}))
        user = client.calls[0]["messages"][1]["content"]
        assert "one-click auto-refund snippet" in user  # rag-refunds-1 text
        assert ep["followed_memory_ids"] == ["rag-refunds-1"]

    def test_missing_domain_blob_degrades_without_crash(self):
        agent, client = self._agent([json_reply(dec(True))])
        ep = asyncio.run(agent.arun(
            {"task_id": "t", "domain": "no-such-domain", "rag": "helpful"}))
        assert ep["followed_memory_ids"] == ["rag-missing"]
        assert ep["valid"] is True
        assert "[Retrieved conventional memory" \
            not in client.calls[0]["messages"][1]["content"]

    def test_stale_offer_visible_to_b(self):
        agent, _ = self._agent([json_reply(dec(True))])
        ep = asyncio.run(agent.arun(
            {"task_id": "t", "domain": "refunds", "rag": "none",
             "stale_offer": "refund-auto-v1"}))
        assert ep["stale_offered"] == ["refund-auto-v1"]
        assert ep["followed_memory_ids"] == []

    def test_mechanical_false_reuse_attribution(self):
        agent, _ = self._agent([json_reply(dec(False))])
        ep = asyncio.run(agent.arun(
            {"task_id": "t", "domain": "refunds", "rag": "helpful"}))
        # followed memory + failed -> attributed; NO fixture-truth read
        # ('misleading' never consulted by the agent).
        assert ep["reuse_caused_failure"] is True
        agent2, _ = self._agent([json_reply(dec(True))])
        ok = asyncio.run(agent2.arun(
            {"task_id": "t", "domain": "refunds", "rag": "misleading"}))
        assert ok["reuse_caused_failure"] is False  # success is never false reuse


class TestRealProcedureAgent:
    def _agent(self, client_replies, surface=None):
        client = FakeClient(client_replies)
        agent = ora.RealProcedureAgent(client, MICRO_FIXTURES,
                                       surface=surface)
        return agent, client

    def test_applicable_reuse_credited_after_gate_call(self):
        agent, client = self._agent([json_reply(
            dec(True, reuse=["dep-resolver-v2"]))])
        task = {"task_id": "mic-dep-001", "domain": "deps",
                "applicable_procedure": "dep-resolver-v2"}
        ep = asyncio.run(agent.arun(task))
        assert ep["reused_procedure_ids"] == ["dep-resolver-v2"]
        assert ep["refused_procedure_ids"] == []
        tools = [j["tool"] for j in agent.surface.journal()]
        assert tools.count("check_applicability") == 1
        assert "record_refusal" not in tools
        assert ep["resolved"] is True
        user = client.calls[0]["messages"][1]["content"]
        assert "id=dep-resolver-v2" in user  # card was offered

    def test_gate_blocked_stale_offer_refused_before_model_sees_it(self):
        agent, _ = self._agent([json_reply(dec(True))])
        task = {"task_id": "mic-refund-001", "domain": "refunds",
                "stale_offer": "refund-auto-v1"}
        ep = asyncio.run(agent.arun(task))
        assert ep["refused_procedure_ids"] == ["refund-auto-v1"]
        refusals = [j for j in agent.surface.journal()
                    if j["tool"] == "record_refusal"]
        assert len(refusals) == 1
        user = agent.client.calls[0]["messages"][1]["content"]
        assert "id=refund-auto-v1" not in user  # card withheld from model

    def test_agent_initiated_refusal_of_offered_stale_proc_recorded(self):
        agent, _ = self._agent([json_reply(dec(True, refuse=[
            {"procedure_id": "refund-auto-v1",
             "reason": "assumptions outdated"}]))])
        # The card only reaches the model through the poisoned gate (the
        # bypass task shape); refusing an OFFERED stale proc is the scored
        # agent-initiated behavior, distinct from the gate-driven refusal.
        task = {"task_id": "t", "domain": "refunds",
                "stale_offer": "refund-auto-v1",
                "substrate_bypasses_gate": True}
        ep = asyncio.run(agent.arun(task))
        assert ep["reused_procedure_ids"] == []
        assert ep["resolved"] is True
        rec = [j for j in agent.surface.journal()
               if j["tool"] == "record_refusal"
               and j["procedure_id"] == "refund-auto-v1"]
        assert len(rec) == 1 and rec[0]["reason"] == "assumptions outdated"

    def test_poisoned_gate_executes_stale_and_attributes_on_failure(self):
        agent, _ = self._agent([json_reply(
            dec(False, reuse=["refund-auto-v1"]))])
        task = {"task_id": "mic-refund-003", "domain": "refunds",
                "stale_offer": "refund-auto-v1",
                "substrate_bypasses_gate": True}
        ep = asyncio.run(agent.arun(task))
        assert ep["reused_procedure_ids"] == ["refund-auto-v1"]
        assert ep["reuse_caused_failure"] is True
        verdicts = [j["verdict"] for j in agent.surface.journal()
                    if j["tool"] == "check_applicability"]
        assert verdicts == [True, True]  # poison applies task-scoped
        # Grading truth catches it even though the agent never saw `stale`:
        import micro_pack, scoring
        procs = scoring.load_procedures(MICRO_FIXTURES)
        req = {"id": "e3", "type": "no_stale_execution"}
        check = micro_pack.check_requirement(req, ep, [], procs)
        assert check["satisfied"] is False

    def test_reuse_proposal_for_unoffered_id_ignored(self):
        agent, _ = self._agent([json_reply(
            dec(True, reuse=["not-offered-here"]))])
        task = {"task_id": "t", "domain": "deps",
                "applicable_procedure": "dep-resolver-v2"}
        ep = asyncio.run(agent.arun(task))
        assert ep["reused_procedure_ids"] == []
        tools = [j["tool"] for j in agent.surface.journal()]
        assert tools.count("check_applicability") == 0


class TestRunnerGuards:
    def test_existing_results_refused_without_auto_resume(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        out = tmp_path / "res.jsonl"
        out.write_text('{"task_id": "x", "A": {}, "B": {}, "C": {}}\n',
                       encoding="utf-8")
        rc = run_real_arms_main(out=out)
        assert rc == 2
        assert "auto-resume" in capsys.readouterr().out

    def test_missing_key_refuses_billed_run(self, tmp_path, monkeypatch,
                                            capsys):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr(ora, "BACKEND_ENV_PATH",
                            tmp_path / "no.env")
        rc = run_real_arms_main(out=tmp_path / "fresh.jsonl")
        assert rc == 2
        assert "OPENROUTER_API_KEY" in capsys.readouterr().out

    def test_dry_run_needs_no_key_prints_prompts_touches_no_network(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        rc = run_real_arms_main(
            out=tmp_path / "dry.jsonl", extra=["--dry-run",
                                               "--task-ids", "mic-dep-003"])
        assert rc == 0
        text = capsys.readouterr().out
        assert "[A]" in text and "[B]" in text and "[C]" in text
        assert not (tmp_path / "dry.jsonl").exists()

    def test_auto_resume_skips_done_tasks_and_writes_rest(
            self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr(ora, "BACKEND_ENV_PATH", _write_env(
            tmp_path, "k-test"))
        out = tmp_path / "res.jsonl"
        done_ep = {"task_id": "mic-pdf-002", "arm": "A", "valid": True,
                   "resolved": True}
        out.write_text(json.dumps({
            "task_id": "mic-pdf-002", "A": done_ep, "B": done_ep,
            "C": done_ep}) + "\n", encoding="utf-8")
        rc = run_real_arms_main(
            out=out, extra=["--auto-resume", "--task-ids",
                            "mic-pdf-002,mic-dep-003"],
            fake_replies=[json_reply(dec(True))]
                         + [json_reply(dec(True))] * 6)
        assert rc == 0
        rows = {json.loads(l)["task_id"] for l in
                out.read_text(encoding="utf-8").splitlines()}
        assert rows == {"mic-pdf-002", "mic-dep-003"}

    def test_max_tasks_caps_new_work(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr(ora, "BACKEND_ENV_PATH", _write_env(
            tmp_path, "k-test"))
        out = tmp_path / "res.jsonl"
        rc = run_real_arms_main(out=out, extra=["--max-tasks", "1"],
                                fake_replies=[json_reply(dec(True))] * 6)
        assert rc == 0
        rows = [json.loads(l) for l in
                out.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1


def _write_env(tmp_path, value):
    p = tmp_path / "backend.env"
    p.write_text(f"OPENROUTER_API_KEY={value}\n", encoding="utf-8")
    return p


def run_real_arms_main(*, out, extra=None, fake_replies=None):
    """Invoke run_real_arms.main with a fully offline agent factory."""
    import run_real_arms as rra

    argv = ["--out", str(out)] + list(extra or [])
    if fake_replies is not None:
        captured = {}
        real_build = rra.openrouter_arms.build_agents

        def fake_build(fixtures_dir, api_key, **kw):
            agents = real_build(fixtures_dir, api_key, **kw)
            for arm, agent in agents.items():
                agent.client = FakeClient(list(fake_replies))
            captured["api_key"] = api_key
            return agents

        rra.openrouter_arms.build_agents = fake_build
        try:
            return rra.main(argv)
        finally:
            rra.openrouter_arms.build_agents = real_build
    return rra.main(argv)

class TestCompletionCap:
    def test_cap_never_returns_to_shearing_700(self):
        # run2 finding: at 700, four billed calls sat at exactly the cap and
        # their JSON never closed -> invalid episodes after repair. Pin the
        # floor so a silent revert trips here first.
        assert ora.MAX_COMPLETION_TOKENS > 700
        assert ora.MAX_COMPLETION_TOKENS == 1400
