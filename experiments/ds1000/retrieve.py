"""Arm B notes: every test problem goes to Kel exactly as a user's task would -- the real
`find_ways` MCP tool, in-process, against kel_ds1000_demo. The planner policy is the one
the BigCodeBench demo automated (unchanged):

  resolved   -> the chosen Procedure;
  ambiguous  -> first, ways the answer already lists on its candidates (score order);
                else re-ask "<candidate name>: <problem>" for the top 3 candidates and
                take the first that resolves with a Procedure;
  no_match   -> no notes.

Query: the problem text, first 1500 characters (as in the BCB demo). Writes
runs/notes_B.json {problem_id: {text, ref, goal_id, observed_on, path}}.
"""
from __future__ import annotations

import asyncio
import json
import logging

import demo_env

from common import problems, test_items
from kel_setup import procedure_text

logging.disable(logging.INFO)


class _RequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _Context:
    def __init__(self, pool):
        self.request_context = _RequestContext(pool)


def _outcome(d: dict) -> str:
    return d.get("outcome") or d.get("status") or "unknown"


async def ask(srv, ctx, query: str) -> dict:
    out = await srv.find_ways(query[:1500], ctx)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"outcome": "refused", "raw": out[:300]}


def first_procedure(d: dict):
    for p in d.get("procedures") or []:
        pid = p.get("procedure_id") or p.get("id")
        if pid:
            goal = (d.get("goal") or {}).get("id") if isinstance(d.get("goal"), dict) else None
            return str(pid), goal, p.get("observed_on_more_specific_goal")
    return None, None, None


async def main(out_name: str = "notes_B.json", query_file: str | None = None) -> None:
    """`query_file`: {problem_id: query} written by agent_queries.py (the agent's own request);
    default: the problem text itself."""
    demo_env.verify_after_import()
    queries = json.loads((demo_env.RUNS / query_file).read_text(encoding="utf-8")) if query_file else {}
    import app.mcp_server.server as srv
    from app.db.session import create_pool

    out_path = demo_env.RUNS / out_name
    out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    ctx = _Context(pool)
    try:
        for t in test_items():
            pid = t["problem_id"]
            if pid in out:
                continue
            query = queries[pid] if query_file else problems()[pid]["prompt"]
            path = []
            d = await ask(srv, ctx, query)
            path.append({"query": "problem", "outcome": _outcome(d)})
            ref, gid, observed = first_procedure(d) if _outcome(d) == "resolved" else (None, None, None)
            if ref is None and _outcome(d) == "ambiguous":
                for cand in sorted(d.get("candidates") or [], key=lambda c: -(c.get("score") or 0)):
                    ways = cand.get("ways") or []
                    if ways:
                        ref, gid = ways[0]["procedure_id"], (cand.get("goal") or {}).get("id")
                        observed = ways[0].get("observed_on_goal")
                        path.append({"query": "candidate ways", "outcome": "ways_on_candidate"})
                        break
            if ref is None and _outcome(d) == "ambiguous":
                for cand in sorted(d.get("candidates") or [], key=lambda c: -(c.get("score") or 0))[:3]:
                    name = (cand.get("goal") or {}).get("canonical_name")
                    if not name:
                        continue
                    d2 = await ask(srv, ctx, f"{name}: {query}")
                    ref, gid, observed = first_procedure(d2) if _outcome(d2) == "resolved" else (None, None, None)
                    path.append({"query": name[:90], "outcome": _outcome(d2), "procedure": ref})
                    if ref:
                        break
            entry = {"path": path, "ref": ref, "goal_id": gid, "observed_on": observed, "text": None}
            if ref:
                entry["text"] = await procedure_text(pool, ref)
            out[pid] = entry
            out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
            print(f"{pid:>4} {t['role']:<9} {' -> '.join(p['outcome'] for p in path):<45} {'NOTES' if ref else '-'}",
                  flush=True)
    finally:
        await pool.close()
    print(f"test problems with Kel notes: {sum(1 for v in out.values() if v['ref'])} of {len(out)}")


if __name__ == "__main__":
    import sys

    asyncio.run(main(*(sys.argv[1:3])))
