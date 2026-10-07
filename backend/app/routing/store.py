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
# Migration 147/148 columns. Written only when a row carries them, so a database the migration has not
# reached yet keeps accepting live observations unchanged.
_OPTIONAL_OBS_COLUMNS = ("item_created_at", "dedupe_key")


class ObservationRejected(ValueError):
    pass


# ------------------------------------------------------------------ row-level security scope (migration 150)
#
# routing_decisions / routing_observations carry FORCE RLS: 'public' rows for everyone, any other row only for its
# owner's readers (the caller's subject and organisations) or under the explicit system scope. Every statement on
# them runs inside a transaction that binds the CURRENT scope first. The scope is set once per entry point:
#   requests  (service.recommend, plan.load_instance / report_result)  -> routing_scope(access_scope)
#   workers   (refits, model update, admin commands, imports)          -> routing_scope(system=True)
# With no scope set a statement sees and writes 'public' rows only -- a forgotten scope fails closed.

import contextlib as _contextlib
import contextvars as _contextvars

_RLS_SCOPE: "_contextvars.ContextVar[dict]" = _contextvars.ContextVar("routing_rls_scope", default={})


@_contextlib.contextmanager
def routing_scope(access_scope: Optional[AccessScope] = None, *, system: bool = False):
    """Bind the routing logs' row-level-security scope for everything awaited inside (contextvars follow awaits)."""
    from app.services.access import routing_readers_of

    if system:
        value = {"system": True}
    elif access_scope is not None and getattr(access_scope, "is_unrestricted", False):
        value = {"system": True}
    elif access_scope is not None:
        value = {"readers": routing_readers_of(access_scope)}
    else:
        value = {}
    token = _RLS_SCOPE.set(value)
    try:
        yield
    finally:
        _RLS_SCOPE.reset(token)


def current_scope() -> dict:
    return dict(_RLS_SCOPE.get())


async def _log_call(log: Any, method: str, sql: str, *args: Any) -> Any:
    """One statement on a routing log table, under the current scope."""
    async with _log_tx(log) as conn:
        return await getattr(conn, method)(sql, *args)


@_contextlib.asynccontextmanager
async def _log_tx(log: Any):
    from app.services.access import scoped_transaction

    async with scoped_transaction(log, **current_scope()) as conn:
        yield conn


def validate_observation(obs: Mapping[str, Any]) -> dict[str, Any]:
    row = {k: obs.get(k) for k in _OBS_COLUMNS + _OPTIONAL_OBS_COLUMNS}
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
    ids = [str(uuid.uuid4()) for _ in clean]
    columns = _OBS_COLUMNS + tuple(c for c in _OPTIONAL_OBS_COLUMNS if any(r.get(c) is not None for r in clean))
    cols = ", ".join(("id",) + columns)
    marks = ", ".join(f"${i}" for i in range(1, len(columns) + 2))
    # a re-imported public result (same dedupe_key) is skipped, never counted twice
    conflict = " ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING" if "dedupe_key" in columns else ""
    now = datetime.now().astimezone()
    # A Goal's observations stay together on one search member (per-Goal aggregates read one database);
    # storage layout v2. Without a search group this is the single search pool, as before.
    by_goal: dict[str, list] = {}
    for obs_id, r in zip(ids, clean):
        by_goal.setdefault(str(r["goal_id"]), []).append(
            [obs_id, *[(r[c] if c != "occurred_at" else (r[c] or now)) for c in columns]])
    for goal_id, batch in by_goal.items():
        log = await _goal_log_pool(pool, goal_id)
        async with _log_tx(log) as conn:
            await conn.executemany(f"INSERT INTO routing_observations ({cols}) VALUES ({marks}){conflict}", batch)
    return ids


async def _goal_log_pool(pool: Any, goal_id: str) -> Any:
    """The search member holding a Goal's routing observations and decisions (the single search pool without a
    search group)."""
    from app.services import search_group

    return await search_group.pool_for_object(pool, "routing_goal", str(goal_id), placement_key=f"goal:{goal_id}")


async def goal_observations(pool: Any, goal_id: str) -> list[dict]:
    log = await _goal_log_pool(pool, goal_id)
    async with _log_tx(log) as conn:
        rows = await conn.fetch(
            "SELECT *, goal_id::text AS goal_id, procedure_id::text AS procedure_id FROM routing_observations "
            "WHERE goal_id = $1::uuid ORDER BY occurred_at", str(goal_id))
    return [dict(r) for r in rows]


def _json_value(value: Any) -> Any:
    """A jsonb value as Python. These routing tables are written with json.dumps() through a pool whose jsonb codec
    already encodes, so a stored value is a JSON *string* holding the JSON text; unwrap until it is not a string
    (twice at most), which also reads correctly if the writers are ever fixed."""
    for _ in range(2):
        if isinstance(value, (str, bytes)):
            value = json.loads(value)
    return value


async def instance_decision(pool: Any, goal_id: str, instance_key: str) -> Optional[dict]:
    """The latest whole-task decision for one instance (each report re-decides under the same key)."""
    log = await _goal_log_pool(pool, goal_id)
    row = await _log_call(log, "fetchrow", 
        "SELECT id::text AS id, goal_id::text AS goal_id, procedure_id::text AS procedure_id, candidates, ladder, "
        "constraints, visibility, owner_id FROM routing_decisions "
        "WHERE goal_id = $1::uuid AND instance_key = $2 AND step_order IS NULL ORDER BY created_at DESC LIMIT 1",
        str(goal_id), instance_key)
    if row is None:
        return None
    out = dict(row)
    for key in ("candidates", "ladder", "constraints"):
        out[key] = _json_value(out[key])
    return out


async def instance_issuer(pool: Any, goal_id: str, instance_key: str) -> Optional[str]:
    """Who an instance was first issued to: the `_caller` stored with its EARLIEST decision (None when it was
    issued to nobody in particular, e.g. by recommend_models without a plan)."""
    log = await _goal_log_pool(pool, goal_id)
    raw = await _log_call(log, "fetchval",        # parsed in Python: `constraints->>'_caller'` returns NULL on the double-encoded rows
        "SELECT constraints FROM routing_decisions "
        "WHERE goal_id = $1::uuid AND instance_key = $2 AND step_order IS NULL ORDER BY created_at ASC LIMIT 1",
        str(goal_id), instance_key)
    constraints = _json_value(raw) if raw is not None else None
    caller = constraints.get("_caller") if isinstance(constraints, dict) else None
    return None if caller is None else str(caller)


async def instance_attempts(pool: Any, goal_id: str, instance_key: str) -> list[dict]:
    """Whole-task attempts already reported for one instance, oldest first."""
    log = await _goal_log_pool(pool, goal_id)
    rows = await _log_call(log, "fetch", 
        "SELECT model_key, scaffold, accepted, check_kind, attempt_index FROM routing_observations "
        "WHERE goal_id = $1::uuid AND instance_key = $2 AND step_order IS NULL "
        "ORDER BY occurred_at, attempt_index", str(goal_id), instance_key)
    return [dict(r) for r in rows]


async def all_observations(pool: Any, *, public_only: bool) -> list[dict]:
    from app.services import search_group

    where = "WHERE visibility = 'public'" if public_only else ""
    rows = await search_group.fetch_all(
        pool, f"SELECT *, goal_id::text AS goal_id, procedure_id::text AS procedure_id FROM routing_observations {where} "
        "ORDER BY occurred_at", strict=True, scope=current_scope())
    return [dict(r) for r in sorted(rows, key=lambda r: (r["occurred_at"], str(r["id"])))]


async def goal_token_stats(pool: Any, goal_id: str, *, steps: bool = False) -> dict[str, dict[str, list[float]]]:
    """{unit: {"1"|"0": [n, mean ln in, mean ln out, mean ln(1+cached)]}} for one Goal's
    whole-task attempts, or (steps=True) its single-step attempts."""
    log = await _goal_log_pool(pool, goal_id)
    kind = "step_order IS NOT NULL" if steps else "step_order IS NULL"
    rows = await _log_call(log, "fetch", 
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


def _jsonable(value: Any) -> Any:
    """A plain JSON value for a jsonb parameter. Every pool registers a jsonb codec that encodes (app/db/session.py),
    so passing json.dumps() text stored a JSON *string* inside jsonb -- invisible to SQL `->>` (it returns NULL), the
    fail-open trap of securityp1.md §5.1 item 2. Pass objects; migration 149 unwraps the rows written before."""
    return json.loads(json.dumps(value, default=str))


async def record_decision(pool: Any, row: Mapping[str, Any]) -> None:
    log = await _goal_log_pool(pool, str(row["goal_id"]))
    await _log_call(log, "execute", 
        "INSERT INTO routing_decisions (id, goal_id, procedure_id, instance_key, params_version, candidates, ladder, "
        "propensity, meets_target, predicted, constraints, visibility, owner_id, step_order) "
        "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10::jsonb, $11::jsonb, $12, $13, $14)",
        row["id"], row["goal_id"], row.get("procedure_id"), row["instance_key"], row.get("params_version"),
        _jsonable(row["candidates"]), _jsonable(row["ladder"]), float(row["propensity"]), bool(row["meets_target"]),
        _jsonable(row["predicted"]), _jsonable(row.get("constraints") or {}), row.get("visibility") or "public",
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
                method, draws, _jsonable(meta), _jsonable(diagnostics)))


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


async def _member_embeddings(pool: Any, goal_ids: Sequence[str]) -> Optional[dict[str, Optional[str]]]:
    """With a search group (migration 132) a Goal's vector is in goal_search_docs on a member, not in the control
    row: {goal id: vector text} from the members. None when there is no group (the control row has it)."""
    from app.services import search_group

    if not await search_group.grouped(pool):
        return None
    rows = await search_group.fetch_all(
        pool, "SELECT goal_id::text AS id, embedding::text AS embedding FROM goal_search_docs "
        "WHERE goal_id = ANY($1::uuid[])", list({str(g) for g in goal_ids}), strict=True)
    return {r["id"]: r["embedding"] for r in rows}


async def visible_goal(pool: Any, goal_id: str, access_scope: AccessScope) -> Optional[dict]:
    vis, params = visibility_predicate(access_scope, alias="g", param_index=2)
    row = await pool.fetchrow(
        f"SELECT g.goal_id::text AS id, g.visibility::text AS visibility, g.owner_id, g.tenant_id::text AS tenant_id, "
        f"g.embedding::text AS embedding FROM goal_search_index g WHERE g.goal_id = $1::uuid AND {vis}",
        str(goal_id), *params)
    if row is None:
        return None
    member = await _member_embeddings(pool, [row["id"]])
    embedding = row["embedding"] if member is None else member.get(row["id"])
    return {"id": row["id"], "visibility": row["visibility"], "owner_id": row["owner_id"],
            "tenant_id": row["tenant_id"], "embedding": _vector(embedding)}


def routing_owner(visibility: Optional[str], owner_id: Optional[str], tenant_id: Optional[str]) -> Optional[str]:
    """The owner a routing row records (migration 150's policy reads it): an 'org'-visible Goal's rows belong to its
    ORGANISATION (so every member's scope -- which carries their org ids -- reaches them), anything else to its owner."""
    if visibility == "org" and tenant_id:
        return str(tenant_id)
    return None if owner_id is None else str(owner_id)


async def goal_rows(pool: Any, goal_ids: Sequence[str]) -> dict[str, dict]:
    if not goal_ids:
        return {}
    rows = await pool.fetch(
        "SELECT goal_id::text AS id, visibility::text AS visibility, owner_id, embedding::text AS embedding "
        "FROM goal_search_index WHERE goal_id = ANY($1::uuid[])", list({str(g) for g in goal_ids}))
    member = await _member_embeddings(pool, [r["id"] for r in rows]) if rows else None
    out = {r["id"]: {"id": r["id"], "visibility": r["visibility"], "owner_id": r["owner_id"],
                     "embedding": _vector(r["embedding"] if member is None else member.get(r["id"]))} for r in rows}
    for goal_id, feats in (await goal_features(pool, list(out))).items():
        out[goal_id]["features"] = feats
    return out


async def goal_features(pool: Any, goal_ids: Sequence[str]) -> dict[str, dict]:
    """Structural patch features of Goals that are also public benchmark items (migration 147)."""
    if not goal_ids:
        return {}
    rows = await _missing_table_safe(pool.fetch(
        "SELECT goal_id::text AS id, features FROM routing_evidence_items WHERE goal_id = ANY($1::uuid[]) "
        "AND features <> '{}'::jsonb", list({str(g) for g in goal_ids})))
    return {r["id"]: _json_value(r["features"]) for r in rows}


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


# ------------------------------------------------------------------ model-side priors (migration 147)

async def _missing_table_safe(coro: Any) -> list:
    """Reads of the migration-136 tables return nothing on a database the migration has not reached."""
    import asyncpg

    try:
        return await coro
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        return []


async def model_cards(pool: Any) -> list[dict]:
    rows = await _missing_table_safe(pool.fetch(
        "SELECT model_key, family, provider, release_date, training_cutoff, open_weights, params_b, active_params_b, "
        "reasoning, context_k, price_in, price_out, aliases, source FROM routing_model_cards"))
    return [dict(r) for r in rows]


async def upsert_model_cards(pool: Any, rows: Sequence[Mapping[str, Any]]) -> int:
    """Insert or refresh cards. A field the new row leaves unknown keeps its stored value; aliases merge."""
    if not rows:
        return 0
    await pool.executemany(
        "INSERT INTO routing_model_cards (model_key, family, provider, release_date, training_cutoff, open_weights, "
        "params_b, active_params_b, reasoning, context_k, price_in, price_out, aliases, source, as_of) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::text[], $14, now()) "
        "ON CONFLICT (model_key) DO UPDATE SET "
        "family = COALESCE(EXCLUDED.family, routing_model_cards.family), "
        "provider = COALESCE(EXCLUDED.provider, routing_model_cards.provider), "
        "release_date = COALESCE(EXCLUDED.release_date, routing_model_cards.release_date), "
        "training_cutoff = COALESCE(EXCLUDED.training_cutoff, routing_model_cards.training_cutoff), "
        "open_weights = COALESCE(EXCLUDED.open_weights, routing_model_cards.open_weights), "
        "params_b = COALESCE(EXCLUDED.params_b, routing_model_cards.params_b), "
        "active_params_b = COALESCE(EXCLUDED.active_params_b, routing_model_cards.active_params_b), "
        "reasoning = COALESCE(EXCLUDED.reasoning, routing_model_cards.reasoning), "
        "context_k = COALESCE(EXCLUDED.context_k, routing_model_cards.context_k), "
        "price_in = COALESCE(EXCLUDED.price_in, routing_model_cards.price_in), "
        "price_out = COALESCE(EXCLUDED.price_out, routing_model_cards.price_out), "
        "aliases = ARRAY(SELECT DISTINCT unnest(routing_model_cards.aliases || EXCLUDED.aliases)), "
        "source = CASE WHEN EXCLUDED.source = '' THEN routing_model_cards.source ELSE EXCLUDED.source END, "
        "as_of = now()",
        [(r["model_key"], r.get("family"), r.get("provider"), r.get("release_date"), r.get("training_cutoff"),
          r.get("open_weights"), r.get("params_b"), r.get("active_params_b"), r.get("reasoning"), r.get("context_k"),
          r.get("price_in"), r.get("price_out"), list(r.get("aliases") or []), r.get("source") or "") for r in rows])
    return len(rows)


async def evidence_items(pool: Any) -> list[dict]:
    rows = await _missing_table_safe(pool.fetch(
        "SELECT item_key, goal_id::text AS goal_id, is_goal, benchmark, repo, created_at, features "
        "FROM routing_evidence_items"))
    return [{**dict(r), "features": _json_value(r["features"]) or {}} for r in rows]


async def upsert_evidence_items(pool: Any, rows: Sequence[Mapping[str, Any]]) -> int:
    if not rows:
        return 0
    await pool.executemany(
        "INSERT INTO routing_evidence_items (item_key, goal_id, is_goal, benchmark, repo, created_at, features) "
        "VALUES ($1, $2::uuid, $3, $4, $5, $6, $7::jsonb) ON CONFLICT (item_key) DO UPDATE SET "
        "goal_id = EXCLUDED.goal_id, is_goal = EXCLUDED.is_goal, benchmark = EXCLUDED.benchmark, "
        "repo = EXCLUDED.repo, created_at = COALESCE(EXCLUDED.created_at, routing_evidence_items.created_at), "
        "features = EXCLUDED.features",
        [(r["item_key"], str(r["goal_id"]), bool(r.get("is_goal")), r["benchmark"], r.get("repo"),
          r.get("created_at"), _jsonable(r.get("features") or {})) for r in rows])
    return len(rows)


async def aggregate_results(pool: Any) -> list[dict]:
    rows = await _missing_table_safe(pool.fetch(
        "SELECT source, benchmark, model_key, scaffold, n, k, item_created_min, item_created_max, occurred_at "
        "FROM routing_aggregate_results"))
    return [dict(r) for r in rows]


async def upsert_aggregate_results(pool: Any, rows: Sequence[Mapping[str, Any]]) -> int:
    if not rows:
        return 0
    await ensure_models(pool, (r["model_key"] for r in rows))
    await pool.executemany(
        "INSERT INTO routing_aggregate_results (source, benchmark, model_key, scaffold, n, k, item_created_min, "
        "item_created_max, occurred_at, dedupe_key) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
        "ON CONFLICT (dedupe_key) DO UPDATE SET n = EXCLUDED.n, k = EXCLUDED.k, occurred_at = EXCLUDED.occurred_at",
        [(r["source"], r["benchmark"], r["model_key"], r["scaffold"], int(r["n"]), int(r["k"]), r.get("item_created_min"),
          r.get("item_created_max"), r["occurred_at"],
          r.get("dedupe_key") or f"{r['benchmark']}|{r['model_key']}|{r['scaffold']}|{r.get('split') or ''}")
         for r in rows])
    return len(rows)


# ------------------------------------------------------------------ between-nightly model updates (model_update.py)

MODEL_UPDATE_JOB = "routing_model_update"


async def observations_since(pool: Any, since: datetime, *, public_only: bool) -> list[dict]:
    """Observations recorded after `since` (the active fit), from every search member: the recent tail only."""
    from app.services import search_group

    where = "WHERE occurred_at > $1" + (" AND visibility = 'public'" if public_only else "")
    rows = await search_group.fetch_all(
        pool, "SELECT goal_id::text AS goal_id, model_key, scaffold, accepted, check_kind, step_order, occurred_at "
        f"FROM routing_observations {where}", since, strict=True, scope=current_scope())
    return [dict(r) for r in rows]


async def save_model_updates(pool: Any, version: int, updates: Sequence[Mapping[str, Any]]) -> None:
    from app.routing.model_update import encode

    if not updates:
        return
    await pool.executemany(
        "INSERT INTO routing_model_updates (params_version, model_key, drift, n_observations, updated_at) "
        "VALUES ($1, $2, $3, $4, now()) ON CONFLICT (params_version, model_key) DO UPDATE SET "
        "drift = EXCLUDED.drift, n_observations = EXCLUDED.n_observations, updated_at = now()",
        [(version, u["model_key"], encode(u["drift"]), int(u["n"])) for u in updates])


async def model_updates(pool: Any, version: int) -> dict[str, Any]:
    """{model_key: (S,) drift draws} for the given params version (empty before migration 147)."""
    from app.routing.model_update import decode

    rows = await _missing_table_safe(pool.fetch(
        "SELECT model_key, drift FROM routing_model_updates WHERE params_version = $1", version))
    return {r["model_key"]: decode(bytes(r["drift"])) for r in rows}


async def enqueue_model_update(pool: Any, now: Optional[datetime] = None) -> None:
    """At most one between-nightly model update per hour, however many observations arrive."""
    from app.ingestion import queue

    hour = (now or datetime.now().astimezone()).strftime("%Y%m%d%H")
    await queue.enqueue(pool, MODEL_UPDATE_JOB, {}, idempotency_key=f"model_update:{hour}", scope_type="global",
                        visibility="public", max_attempts=2, offload=False)
