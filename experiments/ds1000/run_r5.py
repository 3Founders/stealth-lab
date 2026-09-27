"""DS-1000 round 5 (PREREGISTRATION_5.md): do the round-4 fixes recover the knowledge the product lost?

    python run_r5.py AG5      # agent, no Kel -- a fresh, time-matched baseline
    python run_r5.py KP5      # Kel the product's way (as round-4 KP), with the fixes on and the governor active
    python run_r5.py KH       # Kel with the Claude Code knowledge hook: the task is looked up BEFORE the agent
                              # starts and the hook's own text is added to its prompt; find_ways stays available

Same tasks, models, agent, budget, workspace and grading as round 4 (run_r4.py). Settings forced here:
runs5, kel_ds1000_r4 (round 3's knowledge + provenance backfill, unchanged since round 4),
KNOWLEDGE_VERIFIED_EXAMPLES / KNOWLEDGE_RELATED_EXAMPLES / KNOWLEDGE_SUGGESTED_CANDIDATE = true, governor on
(each episode is its own MCP session; the hook's lookup is a separate session, as a hook process is).
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs5_smoke" if os.environ.get("KEL_R5_SMOKE") == "1" else "runs5"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"
for _flag in ("KNOWLEDGE_VERIFIED_EXAMPLES", "KNOWLEDGE_RELATED_EXAMPLES", "KNOWLEDGE_SUGGESTED_CANDIDATE",
              "FIND_WAYS_GOVERNOR"):
    os.environ[_flag] = "true"

import demo_env  # noqa: E402  (isolates the process; RUNS is fixed here, before run_r4 is imported)

import argparse  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from dataclasses import asdict  # noqa: E402

sys.path.insert(0, str(demo_env.HERE.parent))
import kel_product_arm as kpa  # noqa: E402

import run_r4 as r4  # noqa: E402  (constants and helpers only; its env writes happen after demo_env is fixed)
from common import grade_and_record, load_attempts, problems, test_items  # noqa: E402
from models import OPEN_MODELS  # noqa: E402

os.environ["KEL_DS1000_RUNS"] = demo_env.RUNS.name
EPISODES = demo_env.RUNS / "episodes.jsonl"
HOOK_FORMAT = demo_env.HERE / "hook_format.mjs"
_lock = threading.Lock()


def claims_for(model: str) -> str | None:
    """Round 4's survey_repo output for this model (same workspace; surveyed once, as a user would keep it)."""
    path = demo_env.HERE / "runs4" / f"claims_{model}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def hook_context(bridge: kpa.KelBridge, prompt: str, claims: str, session: str) -> tuple[str, dict]:
    """Exactly what the shipped hook does: one find_ways lookup of the user's prompt (its own MCP session),
    formatted by the hook's own code (packaging/npm/lib/hook.mjs via hook_format.mjs)."""
    reply = bridge.find_ways(prompt.strip()[:1500], claims or "", session=session)
    fmt = subprocess.run(["node", str(HOOK_FORMAT)], input=reply, capture_output=True, text=True, encoding="utf-8",
                         timeout=60, check=True).stdout
    try:
        d = json.loads(reply)
    except ValueError:
        d = {}
    summary = {"outcome": d.get("outcome"), "related_examples": len(d.get("related_examples") or []),
               "suggested": bool(d.get("suggested")), "procedures": len(d.get("procedures") or []),
               "context_chars": len(fmt)}
    return fmt, summary


def run_arm(arm: str, models: list[str], workers: int) -> None:
    items = test_items()
    done = {(r["problem_id"], r["model"], r["arm"]) for r in load_attempts() if not r.get("call_error")}
    bridge = kpa.KelBridge(demo_env.DEMO_DSN) if arm in ("KP5", "KH") else None
    agents = {m: kpa.make_agent(r4.client(), m, r4.MAX_STEPS, r4.TEMPERATURE, bridge, r4.TOOL_MAX_CHARS,
                                system=r4.SYSTEM, kel_tools=(arm != "AG5"), governed=True) for m in models}
    jobs = [(t["problem_id"], m) for t in items for m in models if (t["problem_id"], m, arm) not in done]
    print(f"arm {arm}: {len(jobs)} episodes to run", flush=True)

    def work(job):
        pid, m = job
        problem = problems()[pid]["prompt"]
        claims = claims_for(m) if arm in ("KP5", "KH") else None
        root = r4.workspace(claims)
        t0 = time.time()
        hook = None
        try:
            sb = kpa.make_sandbox(root)
            if arm == "KP5":
                problem = kpa.task_prompt(problem, r4.KP_ADAPTATIONS)
            elif arm == "KH":
                ctx_text, hook = hook_context(bridge, problem, claims, session=root + "#hook")
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
                  "kel_calls": sb.kel_log, "hook": hook, "error": run.error, "gold_pass": rec["gold_pass"]}
        except Exception as exc:  # noqa: BLE001 -- infrastructure: a call error, retried next invocation
            rec = grade_and_record(pid, m, "agent-loop", arm, "", 0, 0, False, int((time.time() - t0) * 1000),
                                   error=f"{type(exc).__name__}: {str(exc)[:300]}")
            ep = {"problem_id": pid, "model": m, "arm": arm, "error": rec["call_error"]}
        finally:
            shutil.rmtree(root, ignore_errors=True)
        with _lock, open(EPISODES, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ep) + "\n")
        kel = [c.get("outcome") or c.get("governor") for c in ep.get("kel_calls") or [] if c["tool"] == "find_ways"]
        print(f"{pid:>4} {m:<16} gold={rec['gold_pass']!s:<5} steps={ep.get('steps')} "
              f"hook={(hook or {}).get('outcome')}/{(hook or {}).get('context_chars')} kel={kel} "
              f"{(rec['call_error'] or '')[:60]}", flush=True)

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, jobs))
    finally:
        if bridge is not None:
            bridge.close()
    rows = [r for r in load_attempts() if r["arm"] == arm and not r.get("call_error")]
    for m in models:
        mine = [r for r in rows if r["model"] == m]
        print(f"{m:<16} gold {sum(r['gold_pass'] for r in mine)}/{len(mine)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arm", choices=["AG5", "KP5", "KH"])
    ap.add_argument("--models", default=",".join(OPEN_MODELS))
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    demo_env.verify_after_import()
    from app.config import settings

    assert (settings.knowledge_verified_examples and settings.knowledge_related_examples
            and settings.knowledge_suggested_candidate and settings.find_ways_governor)
    assert str(settings.database_url).endswith("/kel_ds1000_r4")
    run_arm(a.arm, a.models.split(","), a.workers)


if __name__ == "__main__":
    main()
