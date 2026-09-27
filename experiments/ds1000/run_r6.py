"""DS-1000 round 6 (PREREGISTRATION_6.md): which of round 5's knowledge flags does the hook need?

    python run_r6.py KH0      # the hook with KNOWLEDGE_RELATED_EXAMPLES and KNOWLEDGE_SUGGESTED_CANDIDATE both off
    python run_r6.py KHnR     # related examples off, suggested candidate on
    python run_r6.py KHnS     # suggested candidate off, related examples on
    python run_r6.py KHr      # both on: round-5 KH again (replication; Kel's own run-to-run noise)

Everything else is round-5 KH exactly (run_r5.py): same tasks, models, agent, budget, workspace, grading, knowledge
(kel_ds1000_r4), claims, system prompt and hook (kel_product_arm.hook_context = run_r5.hook_context);
KNOWLEDGE_VERIFIED_EXAMPLES and the governor on. Only the two flags differ, set per process from the arm name
before any app module is imported. The comparator is round-5 KH (runs5/), which ran with both flags on.
"""
from __future__ import annotations

import os
import sys

ARM_FLAGS = {   # (KNOWLEDGE_RELATED_EXAMPLES, KNOWLEDGE_SUGGESTED_CANDIDATE)
    "KH0": (False, False),
    "KHnR": (False, True),
    "KHnS": (True, False),
    "KHr": (True, True),    # round-5 KH re-run: replication, and Kel's run-to-run noise
}
_arm = next((a for a in sys.argv[1:] if a in ARM_FLAGS), None)
if _arm is None:
    raise SystemExit(f"usage: run_r6.py {{{'|'.join(ARM_FLAGS)}}} [--workers N]")
os.environ["KEL_DS1000_RUNS"] = "runs6_smoke" if os.environ.get("KEL_R6_SMOKE") == "1" else "runs6"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"
os.environ["KNOWLEDGE_VERIFIED_EXAMPLES"] = "true"
os.environ["FIND_WAYS_GOVERNOR"] = "true"
os.environ["KNOWLEDGE_RELATED_EXAMPLES"] = "true" if ARM_FLAGS[_arm][0] else "false"
os.environ["KNOWLEDGE_SUGGESTED_CANDIDATE"] = "true" if ARM_FLAGS[_arm][1] else "false"

import demo_env  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from dataclasses import asdict  # noqa: E402

sys.path.insert(0, str(demo_env.HERE.parent))
import kel_product_arm as kpa  # noqa: E402

import run_r4 as r4  # noqa: E402  (constants and helpers only)
from common import grade_and_record, load_attempts, problems, test_items  # noqa: E402
from models import OPEN_MODELS  # noqa: E402

os.environ["KEL_DS1000_RUNS"] = demo_env.RUNS.name
EPISODES = demo_env.RUNS / "episodes.jsonl"
_lock = threading.Lock()


def claims_for(model: str) -> str | None:
    """Round 4's survey_repo output for this model, as in round 5."""
    path = demo_env.HERE / "runs4" / f"claims_{model}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def run_arm(arm: str, models: list[str], workers: int) -> None:
    design = demo_env.RUNS / "design.json"
    if not design.exists():
        shutil.copyfile(demo_env.HERE / "runs5" / "design.json", design)
    items = test_items()
    done = {(r["problem_id"], r["model"], r["arm"]) for r in load_attempts() if not r.get("call_error")}
    bridge = kpa.KelBridge(demo_env.DEMO_DSN)
    agents = {m: kpa.make_agent(r4.client(), m, r4.MAX_STEPS, r4.TEMPERATURE, bridge, r4.TOOL_MAX_CHARS,
                                system=r4.SYSTEM, kel_tools=True, governed=True) for m in models}
    jobs = [(t["problem_id"], m) for t in items for m in models if (t["problem_id"], m, arm) not in done]
    print(f"arm {arm}: {len(jobs)} episodes to run", flush=True)

    def work(job):
        pid, m = job
        problem = problems()[pid]["prompt"]
        claims = claims_for(m)
        root = r4.workspace(claims)
        t0 = time.time()
        hook = None
        try:
            sb = kpa.make_sandbox(root)
            ctx_text, hook = kpa.hook_context(bridge, problem, claims, session=root + "#hook")
            if ctx_text:   # Claude Code adds a hook's additionalContext right after the user's prompt
                problem = f"{problem}\n\n{ctx_text}"
            run = agents[m].run({"instance_id": pid, "repo": "ds1000-workspace", "problem_statement": problem}, sb, arm)
            sol = os.path.join(root, "solution.py")
            if os.path.isfile(sol):
                code, source = open(sol, encoding="utf-8").read(), "solution.py"
            else:
                code, source = "", "final_message"
                if "```" in (run.final_message or ""):
                    from evaluate import extract_code
                    code = extract_code(run.final_message)
            error = run.error if (run.error and not code) else None
            rec = grade_and_record(pid, m, "agent-loop", arm, f"```python\n{code}\n```" if code else "",
                                   run.usage.prompt_tokens, run.usage.completion_tokens, False,
                                   int((time.time() - t0) * 1000), error=error,
                                   prompt_sha=kpa.sha(agents[m]._system + problem))
            ep = {"problem_id": pid, "model": m, "arm": arm, "steps": run.steps, "stop_reason": run.stop_reason,
                  "tool_calls": run.tool_calls, "usage": asdict(run.usage), "solution_source": source,
                  "kel_calls": sb.kel_log, "hook": hook, "hook_text_sha256": kpa.sha(ctx_text),
                  "error": run.error, "gold_pass": rec["gold_pass"]}
        except Exception as exc:  # noqa: BLE001 -- infrastructure: a call error, retried next invocation
            rec = grade_and_record(pid, m, "agent-loop", arm, "", 0, 0, False, int((time.time() - t0) * 1000),
                                   error=f"{type(exc).__name__}: {str(exc)[:300]}")
            ep = {"problem_id": pid, "model": m, "arm": arm, "error": rec["call_error"]}
        finally:
            shutil.rmtree(root, ignore_errors=True)
        with _lock, open(EPISODES, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ep) + "\n")
        print(f"{pid:>4} {m:<16} gold={rec['gold_pass']!s:<5} steps={ep.get('steps')} "
              f"hook={(hook or {}).get('outcome')}/{(hook or {}).get('context_chars')} "
              f"{(rec['call_error'] or '')[:60]}", flush=True)

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, jobs))
    finally:
        bridge.close()
    rows = [r for r in load_attempts() if r["arm"] == arm and not r.get("call_error")]
    for m in models:
        mine = [r for r in rows if r["model"] == m]
        print(f"{m:<16} gold {sum(r['gold_pass'] for r in mine)}/{len(mine)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arm", choices=list(ARM_FLAGS))
    ap.add_argument("--models", default=",".join(OPEN_MODELS))
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    demo_env.verify_after_import()
    from app.config import settings

    related, suggested = ARM_FLAGS[a.arm]
    assert settings.knowledge_verified_examples and settings.find_ways_governor
    assert bool(settings.knowledge_related_examples) == related
    assert bool(settings.knowledge_suggested_candidate) == suggested
    assert str(settings.database_url).endswith("/kel_ds1000_r4")
    run_arm(a.arm, a.models.split(","), a.workers)


if __name__ == "__main__":
    main()
