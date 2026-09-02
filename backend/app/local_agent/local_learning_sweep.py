"""
P0-1: ongoing automatic learning that stays PRIVATE.

The V1 promise is `user work -> private personal memory -> explicit
publication only -> global`. This module is the "user work -> private
personal memory" half, run automatically on a timer by
`app/services/ingestion_scheduler.py` (mode="local", the V1 default).

    LOCAL AGENT / USER WORK
        -> events / traces  (.claude/traces/<session>.jsonl, the real collector output)
        -> episode / evidence  (parse_claude_code_trace_file, the SAME reader the
                                one-shot bootstrap uses -- not a second extractor)
        -> LocalProcedureStore candidate  (converge_episode, the SAME writer)

Nothing here imports asyncpg or app.db.session, directly or transitively.
A raw local trace is NEVER uploaded to the global server because automatic
learning is on -- the global ingestion path (observations -> claims ->
shared procedures) is a separate, explicit deployment mode
(ingestion_scheduler mode="global") a shared/company install opts into.

BOUNDED, IDEMPOTENT, RETRY-SAFE, FAILURE-VISIBLE:
  - `max_sessions` caps how many new transcripts one sweep touches.
  - A processed-session marker file next to the store means a transcript
    is read at most once across sweeps (a discussion-only session is
    marked too, so it is not re-read every tick).
  - A per-session exception is counted and surfaced, never aborts the
    sweep, and the session is NOT marked processed so a transient failure
    retries on the next tick.
  - No LLM call per event. Extraction is the deterministic tool-sequence
    reader; the optional `embed` callable is the only network touch and
    is off by default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from app.local_agent.historical_bootstrap import (
    converge_episode,
    parse_claude_code_trace_file,
)
from app.local_agent.local_store import LocalProcedureStore

_MARKER_NAME = "learning_sweep_state.json"


def _marker_path(store: LocalProcedureStore) -> Path:
    return Path(store.db_path).resolve().parent / _MARKER_NAME


def _load_processed(store: LocalProcedureStore) -> set[str]:
    p = _marker_path(store)
    try:
        return set(json.loads(p.read_text(encoding="utf-8")).get("processed_sessions", []))
    except (OSError, ValueError):
        return set()


def _save_processed(store: LocalProcedureStore, processed: set[str]) -> None:
    p = _marker_path(store)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"processed_sessions": sorted(processed)}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        # Best effort: a marker we cannot persist just means the next
        # sweep re-reads (cheap, deterministic) -- never a crash.
        pass


def run_local_learning_sweep(
    store: LocalProcedureStore,
    trace_dir: str,
    *,
    max_sessions: int = 5,
    embed: Optional[Any] = None,
    workspace_entity_id: Optional[str] = None,
) -> dict:
    """Turn up to `max_sessions` not-yet-seen local trace transcripts into
    private LocalProcedureStore candidates. Returns honest counts:
    {trace_dir, sessions_seen, sessions_new, sessions_processed, captured,
    merged, skipped, errors}.
    """
    summary = {
        "trace_dir": str(trace_dir),
        "sessions_seen": 0,
        "sessions_new": 0,
        "sessions_processed": 0,
        "captured": 0,
        "merged": 0,
        "skipped": 0,
        "errors": 0,
    }
    if not os.path.isdir(trace_dir):
        return summary

    files = [
        os.path.join(trace_dir, f)
        for f in os.listdir(trace_dir)
        if f.endswith(".jsonl")
    ]
    summary["sessions_seen"] = len(files)
    # Oldest first so a backlog drains in the order the work happened.
    files.sort(key=lambda p: os.path.getmtime(p))

    processed = _load_processed(store)
    new_files = [p for p in files if os.path.basename(p) not in processed]
    summary["sessions_new"] = len(new_files)

    loop = None
    for fpath in new_files[: max(0, max_sessions)]:
        fname = os.path.basename(fpath)
        try:
            episode = parse_claude_code_trace_file(fpath)
            if episode is None:
                summary["skipped"] += 1
                processed.add(fname)
                summary["sessions_processed"] += 1
                continue

            embedding = None
            if embed is not None:
                import asyncio

                try:
                    loop = loop or asyncio.new_event_loop()
                    embedding = loop.run_until_complete(
                        embed(episode.goal, input_type="document")
                    )
                except Exception:  # noqa: BLE001 -- embedding is optional
                    embedding = None

            result = converge_episode(
                store, episode,
                workspace_entity_id=workspace_entity_id,
                embedding=embedding,
            )
            status = result.get("status", "skipped")
            if status in ("captured", "merged"):
                summary[status] += 1
            else:
                summary["skipped"] += 1
            processed.add(fname)
            summary["sessions_processed"] += 1
        except Exception as exc:  # noqa: BLE001 -- one bad transcript must not
            # sink the sweep; the failure is counted and the session is
            # left UNMARKED so the next tick retries it.
            summary["errors"] += 1
            summary.setdefault("error_detail", []).append({"session": fname, "error": repr(exc)})

    if loop is not None:
        loop.close()
    _save_processed(store, processed)
    return summary
