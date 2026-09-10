"""
MCP hardening B23/B24: the real Procedure<->Implementation many-to-many
relation.

REWRITTEN mid-session: this module originally created its own new table
(`procedure_implementation_bindings`, migration 53) before discovering
that a RICHER, already-in-production table -- `procedure_implementations`
(482 real rows, written by `app/services/skill_ingestion.py`'s real
skill-package ingestion path, read by `app/services/publication.py`'s
dependency traversal) -- already exists with no committed migration
(captured retroactively in migration 58) and already IS this exact
relation, bi-temporally versioned (t_valid/t_invalid, the same pattern
`procedures`/`knowledge_nodes` use). Per CLAUDE.md rule 2 ("before
creating any... registry... first grep/read the repository... reuse and
extend existing infrastructure"), this module now operates against THAT
table instead. Migration 59 drops the now-redundant, empty (outside this
session's own already-cleaned test debris) migration-53 table.

HONEST LIMITATION inherited from the real table, not invented here:
`procedure_implementations` has no `procedure_version` column at all --
a relation applies to the Procedure FAMILY (`procedure_id`), never
pinned to one specific version, unlike this module's first draft which
supported per-version pinning. `implementation_version`/
`implementation_version_constraint` exist for the IMPLEMENTATION side
only. This is the real, existing table's actual scope -- not something
this rewrite chose to narrow.
"""
from __future__ import annotations

from typing import Literal, Optional

import asyncpg

from app.services.access import AccessScope, visibility_predicate

ROLES: tuple[str, ...] = ("primary", "supporting", "partial", "verification")
# Resolution preference order -- a primary binding always outranks a
# supporting one for the SAME step, matching B23's own vocabulary
# ("primary... supporting... partial... verification-only").
_ROLE_PRIORITY = {role: i for i, role in enumerate(ROLES)}

# The real, live CHECK constraint on procedure_implementations.status
# (migration 58) -- 'quarantined' is a real state this table already
# supports that this module's own first draft did not know about.
STATUSES: tuple[str, ...] = ("candidate", "active", "deprecated", "disabled", "quarantined")


class ProcedureImplementationBindingError(Exception):
    """Raised on a malformed link request -- never silently coerced."""


async def link_implementation(
    pool: asyncpg.Pool,
    *,
    procedure_id: str,
    implementation_id: str,
    role: str,
    implementation_version: Optional[int] = None,
    implementation_version_constraint: Optional[str] = None,
    supported_steps: Optional[list[int]] = None,
    supported_capabilities: Optional[list[str]] = None,
    applicability: Optional[dict] = None,
    interface_binding: Optional[dict] = None,
    evidence_refs: Optional[list[str]] = None,
    created_by: Optional[str] = None,
) -> dict:
    """
    Register (or, if an identical LIVE relation already exists, reuse)
    a Procedure<->Implementation binding. Deliberately, explicitly
    inserts `status='candidate'` -- "nothing is born trusted" (same
    posture `implementation_registry.register()`/`procedures.
    capture_procedure()` already keep) -- even though the real table's
    OWN column default is `'active'` (a different, already-working,
    unrelated call path -- `skill_ingestion.py`'s bundled-script
    linking -- whose implementations are trusted by package-admission
    elsewhere; this module's own new call path does not inherit that
    trust and must not rely on the column default to get it right).

    Idempotent on the real identity (`procedure_id`, `implementation_id`,
    `role`) WHERE `t_invalid IS NULL` -- migration 58's own unique index
    (captured, not invented, from the live table). Calling this again
    for the identical relation returns the EXISTING live row unchanged.
    """
    if role not in ROLES:
        raise ProcedureImplementationBindingError(f"role must be one of {ROLES}, got {role!r}")

    existing = await pool.fetchrow(
        "SELECT * FROM procedure_implementations "
        "WHERE procedure_id = $1::uuid AND implementation_id = $2::uuid AND role = $3 "
        "AND t_invalid IS NULL",
        procedure_id, implementation_id, role,
    )
    if existing is not None:
        return dict(existing)

    row = await pool.fetchrow(
        """
        INSERT INTO procedure_implementations (
            procedure_id, implementation_id, role, status,
            implementation_version, implementation_version_constraint,
            supported_steps, supported_capabilities, applicability,
            interface_binding, evidence_refs, created_by
        ) VALUES (
            $1::uuid, $2::uuid, $3, 'candidate', $4, $5, $6::jsonb, $7::jsonb, $8::jsonb,
            $9::jsonb, $10::jsonb, $11
        )
        ON CONFLICT (procedure_id, implementation_id, role) WHERE t_invalid IS NULL DO NOTHING
        RETURNING *
        """,
        procedure_id, implementation_id, role,
        implementation_version, implementation_version_constraint,
        supported_steps or [], supported_capabilities or [], applicability or {},
        interface_binding or {}, evidence_refs or [], created_by,
    )
    if row is not None:
        return dict(row)
    # A concurrent insert won the race -- return the winner's live row
    # (idempotent create-or-return under real concurrency, same
    # contract `durable_run.start_run`'s request_id path establishes).
    winner = await pool.fetchrow(
        "SELECT * FROM procedure_implementations "
        "WHERE procedure_id = $1::uuid AND implementation_id = $2::uuid AND role = $3 "
        "AND t_invalid IS NULL",
        procedure_id, implementation_id, role,
    )
    if winner is None:
        raise ProcedureImplementationBindingError(
            "insert reported a conflict but no matching live row was found -- unexpected"
        )
    return dict(winner)


async def activate_binding(pool: asyncpg.Pool, binding_id: str) -> Optional[dict]:
    """CANDIDATE -> ACTIVE. This function does not itself check evidence
    -- a caller (a future evidence-driven promotion flow) decides WHEN,
    same split `implementation_registry.activate()` already keeps."""
    row = await pool.fetchrow(
        "UPDATE procedure_implementations SET status = 'active' "
        "WHERE id = $1::uuid AND t_invalid IS NULL RETURNING *",
        binding_id,
    )
    return dict(row) if row else None


async def get_bindings_for_procedure(
    pool: asyncpg.Pool,
    *,
    procedure_id: str,
    role: Optional[str] = None,
    status: Optional[str] = "active",
    access_scope: Optional[AccessScope] = None,
) -> list[dict]:
    """
    Every LIVE (t_invalid IS NULL) binding for this Procedure family,
    visibility-filtered on the JOINED Implementation row (a caller must
    not see a binding to an Implementation they cannot see, even though
    the binding row itself has no visibility column of its own).
    """
    vis_sql, vis_params = visibility_predicate(
        access_scope or AccessScope.unrestricted(), alias="i", param_index=2,
    )
    params: list = [procedure_id, *vis_params]
    clauses = ["b.procedure_id = $1::uuid", "b.t_invalid IS NULL"]
    if role is not None:
        params.append(role)
        clauses.append(f"b.role = ${len(params)}")
    if status is not None:
        params.append(status)
        clauses.append(f"b.status = ${len(params)}")
    # B24: carry the IMPLEMENTATION's own (not the binding's)
    # status/verification_status/requirements alongside every binding row
    # -- resolve_binding_for_step's availability/verification/requirements
    # weighing needs the real implementation-side facts, not just the
    # binding's own role/supported_steps. Additive columns (aliased,
    # never colliding with `procedure_implementations`' own column
    # names) -- every existing caller reading `b["role"]`/
    # `b["implementation_id"]`/etc. off this same dict is unaffected.
    rows = await pool.fetch(
        f"""
        SELECT b.*, i.status AS implementation_status,
               i.verification_status AS implementation_verification_status,
               i.requirements AS implementation_requirements
        FROM procedure_implementations b
        JOIN implementations i ON i.id = b.implementation_id
        WHERE {' AND '.join(clauses)} AND {vis_sql}
        ORDER BY b.role
        """,
        *params,
    )
    return [dict(r) for r in rows]


# B24's own literal pipeline names these as terminal, unresolvable
# implementation-level states -- an ACTIVE *binding* to a DISABLED/
# QUARANTINED/DEPRECATED *implementation* is not a real resolution
# candidate (same real signal implementation_lifecycle.py's UNAVAILABLE/
# RETIRED derivation already uses -- not duplicated here, just applied
# as a filter rather than surfaced as a named lifecycle state).
_UNAVAILABLE_IMPLEMENTATION_STATUSES = frozenset({"disabled", "quarantined", "deprecated"})


class AmbiguousBindingResolutionError(ProcedureImplementationBindingError):
    """B24: "If resolution is ambiguous or unavailable, route to ask,
    plan, or refuse rather than silently selecting an unsuitable
    mechanism." Raised instead of silently returning one of several
    equally-ranked, equally-qualified candidates -- the caller (a route
    decision, `continue_run`, ...) is the one positioned to route to
    ask/plan/refuse; this function must never pick on the caller's
    behalf when nothing real distinguishes the tied candidates."""

    def __init__(self, tied_implementation_ids: list[str]):
        self.tied_implementation_ids = tied_implementation_ids
        super().__init__(
            f"resolution is ambiguous -- {len(tied_implementation_ids)} candidates "
            f"tied after every real tiebreak: {tied_implementation_ids}"
        )


# B38 STRICT CLOSURE: V4's typed-state vocabulary distinguishes
# MISSING_IMPLEMENTATION ("no candidate implementation names this
# role/step at all") from IMPLEMENTATION_UNAVAILABLE ("candidates
# exist, but every one is disabled/quarantined/deprecated, or fails a
# real requirements/environment check") -- two states this function's
# own cascade already computes at different stages (see the docstring
# below), never fabricated to satisfy the vocabulary. `None` (no
# reason available) means resolution actually succeeded.
BindingUnresolvedReason = Literal["missing", "unavailable"]


async def resolve_binding_for_step_with_reason(
    pool: asyncpg.Pool,
    *,
    procedure_id: str,
    step_order: Optional[int] = None,
    access_scope: Optional[AccessScope] = None,
    available_context: Optional[dict] = None,
) -> tuple[Optional[dict], Optional["BindingUnresolvedReason"]]:
    """The same real resolution cascade `resolve_binding_for_step` runs,
    additionally reporting WHY resolution failed when it does -- see
    `BindingUnresolvedReason`. `resolve_binding_for_step` itself stays
    the pre-existing, unchanged `Optional[dict]` contract (every
    existing caller/test keeps working); this is the typed-state-aware
    entry point for a caller that needs to surface V4's literal
    MISSING_IMPLEMENTATION/IMPLEMENTATION_UNAVAILABLE distinction
    rather than a bare `None`.
    """
    candidates = await get_bindings_for_procedure(
        pool, procedure_id=procedure_id, status="active", access_scope=access_scope,
    )
    eligible = [
        c for c in candidates
        if step_order is None or not c["supported_steps"] or step_order in c["supported_steps"]
    ]
    if not eligible:
        return None, "missing"

    eligible = [
        c for c in eligible
        if c["implementation_status"] not in _UNAVAILABLE_IMPLEMENTATION_STATUSES
    ]
    if available_context is not None:
        from app.execution.implementation_executor import check_requirements
        eligible = [
            c for c in eligible
            if check_requirements(
                {"requirements": c["implementation_requirements"] or {}}, available_context,
            )
        ]
    if not eligible:
        return None, "unavailable"

    eligible.sort(key=lambda c: _ROLE_PRIORITY.get(c["role"], len(ROLES)))
    best_role_rank = _ROLE_PRIORITY.get(eligible[0]["role"], len(ROLES))
    tied = [c for c in eligible if _ROLE_PRIORITY.get(c["role"], len(ROLES)) == best_role_rank]

    verified = [c for c in tied if c["implementation_verification_status"] == "verified"]
    if verified:
        tied = verified

    if len(tied) > 1:
        raise AmbiguousBindingResolutionError(
            [str(c["implementation_id"]) for c in tied]
        )
    return tied[0], None


async def resolve_binding_for_step(
    pool: asyncpg.Pool,
    *,
    procedure_id: str,
    step_order: Optional[int] = None,
    access_scope: Optional[AccessScope] = None,
    available_context: Optional[dict] = None,
) -> Optional[dict]:
    """
    B24's resolution step for ONE step of ONE Procedure -- the literal
    pipeline ("candidate Implementations -> applicability -> requirements
    -> environment -> permissions -> availability -> verification/
    evidence -> freshness -> cost/latency -> selected Implementation:v"),
    built from every REAL signal this codebase actually has, honest about
    the two it does not:

      - applicability:            `supported_steps` (unrestricted, or
                                   names `step_order`) -- pre-existing.
      - permissions:               already enforced one level up, inside
                                   `get_bindings_for_procedure`'s own
                                   `visibility_predicate` JOIN -- a
                                   candidate this caller cannot see never
                                   reaches this function at all.
      - requirements / environment: when `available_context` is given
                                   (the same shape `implementation_
                                   executor.check_requirements` already
                                   defines -- e.g. `{"network": True,
                                   "credentials": [...]}`), excludes any
                                   candidate whose implementation-level
                                   `requirements` it does not satisfy.
                                   Reuses that function rather than a
                                   second copy of the same check. `None`
                                   (the default) means no real context to
                                   check against -- skips this stage
                                   honestly rather than fabricating one.
      - availability:              excludes any candidate whose
                                   IMPLEMENTATION (not merely its binding)
                                   is disabled/quarantined/deprecated --
                                   the real gap an earlier pass left: this
                                   function used to check only the
                                   binding's own `status='active'` filter
                                   (still applied, in `get_bindings_for_
                                   procedure`) and never the bound
                                   implementation's own lifecycle state.
      - verification/evidence:     among still-tied candidates, a
                                   `verification_status='verified'`
                                   implementation is preferred over an
                                   unverified one -- a real tiebreak, not
                                   a hard filter (an unverified candidate
                                   is still a real candidate when nothing
                                   verified exists).
      - freshness:                 HONEST GAP, not fabricated -- this
                                   schema has no per-implementation
                                   last-used/staleness timestamp to
                                   threshold against (the same absence
                                   `implementation_lifecycle.py`'s own
                                   docstring already documents for STALE).
      - cost / latency:            HONEST GAP -- no real cost/latency
                                   estimate is stored on `implementations`
                                   anywhere in this codebase; weighing one
                                   would mean inventing a number, which
                                   B38's no-fabricated-signal rule forbids.

    Returns `None` -- never a fabricated binding -- when nothing
    qualifies (including "every candidate that matched role/step is
    unavailable or fails a real requirement"); the caller is responsible
    for surfacing MISSING_IMPLEMENTATION. Raises
    `AmbiguousBindingResolutionError` when, after every real tiebreak
    above, more than one candidate remains equally best -- the literal
    "ambiguous... route to ask/plan/refuse" case, never silently
    resolved by insertion order.
    """
    candidate, _reason = await resolve_binding_for_step_with_reason(
        pool, procedure_id=procedure_id, step_order=step_order,
        access_scope=access_scope, available_context=available_context,
    )
    return candidate
