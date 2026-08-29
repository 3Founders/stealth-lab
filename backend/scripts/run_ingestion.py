#!/usr/bin/env python
"""
The missing runnable entry point for the ingestion pipeline (handoff
item 4's real prerequisite). Before this script, process_collector_file()
and process_pending_jobs() both had real, tested implementations and
ZERO callers outside tests -- confirmed by grep, not assumed. Real data
sat in .claude/traces/<session>.jsonl (once hooks were wired) with
nothing ever reading it into the database.

Two passes, one process, for a straightforward reason: process_pending_
jobs() only has work to do once process_collector_file() has inserted
new trace_events rows and queued jobs for them in the SAME run, so
running them back to back (rather than as two separately-scheduled
things) means one `--interval` loop keeps the whole pipeline moving --
collector file -> trace_events -> observations -- without a second
scheduler to configure.

Usage:
    python scripts/run_ingestion.py --once
    python scripts/run_ingestion.py --interval 30
    python scripts/run_ingestion.py --once --trace-dir .claude/traces

Windows note (same as every other script in this repo): run with
`python`, not `python3` -- see IMPLEMENTATION_HANDOFF.md's environment
section for why python3 is intercepted here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app import observability
from app.db.session import create_pool
from app.services.ingestion_jobs import (
    enqueue_pending_claim_promotions,
    enqueue_pending_procedure_extractions,
    process_pending_jobs,
)
from app.services.trace_worker import process_collector_file


def _default_trace_dir() -> Path:
    # Same resolution order as hook_wrapper.py's _default_trace_dir(),
    # duplicated rather than imported: hook_wrapper.py is a standalone
    # script meant to run with no repo-relative imports at all (it's
    # invoked as a Claude Code hook command, possibly from any cwd), so
    # importing from it here would recreate exactly the coupling it was
    # written to avoid. Three lines of duplication is cheaper than that.
    env_dir = os.environ.get("STEALTHLAB_TRACE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd())) / ".claude" / "traces"


async def _run_once(trace_dir: Path, promote_limit: int = 0,
                    extract_limit: int = 0) -> dict:
    pool = await create_pool()
    try:
        collector_totals = {"records_seen": 0, "inserted": 0, "skipped_duplicate": 0, "quarantined": 0}
        files = sorted(trace_dir.glob("*.jsonl")) if trace_dir.is_dir() else []
        for f in files:
            result = await process_collector_file(pool, f)
            for k in collector_totals:
                collector_totals[k] += result.get(k, 0)

        # BEFORE draining, re-offer observations whose justifying episode
        # only showed up after their original promotion job already ran
        # (see enqueue_pending_claim_promotions' docstring -- this is the
        # ordering gap that left the real corpus at 1 claim instead of
        # ~3,106). Off unless --promote-limit is passed: each job it
        # creates costs one real embedding call.
        requeued = None
        if promote_limit > 0:
            requeued = await enqueue_pending_claim_promotions(pool, limit=promote_limit)

        # claim -> procedure candidate. AFTER the promotion sweep on
        # purpose: a claim created moments ago in this same pass is a
        # legitimate trigger for extracting from its episode, and the gate
        # (see _PENDING_EXTRACTION_SQL) is what stops that being reckless.
        # Off unless --extract-limit is passed: one real LLM call per job.
        extracted = None
        if extract_limit > 0:
            extracted = await enqueue_pending_procedure_extractions(
                pool, limit=extract_limit)

        job_totals = await process_pending_jobs(pool)

        summary = {
            "trace_dir": str(trace_dir),
            "files_processed": len(files),
            "collector": collector_totals,
            "jobs": job_totals,
        }
        if requeued is not None:
            summary["requeued_promotions"] = requeued
        if extracted is not None:
            summary["queued_extractions"] = extracted
        return summary
    finally:
        await pool.close()


def main() -> None:
    # The headless third surface. It has no HTTP layer, so nothing else
    # would ever report its failures: 32 jobs failed silently here on
    # 2026-08-28 and were only found by reading the job table by hand.
    # No-op without SENTRY_DSN.
    observability.init("worker")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace-dir", type=Path, default=None,
        help="Directory of collector .jsonl files (default: resolved same way hook_wrapper.py does)",
    )
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit.")
    parser.add_argument(
        "--interval", type=float, default=None,
        help="Loop, sleeping this many seconds between passes. Runs until Ctrl-C.",
    )
    parser.add_argument(
        "--promote-limit", type=int, default=0, metavar="N",
        help="Re-enqueue up to N observations whose justifying episode arrived "
             "after their original promotion job ran. COSTS ONE REAL EMBEDDING "
             "CALL PER OBSERVATION -- 0 (the default) disables it entirely. "
             "Run episode assembly (scripts/ingest_transcripts.py) first, or "
             "there will be no new episode for anything to anchor to.",
    )
    parser.add_argument(
        "--extract-limit", type=int, default=0, metavar="N",
        help="Enqueue procedure extraction for up to N claim-justifying episodes "
             "that clear the quality gate (a test_run or commit_made observation, "
             ">=5 observations, >=2 observation types -- see "
             "ingestion_jobs._PENDING_EXTRACTION_SQL for why each clause exists). "
             "COSTS ONE REAL grounded_hybrid_v1 LLM CALL PER EPISODE -- 0 (the "
             "default) disables it entirely.",
    )
    args = parser.parse_args()

    if not args.once and args.interval is None:
        parser.error("pass --once for a single pass, or --interval N to loop")

    trace_dir = args.trace_dir or _default_trace_dir()

    while True:
        summary = asyncio.run(
            _run_once(trace_dir, args.promote_limit, args.extract_limit))
        print(json.dumps(summary))
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
