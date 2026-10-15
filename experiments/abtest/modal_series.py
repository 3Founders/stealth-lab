"""modal_series: the long-horizon test (arms A, C, C0 over a real repository's issues, runner.py --series) on Modal.

Why Modal: each repository needs Linux and its own Python (the SWE-rebench environments are 3.9), the StealthLab backend needs
Postgres with pgvector, and a series is many long Claude Code sessions -- none of that belongs on a laptop. One container per
repository holds everything: Postgres + the StealthLab test backend (run_backend.py, a local identity), Node + Claude Code, `uv`
and the Pythons the repositories need, and runs `runner.py` over the series task by task. Results and logs go to a Modal Volume.

    # once: the secrets (values from YOUR environment / backend/.env by NAME; nothing is printed, nothing goes on a command line)
    $env:CLAUDE_CODE_OAUTH_TOKEN = "<from `claude setup-token`>"
    python experiments/abtest/modal_series.py secrets --claude        # -> Modal secret "claude-code-auth"
    python experiments/abtest/modal_series.py secrets --keys          # -> Modal secret "stealthlab-keys"

    python experiments/abtest/modal_series.py plan --repos jsonargparse --max-issues 1     # what a run would do; no Modal call
    modal run experiments/abtest/modal_series.py --repos jsonargparse --max-issues 1       # the dry run: 1 issue, 3 arms
    modal run experiments/abtest/modal_series.py --repos jsonargparse --max-issues 8       # 8 issues x 3 arms = 24 sessions

Auth (`--auth`; for `api` also set ABTEST_MODAL_AUTH=api when you run `modal run`): `subscription` (default) uses the owner's Claude Code login as a token (`claude setup-token`, Modal secret
`claude-code-auth` holding CLAUDE_CODE_OAUTH_TOKEN): sessions count against the owner's 5-hour and weekly limits, so repositories
and arms run ONE AT A TIME and a usage limit stops the run (resume by repeating the command). `api` uses an Anthropic API key
(secret `anthropic-api` holding ANTHROPIC_API_KEY), billed per token; repositories run in parallel (arms still one after another
inside a repository: they share its backend and each task's arms must see the same state).

Choices, so a surprise has somewhere to start (plan section 4 says "one image per repo"):
  * ONE image, not one per repository: Postgres/pgvector, Node, Claude Code, the backend's requirements, `uv` with Pythons
    3.9-3.12. A repository's own state (bare clone, pinned dependencies, the dataset file) is built at run time into a Volume
    cache, so a new repository costs minutes, not an image build.
  * Resume: after EVERY task the container copies the run's results, logs, the arms' workspaces (with their .stealth/) and a
    dump of the test database to the Volume; a repeated command restores them and the runner skips what is done. Resuming
    arm C/C0 without that snapshot would restart their `.stealth/` from nothing and break the learning curve, so it is refused
    unless --allow-cold-resume.
  * The Volume path is /mnt/series/<run>/<repository>/ (results/, logs/, state.tgz, db.sql.gz, report.txt).
  * A, C and C0 of one repository share ONE test backend (as in a local run): what the server learned from C's outcomes is also
    visible to C0. Wiping .stealth/ does not wipe the server. Say so wherever C minus C0 is quoted.

Nothing here has run on Modal yet. The offline tests check the plan, the auth handling, what is (not) uploaded, the order of the
container's commands and the snapshot/resume logic against stand-ins; the Postgres, backend and Claude Code commands inside the
container are unverified until the dry run (1 issue per arm) has been done.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

try:                                  # the planning and the offline tests need no Modal
    import modal
except ImportError:                   # pragma: no cover
    modal = None

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1] if len(HERE.parents) > 1 else HERE      # inside the container Modal puts this file at /root/modal_series.py

APP_NAME = "stealthlab-series"
VOLUME_NAME = "stealthlab-series"
KEYS_SECRET = "stealthlab-keys"           # GENERAL_COMPUTE_API_KEY, Z_AI_KEY, the backend's judge/embedding keys
CLAUDE_SECRET = "claude-code-auth"        # CLAUDE_CODE_OAUTH_TOKEN
API_SECRET = "anthropic-api"              # ANTHROPIC_API_KEY
KEY_NAMES = ("GENERAL_COMPUTE_API_KEY", "general_compute_api_key1", "Z_AI_KEY", "JEV_API_KEY", "JEV_BASE_URL", "JEV_MODEL",
             "JEV_CAPABILITIES", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "VOYAGE_API_KEY")
ARMS = ("A", "C", "C0")
AUTHS = ("subscription", "api")
SERIES_MODULE = "swe_series_task"
PYTHONS = ("3.9", "3.10", "3.11", "3.12")
VOL = "/mnt/series"                              # POSIX strings: they are paths inside the Linux container (Modal needs them absolute)
APP_DIR = "/app"
PG_BIN = "/usr/lib/postgresql/15/bin"
PG_DATA = "/var/lib/postgresql/data"
PG_PORT = 55432
DSN = f"postgresql://kel_owner@127.0.0.1:{PG_PORT}/kel_abtest"
HEALTH = "http://127.0.0.1:8765/healthz"
RC_RATE_LIMITED, RC_QUOTA = 3, 4          # runner.py's exit codes: a usage limit; the cheap models' daily quota
PLAN_ESTIMATE = "plan's estimate: 24 sessions (1 repo x 8 issues x 3 arms) ~ $15-30 of Opus-equivalent; 90 sessions (3 x 10 x 3) ~ $50-150 on the API"


# ---------------------------------------------------------------- the run, as data

@dataclass
class RunSpec:
    repos: list[str]
    arms: list[str] = field(default_factory=lambda: list(ARMS))
    max_issues: int = 0                    # 0: every issue of the series
    auth: str = "subscription"
    turns: int = 1                         # 1: the task's own prompt; 2-4: vague opener + simulated user (multiturn.py)
    run: str = ""
    series_module: str = SERIES_MODULE
    seed: int = 20261014
    testfirst_model: str = "glm-5.3"            # the test writer; a slow or failed open writer falls back to Sonnet (dispatch)
    gc_key_name: str = "GENERAL_COMPUTE_API_KEY"
    allow_cold_resume: bool = False
    validate: bool = True                  # the gold check on the issues about to run (swe_series_task only); False skips it
    split_arms: bool = True                # one container per arm, so A, C and C0 run at the same time; False: all arms in one container
    task_ids: list[str] | None = None      # the issues to run, already gold-checked (prepare); None: this container picks them itself
    unit_arm: str = ""                     # set for the one-arm container of a split run: its results live in <run>/<repository>/<arm>/

    def run_name(self) -> str:
        return self.run or f"long-{'-'.join(self.repos)}-{self.turns}t"


def validate_spec(spec: RunSpec) -> list[str]:
    problems = []
    if not spec.repos:
        problems.append("--repos is empty")
    bad = [a for a in spec.arms if a not in ARMS]
    if bad or not spec.arms:
        problems.append(f"arms are {', '.join(ARMS)}; got {','.join(spec.arms) or 'none'}")
    if spec.auth not in AUTHS:
        problems.append(f"--auth is one of {', '.join(AUTHS)}; got {spec.auth!r}")
    if spec.max_issues < 0:
        problems.append("--max-issues cannot be negative")
    if not 1 <= spec.turns <= 4:
        problems.append("--turns is 1-4")
    if spec.unit_arm and spec.arms != [spec.unit_arm]:
        problems.append("a one-arm unit runs exactly its own arm")
    return problems


def series_tasks(spec: RunSpec, repo: str) -> list[dict]:
    """The series' tasks (swe_series_task.series(<repo>)), cut to --max-issues."""
    tasks = importlib.import_module(spec.series_module).series(repo)
    return tasks[: spec.max_issues] if spec.max_issues else tasks


def plan_text(spec: RunSpec, tasks_by_repo: dict[str, list[dict]]) -> str:
    sessions = sum(len(t) for t in tasks_by_repo.values()) * len(spec.arms)
    lines = [f"run {spec.run_name()}: {len(spec.repos)} repo(s), arms {','.join(spec.arms)}, auth {spec.auth}, "
             f"{spec.turns} user turn(s) per task",
             "  repositories run " + ("one after another (a subscription has limits)" if spec.auth == "subscription"
                                      else "in parallel (an API key is billed per token)") + "; "
             + ("the arms run at the same time, one container per arm (each with its own backend: C and C0 do not share server state), "
                "after ONE gold check of the series" if spec.split_arms
                else "the arms run one after another inside a repository")]
    for repo, tasks in tasks_by_repo.items():
        lines.append(f"  {repo}: {len(tasks)} issue(s) -> {len(tasks) * len(spec.arms)} session(s) (x up to {spec.turns} turn(s)): "
                     + ", ".join(t["task_id"] for t in tasks[:3]) + (" ..." if len(tasks) > 3 else ""))
    lines.append(f"  {sessions} session(s) in all; {PLAN_ESTIMATE}")
    lines.append(f"  needs Modal secrets: {KEYS_SECRET} + " + (CLAUDE_SECRET if spec.auth == "subscription" else API_SECRET)
                 + f"; volume {VOLUME_NAME}")
    lines.append(f"  results: /mnt/series/{spec.run_name()}/<repository>/ on the volume (gold_check.json, report.txt"
                 + (", and per arm <arm>/results, <arm>/logs, <arm>/state.tgz, <arm>/db.sql.gz)" if spec.split_arms
                    else ", results/, logs/, state.tgz, db.sql.gz)"))
    return "\n".join(lines)


# ---------------------------------------------------------------- auth and what is uploaded

def auth_env(mode: str, environ: dict[str, str]) -> dict[str, str]:
    """The environment for the runner and Claude Code under `mode`. Raises SystemExit naming the missing variable (never a value)."""
    env = dict(environ)
    if mode == "subscription":
        if not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise SystemExit(f"--auth subscription needs CLAUDE_CODE_OAUTH_TOKEN (Modal secret {CLAUDE_SECRET}; `claude setup-token`)")
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
    elif mode == "api":
        if not env.get("ANTHROPIC_API_KEY"):
            raise SystemExit(f"--auth api needs ANTHROPIC_API_KEY (Modal secret {API_SECRET})")
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    else:
        raise SystemExit(f"--auth is one of {', '.join(AUTHS)}")
    env["ABTEST_AUTH"] = mode
    return env


NEVER_UPLOAD_NAMES = {".git", ".local", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".env"}
NEVER_UPLOAD_SUFFIXES = (".pem", ".key", ".mov", ".pyc", ".sqlite", ".db")


def ignored(path: Path | str) -> bool:
    """True for anything that must not reach the image: secrets (.env files, keys), the local identity and results (.local),
    caches, other people's repositories."""
    p = Path(path)
    return (any(part in NEVER_UPLOAD_NAMES for part in p.parts) or p.name.startswith(".env") or p.name.endswith(".env")
            or p.name.endswith(NEVER_UPLOAD_SUFFIXES))


def build_image():
    """The one image: pgvector Postgres 15, Python 3.12 (the backend), Node + Claude Code, uv with the repositories' Pythons, the
    backend's requirements baked in; the repository's own code mounted last (so an edit does not rebuild the image)."""
    return (
        modal.Image.from_registry("pgvector/pgvector:pg15", add_python="3.12")
        .apt_install("git", "curl", "ca-certificates", "gnupg", "xz-utils")
        .run_commands("curl -fsSL https://deb.nodesource.com/setup_22.x | bash -", "apt-get install -y nodejs",
                      "npm install -g @anthropic-ai/claude-code", "pip install uv",
                      "uv python install " + " ".join(PYTHONS))
        .add_local_file(REPO / "backend" / "requirements.txt", "/tmp/backend-requirements.txt", copy=True)
        .run_commands("pip install -r /tmp/backend-requirements.txt", "pip install uvicorn pytest pyarrow pandas huggingface_hub")
        .env({"PYTHONPATH": f"{APP_DIR}/experiments/abtest", "PYTHONUNBUFFERED": "1", "ABTEST_CONTAINER": "1"})   # NOT the cache variables: see Container.__init__
        .add_local_dir(REPO / "backend" / "app", f"{APP_DIR}/backend/app", ignore=ignored)
        .add_local_dir(REPO / "backend" / "db", f"{APP_DIR}/backend/db", ignore=ignored)
        .add_local_dir(REPO / "backend" / "scripts", f"{APP_DIR}/backend/scripts", ignore=ignored)
        .add_local_dir(REPO / "packaging" / "npm", f"{APP_DIR}/packaging/npm", ignore=ignored)
        .add_local_dir(HERE, f"{APP_DIR}/experiments/abtest", ignore=ignored)
    )


# ---------------------------------------------------------------- inside the container

Shell = Callable[..., "subprocess.CompletedProcess"]


def shell(argv: list[str] | str, *, env: dict | None = None, cwd: str | Path | None = None, timeout: int | None = None,
          background: bool = False, log: Path | None = None):
    """Run a command (a list, or a string for bash -lc). background=True starts it and returns the Popen (its output to `log`)."""
    cmd = ["bash", "-lc", argv] if isinstance(argv, str) else argv
    if background:
        out = open(log, "w", encoding="utf-8") if log else None
        return subprocess.Popen(cmd, env=env, cwd=cwd, stdout=out, stderr=subprocess.STDOUT if out else None)
    return subprocess.run(cmd, env=env, cwd=cwd, timeout=timeout, capture_output=True, text=True)


def done_tasks(results: Path, arms: list[str]) -> set[str]:
    """Task ids finished in EVERY arm (status done) in a results file."""
    seen: dict[str, set[str]] = {}
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "done":
                    seen.setdefault(r["task_id"], set()).add(r["arm"])
    return {t for t, a in seen.items() if set(arms) <= a}


class Container:
    """One repository's run, inside a container. Everything outside the filesystem goes through `sh`, so the order of commands
    and the snapshot/resume logic are testable with a recorder; `vol` is the Volume's mount, `app` the code's."""

    def __init__(self, spec: RunSpec, repo: str, *, sh: Shell = shell, vol: Path | str = VOL, app: Path | str = APP_DIR,
                 environ: dict[str, str] | None = None, commit: Callable[[], None] = lambda: None,
                 sleep: Callable[[float], None] = time.sleep, health: Callable[[], bool] | None = None):
        self.spec, self.repo, self.sh, self.commit, self.sleep = spec, repo, sh, commit, sleep
        self.vol, self.app = Path(vol), Path(app)
        # the dataset cache lives on the Volume; its variable is set HERE, not in the image: a cache directory that exists in the image under
        # the Volume's mount path makes Modal refuse the mount ("cannot mount volume on non-empty path")
        self.environ = {**auth_env(spec.auth, environ if environ is not None else dict(os.environ)),
                        "HF_HOME": str(self.vol / "cache" / "hf")}     # NOT uv's cache: a Volume refuses uv's temp-file rename ("Operation not permitted")
        self.ab = self.app / "experiments" / "abtest"
        self.local = self.ab / ".local"
        self.run_dir = self.vol / spec.run_name() / repo                  # the repository's directory on the volume
        self.out = self.run_dir / spec.unit_arm if spec.unit_arm else self.run_dir
        self.uses_backend = any(a in ("C", "C0") for a in spec.arms)      # arm A is plain Claude Code: no server, no Postgres
        self.health = health or self._healthy
        self.log: list[str] = []

    # -- state on the Volume
    def snapshot(self) -> None:
        """Results, logs, the arms' workspaces and C's home, openers and simulated-user replies, and a database dump -> the Volume."""
        self.out.mkdir(parents=True, exist_ok=True)
        run = self.spec.run_name()
        for sub, src in (("results", self.local / "results"), ("logs", self.local / "logs" / run)):
            if src.exists():
                shutil.copytree(src, self.out / sub, dirs_exist_ok=True)
        with tarfile.open(self.out / "state.tgz", "w:gz") as tf:
            for name in ("ws", "C_home", "openers", "simuser"):
                if (self.local / name).exists():
                    tf.add(self.local / name, arcname=name)
        if self.uses_backend:
            dump = self.sh(f"{PG_BIN}/pg_dump -h 127.0.0.1 -p {PG_PORT} -U kel_owner kel_abtest | gzip > {self.out / 'db.sql.gz'}")
            self.log.append(f"snapshot (db dump rc={getattr(dump, 'returncode', '?')})")
        self.commit()

    def restore(self) -> str:
        """Bring the Volume's snapshot back. Returns "fresh", "restored", or "cold" (results exist but no state: C/C0 cannot resume)."""
        if not (self.out / "results").exists():
            return "fresh"
        shutil.copytree(self.out / "results", self.local / "results", dirs_exist_ok=True)
        if (self.out / "logs").exists():
            shutil.copytree(self.out / "logs", self.local / "logs" / self.spec.run_name(), dirs_exist_ok=True)
        if not (self.out / "state.tgz").exists():
            return "cold"
        with tarfile.open(self.out / "state.tgz") as tf:
            try:
                tf.extractall(self.local, filter="data")
            except TypeError:                                  # Python < 3.12
                tf.extractall(self.local)
        return "restored"

    # -- services
    def start_services(self) -> None:
        pg = "su postgres -c"
        steps = [
            f"[ -f {PG_DATA}/PG_VERSION ] || {pg} \"{PG_BIN}/initdb -D {PG_DATA} --auth=trust -E UTF8\"",
            # a reused container may still have the server running (use it) or only its lock file left (remove it, start)
            f"{pg} \"{PG_BIN}/pg_ctl -D {PG_DATA} status\" >/dev/null 2>&1 || "
            f"{{ rm -f {PG_DATA}/postmaster.pid; {pg} \"{PG_BIN}/pg_ctl -D {PG_DATA} -o '-p {PG_PORT} -c listen_addresses=127.0.0.1' -l /var/lib/postgresql/pg.log -w start\"; }}",
            f"{pg} \"psql -p {PG_PORT} -h 127.0.0.1 -tc \\\"SELECT 1 FROM pg_roles WHERE rolname='kel_owner'\\\" | grep -q 1 || "
            f"createuser -p {PG_PORT} -h 127.0.0.1 -s kel_owner\"",
            f"{pg} \"psql -p {PG_PORT} -h 127.0.0.1 -tc \\\"SELECT 1 FROM pg_database WHERE datname='kel_abtest'\\\" | grep -q 1 || "
            f"createdb -p {PG_PORT} -h 127.0.0.1 -O kel_owner kel_abtest\"",
        ]
        for s in steps:
            self._must(self.sh(s), "postgres: " + s[:80])
        self._must(self.sh([sys.executable, str(self.app / "backend" / "scripts" / "migrate.py"), "--dsn", DSN],
                           cwd=self.app / "backend", env=self.environ), "migrate.py")
        self.out.mkdir(parents=True, exist_ok=True)
        self.backend_log = self.out / "backend.log"
        self.backend = self.sh([sys.executable, str(self.ab / "run_backend.py")], env={**self.environ, "ABTEST_DATABASE_URL": DSN,
                                                                           # the server refuses to start without a static MCP token (locally it is in backend/.env)
                                                                           "STEALTHLAB_MCP_TOKEN": secrets.token_urlsafe(32)},
                               cwd=self.ab, background=True, log=self.backend_log)
        for _ in range(150):
            if self.health():
                return
            if getattr(self.backend, "poll", lambda: None)() is not None:          # the process already ended: no point waiting
                break
            self.sleep(2)
        tail = self.backend_log.read_text(encoding="utf-8", errors="replace")[-2500:] if self.backend_log.exists() else "(no output)"
        raise RuntimeError(f"the test backend did not come up at {HEALTH}; its output ends: {tail}")

    def restore_db(self) -> None:
        dump = self.out / "db.sql.gz"
        if self.uses_backend and dump.exists():
            self.sh(f"gunzip -c {dump} | psql -q -h 127.0.0.1 -p {PG_PORT} -U kel_owner kel_abtest")
            self.log.append("database restored from the snapshot")

    @staticmethod
    def _healthy() -> bool:
        try:
            urllib.request.urlopen(HEALTH, timeout=5)
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _must(proc, what: str) -> None:
        rc = getattr(proc, "returncode", 0)
        if rc not in (0, None):
            raise RuntimeError(f"{what} failed (exit {rc}): {str(getattr(proc, 'stderr', ''))[-300:]}")

    # -- the run
    def runner_cmd(self, task_id: str) -> list[str]:
        s = self.spec
        return [sys.executable, str(self.ab / "runner.py"), "--run", s.run_name(), "--series", f"{s.series_module}:{self.repo}",
                "--arms", ",".join(s.arms), "--only", task_id, "--seed", str(s.seed), "--parallel", "--testfirst-model", s.testfirst_model,
                "--gc-key-name", s.gc_key_name] + (["--turns", str(s.turns)] if s.turns > 1 else [])

    def run(self) -> dict:
        s = self.spec
        problems = validate_spec(s)
        if problems:
            raise SystemExit("; ".join(problems))
        if s.task_ids is not None:                    # a split run: the issues were gold-checked once, before the arms started
            full = importlib.import_module(s.series_module).series(self.repo)
            tasks = [t for t in full if t["task_id"] in set(s.task_ids)]            # in series order
        else:
            tasks = series_tasks(s, self.repo)
        state = self.restore()
        results = self.local / "results" / f"{s.run_name()}.jsonl"
        done = done_tasks(results, s.arms)
        if state == "cold" and done and any(a in ("C", "C0") for a in s.arms) and not s.allow_cold_resume:
            return {"repo": self.repo, "status": "refused", "why": "results exist on the volume without the arms' workspaces and .stealth/: "
                    "resuming C/C0 would break the learning curve. Rerun with --run <new name>, or --allow-cold-resume to accept that."}
        pending = [t for t in tasks if t["task_id"] not in done]
        summary = {"repo": self.repo, "run": s.run_name(), "issues": len(tasks), "already_done": len(tasks) - len(pending), "state": state,
                   "ran": [], "status": "done"}
        if not pending:
            if not s.unit_arm:
                summary["report"] = self.report()
            return summary
        shared = self.run_dir / "validate.json"
        if s.task_ids is not None and shared.exists():      # a split run: the gold check ran in another container
            (self.local / "swe").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(shared, self.local / "swe" / f"validate_{self.repo}.json")
        if self.uses_backend:
            self.start_services()
            if state == "restored":
                self.restore_db()
        self._must(self.sh([sys.executable, str(self.ab / f"{s.series_module}.py"), "fetch", self.repo], env=self.environ, cwd=self.ab),
                   "fetching the series' data")
        if s.validate and s.series_module == SERIES_MODULE and s.task_ids is None:
            full = importlib.import_module(s.series_module).series(self.repo)          # not cut: a refused issue is replaced by the next
            pending, summary["gold_check"] = self.gold_check([t for t in full if t["task_id"] not in done])
            if not pending:
                summary.update(status="error", why="no issue passed the gold check (see gold_check)")
                summary["report"] = self.report()
                return summary
        for t in pending:
            proc = self.sh(self.runner_cmd(t["task_id"]), env=self.environ, cwd=self.ab)
            rc = getattr(proc, "returncode", 0)
            summary["ran"].append({"task": t["task_id"], "rc": rc})
            self.snapshot()
            if rc == RC_RATE_LIMITED:
                summary.update(status="rate_limited", resume="repeat the same command after the usage limit resets")
                break
            if rc == RC_QUOTA:
                summary.update(status="quota", resume="repeat the same command after the cheap models' daily quota resets (or --gc-key-name another key)")
                break
            if rc != 0:
                summary.update(status="error", why=str(getattr(proc, "stderr", ""))[-300:])
                break
        if not s.unit_arm:                              # a split run's report is made once, from every arm's results (finish)
            summary["report"] = self.report()
        return summary

    def gold_check(self, pending: list[dict]) -> tuple[list[dict], dict]:
        """The gold-patch check (swe_series_task.py validate) on the next issues: in THIS environment the hidden tests fail at the base
        commit and pass with the real fix. Checks up to twice as many issues as the run needs (a refused one is replaced by the next),
        keeps the first usable ones in order, and returns them with a record of what was checked and why any was dropped."""
        need = self.spec.max_issues or len(pending)
        pool = pending[: need * 2] if self.spec.max_issues else pending
        proc = self.sh([sys.executable, str(self.ab / f"{SERIES_MODULE}.py"), "validate", self.repo, *[t["task_id"] for t in pool]],
                       env=self.environ, cwd=self.ab)
        self._must(proc, "the gold check")
        path = self.local / "swe" / f"validate_{self.repo}.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if path.exists():                       # the arms' containers need it too: it holds the PASS_TO_PASS tests this environment breaks
            self.run_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, self.run_dir / "validate.json")
        verdict = {r["instance_id"]: r for r in rows}
        usable = [t for t in pool if verdict.get(t["task_id"], {}).get("usable")][:need]
        dropped = {t["task_id"]: str((verdict.get(t["task_id"], {}).get("gold") or {}).get("error")
                                     or ("the base commit already passes its FAIL_TO_PASS tests" if verdict.get(t["task_id"], {}).get("gold_resolves")
                                         else "not checked")) for t in pool if t not in usable}
        record = {"checked": len(pool), "usable": [t["task_id"] for t in usable], "dropped": dropped}
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "gold_check.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        return usable, record

    def prepare(self) -> dict:
        """A split run's first step, in its own container and with no services: fetch the series' data and run the gold check ONCE, for
        every arm. A repeated command reuses the recorded answer (the same issues, in the same order, so a resume matches)."""
        s = self.spec
        record_path = self.run_dir / "gold_check.json"
        if record_path.exists():
            rec = json.loads(record_path.read_text(encoding="utf-8"))
            if rec.get("max_issues") == s.max_issues and rec.get("usable"):
                return {"task_ids": rec["usable"], "gold_check": rec, "reused": True}
        self._must(self.sh([sys.executable, str(self.ab / f"{s.series_module}.py"), "fetch", self.repo], env=self.environ, cwd=self.ab),
                   "fetching the series' data")
        tasks = importlib.import_module(s.series_module).series(self.repo)
        if s.validate and s.series_module == SERIES_MODULE:
            usable, record = self.gold_check(tasks)
        else:
            usable = tasks[: s.max_issues] if s.max_issues else tasks
            record = {"checked": 0, "usable": [t["task_id"] for t in usable], "dropped": {}}
        record["max_issues"] = s.max_issues
        self.run_dir.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        self.commit()
        return {"task_ids": record["usable"], "gold_check": record, "reused": False}

    def finish(self) -> dict:
        """A split run's last step: every arm's results in one file, then report.py's summary and learning curve over all of them."""
        s = self.spec
        merged_dir = self.run_dir / "results"
        merged_dir.mkdir(parents=True, exist_ok=True)
        lines, missing, merged_arms = [], [], []
        for arm in ARMS:                                  # every arm that has results under this run, whatever this command asked for
            f = self.run_dir / arm / "results" / f"{s.run_name()}.jsonl"
            if f.exists():
                lines += [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
                merged_arms.append(arm)
            elif arm in s.arms:
                missing.append(arm)
        merged = merged_dir / f"{s.run_name()}.jsonl"
        merged.write_text("".join(ln + chr(10) for ln in lines), encoding="utf-8")
        env = {**self.environ, "ABTEST_RESULTS_DIR": str(merged_dir)}
        parts = []
        for extra in ([], ["--curve"]):
            r = self.sh([sys.executable, str(self.ab / "report.py"), s.run_name(), *extra], env=env, cwd=self.ab)
            parts.append(str(getattr(r, "stdout", "") or ""))
        text = "".join(p + chr(10) + chr(10) for p in parts).rstrip()
        (self.run_dir / "report.txt").write_text(text, encoding="utf-8")
        self.commit()
        return {"repo": self.repo, "run": s.run_name(), "rows": len(lines), "arms": merged_arms, "arms_without_results": missing, "report": text}

    def report(self) -> str:
        """report.py's summary and learning curve for the run, saved next to the results."""
        env = {**self.environ, "ABTEST_RESULTS_DIR": str(self.local / "results")}
        parts = []
        for extra in ([], ["--curve"]):
            r = self.sh([sys.executable, str(self.ab / "report.py"), self.spec.run_name(), *extra], env=env, cwd=self.ab)
            parts.append(str(getattr(r, "stdout", "") or ""))
        text = "\n\n".join(parts)
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "report.txt").write_text(text, encoding="utf-8")
        self.commit()
        return text


def container_main(spec_dict: dict, repo: str, commit: Callable[[], None] = lambda: None) -> dict:
    return Container(RunSpec(**spec_dict), repo, commit=commit).run()


def prepare_main(spec_dict: dict, repo: str, commit: Callable[[], None] = lambda: None) -> dict:
    return Container(RunSpec(**spec_dict), repo, commit=commit).prepare()


def finish_main(spec_dict: dict, repo: str, commit: Callable[[], None] = lambda: None, reload: Callable[[], None] = lambda: None) -> dict:
    reload()                                           # see what the other containers committed to the volume
    return Container(RunSpec(**spec_dict), repo, commit=commit).finish()


def arm_specs(spec: RunSpec, task_ids: list[str]) -> list[RunSpec]:
    """One spec per arm for a split run: the same issues, that arm only."""
    return [RunSpec(**{**asdict(spec), "arms": [a], "unit_arm": a, "task_ids": list(task_ids)}) for a in spec.arms]


def orchestrate(spec: RunSpec, *, prepare: Callable[[dict, str], dict], run_arms: Callable[[list[tuple[dict, str]]], list[dict]],
                finish: Callable[[dict, str], dict], run_one_container: Callable[[dict, str], dict]) -> list[dict]:
    """The whole run from the local side. Not split (split_arms False): one container per repository does everything.
    Split: for each repository ONE gold check (prepare), then one container per arm at the same time (run_arms), then one merge and
    report (finish). With a subscription the repositories go one after another; with an API key the arm containers of ALL
    repositories start together."""
    if not spec.split_arms:
        d = asdict(spec)
        return [run_one_container(d, r) for r in spec.repos] if spec.auth == "subscription" else run_arms([(d, r) for r in spec.repos])
    prepared = {r: prepare(asdict(spec), r) for r in spec.repos}
    out = []
    batches = [[r] for r in spec.repos] if spec.auth == "subscription" else [list(spec.repos)]
    for batch in batches:
        units = [(asdict(a), r) for r in batch for a in arm_specs(spec, prepared[r]["task_ids"])]
        results = run_arms(units)
        for r in batch:
            fin = finish(asdict(spec), r)
            fin["gold_check"] = prepared[r]["gold_check"]
            fin["arms"] = [x for (d, rr), x in zip(units, results) if rr == r]
            out.append(fin)
    return out


# ---------------------------------------------------------------- the Modal app (defined only when modal is importable)

if modal is not None:
    app = modal.App(APP_NAME)
    volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
    _common = dict(image=build_image(), volumes={VOL: volume}, timeout=86400, cpu=4.0, memory=16384)

    # Modal resolves EVERY secret of EVERY function when the app starts, so only the secret of the chosen auth is attached: the
    # default is the subscription; ABTEST_MODAL_AUTH=api (set when you run `modal run`) attaches the API-key secret instead
    MODAL_AUTH = os.environ.get("ABTEST_MODAL_AUTH", "subscription")
    _auth_secret = API_SECRET if MODAL_AUTH == "api" else CLAUDE_SECRET

    @app.function(secrets=[modal.Secret.from_name(KEYS_SECRET), modal.Secret.from_name(_auth_secret)], **_common)
    def run_unit(spec: dict, repo: str) -> dict:
        return container_main(spec, repo, commit=volume.commit)

    @app.function(secrets=[modal.Secret.from_name(KEYS_SECRET), modal.Secret.from_name(_auth_secret)], **_common)
    def prepare_unit(spec: dict, repo: str) -> dict:
        return prepare_main(spec, repo, commit=volume.commit)

    @app.function(secrets=[modal.Secret.from_name(KEYS_SECRET), modal.Secret.from_name(_auth_secret)], **_common)
    def finish_unit(spec: dict, repo: str) -> dict:
        return finish_main(spec, repo, commit=volume.commit, reload=volume.reload)

    @app.local_entrypoint()
    def main(repos: str, arms: str = "A,C,C0", max_issues: int = 0, auth: str = "subscription", turns: int = 1, run: str = "",
             series_module: str = SERIES_MODULE, allow_cold_resume: bool = False, plan: bool = False, sequential_arms: bool = False,
             testfirst_model: str = "glm-5.3", gc_key_name: str = "GENERAL_COMPUTE_API_KEY"):
        spec = RunSpec(repos=[r.strip() for r in repos.split(",") if r.strip()], arms=[a.strip() for a in arms.split(",") if a.strip()],
                       max_issues=max_issues, auth=auth, turns=turns, run=run, series_module=series_module,
                       allow_cold_resume=allow_cold_resume, split_arms=not sequential_arms, testfirst_model=testfirst_model, gc_key_name=gc_key_name)
        problems = validate_spec(spec)
        if problems:
            raise SystemExit("; ".join(problems))
        tasks = {r: series_tasks(spec, r) for r in spec.repos}
        print(plan_text(spec, tasks))
        if plan:
            return
        if spec.auth != MODAL_AUTH:
            raise SystemExit(f"--auth {spec.auth} needs ABTEST_MODAL_AUTH={spec.auth} in the environment of this command "
                             f"(it chooses which Modal secret the container gets); it is {MODAL_AUTH!r}")
        results = orchestrate(spec, prepare=lambda d, r: prepare_unit.remote(d, r), run_one_container=lambda d, r: run_unit.remote(d, r),
                              run_arms=lambda units: list(run_unit.starmap(units)), finish=lambda d, r: finish_unit.remote(d, r))
        for r in results:
            print(json.dumps({k: v for k, v in r.items() if k != "report"}))
            print(r.get("report", ""))


# ---------------------------------------------------------------- local helpers: plan, secrets

def default_env_files(repo: Path = REPO) -> list[Path]:
    """The backend/.env files that exist: this checkout's, and the MAIN checkout's when this is a git worktree (a worktree does
    not have the untracked, git-ignored .env; it stays in the checkout it was made in)."""
    files = [repo / "backend" / ".env"]
    try:
        common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=20).stdout.strip()
        if common:
            files.append(Path(common).parent / "backend" / ".env")
    except (OSError, subprocess.SubprocessError):
        pass
    seen, out = set(), []
    for f in files:
        key = str(f.resolve()) if f.exists() else str(f)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def read_names(names: tuple[str, ...] | list[str], environ: dict[str, str] | None = None, env_file: Path | None = None) -> dict[str, str]:
    """The named variables from the environment, else from backend/.env (env_file, or every file default_env_files() finds; read here,
    on the owner's machine). Values are returned, never printed."""
    environ = dict(os.environ) if environ is None else environ
    out = {n: environ[n] for n in names if environ.get(n)}
    for path in ([env_file] if env_file is not None else default_env_files()):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            k = k.strip()
            if k in names and k not in out and v.strip():
                out[k] = v.strip().strip('"').strip("'")
    return out


def create_secret(name: str, values: dict[str, str]) -> None:
    """Create the secret, REPLACING one of the same name. (Modal's allow_existing=True keeps the OLD values and does nothing, so an
    updated key or token silently never arrived: the Z_AI_KEY, the backend keys and the rotated Claude token all stayed old.)"""
    modal.Secret.objects.delete(name, allow_missing=True)
    modal.Secret.objects.create(name, values)


def secrets_cli(args: argparse.Namespace, environ: dict[str, str] | None = None, env_file: Path | None = None, out=print) -> int:
    if modal is None:
        out("modal is not installed (pip install modal; modal token new)")
        return 2
    environ = dict(os.environ) if environ is None else environ
    made = False
    if args.claude:
        tok = environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if not tok:
            out("set CLAUDE_CODE_OAUTH_TOKEN in this shell first (the output of `claude setup-token`); it is read from the environment, never from an argument")
            return 2
        create_secret(CLAUDE_SECRET, {"CLAUDE_CODE_OAUTH_TOKEN": tok})
        out(f"secret {CLAUDE_SECRET}: CLAUDE_CODE_OAUTH_TOKEN")
        made = True
    if args.api:
        key = environ.get("ANTHROPIC_API_KEY")
        if not key:
            out("set ANTHROPIC_API_KEY in this shell first")
            return 2
        create_secret(API_SECRET, {"ANTHROPIC_API_KEY": key})
        out(f"secret {API_SECRET}: ANTHROPIC_API_KEY")
        made = True
    if args.keys:
        env_file = env_file or (Path(args.env_file) if getattr(args, "env_file", None) else None)
        vals = read_names(KEY_NAMES, environ, env_file)
        if "GENERAL_COMPUTE_API_KEY" not in vals:
            looked = [env_file] if env_file is not None else default_env_files()
            out("GENERAL_COMPUTE_API_KEY is in neither the environment nor these files: "
                + "; ".join(f"{f} ({'found' if f.exists() else 'does not exist'})" for f in looked)
                + " | point at the right one with --env-file <path to backend/.env> (a git worktree does not have the main checkout's .env)")
            return 2
        create_secret(KEYS_SECRET, vals)
        out(f"secret {KEYS_SECRET}: " + ", ".join(sorted(vals)) + "  (names only)")
        missing = [n for n in KEY_NAMES if n not in vals]
        if missing:
            out("  not found, so not set: " + ", ".join(missing))
        made = True
    if not made:
        out("nothing to do: pass --claude, --api and/or --keys")
        return 2
    return 0


def cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the long-horizon test on Modal: plan, or create the secrets (run it with `modal run` otherwise)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="print what a run would do; no Modal call")
    p.add_argument("--repos", required=True)
    p.add_argument("--arms", default="A,C,C0")
    p.add_argument("--max-issues", type=int, default=0)
    p.add_argument("--auth", default="subscription")
    p.add_argument("--turns", type=int, default=1)
    p.add_argument("--run", default="")
    p.add_argument("--series-module", default=SERIES_MODULE)
    p.add_argument("--sequential-arms", action="store_true", help="all arms in one container, one after another (the slower layout)")
    s = sub.add_parser("secrets", help="create the Modal secrets from your environment / backend/.env by NAME (values are never printed)")
    s.add_argument("--claude", action="store_true", help=f"{CLAUDE_SECRET} from $CLAUDE_CODE_OAUTH_TOKEN")
    s.add_argument("--api", action="store_true", help=f"{API_SECRET} from $ANTHROPIC_API_KEY")
    s.add_argument("--keys", action="store_true", help=f"{KEYS_SECRET} from the cheap-model and backend keys: " + ", ".join(KEY_NAMES))
    s.add_argument("--env-file", default="", help="the backend/.env to read the keys from (default: this checkout's, then the main checkout's)")
    args = ap.parse_args(argv)
    if args.cmd == "secrets":
        return secrets_cli(args)
    spec = RunSpec(repos=[r.strip() for r in args.repos.split(",") if r.strip()], arms=[a.strip() for a in args.arms.split(",") if a.strip()],
                   max_issues=args.max_issues, auth=args.auth, turns=args.turns, run=args.run, series_module=args.series_module,
                   split_arms=not args.sequential_arms)
    problems = validate_spec(spec)
    if problems:
        print("; ".join(problems))
        return 2
    print(plan_text(spec, {r: series_tasks(spec, r) for r in spec.repos}))
    return 0


if __name__ == "__main__":
    sys.exit(cli())
