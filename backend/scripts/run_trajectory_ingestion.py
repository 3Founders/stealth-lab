#!/usr/bin/env python
"""
Headless worker entry point for the trajectory ingestion pipeline
(trajectory-ingestion-hardening task) -- the CLI counterpart to
`ingest_trajectory`/`run_semantic_extraction` (MCP tools) and
`POST /v1/trajectories/{ingest,extract}` (REST). Those two surfaces need
a connected MCP client or a running FastAPI server respectively; this
script needs neither -- it opens its own DB pool from DATABASE_URL and
exits with a real process exit code, which is the actual requirement for
running ingestion as a scheduled worker (a Cloud Run job / cron, or a
GitHub Actions step) rather than something a human or an agent triggers
interactively. Same "headless third surface" reasoning
scripts/run_ingestion.py's own docstring already gives for the Claude
Code collector path -- this is that same pattern for trajectory sources.

Two passes, same reasoning as run_ingestion.py: extraction only has real
work once ingestion has written new episodes in the same run, so one
`--interval` loop keeps both moving without a second scheduler to
configure.

Usage:
    python scripts/run_trajectory_ingestion.py --root ./openhands_exports --once
    python scripts/run_trajectory_ingestion.py --root ./openhands_exports --interval 300 --extract-limit 5
    python scripts/run_trajectory_ingestion.py --root ./openhands_exports --once --owner-id my-org \
        --visibility private --scope-type organization --scope-entity-id my-org

Exit code is non-zero if any pass raises (a real infrastructure failure,
not a per-file quarantine -- a malformed trajectory file is a normal,
successful outcome of a pass, not a script failure).

Windows note (same as every other script in this repo): run with
`python`, not `python3`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app import observability
from app.db.session import create_pool

log = logging.getLogger(__name__)

SUPPORTED_SOURCE_TYPES = ("openhands_trajectory_dir",)


async def _run_once(
    root: Path,
    *,
    source_type: str,
    owner_id: str | None,
    visibility: str,
    scope_type: str,
    scope_entity_id: str | None,
    extract_limit: int,
) -> dict:
    from app.services.extraction_routing import choose_extraction_model
    from app.services.ingestion_jobs import _extraction_client
    from app.services.ingestion_sources.dispatch import ingest_openhands_trajectories
    from app.services.procedure_extraction.schema import ExtractionTransientFailure
    from app.services.trajectory_semantics import extract_trajectory_semantics

    pool = await create_pool()
    try:
        if source_type != "openhands_trajectory_dir":
            raise ValueError(
                f"unsupported --source-type {source_type!r} "
                f"(supported: {', '.join(SUPPORTED_SOURCE_TYPES)})"
            )

        ingest_result = await ingest_openhands_trajectories(
            pool, str(root),
            owner_id=owner_id, visibility=visibility,
            scope_type=scope_type, scope_entity_id=scope_entity_id,
        )

        summary: dict = {
            "root": str(root),
            "source_type": source_type,
            "ingest": ingest_result,
        }

        # Semantic extraction -- off unless --extract-limit is passed: one
        # real LLM call per episode. Same cost-gated-by-default posture
        # run_ingestion.py's own --promote-limit/--extract-limit use.
        if extract_limit > 0:
            client = _extraction_client()
            if client is None:
                summary["extraction"] = {
                    "attempted": 0, "succeeded": 0, "failed": 0,
                    "skipped_reason": "no extraction LLM client configured (GENERAL_COMPUTE_API_KEY unset)",
                }
            else:
                # Episodes with real events but no extraction attempt yet,
                # bounded by --extract-limit -- never re-attempts a
                # 'failed' extraction automatically (that is a deliberate,
                # explicit re-extraction decision, not a bare sweep's job).
                pending = await pool.fetch(
                    """
                    SELECT DISTINCT ep.id, ep.start_ts, ep.end_ts
                    FROM episodes ep
                    JOIN trace_events te ON te.session_id = ep.session_id
                    WHERE NOT EXISTS (
                        SELECT 1 FROM trajectory_extractions x WHERE x.episode_id = ep.id
                    )
                    ORDER BY ep.start_ts ASC NULLS LAST
                    LIMIT $1
                    """,
                    extract_limit,
                )
                attempted = 0
                succeeded = 0
                failed = 0
                errors: list[str] = []
                for row in pending:
                    event_count = await pool.fetchval(
                        "SELECT count(*) FROM trace_events te "
                        "WHERE te.session_id = (SELECT session_id FROM episodes WHERE id = $1::uuid) "
                        "AND ($2::timestamptz IS NULL OR te.\"timestamp\" >= $2) "
                        "AND ($3::timestamptz IS NULL OR te.\"timestamp\" <= $3)",
                        row["id"], row["start_ts"], row["end_ts"],
                    )
                    choice = choose_extraction_model(event_count=event_count or 0)
                    attempted += 1
                    try:
                        await extract_trajectory_semantics(
                            pool, str(row["id"]), client=client, model=choice.model,
                            escalated=choice.escalated, escalation_reason=choice.escalation_reason,
                            owner_id=owner_id, visibility=visibility,
                            scope_type=scope_type, scope_entity_id=scope_entity_id,
                        )
                        succeeded += 1
                    except ExtractionTransientFailure as exc:
                        failed += 1
                        errors.append(f"{row['id']}: {exc}")
                        log.warning("trajectory extraction failed for episode %s: %s", row["id"], exc)
                summary["extraction"] = {
                    "attempted": attempted, "succeeded": succeeded, "failed": failed,
                    "errors": errors,
                }

        return summary
    finally:
        await pool.close()


def main() -> None:
    # The headless third surface -- no HTTP layer, no MCP client. Without
    # this, "spin up a scheduled worker on Cloud Run / GitHub Actions" has
    # no real entry point: ingest_trajectory (MCP) needs a connected MCP
    # client, POST /v1/trajectories/ingest (REST) needs a running FastAPI
    # process with an admin key. This script needs neither.
    observability.init("worker")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--root", type=Path, required=True,
        help="Directory of trajectory export files to ingest.",
    )
    parser.add_argument(
        "--source-type", default="openhands_trajectory_dir", choices=SUPPORTED_SOURCE_TYPES,
        help="Which adapter to route through (default: openhands_trajectory_dir).",
    )
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit.")
    parser.add_argument(
        "--interval", type=float, default=None,
        help="Loop, sleeping this many seconds between passes. Runs until Ctrl-C/SIGTERM.",
    )
    parser.add_argument(
        "--extract-limit", type=int, default=0, metavar="N",
        help="Run semantic extraction for up to N newly-ingested episodes with no prior "
             "extraction attempt. COSTS ONE REAL LLM CALL PER EPISODE -- 0 (the default) "
             "disables it entirely.",
    )
    parser.add_argument("--owner-id", default=None, help="Attribute ingested data to this owner.")
    parser.add_argument(
        "--visibility", default="public", choices=("public", "private"),
        help="Visibility to stamp on ingested rows (default: public).",
    )
    parser.add_argument(
        "--scope-type", default="global",
        help="V0 scope_type for ingested rows (default: global). Non-global requires --scope-entity-id.",
    )
    parser.add_argument("--scope-entity-id", default=None)
    args = parser.parse_args()

    if not args.once and args.interval is None:
        parser.error("pass --once for a single pass, or --interval N to loop")
    if not args.root.is_dir():
        parser.error(f"--root {args.root} is not a directory")

    while True:
        summary = asyncio.run(_run_once(
            args.root,
            source_type=args.source_type,
            owner_id=args.owner_id,
            visibility=args.visibility,
            scope_type=args.scope_type,
            scope_entity_id=args.scope_entity_id,
            extract_limit=args.extract_limit,
        ))
        print(json.dumps(summary, default=str))
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
