"""
Experiment 3 -- debate + bi-temporal supersession, end to end.

Self-contained: needs no benchmark. The original plan named tau3-bench's
`banking_knowledge`, which is not present on this machine (the vendored
tau-bench has only `airline` and `retail`), and this experiment never
actually required it -- only a contradiction and a downstream query.

Three stages, each answering a different question:

  1. DEBATE FIRES. TriggerDetector reads the `traces` table and nothing
     else, so a contradiction sitting in the graph cannot itself start a
     debate. Synthetic failure traces are inserted against the refund
     procedure, exactly as scripts/bootstrap_demo.py does, and the real
     LoopOrchestrator runs. Question: does the machinery produce
     candidates and scorecards against a live panel?

  2. SUPERSESSION APPLIES. The policy is modelled as a task_node and
     revised through UpdateTaskNodeOp via the real KnowledgeUpdater --
     ChangeSet has no update/invalidate op for knowledge_nodes, so a
     task_node is the only shape the supported write path can supersede.
     Question: does the old row get t_invalid, and a SUPERSEDES edge?

  3. BEHAVIOUR CHANGES. The point of the mechanism is not the row state,
     it is what a downstream agent then does. Question: does an agent
     grounded in the graph follow the NEW rule where a flat-RAG baseline
     holding both documents follows the old one or hedges?

Stage 3 is n=1 policy with several paraphrases. It is a case study and is
reported as one -- no p-value is attached to it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openai import AsyncOpenAI

from app.config import settings
from app.db.session import create_pool
from app.debate.panel import default_judge, default_panel
from app.models.change import ChangeSet, UpdateTaskNodeOp
from app.services.access import AccessScope
from app.services.embeddings import to_pgvector
from app.services.knowledge_update import KnowledgeUpdater
from app.services.loop import LoopOrchestrator
from app.services.triggers import ThresholdRule, TriggerDetector
from experiments.after.embed_cache import CachedEmbedder

CREATED_BY = "exp3_supersession"

OLD_RULE = (
    "Process a customer refund request. Every refund requires the customer to "
    "present the ORIGINAL RECEIPT. If no receipt is available the refund must be "
    "denied, with no exceptions for small amounts."
)
NEW_RULE = (
    "Process a customer refund request. As of the policy update, refunds UNDER $50 "
    "no longer require a receipt and may be approved without one. Refunds of $50 or "
    "more still require the original receipt."
)

QUESTIONS = [
    "A customer wants a $30 refund and has no receipt. Approve or deny?",
    "Someone is asking to return a $28 item without a receipt. What do we do?",
    "Refund request: $45, customer lost the receipt. Can this be approved?",
    "Customer has no proof of purchase and wants $19 back. Approve or deny?",
    "$35 refund, no receipt provided. What is the correct action?",
    "A shopper requests a $40 refund but cannot find their receipt. Approve or deny?",
]

CLASSIFY = (
    "You are grading another assistant's answer. Reply with EXACTLY one word: "
    "APPROVE if the answer says the refund can be approved/allowed without a receipt; "
    "DENY if it says the refund must be refused or a receipt is required; "
    "UNCLEAR if it does neither."
)


async def reset(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM traces WHERE task_node_id IN "
                "(SELECT id FROM task_nodes WHERE created_by = $1)", CREATED_BY)
            await conn.execute(
                "DELETE FROM scorecards WHERE debate_id IN (SELECT d.id FROM debates d "
                "JOIN triggers t ON t.debate_id = d.id WHERE t.task_node_id IN "
                "(SELECT id FROM task_nodes WHERE created_by = $1))", CREATED_BY)
            await conn.execute(
                "DELETE FROM candidates WHERE debate_id IN (SELECT d.id FROM debates d "
                "JOIN triggers t ON t.debate_id = d.id WHERE t.task_node_id IN "
                "(SELECT id FROM task_nodes WHERE created_by = $1))", CREATED_BY)
            await conn.execute(
                "DELETE FROM debate_events WHERE debate_id IN (SELECT d.id FROM debates d "
                "JOIN triggers t ON t.debate_id = d.id WHERE t.task_node_id IN "
                "(SELECT id FROM task_nodes WHERE created_by = $1))", CREATED_BY)
            await conn.execute(
                "DELETE FROM debates WHERE trigger_id IN (SELECT id FROM triggers "
                "WHERE task_node_id IN (SELECT id FROM task_nodes WHERE created_by = $1))",
                CREATED_BY)
            await conn.execute(
                "DELETE FROM triggers WHERE task_node_id IN "
                "(SELECT id FROM task_nodes WHERE created_by = $1)", CREATED_BY)
            await conn.execute(
                "DELETE FROM edges WHERE created_by = $1 OR source_id IN "
                "(SELECT id FROM task_nodes WHERE created_by = $1) OR target_id IN "
                "(SELECT id FROM task_nodes WHERE created_by = $1)", CREATED_BY)
            await conn.execute("DELETE FROM task_nodes WHERE created_by = $1", CREATED_BY)


async def seed(pool, embedder) -> uuid.UUID:
    vec = await embedder.embed_one(f"Process customer refund\n{OLD_RULE}", input_type="document")
    row = await pool.fetchrow(
        "INSERT INTO task_nodes (name, description, provenance, created_by, embedding) "
        "VALUES ($1,$2,'company_ingested',$3,$4::vector) RETURNING id",
        "Process customer refund", OLD_RULE, CREATED_BY, to_pgvector(vec),
    )
    return row["id"]


async def insert_traces(pool, task_node_id, n=25, failure_rate=0.8) -> None:
    now = datetime.now(timezone.utc)
    tenant = uuid.UUID(settings.default_tenant_id)
    async with pool.acquire() as conn:
        for i in range(n):
            await conn.execute(
                "INSERT INTO traces (trace_id, tenant_id, timestamp, task_node_id, "
                "actor_id, action_type, outcome, cost, latency_ms) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                f"exp3-{uuid.uuid4()}", tenant, now - timedelta(hours=i),
                task_node_id, "agent:refund-bot", "execute",
                "failure" if i < int(n * failure_rate) else "success", 0.01, 1200,
            )


async def stage1_debate(pool, task_node_id) -> dict:
    detector = TriggerDetector(pool)
    rules = [ThresholdRule(name="refund_error_rate", metric="error_rate",
                           threshold=0.15, direction="above", min_samples=20)]
    hits = await detector.scan(rules)
    hits = [h for h in hits if h.task_node_id == task_node_id]
    if not hits:
        return {"fired": False, "note": "no trigger -- traces did not breach the rule"}
    trigger_ids = await detector.record(hits)
    if not trigger_ids:
        return {"fired": False, "note": "trigger suppressed as already unresolved"}

    orchestrator = LoopOrchestrator(pool, panel=default_panel(), judge=default_judge())
    out: dict = {"fired": True, "observed_error_rate": round(hits[0].observed_value, 3)}
    try:
        scorecards = await orchestrator.run(trigger_ids[0])
        out["scorecards"] = len(scorecards)
        out["recommendations"] = [getattr(sc, "recommendation", None) for sc in scorecards]
        out["layer1_passed"] = sum(1 for sc in scorecards if sc.layer1.passed)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

    row = await pool.fetchrow(
        "SELECT d.id, d.state FROM debates d JOIN triggers t ON t.debate_id = d.id "
        "WHERE t.id = $1", trigger_ids[0])
    if row:
        out["debate_state"] = row["state"]
        out["candidates"] = await pool.fetchval(
            "SELECT count(*) FROM candidates WHERE debate_id = $1", row["id"])
        out["events"] = [
            dict(r) for r in await pool.fetch(
                "SELECT from_state, to_state, reason FROM debate_events "
                "WHERE debate_id = $1 ORDER BY created_at", row["id"])
        ]
    return out


async def stage2_supersede(pool, embedder, task_node_id) -> dict:
    cs = ChangeSet(ops=[UpdateTaskNodeOp(
        task_node_id=task_node_id,
        changes={"description": NEW_RULE},
        reason="Policy update: refunds under $50 no longer require a receipt.",
    )])
    applied = await KnowledgeUpdater(pool).apply(cs, approver_id=CREATED_BY)
    new_id = uuid.UUID(applied[0]["new_id"])

    vec = await embedder.embed_one(f"Process customer refund\n{NEW_RULE}", input_type="document")
    await pool.execute("UPDATE task_nodes SET embedding = $2::vector WHERE id = $1",
                       new_id, to_pgvector(vec))

    old = await pool.fetchrow("SELECT t_invalid FROM task_nodes WHERE id = $1", task_node_id)
    sup = await pool.fetchval(
        "SELECT count(*) FROM edges WHERE edge_type='SUPERSEDES' "
        "AND source_id=$1 AND target_id=$2 AND t_invalid IS NULL", new_id, task_node_id)
    live = await pool.fetch(
        "SELECT description FROM task_nodes WHERE created_by=$1 AND t_invalid IS NULL",
        CREATED_BY)
    return {
        "applied": applied,
        "old_invalidated": old["t_invalid"] is not None,
        "supersedes_edge": sup == 1,
        "live_versions": len(live),
        "live_mentions_new_rule": any("under $50" in (r["description"] or "") for r in live),
        "live_mentions_old_rule": any("no exceptions" in (r["description"] or "") for r in live),
    }


async def stage3_behaviour(pool, model: str) -> dict:
    # Groq, not local Ollama: a hosted snapshot is pinned, where an Ollama
    # tag is whatever it pointed at on pull day.
    client = AsyncOpenAI(api_key=settings.require("groq_api_key"),
                         base_url=settings.groq_base_url)
    judge = default_judge()

    graph_ctx_rows = await pool.fetch(
        "SELECT name, description FROM task_nodes WHERE created_by=$1 AND t_invalid IS NULL",
        CREATED_BY)
    graph_ctx = "\n".join(f"{r['name']}: {r['description']}" for r in graph_ctx_rows)
    # Flat RAG: both documents, no notion of invalidation -- the condition
    # the bi-temporal model is supposed to beat.
    flat_ctx = f"Process customer refund: {OLD_RULE}\n\nProcess customer refund: {NEW_RULE}"

    async def ask(ctx: str, q: str) -> str:
        r = await asyncio.wait_for(client.chat.completions.create(
            model=model, temperature=0, max_tokens=200,
            messages=[
                {"role": "system", "content":
                 "Answer using ONLY the company policy provided. Be decisive: "
                 "state clearly whether the refund is approved or denied."},
                {"role": "user", "content": f"POLICY:\n{ctx}\n\nQUESTION: {q}"},
            ]), timeout=300.0)
        return (r.choices[0].message.content or "").strip()

    async def classify(answer: str) -> str:
        try:
            v = await asyncio.wait_for(judge.respond(CLASSIFY, answer), timeout=120.0)
            v = (v or "").strip().upper()
            for label in ("APPROVE", "DENY", "UNCLEAR"):
                if label in v:
                    return label
        except Exception:  # noqa: BLE001
            pass
        return "UNCLEAR"

    rows = []
    for q in QUESTIONS:
        g = await ask(graph_ctx, q)
        f = await ask(flat_ctx, q)
        rows.append({
            "question": q,
            "graph_answer": g[:400], "graph_verdict": await classify(g),
            "flat_answer": f[:400], "flat_verdict": await classify(f),
        })
        print(f"  {q[:45]:45s} graph={rows[-1]['graph_verdict']:8s} flat={rows[-1]['flat_verdict']}")

    def tally(key: str) -> dict:
        return {v: sum(1 for r in rows if r[key] == v) for v in ("APPROVE", "DENY", "UNCLEAR")}

    return {
        "model": model,
        "n_questions": len(rows),
        "graph_grounded": tally("graph_verdict"),
        "flat_rag": tally("flat_verdict"),
        "rows": rows,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="llama-3.1-8b-instant")
    ap.add_argument("--min-interval", type=float, default=21.0)
    ap.add_argument("--skip-debate", action="store_true")
    args = ap.parse_args()

    pool = await create_pool(min_size=1, max_size=4)
    embedder = CachedEmbedder(min_interval=args.min_interval)
    try:
        await reset(pool)
        task_node_id = await seed(pool, embedder)
        print(f"seeded refund task_node {task_node_id}")
        await insert_traces(pool, task_node_id)
        print("inserted 25 traces at 80% failure")

        results = {}
        if not args.skip_debate:
            print("\n=== stage 1: debate ===")
            results["stage1_debate"] = await stage1_debate(pool, task_node_id)
            print(json.dumps(results["stage1_debate"], indent=2, default=str))

        print("\n=== stage 2: supersession ===")
        results["stage2_supersession"] = await stage2_supersede(pool, embedder, task_node_id)
        print(json.dumps(results["stage2_supersession"], indent=2, default=str))

        print("\n=== stage 3: downstream behaviour ===")
        results["stage3_behaviour"] = await stage3_behaviour(pool, args.model)
        print(json.dumps({k: v for k, v in results["stage3_behaviour"].items() if k != "rows"},
                         indent=2))

        Path(__file__).parent.joinpath("results_exp3_supersession.json").write_text(
            json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\nwrote results_exp3_supersession.json")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
