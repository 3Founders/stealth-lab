"""Read-only whole-substrate audit: inventory, integrity, corpus quality,
trace chains, embeddings, and live retrieval smoke tests.

Safe to run during an active sweep -- no writes anywhere.
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
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

SECTION = "\n=== {} ==="


async def main() -> None:
    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)

    # ---------- A. inventory ----------
    print(SECTION.format("A. INVENTORY"))
    tables = [
        "procedures", "knowledge_nodes", "knowledge_edges", "observations",
        "claims", "agent_traces", "trace_events", "evidence",
        "execution_plans", "task_graphs", "executions", "claim_sources",
    ]
    for t in tables:
        try:
            n = await pool.fetchval(f"SELECT count(*) FROM {t}")
            print(f"  {t}: {n}")
        except Exception as e:
            print(f"  {t}: ERROR {str(e)[:60]}")

    # ---------- B. procedures corpus ----------
    print(SECTION.format("B. PROCEDURES CORPUS"))
    q = lambda s: pool.fetchval(s)
    print("  active:", await q("SELECT count(*) FROM procedures WHERE t_invalid IS NULL"),
          "| retired(t_invalid):", await q("SELECT count(*) FROM procedures WHERE t_invalid IS NOT NULL"))
    print("  steps non-array:", await q(
        "SELECT count(*) FROM procedures WHERE t_invalid IS NULL AND jsonb_typeof(steps)<>'array'"))
    n_pre = await q("SELECT count(*) FROM procedures WHERE t_invalid IS NULL AND jsonb_array_length(preconditions)>0")
    avg_pre = await q("SELECT COALESCE(round(avg(jsonb_array_length(preconditions)),2),0) FROM procedures WHERE t_invalid IS NULL AND jsonb_array_length(preconditions)>0")
    print(f"  with eligibility gates: {n_pre} (avg {avg_pre} predicates)")
    print("  distilled:", await q(
        "SELECT count(*) FROM procedures WHERE t_invalid IS NULL AND domain_payload->>'distilled'='true'"))
    print("  precond_v1 marker:", await q(
        "SELECT count(*) FROM procedures WHERE t_invalid IS NULL AND domain_payload->>'precond_v1'='true'"))
    null_emb = await q("SELECT count(*) FROM procedures WHERE t_invalid IS NULL AND embedding IS NULL")
    dims = await pool.fetch(
        "SELECT DISTINCT vector_dims(embedding::text::vector) AS d FROM procedures WHERE embedding IS NOT NULL LIMIT 3"
    ) if False else await pool.fetch(
        "SELECT DISTINCT array_length(embedding::real[],1) AS d FROM procedures WHERE embedding IS NOT NULL"
    )
    print("  embeddings NULL:", null_emb, "| dimensions:", [r["d"] for r in dims])
    provs = await pool.fetch(
        "SELECT provenance, count(*) AS c FROM procedures WHERE t_invalid IS NULL GROUP BY 1 ORDER BY c DESC LIMIT 4")
    print("  provenance:", {r["provenance"]: r["c"] for r in provs})
    dup = await q(
        "SELECT count(*) FROM (SELECT goal FROM procedures WHERE t_invalid IS NULL GROUP BY goal HAVING count(*)>1) x")
    print("  duplicate goals:", dup)

    # ---------- C. knowledge layer ----------
    print(SECTION.format("C. KNOWLEDGE LAYER"))
    kn = await pool.fetchrow(
        "SELECT count(*) AS n,"
        " count(*) FILTER (WHERE embedding IS NULL) AS no_emb,"
        " count(DISTINCT source_doc_id) AS docs"
        " FROM knowledge_nodes")
    print(f"  nodes: {kn['n']} | without embedding: {kn['no_emb']} | distinct source docs: {kn['docs']}")
    orphan_edges = await q(
        "SELECT count(*) FROM knowledge_edges e WHERE NOT EXISTS"
        " (SELECT 1 FROM knowledge_nodes a WHERE a.id=e.src_id)"
        " OR NOT EXISTS (SELECT 1 FROM knowledge_nodes b WHERE b.id=e.dst_id)")
    print("  orphan edges:", orphan_edges)

    # ---------- D. trace chains ----------
    print(SECTION.format("D. TRACE CHAINS"))
    trs = await pool.fetch(
        "SELECT trace_id, provider, outcome, started_at::date AS d,"
        " (SELECT count(*) FROM trace_events e WHERE e.trace_id=t.trace_id) AS ev"
        " FROM agent_traces t ORDER BY started_at DESC LIMIT 6")
    for r in trs:
        print(f"  {r['trace_id']} [{r['provider']}] {r['outcome']} {r['d']} events={r['ev']}")
    ddup = await q("SELECT count(*) FROM (SELECT dedup_key FROM trace_events GROUP BY 1 HAVING count(*)>1) x")
    orphan_ev = await q(
        "SELECT count(*) FROM trace_events e WHERE NOT EXISTS"
        " (SELECT 1 FROM agent_traces t WHERE t.trace_id=e.trace_id)")
    print("  duplicate dedup_keys:", ddup, "| orphan events:", orphan_ev)

    # ---------- E. migration ledger ----------
    print(SECTION.format("E. MIGRATION LEDGER"))
    try:
        pend = await pool.fetch(
            "SELECT filename FROM schema_migrations WHERE filename IN"
            " ('24_evidence.sql','25_universal_changesets.sql','26_replayability.sql')")
        have = {r["filename"] for r in pend}
        print("  ledgered 24-26:", sorted(have) if have else "NONE stamped (psql bypassed ledger)")
    except Exception as e:
        print("  schema_migrations:", str(e)[:60])

    # ---------- F. live retrieval smoke ----------
    print(SECTION.format("F. LIVE RETRIEVAL SMOKE (read-only queries)"))
    sys.path.insert(0, r"C:\Users\chait\Prog\3Found\vendor\tau2-bench\src")
    try:
        from tau2.domains.banking_knowledge.stealthlab_bridge import substrate_search
    except Exception:
        substrate_search = None
    queries = [
        "open a business checking account requirements",
        "credit card cash back eligibility credit score",
        "wire transfer fee international",
    ]
    if substrate_search is not None:
        for query in queries:
            t0 = time.perf_counter()
            try:
                res = await asyncio.wait_for(substrate_search(query=query, k=5), timeout=45)
                dt = time.perf_counter() - t0
                text = res if isinstance(res, str) else json.dumps(res)
                elig = text.count("ELIGIBILITY:")
                gates = text.count("- ")
                first = text.splitlines()[0][:70] if text else "(empty)"
                print(f"  '{query[:38]}' -> {dt:.1f}s | {len(text)} chars | ELIGIBILITY blocks={elig} | first: {first}")
            except Exception as e:
                print(f"  '{query[:38]}' -> FAIL {time.perf_counter()-t0:.0f}s {str(e)[:80]}")
    else:
        print("  substrate_search symbol not found -- skipping live probes")

    await pool.close()
    print(SECTION.format("AUDIT COMPLETE"))


asyncio.run(main())
