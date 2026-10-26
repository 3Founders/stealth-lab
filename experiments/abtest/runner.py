"""A-vs-C runner: the same tasks through headless Claude Code, plain (A) and with StealthLab (C).

  A  plain Claude Code on MODEL. Its workspace has no .claude/ and no .kell/.
  C  the same Claude Code on MODEL, with StealthLab installed in the workspace (setup_c.mjs: knowledge hook, capture
     hooks, model guard, executor agents), the local test server (run_backend.py) and the open models in exec.json.
  C0 (long-horizon series only) exactly C, but its .kell/ is wiped before EVERY task: what C gains from a library
     and survey that persist across tasks of one repo, C0 does not. C minus C0 is the value of .kell.

Both run `claude -p --setting-sources project` with the same prompt, tools and permission mode, from the same
commit of a workspace that persists across tasks of the same group (so C's .kell/ library can grow; A has none).
After the session the hidden grader decides; the agent's own claim never counts. Cost = Claude Code's reported cost
+ the open-model attempts' list-priced cost (C only).

Resumable: one JSON line per (task, arm) in .local/results/<run>.jsonl, written as soon as it finishes. A session
stopped by a usage limit is recorded as "rate_limited" (not scored) and the run halts; rerun the same command after
the reset and it continues with what is not done. A session silent for HANG_S is killed and recorded as "hang"
(infrastructure, not scored, retried on the next run). There is no other time limit.

    python experiments/abtest/runner.py --run smoke1 --tasks ds1000:5

Long-horizon series (a SERIES of tasks in one repo, in real order; the workspace and, for C, its .kell/ persist):

    python experiments/abtest/runner.py --run toy1 --series series_toy_task --arms A,C,C0 --dry-run     # the plan, nothing runs
    python experiments/abtest/runner.py --run toy1 --series series_toy_task:toy --arms A,C,C0

A series module (see series_toy_task.py for a working template) provides, besides the usual prompt/files/grade:
  series(name) -> the ordered task dicts ({task_id, suite, workspace, ...}); tasks of one series share a workspace.
  setup_project(task, ws)   optional: build the shared project once per workspace and arm (before the first task).
  grade_workspace(task, ws) optional: grade from the whole workspace (a dbt project) instead of one solution file.
  prepare_task(task, ws)    optional: before EVERY task, put the workspace's files where this task starts (a real repository at
                            the issue's base commit: swe_series_task.py); .git, .kell/ and .claude/ stay.
  python_dir(task, ws)      optional: the directory whose `python` the agent, dispatch and the tests use (a per-arm venv).
Results carry `series` and `position`; `report.py <run> --curve` prints the learning curve per arm.

Messy, multi-turn input (any task source): `--turns N` (2-4) replaces the task's own prompt by a vague opener and continues
the same session with a simulated user (multiturn.py); the result then carries `turn_log` (one record per turn), `opener`,
`user_turns`, `questions_asked`, `ended_by`, and the cost and time are summed over the turns:

    python experiments/abtest/runner.py --run toy2 --series series_toy_task:toy --arms A,C,C0 --turns 3 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LOCAL = HERE / ".local"
MODEL = os.environ.get("ABTEST_MODEL", "claude-opus-5-5")


def on_bedrock() -> bool:
    """True when this process's Claude sessions go through AWS Bedrock (modal_series.py --auth subscription+bedrock, after a usage limit)."""
    return os.environ.get("ABTEST_AUTH") == "bedrock"


def session_model() -> str:
    """The id `claude --model` gets: the same model, by the id Bedrock knows it under when the session runs there."""
    return os.environ.get("ABTEST_BEDROCK_OPUS", MODEL) if on_bedrock() else MODEL


def judge_model() -> str:
    return os.environ.get("ABTEST_BEDROCK_SONNET", ANSWER_JUDGE_MODEL) if on_bedrock() else ANSWER_JUDGE_MODEL
SERVER = os.environ.get("ABTEST_SERVER", "http://127.0.0.1:8765")
# arm C runs the PRODUCT's dispatch defaults (packaging/npm lib/dispatch.mjs dispatchPolicy: every model at once, one
# 600 s budget, the reproduction-test rule): the series measures what a user gets. ABTEST_CHEAP_TIMEOUT_S overrides
# the budget only when a run asks for it (modal_series --cheap-timeout-s).
PRODUCT_BUDGET_S = 600


def cheap_s() -> int:
    return int(os.environ.get("ABTEST_CHEAP_TIMEOUT_S") or PRODUCT_BUDGET_S)


def hook_s() -> int:
    # the prompt hook's kill timer: past the budget, the test writer and the reviews, so dispatch's own limit ends a
    # try, never the hook's (the product's own default is 1200 s, which this keeps for the 600 s budget)
    return max(1200, cheap_s() + 600)


# the session is silent while dispatch runs inside its prompt hook, so its hang limit is longer than the hook's
HANG_S = hook_s() + 10 * 60
# Claude subagents the plan may use besides the session's own model; open models come from exec.json
# the session model is the last rung; every cheaper rung (open models, Sonnet via the claude executor) comes from
# exec.json and is dispatched before the session starts -- nothing is delegated inside the session
CLAUDE_CANDIDATES = [f"{MODEL}|claude-code"]
TOOLS = ["Read", "Edit", "Write", "Bash", "PowerShell", "Glob", "Grep", "Agent", "TodoWrite"]
# files a session must not open: the graders and the datasets (reference solutions). A transcript that touches one
# is flagged `peeked` and excluded from the scored comparison.
FORBIDDEN = ("ds1000_task.py", "evaluate.py", "test.jsonl", "experiments\\\\ds1000", "experiments/ds1000",
             "runner.py", ".local\\\\results", ".local/results", "bcb_task.py", "bigcodebench", ".parquet",
             "mbpp_task.py", "mbppplus", "MbppPlus", "ifeval_task.py", "experiments\\\\ifeval", "experiments/ifeval",
             "bird_task.py", "experiments\\\\bird", "experiments/bird", "boolq_task.py", "experiments\\\\boolq",
             "experiments/boolq", "openapi_task.py", "experiments\\\\openapi", "experiments/openapi",
             "multiple_js_task.py", "experiments\\\\multiple", "experiments/multiple", "page_task.py", "html_grade.py",
             "d2c_task.py", "websight_task.py", "experiments\\\\design2code", "experiments/design2code",
             "experiments\\\\websight", "experiments/websight", "swe_series_task.py", ".local/swe", ".local\\\\swe")
# one line: on Windows `claude` is a .cmd shim, and a newline in an argument cuts the command line there
RULE = " Work only inside this directory: do not open, list or search files outside it."
C_TOOLS = ["mcp__stealthlab", "mcp__stealthlab-exec"]

sys.path.insert(0, str(HERE))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, check=True).stdout


def gc_tokens_left(key: str) -> int | None:
    """General Compute's remaining tokens for today on this key (one tiny request), or None when it cannot be read."""
    req = urllib.request.Request("https://api.generalcompute.com/v1/chat/completions", method="POST",
        data=json.dumps({"model": "gpt-oss-120b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            v = r.headers.get("x-ratelimit-remaining-tokens-day")
    except urllib.error.HTTPError as e:
        v = e.headers.get("x-ratelimit-remaining-tokens-day")
    except Exception:  # noqa: BLE001
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def read_token(arms: list[str]) -> str:
    """The local test server's token (local_identity.py, written when run_backend.py starts). Only arms C and C0 talk to that server: a
    run of arm A alone (a split run's A container starts no server) needs none."""
    import local_identity
    path = local_identity.paths()["token"]
    if path.exists():
        return path.read_text().strip()
    if any(is_c(a) for a in arms):
        raise SystemExit(f"{path} is missing: start the test server first (experiments/abtest/run_backend.py)")
    return ""


def secret_value(name: str) -> str:
    """A key by NAME: the environment first (Modal secrets, CI), else backend/.env when that file exists, else ''."""
    if os.environ.get(name):
        return os.environ[name]
    path = REPO / "backend" / ".env"
    return env_file(path).get(name, "") if path.exists() else ""


def env_file(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# ---------------------------------------------------------------- tasks

ARMS = ("A", "C0", "C-R", "C-L", "C")

# What persists from one issue to the next, per StealthLab arm (every one of them has the SAME install and settings; only this differs):
#   facts   .kell/claims.md and survey/ (one `stealthlab-mcp survey` at series start; none for C0)
#   library .kell/library.md, library/, index/, SUMMARY.md, evidence (what was fixed before)
#   routing .kell/routing.md, areas.md, dag_state.json (the local per-model counts)
#   server  the router's state in the test database: reset to its post-setup snapshot before each issue unless kept
ARM_STATE = {
    "C0":  {"facts": False, "library": False, "routing": False, "server": "reset"},
    "C-R": {"facts": True,  "library": False, "routing": False, "server": "reset"},
    "C-L": {"facts": True,  "library": True,  "routing": False, "server": "reset"},
    "C":   {"facts": True,  "library": True,  "routing": True,  "server": "kept"},
}
STATE_FILES = {
    "facts": ("claims.md", "survey"),
    "library": ("library.md", "library", "index", "SUMMARY.md", "evidence.md", "evidence.md.prev", "evidence.key"),
    "routing": ("routing.md", "areas.md", "dag_state.json"),
}
# kept in every arm: bookkeeping that identifies the repository, and the marker that stops the background survey
STATE_KEEP = (".keep", "meta.json", "workspace.md", ".auto-survey", ".gitignore", ".series_survey.json")


def is_c(arm: str) -> bool:
    """Every StealthLab arm (C0, C-R, C-L, C) is the same install; arm A is plain Claude Code."""
    return arm in ARM_STATE


def server_resets(arm: str) -> bool:
    """True when the router's server-side state is put back to its post-setup snapshot before each issue (C0, C-R, C-L)."""
    return ARM_STATE.get(arm, {}).get("server") == "reset"


def wipe_stealth(ws: Path, arm: str = "C0") -> None:
    """Empty what `arm` does NOT keep between issues (ARM_STATE), leaving the directory, its .keep and the repository's bookkeeping,
    so the workspace still counts as a StealthLab repo. C0 keeps nothing; C keeps everything (this is then a no-op)."""
    keep = ARM_STATE[arm]
    d = ws / ".kell"
    if d.exists():
        wipe_all = not all(keep[g] for g in STATE_FILES)      # C keeps everything, whatever else the hooks wrote
        kept = set(STATE_KEEP) | {n for group, names in STATE_FILES.items() if keep[group] for n in names}
        for child in d.iterdir():
            # anything this arm does not keep goes, including files the hooks add that no table names: what is not listed as kept
            # must not leak into the next issue
            if wipe_all and child.name not in kept:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
    d.mkdir(parents=True, exist_ok=True)
    (d / ".keep").write_text("", encoding="utf-8")


def setup_survey(arm: str, ws: Path, env: dict[str, str]) -> dict | None:
    """The ONE survey of a series, run before issue 1 in the arms that keep repo facts (C-R, C-L, C): `stealthlab-mcp survey`
    on the workspace at the first issue's base commit, local only. Returns {seconds, facts, ...} (also written to
    .kell/.series_survey.json so a resume does not run it twice), or None when this arm has none or it already ran."""
    if not ARM_STATE.get(arm, {}).get("facts"):
        return None
    marker = ws / ".kell" / ".series_survey.json"
    if marker.exists():
        return None
    t0 = time.time()
    proc = subprocess.run(["node", str(REPO / "packaging" / "npm" / "bin" / "stealthlab-mcp.mjs"), "survey", str(ws)], cwd=ws, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    claims = ws / ".kell" / "claims.md"
    facts = sum(1 for ln in claims.read_text(encoding="utf-8").splitlines() if ln.startswith("CLAIM|")) if claims.exists() else 0
    rec = {"seconds": round(time.time() - t0, 1), "facts": facts, "rc": proc.returncode, "model_tokens": 0, "cost_usd": 0.0,
           "uses_a_model": False}                  # `survey` without --fill is local only: no model call, so no tokens or dollars
    marker.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def load_series(spec: str) -> list[dict]:
    """`module` or `module:name` -> the module's ordered tasks, each marked with its series and 1-based position."""
    module, _, name = spec.partition(":")
    mod = __import__(module)
    if not hasattr(mod, "series"):
        raise SystemExit(f"{module} has no series(name) function (see series_toy_task.py)")
    tasks = mod.series(name or None)
    if not tasks:
        raise SystemExit(f"{spec}: the series is empty")
    ws = {t["workspace"] for t in tasks}
    if len(ws) != 1:
        raise SystemExit(f"{spec}: the tasks of one series share ONE workspace, got {sorted(ws)}")
    return [{**t, "module": module, "series": name or module, "position": i + 1} for i, t in enumerate(tasks)]


SUITES = {"ds1000": "ds1000_task", "bcb": "bcb_task", "mbpp": "mbpp_task", "ifeval": "ifeval_task",
          "bird": "bird_task", "boolq": "boolq_task", "openapi": "openapi_task", "multiple_js": "multiple_js_task",
          "d2c": "d2c_task", "websight": "websight_task"}


def load_tasks(spec: str, seed: int, exclude: frozenset = frozenset()) -> list[dict]:
    suites = {}
    for part in spec.split(","):
        name, _, n = part.partition(":")
        suites[name] = int(n or 0)
    if exclude:   # tasks used before are skipped: each suite samples extra and keeps the first n new ones
        want = dict(suites)
        tasks = load_tasks(",".join(f"{k}:{v + len(exclude)}" for k, v in suites.items()), seed)
        per: dict[str, list[dict]] = {k: [] for k in want}
        for t in tasks:
            if t["task_id"] not in exclude and len(per[t["suite"]]) < want[t["suite"]]:
                per[t["suite"]].append(t)
        return interleave(list(per.values()))
    unknown = set(suites) - set(SUITES)
    if unknown:
        raise SystemExit(f"unknown suites: {sorted(unknown)} (known: {sorted(SUITES)})")
    per = []
    for name, n in suites.items():
        mod = __import__(SUITES[name])
        per.append([{**t, "module": SUITES[name]} for t in mod.sample(n, seed)])
    return interleave(per)


def interleave(per: list[list[dict]]) -> list[dict]:
    """The suites' tasks interleaved in proportion to each suite's size: a run stopped early is still a balanced
    sample of every suite."""
    order = sorted(((i + 0.5) / len(p), k, i) for k, p in enumerate(per) for i in range(len(p)))
    return [per[k][i] for _, k, i in order]


def task_module(task: dict):
    return __import__(task["module"])


# ---------------------------------------------------------------- workspaces and arms

def c_home() -> Path:
    home = LOCAL / "C_home"
    home.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((HERE / "exec.json").read_text(encoding="utf-8"))
    # ABTEST_CHEAP_AGENT=claude: the same open models, driven by Claude Code (through StealthLab's local bridge) instead
    # of StealthLab's own minimal agent loop -- only WHO drives the models changes; models, profiles, checks, .kell
    # and dispatch stay as they are
    if os.environ.get("ABTEST_CHEAP_AGENT", "stealth").lower() == "claude":
        models = [m for spec in cfg.get("executors", {}).values() for m in (spec.get("models") or []) if m in cfg.get("profiles", {})]
        cfg["executors"] = {"claude": {"models": list(dict.fromkeys(models))}}
    (home / "exec.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
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
    if is_c(arm):
        (ws / ".kell").mkdir(exist_ok=True)
        (ws / ".kell" / ".keep").write_text("", encoding="utf-8")
        install_c(ws)
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "workspace")
    return ws


def install_c(ws: Path) -> None:
    # STEALTHLAB_HOME: the install's saved config (dispatch: true) goes to arm C's home, never the person's own
    subprocess.run(["node", str(HERE / "setup_c.mjs"), str(ws)], check=True, capture_output=True, text=True,
                   env={**os.environ, "STEALTHLAB_HOME": str(c_home()),
                        # the prompt hook outlasts dispatch's limits below, so it never cuts a try short
                        "STEALTHLAB_PROMPT_HOOK_TIMEOUT_S": str(hook_s())})


def arm_env(arm: str, secrets: dict[str, str], task_check: str = "") -> dict[str, str]:
    # Claude Code authenticates as the person's own login (a subscription; CLAUDE_CODE_OAUTH_TOKEN passes through) -- or,
    # with ABTEST_AUTH=api (modal_series.py --auth api), with ANTHROPIC_API_KEY, and then the subscription token is dropped
    drop = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"}
    if os.environ.get("ABTEST_AUTH") == "api":
        drop = (drop - {"ANTHROPIC_API_KEY"}) | {"CLAUDE_CODE_OAUTH_TOKEN"}
    env = {k: v for k, v in os.environ.items() if not k.startswith("STEALTHLAB_") and k not in drop}
    env["ANTHROPIC_MODEL"] = session_model()
    if on_bedrock():
        # the same Claude, served by Bedrock: its login variables are dropped (never both in one session), the model aliases
        # the reviewer and the judge use map to Bedrock's ids, and the AWS credentials stay in this environment only
        for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
            env.pop(k, None)
        env.update({"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_DEFAULT_OPUS_MODEL": session_model(),
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": judge_model()})
    if is_c(arm):
        home = c_home()
        env.update({
            # facts come from the ONE survey at series start (setup_survey), never from a background survey that would rebuild
            # them (or history-mined fixes) in the middle of an issue and blur the arms
            "STEALTHLAB_AUTO_SURVEY": "off",
            "STEALTHLAB_HOME": str(home), "STEALTHLAB_MCP_URL": f"{SERVER}/mcp", "STEALTHLAB_TOKEN": secrets["token"],
            "STEALTHLAB_HOOK_CANDIDATES": ",".join(open_units() + CLAUDE_CANDIDATES),
            "STEALTHLAB_HOOK_TIMEOUT_MS": "60000",
            # dispatch: the plan's cheap rungs run before the session's model, verified by the task's own check
            "STEALTHLAB_DISPATCH_CHECK": task_check,
            **({"STEALTHLAB_DISPATCH_TESTFIRST_MODEL": os.environ["ABTEST_TESTFIRST_MODEL"]}
               if os.environ.get("ABTEST_TESTFIRST_MODEL") else {}),
            **({"STEALTHLAB_DISPATCH_PARALLEL": "on"} if os.environ.get("ABTEST_PARALLEL") else {}),
            # how a passing cheap answer is accepted (packaging/npm lib/accept.mjs; docs/acceptance_rules_preregistered_2026-10-16.md)
            **({"STEALTHLAB_DISPATCH_ACCEPT": os.environ["ABTEST_ACCEPT_RULE"]} if os.environ.get("ABTEST_ACCEPT_RULE") else {}),
            # the cheap models' time budget, the context pack, the strong reviewer's model (packaging/npm dispatch.mjs,
            # context_pack.mjs, accept.mjs): set per run, so the final series can use what the survey measured
            **({"STEALTHLAB_DISPATCH_TIMEOUT_S": os.environ["ABTEST_CHEAP_TIMEOUT_S"]} if os.environ.get("ABTEST_CHEAP_TIMEOUT_S") else {}),
            **({"STEALTHLAB_DISPATCH_CONTEXT_PACK": "on"} if os.environ.get("ABTEST_CONTEXT_PACK") == "on" else {}),
            **({"STEALTHLAB_REVIEW_STRONG_MODEL": os.environ["ABTEST_REVIEW_MODEL"]} if os.environ.get("ABTEST_REVIEW_MODEL") else {}),
            # dispatch's own skip-a-provider floor matches the runner's pause point, so no task between the two
            # quietly goes to the session's model
            **({"STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS": os.environ["ABTEST_GC_MIN_TOKENS"]}
               if os.environ.get("ABTEST_GC_MIN_TOKENS") else {}),
            "STEALTHLAB_EXEC_WORKTREE_ROOT": str(LOCAL / "worktrees"),
            "GENERAL_COMPUTE_API_KEY": secrets["gc"],
            # Claude Code removes CLAUDE_CODE_OAUTH_TOKEN from its hooks' environment; StealthLab hands this copy to the
            # claude processes its hooks start (test-first writer, audit, page judge) -- packaging/npm claudeChildEnv
            **({"STEALTHLAB_CLAUDE_CODE_OAUTH_TOKEN": env["CLAUDE_CODE_OAUTH_TOKEN"]} if env.get("CLAUDE_CODE_OAUTH_TOKEN") else {}),
            # z.ai (GLM) for the cheap rungs and an open-model test writer, when backend/.env has it
            **({"Z_AI_KEY": secrets["zai"]} if secrets.get("zai") else {}),
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
# the session could not log in (a wrong or expired token): Claude never ran
AUTH_WORDS = ("Failed to authenticate", "Invalid bearer token", "Not logged in", "Please run /login",
              "OAuth token has expired", "invalid x-api-key")


def run_claude(ws: Path, prompt: str, env: dict[str, str], extra: list[str], log_path: Path,
               resume: str | None = None) -> dict:
    """One `claude -p` turn. `resume` continues that session (multi-turn tasks, multiturn.py); the result carries the
    session's id and the full reply text (`final` is its first 400 characters)."""
    exe = shutil.which("claude") or "claude"
    # --strict-mcp-config: only the MCP servers this run passes (none for A, the test server for C) -- the person's own
    # user-level MCP servers never load in either arm
    if "\n" in prompt or "\r" in prompt:
        raise ValueError("the prompt must be one line (Windows .cmd shims cut arguments at a newline)")
    cmd = [exe, "-p", prompt, "--model", session_model(), "--setting-sources", "project", "--strict-mcp-config",
           "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits", "--allowedTools", *TOOLS,
           *(["--resume", resume] if resume else []), *extra]
    proc = subprocess.Popen(cmd, cwd=ws, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lines: "queue.Queue[str | None]" = queue.Queue()
    threading.Thread(target=lambda: ([lines.put(x) for x in proc.stdout], lines.put(None)), daemon=True).start()
    result, last, answered = None, time.time(), 0
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
            elif msg.get("type") == "assistant":
                answered += 1
    proc.wait()
    if result is None:
        tail = log_path.read_text(encoding="utf-8")[-800:]
        limited = any(w in tail.lower() for w in LIMIT_WORDS)
        return {"status": "rate_limited" if limited else "infra", "error": tail[-300:]}
    text = str(result.get("result") or "")
    if result.get("is_error") and any(w in text.lower() for w in LIMIT_WORDS):
        return {"status": "rate_limited", "error": text[:300]}
    # a prompt the hook stopped (StealthLab's cheap model did the turn) never reaches the model: no assistant message, and
    # Claude Code's result then repeats the session's previous cost -- that is not this turn's (ct-series-v7: every
    # cheap turn after an Opus turn was charged that Opus turn again)
    cost = result.get("total_cost_usd") if answered else 0.0
    return {"status": "done", "is_error": bool(result.get("is_error")), "claude_cost_usd": cost,
            "claude_cost_reported": result.get("total_cost_usd"),
            "turns": result.get("num_turns"), "duration_ms": result.get("duration_ms"),
            "models": sorted((result.get("modelUsage") or {}).keys()), "model_usage": result.get("modelUsage"),
            "final": text[:400], "text": text[:6000], "session_id": result.get("session_id")}


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
        role = "verifier" if "tests_extra/test_extra.py" in str((r.get("input") or {}).get("task") or "") else "attempt"
        for i, a in enumerate(r.get("attempts") or []):
            # what the try did and why it failed, so a discarded answer can be graded afterwards (a passing glm-5.3-flash
            # answer was thrown away on sqlglot-4051 and nobody could tell whether it was right, ct-series-v4)
            pp = (a.get("diff") or {}).get("patch_path") or str(rj.parent / f"a{i + 1}.patch")
            try:
                patch = Path(pp).read_text(encoding="utf-8", errors="replace")[:40000] if Path(pp).exists() else ""
            except OSError:
                patch = ""
            checks = a.get("checks") or []
            tail = str((checks[-1] or {}).get("tail") or "")[-800:] if checks and isinstance(checks[-1], dict) else ""
            attempts.append({"model": a.get("model"), "executor": a.get("executor"), "state": a.get("state"),
                             "cost_usd": a.get("cost_usd"), "tokens": a.get("tokens"), "role": role,
                             "run_id": r.get("id") or r.get("run_id") or rj.parent.name, "duration_s": a.get("duration_s"), "error": str(a.get("error") or "")[:400] or None,
                             "summary": str(a.get("summary") or "")[:400] or None, "check_tail": tail or None, "patch": patch or None})
            cost += float(a.get("cost_usd") or 0)
    return {"open_cost_usd": round(cost, 6), "exec_runs": runs, "attempts": attempts}


def testfirst_cost(home: Path, since: str, until: str) -> float:
    """The session model's test-first calls in a time window (dispatch's ledger, dispatch_costs.jsonl)."""
    led = home / "dispatch_costs.jsonl"
    rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()] if led.exists() else []
    return round(sum(float(e.get("cost_usd") or 0) for e in rows if since <= str(e.get("at", ""))[:19] + "+00:00" <= until), 6)


def sim_llm(secrets: dict[str, str]):
    """The model behind the opener and the simulated user (multiturn.py): glm-5.3-flash on z.ai, key by name Z_AI_KEY;
    when z.ai fails (out of credit, down), gpt-oss-120b on General Compute, so a provider problem cannot stop the run.
    Openers and replies are cached per task, so every arm still gets the same input whichever model wrote it."""
    import multiturn
    primary = multiturn.zai_llm(secrets["zai"]) if secrets.get("zai") else None
    backup = multiturn.zai_llm(secrets["gc"], profile="gpt-oss-120b") if secrets.get("gc") else None

    def call(system: str, user: str) -> str:
        if primary:
            try:
                return primary(system, user)
            except RuntimeError as e:
                if not backup:
                    raise
                print(f"simulated user: {e}; using gpt-oss-120b", flush=True)
        if not backup:
            raise RuntimeError("no model for the simulated user (Z_AI_KEY and the General Compute key are both missing)")
        return backup(system, user)

    return call


def run_conversation(task: dict, arm: str, mod, ws: Path, env: dict[str, str], extra: list[str], log_path: Path, rule: str,
                     secrets: dict[str, str], turns: int) -> dict:
    """The task as a conversation (multiturn.py): a vague opener, a simulated user, `--resume` turns. The same opener for
    every arm (cached per task); the simulated user's replies cached per (task, arm, turn). Each turn is recorded in
    `turn_log`: time, cost, whether a cheap model did it and the message the user was shown, and what it changed."""
    import multiturn
    issue = (mod.issue_text(task) if hasattr(mod, "issue_text") else mod.prompt(task))
    paths = [log_path]
    snap = {"before": multiturn.tree_snapshot(ws)}

    def run_turn(prompt: str, resume: str | None, n: int) -> dict:
        path = log_path if n == 1 else log_path.with_name(f"{log_path.stem}.t{n}.jsonl")
        if path not in paths:
            paths.append(path)
        return run_claude(ws, prompt, env, extra, path, resume=resume)

    def observe(n: int, since: str, until: str, out: dict) -> dict:
        after = multiturn.tree_snapshot(ws)
        info = {"diff": multiturn.tree_diff(ws, snap["before"], after)}
        snap["before"] = after
        if is_c(arm):
            runs = open_model_runs(LOCAL / "C_home", since, until)
            shown = multiturn.shown_message(paths[n - 1]) if n - 1 < len(paths) else None
            m = multiturn.DONE_BY.search(shown or "")
            info.update({"open_cost_usd": round(runs["open_cost_usd"] + testfirst_cost(LOCAL / "C_home", since, until), 6),
                         "exec_runs": runs["exec_runs"], "run_ids": sorted({a["run_id"] for a in runs["attempts"] if a.get("run_id")}), "shown": shown, "dispatched": bool(shown),
                         "answered": bool(shown) and shown.startswith("Answered by "),
                         "dispatched_model": m.group(1) if m else None})
        return info

    return multiturn.run_session(task_id=task["task_id"], arm=arm, issue=issue, rule=rule, max_turns=turns, llm=sim_llm(secrets),
                                 openers_dir=LOCAL / "openers", simuser_dir=LOCAL / "simuser", run_turn=run_turn, observe=observe)


ANSWER_JUDGE_MODEL = "claude-sonnet-5-5"
ANSWER_JUDGE_SYSTEM = (
    "You grade a reply an assistant gave a developer in a coding session, for an experiment. You get the issue the session "
    "was about, the fix the project really merged for it, what the session itself had changed when the reply was written "
    "(its diff), the developer's message and the reply. Grade only the reply's content: is what it says about the code, "
    "the session's change and the issue correct, and does it answer the message? A reply that describes the session's own "
    "change correctly is correct even when that change differs from the merged fix. Reply with only a JSON object: "
    '{"grade": "correct" | "incorrect" | "unclear", "why": one sentence}.'
)


def grade_answers(task: dict, mod, rec: dict, env: dict[str, str]) -> None:
    """Each turn a cheap model ANSWERED (StealthLab's "Answered by ...") gets `answer_grade`: a strong model's verdict on it
    against the issue and the merged fix. The final code's hidden tests say nothing about an explanation, so this is how
    a cheap answer's quality is measured. Grading, not the arm's work: its cost is kept apart (answer_grade.cost_usd)."""
    turns = [t for t in rec.get("turn_log") or [] if t.get("answered") and t.get("shown")]
    if not turns:
        return
    try:
        issue = mod.issue_text(task) if hasattr(mod, "issue_text") else mod.prompt(task)
    except Exception:  # noqa: BLE001
        issue = ""
    try:
        gold = str(mod.rec(task).get("patch") or "") if hasattr(mod, "rec") else ""
    except Exception:  # noqa: BLE001
        gold = ""
    exe = shutil.which("claude") or "claude"
    log = rec.get("turn_log") or []
    for t in turns:
        before = "\n".join(str(x.get("diff") or "") for x in log[: log.index(t)]).strip()   # the session's changes so far
        user = (f"The issue:\n{issue[:6000]}\n\nThe merged fix:\n{gold[:12000] or '(not available)'}\n\n"
                f"What the session had changed when the reply was written:\n{before[:12000] or '(nothing)'}\n\n"
                f"The developer's message:\n{str(t.get('user_text') or '')[:3000]}\n\nThe reply:\n{str(t['shown'])[:8000]}")
        cmd = [exe, "-p", "--model", judge_model(), "--system-prompt", ANSWER_JUDGE_SYSTEM, "--tools", "", "--setting-sources", "user",
               "--strict-mcp-config", "--output-format", "json"]
        try:
            with tempfile.TemporaryDirectory() as cwd:
                p = subprocess.run(cmd, input=user, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)
            j = json.loads(p.stdout.strip().splitlines()[-1])
            m = re.search(r"\{[\s\S]*\}", str(j.get("result") or ""))
            v = json.loads(m.group(0)) if m else {}
            grade = v.get("grade") if v.get("grade") in ("correct", "incorrect", "unclear") else None
            t["answer_grade"] = {"grade": grade, "why": str(v.get("why") or "")[:300], "model": ANSWER_JUDGE_MODEL,
                                 "cost_usd": j.get("total_cost_usd")}
        except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
            t["answer_grade"] = {"grade": None, "why": f"not graded: {type(e).__name__}", "model": ANSWER_JUDGE_MODEL}


def run_one(task: dict, arm: str, run: str, secrets: dict[str, str], turns: int = 1) -> dict:
    """One task in one arm. turns > 1: a conversation of up to that many user turns (multiturn.py) instead of one prompt."""
    mod = task_module(task)
    ws = workspace(arm, task["workspace"])
    if is_c(arm):
        wipe_stealth(ws, arm)                   # what each arm is: C with only its own state carried over in .kell/ (ARM_STATE)
    if hasattr(mod, "setup_project") and not (ws / ".series_ready").exists():
        mod.setup_project(task, ws)             # a series: the shared project, built once per workspace and arm
        (ws / ".series_ready").write_text("", encoding="utf-8")
    if hasattr(mod, "prepare_task"):
        mod.prepare_task(task, ws)              # a series on a real repository: the files go to THIS task's base commit
    for name, text in mod.files(task, HERE / f"{task['module']}.py").items():
        if isinstance(text, bytes):          # e.g. a screenshot to recreate
            (ws / name).write_bytes(text)
        else:
            (ws / name).write_text(text, encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "--allow-empty", "-m", f"task {task['task_id']}")
    env = arm_env(arm, secrets, getattr(mod, "CHECK", ""))
    survey = setup_survey(arm, ws, env) if is_c(arm) else None     # once, before issue 1, after the files are at its base commit
    # the project's own Python (both arms): `python` in the workspace has the libraries the task uses, as in a real
    # project -- so the agent, dispatch and the test-first tests all run where the code would
    python_dir = mod.python_dir(task, ws) if hasattr(mod, "python_dir") else getattr(mod, "PYTHON_DIR", None)
    if python_dir:
        env["PATH"] = str(python_dir) + os.pathsep + env.get("PATH", "")
    # arm C is `install --with-exec` (setup_c.mjs): hooks only -- the session gets no MCP server, exactly as installed
    extra = []
    logs = LOCAL / "logs" / run
    logs.mkdir(parents=True, exist_ok=True)
    started = now()
    t0 = time.time()
    log_path = logs / f"{task['task_id']}.{arm}.jsonl"
    rule = getattr(mod, "RULE", RULE)
    if turns > 1:
        out = run_conversation(task, arm, mod, ws, env, extra, log_path, rule, secrets, turns)
        turn_logs = [log_path, *sorted(logs.glob(f"{task['task_id']}.{arm}.t*.jsonl"))]   # not C0's: its names start "C0."
        out["peeked"] = sorted({h for p in turn_logs for h in peeked(p)})
    else:
        out = run_claude(ws, mod.prompt(task) + rule, env, extra, log_path)
        out.pop("text", None)                    # the full reply and session id are only kept for conversations
        out.pop("session_id", None)
        out["peeked"] = peeked(log_path)
    rec = {"run": run, "task_id": task["task_id"], "suite": task["suite"], "workspace": task["workspace"], "arm": arm,
           "model": MODEL, "auth": os.environ.get("ABTEST_AUTH") or "subscription", "login": os.environ.get("ABTEST_TOKEN_ID"), "started_at": started, "seconds": round(time.time() - t0, 1), **out,
           **({"series": task["series"], "position": task["position"]} if "series" in task else {})}
    if is_c(arm):
        rec["hook_jobs_drained"] = wait_hook_jobs(LOCAL / "C_home")
        rec.update(open_model_runs(LOCAL / "C_home", started, now()))
        # the session model's test-first calls (dispatch's ledger): part of C's cost
        rec["testfirst_cost_usd"] = testfirst_cost(LOCAL / "C_home", started, now())
        rec["open_cost_usd"] = round(float(rec.get("open_cost_usd") or 0) + rec["testfirst_cost_usd"], 6)
        lib = ws / ".kell" / "library.md"
        if survey:
            rec["setup_survey"] = survey            # the one-time cost, put against the issue it was paid in front of
        rec["library_entries"] = sum(1 for ln in lib.read_text(encoding="utf-8").splitlines()
                                     if ln.startswith("GOAL|")) if lib.exists() else 0
    if out["status"] == "done":
        if hasattr(mod, "grade_workspace"):
            rec["grade"] = mod.grade_workspace(task, ws)
        else:
            solution = (ws / getattr(mod, "solution_file", lambda t: "solution.py")(task)).read_text(encoding="utf-8")
            rec["grade"] = mod.grade(task, solution)
        rec["resolved"] = rec["grade"]["resolved"]
        rec["total_cost_usd"] = round(float(rec.get("claude_cost_usd") or 0) + float(rec.get("open_cost_usd") or 0), 6)
    if is_c(arm):
        grade_answers(task, mod, rec, arm_env("A", secrets))
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "--allow-empty", "-m", f"after {task['task_id']}")
    return rec


# ---------------------------------------------------------------- the run

def dry_run(args, arms: list[str]) -> int:
    """What a run would do, in order: no server, no keys, no model, nothing written outside the temp listing."""
    tasks = load_series(args.series) if args.series else load_tasks(args.tasks, args.seed)
    print(f"run {args.run}: {len(tasks)} task(s), arms {','.join(arms)}" + (f", series {args.series}" if args.series else ""))
    if args.turns > 1:
        print(f"  conversations: up to {args.turns} user turns per task -- a vague opener (the same for every arm, cached), then a "
              "simulated user answers questions or says thanks; turns are `claude --resume`")
    start = {"A": "plain Claude Code; its workspace has no .claude/ and no .kell/, and persists across the series",
             "C": "StealthLab hooks-only install; its .kell/ (facts, library, routing counts) and the server's router state PERSIST",
             "C0": "the same install as C, but .kell/ is wiped before every task and the server's router state is reset (no survey)",
             "C-R": "the same install; the facts (one survey at series start) stay, the library and routing counts are wiped, the router state is reset",
             "C-L": "the same install; facts and library stay, the local routing counts are wiped, the router state is reset"}
    for a in arms:
        print(f"  arm {a}: {start[a]}")
    for t in tasks:
        order = sorted(arms, key=lambda a: hashlib.sha256(f"{args.seed}|{t['task_id']}|{a}".encode()).hexdigest())
        print(f"  {t.get('position', '-'):>3}  {t['task_id']:<28} workspace {t['workspace']:<18} arms in order {','.join(order)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--tasks", default="", help="e.g. ds1000:5")
    ap.add_argument("--series", default="", help="a long-horizon series: module[:name] (see series_toy_task.py); instead of --tasks")
    ap.add_argument("--dry-run", action="store_true", help="print the plan (tasks, arms, what each arm starts with) and run nothing")
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--arms", default="A,C")
    ap.add_argument("--exclude-runs", default="", help="comma-separated earlier runs whose tasks are skipped")
    ap.add_argument("--only", default="", help="comma-separated task ids: run just these from the sampled tasks")
    ap.add_argument("--gc-min-tokens", type=int, default=100000,
                    help="arm C: pause (exit 4) when the General Compute key has fewer tokens left today (0: off)")
    ap.add_argument("--gc-key-name", default="GENERAL_COMPUTE_API_KEY",
                    help="arm C: the backend/.env entry holding the General Compute key (e.g. a second key's daily quota)")
    ap.add_argument("--parallel", action="store_true", help="arm C: the cheap models run at once (first pass wins)")
    ap.add_argument("--testfirst-model", default="", help="arm C: the model that writes the test-first tests")
    ap.add_argument("--turns", type=int, default=1,
                    help="user turns per task (opener included, 1-4): 1 sends the task's own prompt; 2+ sends a vague opener "
                         "and a simulated user continues with `claude --resume` (multiturn.py; needs Z_AI_KEY in backend/.env)")
    args = ap.parse_args()
    if not 1 <= args.turns <= 4:
        raise SystemExit(f"--turns is 1-4 (user turns per task, the opener included); got {args.turns}")
    if bool(args.tasks) == bool(args.series):
        raise SystemExit("give exactly one of --tasks and --series")
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in ARMS]
    if bad or (any(a in ARM_STATE and a != "C" for a in arms) and not args.series):
        raise SystemExit(f"arms are {', '.join(ARMS)} (C0, C-R and C-L only with --series); got {args.arms}")
    if args.dry_run:
        return dry_run(args, arms)

    secrets = {"token": read_token(arms),
               "gc": secret_value(args.gc_key_name), "zai": secret_value("Z_AI_KEY")}
    if not secrets["gc"]:
        raise SystemExit(f"no {args.gc_key_name} in the environment or backend/.env")
    if args.turns > 1 and not secrets["zai"]:
        raise SystemExit("--turns needs Z_AI_KEY in the environment or backend/.env (glm-5.3-flash writes the opener and plays the user)")
    if any(is_c(a) for a in arms):
        try:
            urllib.request.urlopen(f"{SERVER}/healthz", timeout=10)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"the test server is not up at {SERVER} ({exc}); start experiments/abtest/run_backend.py")
    if any(is_c(a) for a in arms):
        print(f"warm-up lookups: {warm(secrets)} s", flush=True)
    results = LOCAL / "results" / f"{args.run}.jsonl"
    results.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("status") == "done":
                done.add((r["task_id"], r["arm"]))
    only = {t.strip() for t in args.only.split(",") if t.strip()}
    if args.testfirst_model:
        os.environ["ABTEST_TESTFIRST_MODEL"] = args.testfirst_model
    if args.parallel:
        os.environ["ABTEST_PARALLEL"] = "on"
    os.environ["ABTEST_GC_MIN_TOKENS"] = str(args.gc_min_tokens)
    seen = frozenset(json.loads(line)["task_id"] for r in args.exclude_runs.split(",") if r.strip()
                     for line in (LOCAL / "results" / f"{r.strip()}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    tasks = load_series(args.series) if args.series else load_tasks(args.tasks, args.seed, seen)
    for task in tasks:
        if only and task["task_id"] not in only:
            continue
        order = sorted(arms, key=lambda a: hashlib.sha256(f"{args.seed}|{task['task_id']}|{a}".encode()).hexdigest())
        for arm in order:
            if (task["task_id"], arm) in done:
                continue
            if is_c(arm) and args.gc_min_tokens > 0:
                left = gc_tokens_left(secrets["gc"])
                if left is not None and left < args.gc_min_tokens:
                    # the cheap models' provider is nearly out for today: a run now would only measure that
                    print(f"General Compute has {left} tokens left today (< {args.gc_min_tokens}): pausing. "
                          "Rerun the same command after its daily reset (or with another key) to continue.", flush=True)
                    return 4
            print(f"[{now()}] {task['task_id']} arm {arm} ...", flush=True)
            rec = run_one(task, arm, args.run, secrets, args.turns)
            if any(w in str(rec.get("final") or "") for w in AUTH_WORDS):
                # no login: the session never ran, so the record would be a fake "not solved" -- not written, run stops
                print(f"Claude Code could not log in ({str(rec.get('final'))[:120]}): stopping, nothing recorded for this "
                      "task. Fix the token (claude setup-token) and rerun the same command.", flush=True)
                return 5
            with results.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            if os.environ.get("ABTEST_BLIND"):            # series10 is blind: the container log shows progress, not how an arm did
                print(f"    {rec['status']}", flush=True)
            else:
                print(f"    {rec['status']} resolved={rec.get('resolved')} cost={rec.get('total_cost_usd')} "
                      f"{rec.get('seconds')}s", flush=True)
            if rec["status"] == "rate_limited":
                print("usage limit reached: stopping. Rerun the same command after the reset to continue.")
                return 3
    print(f"done: {results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
