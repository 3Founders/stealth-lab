"""Re-grade every saved page answer (Design2Code, WebSight) with the layout grader (html_grade2.py), both arms.

    python experiments/abtest/regrade_pages.py [--runs phase2,final1014-p2] > .local/results/regrade_pages.jsonl

The answer is not in the results rows; it is in the arm's workspace history: the runner commits the workspace as
"after <task_id>" when a task ends, so `git show <that commit>:index.html` is exactly what was graded. With several
commits for one task (runs on different days), the one closest after the row's started_at is taken.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE / ".local"
sys.path.insert(0, str(HERE))

import html_grade2  # noqa: E402
import page_task  # noqa: E402


def commits(ws: Path) -> dict[str, list[tuple[dt.datetime, str]]]:
    out: dict[str, list[tuple[dt.datetime, str]]] = {}
    log = subprocess.run(["git", "-C", str(ws), "log", "--format=%H|%cI|%s"], capture_output=True, text=True).stdout
    for line in log.splitlines():
        h, when, subj = line.split("|", 2)
        if subj.startswith("after "):
            out.setdefault(subj[6:].strip(), []).append((dt.datetime.fromisoformat(when), h))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="phase2,final1014-p2")
    args = ap.parse_args()
    hist = {}
    for run in args.runs.split(","):
        for line in (LOCAL / "results" / f"{run}.jsonl").read_text(encoding="utf-8").splitlines():
            x = json.loads(line) if line.strip() else None
            if not x or x.get("status") != "done" or x["suite"] not in ("d2c", "websight"):
                continue
            ws = LOCAL / "ws" / x["arm"] / x["suite"]
            if ws not in hist:
                hist[ws] = commits(ws)
            start = dt.datetime.fromisoformat(x["started_at"])
            cands = sorted((c for c in hist[ws].get(x["task_id"], []) if c[0] >= start), key=lambda c: c[0])
            if not cands:
                print(json.dumps({"run": run, "task_id": x["task_id"], "arm": x["arm"], "error": "no saved answer"}), flush=True)
                continue
            html = subprocess.run(["git", "-C", str(ws), "show", f"{cands[0][1]}:index.html"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace").stdout
            task = {"task_id": x["task_id"], "suite": x["suite"], "page": x["task_id"].split("-", 1)[1]}
            ref, _ = page_task.reference(task["suite"], task["page"])
            s = html_grade2.scores(ref, html)
            print(json.dumps({"run": run, "task_id": x["task_id"], "suite": x["suite"], "arm": x["arm"],
                              "v1_resolved": x.get("resolved"), "v2_resolved": html_grade2.graded_ok(s), "v2": s,
                              "cost": x.get("total_cost_usd"), "seconds": x.get("seconds")}), flush=True)


if __name__ == "__main__":
    main()
