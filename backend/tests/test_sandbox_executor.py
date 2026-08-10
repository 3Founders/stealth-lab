from __future__ import annotations

import asyncio
import sys

import pytest

from app.services.sandbox_executor import SubprocessSandboxExecutor


def test_basic_success_and_output_file_capture():
    executor = SubprocessSandboxExecutor()
    code = "open('output.txt', 'w').write('hello from sandbox')"
    result = asyncio.run(executor.run(code, input_files={}, timeout_seconds=10))
    assert result.exit_code == 0
    assert not result.timed_out
    assert result.output_files == {"output.txt": b"hello from sandbox"}


def test_input_file_is_staged_and_readable():
    executor = SubprocessSandboxExecutor()
    code = (
        "content = open('input.txt').read()\n"
        "open('output.txt', 'w').write(content.upper())"
    )
    result = asyncio.run(executor.run(
        code, input_files={"input.txt": b"hello"}, timeout_seconds=10,
    ))
    assert result.exit_code == 0
    assert result.output_files == {"output.txt": b"HELLO"}
    # input.txt itself must NOT be reported as an output file -- it was
    # staged, not produced by the code.
    assert "input.txt" not in result.output_files


def test_nested_input_path_is_staged_correctly():
    """Real AFTER tasks use nested paths like environment/data/input.pdf
    -- confirm this isn't flattened or mishandled."""
    executor = SubprocessSandboxExecutor()
    code = "content = open('environment/data/input.txt').read()\nprint(content)"
    result = asyncio.run(executor.run(
        code, input_files={"environment/data/input.txt": b"nested content"},
        timeout_seconds=10,
    ))
    assert result.exit_code == 0
    assert "nested content" in result.stdout


def test_nonzero_exit_and_stderr_captured():
    executor = SubprocessSandboxExecutor()
    code = "raise ValueError('deliberate failure')"
    result = asyncio.run(executor.run(code, input_files={}, timeout_seconds=10))
    assert result.exit_code != 0
    assert "ValueError" in result.stderr
    assert "deliberate failure" in result.stderr


def test_timeout_is_enforced_and_process_is_killed():
    executor = SubprocessSandboxExecutor()
    code = "import time; time.sleep(30)"
    result = asyncio.run(executor.run(code, input_files={}, timeout_seconds=1))
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.wall_time_seconds < 5  # confirms it was actually killed, not left to finish


def test_stdout_is_captured():
    executor = SubprocessSandboxExecutor()
    code = "print('real stdout line')"
    result = asyncio.run(executor.run(code, input_files={}, timeout_seconds=10))
    assert "real stdout line" in result.stdout


def test_network_access_false_warns_rather_than_silently_claims_enforcement():
    executor = SubprocessSandboxExecutor()
    with pytest.warns(UserWarning, match="NOT enforced"):
        asyncio.run(executor.run("pass", input_files={}, timeout_seconds=10, network_access=False))


def test_network_access_true_does_not_warn():
    executor = SubprocessSandboxExecutor()
    with warnings_should_be_empty():
        asyncio.run(executor.run("pass", input_files={}, timeout_seconds=10, network_access=True))


class warnings_should_be_empty:
    def __enter__(self):
        import warnings
        self._catcher = warnings.catch_warnings(record=True)
        self._records = self._catcher.__enter__()
        warnings.simplefilter("always")
        return self

    def __exit__(self, *exc):
        self._catcher.__exit__(*exc)
        assert len(self._records) == 0, f"unexpected warnings: {[str(w.message) for w in self._records]}"


def test_unmodified_files_are_not_reported_as_output():
    """A file that exists after the run but was never touched (same
    mtime) must not be reported as output -- only new/modified files."""
    executor = SubprocessSandboxExecutor()
    code = "pass"  # does nothing
    result = asyncio.run(executor.run(
        code, input_files={"untouched.txt": b"still here"}, timeout_seconds=10,
    ))
    assert result.output_files == {}


def test_tmp_directory_is_cleaned_up_after_run():
    """The sandbox temp dir must not leak on disk after each run."""
    import glob
    import tempfile
    executor = SubprocessSandboxExecutor()
    before = set(glob.glob(f"{tempfile.gettempdir()}/sandbox_exec_*"))
    asyncio.run(executor.run("pass", input_files={}, timeout_seconds=10))
    after = set(glob.glob(f"{tempfile.gettempdir()}/sandbox_exec_*"))
    assert after == before, "sandbox temp directory was not cleaned up"
