"""Test 2: gpt-oss-120b (General Compute) proposes a fix for each unseen task, alone and with Kel's top-3 knowledge."""
import concurrent.futures as cf
import json
import random
import sys
import time

import httpx

D, BASE, KEY, N = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
MODEL = "gpt-oss-120b"
items = json.load(open(f"{D}/retrieved.json", encoding="utf-8"))
random.Random(5).shuffle(items)
items = items[:N]

INSTR = ("You are fixing a reported issue in the open-source repository {repo} ({lang}). You cannot see the code, so "
         "reason from the issue and your knowledge of the project. Reply in markdown with exactly:\n"
         "## Root cause\n## Fix (a unified diff with your best-guess file paths and code)\n## How to verify\n")


def kel_block(hits):
    lines = ["# Knowledge from Kel: fixes of SIMILAR problems in OTHER projects (adapt, do not copy)"]
    for h in hits:
        lines.append(f"\n- Goal: {h['goal']}\n  Way: {h.get('procedure')}")
        lines += [f"  step: {s}" for s in h.get("steps", []) if s]
        lines += [f"  pitfall: {p}" for p in h.get("pitfalls", []) if p]
    return "\n".join(lines)


def call(prompt):
    for attempt in range(6):
        try:
            r = httpx.post(f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {KEY}"}, timeout=300,
                           json={"model": MODEL, "temperature": 0, "max_tokens": 6000,
                                 "messages": [{"role": "user", "content": prompt}]})
            if r.status_code == 200:
                j = r.json()
                return j["choices"][0]["message"]["content"] or "", j.get("usage", {})
            err = f"{r.status_code} {r.text[:120]}"
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        time.sleep(10 * (attempt + 1))
    return f"ERROR: {err}", {}


def job(it):
    base = INSTR.format(repo=it["repo"], lang=it["language"]) + f"\n# Issue\n{it['issue']}\n"
    alone, u1 = call(base)
    kel, u2 = call(base + "\n" + kel_block(it["kel"]) + "\n")
    return {"id": it["id"], "alone": alone, "kel": kel, "usage": [u1, u2]}


with cf.ThreadPoolExecutor(6) as ex:
    out = list(ex.map(job, items))
json.dump(out, open(f"{D}/generated.json", "w", encoding="utf-8"), indent=1)
print("generated:", len(out), "errors:", sum(o["alone"].startswith("ERROR") or o["kel"].startswith("ERROR") for o in out),
      "tokens:", sum((u.get("total_tokens") or 0) for o in out for u in o["usage"]), flush=True)
