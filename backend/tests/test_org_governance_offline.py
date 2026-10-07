"""Organisation governance, offline: the pure policy logic, the governed-call flow around a provider call, the cache-aware
token and cost accounting, and the admin REST layer. The database behaviour (row-level security, the settle-once ledger,
budgets under concurrency, the audit hash chain, erasure) is proven in test_org_governance_e2e.py."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api import org_admin
from app.api.deps import get_auth_context
from app.providers import adapters, registry, service
from app.providers.types import (CallRequest, CallResult, Connection, ProviderCallDenied, ProviderCallFailed, UnitSpec,
                                 tokens_cost)
from app.services import org_governance as gov
from app.services.access import AccessScope
from app.services.auth_context import AnonymousContext, AuthContext

ORG = "0b0f6a0e-3a33-4c1c-9f3b-2f3f6f0a0001"
OTHER = "0b0f6a0e-3a33-4c1c-9f3b-2f3f6f0a0002"


def run(coro):
    return asyncio.run(coro)


def policy(**kw) -> gov.OrgPolicy:
    base = dict(organization_id=ORG, kill_switch=False, kill_switch_reason=None, allowed_providers=("example",),
                allowed_models=("m1",), allowed_tools=("call_model",), allowed_data_classes=("USER_PRIVATE",),
                monthly_budget_usd=Decimal("10"), per_user_daily_budget_usd=Decimal("2"), version=1)
    base.update(kw)
    return gov.OrgPolicy(**base)


# ------------------------------------------------------------------ evaluate: nothing is allowed unless listed

def test_a_fully_listed_call_passes():
    gov.evaluate(policy(), provider="example", model="m1", tool="call_model", data_class="USER_PRIVATE")


@pytest.mark.parametrize("kw,needle", [
    ({"provider": "other"}, "provider"), ({"model": "m2"}, "model"), ({"tool": "submit_way"}, "tool"),
    ({"data_class": "CONFIDENTIAL_DATA"}, "data class")])
def test_every_dimension_must_be_on_its_allowlist(kw, needle):
    call = {"provider": "example", "model": "m1", "tool": "call_model", "data_class": "USER_PRIVATE", **kw}
    with pytest.raises(gov.PolicyDenied, match=needle):
        gov.evaluate(policy(), **call)


@pytest.mark.parametrize("field", ["allowed_providers", "allowed_models", "allowed_tools", "allowed_data_classes"])
def test_an_empty_allowlist_allows_nothing(field):
    with pytest.raises(gov.PolicyDenied):
        gov.evaluate(policy(**{field: ()}), provider="example", model="m1", tool="call_model", data_class="USER_PRIVATE")


def test_the_kill_switch_wins_over_an_otherwise_valid_call():
    with pytest.raises(gov.PolicyDenied, match="stopped by its admins: incident 42"):
        gov.evaluate(policy(kill_switch=True, kill_switch_reason="incident 42"), provider="example", model="m1",
                     tool="call_model", data_class="USER_PRIVATE")


# ------------------------------------------------------------------ input validation (no wildcard, no unlimited)

def test_lists_reject_wildcards_duplicates_blanks_and_non_lists():
    assert gov._clean_list("x", [" a ", "b"]) == ["a", "b"]
    for bad in (["*"], ["a", "a"], [""], ["  "], "a", [1], None):
        with pytest.raises(gov.Invalid):
            gov._clean_list("x", bad)


def test_money_must_be_a_finite_number_of_zero_or_more():
    assert gov._clean_money("x", "12.5") == Decimal("12.5")
    assert gov._clean_money("x", 0) == Decimal("0")
    for bad in (-1, "abc", "NaN", "Infinity", None):
        with pytest.raises(gov.Invalid):
            gov._clean_money("x", bad)


def test_policy_changes_need_an_admin_and_known_fields_before_the_database_is_touched():
    base = dict(actor_subject="u", actor_user_id=None, org_id=ORG, expected_version=None)
    with pytest.raises(gov.NotAuthorized):
        run(gov.put_policy(None, actor_roles={"member"}, changes={}, **base))
    with pytest.raises(gov.NotAuthorized):
        run(gov.put_policy(None, actor_roles={"viewer"}, changes={}, **base))
    with pytest.raises(gov.Invalid, match="unknown policy field"):
        run(gov.put_policy(None, actor_roles={"admin"}, changes={"kill_switch": True}, **base))
    with pytest.raises(gov.Invalid, match="wildcard"):
        run(gov.put_policy(None, actor_roles={"owner"}, changes={"allowed_models": ["*"]}, **base))
    with pytest.raises(gov.Invalid, match="zero or more"):
        run(gov.put_policy(None, actor_roles={"owner"}, changes={"monthly_budget_usd": -5}, **base))


def test_the_kill_switch_needs_an_admin_and_a_reason():
    base = dict(actor_subject="u", actor_user_id=None, org_id=ORG, on=True)
    with pytest.raises(gov.NotAuthorized):
        run(gov.set_kill_switch(None, actor_roles={"member"}, reason="x", **base))
    for reason in ("", "   ", None):
        with pytest.raises(gov.Invalid, match="reason"):
            run(gov.set_kill_switch(None, actor_roles={"admin"}, reason=reason, **base))


@pytest.mark.parametrize("fn,roles", [
    (gov.place_legal_hold, {"admin"}), (gov.request_erasure, {"admin"}), (gov.place_legal_hold, {"member"})])
def test_holds_and_erasure_are_owner_only(fn, roles):
    with pytest.raises(gov.NotAuthorized):
        run(fn(None, actor_subject="u", actor_user_id=None, actor_roles=roles, org_id=ORG, reason="because"))


def test_approve_release_and_execute_are_owner_only():
    common = dict(actor_subject="u", actor_user_id=None, actor_roles={"admin"}, org_id=ORG)
    with pytest.raises(gov.NotAuthorized):
        run(gov.approve_erasure(None, request_id="r", **common))
    with pytest.raises(gov.NotAuthorized):
        run(gov.execute_erasure(None, request_id="r", **common))
    with pytest.raises(gov.NotAuthorized):
        run(gov.release_legal_hold(None, hold_id="h", **common))


def test_usage_and_audit_export_need_an_admin():
    from datetime import datetime
    now = datetime(2026, 10, 1)
    with pytest.raises(gov.NotAuthorized):
        run(gov.usage_daily(None, actor_roles={"member"}, org_id=ORG, since=now, until=now))
    with pytest.raises(gov.NotAuthorized):
        run(gov.audit_export(None, actor_roles={"member"}, org_id=ORG, since=now, until=now))
    with pytest.raises(gov.Invalid, match="limit"):
        run(gov.audit_export(None, actor_roles={"admin"}, org_id=ORG, since=now, until=now, limit=0))


def test_a_blocked_registry_can_never_produce_a_false_completion():
    states = {e["table"]: e["state"] for e in gov.ERASURE_REGISTRY}
    assert states["provider_call_ledger"] == "ready" and states["org_policies"] == "ready"
    assert any(s == "blocked" for s in states.values()), "core tables are blocked until their erasure branch exists"
    assert all(e["note"] for e in gov.ERASURE_REGISTRY)


# ------------------------------------------------------------------ which organisation governs a call

def _conn(owner="platform", cid="c1"):
    return Connection(connection_id=cid, kind="fake", base_url="https://api.example.com/v1", owner=owner,
                      units=(UnitSpec(model="m1", input_per_mtok=1.0, output_per_mtok=2.0),), provider="example",
                      allowed_data_classes=("USER_PRIVATE",))


def _scope(*orgs):
    return AccessScope.for_org_member("u1", list(orgs)) if orgs else AccessScope.for_user("u1")


@pytest.mark.parametrize("conn,scope,requested,governed,expected", [
    (_conn(), _scope(ORG), None, False, None),                     # single operator: nobody to govern
    (_conn("org:" + ORG), _scope(ORG), None, True, ORG),           # an org's connection: its owner pays
    (_conn(), _scope(ORG), None, True, ORG),                       # exactly one org: that one
    (_conn(), _scope(ORG, OTHER), ORG, True, ORG),                 # several: named explicitly
    (_conn("user:u1"), _scope(), None, True, None),                # a person with no org, their own connection
])
def test_the_governing_organization(conn, scope, requested, governed, expected):
    assert service.governing_org(conn, scope, requested, governed=governed) == expected


@pytest.mark.parametrize("conn,scope,requested,needle", [
    (_conn(), _scope(ORG, OTHER), None, "several organizations"),
    (_conn(), _scope(ORG), OTHER, "not a member"),
    (_conn(), _scope(), None, "belong to none"),                   # a platform connection with no organization
    (_conn("org:" + ORG), _scope(ORG), OTHER, "different organization")])
def test_nothing_is_guessed_when_the_organization_is_unclear(conn, scope, requested, needle):
    with pytest.raises(ProviderCallDenied, match=needle):
        service.governing_org(conn, scope, requested, governed=True)


# ------------------------------------------------------------------ the governed call: reserve, send, settle

class FakeAdapter:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def call(self, conn, spec, request, secret):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result or CallResult(unit=spec.unit, connection_id=conn.connection_id, text="ok", tokens_in=100,
                                         tokens_out=20, tokens_cache_read=800, cost_usd=0.002, cost_source="declared",
                                         latency_ms=9)


class FakeGov:
    """Stands in for org_governance's ledger functions and records the order of events."""

    def __init__(self, deny=None, settle_error=None, release_error=None, denial_error=None):
        self.events, self.deny, self.settle_error, self.release_error = [], deny, settle_error, release_error
        self.denial_error = denial_error

    async def reserve_call(self, pool, **kw):
        self.events.append(("reserve", kw))
        if self.deny:
            raise self.deny
        return gov.Reservation("led-1", kw["org_id"], Decimal("0.5"))

    async def settle_call(self, pool, reservation, **kw):
        self.events.append(("settle", kw))
        if self.settle_error:
            raise self.settle_error

    async def fail_call(self, pool, reservation, **kw):
        self.events.append(("fail", kw))
        if self.release_error:
            raise self.release_error

    async def record_denial(self, pool, **kw):
        self.events.append(("denial", kw))
        if self.denial_error:
            raise self.denial_error


@pytest.fixture
def governed(monkeypatch):
    monkeypatch.delenv(registry.FILE_ENV, raising=False)
    monkeypatch.setattr(registry, "_STORES", {})
    monkeypatch.setattr(adapters, "ADAPTERS", dict(adapters.ADAPTERS))
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])
    fake = FakeGov()
    for name in ("reserve_call", "settle_call", "fail_call", "record_denial"):
        monkeypatch.setattr(gov, name, getattr(fake, name))
    return fake


def _install(adapter, conn=None):
    adapters.register_adapter("fake", adapter)
    registry.register_connection_store("t", registry.StaticConnectionStore([conn or _conn("org:" + ORG)]))


def _call(**kw):
    kw.setdefault("actor", "u1")
    kw.setdefault("governed", True)
    return run(service.call_unit(object(), _scope(ORG), "m1|direct", CallRequest(prompt="hi"), **kw))


def test_cost_components_use_the_prices_in_force_and_sum_to_the_declared_total():
    prices = {"input": 10.0, "output": 40.0, "cache_read": 1.0, "cache_write": 12.5}
    parts = gov.cost_components(prices, tokens_input_fresh=200, tokens_cache_read=800, tokens_cache_write=100,
                                tokens_output=100)
    assert parts == {"input": Decimal("0.002000"), "output": Decimal("0.004000"), "cache_read": Decimal("0.000800"),
                     "cache_write": Decimal("0.001250")}
    spec = UnitSpec(model="m", input_per_mtok=10.0, output_per_mtok=40.0, cached_input_per_mtok=1.0)
    assert float(sum(parts.values()) - Decimal("0.001250")) == pytest.approx(
        tokens_cost(spec, 200, 100, 800), abs=1e-9)                       # same formula as the adapters' declared cost


def test_missing_cache_prices_default_to_the_input_price_and_missing_tokens_cost_nothing():
    parts = gov.cost_components({"input": 10.0, "output": 40.0}, tokens_input_fresh=100, tokens_cache_read=1000,
                                tokens_cache_write=None, tokens_output=0)
    assert parts == {"input": Decimal("0.001000"), "output": Decimal("0.000000"), "cache_read": Decimal("0.010000"),
                     "cache_write": Decimal("0.000000")}


@pytest.mark.parametrize("prices", [None, {}, {"input": 1.0}, {"output": 1.0}, {"input": None, "output": 2.0}])
def test_without_declared_token_prices_there_is_no_split(prices):
    assert gov.cost_components(prices, tokens_input_fresh=1, tokens_cache_read=1, tokens_cache_write=1,
                               tokens_output=1) is None


def test_a_governed_call_reserves_then_sends_then_settles_with_the_four_token_fields(governed):
    adapter = FakeAdapter()
    _install(adapter)
    out = _call(instance_key="g.k")
    assert out.text == "ok" and adapter.calls == 1
    kinds = [e[0] for e in governed.events]
    assert kinds == ["reserve", "settle"]
    reserve, settle = governed.events[0][1], governed.events[1][1]
    assert (reserve["org_id"], reserve["actor_subject"], reserve["tool"], reserve["unit"], reserve["model"],
            reserve["provider"], reserve["data_class"], reserve["instance_key"]) == (
        ORG, "u1", "call_model", "m1|direct", "m1", "example", "USER_PRIVATE", "g.k")
    assert reserve["worst_case_usd"] is not None and reserve["worst_case_usd"] > 0
    assert (settle["tokens_input_fresh"], settle["tokens_cache_read"], settle["tokens_cache_write"],
            settle["tokens_output"], settle["cost_usd"], settle["cost_source"]) == (100, 800, None, 20, 0.002, "declared")
    assert settle["prices"] == {"input": 1.0, "output": 2.0, "cache_read": None, "cache_write": None}   # the unit's prices


def test_the_router_gate_time_and_the_tier_are_recorded_with_the_settlement(governed):
    spec = UnitSpec(model="m1", input_per_mtok=1.0, output_per_mtok=2.0, tier="light")
    conn = Connection(connection_id="c1", kind="fake", base_url="https://api.example.com/v1", owner="org:" + ORG,
                      units=(spec,), provider="example", allowed_data_classes=("USER_PRIVATE",))
    _install(FakeAdapter(), conn)
    _call()
    settle = governed.events[1][1]
    assert settle["tier"] == "light" and isinstance(settle["gate_ms"], int) and settle["gate_ms"] >= 0


def test_a_unit_without_a_tier_records_none_not_a_guess(governed):
    _install(FakeAdapter())
    _call()
    assert governed.events[1][1]["tier"] is None


def test_a_denied_reservation_means_nothing_is_sent(governed):
    adapter = FakeAdapter()
    _install(adapter)
    governed.deny = gov.BudgetExceeded("over the monthly budget", code="monthly_budget_exceeded")
    with pytest.raises(ProviderCallDenied, match="over the monthly budget"):
        _call()
    assert adapter.calls == 0 and [e[0] for e in governed.events] == ["reserve", "denial"]
    denial = governed.events[1][1]
    assert (denial["org_id"], denial["actor_subject"], denial["tool"], denial["unit"], denial["provider"],
            denial["model"], denial["data_class"]) == (ORG, "u1", "call_model", "m1|direct", "example", "m1", "USER_PRIVATE")
    assert denial["error"].code == "monthly_budget_exceeded"


def test_a_missing_policy_denies_the_call(governed):
    adapter = FakeAdapter()
    _install(adapter)
    governed.deny = gov.PolicyMissing("no policy configured", code="no_policy")
    with pytest.raises(ProviderCallDenied, match="no policy"):
        _call()
    assert adapter.calls == 0 and governed.events[-1][0] == "denial"


def test_a_governance_failure_that_is_not_a_policy_refusal_is_not_recorded_as_one(governed):
    _install(FakeAdapter())
    governed.deny = gov.GovernanceError("database unavailable")
    with pytest.raises(ProviderCallDenied, match="database unavailable"):
        _call()
    assert [e[0] for e in governed.events] == ["reserve"]                       # no invented denial


def test_a_refusal_that_cannot_be_recorded_is_reported_not_swallowed(governed):
    _install(FakeAdapter())
    governed.deny = gov.PolicyDenied("model 'x' is not on this organization's allowlist", code="model_not_allowed")
    governed.denial_error = gov.GovernanceError("db down")
    with pytest.raises(ProviderCallFailed, match="could not be recorded"):
        _call()


@pytest.mark.parametrize("kw,code", [
    (dict(provider="other"), "provider_not_allowed"), (dict(model="m2"), "model_not_allowed"),
    (dict(tool="submit_way"), "tool_not_allowed"), (dict(data_class="CONFIDENTIAL_DATA"), "data_class_not_allowed")])
def test_every_allowlist_refusal_carries_its_reason_code(kw, code):
    call = {"provider": "example", "model": "m1", "tool": "call_model", "data_class": "USER_PRIVATE", **kw}
    with pytest.raises(gov.PolicyDenied) as err:
        gov.evaluate(policy(), **call)
    assert err.value.code == code and code in gov.DENIAL_CODES


def test_the_kill_switch_refusal_has_its_own_code():
    with pytest.raises(gov.PolicyDenied) as err:
        gov.evaluate(policy(kill_switch=True, kill_switch_reason="x"), provider="example", model="m1",
                     tool="call_model", data_class="USER_PRIVATE")
    assert err.value.code == "kill_switch"


def test_cursors_must_be_what_a_page_returned():
    assert gov._cursor(None) is None and gov._cursor("") is None
    stamp = "2026-10-02T10:00:00.123456+00:00"
    ident = "0b0f6a0e-3a33-4c1c-9f3b-2f3f6f0a0001"
    assert gov._cursor(f"{stamp}|{ident}")[1] == ident
    for bad in ("garbage", f"{stamp}|not-a-uuid", f"nope|{ident}"):
        with pytest.raises(gov.Invalid, match="next_after"):
            gov._cursor(bad)


def test_the_new_readers_check_roles_ranges_and_filters_before_the_database():
    from datetime import datetime, timedelta
    since = datetime(2026, 10, 1)
    until = since + timedelta(days=1)
    common = dict(org_id=ORG, since=since, until=until)
    for fn in (gov.usage_by_user, gov.list_calls, gov.list_denials, gov.performance_summary):
        with pytest.raises(gov.NotAuthorized):
            run(fn(None, actor_roles={"member"}, **common))
    with pytest.raises(gov.NotAuthorized):
        run(gov.budget_status(None, actor_roles={"viewer"}, org_id=ORG))
    admin = dict(actor_roles={"admin"})
    with pytest.raises(gov.Invalid, match="after since"):
        run(gov.list_calls(None, **admin, org_id=ORG, since=until, until=since))
    with pytest.raises(gov.Invalid, match="366 days"):
        run(gov.list_denials(None, **admin, org_id=ORG, since=since, until=since + timedelta(days=400)))
    with pytest.raises(gov.Invalid, match="limit"):
        run(gov.list_calls(None, **admin, limit=0, **common))
    with pytest.raises(gov.Invalid, match="status"):
        run(gov.list_calls(None, **admin, status="done", **common))
    with pytest.raises(gov.Invalid, match="group_by"):
        run(gov.performance_summary(None, **admin, group_by="user; DROP TABLE x", **common))


def test_the_group_by_choices_are_fixed_column_names_never_caller_text():
    assert set(gov.PERFORMANCE_GROUPS) == {"total", "model", "provider", "tool", "tier"}
    assert all(v in (None, "model", "provider", "tool", "tier") for v in gov.PERFORMANCE_GROUPS.values())


def test_a_failed_call_releases_its_hold_and_the_error_propagates(governed):
    adapter = FakeAdapter(error=ProviderCallFailed("c1: HTTP 500"))
    _install(adapter)
    with pytest.raises(ProviderCallFailed, match="HTTP 500"):
        _call()
    assert [e[0] for e in governed.events] == ["reserve", "fail"]
    assert governed.events[1][1]["error_type"] == "ProviderCallFailed"


def test_a_hold_that_cannot_be_released_is_reported_not_swallowed(governed):
    adapter = FakeAdapter(error=ProviderCallFailed("c1: HTTP 500"))
    _install(adapter)
    governed.release_error = gov.GovernanceError("db down")
    with pytest.raises(ProviderCallFailed, match="could not be released"):
        _call()


def test_a_result_whose_cost_cannot_be_recorded_is_withheld(governed):
    _install(FakeAdapter())
    governed.settle_error = gov.GovernanceError("db down")
    with pytest.raises(ProviderCallFailed, match="result is withheld"):
        _call()


def test_a_governed_call_needs_an_identified_caller(governed):
    adapter = FakeAdapter()
    _install(adapter)
    with pytest.raises(ProviderCallDenied, match="identified caller"):
        _call(actor=None)
    assert adapter.calls == 0 and governed.events == []


def test_a_single_operator_deployment_is_not_governed_and_writes_no_ledger(governed):
    adapter = FakeAdapter()
    _install(adapter, _conn("platform"))
    out = _call(governed=False)
    assert out.text == "ok" and governed.events == []


# ------------------------------------------------------------------ cache-aware accounting

def _chat(prompt, completion, cached=None, cost=None):
    usage = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    if cost is not None:
        usage["cost"] = cost
    return {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": usage}


def _chat_call(monkeypatch, body, spec):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    monkeypatch.setattr(adapters, "http_client", lambda **kw: httpx.AsyncClient(transport=transport, **kw))
    conn = Connection(connection_id="c", kind="openai_compatible", base_url="https://api.example.com/v1", units=(spec,))
    return run(adapters.OpenAICompatAdapter().call(conn, spec, CallRequest(prompt="p"), None))


def test_cached_tokens_are_split_out_of_the_openai_style_prompt_count(monkeypatch):
    spec = UnitSpec(model="m", input_per_mtok=10.0, output_per_mtok=40.0, cached_input_per_mtok=1.0)
    out = _chat_call(monkeypatch, _chat(1000, 100, cached=800), spec)
    assert (out.tokens_in, out.tokens_cache_read, out.tokens_out) == (200, 800, 100)          # fresh, cache read, output
    assert out.cost_usd == pytest.approx((200 * 10 + 800 * 1 + 100 * 40) / 1e6) and out.cost_source == "declared"


def test_without_a_declared_cached_price_cache_reads_are_charged_in_full_never_less():
    spec = UnitSpec(model="m", input_per_mtok=10.0, output_per_mtok=40.0)
    assert tokens_cost(spec, 200, 100, 800) == pytest.approx((200 * 10 + 800 * 10 + 100 * 40) / 1e6)


def test_a_provider_reported_cost_is_used_and_labelled(monkeypatch):
    spec = UnitSpec(model="m", input_per_mtok=10.0, output_per_mtok=40.0)
    out = _chat_call(monkeypatch, _chat(1000, 100, cost=0.0123), spec)
    assert out.cost_usd == 0.0123 and out.cost_source == "provider" and out.tokens_in == 1000


def test_no_usage_means_no_cost_not_a_zero_cost(monkeypatch):
    spec = UnitSpec(model="m", input_per_mtok=10.0, output_per_mtok=40.0)
    body = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
    out = _chat_call(monkeypatch, body, spec)
    assert out.cost_usd is None and out.cost_source is None and out.tokens_in is None


# ------------------------------------------------------------------ the admin REST layer

def _human(roles: dict[str, set[str]]) -> AuthContext:
    return AuthContext(user_id="00000000-0000-0000-0000-0000000000aa", subject="u-admin",
                       org_ids=tuple(roles), org_roles={k: frozenset(v) for k, v in roles.items()})


@pytest.fixture
def api(monkeypatch):
    app = FastAPI()
    app.include_router(org_admin.mine_router)
    app.include_router(org_admin.router)
    app.state.pool = object()
    state = SimpleNamespace(ctx=_human({ORG: {"admin"}}))
    app.dependency_overrides[get_auth_context] = lambda: state.ctx
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    return SimpleNamespace(app=app, state=state, client=client)


def _req(api, method, path, **kw):
    return run(api.client.request(method, path, **kw))


def test_anonymous_services_and_non_members_are_turned_away(api):
    api.state.ctx = AnonymousContext()
    assert _req(api, "GET", f"/v1/orgs/{ORG}/policy").status_code == 401
    api.state.ctx = _human({OTHER: {"owner"}})
    assert _req(api, "GET", f"/v1/orgs/{ORG}/policy").status_code == 404                  # not revealed
    api.state.ctx = SimpleNamespace(kind="service")
    assert _req(api, "GET", f"/v1/orgs/{ORG}/policy").status_code == 401


def test_a_member_cannot_read_or_change_policy(api):
    api.state.ctx = _human({ORG: {"member"}})
    assert _req(api, "GET", f"/v1/orgs/{ORG}/policy").status_code == 403
    # the route lets a member reach the service, which refuses again on its own (the pool is never touched)
    r = _req(api, "PUT", f"/v1/orgs/{ORG}/policy", json={"policy": {"allowed_models": ["m1"]}})
    assert r.status_code == 403


def test_policy_errors_map_to_http_statuses(api, monkeypatch):
    async def missing(pool, org):
        raise gov.PolicyMissing("no policy yet")
    monkeypatch.setattr(gov, "load_policy", missing)
    r = _req(api, "GET", f"/v1/orgs/{ORG}/policy")
    assert r.status_code == 409 and "no policy" in r.json()["detail"]

    async def conflict(pool, **kw):
        raise gov.Conflict("version 3, not 2")
    monkeypatch.setattr(gov, "put_policy", conflict)
    r = _req(api, "PUT", f"/v1/orgs/{ORG}/policy", json={"expected_version": 2, "policy": {}})
    assert r.status_code == 409


def test_a_policy_write_carries_the_real_actor_and_roles(api, monkeypatch):
    seen = {}

    async def put(pool, **kw):
        seen.update(kw)
        return policy()
    monkeypatch.setattr(gov, "put_policy", put)
    r = _req(api, "PUT", f"/v1/orgs/{ORG}/policy", json={"expected_version": 1, "policy": {"allowed_models": ["m1"]}})
    assert r.status_code == 200 and r.json()["allowed_models"] == ["m1"] and r.json()["monthly_budget_usd"] == "10"
    assert (seen["actor_subject"], seen["org_id"], seen["expected_version"], set(seen["actor_roles"])) == (
        "u-admin", ORG, 1, {"admin"})


def test_the_export_needs_an_explicit_range_and_csv_carries_the_chain_result(api, monkeypatch):
    assert _req(api, "GET", f"/v1/orgs/{ORG}/audit").status_code == 422

    async def export(pool, **kw):
        return {"events": [{"id": 5, "t_created": "2026-10-01T00:00:00+00:00", "actor_subject": "u", "actor_user_id": None,
                            "action": "org_policy.put", "object_type": "org_policy", "object_id": ORG,
                            "tenant_id": ORG, "details": {"a": 1}, "prev_hash": "0" * 64, "row_hash": "f" * 64}],
                "next_after_id": 5, "chain_intact": True, "first_broken_id": None}
    monkeypatch.setattr(gov, "audit_export", export)
    r = _req(api, "GET", f"/v1/orgs/{ORG}/audit?since=2026-10-01&until=2026-11-01&limit=1&format=csv")
    assert r.status_code == 200 and r.headers["x-chain-intact"] == "true" and r.headers["x-next-after-id"] == "5"
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,t_created,actor_subject") and "org_policy.put" in lines[1]
    j = _req(api, "GET", f"/v1/orgs/{ORG}/audit?since=2026-10-01&until=2026-11-01&limit=1")
    assert j.json()["chain_intact"] is True and j.json()["events"][0]["id"] == 5


def test_a_broken_chain_is_visible_in_the_export(api, monkeypatch):
    async def export(pool, **kw):
        return {"events": [], "next_after_id": None, "chain_intact": False, "first_broken_id": 7}
    monkeypatch.setattr(gov, "audit_export", export)
    r = _req(api, "GET", f"/v1/orgs/{ORG}/audit?since=2026-10-01&until=2026-11-01")
    assert r.headers["x-chain-intact"] == "false" and r.json()["first_broken_id"] == 7


def test_erasure_reports_what_it_could_not_remove(api, monkeypatch):
    api.state.ctx = _human({ORG: {"owner"}})

    async def execute(pool, **kw):
        return {"request_id": "r1", "completed": False, "manifest": {"procedures": {"blocked": "trigger"}},
                "blocked": ["procedures"]}
    monkeypatch.setattr(gov, "execute_erasure", execute)
    r = _req(api, "POST", f"/v1/orgs/{ORG}/erasure-requests/r1/execute")
    assert r.status_code == 200 and r.json()["completed"] is False and r.json()["blocked"] == ["procedures"]


class _NamePool:
    def __init__(self, names):
        self.names, self.asked = names, []

    async def fetch(self, sql, ids):
        self.asked.append(list(ids))
        return [{"id": i, "name": self.names[i]} for i in ids if i in self.names]


def test_my_organizations_lists_member_orgs_with_the_highest_role(api):
    api.app.state.pool = _NamePool({ORG: "Zeta Corp", OTHER: "alpha labs"})
    api.state.ctx = _human({ORG: {"member", "admin"}, OTHER: {"viewer"}})
    r = _req(api, "GET", "/v1/orgs/mine")
    assert r.status_code == 200
    assert r.json() == [{"organization_id": OTHER, "name": "alpha labs", "role": "viewer", "roles": ["viewer"]},
                        {"organization_id": ORG, "name": "Zeta Corp", "role": "admin", "roles": ["admin", "member"]}]


def test_my_organizations_for_a_person_with_none_is_empty_and_does_not_query(api):
    api.app.state.pool = _NamePool({})
    api.state.ctx = _human({})
    r = _req(api, "GET", "/v1/orgs/mine")
    assert r.status_code == 200 and r.json() == [] and api.app.state.pool.asked == []


def test_my_organizations_leaves_out_an_organization_that_no_longer_exists(api):
    api.app.state.pool = _NamePool({ORG: "Kept"})
    api.state.ctx = _human({ORG: {"owner"}, OTHER: {"owner"}})
    assert [o["organization_id"] for o in _req(api, "GET", "/v1/orgs/mine").json()] == [ORG]


def test_my_organizations_needs_a_person(api):
    api.state.ctx = AnonymousContext()
    assert _req(api, "GET", "/v1/orgs/mine").status_code == 401
    api.state.ctx = SimpleNamespace(kind="service")
    assert _req(api, "GET", "/v1/orgs/mine").status_code == 401


def test_performance_is_admin_only_needs_a_range_and_passes_rows_through(api, monkeypatch):
    api.state.ctx = _human({ORG: {"member"}})
    assert _req(api, "GET", f"/v1/orgs/{ORG}/performance?since=2026-10-01&until=2026-11-01").status_code == 403
    api.state.ctx = _human({ORG: {"admin"}})
    assert _req(api, "GET", f"/v1/orgs/{ORG}/performance").status_code == 422

    async def rows(pool, **kw):
        return [{"organization_id": ORG, "day": "2026-10-02", "model": "m1", "tier": "light", "provider_p95_ms": 290.0,
                 "gate_p95_ms": 14.5, "cache_hit_rate_tokens": None}]
    monkeypatch.setattr(gov, "performance_daily", rows)
    r = _req(api, "GET", f"/v1/orgs/{ORG}/performance?since=2026-10-01&until=2026-11-01")
    assert r.status_code == 200 and r.json()["rows"][0]["cache_hit_rate_tokens"] is None


def test_members_are_admin_only_and_pass_through(api, monkeypatch):
    api.state.ctx = _human({ORG: {"member"}})
    assert _req(api, "GET", f"/v1/orgs/{ORG}/members").status_code == 403

    async def rows(pool, **kw):
        return [{"user_id": "x", "subject": "sub-1", "display_name": "Ada", "email": "a@b.c", "is_active": True,
                 "roles": ["owner", "member"], "member_since": "2026-10-01T00:00:00+00:00"}]
    monkeypatch.setattr(gov, "list_members", rows)
    api.state.ctx = _human({ORG: {"admin"}})
    r = _req(api, "GET", f"/v1/orgs/{ORG}/members")
    assert r.status_code == 200 and r.json()["members"][0]["subject"] == "sub-1"


def test_the_new_admin_routes_are_admin_only_and_need_an_explicit_range(api, monkeypatch):
    rng = "since=2026-10-01&until=2026-10-08"
    api.state.ctx = _human({ORG: {"member"}})
    for path in ("usage/users?" + rng, "calls?" + rng, "denials?" + rng, "performance/summary?" + rng, "budget"):
        assert _req(api, "GET", f"/v1/orgs/{ORG}/{path}").status_code == 403, path
    api.state.ctx = _human({ORG: {"admin"}})
    for path in ("usage/users", "calls", "denials", "performance/summary"):
        assert _req(api, "GET", f"/v1/orgs/{ORG}/{path}").status_code == 422, path


def test_calls_passes_filters_and_the_cursor_through_and_returns_the_next_one(api, monkeypatch):
    seen = {}

    async def calls(pool, **kw):
        seen.update(kw)
        return {"calls": [{"id": "c1", "created_at": "2026-10-02T00:00:00+00:00", "user_id": "u1", "model": "m1",
                           "tool": "call_model", "status": "settled", "policy_decision": "allowed"}],
                "next_after": "2026-10-02T00:00:00+00:00|c1"}
    monkeypatch.setattr(gov, "list_calls", calls)
    r = _req(api, "GET", f"/v1/orgs/{ORG}/calls?since=2026-10-01&until=2026-10-08&model=m1&tool=call_model"
                         "&status=settled&user=u1&limit=50&after=abc")
    assert r.status_code == 200 and r.json()["next_after"].endswith("|c1")
    assert (seen["model"], seen["tool"], seen["status"], seen["user"], seen["limit"], seen["after"]) == (
        "m1", "call_model", "settled", "u1", 50, "abc")
    assert "prompt" not in str(r.json()) and "response" not in str(r.json())


def test_denials_budget_and_summary_map_service_errors(api, monkeypatch):
    async def missing(pool, **kw):
        raise gov.PolicyMissing("no policy yet")
    monkeypatch.setattr(gov, "budget_status", missing)
    assert _req(api, "GET", f"/v1/orgs/{ORG}/budget").status_code == 409

    async def denials(pool, **kw):
        return {"counts_by_reason": [{"reason_code": "kill_switch", "count": 3}], "events": [], "next_after": None}
    monkeypatch.setattr(gov, "list_denials", denials)
    r = _req(api, "GET", f"/v1/orgs/{ORG}/denials?since=2026-10-01&until=2026-10-08")
    assert r.json()["counts_by_reason"] == [{"reason_code": "kill_switch", "count": 3}]
    r = _req(api, "GET", f"/v1/orgs/{ORG}/performance/summary?since=2026-10-01&until=2026-10-08&group_by=nonsense")
    assert r.status_code == 422                                                       # the real service validates it
    r = _req(api, "GET", f"/v1/orgs/{ORG}/performance/summary?since=2024-01-01&until=2026-10-08")
    assert r.status_code == 422 and "366" in r.json()["detail"]


def test_a_non_owner_cannot_request_erasure_through_the_api(api):
    r = _req(api, "POST", f"/v1/orgs/{ORG}/erasure-requests", json={"reason": "contract ended"})
    assert r.status_code == 403
