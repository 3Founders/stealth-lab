"""Distillation arm: gemma reads each FULL banking policy doc once, writes a
minimal actionable summary, and that summary -- not the raw document --
becomes the procedure's served step content.

Why ingest-time, not query-time: phaseP proved query-time compression kills
reward (0/12) because the grader needs detail the per-turn squeeze destroys.
Here the distillation happens ONCE, offline, where every output can be
validated before any sweep depends on it -- and the original documents stay
intact in the KB (KB_search_bm25 fallback path) plus in tau2's own files.

Every processed document is recorded as a real substrate event: one
agent_traces row for the run, one trace_events row per document (canonical
schema from db/12_trace_ingestion_pipeline.sql, dedup_key idempotent).

Run from backend/:  python scripts/distill_banking_docs.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

# General Compute credentials for the compressor model
for line in Path(os.environ.get("ENV_FILE", r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env")).read_text(encoding="utf-8").splitlines():
    if line.startswith("GENERAL_COMPUTE_"):
        k, _, v = line.strip().partition("=")
        os.environ.setdefault(k, v)
# Force-set: OPENAI_API_KEY may exist as an EMPTY string (backend/.env carries
# an unset placeholder), and setdefault does not override empty values.
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ.get("GENERAL_COMPUTE_API_KEY", "")
if not os.environ.get("OPENAI_BASE_URL"):
    os.environ["OPENAI_BASE_URL"] = os.environ.get("GENERAL_COMPUTE_BASE_URL", "")
os.environ["CALLER_TAG"] = "distill_banking"

DOCS_DIR = Path(r"C:\Users\chait\Prog\3Found\vendor\tau2-bench\data\tau2\domains\banking_knowledge\documents")
MODEL = "openai/gemma-4-31B-it"
BATCH, SLEEP_S = 6, 3.0
TRACE_ID = f"distill-banking-{time.strftime('%Y%m%d-%H%M%S')}"

DISTILL_PROMPT = """Compress this banking policy document into the smallest actionable summary a support agent can execute from.

HARD RULES:
- Preserve EVERY tool name exactly (they contain random numeric suffixes).
- Preserve every eligibility condition, threshold, dollar amount, and duration EXACTLY.
- Preserve ordering constraints (what must happen before what).
- Convert prose to short imperative steps, verb first.
- If the document describes discoverable-tool unlock flows, keep give/call/submit sequence intact.
- Never add information absent from the document. No preamble, no commentary.

Output ONLY the compressed summary."""


async def main() -> None:
    import litellm

    from app.db.session import create_pool
    pool = await create_pool(min_size=1, max_size=2)

    # --- substrate event: the run itself -----------------------------------
    now = await pool.fetchval("SELECT now()")
    await pool.execute(
        "INSERT INTO agent_traces (trace_id, session_id, provider, intent, started_at,"
        " outcome, schema_version, visibility, owner_id)"
        " VALUES ($1,$1,'stealthlab-distiller',$2,$3,'running','v1','public',NULL)"
        " ON CONFLICT (trace_id) DO NOTHING",
        TRACE_ID,
        "Distill 698 banking_knowledge policy documents into minimal actionable "
        "procedure summaries (ingest-time compression arm)",
        now,
    )

    docs = sorted(DOCS_DIR.glob("*.json"))
    print(f"{len(docs)} documents to distill; trace_id={TRACE_ID}")

    # Resumable: skip docs whose procedure is already distilled (marker set
    # by a prior pass). Reruns after an interrupted sweep only pay for the
    # remaining documents.
    already = {
        r["goal"]
        for r in await pool.fetch(
            "SELECT goal FROM procedures WHERE t_invalid IS NULL"
            " AND domain_payload->>'distilled' = 'true'"
        )
    }
    print(f"{len(already)} already distilled -- skipping")

    done = skipped = failed = 0
    pending_events: list[tuple] = []

    for start in range(0, len(docs), BATCH):
        chunk = [json.loads(p.read_text(encoding="utf-8")) for p in docs[start : start + BATCH]]
        for d in chunk:
            if d["title"] in already:
                skipped += 1
                continue
            t0 = time.perf_counter()
            try:
                resp = litellm.completion(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": DISTILL_PROMPT},
                        {"role": "user", "content": f"{d['title']}\n\n{d['content']}"},
                    ],
                    temperature=0.0,
                    max_tokens=700,
                )
                out = (resp.choices[0].message.content or "").strip()
                ok = len(out) >= 40
            except Exception as exc:  # noqa: BLE001
                out, ok = "", False
                print(f"  FAIL {d['id']}: {str(exc)[:100]}")

            latency_ms = int((time.perf_counter() - t0) * 1000)
            pending_events.append((
                TRACE_ID, "tool_call", "distill_policy",
                json.dumps({"doc_id": d["id"], "title": d["title"]}),
                json.dumps({"out_chars": len(out), "ok": ok}),
                ok, latency_ms,
                f"{TRACE_ID}:{d['id']}",  # dedup key -- idempotent reruns
            ))

            if not ok:
                failed += 1
                continue

            row = await pool.fetchrow(
                "SELECT id FROM procedures WHERE goal = $1 AND t_invalid IS NULL LIMIT 1",
                d["title"],
            )
            if row is None:
                skipped += 1
                continue
            await pool.execute(
                "UPDATE procedures SET steps = $2::jsonb,"
                " domain_payload = jsonb_set(COALESCE(domain_payload,'{}'::jsonb),"
                " '{distilled}', 'true'::jsonb, true)"
                " WHERE id = $1::uuid",
                str(row["id"]),
                json.dumps([{"order": 1, "action": out}]),
            )
            done += 1
        print(f"  {min(start+BATCH,len(docs))}/{len(docs)} docs (updated={done}, "
              f"no-match={skipped}, failed={failed})", flush=True)
        if start + BATCH < len(docs):
            await asyncio.sleep(SLEEP_S)

    # --- persist substrate events ------------------------------------------
    await pool.executemany(
        "INSERT INTO trace_events (trace_id, session_id, sequence, event_type,"
        " tool_name, tool_input, tool_output, success, duration_ms, dedup_key,"
        " schema_version)"
        " SELECT $1,$1,ROW_NUMBER() OVER (),$2,$3,$4::jsonb,$5::jsonb,$6,$7,$9,'v1'"
        " FROM (SELECT generate_series(1,$8)) s(seq)"
        " WHERE NOT EXISTS (SELECT 1 FROM trace_events WHERE dedup_key = $9)",
        [(e[0], e[1], e[2], e[3], e[4], e[5], e[6], i + 1, e[7])
         for i, e in enumerate(pending_events)],
    )
    await pool.execute(
        "UPDATE agent_traces SET outcome='success', ended_at=now() WHERE trace_id=$1",
        TRACE_ID,
    )
    ev = await pool.fetchval("SELECT count(*) FROM trace_events WHERE trace_id=$1", TRACE_ID)

    print(f"\nDONE: updated={done} no-match={skipped} failed={failed}")
    print(f"substrate: trace_events persisted for trace {TRACE_ID}: {ev} events")
    await pool.close()


asyncio.run(main())
