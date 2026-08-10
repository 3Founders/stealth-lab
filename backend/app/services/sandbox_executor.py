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
            for rel_path, content in input_files.items():
                dest = tmp_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)

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
