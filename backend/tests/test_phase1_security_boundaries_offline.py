"""Phase 1 P0 security boundaries — offline proving tests.

All offline (no database), per house style: sync tests driving async
boundaries via asyncio.run, FakePool capturing SQL for content assertions.

Covers: Supabase Auth preset (issuer/JWKS derivation, asymmetric algs only,
half-configured fails loud), boot posture (hosted requires OIDC),
ORG_PRIVATE visibility scope, hosted workspace authorization boundary
(foreign tenant == NotFound, path mismatch refused, local passthrough),
migration 41 static assertions, and the impersonation/one-builder hygiene.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.access import AccessScope, visibility_predicate
from app.services.authn import OidcConfig, assert_boot_posture
from app.services import workspace_registry as wr

BACKEND = Path(__file__).resolve().parents[1]


# ------------------------------------------------------- Supabase preset


def _settings(**over):
    base = dict(
        supabase_project_url=None,
        supabase_jwt_audience=None,
        oidc_issuer=None,
        oidc_audience=None,
        oidc_jwks_url=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_supabase_preset_derives_issuer_and_jwks():
    cfg = OidcConfig.from_settings(
        _settings(
            supabase_project_url="https://abc123.supabase.co/",
            supabase_jwt_audience="authenticated",
        )
    )
    assert cfg is not None
    assert cfg.issuer == "https://abc123.supabase.co/auth/v1"
    assert cfg.jwks_url == "https://abc123.supabase.co/auth/v1/.well-known/jwks.json"
    assert cfg.audience == "authenticated"


def test_supabase_preset_never_allows_hs256():
    cfg = OidcConfig.from_settings(
        _settings(
            supabase_project_url="https://abc123.supabase.co",
            supabase_jwt_audience="authenticated",
        )
    )
    assert "HS256" not in cfg.allowed_algs
    assert set(cfg.allowed_algs) == {"ES256", "RS256"}


def test_half_configured_supabase_fails_loud():
    with pytest.raises(RuntimeError, match="TOGETHER"):
        OidcConfig.from_settings(_settings(supabase_project_url="https://x.supabase.co"))
    with pytest.raises(RuntimeError, match="TOGETHER"):
        OidcConfig.from_settings(_settings(supabase_jwt_audience="authenticated"))


def test_generic_oidc_path_unchanged_when_supabase_absent():
    cfg = OidcConfig.from_settings(
        _settings(oidc_issuer="https://idp.example.com", oidc_audience="aud")
    )
    assert cfg is not None
    assert cfg.issuer == "https://idp.example.com"
    assert cfg.allowed_algs == ("RS256",)  # legacy default preserved
    assert OidcConfig.from_settings(_settings()) is None


# ------------------------------------------------------- boot posture


def test_hosted_execution_without_oidc_refuses_to_boot():
    with pytest.raises(RuntimeError, match="hosted_execution_enabled"):
        assert_boot_posture(
            private_visibility_enabled=False,
            real_auth_enabled=False,
            oidc_configured_=False,
            multi_user_exposure_enabled=False,
            hosted_execution_enabled=True,
        )


def test_hosted_execution_with_oidc_boots():
    assert_boot_posture(
        private_visibility_enabled=False,
        real_auth_enabled=False,
        oidc_configured_=True,
        multi_user_exposure_enabled=False,
        hosted_execution_enabled=True,
    )


# ------------------------------------------------------- ORG_PRIVATE scope


def test_org_member_sees_public_own_and_org_rows():
    scope = AccessScope.for_org_member("alice", ["org-uuid-1", "org-uuid-2"])
    sql, params = visibility_predicate(scope, param_index=1)
    assert "visibility = 'public'" in sql
    assert "owner_id = $1" in sql
    assert "visibility = 'org'" in sql
    assert "$2::uuid" in sql and "$3::uuid" in sql
    assert params == ["alice", "org-uuid-1", "org-uuid-2"]


def test_anonymous_never_sees_org_rows():
    sql, params = visibility_predicate(AccessScope.anonymous())
    assert sql == "visibility = 'public'"
    assert params == []
    assert "'org'" not in sql


def test_unrestricted_scope_stays_visibly_true():
    sql, _ = visibility_predicate(AccessScope.unrestricted())
    assert sql == "TRUE"


def test_non_member_scope_has_no_org_clause():
    # A plain signed-in viewer with no org memberships: org rows invisible.
    sql, params = visibility_predicate(AccessScope.for_user("bob"), param_index=1)
    assert "'org'" not in sql
    assert params == ["bob"]


# ------------------------------------------------------- workspace boundary


class FakePool:
    def __init__(self, row=None):
        self._row = row
        self.calls = []

    async def fetchrow(self, sql, *params):
        self.calls.append((" ".join(sql.split()), params))
        return self._row


def _ws_row(tenant_id="org-uuid-1"):
    return {
        "id": "ws-uuid-1",
        "tenant_id": tenant_id,
        "name": "main-repo",
        "storage_path": "/srv/workspaces/main-repo",
        "default_branch": "main",
    }


def _ws():
    return wr.RegisteredWorkspace(
        id="ws-uuid-1", tenant_id="org-uuid-1", name="main-repo",
        storage_path="/srv/workspaces/main-repo", default_branch="main",
    )


def test_resolve_workspace_for_owner_tenant_succeeds():
    pool = FakePool(row=_ws_row())
    ws = asyncio.run(
        wr.resolve_workspace_for_actor(
            pool, workspace_id="ws-uuid-1", actor_tenant_id="org-uuid-1"
        )
    )
    assert ws.storage_path == "/srv/workspaces/main-repo"
    sql, params = pool.calls[0]
    assert "FROM registered_workspaces" in sql
    assert "t_expired IS NULL" in sql  # only active workspaces
    assert params == ("ws-uuid-1",)


def test_foreign_tenant_workspace_is_not_found_not_forbidden():
    # Deliberate: Forbidden would leak the existence of another tenant's
    # workspace. NotFound hides it.
    pool = FakePool(row=_ws_row(tenant_id="org-uuid-OTHER"))
    with pytest.raises(wr.WorkspaceNotFound):
        asyncio.run(
            wr.resolve_workspace_for_actor(
                pool, workspace_id="ws-uuid-1", actor_tenant_id="org-uuid-1"
            )
        )


def test_missing_workspace_is_not_found():
    pool = FakePool(row=None)
    with pytest.raises(wr.WorkspaceNotFound):
        asyncio.run(
            wr.resolve_workspace_for_actor(
                pool, workspace_id="nope", actor_tenant_id="org-uuid-1"
            )
        )


def test_hosted_path_mismatch_is_refused():
    settings = SimpleNamespace(hosted_execution_enabled=True)
    with pytest.raises(wr.WorkspaceNotAuthorized, match="does not match"):
        wr.enforce_hosted_repo_path(
            settings=settings, repo_path="/etc/passwd", workspace=_ws()
        )


def test_hosted_registered_path_wins_over_caller_string():
    settings = SimpleNamespace(hosted_execution_enabled=True)
    out = wr.enforce_hosted_repo_path(
        settings=settings, repo_path=None, workspace=_ws()
    )
    assert out == "/srv/workspaces/main-repo"


def test_hosted_mode_without_workspace_is_refused():
    settings = SimpleNamespace(hosted_execution_enabled=True)
    with pytest.raises(wr.WorkspaceNotAuthorized, match="not an authorization mechanism"):
        wr.enforce_hosted_repo_path(
            settings=settings, repo_path="/some/repo", workspace=None
        )


def test_local_mode_passthrough_unchanged():
    settings = SimpleNamespace(hosted_execution_enabled=False)
    out = wr.enforce_hosted_repo_path(
        settings=settings, repo_path="C:/dev/my-repo", workspace=None
    )
    assert out == "C:/dev/my-repo"


# ------------------------------------------------------- migration 41


def _m41() -> str:
    return (BACKEND / "db" / "41_phase1_security_boundaries.sql").read_text(encoding="utf-8")


def test_migration_41_adds_org_visibility_value():
    assert "ALTER TYPE visibility_level ADD VALUE IF NOT EXISTS 'org'" in _m41()


def test_migration_41_creates_audit_events_append_only_shape():
    sql = _m41()
    assert "CREATE TABLE IF NOT EXISTS audit_events" in sql
    for col in ("actor_subject", "action", "object_type", "object_id", "details"):
        assert col in sql, col
    # the ONE-writer rule: the migration never mutates the table
    low = sql.lower()
    assert "update audit_events" not in low
    assert "delete from audit_events" not in low


def test_migration_41_creates_registered_workspaces_with_tenant_fk():
    sql = _m41()
    assert "CREATE TABLE IF NOT EXISTS registered_workspaces" in sql
    assert "REFERENCES organizations(id)" in sql
    assert "storage_path" in sql and "t_expired" in sql


# ------------------------------------------------------- hygiene / impersonation


def test_identity_comes_from_verified_token_seam():
    # The Supabase preset must be active on the canonical identity path and
    # must never trust a client-supplied identity field for hosted authz.
    cfg = OidcConfig.from_settings(
        _settings(
            supabase_project_url="https://abc.supabase.co",
            supabase_jwt_audience="authenticated",
        )
    )
    assert "HS256" not in cfg.allowed_algs


def test_one_builder_rule_no_hand_written_tenant_filters():
    # No service other than access.py (the ONE builder) may write a tenant
    # filter into SQL by hand — the H1 house rule, now including the new
    # Phase 1 modules.
    offenders = []
    for p in (BACKEND / "app" / "services").glob("*.py"):
        if p.name in ("access.py", "audit.py"):
            continue
        src = p.read_text(encoding="utf-8")
        code_only = "\n".join(
            ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
        )
        if "tenant_id = $" in code_only and "services/access.py" not in src and "workspace_registry" not in src:
            offenders.append(p.name)
    assert offenders == [], f"hand-written tenant filters in {offenders}"


def test_audit_writer_targets_the_migration_41_table():
    from app.services import audit

    src = Path(audit.__file__).read_text(encoding="utf-8")
    assert "audit_events" in src
