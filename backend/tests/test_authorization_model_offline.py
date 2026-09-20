"""Authorization policy (services/authorization.py) — truth table, tenant
isolation, and property/invariant tests. Pure functions: no DB, no network.

Cast:  alice (tenant A member), bob (tenant B member), carol (tenant A *viewer*),
       reviewer, platform_admin (admin:ops but NOT tenancy:cross), anonymous,
       ingestion service (unbound / bound to alice's job / bound + publication_allowed).
"""
from __future__ import annotations

import dataclasses
import itertools
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.services import auth_context as ac
from app.services.auth_context import (
    AnonymousContext, AuthContext, JobAuthority, ServiceAuthContext, scopes_for_roles,
)
from app.services.authorization import (
    Action, AuthorizationDenied, ObjectRef, ObjectScope, authorize, can_admin, can_execute, can_publish,
    can_read, can_write, owner_fields_for_create, require_scopes, scope_for_visibility,
)

TA, TB = "aaaaaaaa-0000-0000-0000-00000000000a", "bbbbbbbb-0000-0000-0000-00000000000b"


def user(subject, tenant=None, role="member", platform=()):
    orgs = (tenant,) if tenant else ()
    return AuthContext(
        user_id=f"uid-{subject}", subject=subject, tenant_id=tenant, org_id=tenant,
        roles=frozenset({role}) if tenant else frozenset(),
        scopes=scopes_for_roles({"user", *platform}), org_ids=orgs,
        org_roles={tenant: frozenset({role})} if tenant else {}, platform_roles=frozenset(platform),
    )


ALICE, BOB = user("alice", TA), user("bob", TB)
CAROL = user("carol", TA, role="viewer")
ADMIN_A = user("adminA", TA, role="admin")
REVIEWER = user("rev", None, platform=("reviewer",))
PLATFORM_ADMIN = user("root", None, platform=("platform_admin",))
ANON = AnonymousContext()
SVC = ServiceAuthContext("cloud-run-ingestion", frozenset({"ingestion_worker"}),
                         frozenset({ac.INGESTION_PROCESS, ac.PROJECTION_WRITE}))
SVC_ALICE = SVC.bind_job(JobAuthority(submitted_by_user_id="alice", tenant_id=None, scope="user_private"))
SVC_ALICE_PUB = SVC.bind_job(JobAuthority(submitted_by_user_id="alice", scope="user_private", publication_allowed=True))
SVC_TENANT_A = SVC.bind_job(JobAuthority(tenant_id=TA, scope="tenant_private"))

PUBLIC = ObjectRef("public")
A_PRIV = ObjectRef("private", owner_id="alice")
B_PRIV = ObjectRef("private", owner_id="bob")
A_ORG = ObjectRef("org", owner_id="alice", tenant_id=TA)
B_ORG = ObjectRef("org", owner_id="bob", tenant_id=TB)
INTERNAL = ObjectRef("system")


def test_visibility_maps_to_scope_and_unknown_fails_closed():
    assert scope_for_visibility("public") is ObjectScope.GLOBAL_PUBLIC
    assert scope_for_visibility("private") is ObjectScope.USER_PRIVATE
    assert scope_for_visibility("org") is ObjectScope.TENANT_PRIVATE
    assert scope_for_visibility(None) is ObjectScope.SYSTEM_INTERNAL
    assert scope_for_visibility("Public ") is ObjectScope.GLOBAL_PUBLIC
    assert scope_for_visibility("wide-open") is ObjectScope.SYSTEM_INTERNAL


# ----------------------------------------------------------------- read

@pytest.mark.parametrize("ctx,obj,expected", [
    (ANON, PUBLIC, True), (ANON, A_PRIV, False), (ANON, A_ORG, False), (ANON, INTERNAL, False),
    (ALICE, PUBLIC, True), (ALICE, A_PRIV, True), (ALICE, B_PRIV, False),
    (ALICE, A_ORG, True), (ALICE, B_ORG, False),
    (BOB, A_PRIV, False), (BOB, A_ORG, False), (BOB, B_PRIV, True), (BOB, B_ORG, True),
    (CAROL, A_ORG, True), (CAROL, A_PRIV, False),
    # platform admin operates the platform; it does NOT read private data
    (PLATFORM_ADMIN, A_PRIV, False), (PLATFORM_ADMIN, B_ORG, False), (PLATFORM_ADMIN, INTERNAL, True),
    (REVIEWER, A_PRIV, False),
    # services: public + system; private only through a job that covers it
    (SVC, PUBLIC, True), (SVC, A_PRIV, False), (SVC, A_ORG, False), (SVC, INTERNAL, True),
    (SVC_ALICE, A_PRIV, True), (SVC_ALICE, B_PRIV, False), (SVC_ALICE, A_ORG, False),
    (SVC_TENANT_A, A_ORG, True), (SVC_TENANT_A, B_ORG, False), (SVC_TENANT_A, A_PRIV, False),
])
def test_can_read(ctx, obj, expected):
    assert can_read(ctx, obj) is expected


def test_service_without_internal_scope_cannot_read_internal():
    weak = ServiceAuthContext("x", frozenset(), frozenset({ac.INGESTION_SUBMIT}))
    assert can_read(weak, INTERNAL) is False


def test_only_explicit_cross_tenant_scope_crosses_tenants():
    crosser = dataclasses.replace(PLATFORM_ADMIN, scopes=PLATFORM_ADMIN.scopes | {ac.TENANCY_CROSS})
    assert can_read(crosser, B_ORG) and can_read(crosser, A_PRIV)
    assert not any(ac.TENANCY_CROSS in s for s in ac.ROLE_SCOPES.values()), "no role may grant tenancy:cross"
    assert not any(ac.TENANCY_CROSS in s for s in ac.SERVICE_ROLE_SCOPES.values())


# ----------------------------------------------------------------- write

@pytest.mark.parametrize("ctx,obj,expected", [
    (ANON, PUBLIC, False), (ANON, A_PRIV, False),
    (ALICE, A_PRIV, True), (ALICE, B_PRIV, False), (ALICE, A_ORG, True), (ALICE, B_ORG, False),
    (ALICE, ObjectRef("private", owner_id=None), True),          # create: owner derived from ctx
    (ALICE, ObjectRef("private", owner_id="bob"), False),        # create-as-someone-else: denied
    (ALICE, ObjectRef("private", owner_id=None, tenant_id=TB), False),
    (CAROL, A_ORG, False),                                        # viewer role is read-only in the tenant
    (ADMIN_A, A_ORG, True),
    (ALICE, PUBLIC, False),                                       # users cannot write global objects...
    (REVIEWER, PUBLIC, True),                                     # ...only holders of knowledge:publish
    (SVC, A_PRIV, False), (SVC_ALICE, A_PRIV, True), (SVC_ALICE, B_PRIV, False),
    (SVC, ObjectRef("public", kind="projection"), True),
    (SVC, PUBLIC, False),                                         # global write needs a global-scoped job
    (SVC.bind_job(JobAuthority(scope="global_public")), PUBLIC, True),
    (SVC, INTERNAL, True),
    (ALICE, INTERNAL, False),
])
def test_can_write(ctx, obj, expected):
    assert can_write(ctx, obj) is expected


def test_read_only_scope_blocks_writes():
    ro = dataclasses.replace(ALICE, scopes=frozenset({ac.KNOWLEDGE_READ, ac.RETRIEVAL_READ}))
    assert not can_write(ro, A_PRIV) and can_read(ro, A_PRIV)


# --------------------------------------------------------------- publish

@pytest.mark.parametrize("ctx,obj,expected", [
    (ALICE, A_PRIV, True), (ALICE, B_PRIV, False), (BOB, A_PRIV, False),
    (ALICE, PUBLIC, False),                    # already global
    (ALICE, A_ORG, False),                     # member (not tenant admin) cannot publish tenant data
    (ADMIN_A, A_ORG, True), (ADMIN_A, B_ORG, False),
    (REVIEWER, A_PRIV, False),                 # knowledge:publish admits candidates, it does not publish other people's private data
    (PLATFORM_ADMIN, A_PRIV, False),
    (ANON, A_PRIV, False),
    (SVC, A_PRIV, False),                      # workers never decide to publish
    (SVC_ALICE, A_PRIV, False),                # job without publication_allowed
    (SVC_ALICE_PUB, A_PRIV, True), (SVC_ALICE_PUB, B_PRIV, False),
])
def test_can_publish(ctx, obj, expected):
    assert can_publish(ctx, obj) is expected


# ------------------------------------------------------ execute / admin

def test_execute_requires_scope_and_read_access_and_is_not_for_services():
    assert can_execute(ALICE, A_PRIV) and not can_execute(ALICE, B_PRIV)
    assert can_execute(ALICE, PUBLIC)
    assert not can_execute(dataclasses.replace(ALICE, scopes=frozenset({ac.KNOWLEDGE_READ})), A_PRIV)
    assert not can_execute(SVC_ALICE, A_PRIV) and not can_execute(ANON, PUBLIC)


def test_admin_rules():
    assert can_admin(ALICE, A_PRIV) and not can_admin(ALICE, B_PRIV)
    assert can_admin(ADMIN_A, A_ORG) and not can_admin(ALICE, A_ORG) and not can_admin(ADMIN_A, B_ORG)
    assert can_admin(PLATFORM_ADMIN, PUBLIC)
    assert not can_admin(SVC, PUBLIC) and not can_admin(ANON, PUBLIC)
    # an ORG admin is not a platform admin
    assert ac.ADMIN_OPS not in ADMIN_A.scopes


def test_expired_context_is_denied_everything():
    gone = dataclasses.replace(ALICE, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert not can_read(gone, PUBLIC) and not can_read(gone, A_PRIV) and not can_write(gone, A_PRIV)
    with pytest.raises(AuthorizationDenied) as e:
        require_scopes(gone, ac.KNOWLEDGE_READ)
    assert e.value.status == 401


# ------------------------------------------------- semantics of authorize

def test_denial_status_codes_and_existence_hiding():
    with pytest.raises(AuthorizationDenied) as e:
        authorize(ANON, Action.READ, A_PRIV)
    assert e.value.status == 401
    with pytest.raises(AuthorizationDenied) as e:
        authorize(BOB, Action.READ, A_PRIV)
    assert e.value.status == 403
    with pytest.raises(AuthorizationDenied) as e:
        authorize(BOB, Action.READ, A_PRIV, hide_existence=True)
    assert e.value.status == 404 and "alice" not in str(e.value) and TA not in str(e.value)
    authorize(ANON, Action.READ, PUBLIC)   # no raise


def test_require_scopes_semantics():
    require_scopes(ALICE, ac.KNOWLEDGE_READ, ac.EXECUTION_RUN)
    with pytest.raises(AuthorizationDenied) as e:
        require_scopes(ALICE, ac.KNOWLEDGE_PUBLISH)
    assert e.value.status == 403
    with pytest.raises(AuthorizationDenied) as e:
        require_scopes(ANON, ac.KNOWLEDGE_READ)
    assert e.value.status == 401
    require_scopes(REVIEWER, ac.KNOWLEDGE_PUBLISH)


# ------------------------------------------------------------ ownership

def test_owner_fields_come_from_the_principal_only():
    assert owner_fields_for_create(ALICE)["owner_id"] == "alice"
    assert owner_fields_for_create(ALICE)["tenant_id"] is None        # private object: no tenant
    f = owner_fields_for_create(ALICE, visibility="org")
    assert f["tenant_id"] == TA and f["created_by_user_id"] == "uid-alice" and f["created_by_service_id"] is None
    s = owner_fields_for_create(SVC_ALICE)
    assert s["owner_id"] == "alice" and s["created_by_service_id"] == "cloud-run-ingestion" and s["created_by_user_id"] is None
    with pytest.raises(AuthorizationDenied):
        owner_fields_for_create(ANON)
    import inspect
    assert list(inspect.signature(owner_fields_for_create).parameters) == ["ctx", "visibility"], \
        "no owner/tenant parameter may exist for a request to fill"


def test_retrying_a_write_preserves_owner_and_scope():
    assert owner_fields_for_create(ALICE, visibility="org") == owner_fields_for_create(ALICE, visibility="org")


# ---------------------------------------------------------- property tests

def _rand_ctx(rng):
    kind = rng.choice(["user", "user", "svc", "anon"])
    if kind == "anon":
        return ANON
    if kind == "svc":
        job = rng.choice([None, JobAuthority(submitted_by_user_id=rng.choice(["u1", "u2"]), scope="user_private"),
                          JobAuthority(tenant_id=rng.choice([TA, TB]), scope="tenant_private")])
        return SVC.bind_job(job) if job else SVC
    return user(rng.choice(["u1", "u2", "u3"]), rng.choice([None, TA, TB]), role=rng.choice(["member", "viewer", "admin"]))


def _rand_obj(rng):
    vis = rng.choice(["public", "private", "org"])
    return ObjectRef(vis, owner_id=rng.choice(["u1", "u2", "u3"]), tenant_id=rng.choice([TA, TB]) if vis == "org" else None)


def test_property_private_object_never_readable_by_a_non_owner_without_cross_scope():
    rng = random.Random(1234)
    for _ in range(4000):
        ctx, obj = _rand_ctx(rng), _rand_obj(rng)
        if not can_read(ctx, obj) or obj.scope is ObjectScope.GLOBAL_PUBLIC:
            continue
        if isinstance(ctx, AuthContext):
            owner = obj.owner_id == ctx.subject
            member = obj.scope is ObjectScope.TENANT_PRIVATE and obj.tenant_id in ctx.org_ids
            assert owner or member, (ctx, obj)
        elif isinstance(ctx, ServiceAuthContext):
            assert ctx.job is not None, (ctx, obj)
        else:
            raise AssertionError(f"anonymous read {obj}")


def test_property_authorization_is_independent_of_physical_placement():
    """ObjectRef has no shard field, and decisions are a pure function of it:
    changing home_shard_id / adding shards cannot change any result."""
    assert not any("shard" in f.name for f in dataclasses.fields(ObjectRef))
    rng = random.Random(7)
    for _ in range(500):
        ctx, obj = _rand_ctx(rng), _rand_obj(rng)
        base = [f(ctx, obj) for f in (can_read, can_write, can_publish, can_execute, can_admin)]
        moved = dataclasses.replace(obj)          # "same object, different shard": identical ref
        assert base == [f(ctx, moved) for f in (can_read, can_write, can_publish, can_execute, can_admin)]


def test_property_supplying_a_tenant_or_owner_does_not_grant_access():
    """A user cannot gain tenant access by asserting a tenant: access derives
    from ctx.org_ids only. A user with no membership never reads/writes org rows."""
    outsider = user("mallory", None)
    for t in (TA, TB, "anything"):
        o = ObjectRef("org", owner_id="mallory-claims-it", tenant_id=t)
        assert not can_read(outsider, o) and not can_write(outsider, o) and not can_admin(outsider, o)
    assert not can_write(outsider, ObjectRef("private", owner_id=None, tenant_id=TA))


def test_property_removing_membership_removes_private_tenant_access():
    with_m = user("dave", TA)
    without = dataclasses.replace(with_m, org_ids=(), org_roles={}, tenant_id=None, org_id=None, roles=frozenset())
    obj = ObjectRef("org", owner_id="someone-else", tenant_id=TA)
    assert can_read(with_m, obj) and can_write(with_m, obj)
    assert not can_read(without, obj) and not can_write(without, obj)


def test_property_role_change_updates_effective_scopes():
    assert ac.KNOWLEDGE_PUBLISH not in scopes_for_roles({"user"})
    assert ac.KNOWLEDGE_PUBLISH in scopes_for_roles({"user", "reviewer"})
    assert ac.ADMIN_OPS not in scopes_for_roles({"user", "reviewer"})
    assert ac.ADMIN_OPS in scopes_for_roles({"user", "platform_admin"})
    assert scopes_for_roles({"user", "platform_admin"}) > scopes_for_roles({"user"})
    assert scopes_for_roles({"nonexistent-role"}) == frozenset()


def test_property_worker_cannot_gain_user_permissions_by_naming_a_user():
    # There is no constructor path from a request value to job authority except
    # bind_job(), which only server code calls; and a job covers exactly its owner.
    for victim in ("alice", "bob"):
        s = SVC.bind_job(JobAuthority(submitted_by_user_id="alice", scope="user_private"))
        assert can_read(s, ObjectRef("private", owner_id=victim)) == (victim == "alice")
    assert not can_read(SVC, ObjectRef("private", owner_id="alice"))


def test_property_job_scope_confusion_does_not_widen():
    tenant_job = SVC.bind_job(JobAuthority(tenant_id=TA, scope="tenant_private"))
    user_job = SVC.bind_job(JobAuthority(submitted_by_user_id="alice", scope="user_private", tenant_id=TA))
    assert not can_read(tenant_job, A_PRIV) and not can_read(user_job, A_ORG)
    assert not can_read(user_job, ObjectRef("private", owner_id="alice", tenant_id=TB))


def test_property_public_objects_readable_by_every_principal_kind():
    for ctx in (ANON, ALICE, BOB, SVC, SVC_ALICE, PLATFORM_ADMIN, REVIEWER):
        assert can_read(ctx, PUBLIC)


def test_property_scope_sets_are_order_insensitive():
    a = AuthContext("u", "s", scopes=frozenset({"knowledge:read", "knowledge:write"}), claims={"a": 1, "b": 2})
    b = AuthContext("u", "s", scopes=frozenset({"knowledge:write", "knowledge:read"}), claims={"b": 2, "a": 1})
    assert a == b


def test_full_matrix_has_no_principal_that_can_read_someone_elses_private_object():
    """Exhaustive cross-product over the named cast."""
    cast = [ANON, ALICE, BOB, CAROL, ADMIN_A, REVIEWER, PLATFORM_ADMIN, SVC, SVC_ALICE, SVC_TENANT_A]
    for ctx, obj in itertools.product(cast, [A_PRIV, B_PRIV, A_ORG, B_ORG]):
        if can_read(ctx, obj):
            legit = (
                (isinstance(ctx, AuthContext) and (obj.owner_id == ctx.subject or obj.tenant_id in ctx.org_ids))
                or (isinstance(ctx, ServiceAuthContext) and ctx.job is not None)
            )
            assert legit, (ctx, obj)
