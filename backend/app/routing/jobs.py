"""Worker job handlers: `routing_local_refit` (queued by every recorded observation)
and `routing_refit` (the nightly joint fit; schedule `admin routing-refit` or enqueue
it). Registered from app.ingestion.handlers; they need requirements-routing.txt."""
from __future__ import annotations

from typing import Any, Mapping

from app.routing.store import LOCAL_REFIT_JOB, NIGHTLY_REFIT_JOB


async def handle_local_refit(pool: Any, payload: Mapping[str, Any]) -> dict:
    from app.routing.fit import local_refit

    return await local_refit(pool, str(payload["goal_id"]))


async def handle_nightly_refit(pool: Any, payload: Mapping[str, Any]) -> dict:
    from app.routing.fit import nightly_refit

    return await nightly_refit(pool, method=payload.get("method"))


def register() -> None:
    from app.services.ingestion_jobs import JOB_HANDLERS

    JOB_HANDLERS[LOCAL_REFIT_JOB] = handle_local_refit
    JOB_HANDLERS[NIGHTLY_REFIT_JOB] = handle_nightly_refit
