"""Phase 2 (launch compliance) — centralized authorization / scope.

Offline proofs that authorization is decided from the SERVER-derived
principal, before ranking, and that no request-supplied identity field
(header, body, query) can redirect it.

The DB-backed cross-user / cross-org isolation proofs live in
test_cross_user_isolation_e2e.py and test_product_model_privacy_e2e.py.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from app.api import procedures as proc_api
from app.api.deps import get_scope
from app.services.access import AccessScope, visibility_predicate
from app.services.authn import Actor, reset_current_actor, set_current_actor


class _actor:
    def __init__(self, a):
        self.a = a

    def __enter__(self):
        self._t = set_current_actor(self.a)

    def __exit__(self, *e):
        reset_current_actor(self._t)


def _req():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=None)))


# ---------------------------------------------------- impersonation defense


def test_validated_actor_beats_a_spoofed_x_viewer_id_header():
    with _actor(Actor(subject="real-user")):
        scope = asyncio.run(get_scope(_req(), x_viewer_id="victim"))
    assert scope.viewer_id == "real-user"


def test_x_viewer_id_is_ignored_once_supabase_is_configured(monkeypatch):
    # The header names a user only in the fully-public dev posture. With
    # Supabase Auth configured, an unauthenticated request is anonymous —
    # so a private row cannot be read by spoofing its owner's subject.
    import app.api.deps as deps

    monkeypatch.setattr(
        deps.settings, "supabase_project_url", "https://p.supabase.co", raising=False
    )
    monkeypatch.setattr(
        deps.settings, "supabase_jwt_audience", "authenticated", raising=False
    )
    with _actor(None):
        scope = asyncio.run(get_scope(_req(), x_viewer_id="victim"))
    assert scope.viewer_id is None
    assert scope == AccessScope.anonymous()


def test_x_viewer_id_still_works_in_the_fully_public_posture(monkeypatch):
    import app.api.deps as deps

    for f in ("supabase_project_url", "supabase_jwt_audience", "oidc_issuer", "oidc_audience"):
        monkeypatch.setattr(deps.settings, f, None, raising=False)
    with _actor(None):
        scope = asyncio.run(get_scope(_req(), x_viewer_id="dev-alice"))
    assert scope.viewer_id == "dev-alice"


# ---------------------------------------------------- access before ranking


def test_anonymous_scope_is_public_only():
    sql, params = visibility_predicate(AccessScope.anonymous())
    assert sql == "visibility = 'public'"
    assert params == []


def test_owner_scope_is_a_predicate_bound_to_the_viewer_not_a_postfilter():
    # A signed-in viewer sees public rows OR their own — the viewer id is a
    # bound parameter, so a different viewer literally cannot match another
    # viewer's private rows.
    a_sql, a_params = visibility_predicate(AccessScope.for_user("alice"), param_index=1)
    b_sql, b_params = visibility_predicate(AccessScope.for_user("bob"), param_index=1)
    assert "owner_id = $1" in a_sql and a_params == ["alice"]
    assert b_params == ["bob"]
    assert "visibility = 'public'" in a_sql  # global commons preserved


# ---------------------------------------------------- IDOR / no client identity


def test_create_bodies_carry_no_identity_or_scope_fields():
    forbidden = {
        "owner_id", "user_id", "organization_id", "org_id", "tenant_id",
        "repository_id", "scope", "scope_type", "scope_entity_id", "visibility",
        "created_by", "provenance",
    }
    for model in (proc_api.ProcedureCreateBody, proc_api.ProcedureFromTextBody):
        fields = set(model.model_fields)
        assert not (fields & forbidden), f"{model.__name__} exposes {fields & forbidden}"


def test_write_endpoints_take_only_body_pool_and_the_auth_dependency():
    for fn in (proc_api.create_procedure, proc_api.create_procedure_from_text):
        params = list(inspect.signature(fn).parameters)
        assert params == ["body", "pool", "principal"]
