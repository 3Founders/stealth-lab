"""
Read-only Claim Graph API domain layer (directive §40).

Pure composition over already-real, already-tested services -- no new
schema, no migration, nothing here reimplements a query another module
already owns:

  - `app.services.claims.get_claim_relations` -- single-hop relation read.
  - `app.services.claim_traversal.research` -- bounded 2-hop traversal.
  - `app.services.claim_evidence.get_claim_evidence` -- per-claim evidence.
  - `app.services.claim_impact.find_procedures_referencing_claim` -- claim
    -> dependent-procedure lookup.
  - `app.services.claims.get_claim_version_chain` +
    `app.services.claim_temporal.get_claim_commit_history` -- version/
    commit history.

The one genuinely new primitive is `get_claim`, below: no existing reader
fetches a single claim row by id (`claims.py`'s own callers all resolve a
claim indirectly -- by family, by subject, by task). It lives here rather
than in `claims.py` itself because it is API-layer composition (the read
this router needs), not core substrate `claims.py` owns.

SCOPE DISCIPLINE (CLAUDE.md hard rule: `scope_predicates()` is the only
legal source of tenant/visibility SQL): every function below either
builds its own predicate directly against `knowledge_nodes`/`evidence`/
`procedures` (mirroring `get_claim`'s own shape), or -- when composing a
service function that returns rows NOT already scope-filtered (relation
edges, traversal hops, evidence rows, dependent procedures) -- resolves
the row ids the composed call returned and re-checks them against
`scope_predicates()` before returning, via `_visible_ids` below. This is
the same anti-enumeration posture `app/api/graph.py` already established:
a row the caller's scope cannot see is OMITTED from the response, never
labelled or 404'd per-row (that would leak existence).

`TenantScope.unrestricted()` is used throughout, per `graph.py`'s own
precedent -- today's honest permissive-in-effect posture; the seam this
module offers a future resolved tenant scope is the same as `graph.py`'s.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import asyncpg

from app.services import claim_evidence as claim_evidence_service
from app.services import claim_impact
from app.services import claim_temporal
from app.services import claim_traversal
from app.services import claims as claims_service
from app.services.access import AccessScope, TenantScope, scope_predicates


async def get_claim(
    pool: asyncpg.Pool, claim_id: str, *, scope: AccessScope,
) -> Optional[dict]:
    """
    The one missing primitive: a single live claim row by id, scope-
    filtered. `None` when `claim_id` does not resolve to a live
    `node_type='claim'` row, OR when it does but the caller's scope
    cannot see it -- deliberately the same `None` for both, per this
    module's anti-enumeration posture (a caller must not be able to
    distinguish "does not exist" from "exists, not yours" from the return
    value alone).
    """
    tenant = TenantScope.unrestricted()
    scope_sql, scope_params, _ = scope_predicates(scope, tenant, param_index=2)
    row = await pool.fetchrow(
        f"SELECT * FROM knowledge_nodes WHERE id = $1::uuid "
        f"AND node_type = 'claim' AND t_invalid IS NULL AND {scope_sql}",
        claim_id, *scope_params,
    )
    return dict(row) if row else None


async def _visible_ids(
    pool: asyncpg.Pool, table: str, ids: Iterable[str], *, scope: AccessScope,
) -> set[str]:
    """
    Given a set of row ids from some OTHER table (`knowledge_nodes`,
    `evidence`, `procedures`), return the subset visible under `scope` --
    the same per-row visibility re-check `app/api/graph.py::get_subgraph`
    already does for its own hydrate step, generalized to any of the
    three tables this module touches. `table` is never caller-supplied
    (always one of the three literal names below), so this is not a SQL
    injection surface.
    """
    id_list = list(ids)
    if not id_list:
        return set()
    tenant = TenantScope.unrestricted()
    scope_sql, scope_params, _ = scope_predicates(scope, tenant, param_index=2)
    rows = await pool.fetch(
        f"SELECT id FROM {table} WHERE id = ANY($1::uuid[]) AND {scope_sql}",
        id_list, *scope_params,
    )
    return {str(r["id"]) for r in rows}


def _other_claim_id(row: dict, claim_id: str) -> str:
    source = str(row["source_id"])
    target = str(row["target_id"])
    return target if source == str(claim_id) else source


async def get_claim_neighbors(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    relations: Optional[set[str]] = None,
    direction: str = "both",
    scope: AccessScope,
) -> list[dict]:
    """
    Single-hop relation neighbors of one claim -- composes
    `claims.get_claim_relations` verbatim (direction/relations validation
    is entirely its job, not re-checked here), then drops any edge whose
    OTHER endpoint is a claim the caller's scope cannot see. `[]` when
    `claim_id` itself isn't visible (or doesn't exist) -- no edges
    against an invisible claim are ever surfaced.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return []
    rows = await claims_service.get_claim_relations(
        pool, claim_id, direction=direction, relations=relations,
    )
    neighbor_ids = {_other_claim_id(r, claim_id) for r in rows}
    visible = await _visible_ids(pool, "knowledge_nodes", neighbor_ids, scope=scope)
    return [dict(r) for r in rows if _other_claim_id(r, claim_id) in visible]


async def traverse_claim_graph(
    pool: asyncpg.Pool, claim_id: str, *, max_hops: int = 2, scope: AccessScope,
) -> dict:
    """
    Bounded multi-hop traversal -- composes `claim_traversal.research`
    verbatim (the 2-hop cap, the dedup-by-claim-id frontier, the
    supporting/contradicting/other bucketing are entirely its job, not
    re-derived here). Every hop whose `from_claim_id`/`to_claim_id` is a
    claim the caller's scope cannot see is dropped from the response.

    Returns an honest empty-bucket dict (never raises) when `claim_id`
    itself is not visible -- same "no existence leak" posture as
    `get_claim_neighbors`.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return {
            "claim_id": str(claim_id),
            "supporting": [], "contradicting": [], "other": [],
        }

    result = await claim_traversal.research(pool, claim_id, max_hops=max_hops)
    all_hops = result.supporting + result.contradicting + result.other
    touched_ids: set[str] = set()
    for hop in all_hops:
        touched_ids.add(hop.from_claim_id)
        touched_ids.add(hop.to_claim_id)
    visible = await _visible_ids(pool, "knowledge_nodes", touched_ids, scope=scope)

    def _dump(hops: list) -> list[dict]:
        return [
            {
                "edge_id": h.edge_id,
                "relation": h.relation,
                "hop": h.hop,
                "from_claim_id": h.from_claim_id,
                "to_claim_id": h.to_claim_id,
                "properties": h.properties,
            }
            for h in hops
            if h.from_claim_id in visible and h.to_claim_id in visible
        ]

    return {
        "claim_id": result.claim_id,
        "supporting": _dump(result.supporting),
        "contradicting": _dump(result.contradicting),
        "other": _dump(result.other),
    }


async def get_claim_evidence_api(
    pool: asyncpg.Pool, claim_id: str, *, scope: AccessScope,
) -> list[dict]:
    """
    Live evidence rows for one claim -- composes
    `claim_evidence.get_claim_evidence` verbatim (the `target_type =
    'claim'`/`t_invalid IS NULL`/oldest-first query is entirely its job),
    then drops any evidence row the caller's scope cannot see (`evidence`
    itself carries `visibility`/`owner_id`/`tenant_id`, per
    `db/24_evidence.sql`). `[]` when `claim_id` isn't visible.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return []
    rows = await claim_evidence_service.get_claim_evidence(pool, claim_id)
    ids = {str(r["id"]) for r in rows}
    visible = await _visible_ids(pool, "evidence", ids, scope=scope)
    return [dict(r) for r in rows if str(r["id"]) in visible]


async def get_claim_dependents(
    pool: asyncpg.Pool, claim_id: str, *, scope: AccessScope,
) -> list[dict]:
    """
    Procedures whose `preconditions` reference this claim -- composes
    `claim_impact.find_procedures_referencing_claim` verbatim (the
    `preconditions @> [{"claim_id": ...}]` containment query is entirely
    its job), already hydrated with `id`/`name` by that function itself,
    then drops any procedure the caller's scope cannot see. `[]` when
    `claim_id` isn't visible.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return []
    procedures = await claim_impact.find_procedures_referencing_claim(pool, claim_id)
    ids = {p["id"] for p in procedures}
    visible = await _visible_ids(pool, "procedures", ids, scope=scope)
    return [p for p in procedures if p["id"] in visible]


async def get_claim_history(
    pool: asyncpg.Pool, claim_id: str, *, scope: AccessScope,
) -> list[dict]:
    """
    One claim's version chain, oldest to newest, each version carrying
    its own real commit history -- composes `claims.get_claim_version_
    chain` (the family-walk) and `claim_temporal.get_claim_commit_
    history` (the commit join) verbatim, neither re-walked or re-joined
    here; this function only merges their outputs (both already walk the
    identical chain in the identical order, so a merge by claim id is
    exact, not a fuzzy join) and drops any version the caller's scope
    cannot see. `[]` when `claim_id` isn't visible or has no chain.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return []
    chain = await claims_service.get_claim_version_chain(pool, claim_id)
    if not chain:
        return []
    commit_history = await claim_temporal.get_claim_commit_history(pool, claim_id)
    by_id: dict[str, dict[str, Any]] = {h["claim_id"]: h for h in commit_history}

    ids = [str(row["id"]) for row in chain]
    visible = await _visible_ids(pool, "knowledge_nodes", ids, scope=scope)

    history: list[dict] = []
    for row in chain:
        claim_version_id = str(row["id"])
        if claim_version_id not in visible:
            continue
        props = dict(row["properties"])
        commit_entry = by_id.get(claim_version_id)
        history.append({
            "claim_id": claim_version_id,
            "version": (commit_entry or {}).get("version", props.get("claim_version", 1)),
            "statement": (commit_entry or {}).get("statement", props.get("statement")),
            "t_valid": (commit_entry or {}).get("t_valid"),
            "commits": (commit_entry or {}).get("commits", []),
            "properties": props,
        })
    return history
