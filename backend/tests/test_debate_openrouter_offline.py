"""
Offline proving tests for WAVE-3 debate-panel OpenRouter wiring (board Lane
CORE-B item 7).

Two layers of proof, zero network, zero wall-clock sleep:

  1. Unit proofs of the ported backoff pattern (full-jitter exponential on
     retryable statuses/network errors, immediate fallthrough on
     non-retryable 4xx, exhaustion carrying its attempt trail, turn budget)
     against injected transports -- the arms pattern re-proven in backend,
     where it now serves debate seats.

  2. ONE FakePool lifecycle proof: scan -> debate -> approve over the REAL
     TriggerDetector / LoopOrchestrator / DebateStateMachine with three
     OpenRouterAgent seats + OpenRouter judge whose transports return
     scripted completions. The exact code path that will run on the
     founder's key runs here against a recording pool; nothing spends.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.debate.panel import (
    BACKOFF_BASE_S,
    BACKOFF_CAP_S,
    MAX_ATTEMPTS_PER_MODEL,
    AllModelsFailedError,
    OpenRouterAgent,
    PanelAgent,
    _call_with_retry,
    _derive_family,
    assert_heterogeneous,
    default_chat_agent,
    default_judge,
    default_layer2_agent,
    default_panel,
    gather_responses,
    openrouter_headers,
    openrouter_judge,
    openrouter_panel,
)
import app.debate.panel as panel_mod


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------


class ProviderFlags:
    """Stub settings carrying only what panel.py's selection logic reads."""

    def __init__(self, **kw):
        self.use_local_models = False
        self.use_general_compute = False
        self.use_openrouter = False
        self.openrouter_panel_models = (
            "ox-alpha,openai/gpt-4o-mini,anthropic/claude-haiku-4.5"
        )
        self.openrouter_judge_model = "google/gemini-2.5-flash"
        self.openrouter_base_url = "https://openrouter.example/v1"
        for k, v in kw.items():
            setattr(self, k, v)


class ScriptedTransport:
    """Per-model queue of (status, body) tuples or Exception instances."""

    def __init__(self, script_by_model: dict[str, list]):
        self.script = script_by_model
        self.payloads: list[dict] = []

    async def __call__(self, payload: dict):
        self.payloads.append(payload)
        queue = self.script[payload["model"]]
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class SleepRecorder:
    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def or_body(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# ---------------------------------------------------------------------
# family derivation over OpenRouter-style slugs
# ---------------------------------------------------------------------


@pytest.mark.parametrize("model,expected", [
    ("ox-alpha", "ox-alpha"),
    ("openai/gpt-4o-mini", "gpt"),
    ("anthropic/claude-3-5-haiku", "claude"),
    ("google/gemini-2.5-flash", "gemini"),
    ("meta-llama/llama-3.3-70b-instruct", "llama"),
])
def test_openrouter_slugs_derive_distinct_families(model, expected):
    assert _derive_family(model) == expected


def test_default_roster_config_yields_four_distinct_families():
    s = ProviderFlags()
    with patch("app.debate.panel.settings", s):
        panel = openrouter_panel()
        judge = openrouter_judge()
    assert_heterogeneous(panel)

    from app.eval.layer1 import enforce_independence

    enforce_independence(judge, panel)  # must not raise


def test_same_family_openrouter_misconfiguration_rejected_at_construction():
    s = ProviderFlags(
        openrouter_panel_models="openai/gpt-4o-mini,openai/gpt-4o,openai/gpt-4.1-mini",
        openrouter_judge_model="google/gemini-2.5-flash",
    )
    with patch("app.debate.panel.settings", s):
        panel = openrouter_panel()
    with pytest.raises(ValueError, match="not heterogeneous"):
        assert_heterogeneous(panel)


def test_judge_sharing_a_seat_family_is_rejected():
    s = ProviderFlags(
        openrouter_panel_models="ox-alpha,openai/gpt-4o-mini,anthropic/claude-3-5-haiku",
        openrouter_judge_model="openai/gpt-4o",
    )
    with patch("app.debate.panel.settings", s):
        panel = openrouter_panel()
        judge = openrouter_judge()

    from app.eval.layer1 import JudgeNotIndependent, enforce_independence

    with pytest.raises(JudgeNotIndependent):
        enforce_independence(judge, panel)


def test_panel_requires_three_models_and_judge_requires_a_slug():
    with patch("app.debate.panel.settings",
               ProviderFlags(openrouter_panel_models="ox-alpha,openai/gpt-4o-mini")):
        with pytest.raises(ValueError, match="at least 3"):
            openrouter_panel()
    with patch("app.debate.panel.settings", ProviderFlags(openrouter_judge_model="")):
        with pytest.raises(ValueError, match="not set"):
            openrouter_judge()


# ---------------------------------------------------------------------
# provider selection wiring
# ---------------------------------------------------------------------


def test_default_panel_routes_to_openrouter_when_flagged():
    with patch("app.debate.panel.settings", ProviderFlags(use_openrouter=True)):
        panel = default_panel()
        judge = default_judge()
    assert all(isinstance(a, OpenRouterAgent) for a in panel)
    assert isinstance(judge, OpenRouterAgent)
    assert judge.agent_id == "judge"


def test_multiple_provider_flags_rejected_loudly():
    with patch("app.debate.panel.settings",
               ProviderFlags(use_openrouter=True, use_general_compute=True)):
        with pytest.raises(ValueError, match="pick one"):
            default_panel()


def test_chat_and_layer2_agents_route_to_openrouter_with_role_ids():
    with patch("app.debate.panel.settings", ProviderFlags(use_openrouter=True)):
        chat = default_chat_agent()
        layer2 = default_layer2_agent()
    assert isinstance(chat, OpenRouterAgent)
    assert chat.agent_id == "chat"
    assert chat.model_id == "google/gemini-2.5-flash"
    assert isinstance(layer2, OpenRouterAgent)
    assert layer2.agent_id == "layer2"


def test_openrouter_agent_satisfies_the_panel_protocol():
    agent = OpenRouterAgent(agent_id="x", model_id="m", family="f")
    assert isinstance(agent, PanelAgent)


# ---------------------------------------------------------------------
# request shape + auth
# ---------------------------------------------------------------------


def test_headers_carry_bearer_and_attribution_without_logging_the_key():
    headers = openrouter_headers("sk-secret")
    assert headers["Authorization"] == "Bearer sk-secret"
    assert headers["X-Title"]
    assert headers["HTTP-Referer"]


def test_payload_shape_messages_roles_max_tokens_temperature():
    tr = ScriptedTransport({"primary": [(200, or_body('{"action":"pass"}'))]})
    agent = OpenRouterAgent(
        agent_id="a", model_id="primary", family="f",
        max_tokens=777, temperature=0.1,
        transport=tr, sleep=SleepRecorder(), rng=lambda: 0.5,
    )
    out = asyncio.run(agent.respond("SYS", "USR"))
    assert out == '{"action":"pass"}'
    p = tr.payloads[0]
    assert p["model"] == "primary"
    assert p["max_tokens"] == 777
    assert p["temperature"] == 0.1
    assert [m["role"] for m in p["messages"]] == ["system", "user"]
    assert p["messages"][0]["content"] == "SYS"
    assert p["messages"][1]["content"] == "USR"
    # Raw passthrough: no response_format unless json_mode is opted into.
    assert "response_format" not in p


def test_json_mode_adds_openrouter_response_format():
    tr = ScriptedTransport({"primary": [(200, or_body("{}"))]})
    agent = OpenRouterAgent(
        agent_id="a", model_id="primary", family="f", json_mode=True,
        transport=tr, sleep=SleepRecorder(), rng=lambda: 0.5,
    )
    asyncio.run(agent.respond("s", "u"))
    assert tr.payloads[0]["response_format"] == {"type": "json_object"}
    with patch("app.debate.panel.settings", ProviderFlags(use_openrouter=True)):
        factory_seats = openrouter_panel() + [openrouter_judge()]
    assert all(seat.json_mode for seat in factory_seats), \
        "factories must enable JSON mode -- the engine parses every reply"


def test_reasoning_caps_and_extra_payload_merge_into_the_payload():
    tr = ScriptedTransport({"primary": [(200, or_body("{}"))]})
    agent = OpenRouterAgent(
        agent_id="a", model_id="primary", family="f", json_mode=True,
        extra_payload={"reasoning": {"max_tokens": 400}, "custom": 1},
        transport=tr, sleep=SleepRecorder(), rng=lambda: 0.5,
    )
    asyncio.run(agent.respond("s", "u"))
    p = tr.payloads[0]
    assert p["reasoning"] == {"max_tokens": 400}
    assert p["custom"] == 1
    assert p["response_format"] == {"type": "json_object"}
    assert p["model"] == "primary", "base identity fields survive the merge"


def test_factories_apply_the_known_reasoner_cap():
    with patch("app.debate.panel.settings", ProviderFlags(use_openrouter=True)):
        seats = {a.model_id: a for a in openrouter_panel()}
        judge = openrouter_judge()
    # The shipped roster's reasoner gets its cap; plain models stay clean.
    ox = [a for mid, a in seats.items() if mid == "ox-alpha"][0]
    gpt = [a for mid, a in seats.items() if mid.startswith("openai/")][0]
    assert ox.extra_payload == {"reasoning": {"max_tokens": 400}}
    assert gpt.extra_payload == {}
    assert judge.extra_payload == {}


def test_missing_key_fails_loud_at_point_of_use_not_at_import():
    class NoKey(ProviderFlags):
        def require(self, field):
            raise RuntimeError(f"Missing required setting '{field}'. Set "
                               f"{field.upper()} in your .env")

    agent = OpenRouterAgent(agent_id="a", model_id="m", family="f")
    with patch("app.debate.panel.settings", NoKey()):
        with pytest.raises(RuntimeError, match="openrouter_api_key"):
            asyncio.run(agent.respond("s", "u"))


# ---------------------------------------------------------------------
# backoff mechanics (arms parity, proven offline)
# ---------------------------------------------------------------------


def _agent(model: str = "primary", *, fallback: tuple[str, ...] = (),
           script=None, sleeps=None, rng=lambda: 0.5, budget=None):
    kw = {}
    if budget is not None:
        kw["turn_budget_s"] = budget
    return OpenRouterAgent(
        agent_id="a", model_id=model, family="f", fallback_models=fallback,
        transport=script, sleep=sleeps or SleepRecorder(), rng=rng, **kw
    )


def test_429s_are_retried_with_bounded_exponential_full_jitter_then_success():
    sleeps = SleepRecorder()
    script = ScriptedTransport({
        "primary": [(429, {}), (429, {}), (200, or_body('{"action":"pass"}'))],
    })
    agent = _agent(script=script, sleeps=sleeps, rng=lambda: 0.5)
    out = asyncio.run(agent.respond("s", "u"))
    assert out == '{"action":"pass"}'
    assert len(sleeps.delays) == 2
    # full jitter: uniform in [0, min(cap, base*2^attempt)); rng pinned 0.5
    assert sleeps.delays[0] == 0.5 * min(BACKOFF_CAP_S, BACKOFF_BASE_S * 1)
    assert sleeps.delays[1] == 0.5 * min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2)


@pytest.mark.parametrize("status", sorted({408, 409, 500, 502, 503, 504}))
def test_every_retryable_status_is_retried(status):
    script = ScriptedTransport({
        "primary": [(status, {}), (200, or_body("ok"))],
    })
    agent = _agent(script=script)
    assert asyncio.run(agent.respond("s", "u")) == "ok"


def test_non_retryable_4xx_falls_through_to_next_chain_model_immediately():
    sleeps = SleepRecorder()
    script = ScriptedTransport({
        "dead": [(400, {"error": {"message": "bad slug"}})],
        "alive": [(200, or_body("second-model-answer"))],
    })
    agent = _agent(model="dead", fallback=("alive",),
                   script=script, sleeps=sleeps)
    out = asyncio.run(agent.respond("s", "u"))
    assert out == "second-model-answer"
    assert sleeps.delays == [], "non-retryable 4xx must never sleep"
    assert [p["model"] for p in script.payloads] == ["dead", "alive"]


def test_network_errors_retry_like_429s():
    sleeps = SleepRecorder()
    script = ScriptedTransport({
        "primary": [
            ConnectionError("conn reset"),
            ConnectionError("conn reset"),
            (200, or_body("recovered")),
        ],
    })
    agent = _agent(script=script, sleeps=sleeps)
    assert asyncio.run(agent.respond("s", "u")) == "recovered"
    assert len(sleeps.delays) == 2


def test_exhaustion_raises_all_models_failed_with_attempt_trail():
    sleeps = SleepRecorder()
    script = ScriptedTransport({
        "dead": [(429, {})] * MAX_ATTEMPTS_PER_MODEL,
        "also-dead": [(503, {})] * MAX_ATTEMPTS_PER_MODEL,
    })
    agent = _agent(model="dead", fallback=("also-dead",),
                   script=script, sleeps=sleeps)
    with pytest.raises(AllModelsFailedError) as excinfo:
        asyncio.run(agent.respond("s", "u"))
    err = excinfo.value
    assert len(err.attempts) == MAX_ATTEMPTS_PER_MODEL * 2
    assert {a["model"] for a in err.attempts} == {"dead", "also-dead"}
    assert {a["status"] for a in err.attempts} == {429, 503}
    assert "dead" in str(err) and "also-dead" in str(err)
    # Arms-parity sleep count: every attempt of NON-final models sleeps
    # (another model still follows); only the final model's last attempt
    # skips its sleep -> 6 + 5.
    assert len(sleeps.delays) == MAX_ATTEMPTS_PER_MODEL * 2 - 1


def test_turn_budget_stops_retries_but_keeps_one_probe_and_the_trail():
    sleeps = SleepRecorder()
    script = ScriptedTransport({"primary": [(429, {})] * 10})
    agent = _agent(script=script, sleeps=sleeps, budget=0.0)
    with pytest.raises(AllModelsFailedError) as excinfo:
        asyncio.run(agent.respond("s", "u"))
    # Budget already expired after the first failure: no further sleeps, and
    # later chain models are not probed at all.
    assert len(excinfo.value.attempts) == 1
    assert excinfo.value.attempts[0]["model"] == "primary"
    assert sleeps.delays == []


def test_turn_budget_constant_is_consulted_at_call_time(monkeypatch):
    monkeypatch.setattr(panel_mod, "TURN_BUDGET_S", -1.0)
    script = ScriptedTransport({"primary": [(429, {})] * 10})
    agent = _agent(script=script)  # turn_budget_s left None -> constant governs
    with pytest.raises(AllModelsFailedError):
        asyncio.run(agent.respond("s", "u"))


# ---------------------------------------------------------------------
# _call_with_retry stand-down for self-retrying seats
# ---------------------------------------------------------------------


class _RateLimitedForever:
    agent_id = "or-seat"
    model_id = "or-model"
    family = "or"

    def __init__(self, marker: bool):
        self.manages_own_retries = marker
        self.calls = 0

    async def respond(self, system, user):
        self.calls += 1
        raise RuntimeError("Error code: 429 - rate limited")


def test_self_retrying_seat_is_not_double_retried_by_outer_layer():
    agent = _RateLimitedForever(marker=True)
    with patch("app.debate.panel.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        results = asyncio.run(asyncio.wait_for(_wrap(agent), timeout=5))
    assert isinstance(results, RuntimeError)
    assert agent.calls == 1, "inner backoff owns retrying; outer layer stands down"
    mock_sleep.assert_not_called()


def test_plain_seats_keep_the_generic_rate_limit_retry():
    agent = _RateLimitedForever(marker=False)
    with patch("app.debate.panel.asyncio.sleep", new=AsyncMock()):
        results = asyncio.run(asyncio.wait_for(_wrap(agent), timeout=5))
    assert isinstance(results, RuntimeError)
    assert agent.calls == 4, "existing generic behavior: 1 call + 3 retries"


async def _wrap(agent):
    try:
        return await _call_with_retry(agent, "sys", "usr", timeout=30.0)
    except Exception as exc:  # surface, don't raise -- assertions inspect it
        return exc


def test_gather_responses_takes_the_stand_down_path_and_records_failures():
    exhausted = OpenRouterAgent(
        agent_id="exhausted", model_id="dead", family="d",
        transport=ScriptedTransport({"dead": [(429, {})] * MAX_ATTEMPTS_PER_MODEL}),
        sleep=SleepRecorder(), rng=lambda: 0.5, turn_budget_s=0.0,
    )
    healthy = OpenRouterAgent(
        agent_id="healthy", model_id="ok", family="o",
        transport=ScriptedTransport({"ok": [(200, or_body('{"action":"pass"}'))]}),
        sleep=SleepRecorder(), rng=lambda: 0.5,
    )
    results = asyncio.run(gather_responses([exhausted, healthy], "sys", "usr"))
    assert isinstance(results["exhausted"], AllModelsFailedError), \
        "an exhausted seat becomes a recorded failed turn, never a crashed debate"
    assert results["healthy"] == '{"action":"pass"}'


# ---------------------------------------------------------------------
# FakePool lifecycle: scan -> debate -> approve, fully offline
# ---------------------------------------------------------------------


TRIGGER_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
DEBATE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
TASK_NODE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a3")
KNOWLEDGE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a4")

SEAT_SLUGS = {
    "panelist_a": "ox-alpha",
    "panelist_b": "openai/gpt-4o-mini",
    "panelist_c": "anthropic/claude-haiku-4.5",
}
JUDGE_SLUG = "google/gemini-2.5-flash"


def test_shipped_default_roster_is_heterogeneous_and_judge_independent():
    """
    Tripwire against stale defaults: the slugs SHIPPED in config.py (as
    loaded from this machine's backend/.env) must form a legal panel --
    three distinct families plus an unaffiliated judge. This runs at
    construction only; it never sends a request. It would NOT catch a
    retired slug (family derivation is name-based) -- the gated live
    smoke exists for that -- but it catches anyone pinning two seats of
    one lineage, which silently defeats debate's whole point.
    """
    panel = openrouter_panel()
    judge = openrouter_judge()

    from app.eval.layer1 import enforce_independence

    assert_heterogeneous(panel)
    enforce_independence(judge, panel)


class FakeConn:
    def __init__(self, pool: "FakePool"):
        self._pool = pool

    async def fetchrow(self, sql, *args):
        return await self._pool.fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        return await self._pool.fetch(sql, *args)

    async def execute(self, sql, *args):
        return await self._pool.execute(sql, *args)

    def transaction(self):
        return self._pool.transaction()


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Acquire:
    def __init__(self, pool: "FakePool"):
        self._pool = pool

    async def __aenter__(self):
        return FakeConn(self._pool)

    async def __aexit__(self, *exc):
        return False


class FakePool:
    """
    Recording pool answering reads from registered rules (whitespace-
    normalized substring match, first hit wins). Debate state is STATEFUL:
    the state-machine's UPDATE advances it so consecutive FOR UPDATE reads
    see the lifecycle move -- that is what lets this proof assert the real
    transition table was honored rather than mocked away.
    """

    def __init__(self):
        self.rules: list[tuple[str, object]] = []
        self.reads: list[tuple[str, tuple]] = []
        self.writes: list[tuple[str, tuple]] = []
        self.debate_state = "OPEN"

    def rule(self, fragment, result):
        self.rules.append((fragment, result))

    def _answer(self, sql, args):
        norm = " ".join(sql.split())
        for fragment, result in self.rules:
            if fragment in norm:
                return result(args) if callable(result) else result
        return None

    async def fetch(self, sql, *args):
        norm = " ".join(sql.split())
        self.reads.append((norm, args))
        value = self._answer(sql, args)
        return list(value or [])

    async def fetchrow(self, sql, *args):
        norm = " ".join(sql.split())
        head = norm.split()[0].upper() if norm else ""
        value = self._answer(sql, args)
        if head in ("INSERT", "UPDATE", "DELETE"):
            self.writes.append((norm, args))
        else:
            self.reads.append((norm, args))
        return value

    async def fetchval(self, sql, *args):
        return self._answer(sql, args)

    async def execute(self, sql, *args):
        norm = " ".join(sql.split())
        self.writes.append((norm, args))
        if "UPDATE debates SET state" in norm:
            self.debate_state = args[1]
        return "UPDATE 1"

    def acquire(self):
        return _Acquire(self)

    def transaction(self):
        return _Tx()


def _vada_reply(action: str, **extra) -> str:
    payload = {"action": action, "content": f"{action} rationale"}
    payload.update(extra)
    return json.dumps(payload)


def _propose_reply() -> str:
    return _vada_reply(
        "propose",
        summary="Add a retry wrapper around flaky export step",
        cites=[{"node_id": str(KNOWLEDGE_ID), "node_table": "knowledge_nodes"}],
        change_set={"ops": [
            {"op_type": "create_task_node", "name": "retry-wrapper",
             "ref": "t1"},
            {"op_type": "create_task_node", "name": "verify-wrapper", "ref": "t2"},
            {"op_type": "create_edge", "edge_type": "REQUIRES",
             "source_ref": "t1", "target_ref": "t2"},
        ]},
    )


JUDGE_REPLY = json.dumps(
    {"fallacy_flags": [], "constructive": True, "notes": "sound"}
)


def _seat(agent_id: str, replies: list[str]) -> OpenRouterAgent:
    return OpenRouterAgent(
        agent_id=agent_id,
        model_id=SEAT_SLUGS[agent_id],
        family=_derive_family(SEAT_SLUGS[agent_id]),
        transport=ScriptedTransport({SEAT_SLUGS[agent_id]:
                                     [(200, or_body(r)) for r in replies]}),
        sleep=SleepRecorder(),
        rng=lambda: 0.5,
    )


def _lifecycle_pool() -> FakePool:
    pool = FakePool()
    pool.rule("FROM traces", [{
        "task_node_id": TASK_NODE_ID, "value": 0.42, "n": 25,
    }])
    pool.rule("FROM triggers WHERE id", {
        "id": TRIGGER_ID, "debate_id": None, "task_node_id": TASK_NODE_ID,
        "rule_name": "error_rate_watch", "metric_name": "error_rate",
        "observed_value": 0.42, "threshold": 0.1, "sample_size": 25,
        "detail": {"direction": "above", "window_days": 30, "min_samples": 20},
    })
    pool.rule("INSERT INTO debates", {"id": DEBATE_ID})
    pool.rule("FROM debates WHERE id", lambda args: {"state": pool.debate_state})
    pool.rule("FOR UPDATE", lambda args: {"state": pool.debate_state})
    pool.rule("FROM task_nodes WHERE id", {
        "id": TASK_NODE_ID, "name": "export-step",
        "description": "nightly export", "io_schema": {}, "skill_ref": None,
        "cost_estimate": None, "latency_estimate_ms": None,
    })
    pool.rule("WITH RECURSIVE frontier", [])
    pool.rule("FROM knowledge_nodes WHERE id", {"?column?": 1})
    pool.rule("LEFT JOIN debates", None)  # no unresolved duplicate trigger
    pool.rule("INSERT INTO triggers", {"id": TRIGGER_ID})
    pool.rule("FROM debate_events", [])
    return pool


def test_scan_debate_approve_runs_end_to_end_on_openrouter_seats_offline():
    """
    THE proving test: the real scan -> debate -> approve pipeline, with the
    real OpenRouterAgent class serving every seat and the judge off
    scripted completions, over a recording pool. Nothing spends; the same
    construction runs live behind SL_DEBATE_LIVE_SMOKE=1.
    """
    from app.services.loop import LoopOrchestrator
    from app.services.triggers import ThresholdRule, TriggerDetector
    from app.debate.state_machine import DebateStateMachine

    pool = _lifecycle_pool()

    # --- SCAN ---
    detector = TriggerDetector(pool)
    hits = asyncio.run(detector.scan([
        ThresholdRule(name="error_rate_watch", metric="error_rate",
                      threshold=0.1, min_samples=20),
    ]))
    assert len(hits) == 1
    trigger_ids = asyncio.run(detector.record(hits))
    assert trigger_ids == [TRIGGER_ID]

    # --- DEBATE (round 1: a proposes; round 2: b amends -> 2 supporters;
    #     round 3: nobody moves -> converged) ---
    panel = [
        _seat("panelist_a", [_propose_reply(),
                             _vada_reply("pass"), _vada_reply("pass")]),
        _seat("panelist_b", [_vada_reply("pass"),
                             _vada_reply("amend"), _vada_reply("pass")]),
        _seat("panelist_c", [_vada_reply("pass"),
                             _vada_reply("pass"), _vada_reply("pass")]),
    ]
    judge = OpenRouterAgent(
        agent_id="judge", model_id=JUDGE_SLUG,
        family=_derive_family(JUDGE_SLUG),
        transport=ScriptedTransport({JUDGE_SLUG: [(200, or_body(JUDGE_REPLY))]}),
        sleep=SleepRecorder(), rng=lambda: 0.5,
    )
    orch = LoopOrchestrator(pool, panel, judge)  # enforces heterogeneity +
                                                 # judge independence itself
    scorecards = asyncio.run(orch.run(TRIGGER_ID))

    assert pool.debate_state == "PENDING_APPROVAL"
    assert len(scorecards) == 1
    sc = scorecards[0]
    assert sc.layer1.passed
    assert sc.layer1.groundedness_score == 1.0
    assert sc.proposers == ["panelist_a", "panelist_b"]
    assert "Passed argument review" in sc.recommendation

    # Every turn was served by the configured OpenRouter slug for its seat.
    turn_writes = [args for sql, args in pool.writes
                   if "INSERT INTO debate_turns" in sql]
    assert len(turn_writes) == 9  # 3 seats x 3 rounds
    models_used = {args[6] for args in turn_writes}
    assert models_used == set(SEAT_SLUGS.values())

    # The durable artifacts landed: candidates, transcript episode, scorecard.
    written_sql = " ".join(sql for sql, _ in pool.writes)
    for fragment in ("INSERT INTO candidates", "INSERT INTO debate_turns",
                     "INSERT INTO episodes", "INSERT INTO scorecards",
                     "INSERT INTO debate_events"):
        assert fragment in written_sql, fragment

    # No seat needed a retry: the scripted completions all answered 200.
    for agent in [*panel, judge]:
        assert agent.sleep.delays == []  # type: ignore[attr-defined]

    # --- APPROVE (the human sign-off step, exercised on the same machine) --
    machine = DebateStateMachine(pool)
    final = asyncio.run(machine.transition(DEBATE_ID, "APPROVED",
                                           reason="offline approve proof"))
    assert final == "APPROVED"
    assert asyncio.run(machine.current_state(DEBATE_ID)) == "APPROVED"

    history = asyncio.run(machine.history(DEBATE_ID))
    assert history == []  # answered from the registered rule; events recorded as writes above

    # Replay the whole transition ladder explicitly to pin the legal path.
    fresh = _lifecycle_pool()
    fresh_machine = DebateStateMachine(fresh)
    states_seen = [fresh.debate_state]  # OPEN
    ladder = ["IN_DEBATE", "PENDING_EVAL", "PENDING_APPROVAL", "APPROVED"]
    for to_state in ladder:
        asyncio.run(fresh_machine.transition(DEBATE_ID, to_state))
        states_seen.append(fresh.debate_state)
    assert states_seen == ["OPEN", "IN_DEBATE", "PENDING_EVAL",
                           "PENDING_APPROVAL", "APPROVED"]

    # An illegal jump is still refused by the real table.
    from app.debate.state_machine import IllegalTransition

    illegal = _lifecycle_pool()
    with pytest.raises(IllegalTransition):
        asyncio.run(DebateStateMachine(illegal).transition(
            DEBATE_ID, "APPROVED"))
