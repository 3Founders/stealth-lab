"""Remove everything ingested under a license, as a class (BLOCKERS I7: CC-BY-4.0 is accepted, with attribution,
and must stay removable).

    python -m app.ingestion.admin license-takedown --spdx CC-BY-4.0             # plan only (the default)
    python -m app.ingestion.admin license-takedown --spdx CC-BY-4.0 --apply     # tombstone it

HOW IT FINDS THE CONTENT
    Every ingestion opens an IngestionContext (migration 65) and stamps it on the artifacts, Procedures, evidence
    and knowledge nodes it derives. Migration 126 records the license on that context when the license requires
    attribution, so "everything ingested under CC-BY-4.0" is: the contexts with that license, then every derived row
    on every shard that carries one of those context ids.

WHAT "REMOVE" MEANS
    Nothing is hard-deleted (append-only substrate; the context row itself stays queryable forever). Each derived
    row is TOMBSTONED with the same pattern the rest of the system uses:
      procedures       availability='disabled', embedding=NULL, t_invalid=now()   ('deleted' is not a value of
                       the procedure_availability enum, so the availability field uses 'disabled')
      knowledge_nodes  embedding=NULL, t_invalid=now()                            (Claims are knowledge nodes)
      evidence         t_invalid=now()                                            (the append-only trigger allows
                       exactly this retraction tombstone)
      ingested_artifacts  license_metadata gains {"license_takedown": true}       (bytes are not deleted here: most
                       sources are stored as URL references; raw blobs, if any, live in object storage)
    Then the search-index refresh is queued for every affected Procedure and Claim, so they leave retrieval.

SAFE TO RE-RUN
    Every UPDATE is guarded by `t_invalid IS NULL`, and the search refresh is queued for ALL matching objects, not
    only the ones tombstoned this time, so a run that died half-way is completed by running it again.

NOT COVERED (stated, not implied)
    - Goals: a Goal is a task name, not the licensed text; it stays even if its only Procedure was removed.
    - Content already copied out of Kel by a client (a user's own `.stealth/` files, an agent's session).
    - Contexts opened before migration 126, or by a code path that did not pass the license: they cannot be found
      by license. Only attribution-required licenses are tagged (`repo_license_policy.ATTRIBUTION_REQUIRED`).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

_CONTEXT_IDS = "SELECT id::text FROM ingestion_contexts WHERE upper(license_spdx) = upper($1)"

_COUNT_SQL = {
    "procedures": "SELECT count(*) FROM procedures WHERE ingestion_context_id = ANY($1::uuid[]) AND t_invalid IS NULL",
    "claims": ("SELECT count(*) FROM knowledge_nodes WHERE ingestion_context_id = ANY($1::uuid[]) "
               "AND node_type = 'claim' AND t_invalid IS NULL"),
    "evidence": "SELECT count(*) FROM evidence WHERE ingestion_context_id = ANY($1::uuid[]) AND t_invalid IS NULL",
    "artifacts": "SELECT count(*) FROM ingested_artifacts WHERE ingestion_context_id = ANY($1::uuid[])",
}

_TOMBSTONE_SQL = {
    "procedures": ("UPDATE procedures SET availability = 'disabled', embedding = NULL, t_invalid = now() "
                   "WHERE ingestion_context_id = ANY($1::uuid[]) AND t_invalid IS NULL"),
    "claims": ("UPDATE knowledge_nodes SET embedding = NULL, t_invalid = now() "
               "WHERE ingestion_context_id = ANY($1::uuid[]) AND t_invalid IS NULL"),
    "evidence": "UPDATE evidence SET t_invalid = now() WHERE ingestion_context_id = ANY($1::uuid[]) AND t_invalid IS NULL",
    "artifacts": ("UPDATE ingested_artifacts SET license_metadata = license_metadata || '{\"license_takedown\": true}'::jsonb "
                  "WHERE ingestion_context_id = ANY($1::uuid[]) AND NOT (license_metadata ? 'license_takedown')"),
}

# every object the search index may hold for this content: ALL of them, tombstoned now or earlier
_PROJECTION_IDS = {
    "procedure": "SELECT DISTINCT procedure_id::text FROM procedures WHERE ingestion_context_id = ANY($1::uuid[])",
    "claim": ("SELECT id::text FROM knowledge_nodes WHERE ingestion_context_id = ANY($1::uuid[]) "
              "AND node_type = 'claim'"),
}


async def _shard_pools(pool: Any) -> list[tuple[str, Any]]:
    from app.services import shards

    return await shards.all_pools(pool, strict=True)      # a missed shard would leave content behind: fail loudly


async def context_ids(pools: list[tuple[str, Any]], spdx: str) -> list[str]:
    seen: set[str] = set()
    for _shard, p in pools:
        seen.update(r[0] for r in await p.fetch(_CONTEXT_IDS, spdx))
    return sorted(seen)


async def plan(pool: Any, spdx: str) -> dict:
    """What a takedown would remove. Writes nothing."""
    pools = await _shard_pools(pool)
    ctx = await context_ids(pools, spdx)
    per_shard: dict[str, dict[str, int]] = {}
    totals = {k: 0 for k in _COUNT_SQL}
    if ctx:
        for shard, p in pools:
            counts = {k: int(await p.fetchval(sql, ctx) or 0) for k, sql in _COUNT_SQL.items()}
            per_shard[shard] = counts
            for k, n in counts.items():
                totals[k] += n
    return {"spdx": spdx, "contexts": len(ctx), "per_shard": per_shard, "totals": totals, "applied": False}


async def apply(pool: Any, spdx: str, *, actor: str = "admin-cli") -> dict:
    """Tombstone everything ingested under `spdx`, queue the search refresh, record an audit event."""
    from app.services import search_projection
    from app.services.audit import record_audit_event

    pools = await _shard_pools(pool)
    ctx = await context_ids(pools, spdx)
    result = await plan(pool, spdx)
    result["applied"] = True
    if not ctx:
        result["projection_refreshes_queued"] = 0
        return result

    for _shard, p in pools:
        for kind in ("procedures", "claims", "evidence", "artifacts"):
            await p.execute(_TOMBSTONE_SQL[kind], ctx)

    queued = 0
    for _shard, p in pools:
        for object_type, sql in _PROJECTION_IDS.items():
            for (oid,) in await p.fetch(sql, ctx):
                await search_projection.enqueue(pool, object_type, oid)     # the outbox lives on the control DB
                queued += 1
    result["projection_refreshes_queued"] = queued

    await record_audit_event(
        pool, actor_subject=actor, action="license_takedown", object_type="license", object_id=spdx,
        details={"contexts": len(ctx), "totals": result["totals"], "projection_refreshes_queued": queued},
    )
    return result
