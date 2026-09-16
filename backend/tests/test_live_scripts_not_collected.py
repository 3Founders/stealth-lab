"""
Regression test for backend/conftest.py's collect_ignore_glob (see that
file's docstring for the full story: backend/test_*_live.py are standalone
manual live-DB smoke scripts, not part of the pytest suite, and several of
them set a hardcoded os.environ["DATABASE_URL"] at module level that
clobbers the rest of the pytest process if pytest ever imports them during
collection).

This runs a real `pytest --collect-only` scoped to backend/ (the whole
tree, no path filter -- the exact bare invocation shape that used to hit
the leak) in a subprocess, and asserts these root-level *_live.py files
never appear as collected items while backend/tests/ items still do (i.e.
the glob protects the right files without over-excluding the real suite).
A subprocess is used rather than pytest.main() in-process so this doesn't
touch the outer test run's own collection state.

(2026-09-16: test_decompose_decide_live.py, test_detect_conflict_trigger_
live.py, test_orphan_cleanup_live.py, test_propose_synthesis_live.py, and
test_submit_approval_live.py were DELETED, not just excluded -- they only
ever probed the debate/decomposition MCP tools removed from the surface
that day (see app/mcp_server/server.py's own top docstring). Dropped from
this list rather than left asserting exclusion of files that no longer
exist.)
"""
import pathlib
import subprocess
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent

LIVE_SCRIPTS = [
    "test_apply_change_set_live.py",
    "test_full_chain_live.py",
    "test_tasks_extension_live.py",
]


def test_root_level_live_scripts_excluded_from_bare_collection():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr

    for script in LIVE_SCRIPTS:
        assert script not in output, (
            f"{script} was collected -- collect_ignore_glob in "
            f"backend/conftest.py should have excluded it"
        )

    assert "tests/test_env_guard_offline.py" in output.replace("\\", "/"), (
        "backend/tests/ items should still be collected -- "
        "collect_ignore_glob must not over-exclude the real suite"
    )
