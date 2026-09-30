"""Reads of canonical objects whose identity is already known, routed to their home
shard(s) -- never broadcast to every shard, never answered from the control
database alone (docs/sharding.md).

Canonical Goals, Procedures and Claims live on their home shard; the control
database holds only routing (object_routes, goal_names) and projections. A
reader that queries the control database's own `goals`/`procedures` tables for
an object homed on K004 silently gets "not found". These helpers are the one
place that turns a known id / exact name / owning Goal into the right shard.

With no remote shard registered every helper is a plain query on `pool`: the
single-database deployment pays nothing.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from app.services.shards import HOME_SHARD, home_pool, hydrate_rows, lookup_routes, multi_shard, pools_for, search_pool

log = logging.getLogger(__name__)


def goal_scope_key(scope_type: Optional[str], scope_entity_id: Optional[str]) -> str:
    """Same key as migration 96's sl_goal_scope_key()."""
    if not scope_type or scope_type == "global":
        return "global"
    return f"{scope_type}:{scope_entity_id or ''}"


async def fetch_goal(
    pool: Any, goal_id: str, *, columns: str = "*", where: str = "t_invalid IS NULL",
) -> Optional[dict]:
    """One canonical Goal row from its home shard (or None)."""
    owner = await home_pool(pool, "goal", str(goal_id))
    row = await owner.fetchrow(f"SELECT {columns} FROM goals WHERE id = $1::uuid AND {where}", str(goal_id))
    return dict(row) if row is not None else None


async def find_goals_by_exact_names(
    pool: Any, normalized_names: Sequence[str], *, scope_type: Optional[str], scope_entity_id: Optional[str],
    columns: str = "*",
) -> dict[str, dict]:
    """{name key: canonical Goal row} for LIVE Goals, preferring the caller's own scope over 'global' (the same
    precedence the write path's dedup uses). Resolved through the global `goal_names` index, so a Goal homed on any
    shard is found; each row is read from its home shard in one batch per shard.

    The name key is computed in SQL on BOTH sides (`normalize_goal_name(canonical_name) = normalize_goal_name(key)`,
    migration 131 -- no stored copy): the rule is idempotent, so a caller may pass raw names or keys, and results
    are keyed by exactly what the caller passed."""
    names = sorted({n for n in normalized_names if n})
    if not names:
        return {}
    if not await multi_shard(pool):
        # One database: its own `goals` table IS every Goal (goal_names mirrors it
        # in the same transaction) -- read it directly, freshest possible.
        scope_sql = ("((g.scope_type = $2 AND g.scope_entity_id IS NOT DISTINCT FROM $3) OR g.scope_type = 'global')"
                     if scope_type and scope_type != "global" else "g.scope_type = 'global'")
        args = [names, scope_type, scope_entity_id] if scope_type and scope_type != "global" else [names]
        rows = await pool.fetch(
            f"SELECT q.lookup_key, g.scope_type AS _scope_type, {_qualified(columns)} "
            "FROM unnest($1::text[]) AS q(lookup_key) "
            "JOIN goals g ON normalize_goal_name(g.canonical_name) = normalize_goal_name(q.lookup_key) "
            f"WHERE g.t_invalid IS NULL AND {scope_sql}", *args)
        picked: dict[str, dict] = {}
        for row in rows:
            row = dict(row)
            key, scope = row.pop("lookup_key"), row.pop("_scope_type")
            current = picked.get(key)
            if current is None or (current[1] == "global" and scope != "global"):
                picked[key] = (row, scope)
        return {k: v[0] for k, v in picked.items()}
    keys = ["global"]
    own = goal_scope_key(scope_type, scope_entity_id)
    if own != "global":
        keys.append(own)
    hits = await pool.fetch(
        "SELECT n.scope_key, q.lookup_key, n.goal_id::text AS goal_id, n.home_shard_id "
        "FROM unnest($2::text[]) AS q(lookup_key) "
        "JOIN goal_names n ON normalize_goal_name(n.canonical_name) = normalize_goal_name(q.lookup_key) "
        "WHERE n.scope_key = ANY($1::text[])",
        keys, names)
    chosen: dict[str, Any] = {}
    for hit in hits:
        current = chosen.get(hit["lookup_key"])
        if current is None or (current["scope_key"] == "global" and hit["scope_key"] != "global"):
            chosen[hit["lookup_key"]] = hit
    if not chosen:
        return {}
    routes = {hit["goal_id"]: hit["home_shard_id"] or HOME_SHARD for hit in chosen.values()}

    async def fetch(goal_pool: Any, ids: list[str]):
        return await goal_pool.fetch(
            f"SELECT {_qualified(columns)} FROM goals g WHERE g.id = ANY($1::uuid[]) AND g.t_invalid IS NULL "
            "AND g.status <> 'merged'", ids)

    hydration = await hydrate_rows(pools_for(pool), routes, fetch)
    if hydration.partial:
        log.warning("exact-name goal lookup: shards unavailable %s", hydration.unavailable_shards)
    by_id = {str(row["id"]): row for row in hydration.rows.values()}
    return {name: dict(by_id[hit["goal_id"]]) for name, hit in chosen.items() if hit["goal_id"] in by_id}


async def fetch_goal_procedures(
    pool: Any, goal_ids: Sequence[str], *, columns: str, where: str = "t_invalid IS NULL",
) -> list[dict]:
    """Live Procedure rows whose `achieves_goal_id` is one of `goal_ids`.

    The control database's own rows are read directly (always fresh). Rows homed
    on remote shards are located through `procedure_search_index.goal_id` (remote
    writes project immediately, see procedures.capture_procedure) and read with
    one batched query per involved shard -- instead of asking every shard.
    `where` is applied on the canonical rows, so the caller's filter is exact."""
    ids = list(dict.fromkeys(str(goal_id) for goal_id in goal_ids if goal_id))
    if not ids:
        return []
    rows = [dict(row) for row in await pool.fetch(
        f"SELECT {columns} FROM procedures WHERE achieves_goal_id = ANY($1::uuid[]) AND {where}", ids)]
    if not await multi_shard(pool):
        return rows
    refs = await (await search_pool(pool)).fetch(
        "SELECT procedure_row_id::text AS id, home_shard_id FROM procedure_search_index "
        "WHERE goal_id = ANY($1::uuid[]) AND home_shard_id <> $2", ids, HOME_SHARD)
    if not refs:
        return rows

    async def fetch(shard_pool: Any, row_ids: list[str]):
        return await shard_pool.fetch(
            f"SELECT {columns} FROM procedures WHERE id = ANY($1::uuid[]) "
            f"AND achieves_goal_id = ANY($2::uuid[]) AND {where}", row_ids, ids)

    hydration = await hydrate_rows(pools_for(pool), {r["id"]: r["home_shard_id"] for r in refs}, fetch)
    if hydration.partial:
        log.warning("goal procedures: shards unavailable %s", hydration.unavailable_shards)
    seen = {str(row.get("id")) for row in rows}
    rows.extend(row for key, row in hydration.rows.items() if key not in seen)
    return rows


async def fetch_procedures_by_stable_ids(
    pool: Any, procedure_ids: Sequence[str], *, columns: str, where: str = "t_invalid IS NULL",
) -> list[dict]:
    """Live Procedure rows for known stable `procedure_id`s, each from its home shard."""
    ids = list(dict.fromkeys(str(pid) for pid in procedure_ids if pid))
    if not ids:
        return []
    if not await multi_shard(pool):
        return [dict(row) for row in await pool.fetch(
            f"SELECT {columns} FROM procedures WHERE procedure_id = ANY($1::uuid[]) AND {where}", ids)]
    routes = await lookup_routes(pool, "procedure", ids)
    id_to_shard = {pid: routes.get(pid, HOME_SHARD) for pid in ids}

    async def fetch(shard_pool: Any, pids: list[str]):
        return await shard_pool.fetch(
            f"SELECT {columns} FROM procedures WHERE procedure_id = ANY($1::uuid[]) AND {where}", pids)

    hydration = await hydrate_rows(pools_for(pool), id_to_shard, fetch, id_key="procedure_id")
    if hydration.partial:
        log.warning("procedures by id: shards unavailable %s", hydration.unavailable_shards)
    return list(hydration.rows.values())


async def fetch_claim(pool: Any, claim_id: str, *, columns: str, where: str = "TRUE", args: Sequence[Any] = ()) -> Optional[dict]:
    """One canonical Claim (knowledge_nodes) row from its home shard. `where` may
    reference $2.. for `args`."""
    owner = await home_pool(pool, "claim", str(claim_id))
    rows = await owner.fetch(
        f"SELECT {columns} FROM knowledge_nodes WHERE id = $1::uuid AND node_type = 'claim' AND {where}",
        str(claim_id), *args)
    return dict(rows[0]) if rows else None


def _qualified(columns: str) -> str:
    """A caller's column list read from `goals g`. `*` is `g.*`; plain names are prefixed; expressions (anything
    with a parenthesis, e.g. `normalize_goal_name(canonical_name) AS normalized_name`) are passed through."""
    if columns.strip() == "*":
        return "g.*"
    out = []
    for part in _split_columns(columns):
        part = part.strip()
        out.append(part if ("(" in part or "." in part) else f"g.{part}")
    return ", ".join(out)


def _split_columns(columns: str) -> list[str]:
    """Split on commas that are not inside parentheses."""
    parts, depth, cur = [], 0, []
    for ch in columns:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts

