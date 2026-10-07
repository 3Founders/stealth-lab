"""The provider layer (app/providers): endpoint safety, credential refs, the two adapters, who may use
which connection, the checks in call_unit, and the call_model tool. Offline: HTTP goes to an httpx
MockTransport and the policy / price stores are faked."""
from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace

import httpx
import pytest

import app.mcp_server.server as srv
import app.providers as providers
from app.providers import adapters, registry, secrets, service, url_guard
from app.providers.types import (CallRequest, Connection, ProviderCallDenied, ProviderCallFailed, UnitSpec,
                                 worst_case_cost)
from app.routing import plan
from app.services.access import AccessScope

SCOPE = AccessScope.for_org_member("u1", ["org-a"])
SECRET = "sk-super-secret-123"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv(registry.FILE_ENV, raising=False)
    monkeypatch.setattr(registry, "_STORES", {})
    monkeypatch.setattr(registry, "_file_cache", (None, []))
    monkeypatch.setattr(adapters, "ADAPTERS", dict(adapters.ADAPTERS))
    saved = dict(secrets._RESOLVERS)
    yield
    secrets._RESOLVERS.clear()
    secrets._RESOLVERS.update(saved)


def _spec(model="deepseek-v3.2", scaffold="direct", **kw):
    return UnitSpec(model=model, scaffold=scaffold, input_per_mtok=1.0, output_per_mtok=2.0, **kw)


def _conn(kind="openai_compatible", cid="c1", units=None, **kw):
    base = dict(connection_id=cid, kind=kind, base_url="https://api.example.com/v1", provider="example",
                units=tuple(units or [_spec()]), credential_ref="env:TEST_KEY", allowed_data_classes=("USER_PRIVATE",))
    base.update(kw)
    return Connection(**base)


def _mock(handler):
    transport = httpx.MockTransport(handler)
    return lambda **kw: httpx.AsyncClient(transport=transport, **kw)


# ------------------------------------------------------------------ url_guard

@pytest.mark.parametrize("url", [
    "http://api.example.com/v1", "ftp://x.example.com", "https://user:pw@api.example.com/", "https:///nohost",
    "file:///etc/passwd", "javascript:alert(1)"])
def test_url_shapes_that_are_refused(url):
    with pytest.raises(ProviderCallDenied):
        url_guard.validate_url_shape(url)


def test_http_is_allowed_only_for_loopback_and_only_when_the_connection_opts_in():
    url_guard.validate_url_shape("http://127.0.0.1:8000/v1", allow_http_loopback=True)
    url_guard.validate_url_shape("http://localhost:8000/v1", allow_http_loopback=True)
    with pytest.raises(ProviderCallDenied):
        url_guard.validate_url_shape("http://127.0.0.1:8000/v1")
    with pytest.raises(ProviderCallDenied):
        url_guard.validate_url_shape("http://10.0.0.5/v1", allow_http_loopback=True)


@pytest.mark.parametrize("url", [
    "https://169.254.169.254/latest/meta-data/", "https://10.0.0.1/", "https://192.168.1.1/", "https://127.0.0.1/",
    "https://[::1]/", "https://[::ffff:10.0.0.1]/", "https://[fe80::1]/", "https://0.0.0.0/"])
def test_private_loopback_link_local_and_mapped_addresses_are_blocked(url):
    with pytest.raises(ProviderCallDenied, match="non-public"):
        run(url_guard.check_endpoint(url))


def test_a_public_literal_passes_without_dns():
    run(url_guard.check_endpoint("https://8.8.8.8/v1"))


def _resolve(monkeypatch, *ips):
    def fake(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_a_name_must_resolve_only_to_public_addresses(monkeypatch):
    _resolve(monkeypatch, "93.184.216.34")
    run(url_guard.check_endpoint("https://api.example.com/v1"))
    _resolve(monkeypatch, "93.184.216.34", "10.1.2.3")                      # one private answer is enough to refuse
    with pytest.raises(ProviderCallDenied, match="non-public"):
        run(url_guard.check_endpoint("https://api.example.com/v1"))


def test_loopback_http_skips_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no DNS")))
    run(url_guard.check_endpoint("http://localhost:8000/v1", allow_http_loopback=True))


# ------------------------------------------------------------------ secrets

def test_env_credentials_resolve_and_fail_closed(monkeypatch):
    monkeypatch.setenv("TEST_KEY", SECRET)
    assert run(secrets.resolve_secret("env:TEST_KEY")) == SECRET
    assert run(secrets.resolve_secret(None)) is None
    monkeypatch.delenv("TEST_KEY")
    with pytest.raises(ProviderCallDenied, match="not set"):
        run(secrets.resolve_secret("env:TEST_KEY"))
    with pytest.raises(ProviderCallDenied, match="no secret resolver"):
        run(secrets.resolve_secret("gsm:projects/x/secrets/y"))


def test_a_registered_scheme_is_used():
    class Vault:
        async def resolve(self, ref):
            return "from-vault"
    secrets.register_secret_resolver("vault", Vault())
    assert run(secrets.resolve_secret("vault:kv/openai")) == "from-vault"


# ------------------------------------------------------------------ openai_compatible adapter

def _chat(content="hi", usage=(10, 4), finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


def test_chat_request_shape_response_usage_and_cost(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers.get("authorization"), json.loads(request.content)
        return httpx.Response(200, json=_chat("hello", usage=(1000, 500)))
    monkeypatch.setattr(adapters, "http_client", _mock(handler))
    conn, spec = _conn(), _spec(provider_model="deepseek-ai/DeepSeek-V3.2", max_output_tokens=100)
    out = run(adapters.OpenAICompatAdapter().call(conn, spec, CallRequest(prompt="p", system="s", max_tokens=900,
                                                                          temperature=0.2), SECRET))
    assert seen["url"] == "https://api.example.com/v1/chat/completions" and seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"]["model"] == "deepseek-ai/DeepSeek-V3.2" and seen["body"]["max_tokens"] == 100
    assert seen["body"]["messages"] == [{"role": "system", "content": "s"}, {"role": "user", "content": "p"}]
    assert out.text == "hello" and (out.tokens_in, out.tokens_out) == (1000, 500)
    assert out.cost_usd == pytest.approx((1000 * 1.0 + 500 * 2.0) / 1e6) and out.unit == "deepseek-v3.2|direct"


def test_content_part_arrays_are_joined(monkeypatch):
    body = _chat([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(200, json=body)))
    assert run(adapters.OpenAICompatAdapter().call(_conn(), _spec(), CallRequest(prompt="p"), None)).text == "ab"


def test_http_errors_never_leak_the_credential(monkeypatch):
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(401, text=f"bad key {SECRET}")))
    with pytest.raises(ProviderCallFailed) as err:
        run(adapters.OpenAICompatAdapter().call(_conn(), _spec(), CallRequest(prompt="p"), SECRET))
    assert "401" in str(err.value) and SECRET not in str(err.value) and "[redacted]" in str(err.value)


def test_a_reasoning_model_that_ran_out_of_budget_is_reported_not_returned_empty(monkeypatch):
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(200, json=_chat(None, finish="length"))))
    with pytest.raises(ProviderCallFailed, match="raise max_tokens"):
        run(adapters.OpenAICompatAdapter().call(_conn(), _spec(), CallRequest(prompt="p"), None))


def test_transport_failures_and_odd_replies_become_one_error(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(adapters, "http_client", _mock(boom))
    with pytest.raises(ProviderCallFailed, match="ConnectError"):
        run(adapters.OpenAICompatAdapter().call(_conn(), _spec(), CallRequest(prompt="p"), None))
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(200, json={"nope": 1})))
    with pytest.raises(ProviderCallFailed, match="unexpected reply shape"):
        run(adapters.OpenAICompatAdapter().call(_conn(), _spec(), CallRequest(prompt="p"), None))
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(ProviderCallFailed, match="did not return JSON"):
        run(adapters.OpenAICompatAdapter().call(_conn(), _spec(), CallRequest(prompt="p"), None))


def test_redirects_are_not_followed(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"}, text="moved")
    monkeypatch.setattr(adapters, "http_client", _mock(handler))
    with pytest.raises(ProviderCallFailed, match="302"):
        run(adapters.OpenAICompatAdapter().call(_conn(), _spec(), CallRequest(prompt="p"), None))
    assert calls == ["https://api.example.com/v1/chat/completions"]


# ------------------------------------------------------------------ a2a adapter

def _agent_conn():
    return _conn(kind="a2a", cid="agent", base_url="https://agent.example.com/a2a",
                 units=[UnitSpec(model="gemma-4", scaffold="coder", per_call_usd=0.05)])


def _rpc(result=None, error=None):
    return {"jsonrpc": "2.0", "id": "1", **({"result": result} if error is None else {"error": error})}


def test_a2a_sends_sendmessage_and_reads_artifacts(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"], seen["version"], seen["auth"] = json.loads(request.content), request.headers.get("a2a-version"), request.headers.get("authorization")
        return httpx.Response(200, json=_rpc({"task": {"status": {"state": "TASK_STATE_COMPLETED"},
                                                       "artifacts": [{"parts": [{"text": "patch applied"}]}]}}))
    monkeypatch.setattr(adapters, "http_client", _mock(handler))
    conn = _agent_conn()
    out = run(adapters.A2AAdapter().call(conn, conn.units[0], CallRequest(prompt="fix it", system="be brief"), SECRET))
    msg = seen["body"]["params"]["message"]
    assert seen["body"]["method"] == "SendMessage" and msg["role"] == "ROLE_USER"
    assert msg["parts"] == [{"text": "be brief\n\nfix it"}] and seen["version"] == "1.0" and seen["auth"] == f"Bearer {SECRET}"
    assert out.text == "patch applied" and out.state is None and out.cost_usd == 0.05 and out.unit == "gemma-4|coder"


def test_a2a_reads_a_plain_message_and_a_bare_task(monkeypatch):
    conn, spec = _agent_conn(), _agent_conn().units[0]
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(
        200, json=_rpc({"message": {"role": "ROLE_AGENT", "parts": [{"text": "done"}]}}))))
    assert run(adapters.A2AAdapter().call(conn, spec, CallRequest(prompt="p"), None)).text == "done"
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(
        200, json=_rpc({"status": {"state": "completed", "message": {"parts": [{"kind": "text", "text": "legacy"}]}}}))))
    assert run(adapters.A2AAdapter().call(conn, spec, CallRequest(prompt="p"), None)).text == "legacy"


def test_a2a_unfinished_and_failed_tasks(monkeypatch):
    conn, spec = _agent_conn(), _agent_conn().units[0]
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(
        200, json=_rpc({"task": {"status": {"state": "TASK_STATE_WORKING"}}}))))
    out = run(adapters.A2AAdapter().call(conn, spec, CallRequest(prompt="p"), None))
    assert out.state == "working" and out.text == ""
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(
        200, json=_rpc({"task": {"status": {"state": "TASK_STATE_FAILED"}}}))))
    with pytest.raises(ProviderCallFailed, match="failed"):
        run(adapters.A2AAdapter().call(conn, spec, CallRequest(prompt="p"), None))


def test_a2a_falls_back_to_the_legacy_method_once(monkeypatch):
    methods = []

    def handler(request):
        body = json.loads(request.content)
        methods.append((body["method"], request.headers.get("a2a-version")))
        if body["method"] == "SendMessage":
            return httpx.Response(200, json=_rpc(error={"code": -32601, "message": "Method not found"}))
        assert body["params"]["message"]["parts"] == [{"kind": "text", "text": "p"}]
        return httpx.Response(200, json=_rpc({"kind": "message", "parts": [{"kind": "text", "text": "old agent"}]}))
    monkeypatch.setattr(adapters, "http_client", _mock(handler))
    conn = _agent_conn()
    out = run(adapters.A2AAdapter().call(conn, conn.units[0], CallRequest(prompt="p"), None))
    assert out.text == "old agent" and methods == [("SendMessage", "1.0"), ("message/send", None)]


def test_a2a_errors_do_not_leak_the_credential(monkeypatch):
    monkeypatch.setattr(adapters, "http_client", _mock(lambda r: httpx.Response(
        200, json=_rpc(error={"code": -32000, "message": f"auth failed for {SECRET}"}))))
    conn = _agent_conn()
    with pytest.raises(ProviderCallFailed) as err:
        run(adapters.A2AAdapter().call(conn, conn.units[0], CallRequest(prompt="p"), SECRET))
    assert SECRET not in str(err.value)


# ------------------------------------------------------------------ registry

def _raw(**kw):
    base = {"connection_id": "c1", "kind": "openai_compatible", "base_url": "https://api.example.com/v1",
            "units": [{"model": "m1", "input_per_mtok": 1, "output_per_mtok": 2}], "allowed_data_classes": ["USER_PRIVATE"]}
    base.update(kw)
    return base


def test_a_valid_connection_loads_with_defaults():
    conn = registry.connection_from_dict(_raw())
    assert conn.owner == "platform" and conn.credential_owner == "customer" and conn.units[0].unit == "m1|direct"
    assert conn.provider == "c1" and conn.allow_http_loopback is False


@pytest.mark.parametrize("patch,message", [
    ({"connection_id": ""}, "connection_id"),
    ({"kind": "smtp"}, "kind must be"),
    ({"owner": "team:x"}, "owner must be"),
    ({"credential_owner": "someone"}, "credential_owner"),
    ({"units": []}, "at least one unit"),
    ({"units": [{"model": "a|b"}]}, "without '|'"),
    ({"units": [{"model": "m", "input_per_mtok": 1}]}, "both input_per_mtok"),
    ({"units": [{"model": "m", "input_per_mtok": -1, "output_per_mtok": 1}]}, "negative"),
    ({"units": [{"model": "m"}, {"model": "m"}]}, "duplicate"),
    ({"base_url": "http://api.example.com"}, "https"),
])
def test_invalid_connections_are_refused_with_a_reason(patch, message):
    with pytest.raises(ValueError, match=message):
        registry.connection_from_dict(_raw(**patch))


def test_a_tier_is_a_short_lowercase_label_or_absent():
    raw = _raw(units=[{"model": "m1", "tier": "flagship"}, {"model": "m2"}])
    conn = registry.connection_from_dict(raw)
    assert [u.tier for u in conn.units] == ["flagship", None]
    for bad in ("Flagship", "", "x" * 33, "has space", 3):
        with pytest.raises(ValueError, match="tier must be"):
            registry.connection_from_dict(_raw(units=[{"model": "m1", "tier": bad}]))


def test_visibility_rules():
    platform, org_a = _conn(cid="p"), _conn(cid="a", owner="org:org-a")
    org_b, mine, theirs = _conn(cid="b", owner="org:org-b"), _conn(cid="m", owner="user:u1"), _conn(cid="t", owner="user:u2")
    off = _conn(cid="off", enabled=False)
    seen = {c.connection_id for c in [platform, org_a, org_b, mine, theirs, off] if registry.visible(c, SCOPE)}
    assert seen == {"p", "a", "m"}
    assert registry.visible(_conn(), AccessScope.anonymous()) and not registry.visible(org_a, AccessScope.anonymous())
    assert registry.visible(org_b, AccessScope.unrestricted()) and not registry.visible(off, AccessScope.unrestricted())


def test_file_store_reloads_on_change_and_stores_merge_first_wins(monkeypatch, tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"connections": [_raw(connection_id="f1")]}), encoding="utf-8")
    monkeypatch.setenv(registry.FILE_ENV, str(path))
    assert registry.configured()
    assert [c.connection_id for c in run(registry.visible_connections(SCOPE))] == ["f1"]
    registry.register_connection_store("mem", registry.StaticConnectionStore([_conn(cid="f1", base_url="https://other.example.com/v1"),
                                                                              _conn(cid="m1")]))
    got = {c.connection_id: c for c in run(registry.visible_connections(SCOPE))}
    assert set(got) == {"f1", "m1"} and got["f1"].base_url == "https://other.example.com/v1"      # registered store wins
    import os
    path.write_text(json.dumps({"connections": [_raw(connection_id="f2")]}), encoding="utf-8")
    os.utime(path, (os.path.getmtime(path) + 5, os.path.getmtime(path) + 5))
    assert {c.connection_id for c in run(registry.visible_connections(SCOPE))} == {"f2", "f1", "m1"}


def test_a_broken_file_fails_loudly(monkeypatch, tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"connections": [_raw(kind="nope")]}), encoding="utf-8")
    monkeypatch.setenv(registry.FILE_ENV, str(path))
    with pytest.raises(ValueError, match="kind must be"):
        run(registry.visible_connections(SCOPE))


# ------------------------------------------------------------------ call_unit

class FakeAdapter:
    def __init__(self):
        self.calls = []

    async def call(self, conn, spec, request, secret):
        self.calls.append((conn.connection_id, spec.unit, request.prompt, secret))
        return providers.CallResult(unit=spec.unit, connection_id=conn.connection_id, text="ok", tokens_in=5, tokens_out=3)


@pytest.fixture
def fake(monkeypatch):
    adapter = FakeAdapter()
    adapters.register_adapter("fake", adapter)
    monkeypatch.setenv("TEST_KEY", SECRET)
    _resolve(monkeypatch, "93.184.216.34")
    return adapter


def _store(*conns):
    registry.register_connection_store("t", registry.StaticConnectionStore(conns))


def test_a_call_goes_through_with_the_resolved_credential(fake):
    _store(_conn(kind="fake"))
    out = run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi")))
    assert out.text == "ok" and fake.calls == [("c1", "deepseek-v3.2|direct", "hi", SECRET)]


def test_an_unknown_unit_and_someone_elses_unit_look_the_same(fake):
    _store(_conn(kind="fake", cid="theirs", owner="org:org-b"))
    for unit in ("nope|direct", "deepseek-v3.2|direct"):
        with pytest.raises(ProviderCallDenied, match="no connection available to you offers"):
            run(service.call_unit(None, SCOPE, unit, CallRequest(prompt="hi")))
    assert fake.calls == []


def test_a_data_class_the_connection_is_not_approved_for_is_refused_before_sending(fake):
    _store(_conn(kind="fake"))
    with pytest.raises(ProviderCallDenied, match="not approved for CONFIDENTIAL_DATA"):
        run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi", data_class="CONFIDENTIAL_DATA")))
    with pytest.raises(ProviderCallDenied, match="unknown data_class"):
        run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi", data_class="MADE_UP")))
    assert fake.calls == []


@pytest.mark.parametrize("request_kw,message", [
    ({"prompt": "  "}, "empty"), ({"prompt": "x", "max_tokens": 0}, "max_tokens"),
    ({"prompt": "x", "max_tokens": 10**6}, "max_tokens"), ({"prompt": "x" * 400_001}, "longer than")])
def test_bad_requests_are_refused(fake, request_kw, message):
    _store(_conn(kind="fake"))
    with pytest.raises(ProviderCallDenied, match=message):
        run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", CallRequest(**request_kw)))


def test_a_platform_credential_must_pass_the_platform_egress_policy(fake, monkeypatch):
    from app.services import provider_policy
    _store(_conn(kind="fake", credential_owner="platform"))
    with pytest.raises(ProviderCallDenied, match="egress policy database"):
        run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi")))
    seen = {}

    async def deny(pool, **kw):
        seen.update(kw)
        raise provider_policy.ProviderPolicyDenied(provider_policy.PolicyDecision(
            False, "no effective model-provider policy", kw["provider"], kw["model"], kw["data_classification"]))
    monkeypatch.setattr(provider_policy, "guard_send", deny)
    with pytest.raises(ProviderCallDenied, match="no effective model-provider policy"):
        run(service.call_unit(object(), SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi"),
                              actor="u1", tenant_id="org-a"))
    assert (seen["provider"], seen["model"], seen["tenant_id"], seen["actor_subject"]) == ("example", "deepseek-v3.2", "org-a", "u1")
    assert fake.calls == []

    async def allow(pool, **kw):
        return None
    monkeypatch.setattr(provider_policy, "guard_send", allow)
    assert run(service.call_unit(object(), SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi"))).text == "ok"


def test_a_customer_credential_skips_the_platform_policy(fake, monkeypatch):
    from app.services import provider_policy

    async def never(pool, **kw):
        raise AssertionError("BYOK is the customer's own risk decision")
    monkeypatch.setattr(provider_policy, "guard_send", never)
    _store(_conn(kind="fake", credential_owner="customer"))
    assert run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi"))).text == "ok"


def test_max_cost_refuses_an_expensive_or_unpriceable_call(fake):
    _store(_conn(kind="fake", units=[_spec(), UnitSpec(model="free", scaffold="direct")]))
    request = CallRequest(prompt="x" * 3500, max_tokens=1000)
    worst = worst_case_cost(_spec(), request)
    assert worst == pytest.approx((1001 * 1.0 + 1000 * 2.0) / 1e6)
    with pytest.raises(ProviderCallDenied, match="exceeds max_cost_usd"):
        run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", request, max_cost_usd=worst / 2))
    with pytest.raises(ProviderCallDenied, match="no price"):
        run(service.call_unit(None, SCOPE, "free|direct", request, max_cost_usd=1.0))
    assert run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", request, max_cost_usd=worst * 2)).text == "ok"
    assert run(service.call_unit(None, SCOPE, "free|direct", request)).text == "ok"            # no cap asked, no price needed


def test_an_unsafe_endpoint_is_refused_at_call_time(fake, monkeypatch):
    _store(_conn(kind="fake"))
    _resolve(monkeypatch, "169.254.169.254")                                                 # rebinds after being configured
    with pytest.raises(ProviderCallDenied, match="non-public"):
        run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi")))
    assert fake.calls == []


def test_a_missing_credential_is_refused_before_sending(fake, monkeypatch):
    _store(_conn(kind="fake"))
    monkeypatch.delenv("TEST_KEY")
    with pytest.raises(ProviderCallDenied, match="not set"):
        run(service.call_unit(None, SCOPE, "deepseek-v3.2|direct", CallRequest(prompt="hi")))
    assert fake.calls == []


# ------------------------------------------------------------------ routing seam

def test_candidates_are_the_units_the_caller_may_run():
    _store(_conn(cid="p", units=[_spec("m1"), _spec("m2", "coder")]), _conn(cid="o", owner="org:org-b", units=[_spec("m3")]))
    got = run(service.ProviderCandidates().candidates(None, scope=SCOPE, goal_id="g", constraints={}))
    assert got == ["m1|direct", "m2|coder"]


def test_the_provider_is_registered_on_the_server_and_inert_until_something_is_configured():
    registered = [p for p in plan.registered_candidate_providers() if p.name == "connections"]
    assert registered, "server.py must register the connections provider"
    assert registered[0].configured() is False and plan.wants_plan(None) is False
    _store(_conn())
    assert registered[0].configured() is True


def test_sync_prices_writes_only_what_differs(monkeypatch):
    from app.routing import store
    from app.routing.costs import Price
    written = []

    async def current(pool, keys):
        return {"same": Price(1.0, 2.0, None), "changed": Price(9.0, 9.0, None)}

    async def set_price(pool, model, **kw):
        written.append((model, kw["input_per_mtok"], kw["output_per_mtok"]))
    monkeypatch.setattr(store, "current_prices", current)
    monkeypatch.setattr(store, "set_price", set_price)
    conn = _conn(units=[_spec("same"), _spec("changed"), _spec("new"), UnitSpec(model="noprice")])
    plan_only = run(service.sync_prices(None, [conn], apply=False))
    assert {c["model"] for c in plan_only} == {"changed", "new"} and written == []
    run(service.sync_prices(None, [conn], apply=True))
    assert sorted(written) == [("changed", 1.0, 2.0), ("new", 1.0, 2.0)]


# ------------------------------------------------------------------ the call_model tool

def _ctx():
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": object()}))


def _tool(monkeypatch, result=None, raises=None):
    seen = {}

    async def fake_call(pool, scope, unit, request, **kw):
        seen.update(unit=unit, request=request, **kw)
        if raises:
            raise raises
        return result or providers.CallResult(unit=unit, connection_id="c1", text="answer", tokens_in=7, tokens_out=3,
                                              cost_usd=0.001, latency_ms=12)
    monkeypatch.setattr(providers, "call_unit", fake_call)
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: SCOPE)
    return seen


def test_call_model_is_registered_classified_and_annotated():
    assert "call_model" in {t.name for t in srv.server._tool_manager.list_tools()} and "call_model" in srv.V1_TOOLS
    assert srv._TOOL_SCOPES["call_model"] == srv._EXEC
    ann = srv._V1_ANNOTATIONS["call_model"]
    assert ann["read_only_hint"] is False and ann["open_world_hint"] is True


def test_call_model_runs_a_named_model(monkeypatch):
    seen = _tool(monkeypatch)
    body = json.loads(run(srv.call_model(_ctx(), "hello", model="deepseek-v3.2", max_tokens=50, max_cost_usd=0.5)))
    assert body["text"] == "answer" and body["unit"] == "deepseek-v3.2|direct" and body["usage"]["tokens_in"] == 7
    assert "next" not in body
    assert seen["unit"] == "deepseek-v3.2|direct" and seen["max_cost_usd"] == 0.5 and seen["tenant_id"] == "org-a"
    assert seen["request"].data_class == "USER_PRIVATE" and seen["request"].max_tokens == 50


def test_call_model_runs_an_agent_by_scaffold(monkeypatch):
    seen = _tool(monkeypatch)
    run(srv.call_model(_ctx(), "do it", model="gemma-4", scaffold="coder"))
    assert seen["unit"] == "gemma-4|coder"


def test_auto_needs_an_instance_key_and_uses_the_plans_next_rung(monkeypatch):
    seen = _tool(monkeypatch)
    assert run(srv.call_model(_ctx(), "p")).startswith("REFUSED: model='auto' needs the instance_key")

    async def load(pool, scope, key):
        return "INSTANCE"
    monkeypatch.setattr(plan, "load_instance", load)
    monkeypatch.setattr(plan, "_default_unit", lambda inst: "cheap|direct" if inst == "INSTANCE" else "?")
    body = json.loads(run(srv.call_model(_ctx(), "p", instance_key="g.k")))
    assert seen["unit"] == "cheap|direct"
    report = body["next"]["with"]
    assert body["next"]["check_then_call"] == "report_result" and report["instance_key"] == "g.k"
    assert report["tokens_in"] == 7 and report["cost_usd"] == 0.001 and "model" not in report      # default rung: no model needed


def test_an_explicit_model_with_an_instance_key_reports_which_one_ran(monkeypatch):
    _tool(monkeypatch)

    async def load(pool, scope, key):
        return "INSTANCE"
    monkeypatch.setattr(plan, "load_instance", load)
    report = json.loads(run(srv.call_model(_ctx(), "p", model="big", scaffold="x", instance_key="g.k")))["next"]["with"]
    assert (report["model"], report["scaffold"]) == ("big", "x")


def test_a_foreign_instance_key_is_refused(monkeypatch):
    _tool(monkeypatch)

    async def load(pool, scope, key):
        raise plan.RoutingError("unknown instance_key")
    monkeypatch.setattr(plan, "load_instance", load)
    assert run(srv.call_model(_ctx(), "p", model="m", instance_key="zzz")).startswith("REFUSED: unknown instance_key")


def test_denials_and_failures_are_distinguished(monkeypatch):
    _tool(monkeypatch, raises=ProviderCallDenied("connection 'c1' is not approved for USER_PRIVATE data"))
    assert run(srv.call_model(_ctx(), "p", model="m")).startswith("REFUSED: connection 'c1' is not approved")
    _tool(monkeypatch, raises=ProviderCallFailed("c1: HTTP 500"))
    assert run(srv.call_model(_ctx(), "p", model="m")).startswith("FAILED: c1: HTTP 500")


def test_an_unfinished_agent_task_shows_its_state(monkeypatch):
    _tool(monkeypatch, result=providers.CallResult(unit="a|b", connection_id="c", text="", state="working"))
    assert json.loads(run(srv.call_model(_ctx(), "p", model="a", scaffold="b")))["state"] == "working"
