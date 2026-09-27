"""DS-1000 round 4, Sonnet through Claude Code subagents (deviation 1 in PREREGISTRATION_4.md).

    python sonnet_r4.py prepare     # workspaces + batch prompts under runs4/sonnet/
    python sonnet_r4.py grade       # grade every workspace's solution.py -> runs4/attempts.jsonl (model claude-sonnet-5)

Arms AG and KP only. Each held-out task gets its own workspace (README.md, requirements.txt, TASK.md; KP also
.stealth/claims.md from a Sonnet survey_repo run). A batch prompt lists 6 workspaces of 6 DIFFERENT families,
so no subagent sees two variants of one family; AG and KP use the same batches. The subagent is told the same
rules the open models' loop enforces (no code execution, 20 tool calls per task, answer in solution.py);
here they are instructions, not enforced. Tokens are not reported by subagents: recorded as estimated
(characters / 4 of the prompt, the files the agent read and the answer), flagged `tokens_estimated`.
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs4"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"
os.environ["KNOWLEDGE_VERIFIED_EXAMPLES"] = "true"

import demo_env  # noqa: E402

import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
from collections import defaultdict  # noqa: E402

sys.path.insert(0, str(demo_env.HERE.parent))
import kel_product_arm as kpa  # noqa: E402

from common import grade_and_record, load_attempts, problems, test_items  # noqa: E402
from run_r4 import KP_ADAPTATIONS, README, REQUIREMENTS, SURVEY_ADAPTATIONS  # noqa: E402

MODEL = "claude-sonnet-5"
ROOT = demo_env.RUNS / "sonnet"
BATCH = 6
PY = sys.executable
CLI = demo_env.HERE / "kel_cli.py"

RULES = """Rules for every task (the same rules the other models run under):
- Work only inside that task's workspace directory. Use Read, Write, Edit, Glob and Grep.
- Do NOT run Python or any other code, and do not use Bash for anything except the StealthLab command below
  (when it is offered). No web access.
- At most 20 tool calls per task.
- Treat every task independently: do not reuse anything from another task in this batch.
- The answer is the file solution.py in the workspace: ONLY the code that goes at the task's solution point
  (where it says BEGIN SOLUTION / put solution in this variable). That code runs right after the task's setup
  code, so the variables the setup defines already exist: do not recreate or hard-code the example data. Store
  the answer in the variable the task asks for (usually `result`). If the task asks you to complete a function,
  write only the body of that function. Hidden tests will check it."""

AG_HEAD = ("You are an expert Python data scientist. Solve each DS-1000 task below; each has its own workspace, "
           "and TASK.md in it holds the problem together with its setup code.\n\n")


def kel_tools(ws: str) -> str:
    return (f"StealthLab's MCP tools are available as commands (run them with Bash; this is the ONLY allowed use "
            f"of Bash):\n"
            f"- find_ways: `\"{PY}\" \"{CLI}\" find_ways --ws \"{ws}\" --query \"<what you want done>\" "
            f"--claims @.stealth/claims.md`\n"
            f"- read_procedure_claims (the resource stealth://procedures/<id>/claims): "
            f"`\"{PY}\" \"{CLI}\" read_procedure_claims --ws \"{ws}\" --id <procedure_id>`\n"
            f"Each StealthLab call counts as one tool call.")


def kp_head() -> str:
    from app.mcp_server.server import _V1_INSTRUCTIONS

    return ("You are an expert Python data scientist working through tasks in Claude Code.\n\n"
            "Connected MCP server: StealthLab (Kel). Its instructions:\n" + _V1_INSTRUCTIONS + "\n\n"
            "For EACH task below, the user invokes StealthLab's plan_and_run prompt with that task; the prompt is:\n\n"
            + kpa.task_prompt("the problem in this workspace's TASK.md", KP_ADAPTATIONS) + "\n\n")


def workspace(arm: str, pid: str, claims: str | None) -> str:
    ws = ROOT / "ws" / arm / pid
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    (ws / "README.md").write_text(README, encoding="utf-8")
    (ws / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
    (ws / "TASK.md").write_text(problems()[pid]["prompt"], encoding="utf-8")
    if claims:
        (ws / kpa.STEALTH).mkdir()
        (ws / kpa.STEALTH / "claims.md").write_text(claims, encoding="utf-8")
    return str(ws)


def batches() -> list[list[str]]:
    """6 tasks per batch, all from different families (deterministic: family order, round-robin)."""
    by_fam = defaultdict(list)
    for t in test_items():
        by_fam[t["family"]].append(t["problem_id"])
    queue = [pid for _ in range(max(len(v) for v in by_fam.values()))
             for fam in sorted(by_fam) for pid in ([by_fam[fam].pop(0)] if by_fam[fam] else [])]
    out: list[list[str]] = []
    fam_of = {t["problem_id"]: t["family"] for t in test_items()}
    for pid in queue:
        for b in out:
            if len(b) < BATCH and fam_of[pid] not in {fam_of[x] for x in b}:
                b.append(pid)
                break
        else:
            out.append([pid])
    return out


def prepare() -> None:
    claims_file = ROOT / "claims_sonnet.md"
    if not claims_file.exists():
        survey_ws = ROOT / "ws" / "survey"
        survey_ws.mkdir(parents=True, exist_ok=True)
        (survey_ws / "README.md").write_text(README, encoding="utf-8")
        (survey_ws / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
        (ROOT / "prompt_survey.md").write_text(
            f"Workspace: {survey_ws}\n\n" + kpa.survey_instructions(SURVEY_ADAPTATIONS)
            + "\n\nUse Read/Glob/Grep/Write/Edit only; do not run any code. Reply 'done' when the file is written.",
            encoding="utf-8")
        print(f"FIRST run the survey subagent on {ROOT / 'prompt_survey.md'}, then copy "
              f"{survey_ws / '.stealth' / 'claims.md'} to {claims_file} and run prepare again.")
        return
    claims = claims_file.read_text(encoding="utf-8")
    plan = {}
    for arm in ("AG", "KP"):
        for i, b in enumerate(batches()):
            parts = [AG_HEAD if arm == "AG" else kp_head(), RULES, ""]
            for pid in b:
                ws = workspace(arm, pid, claims if arm == "KP" else None)
                parts.append(f"## Task {pid}\nWorkspace: {ws}\n" + (kel_tools(ws) + "\n" if arm == "KP" else ""))
            parts.append("When every task's solution.py is written, reply with one line per task: "
                         "`<task> done` or `<task> no answer`.")
            name = f"prompt_{arm}_{i:02d}.md"
            (ROOT / name).write_text("\n".join(parts), encoding="utf-8")
            plan[name] = b
    (ROOT / "batches.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(f"{len(plan)} batch prompts ({len(batches())} per arm) in {ROOT}")


def _est_tokens(ws: str, prompt_chars: int) -> tuple[int, int]:
    files = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(ws) for f in fs)
    sol = os.path.join(ws, "solution.py")
    out = os.path.getsize(sol) if os.path.isfile(sol) else 0
    return max(1, (prompt_chars + files) // 4), max(1, out // 4)


def _batch_share(arm: str, pid: str, plan: dict) -> int | None:
    """The real token total of the batch that produced this task's answer (the retry batch when there was one),
    split evenly over its tasks; None when that batch reported no usage (it was cut off by a usage limit)."""
    usage = json.loads((ROOT / "usage.json").read_text(encoding="utf-8"))
    for name in sorted((n for n, ps in plan.items() if pid in ps and f"_{arm}_" in n), key=lambda n: "_retry" not in n):
        if name in usage:
            return usage[name]["tokens"] // len(plan[name])
        if "_retry" in name:
            return None
    return None


def grade() -> None:
    plan = json.loads((ROOT / "batches.json").read_text(encoding="utf-8"))
    done = {(r["problem_id"], r["arm"]) for r in load_attempts() if r["model"] == MODEL and not r.get("call_error")}
    n = 0
    for name, pids in plan.items():
        arm = name.split("_")[1]
        prompt_chars = len((ROOT / name).read_text(encoding="utf-8")) // len(pids)
        for pid in pids:
            if (pid, arm) in done:
                continue
            ws = str(ROOT / "ws" / arm / pid)
            sol = os.path.join(ws, "solution.py")
            code = open(sol, encoding="utf-8").read() if os.path.isfile(sol) else ""
            tin, tout = _est_tokens(ws, prompt_chars)
            share = _batch_share(arm, pid, plan)
            if share:   # the batch's real total (Claude Code reports no in/out split): output = the answer's size
                tin = max(1, share - tout)
            rec = grade_and_record(pid, MODEL, "claude-code-subagent", arm, f"```python\n{code}\n```" if code else "",
                                   tin, tout, True, 0)   # no solution.py = a failed answer, not a call error
            n += 1
            print(f"{arm} {pid:>4} gold={rec['gold_pass']}", flush=True)
    print(f"graded {n}")


if __name__ == "__main__":
    {"prepare": prepare, "grade": grade}[sys.argv[1]]()
