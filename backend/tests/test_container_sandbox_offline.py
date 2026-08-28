"""ContainerSandboxExecutor: the isolation flags ARE the security contract.

CONTEXT: SubprocessSandboxExecutor's own docstring is honest that it
provides no real isolation -- "network_access=False is a documented
intent, NOT an enforced restriction" -- and that warning fires in this
repo's own test output today. ContainerSandboxExecutor implements the same
SandboxExecutor Protocol so it drops in behind the same contract, which is
exactly what this module's header promised the parallel production-sandbox
track.

Because the flag set is the guarantee, it is pinned here rather than
trusted to review: a silently dropped --network none or --read-only would
look identical in every other test.

Fully offline: _docker_argv is a pure method, and the one execution test
uses a deliberately absent binary so no daemon is required.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.services.sandbox_executor import (
    ContainerSandboxExecutor,
    ExecutionResult,
    SandboxExecutor,
    SubprocessSandboxExecutor,
)


def _argv(**kw):
    ex = ContainerSandboxExecutor(**kw)
    return ex._docker_argv(Path("/tmp/x"), timeout_seconds=5.0,
                           network_access=kw.pop("network_access", False))


def _flag_value(argv, flag):
    return argv[argv.index(flag) + 1]


# ------------------------------------------------- the Protocol contract

def test_satisfies_the_same_protocol_as_the_subprocess_executor():
    """A drop-in replacement or it is useless -- the whole point is that no
    caller changes."""
    proto = inspect.signature(SandboxExecutor.run)
    expected = proto.replace(
        parameters=[p for p in proto.parameters.values() if p.name != "self"]
    )
    assert inspect.signature(ContainerSandboxExecutor().run) == expected
    assert inspect.signature(SubprocessSandboxExecutor().run) == expected


# --------------------------------------------- the isolation guarantees

def test_network_is_actually_disabled_by_default():
    """THE difference from the subprocess executor: this one enforces it."""
    assert _flag_value(_argv(), "--network") == "none"


def test_network_access_true_is_opt_in_and_explicit():
    ex = ContainerSandboxExecutor()
    argv = ex._docker_argv(Path("/tmp/x"), 5.0, network_access=True)
    assert _flag_value(argv, "--network") == "bridge"


def test_rootfs_is_read_only():
    assert "--read-only" in _argv()


def test_resource_ceilings_are_present():
    argv = _argv()
    assert _flag_value(argv, "--memory") == "512m"
    assert _flag_value(argv, "--cpus") == "1.0"
    assert _flag_value(argv, "--pids-limit") == "128"


def test_privileges_are_dropped():
    argv = _argv()
    assert _flag_value(argv, "--cap-drop") == "ALL"
    assert _flag_value(argv, "--security-opt") == "no-new-privileges"
    assert _flag_value(argv, "--user") == "65534:65534"


def test_container_is_removed_after_the_run():
    assert "--rm" in _argv()


def test_only_the_work_dir_is_mounted():
    """Exactly one bind mount, and it is the staged temp dir -- nothing
    else of the host may be visible."""
    argv = _argv()
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert len(mounts) == 1
    assert mounts[0].endswith(":/work")
    assert _flag_value(argv, "-w") == "/work"


@pytest.mark.parametrize(
    "flag", ["--network", "--read-only", "--memory", "--cpus", "--pids-limit",
             "--cap-drop", "--security-opt", "--user", "--rm"],
)
def test_no_isolation_flag_silently_disappears(flag):
    """One parametrized guard so removing any single flag fails loudly."""
    assert flag in _argv()


# ------------------------------------------------------ no silent fallback

@pytest.mark.asyncio
async def test_missing_docker_reports_instead_of_falling_back():
    """A caller that asked for isolation must never silently get none.
    Uses a binary name that cannot exist, so no daemon is involved."""
    ex = ContainerSandboxExecutor(docker_binary="definitely-not-a-real-docker-binary")
    result = await ex.run("print(1)", {}, timeout_seconds=5.0)
    assert isinstance(result, ExecutionResult)
    assert result.exit_code == -1
    assert "not found" in result.stderr
    assert "Refusing to fall back" in result.stderr


@pytest.mark.asyncio
async def test_failure_path_still_cleans_up_its_temp_dir(monkeypatch):
    """The staged directory holds caller code and input files; a launch
    failure must not leave it on disk."""
    seen = {}
    real_mkdtemp = __import__("tempfile").mkdtemp

    def spy(*a, **k):
        path = real_mkdtemp(*a, **k)
        seen["path"] = path
        return path

    monkeypatch.setattr("app.services.sandbox_executor.tempfile.mkdtemp", spy)
    ex = ContainerSandboxExecutor(docker_binary="definitely-not-a-real-docker-binary")
    await ex.run("print(1)", {"data.txt": b"x"}, timeout_seconds=5.0)
    assert not Path(seen["path"]).exists()


def test_docstring_states_the_in_container_deployment_limit():
    """Pins the honest scope note. solve_task runs server-side, inside the
    backend container, which has no docker socket and no docker CLI
    (verified against the running stack 2026-08-28). A future edit that
    quietly drops this warning would let the class read as protecting
    solve_task in the shipped configuration, which it does not."""
    doc = ContainerSandboxExecutor.__doc__
    assert "DEPLOYMENT REALITY" in doc
    assert "docker-compose" in doc
    assert "DO NOT" in doc and "docker.sock" in doc, (
        "the socket-mount anti-recommendation must stay -- it is the "
        "tempting fix and it is a host-compromise downgrade"
    )
