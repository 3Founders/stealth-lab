"""DS-1000 round 4 (PREREGISTRATION_4.md): Kel used the way the product is used, on round 3's held-out tasks.

    python run_r4.py survey           # once per model: the survey_repo workflow writes the workspace's .stealth/claims.md
    python run_r4.py AG               # agent loop, no Kel (run first: KN reuses it where it has no note)
    python run_r4.py KN               # agent loop + round 3's K note in the prompt (content, pasted)
    python run_r4.py KP               # agent loop + Kel as the product is used (find_ways tool, .stealth/)

Every arm: the SAME agent (app.execution.coding_agent.Agent, temperature 0, ROUND4_MAX_STEPS tool calls,
the same system prompt and workspace), graded by the unchanged DS-1000 harness on `solution.py`
(or, if the model never wrote it, the last ```python block of its final message -- same rule for every arm).
Graded attempts go to runs4/attempts.jsonl (arms AG/KN/KP); per-episode detail to runs4/episodes.jsonl.
Settings are forced here, not read from the shell: runs4, kel_ds1000_r4, KNOWLEDGE_VERIFIED_EXAMPLES=true.
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs4_smoke" if os.environ.get("KEL_R4_SMOKE") == "1" else "runs4"   # smoke never touches runs4
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"
os.environ["KNOWLEDGE_VERIFIED_EXAMPLES"] = "true"

import demo_env  # noqa: E402  (isolates the process to kel_ds1000_r4)

import argparse  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from dataclasses import asdict  # noqa: E402

sys.path.insert(0, str(demo_env.HERE.parent))
import kel_product_arm as kpa  # noqa: E402

from common import append, grade_and_record, load_attempts, problems, test_items  # noqa: E402
from models import NOTES_INTRO, OPEN_MODELS  # noqa: E402

MAX_STEPS = 20
TEMPERATURE = 0.0
TOOL_MAX_CHARS = {"find_ways": 24000, "read_procedure_claims": 8000}
EPISODES = demo_env.RUNS / "episodes.jsonl"
SURVEY_LOG = demo_env.RUNS / "survey.json"
_lock = threading.Lock()

SYSTEM = (
    "You are an expert Python data scientist working in a small workspace ({repo}) through tools. The task is one "
    "data-science problem together with its setup code. Write ONLY the code that goes at the solution point (where "
    "the problem says BEGIN SOLUTION / put solution in this variable) into the file solution.py, with create_file "
    "(edit_file to change it). That code runs right after the setup code, so the variables the setup defines already "
    "exist: do not recreate or hard-code the example data. Store the answer in the variable the problem asks for "
    "(usually `result`). If the problem asks you to complete a function, write only the body of that function. "
    "Hidden tests will check the result. You cannot run code. You have at most {max_steps} tool calls. Call finish "
    "when solution.py is complete.")

README = ("# Workspace\n\nOne data-science task at a time. The answer goes in `solution.py`: only the code at the "
          "task's solution point. It runs right after the task's setup code, with the libraries in "
          "`requirements.txt` (Python 3.11.9).\n")
REQUIREMENTS = "numpy==1.26.4\npandas==1.5.3\nscipy==1.12.0\nscikit-learn==1.4.0\nmatplotlib==3.8.4\n"

KP_ADAPTATIONS = """How this session differs from an interactive one (fixed for every task):
- There is no user. Wherever the workflow says to ask, show or get an OK from the user, decide yourself and go on.
- There are no subagents: do every node yourself.
- You cannot run code or tests. A node's `check` is done by reading your code carefully.
- The knowledge base is read-only here: report_discovery, submit_way, recommend_models and report_model_run are
  not available. Still keep .stealth/run.md up to date as the workflow says.
- .stealth/claims.md was already written by the survey_repo workflow for this workspace. For repo_claims you may
  pass the literal "@.stealth/claims.md" instead of retyping the file.
- Write .stealth/ files with create_file / edit_file. They are your working files, not the answer.
- The answer is solution.py; call finish when it is complete."""
SURVEY_ADAPTATIONS = ("Adaptations for this session: you cannot run git, so write `sha=-` in each source; write the "
                      "file with create_file at .stealth/claims.md (edit_file to extend it); call finish when it is "
                      "written.")


def workspace(claims: str | None) -> str:
    root = tempfile.mkdtemp(prefix="kel_r4_")
    for name, text in (("README.md", README), ("requirements.txt", REQUIREMENTS)):
        with open(os.path.join(root, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    if claims:
        os.makedirs(os.path.join(root, kpa.STEALTH), exist_ok=True)
        with open(os.path.join(root, kpa.STEALTH, "claims.md"), "w", encoding="utf-8", newline="") as fh:
            fh.write(claims)
    return root


def client():
    from openai import OpenAI

    from app.config import settings

    return OpenAI(api_key=settings.general_compute_api_key, base_url=settings.general_compute_base_url, max_retries=0)


def claims_path(model: str):
    return demo_env.RUNS / f"claims_{model}.md"


def survey(models: list[str]) -> None:
    log = json.loads(SURVEY_LOG.read_text(encoding="utf-8")) if SURVEY_LOG.exists() else {}
    for m in models:
        if claims_path(m).exists():
            continue
        agent = kpa.make_agent(client(), m, MAX_STEPS, TEMPERATURE, None, TOOL_MAX_CHARS, system=SYSTEM, kel_tools=False)
        for attempt in (1, 2):
            root = workspace(None)
            try:
                sb = kpa.make_sandbox(root)
                run = agent.run({"instance_id": f"survey_{m}", "repo": "ds1000-workspace",
                                 "problem_statement": kpa.survey_instructions(SURVEY_ADAPTATIONS)}, sb, "KP_survey")
                path = os.path.join(root, kpa.STEALTH, "claims.md")
                text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
            finally:
                shutil.rmtree(root, ignore_errors=True)
            log.setdefault(m, []).append({"attempt": attempt, "usage": asdict(run.usage), "stop_reason": run.stop_reason,
                                          "claims_chars": len(text)})
            SURVEY_LOG.write_text(json.dumps(log, indent=1), encoding="utf-8")
            if text.strip():
                claims_path(m).write_text(text, encoding="utf-8")
                break
        print(f"{m:<16} claims.md {claims_path(m).stat().st_size if claims_path(m).exists() else 0} bytes", flush=True)


def run_arm(arm: str, models: list[str], workers: int) -> None:
    items = test_items()
    attempts = load_attempts()
    done = {(r["problem_id"], r["model"], r["arm"]) for r in attempts if not r.get("call_error")}
    ag = {(r["problem_id"], r["model"]): r for r in attempts if r["arm"] == "AG" and not r.get("call_error")}
    notes = json.loads((demo_env.RUNS / "notes_K.json").read_text(encoding="utf-8")) if arm == "KN" else {}
    bridge = kpa.KelBridge(demo_env.DEMO_DSN) if arm == "KP" else None
    if arm == "KP":
        missing = [m for m in models if not claims_path(m).exists()]
        if missing and not SURVEY_LOG.exists():
            raise SystemExit("run `python run_r4.py survey` first")
    agents = {m: kpa.make_agent(client(), m, MAX_STEPS, TEMPERATURE, bridge, TOOL_MAX_CHARS, system=SYSTEM,
                                kel_tools=(arm == "KP")) for m in models}
    jobs, reused = [], 0
    for t in items:
        pid = t["problem_id"]
        for m in models:
            if (pid, m, arm) in done:
                continue
            if arm == "KN" and not (notes.get(pid) or {}).get("text"):
                if (pid, m) not in ag:
                    raise SystemExit("run AG to completion first: KN reuses it where round 3 had no note")
                append({**ag[(pid, m)], "arm": "KN", "reused_from": "AG", "notes_ref": None})
                reused += 1
                continue
            jobs.append((pid, m))
    print(f"arm {arm}: {len(jobs)} episodes to run, {reused} reused from AG", flush=True)

    def work(job):
        pid, m = job
        problem = problems()[pid]["prompt"]
        if arm == "KP":
            memory, problem = "", kpa.task_prompt(problem, KP_ADAPTATIONS)
        elif arm == "KN":
            memory = f"{NOTES_INTRO}\n<notes>\n{notes[pid]['text']}\n</notes>"
        else:
            memory = ""
        root = workspace(claims_path(m).read_text(encoding="utf-8") if arm == "KP" and claims_path(m).exists() else None)
        t0 = time.time()
        try:
            sb = kpa.make_sandbox(root)
            run = agents[m].run({"instance_id": f"{pid}", "repo": "ds1000-workspace",
                                 "problem_statement": problem}, sb, arm, memory_block=memory)
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
                                   notes_ref=(notes.get(pid) or {}).get("ref") if arm == "KN" else None,
                                   prompt_sha=kpa.sha(agents[m]._system + problem + memory))
            ep = {"problem_id": pid, "model": m, "arm": arm, "steps": run.steps, "stop_reason": run.stop_reason,
                  "tool_calls": run.tool_calls, "usage": asdict(run.usage), "solution_source": source,
                  "kel_calls": sb.kel_log, "error": run.error, "gold_pass": rec["gold_pass"]}
        except Exception as exc:  # noqa: BLE001 -- infrastructure: recorded as a call error, retried next invocation
            rec = grade_and_record(pid, m, "agent-loop", arm, "", 0, 0, False, int((time.time() - t0) * 1000),
                                   error=f"{type(exc).__name__}: {str(exc)[:300]}")
            ep = {"problem_id": pid, "model": m, "arm": arm, "error": rec["call_error"]}
        finally:
            shutil.rmtree(root, ignore_errors=True)
        with _lock, open(EPISODES, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ep) + "\n")
        kel = ep.get("kel_calls") or []
        print(f"{pid:>4} {m:<16} gold={rec['gold_pass']!s:<5} steps={ep.get('steps')} stop={ep.get('stop_reason')} "
              f"kel={[c.get('outcome') for c in kel if c['tool'] == 'find_ways']} {(rec['call_error'] or '')[:60]}",
              flush=True)

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
    ap.add_argument("arm", choices=["survey", "AG", "KN", "KP"])
    ap.add_argument("--models", default=",".join(OPEN_MODELS))
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    demo_env.verify_after_import()
    from app.config import settings

    assert settings.knowledge_verified_examples and str(settings.database_url).endswith("/kel_ds1000_r4")
    models = a.models.split(",")
    if a.arm == "survey":
        survey(models)
    else:
        run_arm(a.arm, models, a.workers)


if __name__ == "__main__":
    main()
