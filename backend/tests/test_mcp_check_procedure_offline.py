"""
DB-free wiring proof for the MCP server's check_procedure tool (demo.md
C5, tool #9 -- backend/app/mcp_server/server.py). Same pattern the repo's
own live scripts (backend/test_*_live.py) already use to call a
@server.tool()-decorated function directly: the decorator leaves it a
plain, directly-awaitable coroutine function.

This file does NOT re-prove the decision logic (that's
test_check_procedure_reuse_offline.py's job, against the real
check_procedure_reuse()) -- it proves the THIN-WRAPPER contract: the tool
calls check_procedure_reuse with the right procedure_id, serializes its
ProcedureVerdict to demo.md §3's exact JSON shape, and turns
ProcedureNotFound into a plain "REFUSED: ..." string, same style as this
server's other tools' bad-input handling (e.g. detect_conflict_trigger).

REAL, FOUND-THE-HARD-WAY GOTCHA: nothing under tests/ imported
app.mcp_server.server before this file existed. Importing it runs its
own module-level `load_dotenv()`, which sets DATABASE_URL (previously
unset in an offline run) as a process-wide os.environ mutation --
process-global state a single test FILE has no business changing for
every OTHER test file pytest collects afterward. Confirmed by a real
full-suite run: every *_e2e.py module collected alphabetically after
this one saw DATABASE_URL suddenly present, so their own
skipif(no-DATABASE_URL) gates stopped skipping and instead tried real
(unreachable-from-this-run) connections -- 69 failures that vanished
once the snapshot/restore below was added. Restoring os.environ to its
pre-import snapshot immediately after the import (at COLLECTION time,
not inside a fixture -- a fixture only runs once tests execute, which is
too late to protect other modules' import-time skipif decorators) keeps
this file's own load_dotenv() side effect from leaking into the rest of
the suite.
"""
import asyncio
import json
import os

_ENV_BEFORE_MCP_SERVER_IMPORT = dict(os.environ)

import pytest

from app.mcp_server.server import check_procedure
from app.services.applicability import ProcedureNotFound, ProcedureVerdict
import app.services.applicability as applicability

for _key in set(os.environ) - set(_ENV_BEFORE_MCP_SERVER_IMPORT):
    del os.environ[_key]
for _key, _val in _ENV_BEFORE_MCP_SERVER_IMPORT.items():
    if os.environ.get(_key) != _val:
        os.environ[_key] = _val


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool=None):
        self.request_context = FakeRequestContext(pool)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def patched_verdict(monkeypatch):
    """Stubs check_procedure_reuse entirely -- this file only proves the
    tool's own wrapping/serialization, not the decision logic underneath."""
    calls = []

    async def fake(pool, *, procedure_id, access_scope=None):
        calls.append({"pool": pool, "procedure_id": procedure_id, "access_scope": access_scope})
        return ProcedureVerdict(
            verdict="WOULD_REFUSE",
            procedure="pagination procedure",
            reason="precondition claim cl_17 superseded by cl_23",
            evidence=["claim:cl_17", "claim:cl_23"],
            capability_note="0 failures recorded, environment changed",
        )

    monkeypatch.setattr(applicability, "check_procedure_reuse", fake)
    return calls


def test_check_procedure_returns_the_pinned_demo_md_json_shape(patched_verdict):
    ctx = FakeContext(pool="fake-pool-sentinel")
    raw = _run(check_procedure("proc-123", "should I reuse this?", ctx))
    payload = json.loads(raw)

    assert payload == {
        "verdict": "WOULD_REFUSE",
        "procedure": "pagination procedure",
        "reason": "precondition claim cl_17 superseded by cl_23",
        "evidence": ["claim:cl_17", "claim:cl_23"],
        "capability_note": "0 failures recorded, environment changed",
    }


def test_check_procedure_passes_procedure_id_and_the_real_pool_through(patched_verdict):
    ctx = FakeContext(pool="fake-pool-sentinel")
    _run(check_procedure("proc-123", "ignored by the wrapper today", ctx))

    assert len(patched_verdict) == 1
    assert patched_verdict[0]["procedure_id"] == "proc-123"
    assert patched_verdict[0]["pool"] == "fake-pool-sentinel"


def test_check_procedure_turns_procedure_not_found_into_a_refused_string(monkeypatch):
    async def fake_not_found(pool, *, procedure_id, access_scope=None):
        raise ProcedureNotFound(f"no live procedure for procedure_id={procedure_id}")

    monkeypatch.setattr(applicability, "check_procedure_reuse", fake_not_found)

    ctx = FakeContext(pool="fake-pool-sentinel")
    result = _run(check_procedure("does-not-exist", "irrelevant", ctx))

    assert result.startswith("REFUSED:")
    assert "does-not-exist" in result
