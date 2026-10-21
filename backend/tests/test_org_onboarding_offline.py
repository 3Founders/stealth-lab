"""Org-held provider keys and developer onboarding, offline: encryption of the key, validation of a connection, who may do
what before the database is touched, the invite code, the recommended policy, the REST layer, and the registry store /
secret resolver that hand the key to the governed gateway. Row-level security and the invite's single-use update are
database behaviour (migration 151) and are not claimed here."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api import org_admin
from app.api.deps import get_auth_context
from app.config import settings
from app.providers import registry
from app.providers.secrets import resolve_secret, unregister_secret_resolver
from app.providers.types import ProviderCallDenied
from app.services import org_governance as gov
from app.services import org_keys, org_onboarding, secret_box
from app.services.access import AccessScope
from app.services.auth_context import AnonymousContext, AuthContext

ORG = "0b0f6a0e-3a33-4c1c-9f3b-2f3f6f0a0001"
OTHER = "0b0f6a0e-3a33-4c1c-9f3b-2f3f6f0a0002"
KEY = base64.b64encode(b"k" * 32).decode()
UNITS = [{"model": "gemma-4-31b-it", "provider_model": "gemma-4-31B-it", "input_per_mtok": 0.09, "output_per_mtok": 0.34}]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setattr(settings, "org_key_encryption_key", KEY, raising=False)
    monkeypatch.setattr(settings, "public_api_url", "https://api.example.com", raising=False)
    monkeypatch.setattr(registry, "_STORES", {})
    yield
    unregister_secret_resolver(org_keys.SCHEME)


class FakeConn:
    """Answers by SQL text; remembers every statement and its arguments."""

    def __init__(self, answers):
        self.answers, self.log = answers, []

    def _find(self, sql):
        for needle, value in self.answers.items():
            if needle in sql:
                return value() if callable(value) else value
        return None

    async def execute(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    async def fetch(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql) or []

    async def fetchrow(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    async def fetchval(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()


class FakePool:
    def __init__(self, answers=None):
        self.conn = FakeConn(answers or {})

    @asynccontextmanager
    async def acquire(self):
        yield self.conn

    def statements(self, needle):
        return [(s, a) for s, a in self.conn.log if needle in s]


# ------------------------------------------------------------------ the key is encrypted and bound to its row

def test_a_sealed_key_round_trips_and_is_not_the_plaintext():
    token = secret_box.seal("sk-live-abcdef123456", org_id=ORG, connection_id="gc")
    assert token.startswith("v1.") and "abcdef" not in token
    assert secret_box.open_sealed(token, org_id=ORG, connection_id="gc") == "sk-live-abcdef123456"


def test_a_ciphertext_moved_to_another_row_or_organisation_does_not_decrypt():
    token = secret_box.seal("sk-live-abcdef123456", org_id=ORG, connection_id="gc")
    with pytest.raises(secret_box.SecretBoxError):
        secret_box.open_sealed(token, org_id=OTHER, connection_id="gc")
    with pytest.raises(secret_box.SecretBoxError):
        secret_box.open_sealed(token, org_id=ORG, connection_id="other")


def test_every_seal_uses_a_fresh_iv():
    assert secret_box.seal("x" * 20, org_id=ORG, connection_id="gc") != secret_box.seal("x" * 20, org_id=ORG, connection_id="gc")


@pytest.mark.parametrize("bad", [None, "", "not base64!!", base64.b64encode(b"short").decode()])
def test_without_a_valid_encryption_key_nothing_is_stored_or_read(monkeypatch, bad):
    monkeypatch.setattr(settings, "org_key_encryption_key", bad, raising=False)
    with pytest.raises(secret_box.SecretBoxError):
        secret_box.seal("sk", org_id=ORG, connection_id="gc")


def test_the_hint_reveals_only_the_last_four_characters_of_a_real_key():
    assert secret_box.hint("sk-live-abcdef123456") == "...3456"
    assert secret_box.hint("short") == "..."


# ------------------------------------------------------------------ validation and roles, before any database

def _put(pool, roles, **kw):
    args = dict(actor_subject="u", actor_user_id=None, actor_roles=roles, org_id=ORG, connection_id="gc",
                provider="general-compute", base_url="https://api.generalcompute.com/v1", units=UNITS, api_key="sk-live-abcdef123456")
    args.update(kw)
    return run(org_keys.put_provider_key(pool, **args))


@pytest.mark.parametrize("roles", [{"member"}, {"viewer"}, set()])
def test_only_owners_and_admins_may_connect_a_key(roles):
    with pytest.raises(gov.NotAuthorized):
        _put(None, roles)


@pytest.mark.parametrize("kw,needle", [
    ({"base_url": "http://api.example.com/v1"}, "https"),
    ({"connection_id": "Bad Id"}, "connection_id"),
    ({"provider": " "}, "provider"),
    ({"units": []}, "units"),
    ({"units": [{"model": "m"}]}, "input_per_mtok"),
    ({"data_classes": ["*"]}, "wildcard"),
    ({"api_key": "  "}, "api_key is empty")])
def test_a_bad_connection_is_refused_with_a_message_an_admin_can_act_on(kw, needle):
    with pytest.raises(gov.Invalid, match=needle):
        _put(None, {"admin"}, **kw)


def test_a_new_connection_needs_a_key_and_stores_only_ciphertext_and_a_hint():
    pool = FakePool({"SELECT id FROM org_provider_keys": None, "SELECT * FROM org_provider_keys": lambda: {
        "connection_id": "gc", "provider": "general-compute", "base_url": "https://api.generalcompute.com/v1",
        "units": UNITS, "allowed_data_classes": ["USER_PRIVATE"], "key_hint": "...3456", "enabled": True,
        "updated_by": "u", "updated_at": datetime.now(timezone.utc)},
        "INSERT INTO audit_events": {"id": 1}})
    with pytest.raises(gov.Invalid, match="api_key is required"):
        _put(FakePool({"SELECT id FROM org_provider_keys": None}), {"admin"}, api_key=None)
    out = _put(pool, {"owner"})
    assert out["key_hint"] == "...3456" and "api_key" not in out and "secret" not in json.dumps(out)
    insert = pool.statements("INSERT INTO org_provider_keys")[0][1]
    assert any(isinstance(a, str) and a.startswith("v1.") for a in insert)
    assert "sk-live-abcdef123456" not in json.dumps(insert, default=str)
    audit = pool.statements("INSERT INTO audit_events")[0][1]
    assert "sk-live-abcdef123456" not in json.dumps(audit, default=str)


def test_removing_a_connection_that_is_not_there_is_not_found():
    with pytest.raises(gov.NotFound):
        run(org_keys.delete_provider_key(FakePool({"DELETE FROM org_provider_keys": None}), actor_subject="u",
                                         actor_user_id=None, actor_roles={"admin"}, org_id=ORG, connection_id="gc"))


# ------------------------------------------------------------------ the gateway sees the org's key, only the org's members

def _row(cid="gc", provider="general-compute"):
    return {"connection_id": cid, "provider": provider, "base_url": "https://api.generalcompute.com/v1", "units": UNITS,
            "allowed_data_classes": ["USER_PRIVATE"], "enabled": True}


def test_members_see_their_organisations_connection_owned_by_that_org():
    store = org_keys.OrgKeyStore(lambda: FakePool({"FROM org_provider_keys": [_row()]}))
    conns = run(store.list_connections(AccessScope.for_org_member("u", [ORG])))
    assert [c.connection_id for c in conns] == ["gc"]
    assert conns[0].owner == f"org:{ORG}" and conns[0].credential_ref == f"orgkey:{ORG}:gc"
    assert conns[0].credential_owner == "customer" and conns[0].allowed_data_classes == ("USER_PRIVATE",)
    assert registry.visible(conns[0], AccessScope.for_org_member("u", [ORG]))
    assert not registry.visible(conns[0], AccessScope.for_org_member("u", [OTHER]))


def test_a_caller_without_an_organisation_or_an_internal_scope_sees_none():
    store = org_keys.OrgKeyStore(lambda: FakePool({"FROM org_provider_keys": [_row()]}))
    assert run(store.list_connections(AccessScope.for_user("u"))) == []
    assert run(org_keys.OrgKeyStore(lambda: None).list_connections(AccessScope.for_org_member("u", [ORG]))) == []


def test_the_resolver_decrypts_the_stored_key_for_that_row_only():
    sealed = secret_box.seal("sk-live-abcdef123456", org_id=ORG, connection_id="gc")
    resolver = org_keys.OrgKeyResolver(lambda: FakePool({"secret_ciphertext": {"secret_ciphertext": sealed}}))
    assert run(resolver.resolve(f"orgkey:{ORG}:gc")) == "sk-live-abcdef123456"
    with pytest.raises(ProviderCallDenied):
        run(resolver.resolve(f"orgkey:{ORG}:other"))                     # the AAD no longer matches
    with pytest.raises(ProviderCallDenied, match="not stored"):
        run(org_keys.OrgKeyResolver(lambda: FakePool({})).resolve(f"orgkey:{ORG}:gc"))
    with pytest.raises(ProviderCallDenied):
        run(org_keys.OrgKeyResolver(lambda: None).resolve(f"orgkey:{ORG}:gc"))


def test_install_registers_the_store_and_the_orgkey_scheme():
    sealed = secret_box.seal("sk-live-abcdef123456", org_id=ORG, connection_id="gc")
    org_keys.install(lambda: FakePool({"secret_ciphertext": {"secret_ciphertext": sealed}}))
    assert "org_keys" in registry._STORES
    assert run(resolve_secret(f"orgkey:{ORG}:gc")) == "sk-live-abcdef123456"


# ------------------------------------------------------------------ invites

def test_an_invite_code_names_its_organisation_and_malformed_codes_are_not_found():
    pool = FakePool({"INSERT INTO org_invites": {"id": "i1", "created_at": datetime.now(timezone.utc)},
                     "INSERT INTO audit_events": {"id": 1}})
    out = run(org_onboarding.create_invite(pool, actor_subject="u", actor_user_id=None, actor_roles={"admin"},
                                           org_id=ORG, label="pilot", days=7, max_uses=50))
    assert org_onboarding.parse_code(out["code"])[0] == ORG
    stored = pool.statements("INSERT INTO org_invites")[0][1]
    assert out["code"].rsplit("_", 1)[1] not in stored and org_onboarding._hash(out["code"].rsplit("_", 1)[1]) in stored
    for bad in ("", "nope", "sl1_zz_yy", out["code"] + "x", out["code"][:-1]):
        with pytest.raises(gov.NotFound):
            org_onboarding.parse_code(bad)


@pytest.mark.parametrize("kw", [{"days": 0}, {"days": 31}, {"max_uses": 0}, {"max_uses": 5001}, {"days": "7"}])
def test_invite_limits_are_explicit(kw):
    base = dict(actor_subject="u", actor_user_id=None, actor_roles={"admin"}, org_id=ORG, label=None, days=7, max_uses=5)
    with pytest.raises(gov.Invalid):
        run(org_onboarding.create_invite(None, **{**base, **kw}))


def test_members_cannot_create_or_list_invites():
    with pytest.raises(gov.NotAuthorized):
        run(org_onboarding.create_invite(None, actor_subject="u", actor_user_id=None, actor_roles={"member"}, org_id=ORG,
                                         label=None, days=7, max_uses=5))
    with pytest.raises(gov.NotAuthorized):
        run(org_onboarding.list_invites(None, actor_roles={"member"}, org_id=ORG))


def _ctx():
    return AuthContext(user_id="00000000-0000-0000-0000-0000000000bb", subject="dev-1")


def _code():
    return f"sl1_{ORG.replace('-', '')}_{'ab' * 12}"


def test_redeeming_adds_a_member_once_and_a_second_redeem_by_the_same_person_uses_no_invite():
    pool = FakePool({"FROM org_invites": {"id": "i1"}, "FROM organizations": {"name": "Acme"}, "FROM org_memberships": None,
                     "INSERT INTO audit_events": {"id": 1}})
    out = run(org_onboarding.redeem_invite(pool, _ctx(), _code()))
    assert out == {"organization_id": ORG, "name": "Acme", "already_member": False}
    assert len(pool.statements("UPDATE org_invites SET uses")) == 1
    assert pool.statements("INSERT INTO org_memberships")[0][1][2] == org_onboarding.MEMBER_ROLE_ID
    again = FakePool({"FROM org_invites": {"id": "i1"}, "FROM organizations": {"name": "Acme"}, "FROM org_memberships": 1})
    assert run(org_onboarding.redeem_invite(again, _ctx(), _code()))["already_member"] is True
    assert not again.statements("UPDATE org_invites SET uses") and not again.statements("INSERT INTO org_memberships")


def test_an_unusable_invite_gets_the_same_answer_as_an_unknown_one():
    with pytest.raises(gov.NotFound, match="not valid"):
        run(org_onboarding.redeem_invite(FakePool({"FROM org_invites": None}), _ctx(), _code()))
    with pytest.raises(gov.NotFound, match="not valid"):
        run(org_onboarding.redeem_invite(FakePool({}), _ctx(), "garbage"))
    query = FakePool({"FROM org_invites": None})
    with pytest.raises(gov.NotFound):
        run(org_onboarding.redeem_invite(query, _ctx(), _code()))
    sql = query.statements("FROM org_invites")[0][0]
    assert "revoked_at IS NULL" in sql and "expires_at > now()" in sql and "uses < max_uses" in sql


# ------------------------------------------------------------------ what the machine is told, and the policy

def test_the_client_config_holds_models_and_prices_but_no_credential():
    out = run(org_onboarding.client_config(FakePool({"FROM org_provider_keys": [{"provider": "gc", "units": UNITS}],
                                                     "FROM organizations": "Acme"}), ORG))
    p = out["profiles"]["gemma-4-31b-it"]
    assert out["gateway"] == "https://api.example.com/v1" and p["openai_base_url"] == "https://api.example.com/v1"
    assert p["key_from"] == "stealthlab" and "key" not in json.dumps(out).replace("key_from", "")


def test_the_client_config_needs_a_key_and_a_public_address(monkeypatch):
    with pytest.raises(gov.Conflict, match="not connected"):
        run(org_onboarding.client_config(FakePool({"FROM organizations": "Acme"}), ORG))
    monkeypatch.setattr(settings, "public_api_url", None, raising=False)
    with pytest.raises(gov.Invalid, match="PUBLIC_API_URL"):
        run(org_onboarding.client_config(FakePool({}), ORG))


def test_the_recommended_policy_lists_exactly_what_was_connected_with_the_typed_budgets():
    keys = [{"provider": "b", "units": [{"model": "m2"}], "allowed_data_classes": ["USER_PRIVATE"]},
            {"provider": "a", "units": [{"model": "m1"}, {"model": "m2"}], "allowed_data_classes": ["USER_PRIVATE", "GLOBAL_PROCEDURE"]}]
    p = org_onboarding.recommended_policy(keys, "500", "20")
    assert p["allowed_providers"] == ["a", "b"] and p["allowed_models"] == ["m1", "m2"]
    assert p["allowed_tools"] == ["chat_completions", "call_model"] and p["allowed_data_classes"] == ["GLOBAL_PROCEDURE", "USER_PRIVATE"]
    assert (p["monthly_budget_usd"], p["per_user_daily_budget_usd"]) == ("500", "20")
    with pytest.raises(gov.Conflict):
        org_onboarding.recommended_policy([], "500", "20")


def test_the_policy_it_builds_passes_the_governance_validation():
    keys = [{"provider": "a", "units": [{"model": "m1"}], "allowed_data_classes": ["USER_PRIVATE"]}]
    changes = org_onboarding.recommended_policy(keys, "500", "20")
    gov.evaluate(gov.OrgPolicy(ORG, False, None, tuple(changes["allowed_providers"]), tuple(changes["allowed_models"]),
                               tuple(changes["allowed_tools"]), tuple(changes["allowed_data_classes"]), gov._clean_money("m", "500"),
                               gov._clean_money("d", "20"), 1),
                 provider="a", model="m1", tool="chat_completions", data_class="USER_PRIVATE")


def test_onboarding_status_walks_the_admin_through_the_steps():
    def status(keys, invites, ready, joined, policy):
        pool = FakePool({"FROM org_provider_keys": keys, "FROM org_invites": invites, "FROM provider_call_ledger": ready,
                         "FROM org_memberships": joined})
        return run(org_onboarding.onboarding_status(pool, actor_roles={"admin"}, org_id=ORG))["next_step"]
    # no policy row exists in the fake, so a connected key stops at set_policy
    assert status(0, 0, 0, 1, None) == "connect_key"
    assert status(1, 0, 0, 1, None) == "set_policy"
    with pytest.raises(gov.NotAuthorized):
        run(org_onboarding.onboarding_status(None, actor_roles={"member"}, org_id=ORG))


# ------------------------------------------------------------------ the REST layer

def _human(roles):
    return AuthContext(user_id="00000000-0000-0000-0000-0000000000aa", subject="u-admin", org_ids=tuple(roles),
                       org_roles={k: frozenset(v) for k, v in roles.items()})


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(org_admin.router)
    app.include_router(org_admin.join_router)
    app.state.pool = FakePool({})
    state = SimpleNamespace(ctx=_human({ORG: {"admin"}}))
    app.dependency_overrides[get_auth_context] = lambda: state.ctx
    return SimpleNamespace(app=app, state=state,
                           client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t"))


def _req(api, method, path, **kw):
    return run(api.client.request(method, path, **kw))


def test_the_key_routes_never_return_a_key_and_turn_away_non_admins(api):
    body = {"provider": "gc", "base_url": "https://api.generalcompute.com/v1", "units": UNITS, "api_key": "sk-live-abcdef123456"}
    api.state.ctx = _human({ORG: {"member"}})
    assert _req(api, "PUT", f"/v1/orgs/{ORG}/provider-keys/gc", json=body).status_code == 403
    assert _req(api, "GET", f"/v1/orgs/{ORG}/provider-keys").status_code == 403
    api.state.ctx = _human({OTHER: {"owner"}})
    assert _req(api, "PUT", f"/v1/orgs/{ORG}/provider-keys/gc", json=body).status_code == 404
    api.state.ctx = _human({ORG: {"admin"}})
    r = _req(api, "PUT", f"/v1/orgs/{ORG}/provider-keys/gc", json={**body, "base_url": "http://x"})
    assert r.status_code == 422 and "https" in r.text and "sk-live" not in r.text


def test_any_member_reads_the_client_config_but_only_admins_see_onboarding(api):
    api.state.ctx = _human({ORG: {"member"}})
    assert _req(api, "GET", f"/v1/orgs/{ORG}/onboarding").status_code == 403
    assert _req(api, "POST", f"/v1/orgs/{ORG}/invites", json={}).status_code == 403
    assert _req(api, "GET", f"/v1/orgs/{ORG}/client-config").status_code == 409        # no key connected yet, not 403


def test_joining_needs_a_signed_in_person_and_an_invite_that_works(api):
    api.state.ctx = AnonymousContext()
    assert _req(api, "POST", "/v1/join", json={"code": _code()}).status_code == 401
    api.state.ctx = _ctx()
    r = _req(api, "POST", "/v1/join", json={"code": "garbage"})
    assert r.status_code == 404 and "not valid" in r.text
    assert _req(api, "POST", "/v1/join", json={"code": _code()}).status_code == 404     # the fake finds no invite
