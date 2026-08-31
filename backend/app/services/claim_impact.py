"""
Real, confirmed gap closed here (task #35, `.scratch/final_architecture_audit.md`
§9): a precondition can now carry an optional `claim_id`
(`app/services/procedure_extraction/derive.py::precondition_with_claim`,
landed earlier the same day this module was written), but nothing yet
looks at "a claim just changed -- does any live procedure's precondition
point at it?" and tells that procedure it may no longer be trustworthy.
Procedures already have a real, working staleness mechanism
(`app/services/procedures.py::mark_procedure_stale`) -- this module does
NOT reimplement staleness. It only builds the trigger: find the affected
procedures, then call the existing primitive for each one.

Deliberately NOT wired into `claims.py`'s relation-writing functions
(`relate_claims` / `link_claims` / any future `supersede_claim`) by this
module -- `claims.py` is being edited by another agent in parallel right
now, and wiring the call in here would be a real, avoidable merge
conflict. `propagate_claim_change` below is a real, independently
callable, independently tested primitive; the orchestrator will wire it
into claims.py's relation-writing functions once both pieces have
independently landed.
"""
from __future__ import annotations

import asyncpg

from app.services.procedures import mark_procedure_stale


async def find_procedures_referencing_claim(pool: asyncpg.Pool, claim_id: str) -> list[dict]:
    """
    Real, bounded query against the LIVE `procedures` table
    (`t_invalid IS NULL` -- only the current version row of each procedure
    chain, never a superseded historical row) for any row whose
    `preconditions` JSONB array contains at least one element with
    `"claim_id": "<claim_id>"`.

    Query shape chosen: `preconditions @> $1::jsonb` with a constructed
    single-element probe array `[{"claim_id": "<claim_id>"}]`. Postgres's
    `@>` (jsonb containment) descends into `preconditions` as an array and
    matches if ANY element of the array is a superset of the probe
    object -- i.e. any element containing at least the key/value pair
    `claim_id: <claim_id>`, regardless of that element's other keys
    (`subject`/`predicate`/`object`) or its position. Verified live
    against a real inserted row before landing (a probe for an unrelated
    claim_id correctly returned zero rows; the probe for the real claim_id
    on the row correctly returned exactly that row and no other).

    Chose `@>` over the `EXISTS (SELECT 1 FROM jsonb_array_elements(...)
    elem WHERE elem->>'claim_id' = $1)` alternative (also verified live,
    also correct) because `@>` is the idiomatic Postgres containment
    operator for "does this JSONB array have an element like this", reads
    as a single expression rather than a correlated subquery, and is the
    operator a future GIN index on `preconditions` (`USING gin
    (preconditions jsonb_path_ops)`) would actually accelerate -- no such
    index exists on `procedures.preconditions` today (confirmed via
    `pg_indexes` this session), so both forms currently run as a bounded
    sequential scan over live procedure rows; `@>` is the one that stays
    correct and gets faster for free if that index is added later,
    without changing this function.

    Returns each matching row's `id` (the procedure VERSION row id --
    exactly what `mark_procedure_stale`'s `procedure_row_id` parameter
    expects) and `name` (for a human-readable report). Returns `[]`, not
    an error, when no procedure references the claim -- this is the
    common case for most claims.

    NOTE on the parameter itself: `app/db/session.py` registers a jsonb
    type codec on every pooled connection (`encoder=json.dumps`) so that
    JSONB columns come back as real Python objects, not raw strings. That
    codec also applies to OUTGOING `::jsonb` parameters -- passing an
    already-`json.dumps`-ed string here would get double-encoded (the
    codec would `json.dumps` the string itself, producing a JSON string
    literal instead of a JSON array) and silently match nothing. The
    probe below is therefore a plain Python list/dict, not a pre-serialized
    string -- confirmed live this session: the pre-serialized form passed
    real but returned zero rows for a row proven (by direct SELECT) to
    exist and match.
    """
    probe = [{"claim_id": claim_id}]
    rows = await pool.fetch(
        "SELECT id, name FROM procedures "
        "WHERE t_invalid IS NULL AND preconditions @> $1::jsonb",
        probe,
    )
    return [{"id": str(row["id"]), "name": row["name"]} for row in rows]


async def propagate_claim_change(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    reason: str,
    detected_by: str = "claim_impact",
) -> list[str]:
    """
    The real trigger this module exists to provide: given a claim that
    just changed (superseded, contradicted, or newly related against by
    some other claim), find every LIVE procedure whose precondition names
    that claim (`find_procedures_referencing_claim` above) and mark each
    one stale via the real, existing, unmodified
    `procedures.py::mark_procedure_stale` -- this function does not touch
    the `staleness` column itself, does not duplicate that function's
    idempotency (`mark_procedure_stale` is already a no-op if a procedure
    is already 'stale'/'revalidating') or its ChangeSet recording.

    `reason` and `detected_by` are passed straight through to
    `mark_procedure_stale`'s own parameters of the same name (its real,
    current signature: `mark_procedure_stale(pool, *, procedure_row_id,
    reason, detected_by)`) so the resulting ChangeSet audit trail
    correctly names the claim-change event that caused each staleness
    transition, not a generic message.

    Returns the list of procedure row ids actually processed (passed to
    `mark_procedure_stale`) -- includes ids for procedures that were
    already stale (a real no-op per `mark_procedure_stale`'s own
    contract), since this function's job is "which procedures reference
    this claim", not "which procedures newly transitioned". Returns `[]`
    with no error if zero procedures reference the claim -- the common
    case for most claims, not a failure.
    """
    affected = await find_procedures_referencing_claim(pool, claim_id)
    processed_ids: list[str] = []
    for procedure in affected:
        await mark_procedure_stale(
            pool,
            procedure_row_id=procedure["id"],
            reason=reason,
            detected_by=detected_by,
        )
        processed_ids.append(procedure["id"])
    return processed_ids
