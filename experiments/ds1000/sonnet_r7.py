"""DS-1000 round 7 (PREREGISTRATION_7.md): does the knowledge hook help a frontier model? Sonnet through Claude Code
subagents, arms AG7 (Sonnet alone) and KH7 (Sonnet with the installed product and its knowledge hook).

    python sonnet_r7.py prepare     # hook lookups, workspaces and batch prompts under runs7/sonnet/
    python sonnet_r7.py grade       # grade every workspace's solution.py -> runs7/attempts.jsonl

As round 4's Sonnet arms (sonnet_r4.py): batches of 6 tasks from 6 different families; the same rules as the open
models' loop, as instructions (no code execution, 20 tool calls per task, answer in solution.py); tokens are the
batch's reported total split over its tasks.
* AG7: round 4's AG prompt, unchanged.
* KH7: what Claude Code shows the model when StealthLab is installed with the hook -- the MCP server instructions,
  Kel's tools (find_ways, read_procedure_claims) as a command, .stealth/claims.md (Sonnet's round-4 survey), and
  for each task the hook's own text: find_ways run on the task BEFORE the agent starts (round 5's settings:
  related examples, suggested candidate, governor), formatted by packaging/npm/lib/hook.mjs. No plan_and_run.
AG7 and KH7 use the same batches and run side by side in waves (time-matched).
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs7"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"
for _flag in ("KNOWLEDGE_VERIFIED_EXAMPLES", "KNOWLEDGE_RELATED_EXAMPLES", "KNOWLEDGE_SUGGESTED_CANDIDATE",
              "FIND_WAYS_GOVERNOR"):
    os.environ[_flag] = "true"

import demo_env  # noqa: E402

import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
from collections import defaultdict  # noqa: E402

sys.path.insert(0, str(demo_env.HERE.parent))
import kel_product_arm as kpa  # noqa: E402

from common import grade_and_record, load_attempts, problems, test_items  # noqa: E402
from run_r4 import README, REQUIREMENTS  # noqa: E402

MODEL = "claude-sonnet-5"
ROOT = demo_env.RUNS / "sonnet"
BATCH = 6
PY = sys.executable
CLI = demo_env.HERE / "kel_cli.py"
CLAIMS = demo_env.HERE / "runs4" / "sonnet" / "claims_sonnet.md"   # Sonnet's own survey_repo output, round 4
ARMS = ("AG7", "KH7")

# Round 4's Sonnet rules and AG header, verbatim (sonnet_r4.py; not imported: that module fixes runs4 on import).
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


def kh_head() -> str:
    from app.mcp_server.server import _V1_INSTRUCTIONS

    return ("You are an expert Python data scientist working through tasks in Claude Code. Each task has its own "
            "workspace, and TASK.md in it holds the problem together with its setup code.\n\n"
            "Connected MCP server: StealthLab (Kel). Its instructions:\n" + _V1_INSTRUCTIONS + "\n\n"
            "For EACH task below, the user's message is the problem in that workspace's TASK.md. StealthLab's "
            "UserPromptSubmit hook ran on it before you started; what it added to the user's message is shown "
            "under the task.\n\n")


def kel_tools(ws: str) -> str:
    return (f"StealthLab's MCP tools are available as commands (run them with Bash; this is the ONLY allowed use "
            f"of Bash):\n"
            f"- find_ways: `KEL_CLI_ROUND=7 \"{PY}\" \"{CLI}\" find_ways --ws \"{ws}\" --query \"<what you want done>\" "
            f"--claims @.stealth/claims.md`\n"
            f"- read_procedure_claims (the resource stealth://procedures/<id>/claims): "
            f"`KEL_CLI_ROUND=7 \"{PY}\" \"{CLI}\" read_procedure_claims --ws \"{ws}\" --id <procedure_id>`\n"
            f"Each StealthLab call counts as one tool call.")


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
    """6 tasks per batch, all from different families (deterministic; the same rule as sonnet_r4.py)."""
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


def hook_texts(claims: str) -> dict:
    """The hook's lookup for every task, once (its own MCP session each, as a hook process is)."""
    path = ROOT / "hooks.json"
    hooks = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = [t["problem_id"] for t in test_items() if t["problem_id"] not in hooks]
    if todo:
        bridge = kpa.KelBridge(demo_env.DEMO_DSN)
        try:
            for pid in todo:
                text, summary = kpa.hook_context(bridge, problems()[pid]["prompt"], claims, session=f"r7:{pid}#hook")
                hooks[pid] = {"text": text, "summary": summary}
                path.write_text(json.dumps(hooks, indent=1), encoding="utf-8")
                print(f"hook {pid:>4} {summary['outcome']:<10} {summary['context_chars']} chars", flush=True)
        finally:
            bridge.close()
    return hooks


def prepare() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    design = demo_env.RUNS / "design.json"
    if not design.exists():
        shutil.copyfile(demo_env.HERE / "runs5" / "design.json", design)
    claims = CLAIMS.read_text(encoding="utf-8")
    hooks = hook_texts(claims)
    plan = {}
    for i, b in enumerate(batches()):
        for arm in ARMS:
            parts = [AG_HEAD if arm == "AG7" else kh_head(), RULES, ""]
            for pid in b:
                ws = workspace(arm, pid, claims if arm == "KH7" else None)
                section = f"## Task {pid}\nWorkspace: {ws}\n"
                if arm == "KH7":
                    text = hooks[pid]["text"]
                    section += kel_tools(ws) + "\n\n" + (
                        f"What the StealthLab hook added to the user's message for this task:\n\n{text}\n"
                        if text else "(The StealthLab hook found nothing to add for this task.)\n")
                parts.append(section)
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


def _batch_share(pid: str, arm: str, plan: dict) -> int | None:
    """The batch's reported token total (runs7/sonnet/usage.json, recorded from each subagent's result), split
    evenly over its tasks; the retry batch's when there was one. None when no usage was reported."""
    path = ROOT / "usage.json"
    usage = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for name in sorted((n for n, ps in plan.items() if pid in ps and f"_{arm}_" in n), key=lambda n: "_retry" not in n):
        if name in usage:
            return usage[name]["tokens"] // len(plan[name])
    return None


def grade() -> None:
    plan = json.loads((ROOT / "batches.json").read_text(encoding="utf-8"))
    done_path = ROOT / "done.json"      # batches whose subagent finished (recorded when its result arrives)
    finished = set(json.loads(done_path.read_text(encoding="utf-8"))) if done_path.exists() else set()
    graded = {(r["problem_id"], r["arm"]) for r in load_attempts() if r["model"] == MODEL and not r.get("call_error")}
    n = 0
    for name, pids in plan.items():
        if name not in finished:
            continue
        arm = name.split("_")[1]
        prompt_chars = len((ROOT / name).read_text(encoding="utf-8")) // len(pids)
        for pid in pids:
            if (pid, arm) in graded:
                continue
            ws = str(ROOT / "ws" / arm / pid)
            sol = os.path.join(ws, "solution.py")
            code = open(sol, encoding="utf-8").read() if os.path.isfile(sol) else ""
            tin, tout = _est_tokens(ws, prompt_chars)
            share = _batch_share(pid, arm, plan)
            if share:
                tin = max(1, share - tout)
            rec = grade_and_record(pid, MODEL, "claude-code-subagent", arm, f"```python\n{code}\n```" if code else "",
                                   tin, tout, not share, 0)   # no solution.py in a finished batch = a failed answer
            n += 1
            print(f"{arm} {pid:>4} gold={rec['gold_pass']}", flush=True)
    print(f"graded {n}")


if __name__ == "__main__":
    {"prepare": prepare, "grade": grade}[sys.argv[1]]()
