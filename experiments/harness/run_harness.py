"""
§40 harness runner — SYNTHETIC FIXTURES ONLY until CORE-A lands 1.7.

Adapts experiments/swebench_pro/run_graph_experiment.py's proven loop shape
without its machinery: no DB, no docker, no API — the scripted arms produce
deterministic episodes so scoring/statistics/scoreboard are exercisable
end-to-end tonight. The seams that get real later:

  - arms come from scripted_arms.build_agents() behind the AgentAdapter
    protocol; frontier adapters slot in unchanged;
  - arm C talks through mcp_surface.McpSurface; swap StubSurface for the MCP
    client and nothing else moves.

Resumable like the reference: rows APPEND to JSONL; a task is skipped iff a
prior row holds VALID episodes from all arms (or was excluded); rows with an
`error` key were transient harness failures and ARE retried. A crash costs
at most the task in flight, never the sweep.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scoreboard  # noqa: E402
import scripted_arms  # noqa: E402


def load_done(path: Path) -> set[str]:
    """Tasks needing no re-run. Mirrors the reference rule: only rows with
    every arm valid count as done; `error` rows retry; there is no
    gold-exclusion concept in the synthetic phase yet."""
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not rec.get("task_id") or rec.get("error"):
            continue
        if all(isinstance(rec.get(a), dict) and rec[a].get("valid", True)
               for a in scripted_arms.ARMS):
            done.add(rec["task_id"])
    return done


def run_task(task: dict, agents: dict) -> dict:
    # Additive (evaluation-suite Phase 7): run_id distinguishes THIS
    # execution of task_id from a later resumed/re-run one -- task_id
    # alone is stable across resumes (that's the point of load_done()'s
    # skip logic); run_id is not, and is what a caller correlating rows
    # across a resumed multi-day sweep actually needs. Scoreboard/
    # mcnemar_power read rows by task_id/arm/valid/resolved/etc, never by
    # the full key set, so this is safe to add without touching either.
    rec: dict = {"task_id": task["task_id"], "run_id": uuid.uuid4().hex[:12]}
    for arm in scripted_arms.ARMS:
        ep = agents[arm].run(task)
        ep.setdefault("arm", arm)
        rec[arm] = ep
        print(f"      . {arm} {'PASS' if ep['resolved'] else 'fail'} "
              f"tok={ep['tokens_in'] + ep['tokens_out']:,} "
              f"tools={ep['tool_calls']}", flush=True)
    return rec


def build_arg_parser() -> argparse.ArgumentParser:
    """Split out for tests (reference build_arg_parser pattern)."""
    ap = argparse.ArgumentParser(description="spec 40 three-arm harness "
                                             "(synthetic fixtures)")
    ap.add_argument("--fixtures-dir", default=str(HERE / "fixtures"))
    ap.add_argument("--out",
                    default=str(HERE / "harness_results.jsonl"))
    ap.add_argument("--task-ids", default=None,
                    help="comma-separated subset; default all fixture tasks")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    fixtures = Path(args.fixtures_dir)
    out = Path(args.out)

    tasks = json.loads((fixtures / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    wanted = None
    if args.task_ids:
        wanted = {t.strip() for t in args.task_ids.split(",") if t.strip()}
    picked = [t for t in tasks if wanted is None or t["task_id"] in wanted]

    done = load_done(out)
    print(f"spec-40 harness (SYNTHETIC fixtures — outcomes are pipeline "
          f"exercises, NOT findings)")
    print(f"{len(picked)} tasks selected, {len(done)} already done")

    agents = scripted_arms.build_agents(fixtures)
    for i, task in enumerate(picked, 1):
        tid = task["task_id"]
        if tid in done:
            print(f"[{i}/{len(picked)}] skip (done) {tid}")
            continue
        print(f"\n[{i}/{len(picked)}] {tid} ({task['domain']}"
              f"{', unseen' if task.get('unseen') else ''})", flush=True)
        try:
            rec = run_task(task, agents)
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            rec = {"task_id": tid, "error": f"{type(exc).__name__}: {exc}",
                   "traceback": tb[-2000:]}
            print(f"    error: {rec['error']}", flush=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        time.sleep(0)  # keep the loop shape honest for the async successor

    text, _detail = scoreboard.build_summary([out], fixtures)
    print("\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
