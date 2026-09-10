"""
ProcedureClaimRef -- the typed, role-bearing relation between one
Procedure *version* and one Claim (migration 52, `db/52_procedure_claim_refs.sql`).

WHY THIS EXISTS
    Until this module, "which procedures depend on this claim?" was
    answered by `claim_impact.find_procedures_referencing_claim` doing a
    JSONB containment scan over ONE of a procedure's claim-bearing arrays
    (`procedures.preconditions @> '[{"claim_id": ...}]'`), with no notion
    of WHY the claim is referenced. A claim flip therefore marked *every*
    referencing procedure stale -- including ones that only cite the claim
    as explanatory rationale. V4-hardening "CLAIM INVALIDATION" requires
    the opposite: only STRONG roles (precondition / applicability /
    assumption) force a stale/revalidation; EXPLANATORY roles (rationale /
    decision / expected-effect / failure-mode / verification) are recorded
    for provenance and never auto-invalidate.

WHAT THIS IS NOT
    Not claims-inside-procedures. The relation lives in its own table; the
    Procedure version and the Claim stay independent, independently
    searchable, independently versioned. `procedures.preconditions` JSONB
    is untouched and still read as a COMPATIBILITY fallback by
    `claim_impact` -- this module is the primary index, not a replacement
    for the old scan while a corpus still carries only the JSON form.

HONEST SCOPE LIMITS
    - No in-migration backfill (fresh-start rule). `backfill_refs_from_
      preconditions()` below is the explicit, out-of-band, idempotent
      corpus backfill for existing rows; it is a plain callable, never run
      from a migration.
    - The readers here degrade to `[]` (not an error) when
      `procedure_claim_refs` does not exist yet -- a database mid-rollout
      where migration 52 has not been applied. Callers that also consult
      the JSON scan (`claim_impact`) therefore keep working unchanged on
      such a database.
    - `claim_version` is only ever a nullable snapshot of the claim's
      logical version (which lives in `knowledge_nodes.properties`) at
      authoring time. Nothing here reconciles it after the fact.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import asyncpg

from app.services.access import TenantScope, tenant_transaction
from app.utils.ids import uuid7

logger = logging.getLogger(__name__)

# The 8-value role vocabulary, verbatim from migration 52's CHECK
# constraint. Kept here as the single Python-side source of truth so a
# bad role fails loudly in the service layer before any SQL is emitted.
ROLES: tuple[str, ...] = (
    "PRECONDITION", "APPLICABILITY", "ASSUMPTION",
    "RATIONALE", "DECISION", "EXPECTED_EFFECT",
    "FAILURE_MODE", "VERIFICATION",
)

# STRONG roles force a stale/revalidation when the claim changes. This is
# the non-compensatory half of the invalidation rule: any single strong
# ref is a disqualification for "still trustworthy", never a score.
STRONG_ROLES: tuple[str, ...] = ("PRECONDITION", "APPLICABILITY", "ASSUMPTION")

# EXPLANATORY roles are recorded for provenance and NEVER auto-invalidate
# the procedure -- a caller may still choose to log/annotate them.
EXPLANATORY_ROLES: tuple[str, ...] = tuple(r for r in ROLES if r not in STRONG_ROLES)

_REF_COLUMNS = (
    "id", "procedure_id", "procedure_version", "claim_id", "claim_version",
    "role", "step_refs", "ref_origin", "extractor_version",
    "ingestion_context_id", "created_by", "t_valid", "t_created",
)


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


async def add_procedure_claim_ref(
    pool: asyncpg.Pool,
    *,
    procedure_id: str,
    procedure_version: int,
    claim_id: str,
    role: str,
    claim_version: Optional[int] = None,
    step_refs: Optional[list] = None,
    ref_origin: str = "derived",
    extractor_version: Optional[str] = None,
    ingestion_context_id: Optional[str] = None,
    created_by: str,
) -> str:
    """
    Insert one typed Procedure<->Claim ref, idempotently.

    `role` is validated against `ROLES` here (ValueError otherwise) so a
    bad value never reaches the database CHECK. The write is
    `INSERT ... ON CONFLICT (procedure_id, procedure_version, claim_id,
    role) DO NOTHING RETURNING id`; on a real conflict (the ref already
    exists) `RETURNING` yields nothing, so we fall back to selecting the
    live row's id. Either way the caller gets the id of the ref that is
    now live for that (procedure version, claim, role) tuple.

    id is `uuid7()` (B-tree locality, house rule); the write runs inside
    `tenant_transaction` (house write-path idiom -- binds `app.tenant_id`
    transaction-locally for the RLS backstop even though this table
    carries no tenant column of its own).
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    if ref_origin not in ("authored", "derived", "backfilled"):
        raise ValueError(
            f"ref_origin must be 'authored', 'derived' or 'backfilled', got {ref_origin!r}"
        )
    if procedure_version < 1:
        raise ValueError(f"procedure_version must be >= 1, got {procedure_version!r}")

    new_id = str(uuid7())
    steps = step_refs if step_refs is not None else []

    async with tenant_transaction(pool, TenantScope.commons()) as conn:
        inserted = await conn.fetchval(
            "INSERT INTO procedure_claim_refs "
            "(id, procedure_id, procedure_version, claim_id, claim_version, role, "
            " step_refs, ref_origin, extractor_version, ingestion_context_id, created_by) "
            "VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6, $7::jsonb, $8, $9, $10::uuid, $11) "
            "ON CONFLICT (procedure_id, procedure_version, claim_id, role) DO NOTHING "
            "RETURNING id",
            new_id, procedure_id, procedure_version, claim_id, claim_version, role,
            steps, ref_origin, extractor_version, ingestion_context_id, created_by,
        )
        if inserted is not None:
            return str(inserted)

        existing = await conn.fetchval(
            "SELECT id FROM procedure_claim_refs "
            "WHERE procedure_id = $1::uuid AND procedure_version = $2 "
            "  AND claim_id = $3::uuid AND role = $4 AND t_invalid IS NULL",
            procedure_id, procedure_version, claim_id, role,
        )
        return str(existing) if existing is not None else new_id


async def list_claim_refs_for_procedure(
    pool: asyncpg.Pool,
    procedure_id: str,
    procedure_version: int,
    *,
    roles: Optional[list[str]] = None,
) -> list[dict]:
    """
    Live (`t_invalid IS NULL`) typed refs authored by one procedure
    version, optionally filtered to a subset of roles. Oldest first.

    Returns `[]` -- not an error -- if `procedure_claim_refs` does not
    exist yet (migration 52 not applied on this database).
    """
    params: list[Any] = [procedure_id, procedure_version]
    role_clause = ""
    if roles:
        params.append(list(roles))
        role_clause = f"AND role = ANY(${len(params)}::text[])"
    try:
        rows = await pool.fetch(
            "SELECT id, procedure_id, procedure_version, claim_id, claim_version, "
            "       role, step_refs, ref_origin, extractor_version, "
            "       ingestion_context_id, created_by, t_valid, t_created "
            "FROM procedure_claim_refs "
            "WHERE procedure_id = $1::uuid AND procedure_version = $2 "
            f"  AND t_invalid IS NULL {role_clause} "
            "ORDER BY t_created ASC",
            *params,
        )
    except asyncpg.UndefinedTableError:
        logger.warning("procedure_claim_refs missing; migration 52 not applied?")
        return []
    return [_row_to_dict(r) for r in rows]


async def list_procedures_for_claim(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    roles: Optional[list[str]] = None,
) -> list[dict]:
    """
    Live typed refs TO one claim, joined to the current live `procedures`
    row so each result carries both the stable (`procedure_id`,
    `procedure_version`) handle AND the `id` of the exact version row
    (`mark_procedure_stale` needs that row id) plus `name` for reports.

    A ref whose procedure version is no longer live is dropped by the
    JOIN -- invalidation only ever acts on live procedures.

    Optional `roles` filter (e.g. `STRONG_ROLES`) narrows to those roles.
    Returns `[]` -- not an error -- if `procedure_claim_refs` does not
    exist yet (migration 52 not applied on this database).
    """
    params: list[Any] = [claim_id]
    role_clause = ""
    if roles:
        params.append(list(roles))
        role_clause = f"AND r.role = ANY(${len(params)}::text[])"
    try:
        rows = await pool.fetch(
            "SELECT p.id AS id, p.name AS name, "
            "       r.procedure_id AS procedure_id, r.procedure_version AS procedure_version, "
            "       r.role AS role, r.claim_version AS claim_version "
            "FROM procedure_claim_refs r "
            "JOIN procedures p "
            "  ON p.procedure_id = r.procedure_id "
            " AND p.version = r.procedure_version "
            " AND p.t_invalid IS NULL "
            f"WHERE r.claim_id = $1::uuid AND r.t_invalid IS NULL {role_clause}",
            *params,
        )
    except asyncpg.UndefinedTableError:
        logger.warning("procedure_claim_refs missing; migration 52 not applied?")
        return []
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "procedure_id": str(r["procedure_id"]),
            "procedure_version": r["procedure_version"],
            "role": r["role"],
            "claim_version": r["claim_version"],
        }
        for r in rows
    ]


async def close_procedure_claim_refs(
    pool: asyncpg.Pool,
    *,
    procedure_id: str,
    procedure_version: int,
    closed_by: str,
) -> int:
    """
    Close (set `t_invalid = now()`) every live ref authored by one
    procedure version -- called when a NEW procedure version re-authors
    its claim set, so the old version's refs stop reading as current
    without being deleted (supersede-by-append, bi-temporal rule).

    `closed_by` is recorded on nothing structural here (the table has no
    `closed_by` column); it is accepted for parity with the other write
    signatures and for a future audit hook, and is logged. Returns the
    number of refs closed.
    """
    async with tenant_transaction(pool, TenantScope.commons()) as conn:
        result = await conn.execute(
            "UPDATE procedure_claim_refs SET t_invalid = now() "
            "WHERE procedure_id = $1::uuid AND procedure_version = $2 "
            "  AND t_invalid IS NULL",
            procedure_id, procedure_version,
        )
    # asyncpg returns e.g. "UPDATE 3"
    try:
        closed = int(result.split()[-1])
    except (ValueError, IndexError, AttributeError):
        closed = 0
    if closed:
        logger.info(
            "closed %d procedure_claim_refs for %s v%s (by %s)",
            closed, procedure_id, procedure_version, closed_by,
        )
    return closed


async def backfill_refs_from_preconditions(
    pool: asyncpg.Pool,
    *,
    limit: int = 500,
) -> dict:
    """
    Deterministic, idempotent, duplicate-safe corpus backfill: for each
    live `procedures` row, read `preconditions` JSONB, and for every
    element carrying a `claim_id`, ensure a `role='PRECONDITION'`,
    `ref_origin='backfilled'` typed ref exists.

    This is the out-of-band backfill for data that predates migration 52.
    It is NOT run from a migration (fresh-start rule) -- it is an explicit
    callable an operator invokes once per environment.

    Idempotency: re-running skips any (procedure version, claim_id,
    PRECONDITION) triple that already has a live ref. The underlying
    `add_procedure_claim_ref` is itself ON CONFLICT DO NOTHING, so a
    concurrent run cannot create a duplicate either.

    Returns `{"procedures_scanned", "refs_created", "already_present"}`.
    """
    rows = await pool.fetch(
        "SELECT id, procedure_id, version, preconditions FROM procedures "
        "WHERE t_invalid IS NULL "
        "ORDER BY t_created ASC "
        "LIMIT $1",
        limit,
    )

    procedures_scanned = 0
    refs_created = 0
    already_present = 0

    for row in rows:
        procedures_scanned += 1
        preconditions = row["preconditions"] or []
        procedure_id = str(row["procedure_id"])
        procedure_version = row["version"]

        seen_claims: set[str] = set()
        for element in preconditions:
            if not isinstance(element, dict):
                continue
            claim_id = element.get("claim_id")
            if not claim_id:
                continue
            claim_id = str(claim_id)
            if claim_id in seen_claims:
                continue
            seen_claims.add(claim_id)

            existing = await pool.fetchval(
                "SELECT 1 FROM procedure_claim_refs "
                "WHERE procedure_id = $1::uuid AND procedure_version = $2 "
                "  AND claim_id = $3::uuid AND role = 'PRECONDITION' "
                "  AND t_invalid IS NULL",
                procedure_id, procedure_version, claim_id,
            )
            if existing:
                already_present += 1
                continue

            await add_procedure_claim_ref(
                pool,
                procedure_id=procedure_id,
                procedure_version=procedure_version,
                claim_id=claim_id,
                role="PRECONDITION",
                ref_origin="backfilled",
                created_by="backfill_refs_from_preconditions",
            )
            refs_created += 1

    return {
        "procedures_scanned": procedures_scanned,
        "refs_created": refs_created,
        "already_present": already_present,
    }
