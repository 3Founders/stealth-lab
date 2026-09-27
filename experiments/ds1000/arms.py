"""Notes for the comparison arms (preregistered), built from fit-set knowledge only.

  C  oracle   the Procedure extracted from the test problem's OWN family origin (transfer only)
  D  placebo  a Procedure from ANOTHER family -- another library when one exists -- chosen by hash
  E  RAG      the top-1 fit problem by BM25 (k1=1.5, b=0.75, lowercase \\w+ tokens) among fit
              problems with a verified solution, shown with that solution's code

    python arms.py      # writes runs/notes_C.json, notes_D.json, notes_E.json
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections import Counter

import demo_env

from common import fit_items, load_attempts, problems, test_items
from kel_setup import PREFERENCE, procedure_text

SEED = "ds1000-kel-v1"
K1, B = 1.5, 0.75


def h(*parts) -> str:
    return hashlib.sha256("\x1f".join(map(str, (SEED, *parts))).encode()).hexdigest()


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class BM25:
    def __init__(self, docs: dict[str, str]):
        self.ids = list(docs)
        self.tf = {i: Counter(tokens(d)) for i, d in docs.items()}
        self.len = {i: sum(tf.values()) for i, tf in self.tf.items()}
        self.avg = sum(self.len.values()) / len(self.len)
        df = Counter(t for tf in self.tf.values() for t in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def top(self, query: str) -> str:
        q = tokens(query)

        def score(i):
            tf, dl = self.tf[i], self.len[i]
            return sum(self.idf.get(t, 0) * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * dl / self.avg))
                       for t in q if t in tf)
        return max(self.ids, key=lambda i: (score(i), -int(i)))


async def main() -> None:
    demo_env.verify_after_import()
    from app.db.session import create_pool

    procs = json.loads((demo_env.RUNS / "procedures_fit.json").read_text(encoding="utf-8"))
    fit = {f["problem_id"]: f for f in fit_items()}
    origin_of = {f["family"]: f["problem_id"] for f in fit.values()}
    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=2)
    try:
        text = {pid: await procedure_text(pool, p["procedure_id"]) for pid, p in procs.items()}
    finally:
        await pool.close()

    notes_c, notes_d, notes_e = {}, {}, {}
    wins: dict[str, dict] = {}
    rag_attempts = load_attempts()
    if demo_env.RUNS != demo_env.ROUND1_RUNS:
        # Kel's library holds round 1's fit problems too, so plain RAG searches them as well (same pool)
        r1 = json.loads((demo_env.ROUND1_RUNS / "design.json").read_text(encoding="utf-8"))["fit"]
        fit.update({f["problem_id"]: f for f in r1})
        rag_attempts += [json.loads(l) for l in (demo_env.ROUND1_RUNS / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
                         if l.strip()]
    for r in rag_attempts:
        if r["arm"] == "fit_raw" and r["gold_pass"]:
            best = wins.get(r["problem_id"])
            if best is None or PREFERENCE.index(r["model"]) < PREFERENCE.index(best["model"]):
                wins[r["problem_id"]] = r
    bm25 = BM25({pid: problems()[pid]["prompt"] for pid in wins})

    for t in test_items():
        pid = t["problem_id"]
        if t["role"] == "transfer":
            o = origin_of[t["family"]]
            if o in procs:
                notes_c[pid] = {"text": text[o], "ref": procs[o]["procedure_id"], "source_problem": o}
        others = [f for f in procs if fit[f]["family"] != t["family"]]
        other_lib = [f for f in others if fit[f]["library"] != t["library"]] or others
        pick = min(other_lib, key=lambda f: h("placebo", pid, f))
        notes_d[pid] = {"text": text[pick], "ref": procs[pick]["procedure_id"], "source_problem": pick}
        top = bm25.top(problems()[pid]["prompt"])
        block = (f"Similar past problem:\n{problems()[top]['prompt'][:1200]}\n\n"
                 f"Its verified solution:\n```python\n{wins[top]['code']}\n```")
        notes_e[pid] = {"text": block, "ref": f"rag:{top}", "source_problem": top,
                        "same_family": fit[top]["family"] == t["family"]}

    for name, notes in (("C", notes_c), ("D", notes_d), ("E", notes_e)):
        (demo_env.RUNS / f"notes_{name}.json").write_text(json.dumps(notes, indent=1), encoding="utf-8")
    tr = [t for t in test_items() if t["role"] == "transfer"]
    print(f"C oracle notes: {len(notes_c)} of {len(tr)} transfer problems")
    print(f"D placebo notes: {len(notes_d)}; E RAG notes: {len(notes_e)}, "
          f"RAG top-1 from own family (transfer): {sum(notes_e[t['problem_id']]['same_family'] for t in tr)} of {len(tr)}")


if __name__ == "__main__":
    asyncio.run(main())
