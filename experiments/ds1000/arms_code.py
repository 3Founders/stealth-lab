"""'Procedure + verified code' notes -- what find_ways would return if Kel kept the verified
solution a Procedure was extracted from (today it deliberately drops it).

  Bc  Kel's retrieved Procedure (arm B's retrieval, unchanged) + the verified code of the fit
      solution that Procedure was extracted from
  Cc  the task's own family-origin Procedure (arm C) + that origin's verified code

    python arms_code.py [--runs DIR]     # writes notes_Bc.json, notes_Cc.json in the runs dir
"""
from __future__ import annotations

import json

import demo_env


def code_block(procedure_text: str, code: str) -> str:
    return f"{procedure_text}\nVerified solution of that past problem:\n```python\n{code}\n```"


def verified_code_by_procedure() -> dict[str, str]:
    """procedure_id -> the code of the verified attempt it was extracted from (every runs dir)."""
    from kel_setup import PREFERENCE

    out: dict[str, str] = {}
    for runs in dict.fromkeys((demo_env.HERE / "runs", demo_env.RUNS)):     # round 1, and this round's dir
        procs_path = runs / "procedures_fit.json"
        if not procs_path.exists():
            continue
        procs = json.loads(procs_path.read_text(encoding="utf-8"))
        attempts = [json.loads(l) for l in (runs / "attempts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        wins: dict[str, dict] = {}
        for r in attempts:
            if r["arm"] == "fit_raw" and r["gold_pass"]:
                best = wins.get(r["problem_id"])
                if best is None or PREFERENCE.index(r["model"]) < PREFERENCE.index(best["model"]):
                    wins[r["problem_id"]] = r
        for pid, p in procs.items():
            assert wins[pid]["model"] == p["from_model"], (pid, "extraction source mismatch")
            out[p["procedure_id"]] = wins[pid]["code"]
    return out


def source_problem_by_procedure() -> dict[str, str]:
    """procedure_id -> the fit problem it was extracted from (every runs dir)."""
    out: dict[str, str] = {}
    for runs in dict.fromkeys((demo_env.HERE / "runs", demo_env.RUNS)):
        path = runs / "procedures_fit.json"
        if path.exists():
            out.update({p["procedure_id"]: pid for pid, p in json.loads(path.read_text(encoding="utf-8")).items()})
    return out


def main(pairs: list[tuple[str, str]] | None = None) -> None:
    from common import problems

    code_of = verified_code_by_procedure()
    source_of = source_problem_by_procedure()
    pairs = pairs or [("B", "Bc"), ("C", "Cc")]
    b_path = demo_env.RUNS / f"notes_{pairs[0][0]}.json"
    if b_path.exists():
        # Bw: Kel's retrieval, rendered exactly like arm E (worked example, no Procedure text)
        notes = json.loads(b_path.read_text(encoding="utf-8"))
        bw = {}
        for pid, n in notes.items():
            if n.get("ref") and n.get("text"):
                src = source_of[n["ref"]]
                bw[pid] = {"ref": n["ref"], "source_problem": src,
                           "text": (f"Similar past problem:\n{problems()[src]['prompt'][:1200]}\n\n"
                                    f"Its verified solution:\n```python\n{code_of[n['ref']]}\n```")}
        (demo_env.RUNS / f"notes_{pairs[0][0]}w.json").write_text(json.dumps(bw, indent=1), encoding="utf-8")
        print(f"Bw: {len(bw)} notes")
    for src, dst in pairs:
        path = demo_env.RUNS / f"notes_{src}.json"
        if not path.exists():
            continue
        notes = json.loads(path.read_text(encoding="utf-8"))
        out = {pid: {**n, "text": code_block(n["text"], code_of[n["ref"]])}
               for pid, n in notes.items() if n.get("ref") and n.get("text")}
        (demo_env.RUNS / f"notes_{dst}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"{dst}: {len(out)} notes")


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    main([tuple(a.split(":")) for a in args] if args else None)      # e.g. B_raw:Bc_raw B_q:Bc_q
