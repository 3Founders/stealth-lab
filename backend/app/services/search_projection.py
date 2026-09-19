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
from app.services.shards import HOME_SHARD, OBJECT_TYPES, ShardPools, lookup_routes

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
    "embedding_provider FROM goals WHERE id = $1::uuid AND t_invalid IS NULL"
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
_CLAIM_SQL = (
    "SELECT id, name, subject, predicate, object, properties, claim_status, visibility, owner_id, "
    "scope_type, scope_entity_id, tenant_id, embedding::text AS embedding, embedding_model_id "
    "FROM knowledge_nodes WHERE id = $1::uuid AND node_type = 'claim' AND t_invalid IS NULL"
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
            status=row["status"], version=row["version"], **common,
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
    visibility, owner_id, scope_type, scope_entity_id, updated_at)
VALUES ($1::uuid, $2, $3, $4, $5, to_tsvector('english', $5), $6::vector, $7, $8, $9, $10, $11, $12,
    $13::visibility_level, $14, $15, $16, now())
ON CONFLICT (goal_id) DO UPDATE SET
    canonical_name = EXCLUDED.canonical_name, short_description = EXCLUDED.short_description,
    aliases = EXCLUDED.aliases, search_text = EXCLUDED.search_text, search_tsv = EXCLUDED.search_tsv,
    embedding = EXCLUDED.embedding, embedding_model = EXCLUDED.embedding_model,
    embedding_version = EXCLUDED.embedding_version, embedding_dim = EXCLUDED.embedding_dim,
    home_shard_id = EXCLUDED.home_shard_id, status = EXCLUDED.status, version = EXCLUDED.version,
    visibility = EXCLUDED.visibility, owner_id = EXCLUDED.owner_id, scope_type = EXCLUDED.scope_type,
    scope_entity_id = EXCLUDED.scope_entity_id, updated_at = now(), projected_at = now()
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

_INDEX_TABLE = {"goal": ("goal_search_index", "goal_id"), "procedure": ("procedure_search_index", "procedure_id"),
                "claim": ("claim_search_index", "claim_id")}


async def _upsert(conn, object_type: str, p: dict) -> None:
    common = (p["embedding"], p["embedding_model"], p["embedding_version"], p["embedding_dim"], p["home_shard_id"])
    tail = (p["visibility"], p["owner_id"], p["scope_type"], p["scope_entity_id"])
    if object_type == "goal":
        await conn.execute(_UPSERT_GOAL, p["key"], p["canonical_name"], p["short_description"], p["aliases"],
                           p["search_text"], *common, p["status"], p["version"], *tail)
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
    if row is None or (object_type == "goal" and row["status"] == "merged"):
        await conn.execute(f"DELETE FROM {table} WHERE {key} = $1::uuid", object_id)
        return "deleted"
    await _upsert(conn, object_type, build_projection(object_type, dict(row), shard))
    return "upserted"


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
    "claim": "SELECT id::text FROM knowledge_nodes WHERE node_type = 'claim' AND t_invalid IS NULL",
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
        orphans = int((await pool.execute(orphan_sql, *params)).split()[-1])
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
        report["types"][t] = entry
        if missing or orphans or route_mismatch or unrouted:
            report["ok"] = False
    report["lag"] = await projection_lag(pool)
    return report
