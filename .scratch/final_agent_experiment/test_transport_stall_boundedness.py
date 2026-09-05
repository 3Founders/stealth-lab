"""
Transport-risk gate: deterministic, offline proof that a B_default trial
cannot hang indefinitely, regardless of what the MCP transport does
underneath, and that the exact observed real failure
(`MCPError: SSE stream ended without a response`, seen on the non-scored
negative-control retrieval check after 333.9s -- see
b_default_embedding_verification_run.log) is captured as a real, correctly
False task_success rather than a hang or a false success.

Two independent facts established here, neither requiring the flaky real
network:

1. `run_one_trial`'s own `asyncio.wait_for(coro, timeout=time_budget_s)`
   (orchestrator.py) is a hard, harness-level bound on the WHOLE arm
   coroutine (session open, embed, retrieve, execute) -- so even in a
   worse case than what was actually observed (the transport genuinely
   never resolving, rather than the mcp SDK's own real behavior of
   resolving an abandoned request the moment its SSE stream ends, per
   mcp/client/streamable_http.py::_handle_sse_response /
   _resolve_abandoned_request, read before writing this test), the trial
   still terminates promptly with budget_exceeded=True, task_success=False
   -- never a hang.

2. The exact real error string observed is captured, produces
   task_success=False (never True), and completes fast when raised
   (not hung) -- proving the ALREADY-existing recording path never scores
   an aborted transport call as a success.

No code change was made to orchestrator.py, embeddings.py, or runner.py in
this pass -- this test documents and proves EXISTING behavior. One
pre-existing, cosmetic gap is documented (not fixed, per this gate's own
scope): classify_failure's marker lists don't recognize this literal error
string, so it currently falls through to "product_failure" rather than a
network/environmental bucket. This does not affect task_success (correctly
False either way) and therefore cannot bias the frozen A/B success-rate
comparison -- it only affects a descriptive label, which is why it is
documented, not changed, per this gate's explicit "prefer no code change if
transient and bounded" instruction.

Run: python .scratch/final_agent_experiment/test_transport_stall_boundedness.py
(plain asserts, no pytest dependency required, but pytest also collects it).
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import orchestrator  # noqa: E402
from orchestrator import classify_failure, run_one_trial  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FROZEN_COMMIT = "bd768e62a887b13a94fdd118693a5c671df1cf95"


class _FakeMCPError(Exception):
    """Same shape run_one_trial's `f'{type(exc).__name__}: {exc}'` produces
    for the real mcp.shared.exceptions.MCPError -- named identically so the
    resulting error string is byte-for-byte the one actually observed,
    without needing the real mcp package's constructor/exception-code
    plumbing for a pure string-shape test."""


_FakeMCPError.__name__ = "MCPError"


def _with_tmp_worktree_root(fn):
    """Real (but tiny, local, offline) git worktree add/remove against the
    frozen commit -- run_one_trial does this unconditionally, so exercising
    it for real (not mocked) proves this test is checking the actual
    function, not a stand-in for it. No network involved."""
    tmp_root = Path(orchestrator.__file__).resolve().parent / "_transport_gate_tmp"
    tmp_root.mkdir(exist_ok=True)
    try:
        return asyncio.run(fn(tmp_root))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_a_stalled_arm_coroutine_cannot_hang_a_trial(monkeypatch):
    """Worst case, stronger than anything actually observed: the arm
    coroutine never returns at all (models a transport that never resolves
    the abandoned request, contrary to the mcp SDK's own real behavior).
    time_budget_s=1 must still cut it off within a few seconds of real wall
    clock, not wait for the 30s sleep to finish on its own."""
    async def _never_returns(**kwargs):
        await asyncio.sleep(30.0)
        return {"arm": "B_default"}  # unreachable within the test's bound

    monkeypatch.setattr(orchestrator, "run_trial_arm_B", _never_returns)

    async def _run(tmp_root):
        t0 = time.monotonic()
        record = await run_one_trial(
            task_id="TRANSPORT_GATE", task_description="irrelevant for this check",
            arm="B_default", frozen_commit=FROZEN_COMMIT, repo_root=REPO_ROOT,
            tmp_root=tmp_root, model="irrelevant", max_steps=1, time_budget_s=1,
            server_url=None, token=None,
            out_dir=Path(orchestrator.__file__).resolve().parent / "_transport_gate_tmp" / "out",
            scored=False, verify_fn=None,
        )
        return record, time.monotonic() - t0

    record, wall = _with_tmp_worktree_root(_run)
    # wall includes run_one_trial's OWN real (synchronous, disk-bound) worktree
    # teardown after the coroutine is cut off -- confirmed separately, in
    # isolation, that plain asyncio.wait_for(sleep(10), timeout=1) in this
    # exact environment returns in ~1.0s (not 10s); the load-bearing claim
    # here is budget_exceeded/task_success below, not this generous outer
    # bound, which only guards against a genuine hang (which would show as
    # ~30s+ from the fake coroutine's own sleep, or worse, never returning).
    assert wall < 60.0, f"trial took {wall:.1f}s wall clock -- looks hung, not just slow teardown"
    assert record["budget_exceeded"] is True, record
    assert record["task_success"] is False, record
    assert record["error"] is None, record
    assert classify_failure(record) == "timeout", record


def test_the_exact_observed_sse_error_is_recorded_as_a_real_failure_not_a_hang(monkeypatch):
    """Feeds the byte-for-byte real error string observed on the
    non-scored negative-control check through the real recording path.
    Must complete fast (it's a raise, not a stall) and must never be
    scored as a success."""
    async def _raises_immediately(**kwargs):
        raise _FakeMCPError("SSE stream ended without a response")

    monkeypatch.setattr(orchestrator, "run_trial_arm_B", _raises_immediately)

    async def _run(tmp_root):
        t0 = time.monotonic()
        record = await run_one_trial(
            task_id="TRANSPORT_GATE", task_description="irrelevant for this check",
            arm="B_default", frozen_commit=FROZEN_COMMIT, repo_root=REPO_ROOT,
            tmp_root=tmp_root, model="irrelevant", max_steps=1, time_budget_s=60,
            server_url=None, token=None,
            out_dir=Path(orchestrator.__file__).resolve().parent / "_transport_gate_tmp" / "out",
            scored=False, verify_fn=None,
        )
        return record, time.monotonic() - t0

    record, wall = _with_tmp_worktree_root(_run)
    # Generous for the same real-disk worktree-teardown reason noted above --
    # this assertion still catches a genuine hang, just not a few extra
    # seconds of real git/filesystem cleanup.
    assert wall < 60.0, f"a raised (not stalled) error took {wall:.1f}s -- looks hung"
    assert record["error"] == "MCPError: SSE stream ended without a response", record
    assert record["budget_exceeded"] is False, record
    assert record["task_success"] is False, record
    assert record["deterministic_correctness"] == 0.0, record

    # Documented, not fixed (this gate's explicit scope): the current
    # marker-based classifier doesn't recognize this literal string as
    # network/environmental, so it falls through to product_failure. This
    # is a label-only gap -- task_success is already, correctly, False --
    # and does not bias the A/B success-rate comparison. Asserting the
    # CURRENT value (not the ideal one) so a future change to the
    # classifier's marker lists is a deliberate, visible decision, not a
    # silent behavior change this test masks.
    assert classify_failure(record) == "product_failure", (
        "classify_failure's behavior for this exact string changed -- "
        f"got {classify_failure(record)!r}. If this was a deliberate "
        "reclassification, update this assertion; if not, investigate."
    )


def test_never_produces_a_false_success(monkeypatch):
    """A transport-layer failure must never be indistinguishable from a
    real success in the one field the A/B comparison actually reads."""
    async def _raises_immediately(**kwargs):
        raise _FakeMCPError("SSE stream ended without a response")

    monkeypatch.setattr(orchestrator, "run_trial_arm_B", _raises_immediately)

    async def _run(tmp_root):
        return await run_one_trial(
            task_id="TRANSPORT_GATE", task_description="irrelevant for this check",
            arm="B_default", frozen_commit=FROZEN_COMMIT, repo_root=REPO_ROOT,
            tmp_root=tmp_root, model="irrelevant", max_steps=1, time_budget_s=60,
            server_url=None, token=None,
            out_dir=Path(orchestrator.__file__).resolve().parent / "_transport_gate_tmp" / "out",
            scored=False, verify_fn=None,
        )

    record = _with_tmp_worktree_root(_run)
    assert record["task_success"] is not True, record
    assert record["task_success"] is False, record


if __name__ == "__main__":
    import inspect

    class _FakeMonkeypatch:
        """Minimal stand-in so this file runs standalone (no pytest
        dependency), matching this directory's own established convention
        (test_usage_instrumentation.py, test_failure_classification.py)."""

        def __init__(self):
            self._sets: list[tuple[object, str, object]] = []

        def setattr(self, obj, name, value):
            self._sets.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._sets):
                setattr(obj, name, old)

    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        mp = _FakeMonkeypatch()
        try:
            if "monkeypatch" in inspect.signature(t).parameters:
                t(mp)
            else:
                t()
            print(f"PASS {t.__name__}")
        finally:
            mp.undo()
    print(f"\n{len(tests)}/{len(tests)} passed")
