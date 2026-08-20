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

from app.db.session import create_pool
from app.services.ingestion_jobs import process_pending_jobs
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


async def _run_once(trace_dir: Path) -> dict:
    pool = await create_pool()
    try:
        collector_totals = {"records_seen": 0, "inserted": 0, "skipped_duplicate": 0, "quarantined": 0}
        files = sorted(trace_dir.glob("*.jsonl")) if trace_dir.is_dir() else []
        for f in files:
            result = await process_collector_file(pool, f)
            for k in collector_totals:
                collector_totals[k] += result.get(k, 0)

        job_totals = await process_pending_jobs(pool)

        return {
            "trace_dir": str(trace_dir),
            "files_processed": len(files),
            "collector": collector_totals,
            "jobs": job_totals,
        }
    finally:
        await pool.close()


def main() -> None:
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
    args = parser.parse_args()

    if not args.once and args.interval is None:
        parser.error("pass --once for a single pass, or --interval N to loop")

    trace_dir = args.trace_dir or _default_trace_dir()

    while True:
        summary = asyncio.run(_run_once(trace_dir))
        print(json.dumps(summary))
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
