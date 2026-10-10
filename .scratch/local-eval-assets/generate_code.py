"""The 'with Kel' arm again, now including the REAL diffs of the top-2 earlier fixes from the same repository
(the verified-solution blueprint). The 'alone' answers are reused from generated_repo.json."""
import concurrent.futures as cf, json, sys, time, httpx
D, BASE, KEY = sys.argv[1], sys.argv[2], sys.argv[3]
prev = {g["id"]: g for g in json.load(open(f"{D}/generated_repo.json", encoding="utf-8"))
        if not g["alone"].startswith("ERROR") and not g["kel"].startswith("ERROR")}
items = [it for it in json.load(open(f"{D}/retrieved_repo_code.json", encoding="utf-8")) if it["id"] in prev]
INSTR = ("You are fixing a reported issue in the open-source repository {repo} ({lang}). You cannot see the code, so "
         "reason from the issue and your knowledge of the project. Reply in markdown with exactly:\n"
         "## Root cause\n## Fix (a unified diff with your best-guess file paths and code)\n## How to verify\n")
def kel_block(hits):
    out = ["# Knowledge from Kel: earlier VERIFIED fixes made in THIS repository (real diffs; adapt to this issue, do not copy)"]
    for n, h in enumerate(hits):
        out.append(f"\n## Earlier fix {n + 1}: {h['goal']}\nWay: {h.get('procedure')}")
        out += [f"- {s}" for s in h.get("steps", []) if s]
        if n < 2 and h.get("diff"):
            out.append("Real diff of that fix:\n```diff\n" + h["diff"] + "\n```")
    return "\n".join(out)
def call(prompt):
    for attempt in range(6):
        try:
            r = httpx.post(f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {KEY}"}, timeout=300,
                           json={"model": "gpt-oss-120b", "temperature": 0, "max_tokens": 6000, "messages": [{"role": "user", "content": prompt}]})
            if r.status_code == 200:
                j = r.json(); return j["choices"][0]["message"]["content"] or "", j.get("usage", {})
            err = f"{r.status_code} {r.text[:100]}"
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        time.sleep(10 * (attempt + 1))
    return f"ERROR: {err}", {}
def job(it):
    base = INSTR.format(repo=it["repo"], lang=it["language"]) + f"\n# Issue\n{it['issue']}\n"
    kel, u = call(base + "\n" + kel_block(it["kel"]) + "\n")
    return {"id": it["id"], "alone": prev[it["id"]]["alone"], "kel": kel, "usage": [u]}
with cf.ThreadPoolExecutor(6) as ex:
    out = list(ex.map(job, items))
json.dump(out, open(f"{D}/generated_code.json", "w", encoding="utf-8"), indent=1)
print("generated:", len(out), "errors:", sum(o["kel"].startswith("ERROR") for o in out), "tokens:", sum((u.get("total_tokens") or 0) for o in out for u in o["usage"]), flush=True)
