"""Recover distillation substrate-events from persisted procedure state.

The distillation sweep updated 618 procedures but its final trace_events
INSERT died on a missing timestamp value. The evidence needed to rebuild
that trail is fully persisted -- every distilled row carries
domain_payload.distilled=true plus the distilled steps -- so this replays
event construction WITHOUT any model calls:

  1. mark stale 'running' distill traces aborted (earlier crashed attempts)
  2. reconstruct one trace_event per distilled procedure under the latest
     distill trace id, dedup_key = <trace>:<source_doc_id>
  3. close the trace as success

Run from backend/: python scripts/recover_distill_trace.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool

DOCS_DIR = Path(r"C:\Users\chait\Prog\3Found\vendor\tau2-bench\data\tau2\domains\banking_knowledge\documents")


async def main() -> None:
    from app.db.session import create_pool as _cp  # noqa: F401  (symmetry)

    pool = await create_pool(min_size=1, max_size=2)

    # 1. abort stale running distill traces except the one being recovered
    latest = await pool.fetchval(
        "SELECT trace_id FROM agent_traces WHERE intent LIKE 'Distill 698%'"
        " ORDER BY started_at DESC LIMIT 1"
    )
    print("recovering into trace:", latest)
    await pool.execute(
        "UPDATE agent_traces SET outcome='aborted', ended_at=now()"
        " WHERE intent LIKE 'Distill 698%' AND outcome='running' AND trace_id <> $1",
        latest,
    )

    # title -> doc_id map from the source corpus
    title_to_doc = {}
    for p in sorted(DOCS_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        title_to_doc[d["title"]] = d["id"]

    rows = await pool.fetch(
        "SELECT goal, name, steps FROM procedures"
        " WHERE t_invalid IS NULL AND domain_payload->>'distilled' = 'true'"
    )
    print(f"distilled procedures found: {len(rows)}")

    events = []
    for r in rows:
        goal = r["goal"] or r["name"] or ""
        doc_id = title_to_doc.get(goal)
        if doc_id is None:
            continue
        steps = json.loads(r["steps"]) if isinstance(r["steps"], str) else (r["steps"] or [])
        out_chars = len((steps[0].get("action") if steps and isinstance(steps[0], dict) else "") or "")
        events.append((
            latest, "tool_call", "distill_policy",
            json.dumps({"doc_id": doc_id, "title": goal}),
            json.dumps({"out_chars": out_chars, "ok": True}),
            True,
            f"{latest}:{doc_id}",
        ))

    await pool.executemany(
        'INSERT INTO trace_events (trace_id, session_id, sequence, event_type,'
        ' tool_name, tool_input, tool_output, success, duration_ms, dedup_key,'
        ' schema_version, "timestamp")'
        " VALUES ($1,$1,$8,$2,$3,$4::jsonb,$5::jsonb,$6,0,$7,'v1',now())"
        " ON CONFLICT (dedup_key) DO NOTHING",
        [(e[0], e[1], e[2], e[3], e[4], e[5], e[6], i + 1) for i, e in enumerate(events)],
    )

    n = await pool.fetchval("SELECT count(*) FROM trace_events WHERE trace_id=$1", latest)
    await pool.execute(
        "UPDATE agent_traces SET outcome='success', ended_at=now() WHERE trace_id=$1",
        latest,
    )
    print(f"trace_events persisted: {n}")
    await pool.close()


asyncio.run(main())
