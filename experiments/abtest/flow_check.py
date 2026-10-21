"""flow_check: the scripted real-session check of StealthLab's Claude Code hooks. Every StealthLab change should pass it.

What a developer does in a repository that has never seen StealthLab and has no check configured, as one session:

  1 fix     a messy first prompt ("add is broken, fix it pls") -> dispatched from the FIRST prompt (a new repo gets its
            .stealth/), the prompt is stopped with "Done by <cheap model> in N s ... stealthlab-mcp undo", the fix is in the tree
            and the project's tests pass
  2 it      a follow-up that only makes sense with the first ("now also make it reject strings - raise a TypeError") -> "it" is
            resolved, dispatched, add() raises TypeError for a str; WARNs if the change went wider than add() (the scope
            creep seen in the Cursor test, 2026-10-14)
  3 own     the developer edits a file of their own (notes.md)
  4 undo    `stealthlab-mcp undo` -> only the LAST change is undone (add() keeps the step-1 fix, loses the TypeError check),
            and the developer's own file is untouched
  5 thanks  "thanks" -> NOT dispatched; the continuity note ("Earlier in this session, <model> changed ...") is delivered
  6 again   one more plain prompt ("ok great") -> NOT dispatched; the continuity note is NOT repeated ("shown once")
  7 opus    "!opus explain what calc.py does" -> not dispatched, the session's model is left alone

Each step is PASS, WARN (a quality finding, not a failure) or FAIL; the exit code is 0 with no FAIL, 1 with any, 2 when the
check could not start (see `preflight`). It runs the real hook (`stealthlab-mcp hook-prompt`) with Claude Code-shaped payloads
against a real backend and the real cheap models, so it costs a few cents of open-model tokens and the test-first call of the
session model; it never starts Claude Code itself and never touches your own ~/.claude or ~/.stealthlab (a sandbox home).

    python experiments/abtest/flow_check.py                 # needs the test backend (run_backend.py) and the keys, see preflight
    python experiments/abtest/flow_check.py --dry-run       # prints the steps, runs nothing
    python experiments/abtest/flow_check.py --keep --out flow.json

Keys by NAME: GENERAL_COMPUTE_API_KEY (and Z_AI_KEY for GLM) are read from the environment, else from backend/.env, and go to the
hook's child process only -- never printed, never written. The test token is experiments/abtest/.local/token.txt.

Assumptions not verified against a real Claude Code (said here so a surprise has somewhere to start): the hook's stdout is one JSON
object ({"decision":"block","reason":...} when it dispatches); a real transcript may leave the blocked first prompt out -- this
check writes it in (`--transcript none` leaves the transcript out to test that case).
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BIN = REPO / "packaging" / "npm" / "bin" / "stealthlab-mcp.mjs"
SERVER = os.environ.get("ABTEST_SERVER", "http://127.0.0.1:8765")
MODEL = os.environ.get("ABTEST_MODEL", "claude-opus-5-5")
SLOW_S = 90                           # a dispatched prompt slower than this is a WARN (the user waits that long)
STEPS = ("fix", "it", "own", "undo", "thanks", "again", "opus")

CALC = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
TESTS = "from calc import add, mul, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n    assert add(-1, 1) == 0\n\n\ndef test_sub():\n    assert sub(5, 3) == 2\n\n\ndef test_mul():\n    assert mul(4, 3) == 12\n"
CONFTEST = "import os\nimport sys\n\nsys.path.insert(0, os.path.dirname(__file__))\n"
FIRST = "add is broken, fix it pls"
SECOND = "now also make it reject strings - raise a TypeError"
OWN_TEXT = "my own notes - do not touch\n"
CONTINUITY = "Earlier in this session,"


@dataclass
class HookResult:
    out: dict                          # the hook's JSON answer ({} = the prompt goes on untouched)
    seconds: float
    log: str = ""                      # what it printed besides (the hook logs there too)

    @property
    def dispatched(self) -> bool:
        return self.out.get("decision") == "block" and str(self.out.get("reason", "")).startswith("Done by")

    @property
    def reason(self) -> str:
        return str(self.out.get("reason") or "")

    @property
    def context(self) -> str:
        return str((self.out.get("hookSpecificOutput") or {}).get("additionalContext") or "")


@dataclass
class Step:
    name: str
    status: str = "PASS"               # PASS | WARN | FAIL
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    shown: str | None = None           # the message a dispatch showed the user

    def fail(self, why: str) -> None:
        self.status = "FAIL"
        self.notes.append(why)

    def warn(self, why: str) -> None:
        if self.status == "PASS":
            self.status = "WARN"
        self.notes.append(why)

    def check(self, ok: bool, why: str) -> bool:
        if not ok:
            self.fail(why)
        return ok


# ---------------------------------------------------------------- the sandbox: a fresh repository, a transcript, the hook's environment

def make_repo(root: Path) -> Path:
    """A fresh git repository StealthLab has never seen: no .stealth/, no .claude/, no check configured anywhere."""
    repo = root / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(CALC, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")
    (repo / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.email", "flow@stealthlab.invalid"], ["config", "user.name", "flow"],
                 ["add", "-A"], ["commit", "-q", "-m", "calc"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return repo


def write_transcript(path: Path, turns: list[tuple[str, str]], model: str = MODEL) -> None:
    """A Claude Code session transcript (JSONL) holding these (role, text) turns -- the shape lib/brief.mjs reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for role, text in turns:
        msg = {"role": role, "content": [{"type": "text", "text": text}] if role == "assistant" else text}
        if role == "assistant":
            msg["model"] = model
        rows.append(json.dumps({"type": role, "message": msg}))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def open_units() -> list[str]:
    cfg = json.loads((HERE / "exec.json").read_text(encoding="utf-8"))
    return [f"{m}|{ex}" for ex, spec in cfg["executors"].items() for m in spec["models"]]


def read_env_names(names: list[str]) -> dict[str, str]:
    """The named variables, from the environment or else backend/.env. Only the values that exist are returned."""
    out = {n: os.environ[n] for n in names if os.environ.get(n)}
    env_file = REPO / "backend" / ".env"
    missing = [n for n in names if n not in out]
    if missing and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() in missing and v.strip():
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def hook_env(home: Path, token: str, keys: dict[str, str], *, base: dict[str, str] | None = None) -> dict[str, str]:
    """What the hook process sees: a sandbox StealthLab home, the test backend, the cheap models and their keys, and NO
    check command (no STEALTHLAB_DISPATCH_CHECK: the fresh-repo case)."""
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not k.startswith("STEALTHLAB_") and k not in ("ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR")}
    env.update({"STEALTHLAB_HOME": str(home), "STEALTHLAB_MCP_URL": f"{SERVER}/mcp", "STEALTHLAB_TOKEN": token,
                "STEALTHLAB_HOOK_CANDIDATES": ",".join(open_units() + [f"{MODEL}|claude-code"]), "STEALTHLAB_HOOK_TIMEOUT_MS": "60000",
                "STEALTHLAB_EXEC_WORKTREE_ROOT": str(home / "worktrees"), "ANTHROPIC_MODEL": MODEL, **keys})
    return env


def preflight(*, token_file: Path | None = None) -> list[str]:
    """Why the check cannot run here, or [] when it can: node, the backend, the test token, the cheap models' key."""
    problems = []
    if not shutil.which("node"):
        problems.append("node is not on PATH")
    tf = token_file or HERE / ".local" / "token.txt"
    if not tf.exists():
        problems.append(f"no test token at {tf} (python experiments/abtest/local_identity.py)")
    try:
        urllib.request.urlopen(f"{SERVER}/healthz", timeout=10)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"the test backend is not up at {SERVER} ({type(exc).__name__}); start experiments/abtest/run_backend.py")
    if not read_env_names(["GENERAL_COMPUTE_API_KEY"]):
        problems.append("GENERAL_COMPUTE_API_KEY is not set (environment or backend/.env)")
    if not shutil.which("git"):
        problems.append("git is not on PATH")
    return problems


# ---------------------------------------------------------------- running the real hooks

class Hooks:
    """The real thing: `stealthlab-mcp hook-prompt` and `stealthlab-mcp undo` as child processes."""

    def __init__(self, repo: Path, home: Path, env: dict[str, str], transcript: Path, session_id: str = "flow-1"):
        self.repo, self.home, self.env, self.transcript, self.session_id = repo, home, env, transcript, session_id

    def prompt(self, text: str, timeout: int = 700) -> HookResult:
        payload = {"session_id": self.session_id, "transcript_path": str(self.transcript), "cwd": str(self.repo),
                   "hook_event_name": "UserPromptSubmit", "prompt": text}
        t0 = time.time()
        r = subprocess.run(["node", str(BIN), "hook-prompt"], input=json.dumps(payload), env=self.env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return HookResult(parse_hook_output(r.stdout), round(time.time() - t0, 1), (r.stdout + r.stderr)[-600:])

    def undo(self) -> str:
        r = subprocess.run(["node", str(BIN), "undo"], cwd=self.repo, env=self.env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        return (r.stdout + r.stderr).strip()


def parse_hook_output(stdout: str) -> dict:
    """The hook's answer: the last line that is a JSON object (the hook may log other lines before it); {} when none."""
    for line in reversed([ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]):
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return {}


# ---------------------------------------------------------------- the checks

def run_python(repo: Path, code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=repo, capture_output=True, text=True, timeout=60)


def tests_pass(repo: Path) -> bool:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=repo, capture_output=True, text=True,
                          timeout=120).returncode == 0


def function_source(path: Path, name: str) -> str | None:
    src = path.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    return None


def raises_type_error_for_str(repo: Path, fn: str) -> bool:
    # ('a', 'b') and not ('a', 1): the buggy a - b raises TypeError for any str too; only a + b makes the strictness visible
    r = run_python(repo, f"from calc import {fn}\ntry:\n    {fn}('a', 'b')\nexcept TypeError:\n    print('TE')\n")
    return "TE" in r.stdout


def dispatch_checks(st: Step, res: HookResult, *, name: str) -> None:
    st.seconds = res.seconds
    if st.check(res.dispatched, f"{name} was not dispatched: the prompt went on to the session's model (hook said {json.dumps(res.out)[:160]})"):
        st.shown = res.reason
        st.check("stealthlab-mcp undo" in res.reason, "the message does not say how to undo")
        st.check("calc.py" in res.reason, "the message does not name calc.py")
        st.check("$" in res.reason.split("\n\n")[0], "the message shows no cost")
        if res.seconds > SLOW_S:
            st.warn(f"the prompt was held {res.seconds:.0f} s (over {SLOW_S} s)")


def run_flow(hooks, repo: Path, *, transcript_mode: str = "earlier", log=print) -> list[Step]:
    """The seven steps against `hooks` (anything with .prompt(text) -> HookResult and .undo() -> str) and the repository."""
    steps: list[Step] = []
    original = {fn: function_source(repo / "calc.py", fn) for fn in ("sub", "mul")}

    def begin(name: str) -> Step:
        st = Step(name)
        steps.append(st)
        log(f"[{name}] ...")
        return st

    # 1 fix: a messy first prompt in a repo StealthLab has never seen
    st = begin("fix")
    res = hooks.prompt(FIRST)
    dispatch_checks(st, res, name="the first prompt")
    st.check(tests_pass(repo), "the project's tests do not pass after the first dispatch")
    st.check((repo / ".stealth").is_dir(), "a new repository did not get its .stealth/ from the first prompt")
    after_fix = (repo / "calc.py").read_text(encoding="utf-8")

    # 2 it: a follow-up that needs the first one
    if transcript_mode == "earlier":
        write_transcript(hooks.transcript, [("user", FIRST), ("assistant", (res.reason or "Done.")[:600])])
    st = begin("it")
    res = hooks.prompt(SECOND)
    dispatch_checks(st, res, name="the follow-up with 'it'")
    if st.check(raises_type_error_for_str(repo, "add"), "add('a', 'b') does not raise TypeError: 'it' was not resolved to add()"):
        wider = [fn for fn in ("sub", "mul") if function_source(repo / "calc.py", fn) != original[fn]]
        if wider:
            st.warn(f"the change went wider than add(): {', '.join(wider)} changed too (scope wider than asked)")
    st.check(tests_pass(repo), "the project's tests do not pass after the follow-up")
    after_it = (repo / "calc.py").read_text(encoding="utf-8")

    # 3 own: the developer's own edit
    st = begin("own")
    (repo / "notes.md").write_text(OWN_TEXT, encoding="utf-8")

    # 4 undo: only the last change, and not the developer's file
    st = begin("undo")
    text = hooks.undo()
    st.check("undone" in text.lower(), f"undo did not say it undid anything: {text[:160]!r}")
    now = (repo / "calc.py").read_text(encoding="utf-8")
    st.check(now == after_fix, "undo did not restore calc.py to the state after the FIRST change"
             + (" (it left the follow-up's change in place)" if now == after_it else ""))
    st.check(raises_type_error_for_str(repo, "add") is False, "add() still raises TypeError after undo: the follow-up was not undone")
    st.check((repo / "notes.md").exists() and (repo / "notes.md").read_text(encoding="utf-8") == OWN_TEXT,
             "undo touched the developer's own file")

    # 5 thanks: not dispatched; the continuity note is delivered once
    st = begin("thanks")
    res = hooks.prompt("thanks")
    st.seconds = res.seconds
    st.check(not res.dispatched, "'thanks' was dispatched")
    st.check(CONTINUITY in res.context, "the continuity note ('Earlier in this session, <model> changed ...') was not delivered at the next prompt")
    if CONTINUITY in res.context:
        st.shown = res.context

    # 6 again: and not repeated
    st = begin("again")
    res = hooks.prompt("ok great")
    st.seconds = res.seconds
    st.check(not res.dispatched, "'ok great' was dispatched")
    st.check(CONTINUITY not in res.context, "the continuity note was repeated: it must be shown once")

    # 7 opus: the override leaves the session's model alone
    st = begin("opus")
    res = hooks.prompt("!opus explain what calc.py does")
    st.seconds = res.seconds
    st.check(not res.dispatched, "'!opus ...' was dispatched")
    return steps


def report(steps: list[Step]) -> str:
    lines = [f"{'step':<8} {'result':<6} {'s':>6}  notes"]
    for s in steps:
        lines.append(f"{s.name:<8} {s.status:<6} {s.seconds:>6.1f}  " + "; ".join(n for n in s.notes if n))
        if s.shown and s.name in ("fix", "it"):
            lines.append("         shown: " + s.shown.replace("\n", " | ")[:300])
    bad = [s for s in steps if s.status == "FAIL"]
    lines.append(f"{sum(s.status == 'PASS' for s in steps)} pass, {sum(s.status == 'WARN' for s in steps)} warn, {len(bad)} fail"
                 + ("" if not bad else ": " + ", ".join(s.name for s in bad)))
    return "\n".join(lines)


def exit_code(steps: list[Step]) -> int:
    return 1 if any(s.status == "FAIL" for s in steps) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the scripted real-session check of the Claude Code hooks")
    ap.add_argument("--dry-run", action="store_true", help="print the steps and what is needed; run nothing")
    ap.add_argument("--keep", action="store_true", help="keep the sandbox (repo, home, hook log) and print where it is")
    ap.add_argument("--out", default="", help="write the steps as JSON here")
    ap.add_argument("--transcript", choices=("earlier", "none"), default="earlier", help="write the earlier turns into the transcript the hook reads")
    ap.add_argument("--token-file", default="", help="the test backend's token (default experiments/abtest/.local/token.txt)")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(__doc__.split("Each step is")[0].split("What a developer does")[1].strip("\n:"))
        print("\nneeds: node, git, the test backend at", SERVER, "(run_backend.py), experiments/abtest/.local/token.txt, "
              "GENERAL_COMPUTE_API_KEY (and optionally Z_AI_KEY); the session model's login for the test-first step")
        return 0
    token_file = Path(args.token_file) if args.token_file else HERE / ".local" / "token.txt"
    problems = preflight(token_file=token_file)
    if problems:
        print("flow_check cannot start:\n  - " + "\n  - ".join(problems))
        return 2
    root = Path(tempfile.mkdtemp(prefix="flow_check_"))
    try:
        repo, home = make_repo(root), root / "home"
        home.mkdir()
        shutil.copyfile(HERE / "exec.json", home / "exec.json")
        (home / "config.json").write_text(json.dumps({"dispatch": True}), encoding="utf-8")
        env = hook_env(home, token_file.read_text(encoding="utf-8").strip(), read_env_names(["GENERAL_COMPUTE_API_KEY", "Z_AI_KEY"]))
        transcript = root / "projects" / "flow" / "flow-1.jsonl"
        if args.transcript == "none":
            transcript.parent.mkdir(parents=True, exist_ok=True)
        steps = run_flow(Hooks(repo, home, env, transcript), repo, transcript_mode=args.transcript, log=lambda m: print(m, flush=True))
        print(report(steps))
        if args.out:
            Path(args.out).write_text(json.dumps([s.__dict__ for s in steps], indent=1), encoding="utf-8")
        if args.keep:
            print(f"sandbox kept: {root} (hook log: {home / 'hooks.log'})")
        return exit_code(steps)
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
