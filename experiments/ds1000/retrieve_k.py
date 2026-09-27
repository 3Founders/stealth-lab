"""Round-3 arm K: the real `find_ways` with the knowledge improvements ON (set KNOWLEDGE_* env vars),
agent-written query, the unchanged planner policy (as retrieve.py), rendered as preregistered:

  1. the Procedure the planner selects (as arm B renders it), followed by its verified solution;
  2. "Similar solved problems (NOT verified to apply; adapt):" -- each related example from the
     FIRST find_ways call: task (first 1200 chars) + verified code, in find_ways order.

Writes <runs>/notes_K.json {problem_id: {text, ref, related: [procedure_id...], path}}.
"""
from __future__ import annotations

import asyncio
import json
import logging

import demo_env

from common import test_items
from kel_setup import procedure_text
from retrieve import _Context, _outcome, ask, first_procedure

logging.disable(logging.INFO)
RELATED_HEADER = "Similar solved problems (NOT verified to apply; adapt them):"


def _example_of(d: dict, ref: str | None):
    for p in d.get("procedures") or []:
        if str(p.get("procedure_id")) == str(ref) and p.get("verified_example"):
            return p["verified_example"]
    for c in d.get("candidates") or []:
        for w in c.get("ways") or []:
            if str(w.get("procedure_id")) == str(ref) and w.get("verified_example"):
                return w["verified_example"]
    return None


async def main(out_name: str = "notes_K.json", query_file: str = "agent_queries.json") -> None:
    demo_env.verify_after_import()
    from app.config import settings

    assert settings.knowledge_verified_examples and settings.knowledge_related_examples, "arm K needs the flags ON"
    import app.mcp_server.server as srv
    from app.db.session import create_pool

    queries = json.loads((demo_env.RUNS / query_file).read_text(encoding="utf-8"))
    out_path = demo_env.RUNS / out_name
    out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    ctx = _Context(pool)
    try:
        for t in test_items():
            pid = t["problem_id"]
            if pid in out:
                continue
            query = queries[pid]
            path, example, source = [], None, None
            d = await ask(srv, ctx, query)
            path.append({"query": "request", "outcome": _outcome(d)})
            related = d.get("related_examples") or []
            ref, _gid, _obs = first_procedure(d) if _outcome(d) == "resolved" else (None, None, None)
            source = d if ref else None
            if ref is None and _outcome(d) == "ambiguous":
                for cand in sorted(d.get("candidates") or [], key=lambda c: -(c.get("score") or 0)):
                    ways = cand.get("ways") or []
                    if ways:
                        ref, source = ways[0]["procedure_id"], d
                        path.append({"query": "candidate ways", "outcome": "ways_on_candidate"})
                        break
            if ref is None and _outcome(d) == "ambiguous":
                for cand in sorted(d.get("candidates") or [], key=lambda c: -(c.get("score") or 0))[:3]:
                    name = (cand.get("goal") or {}).get("canonical_name")
                    if not name:
                        continue
                    d2 = await ask(srv, ctx, f"{name}: {query}")
                    ref, _gid, _obs = first_procedure(d2) if _outcome(d2) == "resolved" else (None, None, None)
                    path.append({"query": name[:90], "outcome": _outcome(d2), "procedure": ref})
                    if ref:
                        source = d2
                        break
            parts: list[str] = []
            if ref:
                block = await procedure_text(pool, ref)
                example = _example_of(source or {}, ref)
                if example:
                    block += f"\nVerified solution of that past problem:\n```python\n{example['code']}\n```"
                parts.append(block)
            related = [r for r in related if str(r.get("procedure_id")) != str(ref)]
            if related:
                lines = [RELATED_HEADER]
                for i, r in enumerate(related, 1):
                    lines.append(f"\n{i}. Past problem:\n{r['task'][:1200]}\nIts verified solution:\n```python\n{r['code']}\n```")
                parts.append("\n".join(lines))
            out[pid] = {"text": "\n\n".join(parts) or None, "ref": ref or (related[0]["procedure_id"] if related else None),
                        "procedure": ref, "procedure_has_example": bool(example),
                        "related": [r["procedure_id"] for r in related], "path": path}
            out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
            print(f"{pid:>4} {t['role']:<9} {' -> '.join(p['outcome'] for p in path):<40} "
                  f"proc={'Y' if ref else '-'} related={len(related)}", flush=True)
    finally:
        await pool.close()
    print(f"K notes on {sum(1 for v in out.values() if v['text'])} of {len(out)} test problems")


if __name__ == "__main__":
    import sys

    asyncio.run(main(*(sys.argv[1:3])))
