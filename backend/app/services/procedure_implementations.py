"""
The Procedure <-> Implementation relation service (spec v4-hardening
Sec B23), backed by `db/52_procedure_implementation_relation.sql`.

WHAT THIS IS
    Metadata on the M:N edge between a Procedure (addressed by its stable
    `procedures.procedure_id`, NOT a version row id) and a durable
    `implementations` row: which ROLE the implementation plays for this
    procedure (primary / supporting / partial / verification), which
    steps or capabilities it realizes, when the binding is applicable,
    how the host binds its interface for THIS procedure, and pointers to
    the `evidence` rows showing it realizes this procedure well.

WHAT THIS IS NOT
    Not a second registry. `implementations` (migration 33) stays the one
    registry of implementation identity; nothing here inserts or mutates
    an `implementations` row. It also invents NO numeric coverage/quality
    score -- per Sec B23, "do not invent numeric coverage/quality scores
    unless they come from recorded evaluation." `evidence_refs` points at
    real `evidence` rows instead; a caller wanting a score derives it
    from those.

HONEST LIMITS
    - `procedure_implementations` carries no `tenant_id` column (neither
      migration 39 nor 52 added one), matching `implementations` and
      `procedures`. Writes still run inside
      `tenant_transaction(TenantScope.commons())` so the RLS setting
      (db/29) is bound as the first statement of the write the same way
      every other Wave-3 write path binds it, and so a future tenant_id
      column needs no new wiring here.
    - `close_binding`'s `closed_by` is accepted for symmetry with this
      codebase's other close/retire calls, but the table has no actor
      column for a close -- the closing actor belongs in the surrounding
      ChangeSet audit row, not on this edge -- so it is not persisted.
    - The read helpers take no `AccessScope` and apply no visibility
      predicate. `solution_implementations.get_solution_implementation_
      detail` performs the procedure-level visibility check before
      calling in; a direct caller needing implementation-level visibility
      filtering must post-filter the returned rows.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

import asyncpg

from app.services.access import TenantScope, tenant_transaction
from app.utils.ids import uuid7

# The db/52 CHECK vocabularies, verbatim.
ROLES: tuple[str, ...] = ("primary", "supporting", "partial", "verification")
STATUSES: tuple[str, ...] = (
    "candidate", "active", "deprecated", "disabled", "quarantined",
)


class ProcedureImplementationError(ValueError):
    """A caller-supplied value violates this module's contract (unknown
    role or status). Raised before any SQL runs -- same producer-side
    discipline as `implementation_registry.ImplementationRegistryError`."""


def _require_role(role: str) -> None:
    if role not in ROLES:
        raise ProcedureImplementationError(
            f"unknown role {role!r} (valid: {ROLES})"
        )


def _require_status(status: str) -> None:
    if status not in STATUSES:
        raise ProcedureImplementationError(
            f"unknown status {status!r} (valid: {STATUSES})"
        )


def _require_roles_filter(roles: Optional[list[str]]) -> None:
    for role in roles or ():
        _require_role(role)


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Plain-dict projection with every UUID value stringified, matching
    `implementation_registry._row_to_dict`'s JSON-friendly convention."""
    out = dict(row)
    for key, value in list(out.items()):
        if isinstance(value, UUID):
            out[key] = str(value)
    return out


# ---------------------------------------------------------------------------
# bind_implementation
# ---------------------------------------------------------------------------

_BIND_COLUMNS: tuple[str, ...] = (
    "id", "procedure_id", "implementation_id", "role",
    "implementation_version", "implementation_version_constraint",
    "supported_steps", "supported_capabilities", "applicability",
    "interface_binding", "evidence_refs", "status", "resource_path",
    "ingestion_context_id", "created_by",
)

_BIND_INSERT_SQL = f"""
    INSERT INTO procedure_implementations (
        {", ".join(_BIND_COLUMNS)}
    ) VALUES (
        $1::uuid, $2::uuid, $3::uuid, $4,
        $5, $6,
        $7::jsonb, $8::jsonb, $9::jsonb,
        $10::jsonb, $11::jsonb, $12, $13,
        $14::uuid, $15
    )
    ON CONFLICT (procedure_id, implementation_id, role) WHERE t_invalid IS NULL
    DO NOTHING
    RETURNING id
"""

_BIND_SELECT_LIVE_SQL = """
    SELECT id FROM procedure_implementations
    WHERE procedure_id = $1::uuid AND implementation_id = $2::uuid
      AND role = $3 AND t_invalid IS NULL
"""


async def bind_implementation(
    pool: asyncpg.Pool,
    *,
    procedure_id: str,
    implementation_id: str,
    role: str = "primary",
    implementation_version: Optional[int] = None,
    implementation_version_constraint: Optional[str] = None,
    supported_steps: Optional[list] = None,
    supported_capabilities: Optional[list] = None,
    applicability: Optional[dict] = None,
    interface_binding: Optional[dict] = None,
    evidence_refs: Optional[list] = None,
    status: str = "active",
    resource_path: Optional[str] = None,
    ingestion_context_id: Optional[str] = None,
    created_by: str,
) -> dict:
    """
    Bind one implementation to one procedure in one role, idempotently.

    Identity among live rows is `(procedure_id, implementation_id, role)`
    -- the partial unique index `idx_procedure_implementations_identity`,
    `WHERE t_invalid IS NULL`. Re-binding the same triple is a no-op that
    returns the existing binding with `reused=True`; it never opens a
    second row or overwrites the first (bi-temporal invalidate-and-append
    -- a real change closes the old row via `close_binding` and appends a
    new one).

    `procedure_id` is the STABLE `procedures.procedure_id`, not a version
    row id -- a binding outlives any single procedure version.

    Returns `{"id": <binding row id>, "reused": <bool>}`.
    """
    _require_role(role)
    _require_status(status)

    new_id = str(uuid7())
    async with tenant_transaction(pool, TenantScope.commons()) as conn:
        row = await conn.fetchrow(
            _BIND_INSERT_SQL,
            new_id, procedure_id, implementation_id, role,
            implementation_version, implementation_version_constraint,
            supported_steps or [], supported_capabilities or [],
            applicability or {}, interface_binding or {},
            evidence_refs or [], status, resource_path,
            ingestion_context_id, created_by,
        )
        if row is not None:
            return {"id": str(row["id"]), "reused": False}

        live = await conn.fetchrow(
            _BIND_SELECT_LIVE_SQL, procedure_id, implementation_id, role,
        )
        if live is None:
            # Insert conflicted, then the live row was closed before we
            # could read it. Do not fabricate an id.
            raise ProcedureImplementationError(
                "binding conflicted on insert but no live row is present "
                "to return -- retry"
            )
        return {"id": str(live["id"]), "reused": True}


# ---------------------------------------------------------------------------
# read helpers
# ---------------------------------------------------------------------------

# `i.*` yields the full implementations row (so `id` == the implementation
# id, matching `implementation_registry.get_for_task`'s convention). The
# relation-edge columns are aliased so none collides with an
# `implementations` column (`status`/`version` exist on both tables).
_LIST_FOR_PROCEDURE_SQL = """
    SELECT
        i.*,
        pi.id                                AS binding_id,
        pi.implementation_id                 AS implementation_id,
        pi.role                              AS role,
        pi.status                            AS binding_status,
        pi.resource_path                     AS resource_path,
        pi.implementation_version            AS binding_implementation_version,
        pi.implementation_version_constraint AS implementation_version_constraint,
        pi.supported_steps                   AS supported_steps,
        pi.supported_capabilities            AS supported_capabilities,
        pi.applicability                     AS applicability,
        pi.interface_binding                 AS interface_binding,
        pi.evidence_refs                     AS evidence_refs,
        pi.t_valid                           AS binding_t_valid
    FROM procedure_implementations pi
    JOIN implementations i ON i.id = pi.implementation_id
    WHERE pi.procedure_id = $1::uuid AND pi.t_invalid IS NULL
"""


async def list_implementations_for_procedure(
    pool: asyncpg.Pool,
    procedure_id: str,
    *,
    roles: Optional[list[str]] = None,
    status: Optional[str] = "active",
) -> list[dict]:
    """
    Live bindings for one procedure, each joined to its `implementations`
    row for name/kind/provider/version/locator/invocation.

    Return shape: the full `implementations` row (so `id` is the
    implementation id, `status`/`version` are the implementation's) PLUS
    the relation-edge fields under distinct keys -- `binding_id`,
    `implementation_id`, `role`, `binding_status`, `resource_path`,
    `binding_implementation_version`, `implementation_version_constraint`,
    `supported_steps`, `supported_capabilities`, `applicability`,
    `interface_binding`, `evidence_refs`, `binding_t_valid`. This is
    `implementation_registry.get_for_task`'s shape widened with edge
    metadata -- NOT narrowed -- so a `get_for_task` consumer keeps working
    on the same `id`/`kind`/`provider` keys.

    `status` filters the BINDING lifecycle (`pi.status`); pass `None` for
    an administrative view of every live binding regardless of lifecycle.
    `roles`, if given, restricts to those edge roles.
    """
    _require_roles_filter(roles)
    if status is not None:
        _require_status(status)

    params: list[Any] = [procedure_id]
    sql = _LIST_FOR_PROCEDURE_SQL
    if status is not None:
        params.append(status)
        sql += f" AND pi.status = ${len(params)}"
    if roles:
        params.append(list(roles))
        sql += f" AND pi.role = ANY(${len(params)}::text[])"
    sql += " ORDER BY pi.t_valid DESC"

    rows = await pool.fetch(sql, *params)
    return [_row_to_dict(r) for r in rows]


# The reverse direction -- one implementation, the procedures it supports
# (Sec B23's Graphify example). Joined to the current live version row of
# each procedure for a human-readable name/goal.
_LIST_FOR_IMPLEMENTATION_SQL = """
    SELECT
        pi.id                     AS binding_id,
        pi.procedure_id           AS procedure_id,
        pi.implementation_id      AS implementation_id,
        pi.role                   AS role,
        pi.status                 AS binding_status,
        pi.resource_path          AS resource_path,
        pi.supported_steps        AS supported_steps,
        pi.supported_capabilities AS supported_capabilities,
        pi.applicability          AS applicability,
        pi.interface_binding      AS interface_binding,
        pi.evidence_refs          AS evidence_refs,
        pi.t_valid                AS binding_t_valid,
        p.id                      AS procedure_row_id,
        p.name                    AS procedure_name,
        p.goal                    AS procedure_goal,
        p.version                 AS procedure_version
    FROM procedure_implementations pi
    LEFT JOIN LATERAL (
        SELECT id, name, goal, version
        FROM procedures
        WHERE procedure_id = pi.procedure_id AND t_invalid IS NULL
        ORDER BY version DESC
        LIMIT 1
    ) p ON TRUE
    WHERE pi.implementation_id = $1::uuid AND pi.t_invalid IS NULL
"""


async def list_procedures_for_implementation(
    pool: asyncpg.Pool,
    implementation_id: str,
    *,
    roles: Optional[list[str]] = None,
) -> list[dict]:
    """
    Every live binding for one implementation, newest first, each carrying
    the stable `procedure_id`, the current live version row's
    `procedure_row_id`/`procedure_name`/`procedure_goal`/
    `procedure_version`, and the edge metadata. `roles` restricts to those
    edge roles.
    """
    _require_roles_filter(roles)

    params: list[Any] = [implementation_id]
    sql = _LIST_FOR_IMPLEMENTATION_SQL
    if roles:
        params.append(list(roles))
        sql += f" AND pi.role = ANY(${len(params)}::text[])"
    sql += " ORDER BY pi.t_valid DESC"

    rows = await pool.fetch(sql, *params)
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# mutators
# ---------------------------------------------------------------------------

_CLOSE_SQL = """
    UPDATE procedure_implementations
    SET t_invalid = now()
    WHERE id = $1::uuid AND t_invalid IS NULL
"""


async def close_binding(pool: asyncpg.Pool, *, binding_id: str, closed_by: str) -> None:
    """
    Close a live binding (bi-temporal: `t_invalid = now()`, the row is
    never deleted). A no-op on an already-closed or unknown id.

    `closed_by` is accepted for call-site symmetry but not persisted --
    see the module docstring's HONEST LIMITS.
    """
    _ = closed_by  # documented: no actor column on this edge for a close.
    async with tenant_transaction(pool, TenantScope.commons()) as conn:
        await conn.execute(_CLOSE_SQL, binding_id)


# Append-if-absent: `||` grows the JSONB array, the `@>` guard makes the
# statement a no-op when the ref is already present (dedupe without a
# read-modify-write round trip).
_ADD_EVIDENCE_SQL = """
    UPDATE procedure_implementations
    SET evidence_refs = evidence_refs || to_jsonb($2::text)
    WHERE id = $1::uuid AND t_invalid IS NULL
      AND NOT (evidence_refs @> to_jsonb($2::text))
"""


async def add_evidence_ref(pool: asyncpg.Pool, *, binding_id: str, evidence_id: str) -> None:
    """
    Append one `evidence` row id to a live binding's `evidence_refs`,
    deduped. Per Sec B23 this is how a binding records "it realizes this
    procedure well" -- a pointer at recorded evaluation, never a synthetic
    numeric score. A no-op on an already-closed/unknown id or a duplicate
    ref.
    """
    async with tenant_transaction(pool, TenantScope.commons()) as conn:
        await conn.execute(_ADD_EVIDENCE_SQL, binding_id, str(evidence_id))
