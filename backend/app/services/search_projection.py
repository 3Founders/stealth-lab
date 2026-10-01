"""
Global search projections (goal/claim/procedure) + durable outbox.

The three ``*_search_index`` tables are PROJECTIONS of canonical rows, never a
second source of truth: every row is derivable from canonical state by
``project_object`` and the whole set is rebuildable (``reindex``) and
checkable (``verify_projection``).

Consistency model (docs/sharding.md, "Projection consistency"):
  * a canonical write in the control database fires trigger
    ``sl_canonical_touch`` (migration 95) which records the route and inserts
    ONE coalesced ``projection_outbox`` row in the same transaction;
  * canonical rows on a remote shard: the shard-side writer calls ``enqueue``
    on the control database after commit (crash-safe: ``reindex shard`` repairs);
  * ``drain_outbox`` applies entries idempotently: it always projects the
    CURRENT canonical state under a per-object advisory lock, so replays,
    duplicate drainers and out-of-order entries converge to the same result;
  * a crash between canonical write and projection leaves a pending entry
    (visible as projection lag), never a silent gap.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import asyncpg

from app.services.embeddings import to_pgvector
from app.services.shards import HOME_SHARD, OBJECT_TYPES, ShardPools, lookup_routes, search_pool

log = logging.getLogger(__name__)

MAX_OUTBOX_ATTEMPTS = 5
_TEXT_CAP = 8000
EMBEDDING_DIM = 1024


def _cap(text: Optional[str], n: int = _TEXT_CAP) -> str:
    return (text or "")[:n]


def _norm_version(provider: Optional[str]) -> str:
    return provider or "v1"


# ------------------------------------------------------------- canonical reads
# Each returns a dict of canonical fields for ONE object from the given
# pool/connection, or None if there is no live canonical row.

_GOAL_SQL = (
    "SELECT id, canonical_name, description, aliases, status, version, visibility, owner_id, "
    "scope_type, scope_entity_id, home_shard_id, embedding::text AS embedding, embedding_model_id, "
    "embedding_provider, resolved_at, t_created FROM goals WHERE id = $1::uuid AND t_invalid IS NULL"
)
_PROC_SQL = (
    "SELECT id, procedure_id, name, goal, achieves_goal_id, display_description, capability_statement, "
    "retrieval_document, preconditions, postconditions, verification_state::text AS verification_state, "
    "verification_stats, availability::text AS availability, version, visibility, owner_id, scope_type, "
    "scope_entity_id, tenant_id, home_shard_id, embedding::text AS embedding, embedding_model_id, "
    "embedding_provider FROM procedures WHERE procedure_id = $1::uuid AND t_invalid IS NULL "
    "AND is_engineering_fixture = false "
    "ORDER BY version DESC LIMIT 1"
)
# A trajectory's verification actions ("run the failing test", "check git diff") are stored as claims only so a
# Procedure can say how to check it (procedure_claim_refs VERIFICATION); they are steps, not knowledge, so claim
# search never offers them on their own (pilot 2026-09-30: 23 of 114 claims were such actions).
_SEARCHABLE_CLAIM = "COALESCE(properties->>'claim_type', '') <> 'verification'"
_CLAIM_SQL = (
    "SELECT id, name, subject, predicate, object, properties, claim_status, visibility, owner_id, "
    "scope_type, scope_entity_id, tenant_id, embedding::text AS embedding, embedding_model_id "
    "FROM knowledge_nodes WHERE id = $1::uuid AND node_type = 'claim' AND t_invalid IS NULL "
    f"AND {_SEARCHABLE_CLAIM}"
)
_READ_SQL = {"goal": _GOAL_SQL, "procedure": _PROC_SQL, "claim": _CLAIM_SQL}


def _preview(items: Any, limit: int = 600) -> str:
    import json
    if not items:
        return ""
    try:
        if isinstance(items, str):
            items = json.loads(items)
        parts = [x if isinstance(x, str) else (x.get("description") or x.get("text") or x.get("name") or json.dumps(x, default=str)) for x in items]
    except Exception:  # noqa: BLE001 -- projection text is best-effort
        return ""
    return _cap("; ".join(str(p) for p in parts), limit)


def build_projection(object_type: str, row: dict, shard_id: str) -> dict:
    """Pure: canonical row dict -> projection column dict."""
    emb = row.get("embedding")
    model = row.get("embedding_model_id")
    common = dict(
        embedding=emb,
        embedding_model=(model or "unknown") if emb else model,
        embedding_version=_norm_version(row.get("embedding_provider")) if emb else None,
        embedding_dim=EMBEDDING_DIM if emb else None,
        home_shard_id=shard_id,
        visibility=row["visibility"],
        owner_id=row.get("owner_id"),
        scope_type=row.get("scope_type"),
        scope_entity_id=row.get("scope_entity_id"),
    )
    if object_type == "goal":
        text = " ".join(x for x in [row["canonical_name"], " ".join(row.get("aliases") or []), row.get("description") or ""] if x)
        return dict(
            key=str(row["id"]), canonical_name=row["canonical_name"],
            short_description=_cap(row.get("description"), 500) or None,
            aliases=list(row.get("aliases") or []), search_text=_cap(text),
            status=row["status"], version=row["version"],
            # derived copies: goals.resolved_at stays the authoritative resolution signal
            resolved_at=row.get("resolved_at"), t_created=row.get("t_created"), **common,
        )
    if object_type == "procedure":
        text = " ".join(x for x in [
            row["name"], row["goal"], row.get("display_description") or "",
            row.get("capability_statement") or "", row.get("retrieval_document") or "",
        ] if x)
        stats = row.get("verification_stats") or {}
        if isinstance(stats, str):
            import json
            stats = json.loads(stats)
        verification = f"{row.get('verification_state')}: {stats.get('successes', 0)}/{stats.get('attempts', 0)} successes"
        return dict(
            key=str(row["procedure_id"]), procedure_row_id=str(row["id"]), name=row["name"],
            summary=_cap(row.get("display_description") or row["goal"], 500),
            goal_id=str(row["achieves_goal_id"]) if row.get("achieves_goal_id") else None,
            preconditions_summary=_preview(row.get("preconditions")) or None,
            outcome_summary=_preview(row.get("postconditions")) or None,
            verification_summary=verification, search_text=_cap(text),
            status=row["availability"], version=row["version"], tenant_id=row.get("tenant_id"), **common,
        )
    if object_type == "claim":
        statement = row["name"]
        spo = " ".join(x for x in [row.get("subject"), row.get("predicate"), row.get("object")] if x)
        text = " ".join(x for x in [statement, spo] if x)
        props = row.get("properties") or {}
        if isinstance(props, str):
            import json
            props = json.loads(props)
        return dict(
            key=str(row["id"]), statement=_cap(statement, 2000),
            primary_goal_id=props.get("goal_id") if isinstance(props, dict) else None,
            scope_summary=(props.get("scope") if isinstance(props, dict) and isinstance(props.get("scope"), str) else None),
            search_text=_cap(text), status=row.get("claim_status") or "candidate", version=1,
            tenant_id=row.get("tenant_id"), **common,
        )
    raise ValueError(f"unknown object_type {object_type!r}")


# ---------------------------------------------------------------- upserts

_UPSERT_GOAL = """
INSERT INTO goal_search_index (goal_id, canonical_name, short_description, aliases, search_text, search_tsv,
    embedding, embedding_model, embedding_version, embedding_dim, home_shard_id, status, version,
    visibility, owner_id, scope_type, scope_entity_id, resolved_at, t_created, has_procedures, updated_at)
VALUES ($1::uuid, $2, $3, $4, $5, to_tsvector('english', $5), $6::vector, $7, $8, $9, $10, $11, $12,
    $13::visibility_level, $14, $15, $16, $17, $18, $19, now())
ON CONFLICT (goal_id) DO UPDATE SET
    canonical_name = EXCLUDED.canonical_name, short_description = EXCLUDED.short_description,
    aliases = EXCLUDED.aliases, search_text = EXCLUDED.search_text, search_tsv = EXCLUDED.search_tsv,
    embedding = EXCLUDED.embedding, embedding_model = EXCLUDED.embedding_model,
    embedding_version = EXCLUDED.embedding_version, embedding_dim = EXCLUDED.embedding_dim,
    home_shard_id = EXCLUDED.home_shard_id, status = EXCLUDED.status, version = EXCLUDED.version,
    visibility = EXCLUDED.visibility, owner_id = EXCLUDED.owner_id, scope_type = EXCLUDED.scope_type,
    scope_entity_id = EXCLUDED.scope_entity_id, resolved_at = EXCLUDED.resolved_at,
    t_created = EXCLUDED.t_created, has_procedures = EXCLUDED.has_procedures, updated_at = now(), projected_at = now()
"""
_UPSERT_PROC = """
INSERT INTO procedure_search_index (procedure_id, procedure_row_id, name, summary, goal_id,
    preconditions_summary, outcome_summary, verification_summary, search_text, search_tsv,
    embedding, embedding_model, embedding_version, embedding_dim, home_shard_id, status, version,
    visibility, owner_id, scope_type, scope_entity_id, tenant_id, updated_at)
VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6, $7, $8, $9, to_tsvector('english', $9),
    $10::vector, $11, $12, $13, $14, $15, $16, $17::visibility_level, $18, $19, $20, $21::uuid, now())
ON CONFLICT (procedure_id) DO UPDATE SET
    procedure_row_id = EXCLUDED.procedure_row_id, name = EXCLUDED.name, summary = EXCLUDED.summary,
    goal_id = EXCLUDED.goal_id, preconditions_summary = EXCLUDED.preconditions_summary,
    outcome_summary = EXCLUDED.outcome_summary, verification_summary = EXCLUDED.verification_summary,
    search_text = EXCLUDED.search_text, search_tsv = EXCLUDED.search_tsv, embedding = EXCLUDED.embedding,
    embedding_model = EXCLUDED.embedding_model, embedding_version = EXCLUDED.embedding_version,
    embedding_dim = EXCLUDED.embedding_dim, home_shard_id = EXCLUDED.home_shard_id, status = EXCLUDED.status,
    version = EXCLUDED.version, visibility = EXCLUDED.visibility, owner_id = EXCLUDED.owner_id,
    scope_type = EXCLUDED.scope_type, scope_entity_id = EXCLUDED.scope_entity_id, tenant_id = EXCLUDED.tenant_id,
    updated_at = now(), projected_at = now()
"""
_UPSERT_CLAIM = """
INSERT INTO claim_search_index (claim_id, statement, primary_goal_id, scope_summary, search_text, search_tsv,
    embedding, embedding_model, embedding_version, embedding_dim, home_shard_id, status, version,
    visibility, owner_id, scope_type, scope_entity_id, tenant_id, updated_at)
VALUES ($1::uuid, $2, $3::uuid, $4, $5, to_tsvector('english', $5), $6::vector, $7, $8, $9, $10, $11, $12,
    $13::visibility_level, $14, $15, $16, $17::uuid, now())
ON CONFLICT (claim_id) DO UPDATE SET
    statement = EXCLUDED.statement, primary_goal_id = EXCLUDED.primary_goal_id,
    scope_summary = EXCLUDED.scope_summary, search_text = EXCLUDED.search_text, search_tsv = EXCLUDED.search_tsv,
    embedding = EXCLUDED.embedding, embedding_model = EXCLUDED.embedding_model,
    embedding_version = EXCLUDED.embedding_version, embedding_dim = EXCLUDED.embedding_dim,
    home_shard_id = EXCLUDED.home_shard_id, status = EXCLUDED.status, version = EXCLUDED.version,
    visibility = EXCLUDED.visibility, owner_id = EXCLUDED.owner_id, scope_type = EXCLUDED.scope_type,
    scope_entity_id = EXCLUDED.scope_entity_id, tenant_id = EXCLUDED.tenant_id, updated_at = now(), projected_at = now()
"""

# storage layout v2 (migration 132): with a search group, a Goal's searchable text and vectors live on a search
# member (goal_search_docs); the control database keeps the slim goal_search_index row that SQL joins and filters use.
_UPSERT_GOAL_DOC = """
INSERT INTO goal_search_docs (goal_id, canonical_name, short_description, search_text, search_tsv, embedding,
    embedding_model, embedding_version, embedding_dim, status, has_procedures, scope_type, scope_entity_id,
    visibility, owner_id, home_shard_id, resolved_at, t_created, updated_at)
VALUES ($1::uuid, $2, $3, $4, to_tsvector('english', $4), $5::vector, $6, $7, $8, $9, $10, $11, $12,
    $13::visibility_level, $14, $15, $16, $17, now())
ON CONFLICT (goal_id) DO UPDATE SET
    canonical_name = EXCLUDED.canonical_name, short_description = EXCLUDED.short_description,
    search_text = EXCLUDED.search_text, search_tsv = EXCLUDED.search_tsv, embedding = EXCLUDED.embedding,
    embedding_model = EXCLUDED.embedding_model, embedding_version = EXCLUDED.embedding_version,
    embedding_dim = EXCLUDED.embedding_dim, status = EXCLUDED.status, has_procedures = EXCLUDED.has_procedures,
    scope_type = EXCLUDED.scope_type, scope_entity_id = EXCLUDED.scope_entity_id, visibility = EXCLUDED.visibility,
    owner_id = EXCLUDED.owner_id, home_shard_id = EXCLUDED.home_shard_id, resolved_at = EXCLUDED.resolved_at,
    t_created = EXCLUDED.t_created, updated_at = now()
"""


async def _upsert_goal_doc(target: Any, p: dict) -> None:
    await target.execute(_UPSERT_GOAL_DOC, p["key"], p["canonical_name"], p["short_description"], p["search_text"],
                         p["embedding"], p["embedding_model"], p["embedding_version"], p["embedding_dim"], p["status"],
                         bool(p.get("has_procedures")), p["scope_type"], p["scope_entity_id"], p["visibility"],
                         p["owner_id"], p["home_shard_id"], p.get("resolved_at"), p.get("t_created"))


def _placement_key(object_type: str, object_id: str, projection: Optional[dict]) -> str:
    """Keep a Goal's search rows together on one member: a Goal by its id, a Procedure by its Goal, a Claim by its
    primary Goal (else its own id). Reads always ask every member, so this is locality only."""
    goal = (projection or {}).get("goal_id") or (projection or {}).get("primary_goal_id")
    if object_type == "goal":
        return f"goal:{object_id}"
    return f"goal:{goal}" if goal else f"{object_type}:{object_id}"


_INDEX_TABLE = {"goal": ("goal_search_index", "goal_id"), "procedure": ("procedure_search_index", "procedure_id"),
                "claim": ("claim_search_index", "claim_id")}


async def _upsert(conn, object_type: str, p: dict) -> None:
    common = (p["embedding"], p["embedding_model"], p["embedding_version"], p["embedding_dim"], p["home_shard_id"])
    tail = (p["visibility"], p["owner_id"], p["scope_type"], p["scope_entity_id"])
    if object_type == "goal":
        await conn.execute(_UPSERT_GOAL, p["key"], p["canonical_name"], p["short_description"], p["aliases"],
                           p["search_text"], *common, p["status"], p["version"], *tail,
                           p.get("resolved_at"), p.get("t_created"), bool(p.get("has_procedures")))
    elif object_type == "procedure":
        await conn.execute(_UPSERT_PROC, p["key"], p["procedure_row_id"], p["name"], p["summary"], p["goal_id"],
                           p["preconditions_summary"], p["outcome_summary"], p["verification_summary"],
                           p["search_text"], *common, p["status"], p["version"], *tail, p["tenant_id"])
    else:
        await conn.execute(_UPSERT_CLAIM, p["key"], p["statement"], p["primary_goal_id"], p["scope_summary"],
                           p["search_text"], *common, p["status"], p["version"], *tail, p["tenant_id"])


async def project_object(
    conn: asyncpg.Connection, object_type: str, object_id: str, *, pools: Optional[ShardPools] = None,
) -> str:
    """Project ONE object's current canonical state. Returns 'upserted' or
    'deleted'. Must be called inside a transaction on the control database:
    the advisory lock serialises concurrent projections of the same object."""
    if object_type not in OBJECT_TYPES:
        raise ValueError(f"unknown object_type {object_type!r}")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"proj:{object_type}:{object_id}")
    shard = (await lookup_routes(conn, object_type, [object_id])).get(object_id, HOME_SHARD)  # type: ignore[arg-type]
    if shard == HOME_SHARD:
        row = await conn.fetchrow(_READ_SQL[object_type], object_id)
    else:
        if pools is None:
            raise RuntimeError(f"{object_type} {object_id} lives on shard {shard}; ShardPools required")
        pool = await pools.get(shard)
        row = await pool.fetchrow(_READ_SQL[object_type], object_id)
    table, key = _INDEX_TABLE[object_type]
    # Goal projections live with the Goal hierarchy on the control database;
    # Procedure/Claim projections live on the search database (project B) when one
    # is configured -- with a search GROUP (migration 132), on the object's member, and a Goal's searchable
    # doc too. The outbox entry stays on the control database, in the caller's
    # transaction: a failed write here leaves it pending and the drainer retries it.
    from app.services import search_group

    grouped = await search_group.grouped(conn)
    projection = None if row is None else build_projection(object_type, dict(row), shard)
    gone = row is None or (object_type == "goal" and row["status"] == "merged")
    doc_target = None
    if object_type == "goal":
        target = conn
        if grouped:
            doc_target = await search_group.pool_for_object(
                conn, "goal", object_id, place=not gone, placement_key=_placement_key("goal", object_id, None),
                pools=pools)
    else:
        target = await search_group.pool_for_object(
            conn, object_type, object_id, place=not gone,
            placement_key=_placement_key(object_type, object_id, projection), pools=pools)
    # Procedure -> Goal: the Goal(s) whose `has_procedures` may change are the one the projection pointed at
    # before and the one it points at now.
    previous_goal = None
    if object_type == "procedure" and target is not None:
        previous_goal = await target.fetchval(
            "SELECT goal_id::text FROM procedure_search_index WHERE procedure_id = $1::uuid", object_id)
    if gone:
        if target is not None:
            await target.execute(f"DELETE FROM {table} WHERE {key} = $1::uuid", object_id)
        if doc_target is not None:
            await doc_target.execute("DELETE FROM goal_search_docs WHERE goal_id = $1::uuid", object_id)
        if object_type == "procedure" and previous_goal:
            await refresh_goal_has_procedures(conn, previous_goal, pools=pools)
        return "deleted"
    if object_type == "goal":
        projection["has_procedures"] = await goal_has_live_procedure(conn, object_id, pools=pools)
        if doc_target is not None:
            await _upsert_goal_doc(doc_target, projection)
            # the control database keeps the slim row: no vector, no search text (they live in the doc)
            await _upsert(target, object_type, {**projection, "embedding": None, "search_text": None})
            return "upserted"
    await _upsert(target, object_type, projection)
    if object_type == "procedure":
        for goal_id in {g for g in (previous_goal, projection.get("goal_id")) if g}:
            await refresh_goal_has_procedures(conn, str(goal_id), pools=pools)
    return "upserted"


# ------------------------------------------------------ Goal.has_procedures


async def goal_has_live_procedure(conn: Any, goal_id: str, *, pools: Any = None) -> bool:
    """True when the Goal has at least one 'active' Procedure projection (search database when configured; every
    member of a search group)."""
    from app.services import search_group

    # the single-database query unchanged, asked of every member (strict: never a flag from a partial view)
    parts = await search_group._each(
        conn, lambda p: p.fetchval(
            "SELECT EXISTS (SELECT 1 FROM procedure_search_index WHERE goal_id = $1::uuid AND status = 'active')",
            goal_id), strict=True, pools=pools)
    return any(bool(x) for x in parts)


async def refresh_goal_has_procedures(conn: Any, goal_id: str, *, pools: Any = None) -> Optional[bool]:
    """Recompute one Goal's `has_procedures` (migration 130). A flip bumps `updated_at`, which is what the worker's
    placement-repair sweep watches: a Goal enters the abstraction hierarchy when it first gets a Procedure.
    Returns the new value, or None when the Goal has no projection yet (its own projection computes it)."""
    has = await goal_has_live_procedure(conn, goal_id, pools=pools)
    changed = await conn.fetchval(
        "UPDATE goal_search_index SET has_procedures = $2, "
        "updated_at = CASE WHEN has_procedures IS DISTINCT FROM $2 THEN now() ELSE updated_at END "
        "WHERE goal_id = $1::uuid RETURNING has_procedures", goal_id, has)
    from app.services import search_group

    if changed is not None and await search_group.grouped(conn):
        doc = await search_group.pool_for_object(conn, "goal", goal_id, place=False, pools=pools)
        if doc is not None:
            await doc.execute("UPDATE goal_search_docs SET has_procedures = $2 WHERE goal_id = $1::uuid", goal_id, has)
    return changed


# ------------------------------------------------------------------ outbox


async def enqueue(conn: asyncpg.Pool | asyncpg.Connection, object_type: str, object_id: str) -> None:
    """Queue a projection refresh (coalesced with any pending one)."""
    await conn.execute(
        "INSERT INTO projection_outbox (object_type, object_id) VALUES ($1, $2::uuid) "
        "ON CONFLICT (object_type, object_id) WHERE status = 'pending' DO NOTHING",
        object_type, object_id,
    )


async def drain_outbox(
    pool: asyncpg.Pool, *, batch: int = 200, pools: Optional[ShardPools] = None, max_batches: int = 1_000_000,
) -> dict[str, int]:
    """Apply pending entries until none remain (or ``max_batches``). Safe to run
    concurrently from many workers (SKIP LOCKED) and to replay after a crash."""
    totals = {"applied": 0, "failed": 0, "retry": 0, "batches": 0}
    for _ in range(max_batches):
        async with pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    "SELECT id, object_type, object_id::text AS object_id, attempts FROM projection_outbox "
                    "WHERE status = 'pending' ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED", batch)
                if not rows:
                    break
                for r in rows:
                    try:
                        async with conn.transaction():  # savepoint: one bad object never poisons the batch
                            await project_object(conn, r["object_type"], r["object_id"], pools=pools)
                    except Exception as exc:  # noqa: BLE001 -- recorded on the row
                        attempts = r["attempts"] + 1
                        final = attempts >= MAX_OUTBOX_ATTEMPTS
                        await conn.execute(
                            "UPDATE projection_outbox SET attempts = $2, last_error = $3, status = $4 WHERE id = $1",
                            r["id"], attempts, repr(exc)[:500], "failed" if final else "pending")
                        totals["failed" if final else "retry"] += 1
                        log.warning("projection %s %s failed (attempt %d): %s", r["object_type"], r["object_id"], attempts, exc)
                    else:
                        await conn.execute(
                            "UPDATE projection_outbox SET status = 'applied', applied_at = now(), attempts = attempts + 1 "
                            "WHERE id = $1", r["id"])
                        totals["applied"] += 1
                totals["batches"] += 1
        if totals["retry"] and totals["applied"] == 0:
            break  # only retryable failures left this round; do not spin
    return totals


async def projection_lag(pool: asyncpg.Pool) -> dict[str, Any]:
    r = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE status = 'pending') AS pending, "
        "count(*) FILTER (WHERE status = 'failed') AS failed, "
        "COALESCE(EXTRACT(EPOCH FROM now() - min(created_at) FILTER (WHERE status = 'pending')), 0) AS oldest_pending_s "
        "FROM projection_outbox")
    return {"pending": r["pending"], "failed": r["failed"], "oldest_pending_s": float(r["oldest_pending_s"])}


async def retry_failed(pool: asyncpg.Pool) -> int:
    res = await pool.execute(
        "UPDATE projection_outbox o SET status = 'pending', attempts = 0 WHERE status = 'failed' "
        "AND NOT EXISTS (SELECT 1 FROM projection_outbox p WHERE p.status = 'pending' "
        "AND p.object_type = o.object_type AND p.object_id = o.object_id)")
    return int(res.split()[-1])


# ----------------------------------------------------------- rebuild / verify

_CANONICAL_IDS = {
    "goal": "SELECT id::text FROM goals WHERE t_invalid IS NULL AND status <> 'merged'",
    "procedure": "SELECT DISTINCT procedure_id::text FROM procedures WHERE t_invalid IS NULL AND is_engineering_fixture = false",
    "claim": f"SELECT id::text FROM knowledge_nodes WHERE node_type = 'claim' AND t_invalid IS NULL AND {_SEARCHABLE_CLAIM}",
}


async def reindex(
    pool: asyncpg.Pool, object_type: Optional[str] = None, *, shard: Optional[str] = None,
    pools: Optional[ShardPools] = None, batch: int = 200,
) -> dict[str, Any]:
    """Rebuild projections from canonical rows: enqueue every routed object of
    the type(s) (optionally only one shard) and drain. Also removes projection
    rows that no longer have a canonical row (orphans)."""
    types = [object_type] if object_type else list(OBJECT_TYPES)
    out: dict[str, Any] = {}
    for t in types:
        if shard:
            ids = [r[0] for r in await pool.fetch(
                "SELECT object_id::text FROM object_routes WHERE object_type = $1 AND home_shard_id = $2", t, shard)]
        else:
            ids = [r[0] for r in await pool.fetch(_CANONICAL_IDS[t])]
            # remote-homed objects have no local canonical row; include them via routes
            ids = sorted(set(ids) | {r[0] for r in await pool.fetch(
                "SELECT object_id::text FROM object_routes WHERE object_type = $1 AND home_shard_id <> $2", t, HOME_SHARD)})
        if ids:
            await pool.execute(
                "INSERT INTO projection_outbox (object_type, object_id) SELECT $1, x::uuid FROM unnest($2::text[]) x "
                "ON CONFLICT (object_type, object_id) WHERE status = 'pending' DO NOTHING", t, ids)
        table, key = _INDEX_TABLE[t]
        orphan_sql = f"DELETE FROM {table} WHERE NOT ({key}::text = ANY($1::text[]))"
        params: list[Any] = [ids]
        if shard:
            orphan_sql = f"DELETE FROM {table} WHERE home_shard_id = $2 AND NOT ({key}::text = ANY($1::text[]))"
            params.append(shard)
        from app.services import search_group

        if t == "goal":
            orphans = int((await pool.execute(orphan_sql, *params)).split()[-1])
            if await search_group.grouped(pool):
                await search_group.execute_all(pool, orphan_sql.replace(table, "goal_search_docs"), *params,
                                               pools=pools)
        else:
            orphans = await search_group.execute_all(pool, orphan_sql, *params, pools=pools)
        drained = await drain_outbox(pool, batch=batch, pools=pools)
        out[t] = {"enqueued": len(ids), "orphans_deleted": orphans, **drained}
    return out


async def verify_projection(pool: asyncpg.Pool) -> dict[str, Any]:
    """Consistency report. ``ok`` is False if any live canonical object (in the
    routing table) lacks a projection and has no pending refresh, any
    projection has no canonical/route, or routes disagree with the projection's
    ``home_shard_id``. Objects with a pending outbox entry are counted as
    ``lagging``, not as errors."""
    report: dict[str, Any] = {"ok": True, "types": {}}
    for t in OBJECT_TYPES:
        table, key = _INDEX_TABLE[t]
        canon = _CANONICAL_IDS[t]
        from app.services import search_group

        index_pool = pool if t == "goal" else await search_pool(pool)
        if t != "goal" and await search_group.grouped(pool):
            entry = await _verify_type_across_databases(pool, None, t, table, key, canon)
            report["types"][t] = entry
            if entry["missing"] or entry["orphans"] or entry["route_mismatch"] or entry["unrouted"]:
                report["ok"] = False
            continue
        if index_pool is not pool:
            # the projection is on the search database: no cross-database joins,
            # compare id sets instead (same definitions as the SQL below)
            entry = await _verify_type_across_databases(pool, index_pool, t, table, key, canon)
            report["types"][t] = entry
            if entry["missing"] or entry["orphans"] or entry["route_mismatch"] or entry["unrouted"]:
                report["ok"] = False
            continue
        missing = await pool.fetchval(
            f"SELECT count(*) FROM ({canon}) c(id) WHERE NOT EXISTS (SELECT 1 FROM {table} i WHERE i.{key}::text = c.id) "
            f"AND NOT EXISTS (SELECT 1 FROM projection_outbox o WHERE o.status = 'pending' "
            f"AND o.object_type = $1 AND o.object_id::text = c.id)", t)
        lagging = await pool.fetchval(
            f"SELECT count(*) FROM projection_outbox o WHERE o.status = 'pending' AND o.object_type = $1", t)
        orphans = await pool.fetchval(
            f"SELECT count(*) FROM {table} i WHERE NOT EXISTS (SELECT 1 FROM object_routes r "
            f"WHERE r.object_type = $1 AND r.object_id = i.{key})", t)
        route_mismatch = await pool.fetchval(
            f"SELECT count(*) FROM {table} i JOIN object_routes r ON r.object_type = $1 AND r.object_id = i.{key} "
            f"WHERE r.home_shard_id <> i.home_shard_id", t)
        unrouted = await pool.fetchval(
            f"SELECT count(*) FROM ({canon}) c(id) WHERE NOT EXISTS (SELECT 1 FROM object_routes r "
            f"WHERE r.object_type = $1 AND r.object_id::text = c.id)", t)
        entry = {"missing": missing, "lagging": lagging, "orphans": orphans,
                 "route_mismatch": route_mismatch, "unrouted": unrouted}
        if t == "goal" and await search_group.grouped(pool):
            # a search group: the Goal's searchable doc on a member must exist too (a lost doc is a Goal search
            # cannot find), with the same definitions
            docs = await _verify_type_across_databases(pool, None, t, "goal_search_docs", key, canon)
            entry.update({"docs_missing": docs["missing"], "docs_orphans": docs["orphans"],
                          "docs_route_mismatch": docs["route_mismatch"]})
            missing, orphans = missing + docs["missing"], orphans + docs["orphans"]
            route_mismatch = route_mismatch + docs["route_mismatch"]
        report["types"][t] = entry
        if missing or orphans or route_mismatch or unrouted:
            report["ok"] = False
    report["lag"] = await projection_lag(pool)
    return report


async def _verify_type_across_databases(
    pool: Any, index_pool: Any, object_type: str, table: str, key: str, canon: str,
) -> dict[str, Any]:
    canonical = {r[0] for r in await pool.fetch(canon)}
    routes = {r["id"]: r["home_shard_id"] for r in await pool.fetch(
        "SELECT object_id::text AS id, home_shard_id FROM object_routes WHERE object_type = $1", object_type)}
    pending = {r[0] for r in await pool.fetch(
        "SELECT object_id::text FROM projection_outbox WHERE status = 'pending' AND object_type = $1", object_type)}
    sql = f"SELECT {key}::text AS id, home_shard_id FROM {table}"
    if index_pool is None:   # a search group: every member's rows
        from app.services import search_group

        rows = await search_group.fetch_all(pool, sql, strict=True)
    else:
        rows = await index_pool.fetch(sql)
    projected = {r["id"]: r["home_shard_id"] for r in rows}
    return {
        "missing": len(canonical - set(projected) - pending),
        "lagging": len(pending),
        "orphans": len(set(projected) - set(routes)),
        "route_mismatch": sum(1 for oid, shard in projected.items() if oid in routes and routes[oid] != shard),
        "unrouted": len(canonical - set(routes)),
        "database": "search",
    }


# ------------------------------------------------ in-process drainer (API / MCP server processes)
import asyncio as _asyncio
import os as _os


def start_background_drain(pool: asyncpg.Pool, *, interval_s: Optional[float] = None) -> Optional["_asyncio.Task"]:
    """Keep the global projections fresh in processes that serve reads (REST/MCP) even when no ingestion worker
    is running. Idempotent work (`drain_outbox` is safe to run from any number of processes). Disabled with
    PROJECTION_DRAIN_ENABLED=0. Never raises into the host process."""
    if _os.environ.get("PROJECTION_DRAIN_ENABLED", "1") in ("0", "false", "False"):
        return None
    every = interval_s if interval_s is not None else float(_os.environ.get("PROJECTION_DRAIN_INTERVAL_SECONDS", "5"))
    # Concurrent drainers (SKIP LOCKED makes them share the outbox without overlap) and small batches (a batch is one
    # transaction: its entries show as applied only at its commit). One drainer far from its databases applies an
    # entry in seconds -- 7.5 s each over ~0.8 s round trips (2026-10-01) -- far below what ingestion enqueues.
    concurrency = max(1, int(_os.environ.get("PROJECTION_DRAIN_CONCURRENCY", "1")))
    batch = max(1, int(_os.environ.get("PROJECTION_DRAIN_BATCH", "200")))

    async def loop() -> None:
        from app.services.shards import pools_for

        while True:
            try:
                await drain_outbox(pool, batch=batch, pools=pools_for(pool), max_batches=20)
            except _asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("projection drain failed; retrying", exc_info=True)
            await _asyncio.sleep(every)

    async def loops() -> None:
        await _asyncio.gather(*[loop() for _ in range(concurrency)])

    return _asyncio.get_running_loop().create_task(loops(), name="projection-drain")
