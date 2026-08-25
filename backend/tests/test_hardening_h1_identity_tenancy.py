"""HARDENING H1 proving tests — identity tables + tenancy predicate builder.

All offline (no database), per house style: sync tests driving async
boundaries via asyncio.run, FakePool capturing SQL for content assertions.

Covers:
  Predicate builder — TenantScope / tenant_predicate / scope_predicates
                      unit proofs (never-empty, alias threading, param
                      sequencing, visible-TRUE unrestricted posture).
  Identity boundary — authn.py's Actor -> user row -> memberships ->
                      TenantScope resolution, SQL-content proven.
  Adopted query path — replay_session()'s claims-layer read carries the
                      tenant fragment when tenant-scoped (and a visible
                      TRUE when unrestricted).
  Migration          — static assertions on db/28_identity.sql: additive
                      birth, idempotency, FK teeth, deterministic seeds,
                      commons org == V0 default tenant.
  Hygiene            — no module outside services/access.py writes a
                      tenant filter into SQL by hand (the one-builder
                      rule, enforced repo-wide).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from app.services.access import (
    AccessScope,
    TenantScope,
    next_param_index,
    next_tenant_param_index,
    scope_predicates,
    tenant_predicate,
    visibility_predicate,
)
from app.services.authn import (
    AmbiguousTenant,
    Actor,
    IdentityInactive,
    ResolvedMembership,
    ensure_user,
    resolve_memberships,
    tenant_scope_for,
    tenant_scope_for_actor,
)

BACKEND = Path(__file__).resolve().parents[1]


# ------------------------------------------------------- predicate builder


def test_tenant_unrestricted_returns_literal_true_not_empty():
    sql, params = tenant_predicate(TenantScope.unrestricted())
    assert sql == "TRUE"
    assert params == []


def test_tenant_scoped_binds_uuid_cast_at_requested_index():
    for index in (1, 2, 9):
        sql, params = tenant_predicate(TenantScope.for_tenant("org-a"), param_index=index)
        assert sql == f"tenant_id = ${index}::uuid"
        assert params == ["org-a"]


def test_tenant_predicate_is_never_empty_for_any_scope():
    for scope in (TenantScope.unrestricted(), TenantScope.for_tenant("org-b")):
        sql, _ = tenant_predicate(scope)
        assert sql.strip(), f"empty tenant predicate for {scope}"


def test_tenant_alias_prefixes_the_column():
    sql, _ = tenant_predicate(TenantScope.for_tenant("org-c"), alias="k")
    assert sql == "k.tenant_id = $1::uuid"
    assert " visibility" not in sql  # no axis bleed


def test_next_tenant_param_index_tracks_consumption():
    assert next_tenant_param_index(TenantScope.for_tenant("x"), 3) == 4
    assert next_tenant_param_index(TenantScope.unrestricted(), 3) == 3


def test_commons_scope_is_the_v0_default_tenant():
    from app.config import settings

    scope = TenantScope.commons()
    assert scope.tenant_id == settings.default_tenant_id
    assert not scope.is_unrestricted


def test_combined_predicates_anonymous_plus_tenant_share_first_slot():
    frag, params, nxt = scope_predicates(
        AccessScope.anonymous(), TenantScope.for_tenant("org-a"), param_index=1
    )
    assert frag == "(visibility = 'public') AND (tenant_id = $1::uuid)"
    assert params == ["org-a"]
    assert nxt == 2


def test_combined_predicates_user_viewer_then_tenant_sequence_params():
    frag, params, nxt = scope_predicates(
        AccessScope.for_user("alice"), TenantScope.for_tenant("org-a"), param_index=1
    )
    assert "visibility = 'public'" in frag
    assert "owner_id = $1" in frag
    assert "tenant_id = $2::uuid" in frag
    assert params == ["alice", "org-a"]
    assert nxt == 3


def test_combined_predicates_unrestricted_posture_stays_visible():
    frag, params, nxt = scope_predicates(
        AccessScope.unrestricted(), TenantScope.unrestricted()
    )
    assert frag == "(TRUE) AND (TRUE)"
    assert params == [] and nxt == 1


def test_combined_predicates_alias_reaches_both_axes():
    frag, _, _ = scope_predicates(
        AccessScope.for_user("a"), TenantScope.for_tenant("o"), alias="e", param_index=2
    )
    assert "e.visibility" in frag and "e.owner_id = $2" in frag
    assert "e.tenant_id = $3::uuid" in frag


def test_combined_predicates_tenant_argument_has_no_silent_default():
    with pytest.raises(TypeError):
        scope_predicates(AccessScope.anonymous())  # type: ignore[call-arg]


def test_visibility_builder_contract_unchanged_by_h1():
    """The original visibility surface must be untouched by this wave."""
    sql, params = visibility_predicate(AccessScope.for_user("bob"), param_index=4)
    assert "$4" in sql and params == ["bob"]
    assert next_param_index(AccessScope.anonymous(), 7) == 7


# -------------------------------------------------------- identity boundary


class FakeIdentityPool:
    """Routes authn.py's three statement shapes; records everything."""

    def __init__(self, *, user_row=None, insert_return=None, membership_rows=()):
        self.user_row = user_row
        self.insert_return = insert_return
        self.membership_rows = list(membership_rows)
        self.selects: list[tuple[str, tuple]] = []
        self.inserts: list[tuple[str, tuple]] = []

    def _flat(self, sql):
        return " ".join(sql.split())

    async def fetchrow(self, sql, *params):
        flat = self._flat(sql)
        if flat.startswith("SELECT id, is_active"):
            self.selects.append((flat, params))
            return self.user_row
        if flat.startswith("INSERT INTO users"):
            self.inserts.append((flat, params))
            return self.insert_return
        raise AssertionError(f"unexpected fetchrow: {flat[:80]}")

    async def fetch(self, sql, *params):
        flat = self._flat(sql)
        if flat.startswith("SELECT m.organization_id"):
            self.selects.append((flat, params))
            return self.membership_rows
        raise AssertionError(f"unexpected fetch: {flat[:80]}")


ACTOR = Actor(subject="sub-1", issuer="https://idp.example", name="A One", email="a@x")


def _user_row(user_id="u-111", active=True, expired=None):
    return {"id": user_id, "is_active": active, "t_expired": expired}


def test_ensure_user_select_hit_returns_existing_active_row():
    pool = FakeIdentityPool(user_row=_user_row("u-1"))
    assert asyncio.run(ensure_user(pool, ACTOR)) == "u-1"
    assert pool.inserts == []
    sql, params = pool.selects[0]
    assert params == ("https://idp.example", "sub-1")  # issuer+subject pair


def test_ensure_user_miss_provisions_via_conflict_safe_insert():
    pool = FakeIdentityPool(user_row=None, insert_return={"id": "u-new"})
    assert asyncio.run(ensure_user(pool, ACTOR)) == "u-new"
    assert len(pool.inserts) == 1
    sql, params = pool.inserts[0]
    assert "ON CONFLICT (issuer, external_subject) DO NOTHING" in sql
    assert "RETURNING id" in sql
    assert params == ("https://idp.example", "sub-1", "A One", "a@x")


def test_ensure_user_lost_create_race_falls_back_to_reselect():
    class RacePool(FakeIdentityPool):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.select_calls = 0

        async def fetchrow(self, sql, *params):
            flat = self._flat(sql)
            if flat.startswith("INSERT INTO users"):
                await super().fetchrow(sql, *params)
                return None  # concurrent winner took the row
            if flat.startswith("SELECT id, is_active"):
                self.select_calls += 1
                if self.select_calls > 1:
                    # every re-select after the initial miss sees the
                    # concurrent winner's freshly provisioned row
                    self.user_row = _user_row(f"u-race-{self.select_calls - 1}")
            return await super().fetchrow(sql, *params)

    pool = RacePool(user_row=None)
    assert asyncio.run(ensure_user(pool, ACTOR)) == "u-race-1"
    assert pool.inserts != [] and "ON CONFLICT" in pool.inserts[0][0]


@pytest.mark.parametrize("row", [_user_row(active=False), _user_row(expired="2026-01-01")])
def test_ensure_user_inactive_or_expired_identity_fails_closed(row):
    pool = FakeIdentityPool(user_row=row)
    with pytest.raises(IdentityInactive):
        asyncio.run(ensure_user(pool, ACTOR))


def test_resolve_memberships_sql_filters_expired_and_joins_roles():
    pool = FakeIdentityPool(membership_rows=[{
        "organization_id": "org-a",
        "organization_name": "Org A",
        "organization_slug": "org-a",
        "role_name": "member",
    }])
    got = asyncio.run(resolve_memberships(pool, "u-1"))
    assert got == [
        ResolvedMembership(
            organization_id="org-a",
            organization_name="Org A",
            organization_slug="org-a",
            role_name="member",
        )
    ]
    sql, params = pool.selects[-1]
    assert "JOIN organizations o ON o.id = m.organization_id" in sql
    assert "JOIN roles r ON r.id = m.role_id" in sql
    assert "m.t_expired IS NULL AND o.t_expired IS NULL" in sql  # revocation respected
    assert params == ("u-1",)


def test_tenant_scope_for_zero_memberships_resolves_to_commons():
    from app.config import settings

    scope = tenant_scope_for([])
    assert scope.tenant_id == settings.default_tenant_id


def test_tenant_scope_for_single_membership_names_that_org():
    m = ResolvedMembership("org-x", "Org X", "org-x", "viewer")
    assert tenant_scope_for([m]) == TenantScope.for_tenant("org-x")


def test_tenant_scope_for_multiple_orgs_fails_loud_not_implicitly_picked():
    ms = [
        ResolvedMembership("org-x", "Org X", "alpha", "member"),
        ResolvedMembership("org-y", "Org Y", "beta", "viewer"),
    ]
    with pytest.raises(AmbiguousTenant) as exc:
        tenant_scope_for(ms)
    assert "alpha" in str(exc.value) and "beta" in str(exc.value)


def test_tenant_scope_for_actor_one_call_seam():
    pool = FakeIdentityPool(
        user_row=_user_row("u-9"),
        membership_rows=[{
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "organization_name": "Commons",
            "organization_slug": "commons",
            "role_name": "owner",
        }],
    )
    user_id, scope = asyncio.run(tenant_scope_for_actor(pool, ACTOR))
    assert user_id == "u-9"
    assert scope == TenantScope.commons()


# --------------------------------------------------- adopted query path


class ReplayCapturePool:
    """Answers replay_session()'s reads with empty sets; records SQL."""

    def __init__(self):
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *params):
        self.queries.append((" ".join(sql.split()), params))
        return []

    def claims_query(self):
        for sql, params in self.queries:
            if "FROM knowledge_nodes k" in sql:
                return sql, params
        raise AssertionError("claims-layer query was never issued")


def _run_replay(pool, **kw):
    from app.execution.replay import replay_session

    return asyncio.run(replay_session(pool, session_id="sess-1", **kw))


def test_replay_claims_query_carries_tenant_fragment_when_scoped():
    pool = ReplayCapturePool()
    report = _run_replay(pool, tenant_scope=TenantScope.for_tenant("org-zz"))
    assert report["claims"]["checked"] == 0  # vacuous but exercised
    sql, params = pool.claims_query()
    assert "k.node_type = 'claim'" in sql
    assert "AND k.tenant_id = $2::uuid" in sql  # $1 is session_id
    assert params == ("sess-1", "org-zz")


def test_replay_claims_query_shows_visible_true_when_unrestricted():
    pool = ReplayCapturePool()
    _run_replay(pool)  # default None -> explicit unrestricted posture
    sql, params = pool.claims_query()
    assert "AND TRUE" in sql
    assert params == ("sess-1",)  # no stray bindings


# --------------------------------------------------------- migration checks


DB28 = (BACKEND / "db" / "28_identity.sql").read_text(encoding="utf-8")


def test_migration_births_all_four_tables_additively():
    for table in ("organizations", "users", "roles", "org_memberships"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in DB28, table


def test_migration_membership_fks_bind_all_three_parents():
    block = DB28.split("CREATE TABLE IF NOT EXISTS org_memberships")[1]
    assert "REFERENCES organizations(id)" in block
    assert "REFERENCES users(id)" in block
    assert "REFERENCES roles(id)" in block


def test_migration_identity_keys_are_unique():
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_issuer_subject" in DB28
    assert "ON users(issuer, external_subject)" in DB28
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_org_slug" in DB28
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_roles_name" in DB28


def test_migration_membership_pk_is_the_full_triple():
    assert "PRIMARY KEY (organization_id, user_id, role_id)" in DB28


def test_migration_is_idempotent_and_additive_only():
    assert DB28.count("IF NOT EXISTS") >= 8  # every CREATE guarded
    assert "ON CONFLICT (id) DO NOTHING" in DB28  # seed re-run safe
    assert "ON CONFLICT (name) DO NOTHING" in DB28
    for banned in ("DROP TABLE", "DROP COLUMN", "DELETE FROM", "TRUNCATE", "ALTER TABLE"):
        assert banned not in DB28, banned


def test_migration_seed_org_is_the_v0_default_tenant():
    from app.config import settings

    assert settings.default_tenant_id in DB28  # commons id == decorative placeholder
    assert "'Commons'" in DB28 and "'commons'" in DB28


def test_migration_seeds_four_builtin_roles_deterministically():
    for role in ("'owner'", "'admin'", "'member'", "'viewer'"):
        assert role in DB28, role
    # fixed uuids => same rows in every environment
    assert DB28.count("10000000-0000-0000-0000-00000000000") == 4


# ------------------------------------------------------------------ hygiene


def test_no_module_outside_access_py_writes_tenant_filters_by_hand():
    """
    The one-builder rule, repo-wide: `tenant_id =` may appear only as an
    INSERT column list or a model field copy — never as a WHERE/AND
    filter outside services/access.py. This is the tooth that keeps H1's
    predicate from being quietly bypassed by future queries. Line-scoped:
    a WHERE and its tenant column always share a line in this codebase's
    SQL style, while INSERT column lists put tenant_id on its own line.
    """
    offender = re.compile(r"(?i)\b(?:where|and)\b[^;]*\btenant_id\s*=")
    violations = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        if path.name == "access.py":
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if offender.search(line):
                rel = path.relative_to(BACKEND)
                violations.append(f"{rel}:{lineno}: {' '.join(line.split())[:100]}")
    assert violations == [], (
        "hand-written tenant filter(s) outside the builder:\n" + "\n".join(violations)
    )


def test_access_py_hosts_the_builder_the_rule_protects():
    access_src = (BACKEND / "app" / "services" / "access.py").read_text(encoding="utf-8")
    assert "def tenant_predicate(" in access_src
    assert "def scope_predicates(" in access_src
