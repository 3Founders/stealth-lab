"""
Durable Implementation Registry (IMPLEMENTATION REGISTRY directive Sec 24),
backed by `db/33_implementation_registry.sql`.

WHERE THIS SITS relative to what already exists (read `app/execution/
implementations.py` and `app/execution/providers.py` in full before
touching this file -- neither is modified here):

  - `implementations.py` owns the closed KIND vocabulary
    (`IMPLEMENTATION_KINDS`) and answers "is this KIND runnable at all,
    by anything, right now" (`resolve_implementation`). It knows nothing
    about a specific, durable, named implementation identity.
  - `providers.py` owns the real EXECUTION mechanism for two kinds
    (`FrontierProvider`, `DeterministicProvider`) via the
    `ImplementationProvider.discover/inspect/execute` contract. It also
    knows nothing about durable identity -- its registry
    (`PROVIDER_REGISTRY`) is keyed by KIND, one class per kind, not one
    row per concrete implementation.
  - THIS module is the missing piece: a durable, addressable,
    queryable ROW per concrete implementation (a specific
    provider+name+version), the object `PlanNode.implementation_id` and
    `executions.implementation_id` (both real, typed columns since
    migration 23, confirmed by grep to have had zero real writers before
    this module) can actually reference. It stores WHICH implementation
    exists; `providers.py` still owns HOW to run one once resolved.

Nothing here duplicates the KIND vocabulary or the execution mechanism --
`register()` validates `kind` against the exact same closed set the DB
CHECK constraint enforces (`implementations.py`'s five kinds plus the
three directive-named future-compatible kinds: wasm/computer_use/api),
and this module never calls `.execute()` on anything -- that remains
`app/execution/implementation_executor.py`'s job (a separate module, by
design, matching this repo's plans.py/plan_persistence.py split between
compiling a shape and persisting/running it).

WRITE-PATH CONVENTION: mirrors `procedures.py::capture_procedure` exactly
-- a plain `pool.fetchrow` INSERT, app-side `uuid7()` id, no
`tenant_transaction()` wrapper, because `implementations` (like
`procedures`) carries no `tenant_id` column (confirmed: this migration
did not add one, matching `procedures`' own precedent per
`procedure_graph_api.py`'s documented finding). Reads use
`visibility_predicate()` alone for the same reason.

V0-GATE SCOPE, NOT PROVENANCE: `v0_gate.validate_scope()` is reused
verbatim when a caller supplies `scope_type`. `v0_gate.validate_
provenance()` is deliberately NOT called here -- that gate validates a
specific claims/procedures vocabulary ('system_pending_review',
'company_ingested', ...) describing HOW a row was extracted from an
episode, which does not fit an implementation's real origin story (a
human/system registering a known provider integration, or eventually an
LLM-generation pipeline for WASM -- directive Sec 57). `created_by` is
required instead, the honest minimum attribution this module can enforce
today; a real `provenance`-shaped gate for implementations, if wanted
later, is new work, not a retrofit of the claims/procedures one.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.execution.implementations import IMPLEMENTATION_KINDS
from app.services.access import AccessScope, visibility_predicate
from app.utils.ids import uuid7

# The DB CHECK's exact vocabulary (implementations_kind_chk,
# db/33_implementation_registry.sql): IMPLEMENTATION_KINDS' five real
# kinds plus the three directive-named future-compatible ones a row may
# be REGISTERED as ahead of a real Python-side executor existing
# (directive Sec 6) -- registering one is not a claim it is runnable;
# that claim is `providers.py::discover_providers()`'s job alone.
REGISTRABLE_KINDS: tuple[str, ...] = IMPLEMENTATION_KINDS + ("wasm", "computer_use", "api")

STATUS_VALUES = ("candidate", "active", "deprecated", "disabled", "quarantined")
VERIFICATION_STATUS_VALUES = ("unverified", "verified")


class ImplementationRegistryError(ValueError):
    """A caller-supplied value violates this module's own contract
    (unknown kind/status, blank name/provider). Raised before any SQL
    runs -- same discipline as ImplementationViolation in
    implementations.py: a producer-side contract violation, not an
    internal error."""


def _require_registrable_kind(kind: str) -> None:
    if kind not in REGISTRABLE_KINDS:
        raise ImplementationRegistryError(
            f"unknown implementation kind {kind!r} (valid: {REGISTRABLE_KINDS})"
        )


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    for k in ("id", "derived_from"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    return d


async def register(
    pool: asyncpg.Pool,
    *,
    name: str,
    kind: str,
    provider: str,
    created_by: str,
    description: Optional[str] = None,
    version: int = 1,
    locator: Optional[dict] = None,
    invocation: Optional[dict] = None,
    input_schema: Optional[dict] = None,
    output_schema: Optional[dict] = None,
    requirements: Optional[dict] = None,
    auth_requirements: Optional[dict] = None,
    resource_requirements: Optional[dict] = None,
    source_ref: Optional[str] = None,
    author: Optional[str] = None,
    license: Optional[str] = None,
    derived_from: Optional[str] = None,
    content_hash: Optional[str] = None,
    visibility: str = "public",
    owner_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    task_node_ids: Optional[list[str]] = None,
    execution_location: str = "stealth_hosted",
) -> dict:
    """
    Registers one new, durable implementation identity. Always starts
    `status='candidate'`/`verification_status='unverified'` -- nothing is
    born active or verified (same "nothing is born trusted" posture
    `procedures.py::capture_procedure` establishes for procedures).

    `(name, provider, version)` must be globally unique (DB unique index
    `idx_implementations_identity`) -- registering the same identity
    twice raises `asyncpg.UniqueViolationError`, never silently
    overwrites (directive Sec 19: never mutate a used implementation's
    identity silently).

    `task_node_ids`, if given, links this implementation to each task in
    the SAME transaction the implementation row is inserted in -- an
    implementation with no linked task is a real, storable state (it may
    be registered ahead of being wired to any task, or discovered without
    yet being attributed to one), not an error.

    `auth_requirements` must never carry a literal secret (directive
    Sec 13/50) -- this function does not scan for one (that would be a
    false sense of security over an open-ended JSONB shape), the caller
    contract is the one enforcement point, matching this repo's existing
    "the writer is responsible, storage does not police JSONB shape"
    posture (e.g. `evidence.success_criteria`).

    Returns the new row as a plain dict (`_row_to_dict` shape).
    """
    if visibility not in ("public", "private"):
        raise ValueError(f"visibility must be 'public' or 'private', got {visibility!r}")
    if execution_location not in ("stealth_hosted", "user_hosted", "third_party_hosted"):
        raise ValueError(
            f"execution_location must be 'stealth_hosted', 'user_hosted', or "
            f"'third_party_hosted', got {execution_location!r}"
        )
    _require_registrable_kind(kind)
    if not name.strip():
        raise ImplementationRegistryError("name must not be blank")
    if not provider.strip():
        raise ImplementationRegistryError("provider must not be blank")
    if not created_by:
        raise ImplementationRegistryError(
            "created_by is required -- an implementation with no real "
            "attribution is not registrable (this module's own minimum "
            "provenance requirement, see module docstring)"
        )

    resolved_scope_type = scope_type
    resolved_scope_entity_id = scope_entity_id
    if scope_type is not None:
        from app.services.v0_gate import validate_scope

        resolved_scope_type, resolved_scope_entity_id = validate_scope(
            scope_type, scope_entity_id,
        )

    new_id = str(uuid7())

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO implementations (
                    id, name, description, kind, provider, version,
                    locator, invocation, input_schema, output_schema,
                    requirements, auth_requirements, resource_requirements,
                    source_ref, author, license, derived_from, content_hash,
                    created_by, visibility, owner_id, scope_type, scope_entity_id,
                    execution_location
                ) VALUES (
                    $1::uuid, $2, $3, $4, $5, $6,
                    $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb,
                    $11::jsonb, $12::jsonb, $13::jsonb,
                    $14, $15, $16, $17::uuid, $18,
                    $19, $20::visibility_level, $21, $22, $23,
                    $24
                )
                RETURNING *
                """,
                new_id, name, description, kind, provider, version,
                locator or {}, invocation or {}, input_schema or {}, output_schema or {},
                requirements or {}, auth_requirements or {}, resource_requirements or {},
                source_ref, author, license, derived_from, content_hash,
                created_by, visibility, owner_id, resolved_scope_type, resolved_scope_entity_id,
                execution_location,
            )
            for task_node_id in (task_node_ids or []):
                await conn.execute(
                    """
                    INSERT INTO implementation_tasks (id, implementation_id, task_node_id, created_by)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4)
                    ON CONFLICT (implementation_id, task_node_id) DO NOTHING
                    """,
                    str(uuid7()), new_id, task_node_id, created_by,
                )
    return _row_to_dict(row)


async def get(pool: asyncpg.Pool, implementation_id: str, *, scope: AccessScope) -> Optional[dict]:
    """One implementation row by id, visibility-filtered. `None` for
    "does not exist" and "exists, not visible" alike -- same anti-
    enumeration posture every other Wave 1 reader in this codebase uses
    (see `claim_graph_api.get_claim`'s identical contract)."""
    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    row = await pool.fetchrow(
        f"SELECT * FROM implementations WHERE id = $1::uuid AND {vis_sql}",
        implementation_id, *vis_params,
    )
    return _row_to_dict(row) if row else None


async def get_for_task(
    pool: asyncpg.Pool, task_node_id: str, *, scope: AccessScope,
    status: Optional[str] = "active",
) -> list[dict]:
    """Every implementation linked to `task_node_id` via
    `implementation_tasks`, visibility-filtered. `status='active'` by
    default (directive Sec 34/38: a candidate/deprecated/disabled/
    quarantined implementation should not be an ordinary retrieval
    candidate) -- pass `status=None` to see every linked implementation
    regardless of lifecycle state (an administrative/inspection view,
    not the default resolution path)."""
    vis_sql, vis_params = visibility_predicate(scope, alias="i", param_index=2)
    params: list[Any] = [task_node_id, *vis_params]
    status_clause = ""
    if status is not None:
        params.append(status)
        status_clause = f"AND i.status = ${len(params)}"
    rows = await pool.fetch(
        f"""
        SELECT i.* FROM implementations i
        JOIN implementation_tasks it ON it.implementation_id = i.id
        WHERE it.task_node_id = $1::uuid AND {vis_sql} {status_clause}
        ORDER BY i.t_created DESC
        """,
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def list_implementations(
    pool: asyncpg.Pool,
    *,
    scope: AccessScope,
    kind: Optional[str] = None,
    provider: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Bounded, filtered listing -- never the entire table by default
    (`limit`, default 50, is a real bound, matching this codebase's
    established "never return the entire graph by default" posture)."""
    vis_sql, vis_params = visibility_predicate(scope, param_index=1)
    params: list[Any] = list(vis_params)
    clauses = [vis_sql]
    if kind is not None:
        _require_registrable_kind(kind)
        params.append(kind)
        clauses.append(f"kind = ${len(params)}")
    if provider is not None:
        params.append(provider)
        clauses.append(f"provider = ${len(params)}")
    if status is not None:
        if status not in STATUS_VALUES:
            raise ImplementationRegistryError(f"unknown status {status!r} (valid: {STATUS_VALUES})")
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    params.append(max(1, min(limit, 200)))
    rows = await pool.fetch(
        f"SELECT * FROM implementations WHERE {' AND '.join(clauses)} "
        f"ORDER BY t_created DESC LIMIT ${len(params)}",
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def resolve(
    pool: asyncpg.Pool,
    task_node_id: str,
    *,
    scope: AccessScope,
    hint_kinds: Optional[tuple[str, ...]] = None,
) -> Optional[dict]:
    """
    "Which concrete, durable implementation should satisfy this task
    node?" (directive Sec 25). A thin, honest resolver over `get_for_task`
    -- NOT a router (no cost/latency/capability weighing here; that is
    `app/execution/implementation_executor.py`'s job, a deliberately
    separate concern per this module's own docstring). This function only
    narrows "which of the task's ACTIVE, linked implementations match the
    caller's kind preference, if any" and returns the first match in
    recency order -- callers wanting real ranking (capability, cost,
    applicability) compose over `get_for_task`'s full list themselves.

    `hint_kinds`: an ordered preference tuple, same shape
    `PlanNode.implementation_hint`/`validate_implementation_hint` already
    use -- the first candidate whose `kind` is in `hint_kinds` wins,
    preference order respected. `None` means no preference: the most
    recently registered active implementation for this task wins.

    Returns `None` (never a fabricated pick) when no active, visible
    implementation is linked to this task at all.
    """
    candidates = await get_for_task(pool, task_node_id, scope=scope, status="active")
    if not candidates:
        return None
    if hint_kinds is None:
        return candidates[0]
    for kind in hint_kinds:
        for c in candidates:
            if c["kind"] == kind:
                return c
    return None


async def _transition(
    pool: asyncpg.Pool, implementation_id: str, *, new_status: str, timestamp_column: Optional[str],
) -> Optional[dict]:
    set_clause = f"status = $2"
    if timestamp_column:
        set_clause += f", {timestamp_column} = now()"
    row = await pool.fetchrow(
        f"UPDATE implementations SET {set_clause} WHERE id = $1::uuid RETURNING *",
        implementation_id, new_status,
    )
    return _row_to_dict(row) if row else None


async def activate(pool: asyncpg.Pool, implementation_id: str) -> Optional[dict]:
    """CANDIDATE -> ACTIVE. Real promotion, per directive Sec 33/62 --
    this function does not itself check evidence/capability; a caller
    (the future capability-driven promotion flow, directive Sec 62) is
    responsible for deciding WHEN promotion is warranted. Matches this
    repo's existing split between `procedures.py::record_execution_
    outcome` (decides) and `approve_procedure` (a separate, explicit
    action) -- kept as two concerns here too, not fused."""
    return await _transition(pool, implementation_id, new_status="active", timestamp_column=None)


async def deprecate(pool: asyncpg.Pool, implementation_id: str) -> Optional[dict]:
    """ACTIVE -> DEPRECATED (directive Sec 63). A new plan should not
    select a deprecated implementation by default -- enforced by
    `get_for_task`'s own `status='active'` default, not by this function
    deleting or hiding the row. Historical `executions.implementation_id`
    bindings referencing this id remain fully resolvable forever (the row
    is never deleted, only its own `status` column changes)."""
    return await _transition(pool, implementation_id, new_status="deprecated", timestamp_column="deprecated_at")


async def disable(pool: asyncpg.Pool, implementation_id: str) -> Optional[dict]:
    """Any status -> DISABLED. Stronger than deprecate: an implementation
    that must not be selected or executed at all (e.g. a security issue),
    not merely superseded by a newer version."""
    return await _transition(pool, implementation_id, new_status="disabled", timestamp_column="disabled_at")


async def quarantine(pool: asyncpg.Pool, implementation_id: str) -> Optional[dict]:
    """Any status -> QUARANTINED (directive Sec 5). Distinct from
    `disable`: quarantine is the same posture `procedures.py`'s own
    availability axis uses for "suspicious, held pending review" rather
    than "confirmed bad" -- both stop `get_for_task`'s default
    `status='active'` filter from selecting the row, but they mean
    different things to a human reviewing why."""
    return await _transition(pool, implementation_id, new_status="quarantined", timestamp_column="disabled_at")


async def verify(pool: asyncpg.Pool, implementation_id: str) -> Optional[dict]:
    """UNVERIFIED -> VERIFIED (directive Sec 61's separate axis from
    `status`). Same "a caller decides when, this function only performs
    the transition" split as `activate()`."""
    row = await pool.fetchrow(
        "UPDATE implementations SET verification_status = 'verified' WHERE id = $1::uuid RETURNING *",
        implementation_id,
    )
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Execution descriptor -- the stable machine-readable ABI between Stealth
# and any execution/replay/harness consumer (final-V1 §1, directive
# §22/§27/§28). ONE canonical projection of a registry row; consumers use
# THIS instead of each re-deriving the shape from raw columns.
# ---------------------------------------------------------------------------
DESCRIPTOR_VERSION = "impl-descriptor/1"

# Ordered so serialization is deterministic regardless of dict insertion order.
_DESCRIPTOR_FIELDS = (
    "descriptor_version", "implementation_id", "kind", "provider", "version",
    "status", "verification_status", "protocol", "locator", "invocation",
    "input_schema", "output_schema", "requirements", "auth_requirements",
    "resource_requirements",
)

# A value under auth_requirements is allowed ONLY if it is a reference, not a
# secret. Keys whose value is an inline secret are dropped from the descriptor.
_SECRETish_KEYS = ("token", "secret", "password", "passwd", "api_key", "apikey",
                   "private_key", "client_secret", "access_key", "bearer")
_REF_OK_KEYS = ("credential_ref", "ref", "secret_ref", "vault_path", "env",
                "env_var", "provider", "scheme", "required", "scopes")


def _protocol_for(row: dict[str, Any]) -> str:
    """Derive the wire protocol. Prefer an explicit locator/invocation
    field; otherwise map from kind. Never guesses a value the row can't
    support."""
    loc = row.get("locator") or {}
    inv = row.get("invocation") or {}
    for src in (loc, inv):
        if isinstance(src, dict):
            for k in ("protocol", "runtime", "scheme", "transport"):
                v = src.get(k)
                if isinstance(v, str) and v:
                    return v.lower()
    return {
        "frontier": "model", "slm": "model",
        "deterministic": "native", "human": "human",
        "tool": "mcp", "api": "https", "wasm": "wasm", "computer_use": "computer_use",
    }.get(row.get("kind", ""), "unknown")


def _sanitize_auth(auth: Any) -> dict[str, Any]:
    """Strip inline secret material -- the descriptor carries credential
    REFERENCES only (§24). A dict value that itself contains a secret-ish
    key with a non-empty string value is reduced to {'redacted': true}."""
    if not isinstance(auth, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in auth.items():
        kl = str(k).lower()
        if any(s in kl for s in _SECRETish_KEYS) and isinstance(v, str) and v and not kl.endswith("_ref"):
            out[k] = {"redacted": True}
            continue
        if isinstance(v, dict):
            inner = {ik: (iv if not (any(s in str(ik).lower() for s in _SECRETish_KEYS)
                                     and isinstance(iv, str) and iv) else {"redacted": True})
                     for ik, iv in v.items()}
            out[k] = inner
        else:
            out[k] = v
    return out


def descriptor(row: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic, secret-free execution descriptor for one implementation
    row (`_row_to_dict` / `get` / `resolve` shape). Pure function, no DB.

    Guarantees:
      - stable field set + order (`_DESCRIPTOR_FIELDS`), so two calls on
        the same row byte-serialize identically;
      - exact identity: `implementation_id` + `version` (a newer version
        is a different row -> a different descriptor; an already-bound
        descriptor never changes because the bound row never changes);
      - no secret material -- `auth_requirements` is sanitized to
        references only;
      - optional fields absent on the row serialize as `{}` (JSONB) so a
        consumer can rely on the key existing.
    """
    kind = row.get("kind")
    if kind is not None and kind not in REGISTRABLE_KINDS:
        raise ImplementationRegistryError(f"row has unknown kind {kind!r}")
    d = {
        "descriptor_version": DESCRIPTOR_VERSION,
        "implementation_id": str(row["id"]) if row.get("id") is not None else None,
        "kind": kind,
        "provider": row.get("provider"),
        "version": row.get("version"),
        "status": row.get("status"),
        "verification_status": row.get("verification_status"),
        "protocol": _protocol_for(row),
        "locator": row.get("locator") or {},
        "invocation": row.get("invocation") or {},
        "input_schema": row.get("input_schema") or {},
        "output_schema": row.get("output_schema") or {},
        "requirements": row.get("requirements") or {},
        "auth_requirements": _sanitize_auth(row.get("auth_requirements")),
        "resource_requirements": row.get("resource_requirements") or {},
    }
    return {k: d[k] for k in _DESCRIPTOR_FIELDS}


async def get_descriptor(
    pool: asyncpg.Pool, implementation_id: str, *, scope: AccessScope,
) -> Optional[dict[str, Any]]:
    """The descriptor for a scoped, visible implementation row, or None."""
    row = await get(pool, implementation_id, scope=scope)
    return descriptor(row) if row else None
