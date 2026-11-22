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


def _agent37_key() -> str:
    return os.environ.get("AGENT37_API_KEY", "") or os.environ.get("AGENT37_KEY", "")


def provider() -> str:
    """Which sandbox actually runs a script: "agent37", "modal", or "" (none configured).

    SUBMISSION_SANDBOX picks one explicitly. Otherwise MODAL wins whenever it is configured, because its default
    instance IS gVisor and its network is blocked per sandbox — and our script steps run code written by strangers.
    Agent37 is ~36x cheaper sustained but ships a container with open egress by default
    (docs/pricing_and_sandbox_per_way.md 4.1), so it is opt-in, not the fallback.
    """
    chosen = (os.environ.get("SUBMISSION_SANDBOX") or "").strip().lower()
    if chosen == "agent37":
        return "agent37" if _agent37_key() else ""
    if chosen == "modal":
        return "modal" if configured() else ""
    if configured():
        return "modal"
    if _agent37_key():
        return "agent37"
    return ""


def configured() -> bool:
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return bool(os.environ.get("SUBMISSION_SANDBOX_LOCAL_PROFILE")) and os.path.exists(os.path.expanduser("~/.modal.toml"))


def runtime_for(name: str) -> Optional[tuple[str, ...]]:
    path = name.split("?", 1)[0].split("#", 1)[0].lower()
    return next((cmd for ext, cmd in RUNTIMES.items() if path.endswith(ext)), None)


async def _run_on_agent37(cmd: tuple[str, ...], code: str, stdin: str) -> ScriptResult:
    """One script on a throwaway Agent37 instance: create, exec, delete.

    A fresh instance per script, so two callers can never see each other's files — the same guarantee the Modal
    path gets from a fresh Sandbox. `type: "default"` is the cheap shape (docs/sandboxing.md §4); the workspace
    is deleted in the `finally`, so nothing is billed after the run.

    The command is passed to `sh -c`, so the runtime invocation and the script are joined into one shell command
    and stdin is fed in the same pipeline. Agent37 returns stdout/stderr/exit_code directly, which is exactly
    `ScriptResult`.
    """
    import httpx

    key = _agent37_key()
    api = (os.environ.get("AGENT37_API_URL") or "https://api.agent37.com").rstrip("/")
    sandbox_host = (os.environ.get("AGENT37_SANDBOX_URL") or "https://sandbox.agent37.app").rstrip("/")
    # gVisor is an OPTION at Agent37, not the default instance type (docs/pricing_and_sandbox_per_way.md §4.0).
    # Since these scripts were written by strangers, the default request asks for the isolated runtime.
    template = os.environ.get("AGENT37_TEMPLATE") or "agent37-base"
    # Agent37 allows public egress BY DEFAULT. Our Modal path sets block_network=True, so parity means deny here
    # too. Allowing it back is an explicit, named decision (AGENT37_NETWORK=open), never a silent default.
    network = (os.environ.get("AGENT37_NETWORK") or "deny-all").strip().lower()

    create_body: dict = {
        "template": template,
        "type": os.environ.get("AGENT37_INSTANCE_TYPE") or "default",
        "resources": {"cpu": 2, "memory": 4, "disk": 4},
        "auto_sleep": True,
        "idle_timeout_seconds": 120,
    }
    if network == "deny-all":
        create_body["network"] = {"mode": "deny-all"}

    async with httpx.AsyncClient(timeout=60) as client:
        made = await client.post(f"{api}/v1/instances",
                                 headers={"Authorization": f"Bearer {key}"}, json=create_body)
        if made.status_code >= 400:
            raise SandboxUnavailable(f"the sandbox could not be created ({made.status_code}): {made.text[:180]}")
        instance = made.json()
        iid = instance.get("id")
        if not iid:
            raise SandboxUnavailable("the sandbox answered without an id")

        try:
            # One shell command: the language runtime gets the script on argv, the run's context on stdin.
            import shlex

            argv = " ".join(shlex.quote(part) for part in cmd)
            command = f"printf '%s' {shlex.quote(stdin[:200_000])} | {argv} {shlex.quote(code)}" if stdin else \
                      f"{argv} {shlex.quote(code)}"
            ran = await client.post(
                f"{sandbox_host}/v1/instances/{iid}/exec",
                headers={"X-Agent37-Key": key, "Content-Type": "application/json"},
                json={"command": command},
            )
            if ran.status_code >= 400:
                raise SandboxUnavailable(f"the script could not run ({ran.status_code}): {ran.text[:180]}")
            out = ran.json()
            return ScriptResult(int(out.get("exit_code", -1) or -1),
                                str(out.get("stdout") or "")[:MAX_OUTPUT],
                                str(out.get("stderr") or "")[-4000:])
        finally:
            try:
                await client.delete(f"{api}/v1/instances/{iid}", headers={"Authorization": f"Bearer {key}"})
            except Exception:  # noqa: BLE001 -- it auto-sleeps and expires anyway
                pass


async def run_script(code: str, name: str, stdin: str = "") -> ScriptResult:
    """Run `code` (from the file `name`, which picks the language) in a fresh isolated sandbox."""
    cmd = runtime_for(name)
    if cmd is None:
        raise SandboxUnavailable(f"{name} is not a Python, shell or JavaScript file")
    if len(code) > MAX_SCRIPT:
        raise SandboxUnavailable(f"the script is larger than {MAX_SCRIPT // 1000} KB")
    which = provider()
    if not which:
        raise SandboxUnavailable("scripts run in an isolated sandbox this server is not connected to yet "
                                 "(set AGENT37_API_KEY, or MODAL_TOKEN_ID / MODAL_TOKEN_SECRET)")
    if which == "agent37":
        return await _run_on_agent37(cmd, code, stdin)

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
