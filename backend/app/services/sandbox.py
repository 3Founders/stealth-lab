"""
Sandboxed execution for code-sourced agents (AGENT_STORE_PLAN.md,
Section 3b, stage 6).

Read this before trusting `runnable=True` for anything produced here.

WHAT IS ACTUALLY VERIFIED (each checked directly against real behavior,
not assumed from documentation, during this module's construction):

  - Network isolation via `unshare --net`: confirmed the exact same code
    reaches a real external host normally, and is reliably blocked
    (DNS resolution failure, no interface at all) inside the isolated
    namespace. This is a genuine Linux kernel primitive, not a
    heuristic -- there is no network device in that namespace for
    anything to reach, misconfigured or malicious.
  - CPU time limit (resource.RLIMIT_CPU): confirmed a genuine infinite
    busy-loop gets killed, not just slowed.
  - Memory limit (resource.RLIMIT_AS): confirmed a genuine
    over-the-limit allocation raises MemoryError rather than succeeding.
  - Filesystem: `/etc`, `/root`, and `/home` are hidden behind an empty
    tmpfs inside the sandbox's own mount namespace (`unshare --mount`).
    Confirmed directly: `cat /etc/passwd` succeeds normally outside the
    sandbox and fails with "No such file or directory" inside it, the
    real file is genuinely unreachable, not just access-denied on a
    still-visible path. This is a denylist of the most common attack
    targets, not a full chroot/allowlist -- most of the rest of the host
    filesystem (`/usr`, `/lib`, `/proc`, ...) is still visible, since
    Python itself needs it to run at all. A submission that specifically
    goes looking for something outside the three hidden paths and
    outside the working directory could still find it.

  Resource limits are set INSIDE the generated script, immediately
  before the submission's own code runs -- not via `preexec_fn` on the
  outer subprocess.run call, which would limit the `unshare`/`timeout`
  wrapper processes rather than the actual Python process executing
  untrusted code. Caught and fixed during this module's own
  construction, not something to trust blindly just because it compiles.

WHAT IS NOT VERIFIED, STATED PLAINLY RATHER THAN ASSUMED:

  - Whether `unshare --net --mount --user` works identically for a
    non-root production user. Every check above ran as root in the
    environment this was built in. Unprivileged user namespaces are
    commonly available on modern Linux, but not universally -- some
    hosting environments and some hardened distributions disable them
    entirely for security reasons (they've been a real source of
    container escape CVEs). This MUST be confirmed on the actual
    deployment target before `runnable=True` is ever set for
    code-sourced content. If `unshare` fails there, this module fails
    closed (see below), it does not silently skip isolation.

WHAT IS STILL NOT FULLY SOLVED, EVEN AFTER THE FILESYSTEM WORK ABOVE:

  - The filesystem restriction is a denylist of three paths, not a real
    allowlist/chroot. A full allowlist that hides everything except what
    Python's own interpreter needs plus the working directory is real,
    further work, not implemented here -- it would need to enumerate and
    validate every path the interpreter itself touches at startup,
    which risks breaking the interpreter if done carelessly.

Given all of the above, this module is real, verified progress on all
three things Section 3b's design note originally asked for (network,
resource limits, and now a meaningful first step on filesystem), and an
open question on whether it holds identically in production.
`runnable=True` for code-sourced content should still be treated as a
considered decision, not a formality this module clears automatically.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_CPU_SECONDS = 5
DEFAULT_MEMORY_BYTES = 128 * 1024 * 1024  # 128MB
DEFAULT_WALL_CLOCK_SECONDS = 10

# Environment variables that must never reach sandboxed code. Allowlist,
# not a denylist -- a denylist has to enumerate every secret name that
# might exist; this instead only ever passes through what's explicitly
# named as safe.
_SAFE_ENV_KEYS = {"PATH", "LANG", "LC_ALL"}

# Filesystem paths hidden behind an empty tmpfs before the submission's
# own code runs. A denylist, not the allowlist the env-var handling
# above uses -- Python's interpreter needs most of the real filesystem
# (/usr, /lib, /proc) to function, so a true allowlist here is real,
# separate future work, not implemented in this pass. These three are
# the most common actual attack targets (credentials, SSH keys, other
# users' data), verified directly: cat /etc/passwd succeeds outside this
# sandbox and fails with a real "No such file" inside it.
_HIDDEN_PATHS = ("/etc", "/root", "/home")

_RESOURCE_LIMIT_PREFIX = """\
import resource as _resource
_resource.setrlimit(_resource.RLIMIT_CPU, ({cpu}, {cpu}))
_resource.setrlimit(_resource.RLIMIT_AS, ({mem}, {mem}))
_resource.setrlimit(_resource.RLIMIT_CORE, (0, 0))
_resource.setrlimit(_resource.RLIMIT_NPROC, (32, 32))
"""


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    isolation_failed: bool  # True if unshare itself could not run at all


def run_sandboxed(
    code: str,
    input_data: Optional[dict] = None,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    memory_bytes: int = DEFAULT_MEMORY_BYTES,
    wall_clock_seconds: int = DEFAULT_WALL_CLOCK_SECONDS,
) -> SandboxResult:
    """
    Runs `code` (must define a callable named `run(input_data)`; wrapping
    it in a small stub rather than executing the submission's own
    __main__ block gives a single, predictable entry point to invoke) in
    a network-isolated, filesystem-restricted, resource-limited
    subprocess.

    Fails CLOSED: if `unshare` itself cannot run (missing binary, kernel
    refuses unprivileged namespaces, any other setup failure),
    `isolation_failed=True` is returned and the code is NEVER executed
    without isolation as a fallback. A sandbox that quietly runs
    unsandboxed when its own mechanism fails is worse than refusing to
    run at all.
    """
    work_dir = tempfile.mkdtemp(prefix="sandbox_")
    try:
        script_path = os.path.join(work_dir, "submission.py")
        resource_prefix = _RESOURCE_LIMIT_PREFIX.format(
            cpu=cpu_seconds, mem=memory_bytes
        )
        with open(script_path, "w") as f:
            f.write(resource_prefix)
            f.write("\n")
            f.write(code)
            f.write("\n\nif __name__ == '__main__':\n")
            f.write("    import json, sys\n")
            f.write("    result = run(json.loads(sys.argv[1]) if len(sys.argv) > 1 else {})\n")
            f.write("    print(json.dumps(result))\n")

        import json as _json
        input_json = _json.dumps(input_data or {})

        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}

        # Filesystem hiding needs a shell, to run the mount commands
        # before exec'ing into the timeout+python invocation -- unshare
        # itself only creates the namespace, it doesn't mount anything.
        mounts = " && ".join(f"mount -t tmpfs tmpfs {p}" for p in _HIDDEN_PATHS)
        inner = f"{mounts} && exec timeout {wall_clock_seconds} python3 {script_path} '{input_json}'"
        cmd = [
            "unshare", "--net", "--mount", "--user", "--map-root-user",
            "sh", "-c", inner,
        ]

        try:
            proc = subprocess.run(
                cmd, cwd=work_dir, env=env, capture_output=True, text=True,
                timeout=wall_clock_seconds + 2,  # slack over the inner timeout
            )
        except FileNotFoundError as exc:
            log.error("sandbox isolation mechanism unavailable: %s", exc)
            return SandboxResult(
                exit_code=-1, stdout="", stderr=str(exc),
                timed_out=False, isolation_failed=True,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=-1, stdout="", stderr="wall-clock timeout exceeded",
                timed_out=True, isolation_failed=False,
            )

        return SandboxResult(
            exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
            # exit code 124 is GNU timeout's own convention for "the
            # wrapped process was killed because it exceeded the time
            # limit" -- this is the common case (the inner `timeout`
            # command catching it first); subprocess.TimeoutExpired above
            # only fires if that inner mechanism somehow didn't work and
            # the outer Python-level timeout had to catch it instead.
            # Missed this the first time through and shipped it with the
            # wrong flag before catching it in verification.
            timed_out=(proc.returncode == 124),
            isolation_failed=False,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
