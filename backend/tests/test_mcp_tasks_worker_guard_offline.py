"""
Offline proving tests for the MCP TasksExtension single-worker guard
(Phase 34 / "MCP TASK STATE").

Confirms the guard is a real, wired boot-time check -- not another
comment-only "tripwire" like app/services/governance.py's own ">1 worker"
paragraph (grepped for a runtime enforcement of that paragraph before
writing this: there isn't one). This mirrors the one guard of this exact
shape that IS wired up in this codebase: app/services/authn.py's
assert_boot_posture, exercised directly (no settings/monkeypatch) in
test_authn_offline.py -- same style used here.

No DATABASE_URL needed: assert_single_worker and TasksExtension's
constructor are pure Python, no I/O.
"""
from __future__ import annotations

import pytest

from app.mcp_server.tasks_extension import (
    InMemoryTaskStore,
    TasksExtension,
    assert_single_worker,
)


# ---------------------------------------------------------------------------
# assert_single_worker directly (mirrors test_authn_offline.py's direct
# assert_boot_posture calls).
# ---------------------------------------------------------------------------


def test_single_worker_is_a_real_noop():
    assert assert_single_worker(1) is None  # does not raise


def test_multi_worker_refuses_to_boot():
    with pytest.raises(RuntimeError, match="in-memory and single-process"):
        assert_single_worker(2)


def test_multi_worker_error_names_the_declared_count():
    with pytest.raises(RuntimeError, match="MCP_WORKER_COUNT=5"):
        assert_single_worker(5)


def test_multi_worker_error_names_the_env_var_and_the_flag():
    # The message must give an operator the actual fix, not just say "no".
    with pytest.raises(RuntimeError, match="MCP_WORKER_COUNT=1"):
        assert_single_worker(3)
    with pytest.raises(RuntimeError, match=r"--workers 1"):
        assert_single_worker(3)


# ---------------------------------------------------------------------------
# TasksExtension() itself is the boot-time check -- constructing it with an
# injected worker_count (the test-facing seam) must behave identically to
# calling assert_single_worker directly, and a real instance must still
# work exactly as before at worker_count=1.
# ---------------------------------------------------------------------------


def test_tasks_extension_constructs_at_one_worker():
    ext = TasksExtension(worker_count=1)
    assert isinstance(ext.store, InMemoryTaskStore)


def test_tasks_extension_refuses_to_construct_above_one_worker():
    with pytest.raises(RuntimeError, match="in-memory and single-process"):
        TasksExtension(worker_count=2)


def test_tasks_extension_default_reads_settings_mcp_worker_count(monkeypatch):
    """No worker_count passed -- the real server.py call shape
    (`TasksExtension()`) -- must fall through to settings.mcp_worker_count,
    proving the boot-time check actually fires on the codepath production
    uses, not just the test-only injection seam."""
    import app.config as config_module

    monkeypatch.setattr(config_module.settings, "mcp_worker_count", 4, raising=True)
    with pytest.raises(RuntimeError, match="MCP_WORKER_COUNT=4"):
        TasksExtension()


def test_tasks_extension_default_boots_at_the_documented_default(monkeypatch):
    import app.config as config_module

    monkeypatch.setattr(config_module.settings, "mcp_worker_count", 1, raising=True)
    ext = TasksExtension()
    assert isinstance(ext.store, InMemoryTaskStore)
