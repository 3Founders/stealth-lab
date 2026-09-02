"""
Experiment 4, Step 2: the execution substrate for a real code-generating
agent. Implements the SandboxExecutor interface handed to the parallel
production-sandbox design track (EXECUTION_SANDBOX_DESIGN_BRIEF.md) --
same contract, so that work can drop in behind this without touching
any experiment code that calls it.

SECURITY: THIS IS NOT A SECURITY SANDBOX. Read this before using it for
anything beyond the current experiment. A bare subprocess provides:
  - NO filesystem isolation beyond a fresh temp directory (the process
    can still read/write anywhere the OS user running this script can)
  - NO real network isolation (network_access=False is a documented
    intent, NOT an enforced restriction -- see _check_network below)
  - NO CPU/memory limits beyond the wall-clock timeout
  - NO protection against a malicious or badly-behaved script

This is acceptable ONLY because: (1) the code being executed here comes
from a small number of controlled experiment runs, not adversarial
input, (2) a human (you) is directly supervising each run, and (3) this
explicitly defers to the parallel production-sandbox work for anything
beyond that. Do NOT reuse this executor for production, multi-tenant,
or genuinely untrusted code without the real isolation layer.

Run offline tests: python -m pytest tests/test_sandbox_executor.py -v
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    output_files: dict[str, bytes] = field(default_factory=dict)
    wall_time_seconds: float = 0.0
    timed_out: bool = False


class SandboxExecutor(Protocol):
    async def run(
        self,
        code: str,
        input_files: dict[str, bytes],
        timeout_seconds: float,
        network_access: bool = False,
    ) -> ExecutionResult: ...


class InputPathEscape(ValueError):
    """An `input_files` key resolved outside the sandbox's own temp
    directory. Raised instead of silently writing the file: `tmp_dir /
    rel_path` in `pathlib` discards `tmp_dir` entirely when `rel_path` is
    absolute (`Path("/tmp/x") / "/etc/passwd" == Path("/etc/passwd")`),
    and `..` segments walk back out of it either way -- both let a caller-
    supplied filename write anywhere the OS user running this process can,
    with no sandboxing at all. `DeterministicProvider.execute()` passes
    `context['input_files']` straight through, so this is reachable from
    any caller of that provider."""


def stage_input_files(tmp_dir: Path, input_files: dict[str, bytes]) -> None:
    """Write every `(rel_path, content)` pair under `tmp_dir`, refusing
    (via `InputPathEscape`) any key that would land outside it -- an
    absolute path (POSIX or Windows-drive) or a `..`-carrying relative
    path. Shared by both executors so the check can't drift between
    them."""
    tmp_dir = tmp_dir.resolve()
    for rel_path, content in input_files.items():
        candidate = (tmp_dir / rel_path).resolve()
        if candidate != tmp_dir and tmp_dir not in candidate.parents:
            raise InputPathEscape(
                f"input_files key {rel_path!r} resolves to {candidate}, "
                f"outside the sandbox directory {tmp_dir} -- refusing to write it"
            )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(content)


class SubprocessSandboxExecutor:
    """
    The simple, experiment-scoped implementation. See module docstring
    for what this deliberately does NOT provide.

    Layout per run, in a fresh temp directory:
        <tmp>/<input files, staged exactly as given>
        <tmp>/solution.py      <- the generated code
    Runs `python <script_name> ` with cwd=<tmp>, a wall-clock timeout,
    and reports every file present after the run that wasn't part of
    the original input set as an "output file" -- a generic definition
    that doesn't need to know a task's expected output filename ahead
    of time.
    """

    def __init__(self, python_executable: str | None = None):
        self.python_executable = python_executable or sys.executable

    async def run(
        self,
        code: str,
        input_files: dict[str, bytes],
        timeout_seconds: float,
        network_access: bool = False,
    ) -> ExecutionResult:
        self._check_network(network_access)

        tmp_dir = Path(tempfile.mkdtemp(prefix="sandbox_exec_"))
        try:
            try:
                stage_input_files(tmp_dir, input_files)
            except InputPathEscape as exc:
                return ExecutionResult(
                    exit_code=-1, stdout="", stderr=str(exc), wall_time_seconds=0.0,
                )

            script_path = tmp_dir / "solution.py"
            script_path.write_text(code, encoding="utf-8")

            before = self._snapshot(tmp_dir)
            start = time.monotonic()
            timed_out = False
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.python_executable, str(script_path),
                    cwd=tmp_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout_seconds,
                    )
                    exit_code = proc.returncode
                except asyncio.TimeoutError:
                    timed_out = True
                    proc.kill()
                    await proc.wait()
                    stdout_b, stderr_b = b"", b"(killed after timeout)"
                    exit_code = -1
            except Exception as exc:  # noqa: BLE001 -- report, don't crash the caller
                wall_time = time.monotonic() - start
                return ExecutionResult(
                    exit_code=-1, stdout="", stderr=f"executor failed to launch process: {exc}",
                    wall_time_seconds=wall_time, timed_out=False,
                )
            wall_time = time.monotonic() - start

            after = self._snapshot(tmp_dir)
            new_or_changed = {
                rel: (tmp_dir / rel).read_bytes()
                for rel, mtime in after.items()
                if rel != "solution.py" and (rel not in before or before[rel] != mtime)
            }

            return ExecutionResult(
                exit_code=exit_code if exit_code is not None else -1,
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace"),
                output_files=new_or_changed,
                wall_time_seconds=wall_time,
                timed_out=timed_out,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _snapshot(root: Path) -> dict[str, float]:
        return {
            str(p.relative_to(root)): p.stat().st_mtime
            for p in root.rglob("*") if p.is_file()
        }

    @staticmethod
    def _check_network(network_access: bool) -> None:
        if not network_access:
            # Documented intent only -- see module docstring. A real
            # restriction needs OS-level firewalling or containerization,
            # neither of which a bare subprocess provides. Warn loudly
            # rather than silently claim a guarantee this class can't keep.
            import warnings
            warnings.warn(
                "SubprocessSandboxExecutor: network_access=False is NOT enforced -- "
                "this executor cannot actually block network calls. If the generated "
                "code makes one, it will succeed. See module docstring.",
                stacklevel=3,
            )


class ContainerSandboxExecutor:
    """
    The real-isolation implementation of the SAME SandboxExecutor Protocol,
    so it drops in behind SubprocessSandboxExecutor without touching any
    caller -- which is exactly what this module's header promised the
    parallel production-sandbox track ("same contract, so that work can
    drop in behind this").

    WHAT THIS ACTUALLY ENFORCES, as opposed to documents:
      - `network_access=False` becomes REAL. `--network none` gives the
        container no interfaces at all, so a script that tries to reach the
        network fails rather than succeeding-with-a-warning. This is the
        single honest difference from SubprocessSandboxExecutor, whose own
        docstring admits the flag is "a documented intent, NOT an enforced
        restriction".
      - Filesystem: `--read-only` rootfs, so the only writable path is the
        bind-mounted work directory. A script cannot touch the host outside
        that directory, which a bare subprocess can (it runs as the same OS
        user as the server).
      - Resources: `--memory`, `--cpus`, `--pids-limit` -- real ceilings, not
        just a wall-clock timeout.
      - Privileges: `--cap-drop ALL`, `--security-opt no-new-privileges`,
        and a non-root `--user`.

    HONEST LIMITS, stated plainly because "container" is not a synonym for
    "safe":
      - This is a container, not a VM. A kernel exploit escapes it. For
        genuinely adversarial code, a microVM (Firecracker) or gVisor is
        the next rung, not this.
      - `--user` defaults to a fixed non-root uid. On a bind mount whose
        host files are owned by someone else, that uid may be unable to
        write; on Docker Desktop the mount is usually permissive, on native
        Linux it may not be. Configurable for that reason.
      - Nothing here inspects the code being run. Isolation is the whole
        guarantee; correctness and intent are not.

    NEVER FALLS BACK. If Docker is missing or the daemon is down, this
    returns a failed ExecutionResult saying so. It does NOT quietly hand
    the work to SubprocessSandboxExecutor -- a caller that asked for
    isolation and silently got none is the exact failure this class exists
    to remove.

    DEPLOYMENT REALITY -- READ BEFORE CLAIMING THIS PROTECTS find_best_way.
    This works when the backend runs on a HOST with a Docker daemon (the
    dev setup, and how Experiment 4 actually ran). It does NOT work in the
    shipped docker-compose configuration, verified against the running
    stack 2026-08-28:
        backend container:  no /var/run/docker.sock
        backend container:  no docker CLI
        docker-compose.yml: socket not mounted (0 references)
    find_best_way is an MCP tool, so it executes server-side -- inside that
    container -- where there is no daemon to talk to. In that configuration
    this executor fails loudly on every call (see the FileNotFoundError
    path below). That is deliberate: a visible gap beats a latent one, and
    this repo's recurring failure mode is capability claimed ahead of what
    the code does in the SHIPPED configuration.

    DO NOT "FIX" THIS BY MOUNTING /var/run/docker.sock INTO THE BACKEND.
    Socket access is effectively root on the host, and that same container
    serves find_best_way (whose repo_path is caller-controlled). Granting
    that a Docker socket turns a contained bad outcome into host
    compromise -- arguably a worse posture
    than the unisolated executor this class replaces. If it ever goes that
    way it needs a founder ruling, the same class of decision as the
    licence question, not a lane's call.

    The routes that do not have that problem, roughly in descending order
    of how much they should be trusted: a sibling compose service that owns
    execution and is reached over the network (socket stays out of the
    backend); keeping this executor host-only and letting it fail loudly
    in-container, as it does now; or gVisor / Firecracker / a rootless
    Podman socket for a smaller blast radius at the cost of more setup.
    """

    def __init__(
        self,
        image: str = "python:3.13-slim",
        memory: str = "512m",
        cpus: str = "1.0",
        pids_limit: int = 128,
        user: str = "65534:65534",   # nobody:nogroup
        docker_binary: str = "docker",
    ):
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.user = user
        self.docker_binary = docker_binary

    def _docker_argv(self, tmp_dir: Path, timeout_seconds: float, network_access: bool) -> list:
        """Built as a separate pure method so the flag set is assertable in
        an offline test without a Docker daemon -- the isolation flags ARE
        the security contract, so they get pinned by tests rather than
        trusted to review."""
        argv = [
            self.docker_binary, "run", "--rm",
            "--network", "bridge" if network_access else "none",
            "--read-only",
            "--memory", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", str(self.pids_limit),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", self.user,
            "-v", f"{tmp_dir}:/work",
            "-w", "/work",
            self.image,
            "python", "/work/solution.py",
        ]
        return argv

    async def run(
        self,
        code: str,
        input_files: dict[str, bytes],
        timeout_seconds: float,
        network_access: bool = False,
    ) -> ExecutionResult:
        tmp_dir = Path(tempfile.mkdtemp(prefix="container_sandbox_"))
        try:
            try:
                stage_input_files(tmp_dir, input_files)
            except InputPathEscape as exc:
                return ExecutionResult(
                    exit_code=-1, stdout="", stderr=str(exc), wall_time_seconds=0.0,
                )
            (tmp_dir / "solution.py").write_text(code, encoding="utf-8")

            before = SubprocessSandboxExecutor._snapshot(tmp_dir)
            argv = self._docker_argv(tmp_dir, timeout_seconds, network_access)
            start = time.monotonic()
            timed_out = False
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout_seconds,
                    )
                    exit_code = proc.returncode
                except asyncio.TimeoutError:
                    # --rm plus killing the client is not always enough to
                    # stop the container itself, but the client dying does
                    # release this coroutine; the container is bounded by
                    # its own resource limits either way.
                    timed_out = True
                    proc.kill()
                    await proc.wait()
                    stdout_b, stderr_b = b"", b"(killed after timeout)"
                    exit_code = -1
            except FileNotFoundError:
                return ExecutionResult(
                    exit_code=-1, stdout="",
                    stderr=(
                        f"ContainerSandboxExecutor: {self.docker_binary!r} not found. "
                        f"Refusing to fall back to an unisolated executor -- a caller "
                        f"that asked for isolation must not silently get none."
                    ),
                    wall_time_seconds=time.monotonic() - start,
                )
            except Exception as exc:  # noqa: BLE001 -- report, don't crash the caller
                return ExecutionResult(
                    exit_code=-1, stdout="",
                    stderr=f"ContainerSandboxExecutor failed to launch: {exc}",
                    wall_time_seconds=time.monotonic() - start,
                )
            wall_time = time.monotonic() - start

            after = SubprocessSandboxExecutor._snapshot(tmp_dir)
            new_or_changed = {
                rel: (tmp_dir / rel).read_bytes()
                for rel, mtime in after.items()
                if rel != "solution.py" and (rel not in before or before[rel] != mtime)
            }
            return ExecutionResult(
                exit_code=exit_code if exit_code is not None else -1,
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace"),
                output_files=new_or_changed,
                wall_time_seconds=wall_time,
                timed_out=timed_out,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
