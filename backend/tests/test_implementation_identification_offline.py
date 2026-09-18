"""Offline tests for the deterministic tool->Implementation identity map
(trajectory-ingestion-hardening task, Sec 8). `identify_implementation`
is pure; `find_or_create_implementation_identity` is exercised only for
its SQL shape via a FakePool (no real database)."""
from __future__ import annotations

import pytest

from app.services.implementation_identification import (
    find_or_create_implementation_identity,
    identify_implementation,
)


class TestIdentifyImplementation:
    def test_bash_pytest_command_resolves_to_pytest_not_generic_bash(self):
        assert identify_implementation("Bash", "pytest tests/test_foo.py") == ("pytest", "python", "tool")

    def test_bash_other_command_resolves_to_generic_shell(self):
        assert identify_implementation("Bash", "ls -la") == ("bash", "posix", "tool")

    def test_openhands_run_action_with_test_command_resolves_to_pytest(self):
        assert identify_implementation("run", "pytest -q") == ("pytest", "python", "tool")

    def test_openhands_read_action_resolves_to_its_own_editor(self):
        assert identify_implementation("read") == ("openhands_file_editor", "openhands", "tool")

    def test_claude_code_read_tool_resolves_deterministically(self):
        assert identify_implementation("Read") == ("file_read", "claude_code", "tool")

    def test_unrecognized_tool_returns_none_not_a_guess(self):
        """No phantom Implementation -- an unrecognized tool honestly
        resolves to 'no known Implementation', never an invented one."""
        assert identify_implementation("SomeBrandNewTool") is None
        assert identify_implementation(None, None) is None

    def test_git_commit_is_identified_as_git_not_generic_bash(self):
        assert identify_implementation("Bash", "git commit -m 'x'") == ("git", "posix", "tool")


# ------------------------------------------------ find_or_create (fake DB)

class FakePool:
    def __init__(self, existing_row=None):
        self._existing_row = existing_row
        self.fetched: list[tuple] = []

    async def fetchrow(self, sql, *args):
        self.fetched.append((sql, args))
        return self._existing_row


@pytest.mark.asyncio
async def test_find_or_create_returns_existing_row_without_registering(monkeypatch):
    existing = {"id": "impl-1", "name": "pytest", "provider": "python", "version": 1}
    pool = FakePool(existing_row=existing)

    async def boom(*a, **k):
        raise AssertionError("register() must not be called when an identity already exists")

    import app.execution.implementation_registry as reg
    monkeypatch.setattr(reg, "register", boom)

    result = await find_or_create_implementation_identity(
        pool, name="pytest", provider="python", kind="tool", created_by="test",
    )
    assert result == existing
    assert "implementations" in pool.fetched[0][0]


@pytest.mark.asyncio
async def test_find_or_create_registers_when_absent(monkeypatch):
    pool = FakePool(existing_row=None)
    registered = {}

    async def fake_register(pool_, **kwargs):
        registered.update(kwargs)
        return {"id": "impl-new", **kwargs}

    import app.execution.implementation_registry as reg
    monkeypatch.setattr(reg, "register", fake_register)

    result = await find_or_create_implementation_identity(
        pool, name="ripgrep", provider="posix", kind="tool", created_by="test",
    )
    assert result["id"] == "impl-new"
    assert registered["name"] == "ripgrep"
    assert registered["provider"] == "posix"
