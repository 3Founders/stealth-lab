"""Running a submitted script, isolated: Agent37 (default) or a Modal Sandbox (gVisor) — never this server.

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

Files a script produces (`produces=`) are read back from /out before the sandbox is deleted: exec returns only
stdout, and the instance is terminated in a `finally`, so a file asked for afterwards would never arrive.

Provider: Agent37 by default; `SUBMISSION_SANDBOX=modal` switches back (docs/sandboxing.md).
"""
from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from app.submissions.artifacts import OUT_DIR

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

    DEFAULT IS AGENT37 (owner's call, 2026-11-22): it is ~36x cheaper sustained and wakes 13x faster, and the
    reason it was previously opt-in — its default instance is a container with open egress — is handled here
    rather than avoided: _run_on_agent37 asks for `deny-all` egress unless AGENT37_NETWORK=open, so the cheap
    provider is also the closed one.

    SUBMISSION_SANDBOX overrides the choice, including `modal`, so switching back is one variable and no code.
    """
    chosen = (os.environ.get("SUBMISSION_SANDBOX") or "").strip().lower()
    if chosen == "modal":
        return "modal" if configured() else ""
    if chosen == "agent37":
        return "agent37" if _agent37_key() else ""
    if _agent37_key():
        return "agent37"
    if configured():
        return "modal"
    return ""


def configured() -> bool:
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return bool(os.environ.get("SUBMISSION_SANDBOX_LOCAL_PROFILE")) and os.path.exists(os.path.expanduser("~/.modal.toml"))


def runtime_for(name: str) -> Optional[tuple[str, ...]]:
    path = name.split("?", 1)[0].split("#", 1)[0].lower()
    return next((cmd for ext, cmd in RUNTIMES.items() if path.endswith(ext)), None)


async def _run_on_agent37(cmd: tuple[str, ...], code: str, stdin: str,
                          imports: Optional[Mapping[str, str]] = None) -> ScriptResult:
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
    # Where the sandbox runs. Agent37 defaults to `us`; `ap-south` is Mumbai, which matters once buyers are in India:
    # a script step's files otherwise cross the Pacific twice (out of the sandbox, then again on download).
    #
    # Region is fixed for an instance's life, and `ap-south` refuses the `default` type -- it runs only `vm` and
    # `performance`. Asking for `default` there is a 400 rather than a silent fallback, so the pair is corrected
    # here instead of failing the step with a message a person cannot act on.
    region = (os.environ.get("AGENT37_REGION") or "").strip().lower()
    if region and region != "us":
        create_body["region"] = region
        if create_body["type"] == "default":
            create_body["type"] = "vm"
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
            run_it = f"printf '%s' {shlex.quote(stdin[:200_000])} | {argv} {shlex.quote(code)}" if stdin else \
                f"{argv} {shlex.quote(code)}"
            # The step's imported files are written first, in the same exec, so they are on disk when the script
            # starts — this is what lets a step read an earlier step's file (steps_sandboxes_filesystems.md §4).
            writing = _materialise_agent37_command(imports or {})
            command = f"{writing} && cd {OUT_DIR} && {run_it}" if writing else run_it
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


def _capture_script_js(produced: Iterable[str]) -> str:
    """The Node tail that emits a script's files as one base64 JSON line, matching `artifacts.parse_captured`."""
    return "\n".join([
        "const fs = require('fs'), path = require('path');",
        "const out = [];",
        f"for (const rel of {list(map(str, produced))!r}) {{",
        f"  const p = path.join({OUT_DIR!r}, rel);",
        "  if (fs.existsSync(p) && fs.statSync(p).isFile())",
        "    out.push({ name: path.basename(p), b64: fs.readFileSync(p).toString('base64') });",
        "}",
        "console.log('\\nKELL_ARTIFACTS:' + JSON.stringify(out));",
    ])


async def _materialise_modal(sb: Any, imports: Mapping[str, str]) -> None:
    """Write the step's imported files into the sandbox before its script runs (Modal)."""
    for rel, body in imports.items():
        safe = _safe_relpath(rel)
        await sb.mkdir.aio(posixpath.dirname(f"{OUT_DIR}/{safe}") or OUT_DIR, parents=True)
        await sb.filesystem.write_text.aio(body, f"{OUT_DIR}/{safe}")


def _safe_relpath(name: str) -> str:
    """A path inside the sandbox's /out. A name is stranger-supplied, so it may not walk out of the directory."""
    clean = posixpath.normpath("/" + str(name).replace("\\", "/")).lstrip("/")
    if not clean or clean.startswith(".."):
        raise SandboxUnavailable(f"{name!r} is not a usable filename")
    return clean


def _materialise_agent37_command(imports: Mapping[str, str]) -> str:
    """A shell command that writes the imports into /out on an Agent37 instance.

    Agent37's base template has no `/v1/files` endpoints, so bytes go through `exec`, whose request body is capped at
    1 MB. Each file is therefore written in chunks under that cap, base64'd so a binary input survives, and the whole
    command stays one exec so the files land before the step's own command runs.
    """
    import base64

    if not imports:
        return ""
    parts = [f"mkdir -p {OUT_DIR}"]
    limit = 600_000                      # comfortably under the 1 MB request cap once the command text is added
    for rel, body in imports.items():
        safe = _safe_relpath(rel)
        parent = posixpath.dirname(safe)
        if parent:
            parts.append(f"mkdir -p {OUT_DIR}/{parent}")
        data = base64.b64encode(body.encode("utf-8")).decode()
        parts.append(f": > {OUT_DIR}/{safe}")
        for i in range(0, max(1, len(data)), limit):
            parts.append(f"printf '%s' '{data[i:i + limit]}' | base64 -d >> {OUT_DIR}/{safe}")
    return " && ".join(parts)


async def run_script(code: str, name: str, stdin: str = "",
                     produces: Optional[Iterable[str]] = None,
                     imports: Optional[Mapping[str, str]] = None) -> ScriptResult:
    """Run `code` (from the file `name`, which picks the language) in a fresh isolated sandbox.

    `produces` names files the script writes into `/out`. They are read back over the same channel *before* the
    instance is deleted — the deletion is in a `finally`, so a file requested afterwards would never arrive. With
    no `produces`, nothing is captured and a script step behaves exactly as it did before artifacts existed.
    """
    cmd = runtime_for(name)
    if cmd is None:
        raise SandboxUnavailable(f"{name} is not a Python, shell or JavaScript file")
    if len(code) > MAX_SCRIPT:
        raise SandboxUnavailable(f"the script is larger than {MAX_SCRIPT // 1000} KB")
    which = provider()
    if not which:
        raise SandboxUnavailable("scripts run in an isolated sandbox this server is not connected to yet "
                                 "(set AGENT37_API_KEY, or MODAL_TOKEN_ID / MODAL_TOKEN_SECRET)")
    wanted = [w for w in (produces or []) if isinstance(w, str) and w.strip()]
    if wanted:
        from app.submissions.artifacts import OUT_DIR, capture_script

        if name.lower().endswith(".py"):
            code = code + "\n\n" + capture_script(produced=wanted, order=0)
        elif name.lower().endswith((".js", ".mjs")):
            # Node gets a real script; the tail writes the files as base64 JSON on stdout.
            code = code + "\n\n" + _capture_script_js(wanted)
        else:
            # Shell: tar the drop zone, base64'd, on stdout. No python assumed to exist in the image.
            code = (f"mkdir -p {OUT_DIR}; ( {code} ); "
                    f"cd {OUT_DIR} && tar cf - . 2>/dev/null | base64")
    if which == "agent37":
        return await _run_on_agent37(cmd, code, stdin, imports=imports or {})

    import modal

    app = await modal.App.lookup.aio(APP_NAME, create_if_missing=True)
    image = modal.Image.debian_slim(python_version="3.12").apt_install("nodejs")
    sb = await modal.Sandbox.create.aio(app=app, image=image, timeout=SCRIPT_TIMEOUT_S + 30, cpu=1.0, memory=1024,
                                        block_network=True)
    try:
        if imports:
            # Modal has a real filesystem API, so the inputs are simply written before the script runs. This is the
            # case that makes "read literature.md, write main.tex" work (docs/steps_sandboxes_filesystems.md §4).
            await _materialise_modal(sb, imports)
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
