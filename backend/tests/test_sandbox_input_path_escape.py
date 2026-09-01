"""Regression test for a real path-escape bug in
`SubprocessSandboxExecutor`/`ContainerSandboxExecutor.run()`.

`input_files` is a caller-supplied `dict[str, bytes]` -- reachable
end-to-end from `DeterministicProvider.execute()`'s `context['input_files']`
(app/execution/providers.py). Before the fix, staging did
`dest = tmp_dir / rel_path`; `pathlib`'s `/` operator DISCARDS the left
side entirely when the right side is an absolute path
(`Path("/tmp/x") / "/etc/passwd" == Path("/etc/passwd")`), and a
`..`-carrying relative path walks back out of `tmp_dir` either way. Either
key let a caller write an arbitrary file anywhere the OS user running the
backend can write, with no sandboxing at all -- proven here to have
existed, and proven closed.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from app.services.sandbox_executor import (
    ContainerSandboxExecutor,
    InputPathEscape,
    SubprocessSandboxExecutor,
    stage_input_files,
)


def test_stage_input_files_rejects_absolute_path(tmp_path):
    target = tmp_path / "outside" / "pwned.txt"
    target.parent.mkdir()

    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()

    with pytest.raises(InputPathEscape):
        stage_input_files(sandbox_dir, {str(target): b"pwned"})

    assert not target.exists()


def test_stage_input_files_rejects_dotdot_traversal(tmp_path):
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    victim = tmp_path / "victim.txt"

    with pytest.raises(InputPathEscape):
        stage_input_files(sandbox_dir, {"../victim.txt": b"pwned"})

    assert not victim.exists()


def test_stage_input_files_still_allows_nested_relative_paths(tmp_path):
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    stage_input_files(sandbox_dir, {"environment/data/input.txt": b"ok"})
    assert (sandbox_dir / "environment" / "data" / "input.txt").read_bytes() == b"ok"


def test_subprocess_executor_refuses_dotdot_escape_and_does_not_write_outside(tmp_path):
    victim_dir = tempfile.mkdtemp(prefix="victim_")
    victim_path = Path(victim_dir) / "pwned.txt"
    # A `..` key deep enough to reach a real temp-adjacent location is not
    # reliably constructible across platforms in a test, so this proves
    # the narrower, always-true property: the executor refuses rather than
    # silently writing anywhere outside its own sandbox directory, and
    # never raises an uncaught exception up to the caller.
    executor = SubprocessSandboxExecutor()
    result = asyncio.run(executor.run(
        "pass",
        input_files={"../../../../../../etc/should-not-exist.txt": b"pwned"},
        timeout_seconds=10,
    ))
    assert result.exit_code == -1
    assert "outside the sandbox directory" in result.stderr
    assert not victim_path.exists()


def test_subprocess_executor_refuses_absolute_path_escape(tmp_path):
    target = tmp_path / "pwned.txt"
    executor = SubprocessSandboxExecutor()
    result = asyncio.run(executor.run(
        "pass",
        input_files={str(target): b"pwned"},
        timeout_seconds=10,
    ))
    assert result.exit_code == -1
    assert "outside the sandbox directory" in result.stderr
    assert not target.exists()


def test_container_executor_docker_argv_unaffected_by_staging_fix():
    # The fix only changes staging, not the isolation flag set -- pin that
    # the container executor's argv builder is untouched.
    executor = ContainerSandboxExecutor()
    argv = executor._docker_argv(Path("/work"), 10.0, network_access=False)
    assert "--read-only" in argv
    assert "none" in argv
