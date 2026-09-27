"""Round-2 re-measure with production-like knowledge (runs2pl): how much did the experiment's
retrieval gaps cost Kel? Same 124 round-2 test problems, same models, same grading.

Arms (open models): A, E (plain RAG), B_old / Bc_old (round 2 as run: no embeddings, exact-name
identity, hard-coded library edges, raw problem text as query), and on the production-like
knowledge base: B_raw / Bc_raw (raw problem text) and B_q / Bc_q (agent-written request).

    python remeasure.py      # writes <runs>/remeasure.json
"""
from __future__ import annotations

import json
from collections import Counter

import demo_env

from analyze import paired
from common import load_attempts, test_items
from models import OPEN_MODELS


def main() -> None:
    tasks = test_items()
    transfer = [t for t in tasks if t["role"] == "transfer"]
    control = [t for t in tasks if t["role"] == "control"]
    att = {(r["problem_id"], r["model"], r["arm"]): r for r in load_attempts() if not r.get("call_error")}
    pt = [{**t, "problem_id": f"{t['problem_id']}|{m}"} for t in transfer for m in OPEN_MODELS]
    pg = lambda arm: {f"{pid}|{m}": r["gold_pass"] for (pid, m, a), r in att.items() if a == arm}
    gold = lambda m, arm: {pid: r["gold_pass"] for (pid, mm, a), r in att.items() if mm == m and a == arm}

    old = json.loads((demo_env.ROUND1_RUNS.parent / "runs2" / "notes_B.json").read_text(encoding="utf-8"))
    fam_of = {}
    for d in ("runs", "runs2"):
        for f in json.loads((demo_env.HERE / d / "design.json").read_text(encoding="utf-8"))["fit"]:
            fam_of[f["problem_id"]] = f["family"]

    def retrieval(notes: dict, src_of: dict) -> dict:
        given = lambda role: [t for t in role if notes.get(t["problem_id"], {}).get("ref")]
        right = sum(1 for t in given(transfer) if fam_of.get(src_of.get(notes[t["problem_id"]]["ref"])) == t["family"])
        return {"transfer_given": len(given(transfer)), "transfer_from_own_family": right, "of": len(transfer),
                "control_given": len(given(control)), "control_of": len(control),
                "paths": dict(Counter(" -> ".join(p["outcome"] for p in v["path"]) for v in notes.values()))}

    src_new = {p["procedure_id"]: pid for pid, p in json.loads(
        (demo_env.RUNS / "procedures_fit.json").read_text(encoding="utf-8")).items()}
    src_old = {p["procedure_id"]: pid for d in ("runs", "runs2") for pid, p in json.loads(
        (demo_env.HERE / d / "procedures_fit.json").read_text(encoding="utf-8")).items()}
    report = {"retrieval": {
        "old (round 2 as run)": retrieval(old, src_old),
        "prodlike, raw query": retrieval(json.loads((demo_env.RUNS / "notes_B_raw.json").read_text(encoding="utf-8")), src_new),
        "prodlike, agent query": retrieval(json.loads((demo_env.RUNS / "notes_B_q.json").read_text(encoding="utf-8")), src_new),
    }}
    pairs = {"Bc_old-A": ("A", "Bc_old"), "Bc_raw-A": ("A", "Bc_raw"), "Bc_q-A": ("A", "Bc_q"),
             "B_q-A": ("A", "B_q"), "E-A": ("A", "E"), "Bc_q-E": ("E", "Bc_q"), "Bc_q-Bc_old": ("Bc_old", "Bc_q"),
             "Bc_raw-Bc_old": ("Bc_old", "Bc_raw")}
    report["pooled_transfer"] = {k: paired(pt, pg(x), pg(y)) for k, (x, y) in pairs.items()}
    report["per_model_transfer"] = {m: {k: paired(transfer, gold(m, x), gold(m, y)) for k, (x, y) in pairs.items()}
                                    for m in OPEN_MODELS}
    report["control_Bc_q-A"] = {m: paired(control, gold(m, "A"), gold(m, "Bc_q")) for m in OPEN_MODELS}
    (demo_env.RUNS / "remeasure.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    fmt = lambda d: (f"{d['x_rate']}->{d['y_rate']} d={d['delta']:+} CI{d['ci95']} +{d['gained']}/-{d['lost']} "
                     f"p={d['mcnemar_p']}") if d.get("n") else "n/a"
    for k, v in report["retrieval"].items():
        print(f"{k:<24} notes on {v['transfer_given']}/{v['of']} related ({v['transfer_from_own_family']} own family), "
              f"{v['control_given']}/{v['control_of']} controls")
    for k, v in report["pooled_transfer"].items():
        print(f"  {k:<14} {fmt(v)}")


if __name__ == "__main__":
    main()
