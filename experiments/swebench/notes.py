"""Memory blocks for the held-out arms, built from the frozen train-pool knowledge only.

    python notes.py queries     # the agent's own find_ways request per held-out issue (experiment model, temp 0)
    python notes.py K           # real find_ways (in-process, kel_swebench) + unchanged planner policy
    python notes.py E           # plain RAG: BM25 top-1 resolved train issue of the SAME repo + its verified patch
    python notes.py controls    # C1 random memory item, C2 generic placebo -- both only where K has notes,
                                # both cut to K's exact length

K rendering: the Procedure the planner selects (Way, steps, pitfalls), then "Verified patch of that past
issue:" + the patch it was extracted from. E rendering: the past issue (1200 chars) + its verified patch.
Every block is capped at memory.max_chars; patches at memory.procedure_arm_code_max_chars.
Writes runs/notes_<arm>.json {instance_id: {"text", "ref", ...}}.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sys
from collections import Counter

import swe_env
from generate import design, instances
from learn import resolved_train

MEM = swe_env.CONFIG["memory"]
QUERY_SYSTEM = ("You are a coding agent about to fix a GitHub issue in a repository. Before reading code you call a "
                "knowledge tool, find_ways, with a short request describing what you need to accomplish, so it can "
                "return known ways to do it. Write that request: one or two sentences, at most 40 words, naming the "
                "behaviour to change and the component involved. Reply with the request only.")
PLACEBO = ("General advice for fixing issues in large codebases: reproduce the reported behaviour mentally from the "
           "issue text; find where the behaviour is implemented before changing anything; prefer the smallest change "
           "that fixes the root cause; keep existing public interfaces and behaviour for other callers; handle edge "
           "cases the issue mentions; follow the surrounding code's conventions; do not modify tests. ")


def _cap(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 20] + "\n[... truncated]"


def queries() -> None:
    from generate import client

    path = swe_env.RUNS / "agent_queries.json"
    out = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    c, inst = client(), instances()
    for iid in design()["test"]:
        if iid in out:
            continue
        r = c.chat.completions.create(
            model=swe_env.CONFIG["model"]["id"], temperature=0, max_tokens=swe_env.CONFIG["query_writer"]["max_tokens"],
            messages=[{"role": "system", "content": QUERY_SYSTEM},
                      {"role": "user", "content": f"Repository: {inst[iid]['repo']}\n\n{inst[iid]['problem_statement'][:4000]}"}])
        text = re.sub(r"\s+", " ", (r.choices[0].message.content or "").strip())
        m = re.fullmatch(r'find_ways\((.*)\)\.?', text, flags=re.S)
        text = (m.group(1) if m else text).strip().strip('"').strip("'")
        if text:
            out[iid] = text
            path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"queries: {len(out)} of {len(design()['test'])}")


async def procedure_text(pool, procedure_id: str) -> str:
    row = await pool.fetchrow("SELECT name, capability_statement, steps, failure_conditions FROM procedures "
                              "WHERE procedure_id = $1::uuid AND t_invalid IS NULL ORDER BY version DESC LIMIT 1",
                              procedure_id)
    steps = row["steps"] if isinstance(row["steps"], list) else json.loads(row["steps"] or "[]")
    pit = row["failure_conditions"] if isinstance(row["failure_conditions"], list) else json.loads(row["failure_conditions"] or "[]")
    lines = [f"Way: {row['capability_statement'] or row['name']}"]
    lines += [f"  {s.get('order')}. {s.get('action')}" for s in sorted(steps, key=lambda s: s.get("order", 0))]
    if pit:
        lines += ["Pitfalls:"] + [f"  - {p}" for p in pit]
    return "\n".join(lines)


async def arm_k() -> None:
    import app.mcp_server.server as srv
    from app.db.session import create_pool

    procs = json.loads((swe_env.RUNS / "procedures_train.json").read_text(encoding="utf-8"))
    source_of = {p["procedure_id"]: iid for iid, p in procs.items()}
    wins = resolved_train()
    qs = json.loads((swe_env.RUNS / "agent_queries.json").read_text(encoding="utf-8"))

    class RC:
        def __init__(self, pool): self.lifespan_context = {"pool": pool}

    class Ctx:
        def __init__(self, pool): self.request_context = RC(pool)

    async def ask(ctx, q):
        try:
            return json.loads(await srv.find_ways(q[:1500], ctx))
        except json.JSONDecodeError:
            return {"outcome": "refused"}

    def first_proc(d):
        for p in d.get("procedures") or []:
            if p.get("procedure_id"):
                return str(p["procedure_id"])
        return None

    out_path = swe_env.RUNS / "notes_K.json"
    out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    pool = await create_pool(swe_env.DSN, min_size=1, max_size=4)
    ctx = Ctx(pool)
    try:
        for iid in design()["test"]:
            if iid in out:
                continue
            d = await ask(ctx, qs[iid])
            path = [d.get("outcome")]
            ref = first_proc(d) if d.get("outcome") == "resolved" else None
            if ref is None and d.get("outcome") == "ambiguous":
                for cand in sorted(d.get("candidates") or [], key=lambda c: -(c.get("score") or 0)):
                    if cand.get("ways"):
                        ref = cand["ways"][0]["procedure_id"]
                        path.append("ways_on_candidate")
                        break
            if ref is None and d.get("outcome") == "ambiguous":
                for cand in sorted(d.get("candidates") or [], key=lambda c: -(c.get("score") or 0))[:3]:
                    name = (cand.get("goal") or {}).get("canonical_name")
                    if name:
                        d2 = await ask(ctx, f"{name}: {qs[iid]}")
                        path.append(d2.get("outcome"))
                        ref = first_proc(d2) if d2.get("outcome") == "resolved" else None
                        if ref:
                            break
            text = None
            if ref:
                text = await procedure_text(pool, ref)
                src = source_of.get(ref)
                if src in wins:
                    text += "\nVerified patch of that past issue:\n" + _cap(wins[src]["patch"], MEM["procedure_arm_code_max_chars"])
                text = _cap(text, MEM["max_chars"])
            out[iid] = {"text": text, "ref": ref, "source_instance": source_of.get(ref), "path": path}
            out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
            print(f"{iid:<45} {' -> '.join(map(str, path)):<35} {'NOTES' if text else '-'}", flush=True)
    finally:
        await pool.close()
    print(f"K notes on {sum(1 for v in out.values() if v['text'])} of {len(out)}")


def _bm25_top(query: str, docs: dict[str, str]) -> str | None:
    tok = lambda s: re.findall(r"\w+", s.lower())
    if not docs:
        return None
    tfs = {i: Counter(tok(t)) for i, t in docs.items()}
    lens = {i: sum(tf.values()) for i, tf in tfs.items()}
    avg = sum(lens.values()) / len(lens)
    df = Counter(t for tf in tfs.values() for t in tf)
    n = len(docs)
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    q = tok(query)
    score = lambda i: sum(idf.get(t, 0) * tfs[i][t] * 2.5 / (tfs[i][t] + 1.5 * (0.25 + 0.75 * lens[i] / avg))
                          for t in q if t in tfs[i])
    return max(docs, key=lambda i: (score(i), i))


def arm_e() -> None:
    inst, wins = instances(), resolved_train()
    out = {}
    for iid in design()["test"]:
        repo = inst[iid]["repo"]
        pool = {w: inst[w]["problem_statement"] for w in wins if inst[w]["repo"] == repo}
        top = _bm25_top(inst[iid]["problem_statement"], pool)
        text = None
        if top:
            text = _cap(f"Similar past issue:\n{inst[top]['problem_statement'][:1200]}\n\nIts verified patch:\n"
                        + _cap(wins[top]["patch"], MEM["procedure_arm_code_max_chars"]), MEM["max_chars"])
        out[iid] = {"text": text, "ref": top}
    (swe_env.RUNS / "notes_E.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"E notes on {sum(1 for v in out.values() if v['text'])} of {len(out)}")


def controls() -> None:
    inst, wins = instances(), resolved_train()
    k = json.loads((swe_env.RUNS / "notes_K.json").read_text(encoding="utf-8"))
    c1, c2 = {}, {}
    seed = swe_env.CONFIG["split"]["seed"]
    for iid, note in k.items():
        if not note.get("text"):
            c1[iid] = c2[iid] = {"text": None, "ref": None}
            continue
        n = len(note["text"])
        repo = inst[iid]["repo"]
        others = sorted((w for w in wins if inst[w]["repo"] == repo and w != note.get("source_instance")),
                        key=lambda w: hashlib.sha256(f"{seed}|C1|{iid}|{w}".encode()).hexdigest())
        if others:
            w = others[0]
            item = f"Past issue:\n{inst[w]['problem_statement'][:1200]}\n\nIts verified patch:\n{wins[w]['patch']}"
            c1[iid] = {"text": item[:n], "ref": w}
        else:
            c1[iid] = {"text": None, "ref": None}
        c2[iid] = {"text": (PLACEBO * (n // len(PLACEBO) + 1))[:n], "ref": "placebo"}
    (swe_env.RUNS / "notes_C1.json").write_text(json.dumps(c1, indent=1), encoding="utf-8")
    (swe_env.RUNS / "notes_C2.json").write_text(json.dumps(c2, indent=1), encoding="utf-8")
    print(f"C1 notes on {sum(1 for v in c1.values() if v['text'])}, C2 on {sum(1 for v in c2.values() if v['text'])}")


FROZEN = swe_env.RUNS / "kel_frozen.json"


def freeze() -> None:
    """Kel's knowledge is frozen from the first notes step on: learn.py refuses to run after this."""
    if not FROZEN.exists():
        procs = json.loads((swe_env.RUNS / "procedures_train.json").read_text(encoding="utf-8"))
        FROZEN.write_text(json.dumps({"procedures": len(procs), "procedure_ids": sorted(p["procedure_id"] for p in procs.values())},
                                     indent=1), encoding="utf-8")


def main(action: str) -> None:
    from check_env import require_pinned

    require_pinned(scored=True)
    swe_env.verify_after_import()
    freeze()
    if action == "queries":
        queries()
    elif action == "K":
        asyncio.run(arm_k())
    elif action == "E":
        arm_e()
    elif action == "controls":
        controls()
    else:
        raise SystemExit("usage: notes.py queries|K|E|controls")


if __name__ == "__main__":
    main(sys.argv[1])
