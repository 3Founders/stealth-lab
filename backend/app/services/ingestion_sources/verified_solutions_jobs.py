"""
The job type that turns a verified-solution corpus into Procedures.

`HANDLERS` is merged into `ingestion_jobs.JOB_HANDLERS` by one line there,
mirroring how `app.services.semantic.jobs` is registered. Keeping the handler
here means the shared file gains a registration and nothing else.

WHY THE PAYLOAD CARRIES THE DOCUMENT
`VerifiedSolutionSource.fetch()` re-streams the corpus to resolve one ref, so
a per-instance job that called it would re-read a 32k-row dataset per job --
quadratic in the corpus size. The enqueue side therefore runs the admission
gates once (all of which are pure and free), and hands the already-gated
document to the handler. `enqueue`'s existing `offload=True` keeps a large
payload off the row.

WHY THIS IS THE ONLY PAID STEP
Every gate in `verified_solutions_hf` is pure and free. The only spend is
`compile_skill_artifact`, which per founder directive 2026-09-15 refuses the
artifact outright when no General Compute LLM client is configured. So
"nothing configured" degrades to a loud no-op, never to a deterministic
silent capture.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import asyncpg

from app.services.ingestion_sources import verified_solutions_hf as vs
from app.services.repo_license_policy import classify_spdx

log = logging.getLogger(__name__)

JOB_TYPE = "ingest_verified_solution"


async def handle_ingest_verified_solution(pool: asyncpg.Pool, payload: dict) -> None:
    """Compile exactly one pre-gated verified solution into a Procedure.

    Mirrors `handle_ingest_document`: the caller owns its own transaction,
    the job's own identity comes from the trusted stamp, and the refusal when
    no LLM client is configured is logged rather than swallowed.
    """
    from app.config import settings
    from app.services.embeddings import Embedder
    from app.services.ingestion_jobs import _general_compute_client, _tel
    from app.services.skill_ingestion import compile_skill_artifact

    content = payload.get("content")
    if not content:
        raise ValueError(f"{JOB_TYPE}: payload carries no gated document")

    instance_id = str(payload.get("instance_id") or "")
    repo = str(payload.get("repo") or "")
    base_commit = str(payload.get("base_commit") or "")
    if not instance_id or not base_commit:
        raise ValueError(f"{JOB_TYPE}: payload missing instance_id/base_commit")

    from app.services.ingestion_sources.base import SourceArtifact, compute_content_hash
    from app.services.verified_solutions import SOURCE_TYPE

    artifact = SourceArtifact(
        source_type=SOURCE_TYPE,
        uri=payload.get("uri") or "",
        content=content,
        content_hash=compute_content_hash(content),
        repository=repo or None,
        path=f"instances/{instance_id}",
        commit=base_commit,
        source_id=instance_id,
        license_metadata={
            "license": payload.get("license_raw"),
            "spdx_id": payload.get("license_spdx"),
        },
    )

    client = await asyncio.to_thread(_general_compute_client)
    if client is None:
        log.warning(
            "%s: no LLM client configured (GENERAL_COMPUTE_API_KEY/"
            "GENERAL_COMPUTE_JUDGE_MODEL) -- this item will be refused, "
            "not deterministically captured",
            JOB_TYPE,
        )

    judge_model = settings.general_compute_judge_model or "gemma-4-31B-it"
    with _tel.span(
        "ingestion.compile", kind="CHAIN",
        on_error=_tel.FailureCode.INGESTION_ERROR, items_attempted=1,
        source_type=artifact.source_type,
    ) as sp:
        outcome = await compile_skill_artifact(
            pool, artifact, embedder=Embedder(rate_limit_pool=pool),
            created_by="verified_solutions_worker",
            client=client,
            admission_llm_model=judge_model,
            extraction_llm_model=judge_model,
            fallback_extraction_llm_model=settings.general_compute_fallback_model or None,
            claim_extraction_llm_model=judge_model,
            identity_job_id=_trusted_identity_job_id(payload),
        )
        status = getattr(outcome, "status", None)
        _tel.set_attrs(
            sp, ingest_status=status,
            items_accepted=int(status in ("captured", "new_version")),
            items_duplicate=int(status in ("duplicate", "unchanged")),
            items_rejected=int(status == "rejected"),
        )


def _trusted_identity_job_id(payload: dict) -> Optional[str]:
    """Read the identity the CONSUMER stamped, never one a payload asserts.

    Same rule as every other handler: an untrusted payload must not be able
    to claim the job's provenance.
    """
    from app.services.ingestion_jobs import _trusted_identity_job_id as _read

    return _read(payload)


HANDLERS: dict[str, Any] = {JOB_TYPE: handle_ingest_verified_solution}


async def enqueue_verified_solution_jobs(
    pool: asyncpg.Pool,
    *,
    source_key: str,
    target: int,
    design_paths,
    include_repos: bool = True,
    scope_type: str = "global",
    visibility: str = "public",
    owner_id: Optional[str] = None,
    max_attempts: int = 3,
) -> dict[str, int]:
    """Gate a corpus for free, then enqueue one job per admissible item.

    All admission decisions happen here, before any spend: license, held-out
    exclusion, dedup and the structural quality gates. A corpus that would be
    entirely rejected (SWE-Gym has no license field) enqueues nothing and
    costs nothing, which is the behaviour worth preserving.
    """
    from app.ingestion.queue import enqueue

    if source_key not in vs.SOURCES:
        raise KeyError(f"unknown verified-solution source: {source_key!r}")

    held = vs.load_held_out(design_paths, include_repos=include_repos)
    counters = vs.GateCounters()
    source = vs.VerifiedSolutionSource(
        source_key, held_out=held, license_classify=classify_spdx,
        counters=counters,
    )

    enqueued = 0
    existing = 0
    for row, content in source.iter_admissible():
        if enqueued >= target:
            break
        job_id, created = await enqueue(
            pool, JOB_TYPE,
            {
                "source_key": source_key,
                "instance_id": row.instance_id,
                "repo": row.repo,
                "base_commit": row.base_commit,
                "uri": f"hf://{row.dataset_repo}@{row.revision}/{row.split}/{row.instance_id}",
                "content": content,
                "license_raw": row.license_raw,
                "license_spdx": row.license_spdx,
                "language": row.language,
            },
            # Instance + revision + document hash, so a re-run over the same
            # pinned revision is a no-op and a new revision enqueues anew.
            idempotency_key=f"{source_key}:{row.instance_id}:{row.base_commit[:12]}",
            source_id=row.instance_id,
            scope_type=scope_type, visibility=visibility, owner_id=owner_id,
            max_attempts=max_attempts,
        )
        if created:
            enqueued += 1
        else:
            existing += 1

    log.info(
        "%s: enqueued=%d already_present=%d rows_seen=%d rejected=%s",
        source_key, enqueued, existing, counters.rows_seen, counters.reasons,
    )
    return {
        "enqueued": enqueued,
        "already_present": existing,
        "rows_seen": counters.rows_seen,
        **{f"rejected_{k}": v for k, v in counters.reasons.items()},
    }
