"""Database access for the recommender.

Control DB (A): routing_models, routing_prices, routing_params, routing_posteriors,
and reads of goal_search_index / goal_relations. Project B (search_pool): the
routing_observations and routing_decisions logs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.routing.config import CHECK_KINDS, STEP_ROLES
from app.routing.costs import Price
from app.services.access import AccessScope, visibility_predicate
from app.services.shards import search_pool

LOCAL_REFIT_JOB = "routing_local_refit"
NIGHTLY_REFIT_JOB = "routing_refit"
SOURCES = ("live", "sweep", "public_import")

_OBS_COLUMNS = (
    "source", "goal_id", "procedure_id", "model_key", "scaffold", "instance_key", "attempt_index", "check_kind",
    "accepted", "gold_correct", "pass_fraction", "tokens_in", "tokens_out", "tokens_cached", "cost_usd",
    "latency_ms", "reporter", "recommendation_id", "visibility", "owner_id", "occurred_at",
    "step_order", "step_role",
)


class ObservationRejected(ValueError):
    pass


def validate_observation(obs: Mapping[str, Any]) -> dict[str, Any]:
    row = {k: obs.get(k) for k in _OBS_COLUMNS}
    for key in ("goal_id", "model_key", "scaffold", "instance_key"):
        if not row[key]:
            raise ObservationRejected(f"{key} is required")
    row["source"] = row["source"] or "live"
    if row["source"] not in SOURCES:
        raise ObservationRejected(f"source must be one of {SOURCES}")
    row["check_kind"] = row["check_kind"] or "self_report"
    if row["check_kind"] not in CHECK_KINDS:
        raise ObservationRejected(f"check_kind must be one of {CHECK_KINDS}")
    if row["reporter"] and row["check_kind"] == "benchmark":
        # a host's report cannot DEFINE correctness; only Stealth-observed runs can
        raise ObservationRejected("a host-reported attempt cannot use check_kind 'benchmark'")
    if "|" in str(row["model_key"]) or "|" in str(row["scaffold"]):
        raise ObservationRejected("model and scaffold may not contain '|'")
    if row["accepted"] is None:
        raise ObservationRejected("accepted is required")
    if row["step_order"] is not None:
        if not row["procedure_id"]:
            raise ObservationRejected("a step attempt (step_order) needs its procedure_id")
        row["step_order"] = int(row["step_order"])
        row["step_role"] = row["step_role"] or "other"
        if row["step_role"] not in STEP_ROLES:
            raise ObservationRejected(f"step_role must be one of {STEP_ROLES}")
    elif row["step_role"] is not None:
        raise ObservationRejected("step_role needs a step_order")
    row["accepted"] = bool(row["accepted"])
    row["attempt_index"] = int(row["attempt_index"] or 0)
    row["visibility"] = row["visibility"] or "public"
    for key in ("tokens_in", "tokens_out", "tokens_cached", "latency_ms"):
        if row[key] is not None:
            row[key] = max(0, int(row[key]))
    return row


async def ensure_models(pool: Any, model_keys: Iterable[str]) -> None:
    keys = sorted({k for k in model_keys if k})
    if keys:
        await pool.execute(
            "INSERT INTO routing_models (model_key) SELECT unnest($1::text[]) ON CONFLICT (model_key) DO NOTHING", keys)


async def insert_observations(pool: Any, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Validated rows -> project B. Returns their ids. The model registry on A learns
    every model key it has not seen."""
    clean = [validate_observation(r) for r in rows]
    if not clean:
        return []
    await ensure_models(pool, (r["model_key"] for r in clean))
    log = await search_pool(pool)
    ids = [str(uuid.uuid4()) for _ in clean]
    cols = ", ".join(("id",) + _OBS_COLUMNS)
    marks = ", ".join(f"${i}" for i in range(1, len(_OBS_COLUMNS) + 2))
    now = datetime.now().astimezone()
    await log.executemany(
        f"INSERT INTO routing_observations ({cols}) VALUES ({marks})",
        [[obs_id, *[(r[c] if c != "occurred_at" else (r[c] or now)) for c in _OBS_COLUMNS]]
         for obs_id, r in zip(ids, clean)])
    return ids


async def goal_observations(pool: Any, goal_id: str) -> list[dict]:
    log = await search_pool(pool)
    rows = await log.fetch(
        "SELECT *, goal_id::text AS goal_id, procedure_id::text AS procedure_id FROM routing_observations "
        "WHERE goal_id = $1::uuid ORDER BY occurred_at", str(goal_id))
    return [dict(r) for r in rows]


async def all_observations(pool: Any, *, public_only: bool) -> list[dict]:
    log = await search_pool(pool)
    where = "WHERE visibility = 'public'" if public_only else ""
    rows = await log.fetch(
        f"SELECT *, goal_id::text AS goal_id, procedure_id::text AS procedure_id FROM routing_observations {where} "
        "ORDER BY occurred_at")
    return [dict(r) for r in rows]


async def goal_token_stats(pool: Any, goal_id: str, *, steps: bool = False) -> dict[str, dict[str, list[float]]]:
    """{unit: {"1"|"0": [n, mean ln in, mean ln out, mean ln(1+cached)]}} for one Goal's
    whole-task attempts, or (steps=True) its single-step attempts."""
    log = await search_pool(pool)
    kind = "step_order IS NOT NULL" if steps else "step_order IS NULL"
    rows = await log.fetch(
        "SELECT model_key || '|' || scaffold AS unit, accepted, count(*) AS n, "
        "avg(ln(greatest(tokens_in, 1))) AS li, avg(ln(greatest(tokens_out, 1))) AS lo, "
        "avg(ln(1 + coalesce(tokens_cached, 0))) AS lc "
        f"FROM routing_observations WHERE goal_id = $1::uuid AND tokens_in IS NOT NULL AND tokens_out IS NOT NULL "
        f"AND {kind} GROUP BY 1, 2", str(goal_id))
    out: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        out.setdefault(r["unit"], {})["1" if r["accepted"] else "0"] = [
            float(r["n"]), float(r["li"]), float(r["lo"]), float(r["lc"])]
    return out


async def record_decision(pool: Any, row: Mapping[str, Any]) -> None:
    log = await search_pool(pool)
    await log.execute(
        "INSERT INTO routing_decisions (id, goal_id, procedure_id, instance_key, params_version, candidates, ladder, "
        "propensity, meets_target, predicted, constraints, visibility, owner_id, step_order) "
        "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10::jsonb, $11::jsonb, $12, $13, $14)",
        row["id"], row["goal_id"], row.get("procedure_id"), row["instance_key"], row.get("params_version"),
        json.dumps(row["candidates"]), json.dumps(row["ladder"]), float(row["propensity"]), bool(row["meets_target"]),
        json.dumps(row["predicted"]), json.dumps(row.get("constraints") or {}), row.get("visibility") or "public",
        row.get("owner_id"), row.get("step_order"))


# ------------------------------------------------------------------ control DB

async def active_params(pool: Any) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT version, draws, meta, method FROM routing_params WHERE status = 'active' ORDER BY version DESC LIMIT 1")
    return _params_row(row)


async def params_version(pool: Any, version: int) -> Optional[dict]:
    row = await pool.fetchrow("SELECT version, draws, meta, method FROM routing_params WHERE version = $1", version)
    return _params_row(row)


def _params_row(row: Any) -> Optional[dict]:
    if row is None:
        return None
    meta = row["meta"]
    return {"version": int(row["version"]), "draws": bytes(row["draws"]),
            "meta": json.loads(meta) if isinstance(meta, str) else dict(meta), "method": row["method"]}


async def save_params(pool: Any, *, method: str, draws: bytes, meta: Mapping[str, Any],
                      diagnostics: Mapping[str, Any]) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE routing_params SET status = 'superseded' WHERE status = 'active'")
            return int(await conn.fetchval(
                "INSERT INTO routing_params (status, method, draws, meta, diagnostics) "
                "VALUES ('active', $1, $2, $3::jsonb, $4::jsonb) RETURNING version",
                method, draws, json.dumps(meta), json.dumps(diagnostics)))


async def load_posteriors(pool: Any, kind: str, ids: Sequence[str]) -> dict[str, dict]:
    if not ids:
        return {}
    rows = await pool.fetch(
        "SELECT entity_id::text AS id, params_version, draws, n_observations, method FROM routing_posteriors "
        "WHERE entity_kind = $1 AND entity_id = ANY($2::uuid[])", kind, list({str(i) for i in ids}))
    return {r["id"]: {"version": int(r["params_version"]), "draws": bytes(r["draws"]),
                      "n_observations": int(r["n_observations"]), "method": r["method"]} for r in rows}


async def save_posteriors(pool: Any, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    await pool.executemany(
        "INSERT INTO routing_posteriors (entity_kind, entity_id, params_version, draws, n_observations, "
        "last_observation_at, method, updated_at) VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, now()) "
        "ON CONFLICT (entity_kind, entity_id) DO UPDATE SET params_version = EXCLUDED.params_version, "
        "draws = EXCLUDED.draws, n_observations = EXCLUDED.n_observations, "
        "last_observation_at = EXCLUDED.last_observation_at, method = EXCLUDED.method, updated_at = now()",
        [(r["kind"], r["id"], r["version"], r["draws"], int(r.get("n_observations") or 0),
          r.get("last_observation_at"), r["method"]) for r in rows])


async def model_registry(pool: Any) -> dict[str, dict]:
    rows = await pool.fetch("SELECT model_key, predecessor_model_key, open_weights, runs_locally FROM routing_models")
    return {r["model_key"]: {"predecessor": r["predecessor_model_key"], "open_weights": r["open_weights"],
                             "local": r["runs_locally"]} for r in rows}


async def current_prices(pool: Any, model_keys: Sequence[str]) -> dict[str, Price]:
    if not model_keys:
        return {}
    rows = await pool.fetch(
        "SELECT DISTINCT ON (model_key) model_key, input_usd_per_mtok, output_usd_per_mtok, cached_input_usd_per_mtok "
        "FROM routing_prices WHERE model_key = ANY($1::text[]) AND effective_from <= now() "
        "ORDER BY model_key, effective_from DESC", list(set(model_keys)))
    return {r["model_key"]: Price(float(r["input_usd_per_mtok"]), float(r["output_usd_per_mtok"]),
                                  None if r["cached_input_usd_per_mtok"] is None else float(r["cached_input_usd_per_mtok"]))
            for r in rows}


async def set_price(pool: Any, model_key: str, *, input_per_mtok: float, output_per_mtok: float,
                    cached_per_mtok: Optional[float] = None) -> None:
    await ensure_models(pool, [model_key])
    await pool.execute(
        "INSERT INTO routing_prices (model_key, input_usd_per_mtok, output_usd_per_mtok, cached_input_usd_per_mtok) "
        "VALUES ($1, $2, $3, $4)", model_key, input_per_mtok, output_per_mtok, cached_per_mtok)


async def set_model(pool: Any, model_key: str, *, predecessor: Optional[str] = None,
                    open_weights: Optional[bool] = None, runs_locally: Optional[bool] = None) -> None:
    await ensure_models(pool, [model_key] + ([predecessor] if predecessor else []))
    await pool.execute(
        "UPDATE routing_models SET predecessor_model_key = COALESCE($2, predecessor_model_key), "
        "open_weights = COALESCE($3, open_weights), runs_locally = COALESCE($4, runs_locally) WHERE model_key = $1",
        model_key, predecessor, open_weights, runs_locally)


def _vector(text: Optional[str]) -> Optional[list[float]]:
    return json.loads(text) if text else None


async def visible_goal(pool: Any, goal_id: str, access_scope: AccessScope) -> Optional[dict]:
    vis, params = visibility_predicate(access_scope, alias="g", param_index=2)
    row = await pool.fetchrow(
        f"SELECT g.goal_id::text AS id, g.visibility::text AS visibility, g.owner_id, g.embedding::text AS embedding "
        f"FROM goal_search_index g WHERE g.goal_id = $1::uuid AND {vis}", str(goal_id), *params)
    if row is None:
        return None
    return {"id": row["id"], "visibility": row["visibility"], "owner_id": row["owner_id"],
            "embedding": _vector(row["embedding"])}


async def goal_rows(pool: Any, goal_ids: Sequence[str]) -> dict[str, dict]:
    if not goal_ids:
        return {}
    rows = await pool.fetch(
        "SELECT goal_id::text AS id, visibility::text AS visibility, owner_id, embedding::text AS embedding "
        "FROM goal_search_index WHERE goal_id = ANY($1::uuid[])", list({str(g) for g in goal_ids}))
    return {r["id"]: {"id": r["id"], "visibility": r["visibility"], "owner_id": r["owner_id"],
                      "embedding": _vector(r["embedding"])} for r in rows}


async def goal_parents(pool: Any, goal_ids: Sequence[str]) -> dict[str, list[str]]:
    if not goal_ids:
        return {}
    rows = await pool.fetch(
        "SELECT specific_goal_id::text AS child, abstract_goal_id::text AS parent FROM goal_relations "
        "WHERE specific_goal_id = ANY($1::uuid[]) AND relation_type = 'SPECIALIZES' AND status = 'accepted'",
        list({str(g) for g in goal_ids}))
    out: dict[str, list[str]] = {str(g): [] for g in goal_ids}
    for r in rows:
        out.setdefault(r["child"], []).append(r["parent"])
    return out


async def ancestry(pool: Any, goal_ids: Sequence[str]) -> dict[str, list[str]]:
    """Parents of every Goal in `goal_ids` and of all their ancestors (the closure)."""
    parents: dict[str, list[str]] = {}
    frontier = {str(g) for g in goal_ids}
    while frontier:
        found = await goal_parents(pool, sorted(frontier))
        parents.update(found)
        frontier = {p for ps in found.values() for p in ps} - set(parents)
    return parents


async def enqueue_local_refit(pool: Any, goal_id: str, observation_id: str, *, visibility: str,
                              owner_id: Optional[str]) -> None:
    from app.ingestion import queue

    await queue.enqueue(
        pool, LOCAL_REFIT_JOB, {"goal_id": str(goal_id)}, idempotency_key=f"{goal_id}:{observation_id}",
        scope_type={"public": "global", "private": "user", "org": "organization"}[visibility],
        owner_id=owner_id if visibility != "public" else None, visibility=visibility, max_attempts=3,
        offload=False)   # the payload is one id: nothing to put in object storage
