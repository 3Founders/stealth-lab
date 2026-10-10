"""A-vs-C runner: the same tasks through headless Claude Code, plain (A) and with StealthLab (C).

  A  plain Claude Code on MODEL. Its workspace has no .claude/ and no .stealth/.
  C  the same Claude Code on MODEL, with StealthLab installed in the workspace (setup_c.mjs: knowledge hook, capture
     hooks, model guard, executor agents), the local test server (run_backend.py) and the open models in exec.json.

Both run `claude -p --setting-sources project` with the same prompt, tools and permission mode, from the same
commit of a workspace that persists across tasks of the same group (so C's .stealth/ library can grow; A has none).
After the session the hidden grader decides; the agent's own claim never counts. Cost = Claude Code's reported cost
+ the open-model attempts' list-priced cost (C only).

Resumable: one JSON line per (task, arm) in .local/results/<run>.jsonl, written as soon as it finishes. A session
stopped by a usage limit is recorded as "rate_limited" (not scored) and the run halts; rerun the same command after
the reset and it continues with what is not done. A session silent for HANG_S is killed and recorded as "hang"
(infrastructure, not scored, retried on the next run). There is no other time limit.

    python experiments/abtest/runner.py --run smoke1 --tasks ds1000:5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LOCAL = HERE / ".local"
MODEL = os.environ.get("ABTEST_MODEL", "claude-opus-5-5")
SERVER = os.environ.get("ABTEST_SERVER", "http://127.0.0.1:8765")
HANG_S = 30 * 60
# Claude subagents the plan may use besides the session's own model; open models come from exec.json
# the session model is the last rung; every cheaper rung (open models, Sonnet via the claude executor) comes from
# exec.json and is dispatched before the session starts -- nothing is delegated inside the session
CLAUDE_CANDIDATES = [f"{MODEL}|claude-code"]
TOOLS = ["Read", "Edit", "Write", "Bash", "PowerShell", "Glob", "Grep", "Agent", "TodoWrite"]
# files a session must not open: the graders and the datasets (reference solutions). A transcript that touches one
# is flagged `peeked` and excluded from the scored comparison.
FORBIDDEN = ("ds1000_task.py", "evaluate.py", "test.jsonl", "experiments\\\\ds1000", "experiments/ds1000",
             "runner.py", ".local\\\\results", ".local/results", "bcb_task.py", "bigcodebench", ".parquet")
# one line: on Windows `claude` is a .cmd shim, and a newline in an argument cuts the command line there
RULE = " Work only inside this directory: do not open, list or search files outside it."
C_TOOLS = ["mcp__stealthlab", "mcp__stealthlab-exec"]

sys.path.insert(0, str(HERE))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, check=True).stdout


def env_file(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# ---------------------------------------------------------------- tasks

def load_tasks(spec: str, seed: int) -> list[dict]:
    suites = {}
    for part in spec.split(","):
        name, _, n = part.partition(":")
        suites[name] = int(n or 0)
    tasks = []
    if "ds1000" in suites:
        import ds1000_task
        tasks += [{**t, "module": "ds1000_task"} for t in ds1000_task.sample(suites["ds1000"], seed)]
    if "bcb" in suites:
        import bcb_task
        tasks += [{**t, "module": "bcb_task"} for t in bcb_task.sample(suites["bcb"], seed)]
    unknown = set(suites) - {"ds1000", "bcb"}
    if unknown:
        raise SystemExit(f"unknown suites: {sorted(unknown)}")
    return tasks


def task_module(task: dict):
    return __import__(task["module"])


# ---------------------------------------------------------------- workspaces and arms

def c_home() -> Path:
    home = LOCAL / "C_home"
    home.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "exec.json", home / "exec.json")
    return home


def workspace(arm: str, name: str) -> Path:
    ws = LOCAL / "ws" / arm / name
    if (ws / ".git").exists():
        return ws
    ws.mkdir(parents=True, exist_ok=True)
    git(ws, "init", "-q")
    git(ws, "config", "user.email", "abtest@stealthlab.invalid")
    git(ws, "config", "user.name", "abtest")
    (ws / "README.md").write_text(f"Workspace {name} for the A-vs-C test (arm {arm}).\n", encoding="utf-8")
    if arm == "C":
        (ws / ".stealth").mkdir(exist_ok=True)
        (ws / ".stealth" / ".keep").write_text("", encoding="utf-8")
        subprocess.run(["node", str(HERE / "setup_c.mjs"), str(ws)], check=True, capture_output=True, text=True)
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "workspace")
    return ws


def arm_env(arm: str, secrets: dict[str, str], task_check: str = "") -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("STEALTHLAB_") and k not in (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR")}
    env["ANTHROPIC_MODEL"] = MODEL
    if arm == "C":
        home = c_home()
        env.update({
            "STEALTHLAB_HOME": str(home), "STEALTHLAB_MCP_URL": f"{SERVER}/mcp", "STEALTHLAB_TOKEN": secrets["token"],
            "STEALTHLAB_HOOK_CANDIDATES": ",".join(open_units() + CLAUDE_CANDIDATES),
            "STEALTHLAB_HOOK_TIMEOUT_MS": "60000",
            # dispatch: the plan's cheap rungs run before the session's model, verified by the task's own check
            "STEALTHLAB_DISPATCH_CHECK": task_check,
            "STEALTHLAB_EXEC_WORKTREE_ROOT": str(LOCAL / "worktrees"),
            "GENERAL_COMPUTE_API_KEY": secrets["gc"],
        })
    return env


def open_units() -> list[str]:
    cfg = json.loads((HERE / "exec.json").read_text(encoding="utf-8"))
    return [f"{m}|{ex}" for ex, spec in cfg["executors"].items() for m in spec["models"]]


def mcp_config(secrets: dict[str, str]) -> Path:
    path = LOCAL / "mcp_c.json"
    # the executor server too: the delegator agent declares it inline, but --strict-mcp-config (which keeps the
    # person's own MCP servers out) also drops servers an agent file declares, so it is passed here explicitly
    path.write_text(json.dumps({"mcpServers": {
        "stealthlab": {"type": "http", "url": f"{SERVER}/mcp", "headers": {"Authorization": f"Bearer {secrets['token']}"}},
        "stealthlab-exec": {"type": "stdio", "command": "node",
                            "args": [str(REPO / "packaging" / "npm" / "bin" / "stealthlab-mcp.mjs"), "exec"]}}}),
        encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


# ---------------------------------------------------------------- one session

LIMIT_WORDS = ("usage limit", "limit reached", "rate limit", "weekly limit", "5-hour limit", "out of extra usage",
               "session limit", "usage_limit_reached", "hit your")


def run_claude(ws: Path, prompt: str, env: dict[str, str], extra: list[str], log_path: Path) -> dict:
    exe = shutil.which("claude") or "claude"
    # --strict-mcp-config: only the MCP servers this run passes (none for A, the test server for C) -- the person's own
    # user-level MCP servers never load in either arm
    if "\n" in prompt or "\r" in prompt:
        raise ValueError("the prompt must be one line (Windows .cmd shims cut arguments at a newline)")
    cmd = [exe, "-p", prompt, "--model", MODEL, "--setting-sources", "project", "--strict-mcp-config",
           "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits", "--allowedTools", *TOOLS,
           *extra]
    proc = subprocess.Popen(cmd, cwd=ws, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lines: "queue.Queue[str | None]" = queue.Queue()
    threading.Thread(target=lambda: ([lines.put(x) for x in proc.stdout], lines.put(None)), daemon=True).start()
    result, last = None, time.time()
    with log_path.open("w", encoding="utf-8") as log:
        while True:
            try:
                line = lines.get(timeout=30)
            except queue.Empty:
                if time.time() - last > HANG_S:
                    proc.kill()
                    return {"status": "hang"}
                continue
            if line is None:
                break
            last = time.time()
            log.write(line)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "result":
                result = msg
    proc.wait()
    if result is None:
        tail = log_path.read_text(encoding="utf-8")[-800:]
        limited = any(w in tail.lower() for w in LIMIT_WORDS)
        return {"status": "rate_limited" if limited else "infra", "error": tail[-300:]}
    text = str(result.get("result") or "")
    if result.get("is_error") and any(w in text.lower() for w in LIMIT_WORDS):
        return {"status": "rate_limited", "error": text[:300]}
    return {"status": "done", "is_error": bool(result.get("is_error")), "claude_cost_usd": result.get("total_cost_usd"),
            "turns": result.get("num_turns"), "duration_ms": result.get("duration_ms"),
            "models": sorted((result.get("modelUsage") or {}).keys()), "model_usage": result.get("modelUsage"),
            "final": text[:400]}


def peeked(log_path: Path) -> list[str]:
    """Tool calls in the transcript that name a grader or dataset file (the agent's own tool inputs only)."""
    hits = []
    for line in log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") != "assistant":
            continue
        for c in msg.get("message", {}).get("content", []):
            if c.get("type") == "tool_use":
                text = json.dumps(c.get("input"))
                hits += [f for f in FORBIDDEN if f in text]
    return sorted(set(hits))


def warm(secrets: dict[str, str]) -> float:
    """One lookup before the first session, so a cold server's first find_ways does not eat a session's hook time."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "find_ways", "arguments": {
        "query": "warm-up: fix a failing unit test in a small Python project", "my_model": f"{MODEL}|claude-code",
        "candidates": open_units() + CLAUDE_CANDIDATES}}}).encode()
    t0 = time.time()
    for _ in range(2):
        try:
            req = urllib.request.Request(f"{SERVER}/mcp", data=body, headers={
                "Authorization": f"Bearer {secrets['token']}", "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"})
            urllib.request.urlopen(req, timeout=180).read()
        except Exception:  # noqa: BLE001 -- warming is best effort; the hook's own timeout still guards
            pass
    return round(time.time() - t0, 1)


def wait_hook_jobs(home: Path, limit_s: float = 180) -> bool:
    jobs = home / "hooks" / "jobs"
    t0 = time.time()
    while time.time() - t0 < limit_s:
        if not jobs.exists() or not any(jobs.iterdir()):
            return True
        time.sleep(3)
    return False


def open_model_runs(home: Path, since: str, until: str) -> dict:
    cost, attempts, runs = 0.0, [], 0
    for rj in (home / "runs").glob("*/run.json") if (home / "runs").exists() else []:
        try:
            r = json.loads(rj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not (since <= (r.get("created_at") or "") <= until):
            continue
        runs += 1
        for a in r.get("attempts") or []:
            attempts.append({"model": a.get("model"), "executor": a.get("executor"), "state": a.get("state"),
                             "cost_usd": a.get("cost_usd"), "tokens": a.get("tokens")})
            cost += float(a.get("cost_usd") or 0)
    return {"open_cost_usd": round(cost, 6), "exec_runs": runs, "attempts": attempts}


def run_one(task: dict, arm: str, run: str, secrets: dict[str, str]) -> dict:
    mod = task_module(task)
    ws = workspace(arm, task["workspace"])
    for name, text in mod.files(task, HERE / f"{task['module']}.py").items():
        (ws / name).write_text(text, encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "--allow-empty", "-m", f"task {task['task_id']}")
    env = arm_env(arm, secrets, getattr(mod, "CHECK", ""))
    extra = ["--mcp-config", str(mcp_config(secrets)), "--allowedTools", *C_TOOLS] if arm == "C" else []
    logs = LOCAL / "logs" / run
    logs.mkdir(parents=True, exist_ok=True)
    started = now()
    t0 = time.time()
    log_path = logs / f"{task['task_id']}.{arm}.jsonl"
    out = run_claude(ws, mod.prompt(task) + RULE, env, extra, log_path)
    out["peeked"] = peeked(log_path)
    rec = {"run": run, "task_id": task["task_id"], "suite": task["suite"], "workspace": task["workspace"], "arm": arm,
           "model": MODEL, "started_at": started, "seconds": round(time.time() - t0, 1), **out}
    if arm == "C":
        rec["hook_jobs_drained"] = wait_hook_jobs(LOCAL / "C_home")
        rec.update(open_model_runs(LOCAL / "C_home", started, now()))
        lib = ws / ".stealth" / "library.md"
        rec["library_entries"] = sum(1 for ln in lib.read_text(encoding="utf-8").splitlines()
                                     if ln.startswith("GOAL|")) if lib.exists() else 0
    if out["status"] == "done":
        solution = (ws / "solution.py").read_text(encoding="utf-8")
        rec["grade"] = mod.grade(task, solution)
        rec["resolved"] = rec["grade"]["resolved"]
        rec["total_cost_usd"] = round(float(rec.get("claude_cost_usd") or 0) + float(rec.get("open_cost_usd") or 0), 6)
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "--allow-empty", "-m", f"after {task['task_id']}")
    return rec


# ---------------------------------------------------------------- the run

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--tasks", required=True, help="e.g. ds1000:5")
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--arms", default="A,C")
    args = ap.parse_args()

    import local_identity
    secrets = {"token": local_identity.paths()["token"].read_text().strip(),
               "gc": env_file(REPO / "backend" / ".env").get("GENERAL_COMPUTE_API_KEY", "")}
    if "C" in args.arms:
        try:
            urllib.request.urlopen(f"{SERVER}/healthz", timeout=10)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"the test server is not up at {SERVER} ({exc}); start experiments/abtest/run_backend.py")
    if "C" in args.arms:
        print(f"warm-up lookups: {warm(secrets)} s", flush=True)
    results = LOCAL / "results" / f"{args.run}.jsonl"
    results.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("status") == "done":
                done.add((r["task_id"], r["arm"]))
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for task in load_tasks(args.tasks, args.seed):
        order = sorted(arms, key=lambda a: hashlib.sha256(f"{args.seed}|{task['task_id']}|{a}".encode()).hexdigest())
        for arm in order:
            if (task["task_id"], arm) in done:
                continue
            print(f"[{now()}] {task['task_id']} arm {arm} ...", flush=True)
            rec = run_one(task, arm, args.run, secrets)
            with results.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(f"    {rec['status']} resolved={rec.get('resolved')} cost={rec.get('total_cost_usd')} "
                  f"{rec.get('seconds')}s", flush=True)
            if rec["status"] == "rate_limited":
                print("usage limit reached: stopping. Rerun the same command after the reset to continue.")
                return 3
    print(f"done: {results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
