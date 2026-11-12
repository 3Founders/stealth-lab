"""Running a submitted script, isolated: a Modal Sandbox (gVisor), never this server.

app/services/sandbox_executor.py's subprocess executor says it is not a security sandbox, and the hosted server has no
Docker, so a stranger's script runs only here:

    a fresh container per run   gVisor (a user-space kernel between the script and the host), a read-only base image
    no network                  block_network=True: the script cannot call out or reach kell
    limits                      SCRIPT_TIMEOUT_S wall clock, 1 CPU, 1 GB, then the sandbox is terminated
    input                       the run's input and the earlier steps' outputs, on stdin
    output                      stdout (cut to MAX_OUTPUT), and the exit code; stderr only when it failed

Languages by the file's extension: Python, shell (bash/sh), JavaScript (node). Anything else is refused.
Credentials: MODAL_TOKEN_ID + MODAL_TOKEN_SECRET (the Modal SDK reads them); without them a script step fails with a
sentence saying so -- it is never run anywhere else instead.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

APP_NAME = os.environ.get("SUBMISSION_SANDBOX_APP", "kell-way-scripts")
SCRIPT_TIMEOUT_S = int(os.environ.get("SUBMISSION_SCRIPT_TIMEOUT_S", "120"))
MAX_SCRIPT = 100_000
MAX_OUTPUT = 12_000
RUNTIMES = {".py": ("python3", "-c"), ".sh": ("bash", "-c"), ".bash": ("bash", "-c"), ".js": ("node", "-e"),
            ".mjs": ("node", "--input-type=module", "-e")}


class SandboxUnavailable(RuntimeError):
    pass


@dataclass
class ScriptResult:
    exit_code: int
    stdout: str
    stderr: str


def configured() -> bool:
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return bool(os.environ.get("SUBMISSION_SANDBOX_LOCAL_PROFILE")) and os.path.exists(os.path.expanduser("~/.modal.toml"))


def runtime_for(name: str) -> Optional[tuple[str, ...]]:
    path = name.split("?", 1)[0].split("#", 1)[0].lower()
    return next((cmd for ext, cmd in RUNTIMES.items() if path.endswith(ext)), None)


async def run_script(code: str, name: str, stdin: str = "") -> ScriptResult:
    """Run `code` (from the file `name`, which picks the language) in a fresh isolated sandbox."""
    cmd = runtime_for(name)
    if cmd is None:
        raise SandboxUnavailable(f"{name} is not a Python, shell or JavaScript file")
    if len(code) > MAX_SCRIPT:
        raise SandboxUnavailable(f"the script is larger than {MAX_SCRIPT // 1000} KB")
    if not configured():
        raise SandboxUnavailable("scripts run in an isolated sandbox this server is not connected to yet "
                                 "(MODAL_TOKEN_ID / MODAL_TOKEN_SECRET)")
    import modal

    app = await modal.App.lookup.aio(APP_NAME, create_if_missing=True)
    image = modal.Image.debian_slim(python_version="3.12").apt_install("nodejs")
    sb = await modal.Sandbox.create.aio(app=app, image=image, timeout=SCRIPT_TIMEOUT_S + 30, cpu=1.0, memory=1024,
                                        block_network=True)
    try:
        proc = await sb.exec.aio(*cmd, code, timeout=SCRIPT_TIMEOUT_S)
        if stdin:
            proc.stdin.write(stdin.encode("utf-8")[:200_000])
        proc.stdin.write_eof()
        await proc.stdin.drain.aio()
        exit_code = await proc.wait.aio()
        out = await proc.stdout.read.aio()
        err = await proc.stderr.read.aio()
    finally:
        try:
            await sb.terminate.aio()
        except Exception:  # noqa: BLE001 -- it ends at its own timeout anyway
            pass
    return ScriptResult(int(exit_code if exit_code is not None else -1), str(out or "")[:MAX_OUTPUT],
                        str(err or "")[-4000:])
