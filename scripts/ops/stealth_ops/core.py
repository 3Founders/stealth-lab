"""Shared plumbing for the operator CLI: paths, environment, subprocess helpers, check reporting, local state.

Design rules (docs/OPERATIONS_AUTOMATION.md):
  * The CLI COMPOSES existing commands (`python -m app.ingestion.admin|worker|enqueue`, `scripts/migrate.py`);
    it never re-implements queue, shard, migration or retrieval logic.
  * Secrets never touch the repository. Generated connection strings live in ``$STEALTH_OPS_HOME/secrets.env``
    (default ``~/.stealth-ops``, mode 0600, outside any git checkout); ``state.json`` beside it holds only
    non-secret ids (Neon project ids, shard map, which snapshots were taken).
  * Secret values are passed to child processes through the environment or stdin, never argv.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
OPS_HOME = Path(os.environ.get("STEALTH_OPS_HOME") or Path.home() / ".stealth-ops")

OK, WARN, DEGRADED, FAIL, SKIP = "OK", "WARN", "DEGRADED", "FAIL", "SKIP"
EXIT_OK, EXIT_FAIL, EXIT_CONFIG = 0, 1, 2


class OpsError(Exception):
    """A step failed in a way the operator must act on (message is shown verbatim, no traceback)."""


# ------------------------------------------------------------------ environment


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def secrets_path() -> Path:
    """secrets.env, or secrets.<name>.env when STEALTH_OPS_ENV names a non-default environment (staging, ...)."""
    name = os.environ.get("STEALTH_OPS_ENV", "").strip()
    return OPS_HOME / (f"secrets.{name}.env" if name and name != "production" else "secrets.env")


def load_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Precedence (low -> high): backend/.env, ~/.stealth-ops/secrets.env, the real process environment, `extra`."""
    env = {**parse_env_file(BACKEND / ".env"), **parse_env_file(secrets_path())}
    env.update({k: v for k, v in os.environ.items()})
    if extra:
        env.update(extra)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def write_secret_env(name: str, value: str) -> None:
    """Idempotently set NAME=value in the private secrets file (0600, outside the repo)."""
    OPS_HOME.mkdir(parents=True, exist_ok=True)
    path = secrets_path()
    cur = parse_env_file(path)
    if cur.get(name) == value:
        return
    cur[name] = value
    path.write_text("".join(f'{k}="{v}"\n' for k, v in sorted(cur.items())), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:   # Windows: ACLs, not modes; the file is still under the user's profile
        pass


# ------------------------------------------------------------------ state (non-secret)


def load_state() -> dict[str, Any]:
    p = OPS_HOME / "state.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    OPS_HOME.mkdir(parents=True, exist_ok=True)
    (OPS_HOME / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def update_state(**kv: Any) -> dict[str, Any]:
    s = load_state()
    s.update(kv)
    save_state(s)
    return s


# ------------------------------------------------------------------ subprocess


@dataclass
class Proc:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def json(self) -> Any:
        """Last JSON document in stdout (backend CLIs may print log lines before it)."""
        text = self.out.strip()
        for start in [i for i, ch in enumerate(text) if ch in "[{"]:
            try:
                return json.loads(text[start:])
            except ValueError:
                continue
        raise OpsError(f"expected JSON on stdout, got: {text[:300]!r} (stderr: {self.err[:300]!r})")


def run(cmd: list[str], *, cwd: Optional[Path] = None, env: Optional[dict[str, str]] = None, input: Optional[str] = None,
        timeout: Optional[float] = None, check: bool = False, quiet: bool = True) -> Proc:
    try:
        cp = subprocess.run(cmd, cwd=str(cwd or REPO), env=env or load_env(), input=input, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
        p = Proc(cp.returncode, cp.stdout or "", cp.stderr or "")
    except FileNotFoundError:
        p = Proc(127, "", f"command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        p = Proc(124, "", f"timed out after {timeout}s: {' '.join(cmd[:4])}")
    if not quiet:
        sys.stdout.write(p.out)
        sys.stderr.write(p.err)
    if check and not p.ok:
        raise OpsError(f"{' '.join(cmd[:5])} ... exited {p.rc}: {(p.err or p.out).strip()[-600:]}")
    return p


def backend(module_args: list[str], *, env: Optional[dict[str, str]] = None, timeout: Optional[float] = 900, **kw: Any) -> Proc:
    """Run `python -m <module> ...` inside backend/ (the existing CLIs: app.ingestion.admin / worker / enqueue)."""
    return run([sys.executable, "-m", *module_args], cwd=BACKEND, env=env, timeout=timeout, **kw)


def admin(*args: str, env: Optional[dict[str, str]] = None, timeout: Optional[float] = 900) -> Proc:
    return backend(["app.ingestion.admin", *args], env=env, timeout=timeout)


def migrate(dsn: str, *flags: str) -> Proc:
    env = load_env({"DATABASE_URL": dsn})    # the DSN travels in the environment, never argv
    return run([sys.executable, "scripts/migrate.py", *flags], cwd=BACKEND, env=env, timeout=1800)


# ------------------------------------------------------------------ reporting


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0


class Report:
    """Ordered checks; prints a fixed-width table; exit code non-zero when any blocking check fails."""

    def __init__(self, title: str, *, json_out: bool = False):
        self.title, self.checks, self.json_out, self._t0 = title, [], json_out, time.monotonic()

    def add(self, name: str, status: str, detail: str = "", **data: Any) -> Check:
        c = Check(name, status, detail, data)
        self.checks.append(c)
        if not self.json_out:
            print(f"{name:<18} {status:<9} {detail}", flush=True)
        return c

    def stage(self, name: str, fn: Any, *, blocking: bool = True) -> Check:
        """Run ``fn() -> (status, detail[, data])``; an exception becomes FAIL (never a traceback)."""
        t0 = time.monotonic()
        try:
            res = fn()
            status, detail, *rest = res
            c = self.add(name, status, detail, **(rest[0] if rest else {}))
        except OpsError as exc:
            c = self.add(name, FAIL, str(exc))
        except Exception as exc:  # noqa: BLE001 -- a broken check is a failed check, with the reason
            c = self.add(name, FAIL, f"{type(exc).__name__}: {exc}")
        c.seconds = round(time.monotonic() - t0, 2)
        c.data["blocking"] = blocking
        return c

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL and c.data.get("blocking", True)]

    def exit_code(self) -> int:
        return EXIT_FAIL if self.failed else EXIT_OK

    def finish(self) -> int:
        code = self.exit_code()
        if self.json_out:
            print(json.dumps({"title": self.title, "ok": code == 0, "checks": [c.__dict__ for c in self.checks]}, default=str, indent=2))
        else:
            verdict = "READY" if code == 0 else "BLOCKED: " + ", ".join(c.name for c in self.failed)
            print(f"\n{self.title}: {verdict}  ({time.monotonic() - self._t0:.0f}s)")
        return code


def confirm(prompt: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"{prompt} -- refusing without --yes (non-interactive)", file=sys.stderr)
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def redact(text: str, env: Optional[dict[str, str]] = None) -> str:
    """Remove secret-looking env values from anything we print."""
    import re

    out = re.sub(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+@", r"\1***@", text)
    for k, v in (env or {}).items():
        if len(v) >= 12 and any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD", "DSN", "URL")):
            out = out.replace(v, "***")
    return out
