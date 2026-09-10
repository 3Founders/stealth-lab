"""
Trigger (not mechanism) for "a claim just changed -- does any live
procedure that depends on it need to know?".

Procedures already have a real staleness mechanism
(`app/services/procedures.py::mark_procedure_stale`) -- this module does
NOT reimplement it. It finds the affected procedures and calls that
primitive for each one.

ROLE-AWARE INVALIDATION (V4-hardening "CLAIM INVALIDATION", B5)
    The primary index for "which procedures depend on this claim" is now
    the typed `procedure_claim_refs` table
    (`app/services/procedure_claim_refs.py`), which records WHY each
    procedure references the claim:

      - STRONG roles  (PRECONDITION / APPLICABILITY / ASSUMPTION) --
        a claim change disqualifies the procedure until it is
        re-verified: it is marked stale.
      - EXPLANATORY roles (RATIONALE / DECISION / EXPECTED_EFFECT /
        FAILURE_MODE / VERIFICATION) -- recorded for provenance, NEVER
        auto-invalidated. `propagate_claim_change` returns them so a
        caller can log/annotate, and does nothing else to them.

COMPATIBILITY
    Corpora that predate migration 66 carry the dependency only as
    `procedures.preconditions[*].claim_id`. The old JSONB containment
    scan is kept as a fallback and its hits are treated as
    `role='PRECONDITION'` (strong) -- so invalidation never silently
    regresses on un-backfilled data. The two sources are merged and
    de-duplicated by the procedure *version row id* (`procedures.id`),
    the one key both sources expose (the frozen compat query cannot
    surface the stable `procedure_id`/`version` pair without changing its
    text, which several offline fakes match on).

SHAPES
    - `find_procedures_referencing_claim` / `find_procedures_referencing_
      claim_flat` -- the backward-compatible FLAT list of every
      referencing procedure (strong AND explanatory), one `{id, name,
      procedure_id, procedure_version, role}` dict each. Existing callers
      (`claim_graph_api.get_claim_dependents`) read only `id`/`name`; the
      extra keys are additive.
    - `find_procedures_referencing_claim_grouped` -- the role-aware
      `{"strong": [...], "explanatory": [...]}` shape B5 introduces, used
      by `propagate_claim_change` to invalidate ONLY strong refs.

      The flat function is kept as the public name (rather than renaming
      it and repointing an out-of-lane caller) so this change stays
      strictly additive; the grouped view is a sibling, not a
      replacement.
"""
from __future__ import annotations

import logging

import asyncpg

from app.services.procedure_claim_refs import STRONG_ROLES, list_procedures_for_claim
from app.services.procedures import mark_procedure_stale

logger = logging.getLogger(__name__)

# The compatibility precondition scan, verbatim. DO NOT change this string
# without checking the offline fakes that match on it
# (test_claims.py, test_tms_readability_offline.py,
# test_claim_graph_api_offline.py).
_COMPAT_PRECONDITION_SQL = (
    "SELECT id, name FROM procedures "
    "WHERE t_invalid IS NULL AND preconditions @> $1::jsonb"
)


async def _compat_precondition_hits(pool: asyncpg.Pool, claim_id: str) -> list[dict]:
    """The pre-migration-66 fallback: any live procedure whose
    `preconditions` JSONB array contains an element with this
    `claim_id`. Each hit is a strong PRECONDITION-role dependency."""
    probe = [{"claim_id": claim_id}]
    rows = await pool.fetch(_COMPAT_PRECONDITION_SQL, probe)
    return [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "procedure_id": str(row["id"]),
            "procedure_version": None,
            "role": "PRECONDITION",
        }
        for row in rows
    ]


async def find_procedures_referencing_claim_grouped(
    pool: asyncpg.Pool, claim_id: str,
) -> dict:
    """
    Every LIVE procedure that references `claim_id`, grouped by the
    STRENGTH of the reference:

        {
          "strong":      [{procedure_id, procedure_version, name, role, id}, ...],
          "explanatory": [{procedure_id, procedure_version, name, role, id}, ...],
        }

    PRIMARY source is the typed `procedure_claim_refs` table (via
    `procedure_claim_refs.list_procedures_for_claim`). The
    `procedures.preconditions[*].claim_id` JSONB scan is UNIONed in as a
    COMPATIBILITY fallback for un-backfilled corpora, its hits classified
    as strong `PRECONDITION` refs.

    De-duplication is by the procedure version row id (`procedures.id` --
    present on both sources). A procedure that has ANY strong ref lands in
    `strong` and is kept out of `explanatory`, even if it also has
    explanatory refs to the same claim.
    """
    try:
        typed = await list_procedures_for_claim(pool, claim_id)
    except (asyncpg.PostgresError, AssertionError, NotImplementedError) as exc:
        # `procedure_claim_refs` is unavailable: migration 66 is not
        # applied on this database, OR an offline test double that
        # predates the typed relation rejects the query. The
        # compatibility precondition scan below still covers every
        # procedure that names the claim in its `preconditions` JSONB, so
        # invalidation degrades to the pre-51 behaviour rather than
        # failing. Transitional -- remove once every offline fake models
        # `procedure_claim_refs`.
        logger.debug("procedure_claim_refs lookup unavailable (%s); compat scan only", exc)
        typed = []

    compat = await _compat_precondition_hits(pool, claim_id)

    strong_by_id: dict[str, dict] = {}
    explanatory_by_id: dict[str, dict] = {}
    for ref in [*typed, *compat]:
        row_id = str(ref["id"])
        entry = {
            "procedure_id": ref.get("procedure_id", row_id),
            "procedure_version": ref.get("procedure_version"),
            "name": ref.get("name"),
            "role": ref["role"],
            "id": row_id,
        }
        if ref["role"] in STRONG_ROLES:
            strong_by_id.setdefault(row_id, entry)
        else:
            explanatory_by_id.setdefault(row_id, entry)

    # A procedure that is strong anywhere must not also read as
    # explanatory-only.
    for row_id in list(explanatory_by_id):
        if row_id in strong_by_id:
            del explanatory_by_id[row_id]

    return {
        "strong": list(strong_by_id.values()),
        "explanatory": list(explanatory_by_id.values()),
    }


async def find_procedures_referencing_claim(
    pool: asyncpg.Pool, claim_id: str,
) -> list[dict]:
    """
    Backward-compatible FLAT shape: every LIVE procedure that references
    `claim_id` (strong AND explanatory roles), one `{id, name,
    procedure_id, procedure_version, role}` dict each.

    Same PRIMARY (`procedure_claim_refs`) + COMPATIBILITY (`preconditions`
    JSON scan) union as `find_procedures_referencing_claim_grouped`; this
    is just that result flattened. Existing callers read only `id`/`name`
    -- the other keys are additive. Role-aware invalidation uses the
    grouped function instead.
    """
    grouped = await find_procedures_referencing_claim_grouped(pool, claim_id)
    return [*grouped["strong"], *grouped["explanatory"]]


async def find_procedures_referencing_claim_flat(
    pool: asyncpg.Pool, claim_id: str,
) -> list[dict]:
    """Explicit name for the flat shape; identical to
    `find_procedures_referencing_claim`."""
    return await find_procedures_referencing_claim(pool, claim_id)


async def propagate_claim_change(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    reason: str,
    detected_by: str = "claim_impact",
) -> dict:
    """
    Given a claim that just changed (superseded / contradicted / related
    against), mark stale ONLY the procedures whose reference is STRONG
    (precondition / applicability / assumption). Procedures whose
    reference is merely EXPLANATORY are left completely untouched and
    returned so a caller can log or annotate them.

    `reason` / `detected_by` pass straight through to
    `mark_procedure_stale` so the ChangeSet audit trail names the real
    claim-change event.

    Returns:

        {
          "marked_stale":          [procedure_row_id, ...],   # strong refs
          "explanatory_untouched": [{procedure_id, procedure_version,
                                     name, role, id}, ...],
        }

    `marked_stale` includes procedures that were already stale (a real
    no-op per `mark_procedure_stale`'s own contract) -- this function's
    job is "which strong-referencing procedures", not "which newly
    transitioned". Both lists empty (no error) when nothing references the
    claim -- the common case.
    """
    grouped = await find_procedures_referencing_claim_grouped(pool, claim_id)

    marked_stale: list[str] = []
    for procedure in grouped["strong"]:
        await mark_procedure_stale(
            pool,
            procedure_row_id=procedure["id"],
            reason=reason,
            detected_by=detected_by,
        )
        marked_stale.append(procedure["id"])

    explanatory = grouped["explanatory"]
    if explanatory:
        logger.info(
            "claim %s changed: %d explanatory-role procedure ref(s) left untouched",
            claim_id, len(explanatory),
        )

    return {"marked_stale": marked_stale, "explanatory_untouched": list(explanatory)}
