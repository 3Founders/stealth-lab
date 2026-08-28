#!/usr/bin/env python
"""
Runnable entry point for the RAW SESSION TRANSCRIPT ingestion path.

Why this exists (grep, not assumed): process_transcript_session() in
app/services/trace_worker.py is the ONLY production function that calls
assemble_episodes(), and before this script it had zero callers outside
its own module docstrings. run_ingestion.py drives a different chain --
collector file -> trace_events -> observations -- which never assembles
episodes at all. So the segmentation rules were fully implemented, fully
unit-tested, and never once run against real data.

This is deliberately NOT folded into run_ingestion.py: the two consume
different file formats from different directories. run_ingestion.py reads
hook_wrapper's collector envelopes ({dedup_key, event, event_type,
sequence, session_id}) out of .claude/traces/. This reads Claude Code's
own session transcripts ({uuid, timestamp, message, type}) out of
~/.claude/projects/<mangled-project-path>/, which is what _classify()
actually parses. Pointing either script at the other's directory parses
without error and yields silent garbage -- every line classifying to an
empty _Line, so one undifferentiated episode -- which is exactly why the
directories stay separate and explicit here.

content_ref stays a locator ("file#start:end"), never message content --
write_session_episodes() owns that posture; nothing here weakens it.

Usage:
    python scripts/ingest_transcripts.py --dry-run
    python scripts/ingest_transcripts.py --session <uuid>
    python scripts/ingest_transcripts.py --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.trace_worker import (
    assemble_episodes,
    discover_subagent_files,
    load_transcript,
    process_transcript_session,
)


def _default_transcript_dir() -> Path:
    """Claude Code stores session transcripts under a per-project directory
    whose name is the absolute project path with every separator and colon
    flattened to '-'. Mirrored here rather than imported: no library exposes
    it, and hard-coding one user's path would make the script unusable for
    anyone else."""
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd())).resolve()
    mangled = str(project).replace("\\", "-").replace("/", "-").replace(":", "-")
    return Path.home() / ".claude" / "projects" / mangled


def _sessions(transcript_dir: Path, session: str | None, limit: int | None) -> list:
    if not transcript_dir.is_dir():
        return []
    files = sorted(transcript_dir.glob("*.jsonl"))
    if session:
        files = [f for f in files if f.stem == session]
    if limit:
        files = files[:limit]
    return files


async def _dry_run(files: list) -> dict:
    """Assemble only -- proves the segmentation path runs on real data
    without opening a connection or writing a row."""
    out = []
    for f in files:
        main_lines, bad = load_transcript(f)
        assembly = assemble_episodes(main_lines, discover_subagent_files(f))
        out.append({
            "session": f.stem,
            "main_lines": assembly.main_lines,
            "bad_lines": bad + assembly.unparsed_main_lines,
            "episodes": len(assembly.episodes),
            "child_episodes": sum(len(e.children) for e in assembly.episodes),
            "subagent_files_seen": assembly.subagent_files_seen,
            "subagent_files_joined": assembly.subagent_files_joined,
            "unjoined_subagent_lines": assembly.unjoined_subagent_lines,
        })
    return {"dry_run": True, "sessions": out}


async def _ingest(files: list, *, owner_id, visibility, project_id) -> dict:
    pool = await create_pool()
    try:
        out = []
        for f in files:
            out.append({"session": f.stem, **await process_transcript_session(
                pool, f,
                owner_id=owner_id,
                visibility=visibility,
                project_id=project_id,
            )})
        return {"dry_run": False, "sessions": out}
    finally:
        await pool.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--transcript-dir", type=Path, default=None)
    p.add_argument("--session", default=None, help="single session id (file stem)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="assemble and report; touch no database")
    p.add_argument("--owner-id", default=None)
    p.add_argument("--visibility", default="private")
    p.add_argument("--project-id", default=None)
    args = p.parse_args()

    tdir = args.transcript_dir or _default_transcript_dir()
    files = _sessions(tdir, args.session, args.limit)
    if not files:
        print(json.dumps({"transcript_dir": str(tdir), "files": 0,
                          "error": "no matching transcripts"}, indent=2))
        return 1

    if args.dry_run:
        result = asyncio.run(_dry_run(files))
    else:
        result = asyncio.run(_ingest(
            files,
            owner_id=args.owner_id,
            visibility=args.visibility,
            project_id=args.project_id,
        ))
    result["transcript_dir"] = str(tdir)
    result["files"] = len(files)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
