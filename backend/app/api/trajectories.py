"""
Product-ready API surface for trajectory ingestion (trajectory-
ingestion-hardening task, Sec 19). Reuses this codebase's existing
router/auth convention (`Depends(require_admin_api_key)`, same as
`admin.py`) rather than a separate administration framework, and calls
existing service functions unchanged -- this file is wiring, not new
extraction/ingestion logic.

ingest -> inspect trajectory -> inspect normalized events -> run
semantic extraction -> inspect extraction -> list extracted Goals/
Procedures/Implementations/Claims -> inspect provenance -> re-extract
with a newer model/version.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.admin import get_pool
from app.api.deps import require_admin_api_key
from app.services.extraction_routing import choose_extraction_model
from app.services.ingestion_jobs import _extraction_client
from app.services.ingestion_sources.dispatch import ingest_openhands_trajectories
from app.services.procedure_extraction.schema import ExtractionTransientFailure
from app.services.trajectory_semantics import extract_trajectory_semantics

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/trajectories", tags=["trajectories"],
    dependencies=[Depends(require_admin_api_key)],
)


# ------------------------------------------------------------------ ingest

class TrajectoryIngestRequest(BaseModel):
    source_type: str
    root: str
    owner_id: Optional[str] = None
    visibility: str = "public"
    scope_type: str = "global"
    scope_entity_id: Optional[str] = None


class TrajectoryIngestResponse(BaseModel):
    trajectories_ingested: int
    events_normalized: int
    objects_quarantined: int
    results: list[dict]


@router.post("/ingest", response_model=TrajectoryIngestResponse)
async def ingest_trajectory(
    body: TrajectoryIngestRequest, pool=Depends(get_pool),
) -> TrajectoryIngestResponse:
    """Walks `root` for trajectory exports matching `source_type` and
    writes them through the shared trace_events/agent_traces/episodes
    pipeline (`ingestion_sources.dispatch`). Currently routes
    `openhands_trajectory_dir`; adding a future source_type means one
    more branch here, never a second write path."""
    if body.source_type != "openhands_trajectory_dir":
        raise HTTPException(
            status_code=400,
            detail=f"unsupported source_type {body.source_type!r} (supported: openhands_trajectory_dir)",
        )
    result = await ingest_openhands_trajectories(
        pool, body.root,
        owner_id=body.owner_id, visibility=body.visibility,
        scope_type=body.scope_type, scope_entity_id=body.scope_entity_id,
    )
    return TrajectoryIngestResponse(**result)


# ----------------------------------------------------------------- observability
#
# Registered BEFORE the "/{trace_id}" catch-all below: FastAPI/Starlette
# match routes in registration order, and a literal path ("/stats")
# registered AFTER a single-segment path-parameter route ("/{trace_id}")
# would never be reached -- "stats" would just be swallowed as a
# trace_id. (Found by this file's own offline test, not by inspection --
# see test_trajectories_api_offline.py::test_stats_endpoint_returns_real_
# aggregate_shape.)

@router.get("/stats")
async def trajectory_ingestion_stats(pool=Depends(get_pool)) -> dict:
    """Rollup metrics (task Sec 23): trajectories/events ingested,
    extraction attempt/success/failure counts, objects produced/
    deduplicated/quarantined, average trajectory length, escalation rate.
    Every number is a real aggregate query, never an estimate."""
    trajectories_ingested = await pool.fetchval(
        "SELECT count(*) FROM agent_traces WHERE provider IS NOT NULL",
    )
    events_normalized = await pool.fetchval("SELECT count(*) FROM trace_events")
    avg_trajectory_length = await pool.fetchval(
        "SELECT avg(cnt) FROM (SELECT count(*) AS cnt FROM trace_events GROUP BY trace_id) t",
    )
    extraction_rows = await pool.fetch(
        "SELECT status, count(*) AS n FROM trajectory_extractions GROUP BY status",
    )
    extraction_counts = {row["status"]: row["n"] for row in extraction_rows}
    escalated_count = await pool.fetchval(
        "SELECT count(*) FROM trajectory_extractions WHERE escalated",
    )
    total_extractions = await pool.fetchval("SELECT count(*) FROM trajectory_extractions")
    escalation_rate = (escalated_count / total_extractions) if total_extractions else 0.0
    object_rows = await pool.fetch(
        "SELECT object_type, count(*) AS n FROM trajectory_extraction_objects GROUP BY object_type",
    )
    objects_by_type = {row["object_type"]: row["n"] for row in object_rows}
    objects_quarantined = await pool.fetchval("SELECT count(*) FROM quarantined_records")
    avg_extraction_cost = await pool.fetchval(
        "SELECT avg(cost_usd) FROM agent_traces WHERE cost_usd IS NOT NULL",
    )

    return {
        "trajectories_ingested": trajectories_ingested or 0,
        "events_normalized": events_normalized or 0,
        "avg_trajectory_length": float(avg_trajectory_length) if avg_trajectory_length else None,
        "semantic_extractions_attempted": total_extractions or 0,
        "semantic_extractions_succeeded": extraction_counts.get("completed", 0),
        "semantic_extractions_failed": extraction_counts.get("failed", 0),
        "semantic_extractions_pending": extraction_counts.get("pending", 0),
        "escalation_rate": escalation_rate,
        "goals_extracted": objects_by_type.get("goal", 0),
        "claims_extracted": objects_by_type.get("claim", 0),
        "procedures_extracted": objects_by_type.get("procedure", 0),
        "implementations_extracted": objects_by_type.get("implementation", 0),
        "objects_quarantined": objects_quarantined or 0,
        "avg_extraction_cost_usd": float(avg_extraction_cost) if avg_extraction_cost else None,
    }


# ------------------------------------------------------------- inspection

@router.get("/{trace_id}")
async def inspect_trajectory(trace_id: str, pool=Depends(get_pool)) -> dict:
    row = await pool.fetchrow(
        "SELECT trace_id, session_id, provider, provider_version, model, "
        "       token_usage, cost_usd, outcome, started_at, ended_at, metadata "
        "FROM agent_traces WHERE trace_id = $1",
        trace_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="trajectory not found")
    return dict(row)


@router.get("/{trace_id}/events")
async def list_trajectory_events(trace_id: str, limit: int = 500, pool=Depends(get_pool)) -> dict:
    """Real events, not a summary -- proves nothing was silently
    discarded (task Sec 20's 'raw event count is preserved' acceptance
    check reads directly off this)."""
    rows = await pool.fetch(
        "SELECT id, sequence, event_type, canonical_event_type, tool_name, "
        "       tool_input, tool_output, success, \"timestamp\" "
        "FROM trace_events WHERE trace_id = $1 ORDER BY sequence ASC LIMIT $2",
        trace_id, limit,
    )
    count = await pool.fetchval("SELECT count(*) FROM trace_events WHERE trace_id = $1", trace_id)
    return {"trace_id": trace_id, "total_events": count, "events": [dict(r) for r in rows]}


@router.get("/{trace_id}/episodes")
async def list_trajectory_episodes(trace_id: str, pool=Depends(get_pool)) -> dict:
    session_id = await pool.fetchval(
        "SELECT session_id FROM agent_traces WHERE trace_id = $1", trace_id,
    )
    if session_id is None:
        raise HTTPException(status_code=404, detail="trajectory not found")
    rows = await pool.fetch(
        "SELECT id, start_ts, end_ts, parent_episode_id, metadata "
        "FROM episodes WHERE session_id = $1 AND t_invalid IS NULL ORDER BY start_ts ASC",
        session_id,
    )
    return {"trace_id": trace_id, "session_id": session_id, "episodes": [dict(r) for r in rows]}


# ------------------------------------------------------------- extraction

class RunExtractionRequest(BaseModel):
    episode_id: UUID
    force_strong_model: bool = False


class ExtractionResponse(BaseModel):
    extraction_id: str
    episode_id: str
    outcome: Optional[str]
    goals: int
    claims: int
    procedures: int
    implementations: int
    uncertainties: list[str]
    model: str
    escalated: bool
    escalation_reason: Optional[str]


@router.post("/{trace_id}/extract", response_model=ExtractionResponse)
async def run_semantic_extraction(
    trace_id: str, body: RunExtractionRequest, pool=Depends(get_pool),
) -> ExtractionResponse:
    """One structured LLM semantic-extraction pass over one episode
    (trajectory_semantics.py). `trace_id` is accepted for URL symmetry
    with the rest of this router and is NOT re-validated against
    `episode_id` beyond what `extract_trajectory_semantics` itself does
    (an episode row that does not exist raises there)."""
    client = _extraction_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="no extraction LLM client configured (GENERAL_COMPUTE_API_KEY unset)",
        )
    start_ts, end_ts = await _episode_ts_bounds(pool, body.episode_id)
    event_count = await pool.fetchval(
        "SELECT count(*) FROM trace_events te JOIN episodes ep ON ep.session_id = te.session_id "
        "WHERE ep.id = $1::uuid "
        "AND ($2::timestamptz IS NULL OR te.\"timestamp\" >= $2) "
        "AND ($3::timestamptz IS NULL OR te.\"timestamp\" <= $3)",
        str(body.episode_id), start_ts, end_ts,
    )
    choice = choose_extraction_model(
        event_count=event_count or 0, malformed_prior_attempt=body.force_strong_model,
    )
    try:
        result = await extract_trajectory_semantics(
            pool, str(body.episode_id), client=client,
            model=choice.model, escalated=choice.escalated, escalation_reason=choice.escalation_reason,
        )
    except ExtractionTransientFailure as exc:
        raise HTTPException(status_code=502, detail=f"semantic extraction failed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ExtractionResponse(
        model=choice.model, escalated=choice.escalated, escalation_reason=choice.escalation_reason,
        **result,
    )


async def _episode_ts_bounds(pool, episode_id: UUID) -> tuple[Any, Any]:
    row = await pool.fetchrow(
        "SELECT start_ts, end_ts FROM episodes WHERE id = $1::uuid", str(episode_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="episode not found")
    return row["start_ts"], row["end_ts"]


@router.get("/extractions/{extraction_id}")
async def inspect_extraction(extraction_id: str, pool=Depends(get_pool)) -> dict:
    row = await pool.fetchrow(
        "SELECT id, episode_id, ingestion_context_id, extractor_id, model, model_version, "
        "       prompt_version, schema_version, input_hash, output_hash, status, "
        "       confidence_summary, escalated, escalation_reason, error, "
        "       created_at, completed_at "
        "FROM trajectory_extractions WHERE id = $1::uuid",
        extraction_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="extraction not found")
    return dict(row)


@router.get("/extractions/{extraction_id}/objects")
async def list_extraction_objects(
    extraction_id: str, object_type: Optional[str] = None, pool=Depends(get_pool),
) -> dict:
    """Lists exactly what one extraction run produced -- each row's
    `event_refs` is the citation proof required by task Sec 6 ('every
    extracted object MUST cite the exact source event(s)')."""
    if object_type is not None:
        rows = await pool.fetch(
            "SELECT id, object_type, object_id, event_refs, epistemic_status, confidence, created_at "
            "FROM trajectory_extraction_objects WHERE extraction_id = $1::uuid AND object_type = $2",
            extraction_id, object_type,
        )
    else:
        rows = await pool.fetch(
            "SELECT id, object_type, object_id, event_refs, epistemic_status, confidence, created_at "
            "FROM trajectory_extraction_objects WHERE extraction_id = $1::uuid",
            extraction_id,
        )
    return {"extraction_id": extraction_id, "objects": [dict(r) for r in rows]}


@router.get("/provenance/{object_type}/{object_id}")
async def inspect_provenance(object_type: str, object_id: str, pool=Depends(get_pool)) -> dict:
    """Walks trajectory_extraction_objects -> trajectory_extractions ->
    ingestion_contexts -> trace_events, the full lineage chain task
    Sec 12 requires every derived object be able to answer."""
    if object_type not in ("goal", "claim", "procedure", "implementation"):
        raise HTTPException(status_code=400, detail=f"unknown object_type {object_type!r}")
    link_rows = await pool.fetch(
        "SELECT teo.id AS link_id, teo.extraction_id, teo.event_refs, teo.epistemic_status, "
        "       teo.confidence, te.extractor_id, te.model, te.model_version, te.prompt_version, "
        "       te.schema_version, te.ingestion_context_id, te.created_at AS extracted_at "
        "FROM trajectory_extraction_objects teo "
        "JOIN trajectory_extractions te ON te.id = teo.extraction_id "
        "WHERE teo.object_type = $1 AND teo.object_id = $2::uuid",
        object_type, object_id,
    )
    if not link_rows:
        raise HTTPException(status_code=404, detail="no extraction lineage found for this object")
    lineage = []
    for row in link_rows:
        ingestion_context = None
        if row["ingestion_context_id"] is not None:
            ctx_row = await pool.fetchrow(
                "SELECT id, source_type, source_uri, source_hash, extractor_id, extractor_version, "
                "       actor_id, status FROM ingestion_contexts WHERE id = $1::uuid",
                row["ingestion_context_id"],
            )
            ingestion_context = dict(ctx_row) if ctx_row else None
        event_rows = await pool.fetch(
            "SELECT id, sequence, event_type, canonical_event_type, tool_name, \"timestamp\" "
            "FROM trace_events WHERE id = ANY($1::uuid[]) ORDER BY sequence ASC",
            list(row["event_refs"]),
        )
        lineage.append({
            "extraction_id": str(row["extraction_id"]),
            "epistemic_status": row["epistemic_status"],
            "confidence": row["confidence"],
            "extractor_id": row["extractor_id"],
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "schema_version": row["schema_version"],
            "extracted_at": row["extracted_at"],
            "ingestion_context": ingestion_context,
            "source_events": [dict(r) for r in event_rows],
        })
    return {"object_type": object_type, "object_id": object_id, "lineage": lineage}


@router.post("/extractions/{extraction_id}/reextract", response_model=ExtractionResponse)
async def reextract_trajectory(extraction_id: str, pool=Depends(get_pool)) -> ExtractionResponse:
    """Re-runs semantic extraction for the SAME episode a prior extraction
    covered, always escalated to the strong model (a re-extraction is, by
    definition, a deliberate request for a better pass) -- produces a NEW
    `trajectory_extractions` row (v2, v3, ...) that coexists with every
    prior one; nothing is overwritten or deleted (task Sec 13)."""
    prior = await pool.fetchrow(
        "SELECT episode_id FROM trajectory_extractions WHERE id = $1::uuid", extraction_id,
    )
    if prior is None:
        raise HTTPException(status_code=404, detail="extraction not found")
    client = _extraction_client()
    if client is None:
        raise HTTPException(status_code=503, detail="no extraction LLM client configured")
    # A re-extraction is, by construction, a deliberate request for a
    # better pass -- always the strong tier, never re-derived from event
    # count/outcome heuristics.
    strong_choice = choose_extraction_model(event_count=0, malformed_prior_attempt=True)
    try:
        result = await extract_trajectory_semantics(
            pool, str(prior["episode_id"]), client=client,
            model=strong_choice.model, escalated=True,
            escalation_reason="explicit_reextraction_request",
        )
    except ExtractionTransientFailure as exc:
        raise HTTPException(status_code=502, detail=f"re-extraction failed: {exc}") from exc
    return ExtractionResponse(
        model=strong_choice.model, escalated=True,
        escalation_reason="explicit_reextraction_request", **result,
    )


