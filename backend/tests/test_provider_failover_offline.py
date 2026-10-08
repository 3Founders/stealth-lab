"""
Failover when a model's endpoint is not working (providers/health.py, providers/service.py call_unit, the
call_model / recommend_models / find_ways plan surfaces). Offline: adapters are fakes, the clock is injected.

What must hold:
  * an endpoint that is DOWN (transport error, timeout, 408/425/429/5xx, 401/402/403) is skipped and the next
    connection offering the SAME unit answers; a bad request (400/404/422) is not retried anywhere;
  * a down endpoint stays out of rotation for its cool-down, then gets one call again; success closes it;
  * a connection refused by policy is skipped so another approved one can serve the call;
  * call_model moves to another MODEL only with model="auto" (the plan's next rung) or `fallback_models`;
  * the reply says which model answered, and when it is not the plan's default rung, report_result is told so;
  * plans and recommendations mark a unit with no working endpoint, without changing the ladder.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import app.mcp_server.server as srv
import app.providers as providers
from app.providers import adapters, health, registry, secrets, service
from app.providers.types import CallRequest, CallResult, Connection, ProviderCallDenied, ProviderCallFailed, UnitSpec
from app.routing import plan
from app.services.access import AccessScope

SCOPE = AccessScope.for_org_member("u1", ["org-a"])


def run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv(registry.FILE_ENV, raising=False)
    monkeypatch.setattr(registry, "_STORES", {})
    monkeypatch.setattr(registry, "_file_cache", (None, []))
    monkeypatch.setattr(adapters, "ADAPTERS", dict(adapters.ADAPTERS))
    clock = Clock()
    fresh = health.Health(clock=clock)
    monkeypatch.setattr(health, "HEALTH", fresh)
    monkeypatch.setattr(service, "HEALTH", fresh)
    monkeypatch.setenv("TEST_KEY", "sk-test")

    async def ok_endpoint(url, *, allow_http_loopback=False):
        return None
    monkeypatch.setattr(service, "check_endpoint", ok_endpoint)
    saved = dict(secrets._RESOLVERS)
    yield clock
    secrets._RESOLVERS.clear()
    secrets._RESOLVERS.update(saved)


class ScriptedAdapter:
    """Per connection id: a list of outcomes, consumed in order (an Exception is raised, "ok" answers)."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    async def call(self, conn, spec, request, secret):
        self.calls.append((conn.connection_id, spec.unit))
        outcome = self.script.get(conn.connection_id, ["ok"]).pop(0) if self.script.get(conn.connection_id) else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return CallResult(unit=spec.unit, connection_id=conn.connection_id, text=f"answer from {conn.connection_id}")


def _conn(cid, units=("deepseek-v3.2",), **kw):
    base = dict(connection_id=cid, kind="scripted", base_url=f"https://{cid}.example.com/v1", provider=cid,
                units=tuple(UnitSpec(model=u) for u in units), credential_ref="env:TEST_KEY",
                allowed_data_classes=("USER_PRIVATE",))
    base.update(kw)
    return Connection(**base)


def _setup(script, *conns):
    adapter = ScriptedAdapter(script)
    adapters.register_adapter("scripted", adapter)
    registry.register_connection_store("t", registry.StaticConnectionStore(conns))
    return adapter


def _call(unit="deepseek-v3.2|direct"):
    return run(service.call_unit(None, SCOPE, unit, CallRequest(prompt="hi")))


# ---------------------------------------------------------------- the breaker

@pytest.mark.parametrize("status,transport,outage", [
    (None, True, True), (408, False, True), (429, False, True), (500, False, True), (503, False, True),
    (401, False, True), (402, False, True), (403, False, True),
    (400, False, False), (404, False, False), (422, False, False), (None, False, False)])
def test_what_counts_as_an_outage(status, transport, outage):
    assert health.is_outage(status, transport) is outage
    assert ProviderCallFailed("x", status=status, transport=transport).outage is outage


def test_cool_down_grows_then_one_call_is_let_through_and_success_closes_it(_isolate):
    clock, h = _isolate, service.HEALTH
    h.record_failure("c1", "m|direct", status=503, error="HTTP 503")
    assert not h.available("c1", "m|direct") and h.available("c1", "other|direct")   # per unit for a 503
    clock.t += 31
    assert h.available("c1", "m|direct")                                               # half-open after 30 s
    h.record_failure("c1", "m|direct", status=503)
    clock.t += 31
    assert not h.available("c1", "m|direct")                                           # second failure: 60 s
    clock.t += 30
    assert h.available("c1", "m|direct")
    h.record_success("c1", "m|direct")
    h.record_failure("c1", "m|direct", status=503)
    clock.t += 31
    assert h.available("c1", "m|direct"), "a success resets the back-off"


def test_transport_and_account_failures_take_the_whole_endpoint_out(_isolate):
    h = service.HEALTH
    h.record_failure("c1", "m|direct", transport=True)
    assert not h.available("c1", "anything|direct")
    h.record_failure("c2", "m|direct", status=402)
    _isolate.t += 600
    assert not h.available("c2", "m|direct"), "an account refusal waits 15 minutes"
    h.record_failure("c3", "m|direct", status=400)
    assert h.available("c3", "m|direct"), "a bad request is not an outage"


# ---------------------------------------------------------------- call_unit: same model, other endpoint

def test_a_down_endpoint_fails_over_to_the_next_connection_for_the_same_model():
    adapter = _setup({"a": [ProviderCallFailed("a: HTTP 503", status=503)]}, _conn("a"), _conn("b"))
    out = _call()
    assert out.text == "answer from b" and out.connection_id == "b"
    assert out.extra["failover"][0]["connection_id"] == "a" and "503" in out.extra["failover"][0]["error"]
    assert adapter.calls == [("a", "deepseek-v3.2|direct"), ("b", "deepseek-v3.2|direct")]
    # the next call goes straight to b while a cools down
    _call()
    assert adapter.calls[-1] == ("b", "deepseek-v3.2|direct") and len(adapter.calls) == 3


def test_a_bad_request_is_not_retried_on_another_endpoint():
    adapter = _setup({"a": [ProviderCallFailed("a: HTTP 400: bad", status=400)]}, _conn("a"), _conn("b"))
    with pytest.raises(ProviderCallFailed, match="HTTP 400"):
        _call()
    assert adapter.calls == [("a", "deepseek-v3.2|direct")]
    assert service.HEALTH.available("a", "deepseek-v3.2|direct")


def test_every_endpoint_down_is_one_failure_listing_each():
    _setup({"a": [ProviderCallFailed("a: timeout", transport=True)], "b": [ProviderCallFailed("b: HTTP 502", status=502)]},
           _conn("a"), _conn("b"))
    with pytest.raises(ProviderCallFailed) as err:
        _call()
    assert err.value.outage and [a["connection_id"] for a in err.value.attempts] == ["a", "b"]
    assert "every endpoint offering 'deepseek-v3.2|direct' failed" in str(err.value)


def test_when_all_endpoints_are_cooling_down_the_oldest_failure_still_gets_the_call(_isolate):
    adapter = _setup({}, _conn("a"), _conn("b"))
    service.HEALTH.record_failure("a", "deepseek-v3.2|direct", status=503)
    _isolate.t += 1
    service.HEALTH.record_failure("b", "deepseek-v3.2|direct", status=503)
    assert _call().connection_id == "a" and adapter.calls == [("a", "deepseek-v3.2|direct")]


def test_a_connection_refused_by_policy_is_skipped_for_one_that_is_approved():
    adapter = _setup({}, _conn("a", allowed_data_classes=("PUBLIC_SOURCE",)), _conn("b"))
    assert _call().connection_id == "b" and adapter.calls == [("b", "deepseek-v3.2|direct")]
    _setup({}, _conn("a", allowed_data_classes=("PUBLIC_SOURCE",)))
    with pytest.raises(ProviderCallDenied, match="not approved for USER_PRIVATE"):
        _call()


def test_unit_availability_reports_down_and_unknown_units(_isolate):
    _setup({}, _conn("a", units=("m1", "m2")), _conn("b", units=("m2",)))
    service.HEALTH.record_failure("a", "m1|direct", status=503)
    service.HEALTH.record_failure("a", "m2|direct", status=503)
    got = run(service.unit_availability(SCOPE, ["m1|direct", "m2|direct", "local|claude-code"]))
    assert got["m1|direct"]["status"] == "unavailable" and got["m1|direct"]["retry_in_s"] == 30.0
    assert got["m2|direct"] == {"status": "available"}                 # b still serves it
    assert got["local|claude-code"] == {"status": "not_served"}         # the caller's own: never marked down


# ---------------------------------------------------------------- call_model: other models

class _Ctx:
    class request_context:
        lifespan_context = {"pool": None}


def _tool(monkeypatch, outcomes):
    """call_unit replaced: per unit, raise or answer."""
    seen = []

    async def fake_call(pool, scope, unit, request, **kw):
        seen.append(unit)
        out = outcomes.get(unit, "ok")
        if isinstance(out, Exception):
            raise out
        return CallResult(unit=unit, connection_id="c", text=f"from {unit}", tokens_in=3, tokens_out=2)
    monkeypatch.setattr(providers, "call_unit", fake_call)
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: SCOPE)
    return seen


DOWN = ProviderCallFailed("every endpoint offering it failed", transport=True)


def test_a_named_model_is_never_swapped_without_fallback_models(monkeypatch):
    seen = _tool(monkeypatch, {"big|direct": DOWN})
    assert run(srv.call_model(_Ctx(), "p", model="big")).startswith("FAILED: every endpoint")
    assert seen == ["big|direct"]


def test_fallback_models_are_tried_in_order_when_the_model_is_down(monkeypatch):
    seen = _tool(monkeypatch, {"big|direct": DOWN, "mid|direct": DOWN})
    body = json.loads(run(srv.call_model(_Ctx(), "p", model="big", fallback_models=["mid", "small|coder"])))
    assert seen == ["big|direct", "mid|direct", "small|coder"]
    assert body["unit"] == "small|coder" and body["requested_unit"] == "big|direct"
    assert [f["unit"] for f in body["fell_back"]] == ["big|direct", "mid|direct"]


def test_a_bad_request_does_not_move_to_a_fallback_model(monkeypatch):
    seen = _tool(monkeypatch, {"big|direct": ProviderCallFailed("HTTP 400", status=400)})
    assert run(srv.call_model(_Ctx(), "p", model="big", fallback_models=["mid"])).startswith("FAILED: HTTP 400")
    assert seen == ["big|direct"]


def test_auto_skips_a_down_rung_and_tells_report_result_which_model_ran(monkeypatch):
    seen = _tool(monkeypatch, {"cheap|direct": DOWN})

    class Instance:
        decision = {"ladder": ["cheap|direct", "mid|direct", "big|direct"]}

    async def load(pool, scope, key):
        return Instance()
    monkeypatch.setattr(plan, "load_instance", load)
    monkeypatch.setattr(plan, "_default_unit", lambda inst: "cheap|direct")
    body = json.loads(run(srv.call_model(_Ctx(), "p", instance_key="g.k")))
    assert seen == ["cheap|direct", "mid|direct"] and body["unit"] == "mid|direct"
    report = body["next"]["with"]
    assert (report["model"], report["scaffold"]) == ("mid", "direct"), "not the default rung: name what ran"
    assert body["fell_back"][0]["unit"] == "cheap|direct"


def test_auto_on_the_default_rung_needs_no_model_in_the_report(monkeypatch):
    _tool(monkeypatch, {})

    class Instance:
        decision = {"ladder": ["cheap|direct", "mid|direct"]}

    async def load(pool, scope, key):
        return Instance()
    monkeypatch.setattr(plan, "load_instance", load)
    monkeypatch.setattr(plan, "_default_unit", lambda inst: "cheap|direct")
    body = json.loads(run(srv.call_model(_Ctx(), "p", instance_key="g.k")))
    assert body["unit"] == "cheap|direct" and "model" not in body["next"]["with"] and "fell_back" not in body


# ---------------------------------------------------------------- plans: mark, never reorder

def test_a_plan_marks_a_down_rung_and_names_what_to_run_first(monkeypatch):
    async def avail(scope, units):
        return {u: ({"status": "unavailable", "retry_in_s": 12.0} if u == "cheap|direct" else {"status": "available"})
                for u in units}
    monkeypatch.setattr(providers, "unit_availability", avail)
    plan_body = {"status": "ok", "ladder": ["cheap|direct", "mid|direct"]}
    run(srv._mark_availability(plan_body, SCOPE))
    assert plan_body["ladder"] == ["cheap|direct", "mid|direct"], "the ladder is a quality decision: unchanged"
    assert plan_body["next_available"] == "mid|direct"
    assert plan_body["availability"]["cheap|direct"]["status"] == "unavailable"
    assert "Do not report a skipped model" in plan_body["availability_note"]


def test_a_plan_with_everything_working_is_left_untouched(monkeypatch):
    async def avail(scope, units):
        return {u: {"status": "available"} for u in units}
    monkeypatch.setattr(providers, "unit_availability", avail)
    body = {"status": "ok", "recommended": {"ladder": ["a|direct"]}}
    run(srv._mark_availability(body, SCOPE))
    assert body == {"status": "ok", "recommended": {"ladder": ["a|direct"]}}


# ---------------------------------------------------------------- several keys per endpoint

class KeyAdapter:
    """Answers per secret: a secret in `refuse` gets that status, others answer."""

    def __init__(self, refuse):
        self.refuse, self.secrets = refuse, []

    async def call(self, conn, spec, request, secret):
        self.secrets.append(secret)
        if secret in self.refuse:
            raise ProviderCallFailed(f"{conn.connection_id}: HTTP {self.refuse[secret]}", status=self.refuse[secret])
        return CallResult(unit=spec.unit, connection_id=conn.connection_id, text=f"ok via {secret}", latency_ms=50)


def _keyed(monkeypatch, refuse, refs=("env:K1", "env:K2", "env:K3")):
    for i, ref in enumerate(refs):
        monkeypatch.setenv(ref.split(":")[1], f"secret-{i + 1}")
    adapter = KeyAdapter(refuse)
    adapters.register_adapter("keyed", adapter)
    registry.register_connection_store("t", registry.StaticConnectionStore(
        [_conn("a", kind="keyed", credential_ref=None, credential_refs=tuple(refs))]))
    return adapter


def test_a_rate_limited_key_hands_over_to_the_next_key_on_the_same_endpoint(monkeypatch):
    adapter = _keyed(monkeypatch, {"secret-1": 429})
    assert _call().text == "ok via secret-2" and adapter.secrets == ["secret-1", "secret-2"]
    _call()                                                     # the rested key is skipped next time
    assert adapter.secrets[-1] == "secret-2" and adapter.secrets.count("secret-1") == 1


def test_a_refused_key_is_rested_but_a_server_error_is_the_endpoints(monkeypatch):
    adapter = _keyed(monkeypatch, {"secret-1": 402, "secret-2": 503})
    with pytest.raises(ProviderCallFailed, match="HTTP 503"):  # 503 = the endpoint, not the key: no third key
        _call()
    assert adapter.secrets == ["secret-1", "secret-2"]


def test_a_missing_key_is_skipped_when_others_are_set(monkeypatch):
    adapter = _keyed(monkeypatch, {})
    monkeypatch.delenv("K1")
    assert _call().text == "ok via secret-2" and adapter.secrets == ["secret-2"]


def test_connections_file_accepts_several_keys_a_timeout_and_a_slow_threshold():
    conn = registry.connection_from_dict({
        "connection_id": "x", "kind": "openai_compatible", "base_url": "https://api.example.com/v1",
        "credential_refs": ["env:A", "env:B"], "timeout_s": 30, "slow_ms": 8000,
        "units": [{"model": "m"}]})
    assert conn.keys == ("env:A", "env:B") and conn.timeout_s == 30.0 and conn.slow_ms == 8000
    with pytest.raises(ValueError, match="not both"):
        registry.connection_from_dict({"connection_id": "x", "kind": "openai_compatible",
                                       "base_url": "https://api.example.com/v1", "credential_ref": "env:A",
                                       "credential_refs": ["env:B"], "units": [{"model": "m"}]})
    with pytest.raises(ValueError, match="timeout_s"):
        registry.connection_from_dict({"connection_id": "x", "kind": "openai_compatible",
                                       "base_url": "https://api.example.com/v1", "timeout_s": 0,
                                       "units": [{"model": "m"}]})


# ---------------------------------------------------------------- latency

class SlowAdapter:
    def __init__(self, delays):
        self.delays, self.calls = delays, []

    async def call(self, conn, spec, request, secret):
        self.calls.append(conn.connection_id)
        await asyncio.sleep(self.delays.get(conn.connection_id, 0))
        return CallResult(unit=spec.unit, connection_id=conn.connection_id, text="ok",
                          latency_ms=int(self.delays.get(conn.connection_id, 0) * 1000))


def test_a_slow_endpoint_is_tried_after_a_fast_one():
    adapter = SlowAdapter({})
    adapters.register_adapter("slow", adapter)
    registry.register_connection_store("t", registry.StaticConnectionStore(
        [_conn("a", kind="slow"), _conn("b", kind="slow")]))
    service.HEALTH.record_latency("a", "deepseek-v3.2|direct", 9000)      # a usually takes 9 s
    service.HEALTH.record_latency("b", "deepseek-v3.2|direct", 800)
    assert _call().connection_id == "b"


def test_a_latency_budget_moves_on_without_resting_the_endpoint():
    adapter = SlowAdapter({"a": 0.5})
    adapters.register_adapter("slow", adapter)
    registry.register_connection_store("t", registry.StaticConnectionStore(
        [_conn("a", kind="slow"), _conn("b", kind="slow")]))
    out = run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi"), max_latency_ms=100))
    assert out.connection_id == "b" and "max_latency_ms" in out.extra["failover"][0]["error"]
    assert service.HEALTH.available("a", "deepseek-v3.2|direct"), "the caller's budget is not the endpoint's outage"


def test_call_model_with_a_budget_moves_to_a_fallback_model(monkeypatch):
    seen = _tool(monkeypatch, {"big|direct": ProviderCallFailed("no answer within max_latency_ms=2000",
                                                                 transport=True, budget_timeout=True)})
    body = json.loads(run(srv.call_model(_Ctx(), "p", model="big", fallback_models=["fast"], max_latency_ms=2000)))
    assert seen == ["big|direct", "fast|direct"] and body["unit"] == "fast|direct"
    assert run(srv.call_model(_Ctx(), "p", model="big", max_latency_ms=10)).startswith("REFUSED: max_latency_ms")
