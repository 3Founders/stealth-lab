"""Phase 1 (launch compliance) — the ONE authentication dependency.

Offline (no DB, no network): a FakePool routes authn.py's user /
membership statement shapes; the validated identity is placed on the
contextvar exactly as the ASGI middleware would after full JWT
verification.

Covers: unauthenticated request rejected (401); a validated user is
accepted and its identity is server-derived (token subject + provisioned
users row), never a request field; org memberships resolve into the
access scope BEFORE any ranking; a deactivated account is refused (403);
the dependency is structurally incapable of reading a request body;
`optional_authenticated_user` degrades to None instead of 401.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.api.deps import (
    AuthenticatedPrincipal,
    optional_authenticated_user,
    require_authenticated_user,
)
from app.services.access import AccessScope
from app.services.authn import Actor, reset_current_actor, set_current_actor


# --------------------------------------------------------------- fakes


class FakeIdentityPool:
    """Routes authn.py's three statement shapes (see test_hardening_h1)."""

    def __init__(self, *, user_row=None, insert_return=None, membership_rows=()):
        self.user_row = user_row
        self.insert_return = insert_return
        self.membership_rows = list(membership_rows)

    async def fetchrow(self, sql, *params):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT id, is_active"):
            return self.user_row
        if flat.startswith("INSERT INTO users"):
            return self.insert_return
        raise AssertionError(f"unexpected fetchrow: {flat[:80]}")

    async def fetch(self, sql, *params):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT m.organization_id"):
            return self.membership_rows
        raise AssertionError(f"unexpected fetch: {flat[:80]}")


def _request(pool):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool)))


ACTOR = Actor(
    subject="supabase-uid-abc",
    issuer="https://proj.supabase.co/auth/v1",
    email="user@example.com",
    name="Real User",
)


def _user_row(user_id="u-1", active=True, expired=None):
    return {"id": user_id, "is_active": active, "t_expired": expired}


def _membership_row(org_id):
    return {
        "organization_id": org_id,
        "organization_name": f"Org {org_id}",
        "organization_slug": f"org-{org_id}",
        "role_name": "member",
    }


class _actor_on_cv:
    def __init__(self, actor):
        self.actor = actor

    def __enter__(self):
        self._tok = set_current_actor(self.actor)

    def __exit__(self, *exc):
        reset_current_actor(self._tok)


# --------------------------------------------------------------- tests


def test_unauthenticated_request_is_rejected_401():
    from fastapi import HTTPException

    with _actor_on_cv(None):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(require_authenticated_user(_request(FakeIdentityPool())))
    assert ei.value.status_code == 401
    assert ei.value.headers.get("WWW-Authenticate") == "Bearer"


def test_validated_user_is_accepted_and_identity_is_server_derived():
    pool = FakeIdentityPool(user_row=_user_row("u-42"))
    with _actor_on_cv(ACTOR):
        principal = asyncio.run(require_authenticated_user(_request(pool)))
    assert isinstance(principal, AuthenticatedPrincipal)
    # user_id is the provisioned users row id, NOT the raw token subject
    assert principal.user_id == "u-42"
    assert principal.subject == "supabase-uid-abc"
    assert principal.email == "user@example.com"
    assert principal.org_ids == ()


def test_no_memberships_scopes_to_the_user_only():
    pool = FakeIdentityPool(user_row=_user_row("u-1"))
    with _actor_on_cv(ACTOR):
        principal = asyncio.run(require_authenticated_user(_request(pool)))
    scope = principal.access_scope()
    assert scope == AccessScope.for_user("supabase-uid-abc")
    sql, _ = _predicate(scope)
    assert "'org'" not in sql


def test_org_membership_resolves_into_scope_before_ranking():
    pool = FakeIdentityPool(
        user_row=_user_row("u-1"),
        membership_rows=[_membership_row("org-A"), _membership_row("org-A")],
    )
    with _actor_on_cv(ACTOR):
        principal = asyncio.run(require_authenticated_user(_request(pool)))
    assert principal.org_ids == ("org-A",)
    scope = principal.access_scope()
    sql, params = _predicate(scope)
    assert "visibility = 'org'" in sql
    assert "org-A" in params


def test_deactivated_account_is_refused_403():
    from fastapi import HTTPException

    pool = FakeIdentityPool(user_row=_user_row("u-1", active=False))
    with _actor_on_cv(ACTOR):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(require_authenticated_user(_request(pool)))
    assert ei.value.status_code == 403


def test_dependency_cannot_read_a_request_body():
    # Structural proof: the only parameter is `request`. There is no
    # body / model / Form / Header parameter through which a client could
    # assert an identity field.
    params = list(inspect.signature(require_authenticated_user).parameters)
    assert params == ["request"]


def test_optional_dependency_returns_none_when_unauthenticated():
    with _actor_on_cv(None):
        got = asyncio.run(optional_authenticated_user(_request(FakeIdentityPool())))
    assert got is None


def _predicate(scope):
    from app.services.access import visibility_predicate

    return visibility_predicate(scope, param_index=1)
