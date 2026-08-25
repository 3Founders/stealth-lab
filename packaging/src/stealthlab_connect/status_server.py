"""
P2 minimal status surface (board Lane SHIP item 2): a tiny read-only
FastAPI app serving one HTML/JS page that lists episodes -> claims ->
procedures with capability scores and evidence-trail links.

READ-ONLY contract, in both senses:

  - No backend file is edited. Everything backend-side is IMPORTED and
    used as its owners shipped it: the access predicate builders
    (services/access.py), the identity middleware and boot-posture guard
    (services/authn.py), the scope dependency (api/deps.py::get_scope),
    the pool factory (db/session.py), settings (config.py), and -- for
    capability scores -- the REAL D1-banded engine
    (services/procedure_extraction/capability.py). This module contains
    zero re-implementations of governed logic.

  - Every SQL statement below is a SELECT. Nothing here writes. The
    queries are scoped through access.py builders -- never hand-written
    filter text. The SPLIT follows each table's real schema: only the
    migration-01/02 core tables (knowledge_nodes, task_nodes, episodes)
    are tenant-bearing, so they get scope_predicates() (visibility AND
    tenancy); procedures/evidence carry visibility + owner_id only
    (migrations 18/24), so they get visibility_predicate() alone --
    passing them a tenant predicate would emit SQL for a column that
    does not exist. observations/episode_links/claim_sources have no
    tenant_id either; episode-link existence leaks are avoided by
    resolving target names through scope-filtered node lookups.

Capability scores are computed by feeding each procedure's recorded
outcome evidence (supports-direction execution_result/reproduction rows,
exactly procedure_evidence_stats' population) through
capability.compute_capability(). Verification-plan and completed-review
gates are reported honestly as False until anything stores them -- no
invented inputs, so trust tiers above "reproduced" cannot appear from
statistics alone (spec §16's own fail-closed posture).

Auth surface: exactly what authn.py provides. install_actor_middleware +
assert_boot_posture + deps.get_scope, same wiring as app/main.py. No new
tokens, no new endpoints that accept writes, nothing else.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

# --- read-only imports from the backend checkout ---------------------------
from app.api.deps import get_scope, require_trustworthy_identity
from app.config import settings
from app.db.session import create_pool
from app.services.access import (
    AccessScope,
    TenantScope,
    next_param_index,
    scope_predicates,
    visibility_predicate,
)
from app.services.authn import assert_boot_posture, install_actor_middleware
from app.services.procedure_extraction.capability import (
    CapabilityScope,
    OutcomeRecord,
    compute_capability,
)

STATUS_PAGE = Path(__file__).with_name("status_page.html")

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

API_BASE_ENV_VAR = "STEALTHLAB_API_BASE"
DEFAULT_API_BASE = "http://127.0.0.1:8000"

TRAIL_TARGET_TYPES = ("claim", "procedure")

# capability.py's OutcomeRecord needs an environment string per outcome;
# evidence rows may have NULL context_key. A named constant, not "".
UNRECORDED_ENVIRONMENT = "unrecorded"

CAPABILITY_EVIDENCE_TYPES = ("execution_result", "reproduction")
CAPABILITY_DIRECTION = "supports"


def api_base() -> str:
    return os.environ.get(API_BASE_ENV_VAR, DEFAULT_API_BASE)


# ---------------------------------------------------------------------------
# Pure helpers (offline-provable).
# ---------------------------------------------------------------------------


def jsonable(value: Any) -> Any:
    """UUID/datetime/Decimal -> JSON-safe primitives; recurses containers."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return value


def outcome_records_from_evidence(rows: Sequence[Mapping[str, Any]]) -> list[OutcomeRecord]:
    """
    Evidence rows -> capability engine inputs. Population mirrors
    db/24's procedure_evidence_stats view exactly: t_invalid-filtered
    (done in SQL), supports direction, execution_result/reproduction
    kinds. Repeated defensively here so the pure function stays correct
    even if a caller hands it unfiltered rows.
    """
    records: list[OutcomeRecord] = []
    for row in rows:
        if row["direction"] != CAPABILITY_DIRECTION:
            continue
        if row["evidence_type"] not in CAPABILITY_EVIDENCE_TYPES:
            continue
        records.append(
            OutcomeRecord(
                success=row["outcome_status"] == "success",
                environment=row["context_key"] or UNRECORDED_ENVIRONMENT,
                independence_group=row["independence_group"],
            )
        )
    return records


def capability_from_evidence_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """
    The capability score card for one procedure version, computed by the
    real engine over that procedure's outcome stream. Scope fields label
    the aggregation honestly: this view aggregates ACROSS contexts (the
    same granularity db/24's stats view chose), so the scope names the
    aggregate rather than pretending to be one conditioned context.
    """
    record = compute_capability(
        outcome_records_from_evidence(rows),
        CapabilityScope(
            task="procedure_reuse",
            state_signature="aggregated_all_contexts",
            environment="aggregated_all_environments",
            input_signature="all_recorded_inputs",
            evaluation_criterion="evidence.outcome_status == 'success'",
        ),
        # Nothing stores verification plans or completed reviews yet;
        # reporting them True would manufacture trust tiers.
        verification_plan_satisfied=False,
        completed_review=False,
    )
    return {
        "level": record.level,
        "level_label": record.level_label,
        "p_estimate": record.p_estimate,
        "p_lower": record.p_lower,
        "p_upper": record.p_upper,
        "routing": record.routing.value,
        "evidence_count": record.evidence_count,
        "success_count": record.success_count,
        "independent_groups": record.independent_groups,
        "environments_held": record.environments_held,
        "gates_reported": {
            "verification_plan_satisfied": False,
            "completed_review": False,
        },
    }


# ---------------------------------------------------------------------------
# SQL. SELECT-only; every list query carries the builder-produced
# scope fragment and a parameterized LIMIT.
# ---------------------------------------------------------------------------


def _procedures_sql(limit_idx: int) -> str:
    return f"""
SELECT p.id, p.procedure_id, p.version, p.name, p.goal,
       p.verification_state::text AS verification_state,
       p.staleness::text AS staleness,
       p.availability::text AS availability,
       p.t_valid, p.created_at
FROM procedures p
WHERE p.t_invalid IS NULL AND ({{preds}})
ORDER BY p.created_at DESC
LIMIT ${limit_idx}
"""


def _procedure_evidence_sql(ids_idx: int) -> str:
    return f"""
SELECT e.id, e.target_id, e.target_version,
       e.evidence_type::text AS evidence_type,
       e.direction, e.outcome_status, e.strength_score, e.strength_method,
       e.independence_group, e.context_key, e.failure_class, e.t_valid
FROM evidence e
WHERE e.t_invalid IS NULL AND ({{preds}})
  AND e.target_type = 'procedure'
  AND e.direction = 'supports'
  AND e.evidence_type IN ('execution_result', 'reproduction')
  AND e.target_id = ANY(${ids_idx}::uuid[])
ORDER BY e.t_valid
"""


def _claims_sql(limit_idx: int) -> str:
    return f"""
SELECT k.id, k.name,
       k.properties->>'statement' AS statement,
       k.properties->>'truth_state' AS truth_state,
       k.properties->>'epistemic_status' AS epistemic_status,
       k.properties->>'extraction_version' AS extraction_version,
       k.properties->>'subject' AS subject,
       k.properties->>'predicate' AS predicate,
       k.properties->>'object' AS object,
       k.t_valid
FROM knowledge_nodes k
WHERE k.node_type = 'claim' AND k.t_invalid IS NULL AND ({{preds}})
ORDER BY k.t_valid DESC
LIMIT ${limit_idx}
"""


def _claim_sources_sql(vis_preds: str) -> str:
    # claim_sources is a bare join table (no visibility columns); the
    # observations side carries the visibility axis. It has no tenant_id
    # column (migration 14), so only that axis applies here.
    return f"""
SELECT cs.claim_id, o.id AS observation_id, o.observation_type, o.label,
       o.extractor_kind, o.extractor_name, o.code_version, o.model_id
FROM claim_sources cs
JOIN observations o ON o.id = cs.observation_id
WHERE cs.claim_id = ANY($1::uuid[]) AND ({vis_preds})
ORDER BY o.extracted_at
"""


def _claim_evidence_counts_sql(ids_idx: int) -> str:
    return f"""
SELECT e.target_id, count(*) AS n
FROM evidence e
WHERE e.t_invalid IS NULL AND ({{preds}})
  AND e.target_type = 'claim'
  AND e.target_id = ANY(${ids_idx}::uuid[])
GROUP BY e.target_id
"""


def _episodes_sql(limit_idx: int) -> str:
    return f"""
SELECT ep.id, ep.episode_type, ep.session_id, ep.project_id,
       ep.parent_episode_id, ep.start_ts, ep.end_ts, ep.timestamp,
       ep.metadata
FROM episodes ep
WHERE ep.t_invalid IS NULL AND ({{preds}})
ORDER BY COALESCE(ep.start_ts, ep.timestamp) DESC
LIMIT ${limit_idx}
"""


def _episode_links_sql(ids_idx: int) -> str:
    return f"""
SELECT el.episode_id, el.target_id, el.target_table
FROM episode_links el
WHERE el.episode_id = ANY(${ids_idx}::uuid[])
"""


def _node_names_sql(table: str, ids_idx: int) -> str:
    return f"""
SELECT n.id, n.name
FROM {table} n
WHERE n.t_invalid IS NULL AND ({{preds}})
  AND n.id = ANY(${ids_idx}::uuid[])
"""


def _evidence_trail_sql(vis_start_idx: int) -> str:
    return f"""
SELECT e.id, e.evidence_type::text AS evidence_type, e.target_type,
       e.target_id, e.target_version, e.direction, e.outcome_status,
       e.success_criteria, e.strength_score, e.strength_method,
       e.independence_group, e.context_key, e.failure_class,
       e.extractor_version, e.created_by, e.scope_type, e.scope_entity_id,
       e.t_valid, e.t_invalid
FROM evidence e
WHERE e.target_type = $1 AND e.target_id = $2 AND e.t_invalid IS NULL
  AND ({{preds}})
ORDER BY e.t_valid DESC
"""


# ---------------------------------------------------------------------------
# App factory.
# ---------------------------------------------------------------------------


def _visibility_call(
    scope: AccessScope, *, alias: str, param_index: int
) -> tuple[str, list, int]:
    """
    Visibility-axis-only fragment for tables WITHOUT a tenant_id column
    (procedures, evidence: migrations 18/24 give them visibility +
    owner_id + scope pair, no tenancy). Same builder discipline as
    scope_predicates -- the fragment text comes from access.py, never
    from this module.
    """
    vis_sql, vis_params = visibility_predicate(scope, alias=alias, param_index=param_index)
    return f"({vis_sql})", vis_params, next_param_index(scope, param_index)


async def _run_boot_guards() -> None:
    """The exact guards app/main.py runs at startup -- same surface, no
    new auth posture. Refuses half-enabled identity configurations."""
    require_trustworthy_identity()
    assert_boot_posture(
        private_visibility_enabled=settings.private_visibility_enabled,
        real_auth_enabled=settings.real_auth_enabled,
        oidc_configured_=settings.oidc_issuer is not None
        and settings.oidc_audience is not None,
        multi_user_exposure_enabled=settings.multi_user_exposure_enabled,
    )


def create_status_app(
    pool_factory: Optional[Callable[[], Any]] = None,
) -> FastAPI:
    """
    Build the status app. `pool_factory` defaults to the backend's own
    create_pool; tests inject a fake pool instead (no database).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await _run_boot_guards()
        factory = pool_factory or create_pool
        app.state.pool = await factory()
        try:
            yield
        finally:
            closer = getattr(app.state.pool, "close", None)
            if closer is not None:
                result = closer()
                if hasattr(result, "__await__"):
                    await result

    app = FastAPI(title="StealthLab Status", version="0.1.0", lifespan=lifespan)

    # Same identity gate as app/main.py: pass-through while OIDC is
    # unconfigured, validated-actor propagation when it is.
    install_actor_middleware(app, settings)

    async def get_tenant_scope() -> TenantScope:
        # Today's explicit posture: the seeded commons organization.
        # Deliberate call, not a default -- H1's builder has no implicit
        # tenant anywhere.
        return TenantScope.commons()

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATUS_PAGE, media_type="text/html")

    @app.get("/api/meta")
    async def meta(
        scope: AccessScope = Depends(get_scope),
        tenant: TenantScope = Depends(get_tenant_scope),
    ) -> dict:
        return {
            "api_base": api_base(),
            "viewer_id": scope.viewer_id,
            "tenant_id": tenant.tenant_id,
            "posture": {
                "private_visibility_enabled": settings.private_visibility_enabled,
                "real_auth_enabled": settings.real_auth_enabled,
                "multi_user_exposure_enabled": settings.multi_user_exposure_enabled,
                "oidc_configured": settings.oidc_issuer is not None
                and settings.oidc_audience is not None,
            },
        }

    @app.get("/api/overview")
    async def overview(
        request: Request,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        scope: AccessScope = Depends(get_scope),
        tenant: TenantScope = Depends(get_tenant_scope),
    ) -> dict:
        pool = request.app.state.pool

        # -- procedures + capability --------------------------------------
        # procedures is NOT tenant-bearing (migration 18): visibility axis only.
        vis_preds, vis_params, next_idx = _visibility_call(scope, alias="p", param_index=1)
        proc_rows = await pool.fetch(
            _procedures_sql(next_idx).format(preds=vis_preds), *vis_params, limit
        )
        proc_ids = [r["id"] for r in proc_rows]

        evidence_by_proc: dict[Any, list[dict]] = {}
        outcome_evidence_available = True
        if proc_ids:
            vis_preds, vis_params, next_idx = _visibility_call(scope, alias="e", param_index=1)
            try:
                ev_rows = await pool.fetch(
                    _procedure_evidence_sql(next_idx).format(preds=vis_preds),
                    *vis_params,
                    proc_ids,
                )
            except asyncpg.UndefinedTableError:
                # Pre-migration-24 database (the documented shared-instance
                # drift): no evidence table exists yet. The page degrades
                # with a named note instead of dying -- capability scores
                # honestly read level 0 "unknown" because there is truly
                # no recorded outcome stream. Any OTHER database error
                # still raises loudly.
                ev_rows = []
                outcome_evidence_available = False
            for row in ev_rows:
                evidence_by_proc.setdefault(row["target_id"], []).append(dict(row))

        procedures = []
        for row in proc_rows:
            capabilities = capability_from_evidence_rows(evidence_by_proc.get(row["id"], []))
            procedures.append(
                {
                    "id": str(row["id"]),
                    "procedure_id": str(row["procedure_id"]),
                    "version": row["version"],
                    "name": row["name"],
                    "goal": row["goal"],
                    "verification_state": row["verification_state"],
                    "staleness": row["staleness"],
                    "availability": row["availability"],
                    "t_valid": jsonable(row["t_valid"]),
                    "created_at": jsonable(row["created_at"]),
                    "capability": capabilities,
                }
            )

        # -- claims + provenance sources -----------------------------------
        preds, params, next_idx = scope_predicates(scope, tenant, alias="k", param_index=1)
        claim_rows = await pool.fetch(_claims_sql(next_idx).format(preds=preds), *params, limit)
        claim_ids = [r["id"] for r in claim_rows]

        sources_by_claim: dict[Any, list[dict]] = {}
        counts_by_claim: dict[Any, int] = {}

        if claim_ids:
            sources_sql, sources_params = _claim_sources_call(scope, claim_ids)
            try:
                sources_rows = await pool.fetch(sources_sql, *sources_params)
            except asyncpg.UndefinedTableError:
                # Pre-migration-26 database: no claim_sources yet. Claims
                # simply show "no recorded observation sources".
                sources_rows = []
            for row in sources_rows:
                sources_by_claim.setdefault(row["claim_id"], []).append(dict(row))

            preds, params, next_idx = _visibility_call(scope, alias="e", param_index=1)
            try:
                count_rows = await pool.fetch(
                    _claim_evidence_counts_sql(next_idx).format(preds=preds), *params, claim_ids
                )
            except asyncpg.UndefinedTableError:
                count_rows = []
            for row in count_rows:
                counts_by_claim[row["target_id"]] = row["n"]

        claims = [
            {
                "id": str(row["id"]),
                "name": row["name"],
                "statement": row["statement"],
                "truth_state": row["truth_state"],
                "epistemic_status": row["epistemic_status"],
                "extraction_version": row["extraction_version"],
                "subject": row["subject"],
                "predicate": row["predicate"],
                "object": row["object"],
                "t_valid": jsonable(row["t_valid"]),
                "evidence_count": int(counts_by_claim.get(row["id"], 0)),
                "sources": [
                    {
                        "observation_id": str(s["observation_id"]),
                        "observation_type": s["observation_type"],
                        "label": s["label"],
                        "extractor_kind": s["extractor_kind"],
                        "extractor_name": s["extractor_name"],
                        "code_version": s["code_version"],
                        "model_id": s["model_id"],
                    }
                    for s in sources_by_claim.get(row["id"], [])
                ],
            }
            for row in claim_rows
        ]

        # -- episodes + their visible link targets --------------------------
        preds, params, next_idx = scope_predicates(scope, tenant, alias="ep", param_index=1)
        episode_rows = await pool.fetch(_episodes_sql(next_idx).format(preds=preds), *params, limit)
        episode_ids = [r["id"] for r in episode_rows]

        name_by_node: dict[Any, str] = {}
        links_by_episode: dict[Any, list[dict]] = {}

        if episode_ids:
            link_rows = await pool.fetch(_episode_links_sql(1), episode_ids)
            targets_by_table: dict[str, list] = {}
            for row in link_rows:
                links_by_episode.setdefault(row["episode_id"], []).append(
                    {"target_id": row["target_id"], "target_table": row["target_table"]}
                )
                targets_by_table.setdefault(row["target_table"], []).append(row["target_id"])
            for table in ("knowledge_nodes", "task_nodes"):
                ids = targets_by_table.get(table, [])
                if not ids:
                    continue
                preds, params, next_idx = scope_predicates(scope, tenant, alias="n", param_index=1)
                name_rows = await pool.fetch(
                    _node_names_sql(table, next_idx).format(preds=preds), *params, ids
                )
                for row in name_rows:
                    name_by_node[row["id"]] = row["name"]

        def _links_for(episode_id) -> list[dict]:
            out = []
            for link in links_by_episode.get(episode_id, []):
                out.append(
                    {
                        "target_id": str(link["target_id"]),
                        "target_table": link["target_table"],
                        "name": name_by_node.get(link["target_id"]),
                    }
                )
            return out

        episodes = [
            {
                "id": str(row["id"]),
                "episode_type": row["episode_type"],
                "session_id": row["session_id"],
                "project_id": row["project_id"],
                "parent_episode_id": jsonable(row["parent_episode_id"]),
                "start_ts": jsonable(row["start_ts"]),
                "end_ts": jsonable(row["end_ts"]),
                "timestamp": jsonable(row["timestamp"]),
                "metadata": jsonable(row["metadata"]),
                "links": _links_for(row["id"]),
            }
            for row in episode_rows
        ]

        return {
            "viewer_id": scope.viewer_id,
            "tenant_id": tenant.tenant_id,
            "counts": {
                "episodes": len(episodes),
                "claims": len(claims),
                "procedures": len(procedures),
            },
            "outcome_evidence_available": outcome_evidence_available,
            "episodes": episodes,
            "claims": claims,
            "procedures": procedures,
        }

    @app.get("/api/evidence/{target_type}/{target_id}")
    async def evidence_trail(
        request: Request,
        target_type: str,
        target_id: UUID,
        scope: AccessScope = Depends(get_scope),
    ) -> dict:
        # No tenant dependency here by schema fact, not by omission:
        # evidence is not tenant-bearing (migration 24).
        if target_type not in TRAIL_TARGET_TYPES:
            raise HTTPException(
                400,
                f"target_type must be one of {TRAIL_TARGET_TYPES} "
                "(implementation trails arrive with their owner)",
            )
        pool = request.app.state.pool
        preds, params, _ = _visibility_call(scope, alias="e", param_index=3)
        try:
            rows = await pool.fetch(
                _evidence_trail_sql(3).format(preds=preds),
                target_type,
                target_id,
                *params,
            )
        except asyncpg.UndefinedTableError:
            rows = []
            note = "evidence table not present on this database (pre-migration-24 instance)"
        else:
            note = None
        return {
            "target_type": target_type,
            "target_id": str(target_id),
            "note": note,
            "evidence": [jsonable(dict(row)) for row in rows],
        }

    return app


def _claim_sources_call(scope: AccessScope, claim_ids: list) -> tuple[str, list]:
    """
    Claim-source lookup with the visibility axis applied on observations.
    Observations carry visibility but NO tenant_id (migration 14), so the
    tenancy axis genuinely does not exist there -- the fragment comes from
    the builder anyway rather than being hand-written SQL.
    """
    vis_sql, vis_params = visibility_predicate(scope, alias="o", param_index=2)
    sql = _claim_sources_sql(f"({vis_sql})")
    return sql, [claim_ids, *vis_params]
