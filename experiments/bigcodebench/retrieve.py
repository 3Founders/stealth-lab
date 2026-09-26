"""Held-out tasks go to Kel exactly as a user's task would: the real `find_ways` MCP tool,
called in-process against the demo database. The planner policy is the v1 one, automated:

  resolved   -> use the chosen Procedure;
  ambiguous  -> try the candidates in score order (max 3): re-ask with a SHARPER query
                ("<candidate name>: <the task>"), use the first that resolves with a Procedure;
  no_match   -> no knowledge (the agent works from the task alone).

Writes runs/knowledge_heldout.json: {task_id: {text, ref (procedure_id), goal_id, path}}.
The retrieval path of every task is kept for audit.
"""
from __future__ import annotations

import asyncio
import json
import logging

import demo_env

from learn import procedure_text
from run_models import sample_tasks

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


def observed_on(d: dict):
    for p in d.get("procedures") or []:
        if p.get("observed_on_more_specific_goal"):
            return p["observed_on_more_specific_goal"]
    return None


def first_procedure(d: dict) -> tuple[str | None, str | None]:
    for p in d.get("procedures") or []:
        pid = p.get("procedure_id") or p.get("id")
        if pid:
            goal = (d.get("goal") or {}).get("id") if isinstance(d.get("goal"), dict) else None
            return str(pid), goal
    return None, None


async def main(out_name: str, only: list[str] | None = None) -> None:
    demo_env.verify_after_import()
    import app.mcp_server.server as srv
    from app.db.session import create_pool

    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    ctx = _Context(pool)
    out: dict[str, dict] = {}
    try:
        for task in sample_tasks("heldout").values():
            if only and task.external_id not in only:
                continue
            path = []
            d = await ask(srv, ctx, task.goal_description)
            path.append({"query": "task statement", "outcome": _outcome(d)})
            pid, gid = first_procedure(d) if _outcome(d) == "resolved" else (None, None)
            observed = observed_on(d) if pid else None
            if pid is None and _outcome(d) == "ambiguous":
                # the planner's first look: ways the answer already offers on its candidates
                for cand in sorted(d.get("candidates") or [], key=lambda c: -(c.get("score") or 0)):
                    ways = cand.get("ways") or []
                    if ways:
                        pid, gid = ways[0]["procedure_id"], (cand.get("goal") or {}).get("id")
                        observed = ways[0].get("observed_on_goal")
                        path.append({"query": "candidate ways", "outcome": "ways_on_candidate", "procedure": pid})
                        break
            if pid is None and _outcome(d) == "ambiguous":
                for cand in sorted(d.get("candidates") or [], key=lambda c: -(c.get("score") or 0))[:3]:
                    name = (cand.get("goal") or {}).get("canonical_name")
                    if not name:
                        continue
                    d2 = await ask(srv, ctx, f"{name}: {task.goal_description}")
                    pid, gid = first_procedure(d2) if _outcome(d2) == "resolved" else (None, None)
                    path.append({"query": name[:90], "outcome": _outcome(d2), "procedure": pid,
                                 "candidate_score": cand.get("score")})
                    if pid:
                        observed = observed_on(d2)
                        break
            entry = {"path": path, "ref": pid, "goal_id": gid, "text": None, "observed_on": observed}
            if pid:
                entry["text"] = await procedure_text(pool, pid)
            out[task.external_id] = entry
            print(f"{task.external_id:<18} {' -> '.join(p['outcome'] for p in path):<40} "
                  f"{'knowledge' if pid else 'none'}", flush=True)
    finally:
        await pool.close()
    (demo_env.RUNS / out_name).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"held-out tasks with retrieved knowledge: {sum(1 for v in out.values() if v['ref'])} of {len(out)}")


if __name__ == "__main__":
    import sys
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "knowledge_heldout_down.json", sys.argv[2:] or None))
