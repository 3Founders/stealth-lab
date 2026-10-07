"""Blind "right cause" grading by Claude Sonnet subagents (PREREGISTRATION.md section 5).

    .venv/Scripts/python blind.py make  [--part test] [--per-batch 8]   # runs/blind/batchNN.md + key.json + prompts.json
    .venv/Scripts/python blind.py collect                               # runs/blind/gradesNN.json -> runs/right_cause.json

Each task shows the issue, the REAL fix, and the four arms' proposals as S1..S4 in a per-task shuffled order
(stable hash of the seed and the task id). Batch files never contain an arm name, a memory block, or anything
else that identifies the arm; key.json (never shown to a grader) maps S-labels back to arms. A proposal is the
agent's final patch plus the end of its final message (its own statement of the cause). 10% of batches are
graded twice by independent graders (inter-rater agreement is reported).
"""
from __future__ import annotations

import argparse
import json
import random
import re

from common import ARMS, CONFIG, RUNS, clip, load_jsonl, read_json, stable_hash, write_json

BLIND = RUNS / "blind"
PATCH_CHARS = 7000
MESSAGE_CHARS = 1500
GOLD_CHARS = 9000
ARM_WORDS = re.compile(r"\bKel\b|\.stealth\b|\blibrary\.md\b|\bclaims\.md\b|\benterprise tier\b|\bglobal tier\b|"
                       r"\bNotes from previous work\b", re.I)

PROMPT = """You are a strict senior reviewer. Read ONLY the file {path} (no other files, no network, no tools that \
reach outside it). Each task has the issue, the REAL FIX (ground truth diff) and four proposals S1..S4, each written by \
an engineer working in the real repository.

For each proposal decide:
- "root_cause_right": does it identify and act on the real root cause / requirement that the real fix addresses? (true/false)
- "score": 0-10 how close it is to the real fix: correct root cause (most important), the change is equivalent or \
would also fix the issue, right files/functions, no harmful changes. 0 = wrong cause and wrong fix; 5 = right cause, \
partly right fix; 8+ = would essentially work.
- "accept": would a maintainer merge it after at most small nits? (true/false)
An empty proposal is root_cause_right false, score 0, accept false. Judge correctness, not length or polish; proposals \
may differ in style.

Write {out} as {{"<task id>": {{"S1": {{"root_cause_right": bool, "score": n, "accept": bool}}, "S2": ..., "S3": ..., \
"S4": ...}}, ...}} for ALL tasks in the file, then reply with only the number of tasks graded."""


def attempts(part: str) -> dict[str, dict[str, dict]]:
    out = {}
    for arm in ARMS:
        recs = {r["instance_id"]: r for r in load_jsonl(RUNS / f"attempts_{part}_{arm}.jsonl")
                if not r.get("environmental_failure")}
        out[arm] = recs
    return out


def proposal(rec: dict | None) -> str:
    if not rec:
        return "(no proposal)"
    patch = (rec.get("patch") or "").strip()
    msg = ARM_WORDS.sub("[...]", (rec.get("final_message") or "").strip())
    parts = []
    if msg:
        parts.append("Engineer's summary:\n" + clip(msg[-MESSAGE_CHARS:], MESSAGE_CHARS))
    parts.append("Patch:\n```diff\n" + (clip(patch, PATCH_CHARS) if patch else "(empty)") + "\n```")
    return "\n".join(parts)


def order(iid: str) -> list[str]:
    arms = list(ARMS)
    random.Random(stable_hash(CONFIG["split"]["seed"], "blind", iid)).shuffle(arms)
    return arms


def make(part: str, per_batch: int) -> None:
    inst, src = read_json(RUNS / "instances.json"), read_json(RUNS / "grading_source.json")
    att = attempts(part)
    ids = [i for i in read_json(RUNS / "design.json")[part] if all(i in att[a] for a in ARMS)]
    missing = len(read_json(RUNS / "design.json")[part]) - len(ids)
    ids.sort(key=lambda i: stable_hash(CONFIG["split"]["seed"], "batch", i))
    BLIND.mkdir(parents=True, exist_ok=True)
    key, prompts = {}, []
    batches = [ids[k:k + per_batch] for k in range(0, len(ids), per_batch)]
    for n, batch in enumerate(batches, 1):
        lines = []
        for i in batch:
            arms = order(i)
            key[i] = {f"S{k + 1}": arm for k, arm in enumerate(arms)}
            lines += [f"=== TASK {i} | repo {inst[i]['repo']} | {inst[i]['language']}", "--- Issue",
                      clip(inst[i]["problem_statement"], 6000), "--- REAL FIX (ground truth)",
                      clip(src[i]["patch"], GOLD_CHARS)]
            for k, arm in enumerate(arms):
                lines += [f"--- Proposal S{k + 1}", proposal(att[arm].get(i))]
            lines.append("")
        path = BLIND / f"batch{n:02d}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        prompts.append({"batch": n, "prompt": PROMPT.format(path=path, out=BLIND / f"grades{n:02d}.json")})
    # 10% double grading, chosen by hash (fixed before any grade exists)
    doubles = sorted(range(1, len(batches) + 1), key=lambda n: stable_hash(CONFIG["split"]["seed"], "double", str(n)))
    for n in doubles[: max(1, len(batches) // 10)]:
        path = BLIND / f"batch{n:02d}.md"
        prompts.append({"batch": n, "second": True,
                        "prompt": PROMPT.format(path=path, out=BLIND / f"grades{n:02d}_second.json")})
    write_json(BLIND / "key.json", key)
    write_json(BLIND / "prompts.json", prompts)
    print(json.dumps({"tasks": len(ids), "missing_an_arm": missing, "batches": len(batches),
                      "double_graded": len(prompts) - len(batches)}, indent=1))


def collect() -> None:
    key = read_json(BLIND / "key.json")
    out, second, problems = {}, {}, []
    for f in sorted(BLIND.glob("grades*.json")):
        g = read_json(f)
        target = second if f.stem.endswith("_second") else out
        for iid, labels in g.items():
            if iid not in key:
                problems.append(f"{f.name}: unknown task {iid}")
                continue
            for s, v in labels.items():
                if s in key[iid]:
                    target.setdefault(iid, {})[key[iid][s]] = {
                        "root_cause_right": bool(v.get("root_cause_right")), "score": float(v.get("score", 0)),
                        "accept": bool(v.get("accept"))}
    incomplete = [i for i in key if len(out.get(i, {})) != len(ARMS)]
    write_json(RUNS / "right_cause.json", {"primary": out, "second": second})
    print(json.dumps({"graded_tasks": len(out), "incomplete": incomplete[:20], "n_incomplete": len(incomplete),
                      "second_graded_tasks": len(second), "problems": problems[:20]}, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "collect"])
    ap.add_argument("--part", default="test")
    ap.add_argument("--per-batch", type=int, default=8)
    a = ap.parse_args()
    make(a.part, a.per_batch) if a.cmd == "make" else collect()


if __name__ == "__main__":
    main()
