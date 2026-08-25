"""
Access scoping (V2 + HARDENING H1).

One module builds every visibility AND tenancy predicate in the system.
Nothing outside this file should write `visibility = ...`, `owner_id =
...` or `tenant_id = ...` into a query.

That constraint is the entire point. V0 put a `tenant_id` column on
every table and then never filtered by it anywhere — so isolation
looked implemented and was decorative, and turning it on later would
have meant auditing every query in the codebase. Centralising the
predicate here means V2's private mode ships as a change to one
function, verified by one set of tests, rather than a codebase-wide
audit hoping nothing was missed. H1 applies the identical lesson to
tenancy: the predicate builder below means flipping multi-tenancy on
is a change of what callers PASS, not a codebase-wide audit.

Current policy: a fully shared commons. Everything is public and every
tenant-scoped query names the seeded commons organization (the V0
default tenant), so both predicates are permissive — but they are
*present in every query path*, which is what makes flipping them cheap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AccessScope:
    """
    Who is asking, and what they may therefore see.

    `viewer_id` is None for anonymous public traffic — the default on a
    public commons, not an error condition.
    """

    viewer_id: Optional[str] = None
    include_private: bool = True

    @classmethod
    def anonymous(cls) -> "AccessScope":
        return cls(viewer_id=None)

    @classmethod
    def for_user(cls, viewer_id: str) -> "AccessScope":
        return cls(viewer_id=viewer_id)

    @classmethod
    def unrestricted(cls) -> "AccessScope":
        """
        Bypasses visibility entirely. For internal maintenance paths
        (backfills, migrations, admin tooling) — never for a request
        originating from a user.
        """
        return cls(viewer_id=None, include_private=False)

    @property
    def is_unrestricted(self) -> bool:
        return self.viewer_id is None and not self.include_private


def visibility_predicate(
    scope: AccessScope, alias: str = "", param_index: int = 1
) -> tuple[str, list]:
    """
    Build a SQL predicate and its parameters for the given scope.

    Returns (sql_fragment, params). The fragment is always a complete
    boolean expression safe to AND into a WHERE clause — never an empty
    string, because an empty string silently drops the filter and that is
    exactly the failure this module exists to prevent. An unrestricted
    scope returns the literal `TRUE`, which is visibly permissive in the
    query text rather than invisibly absent.

    `param_index` is the first positional placeholder available to the
    caller ($1, $2, ...). asyncpg has no named parameters, so callers
    must thread this correctly; get it wrong and the query binds the
    wrong values. `next_param_index` below makes that explicit.
    """
    prefix = f"{alias}." if alias else ""

    if scope.is_unrestricted:
        return "TRUE", []

    if scope.viewer_id is None:
        # Anonymous: public content only.
        return f"{prefix}visibility = 'public'", []

    if not scope.include_private:
        return f"{prefix}visibility = 'public'", []

    # Signed in: public content, plus anything they own.
    return (
        f"({prefix}visibility = 'public' OR {prefix}owner_id = ${param_index})",
        [scope.viewer_id],
    )


def next_param_index(scope: AccessScope, current: int) -> int:
    """
    How many placeholders `visibility_predicate` consumed, so the caller
    knows where its own parameters resume. Threading this by hand is a
    real source of off-by-one bugs in raw SQL.
    """
    _, params = visibility_predicate(scope, param_index=current)
    return current + len(params)


# ---------------------------------------------------------------------------
# Tenancy (HARDENING H1): the same ONE-builder pattern, second axis.
#
# The tenant predicate mirrors visibility_predicate's contract exactly:
# never empty, alias-threaded, positional-param-threaded, unrestricted
# visibly `TRUE`. The one difference is the ::uuid cast — tenant_id
# columns are UUID while owner_id was TEXT, and asyncpg binds a Python
# str to an untyped parameter without knowing the column type; the cast
# in the SQL text makes the binding explicit instead of hoping callers
# pass uuid.UUID objects.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantScope:
    """
    Which organization's data this query may touch.

    `tenant_id` is None only for internal maintenance paths — the same
    escape hatch AccessScope.unrestricted() is, with the same rule: it
    must never serve a user-originated request.
    """

    tenant_id: Optional[str] = None

    @classmethod
    def for_tenant(cls, tenant_id: str) -> "TenantScope":
        return cls(tenant_id=str(tenant_id))

    @classmethod
    def commons(cls) -> "TenantScope":
        """
        The seeded commons organization — V0's default_tenant_id, which
        every existing row carries. This is today's permissive-in-effect
        posture made explicit: queries name a real organization row
        rather than silently filtering nothing.
        """
        from app.config import settings

        return cls(tenant_id=settings.default_tenant_id)

    @classmethod
    def unrestricted(cls) -> "TenantScope":
        """
        Bypasses tenancy entirely. For internal maintenance paths
        (integrity provers, backfills, migrations) — never for a request
        originating from a user.
        """
        return cls(tenant_id=None)

    @property
    def is_unrestricted(self) -> bool:
        return self.tenant_id is None


def tenant_predicate(
    scope: TenantScope, alias: str = "", param_index: int = 1
) -> tuple[str, list]:
    """
    Build the SQL predicate and its parameters for the given tenant scope.

    Same contract as visibility_predicate: the fragment is always a
    complete boolean expression safe to AND into a WHERE clause — an
    unrestricted scope returns the literal `TRUE` so permissiveness is
    visible in the query text instead of invisibly absent.
    """
    prefix = f"{alias}." if alias else ""

    if scope.is_unrestricted:
        return "TRUE", []

    return (
        f"{prefix}tenant_id = ${param_index}::uuid",
        [scope.tenant_id],
    )


def next_tenant_param_index(scope: TenantScope, current: int) -> int:
    """Tenant twin of next_param_index."""
    _, params = tenant_predicate(scope, param_index=current)
    return current + len(params)


def scope_predicates(
    access_scope: AccessScope,
    tenant_scope: TenantScope,
    alias: str = "",
    param_index: int = 1,
) -> tuple[str, list, int]:
    """
    ONE call building BOTH predicates with correctly sequenced params.

    This is the adoption seam: a query path threads its scope objects
    through here instead of hand-sequencing two builders' placeholder
    indices. Returns (fragment, params, next_index); the fragment is
    `(visibility...) AND (tenant...)`, each side produced by its own
    builder so neither axis can silently vanish.

    `tenant_scope` has deliberately NO default. A caller that skips it
    gets a TypeError, not a silently unscoped query.
    """
    vis_sql, vis_params = visibility_predicate(access_scope, alias=alias, param_index=param_index)
    after_vis = next_param_index(access_scope, param_index)
    ten_sql, ten_params = tenant_predicate(tenant_scope, alias=alias, param_index=after_vis)
    return (
        f"({vis_sql}) AND ({ten_sql})",
        [*vis_params, *ten_params],
        after_vis + len(ten_params),
    )
