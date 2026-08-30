"""
Regression test for backend/conftest.py's collect_ignore_glob (see that
file's docstring for the full story: backend/test_*_live.py are standalone
manual live-DB smoke scripts, not part of the pytest suite, and several of
them set a hardcoded os.environ["DATABASE_URL"] at module level that
clobbers the rest of the pytest process if pytest ever imports them during
collection).

This runs a real `pytest --collect-only` scoped to backend/ (the whole
tree, no path filter -- the exact bare invocation shape that used to hit
the leak) in a subprocess, and asserts the 8 root-level *_live.py files
never appear as collected items while backend/tests/ items still do (i.e.
the glob protects the right files without over-excluding the real suite).
A subprocess is used rather than pytest.main() in-process so this doesn't
touch the outer test run's own collection state.
"""
import pathlib
import subprocess
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent

LIVE_SCRIPTS = [
    "test_apply_change_set_live.py",
    "test_decompose_decide_live.py",
    "test_detect_conflict_trigger_live.py",
    "test_local_agent_runner_live.py",
    "test_six_tool_mcp_surface_live.py",
    "test_skill_ingestion_live.py",
    "test_orphan_cleanup_live.py",
    "test_propose_synthesis_live.py",
    "test_real_mcp_client_live.py",
    "test_find_best_way_live.py",
    "test_graph_executor_live.py",
    "test_stored_procedure_multistep_live.py",
    "test_submit_approval_live.py",
    "test_tier1_execution_live.py",
    "test_tier2_multistep_live.py",
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
