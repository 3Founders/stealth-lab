"""
Goal-level verification contract (Prompt 2 Sec 9, 2026-09-15): "Every
executable Goal route MUST have a verification contract... A successful
model response is NOT equivalent to successful Goal completion."

Reads `goals.verification_requirement` (migration 83's real JSONB
column, `NOT NULL DEFAULT '{}'` -- a real column that NOTHING populated
or consumed before this module; no shape was previously defined for it
anywhere in this codebase, confirmed by reading migration 83 and every
`search_goals`/`get_goal`/`resolve_goal` caller before writing this).
This module DEFINES a real, minimal, honest shape rather than guessing
at a convention that doesn't exist yet:

    {"method": "deterministic_check", "command": "pytest tests/checkout"}
    {"method": "artifact_inspection", "expected_files": ["dist/bundle.js"]}
    {"method": "human_review"}
    {}   -- no contract at all (today's overwhelming real default)

`deterministic_check` reuses the EXISTING sandbox mechanism
(`app.services.sandbox_executor.SubprocessSandboxExecutor`, the SAME
one `providers.py::DeterministicProvider` already wraps for
Implementation execution -- same disclosed non-isolation caveat, not a
second sandbox). `artifact_inspection` checks the REAL `output_files`
an Implementation's own `NodeResult.data` already reported -- never a
guessed file. `human_review` is NEVER auto-passed -- it honestly
reports `needs_human_review`, a real, disclosed non-answer, same
posture `app/services/verification.py::record_human_review` already
enforces for the Procedure-run ladder.

NOT WIRED INTO `app/services/verification.py`'s real ladder /
`verification_results` table -- that table's every row is keyed to a
real `execution_run_id` (migration 36's Procedure-anchored
`execution_runs`), which a bare Goal-DAG walk
(`goal_execution.py`, this session's own disclosed scope limit) never
creates. This module computes a real, honest verification OUTCOME
inline and returns it to its caller (`goal_execution.py`); persisting
it into the shared ladder remains open technical debt, same frozen-
invariant reasoning `goal_execution.py`'s own module docstring already
states for durable execution generally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from app.execution.graph_executor import NodeResult
from app.services.sandbox_executor import SandboxExecutor, SubprocessSandboxExecutor

VerificationState = Literal["verified", "checked", "failed_verification", "needs_human_review", "unverified"]

_SUPPORTED_METHODS = ("deterministic_check", "artifact_inspection", "human_review")


@dataclass
class VerificationOutcome:
    method: str  # "" when no contract was given at all
    state: VerificationState
    detail: str


def _no_contract() -> VerificationOutcome:
    return VerificationOutcome(
        method="", state="unverified",
        detail=(
            "this Goal has no verification_requirement contract -- a successful "
            "implementation response is NOT treated as verified Goal completion "
            "(Prompt 2 Sec 9); execution outcome alone still governs node status"
        ),
    )


async def _run_deterministic_check(
    contract: dict, *, executor: Optional[SandboxExecutor] = None, timeout_seconds: float = 30.0,
) -> VerificationOutcome:
    command = contract.get("command")
    if not command or not isinstance(command, str):
        return VerificationOutcome(
            method="deterministic_check", state="unverified",
            detail="deterministic_check contract is missing a real 'command' string -- cannot run it, never guessed",
        )
    executor = executor or SubprocessSandboxExecutor()
    # subprocess.run(..., shell=True) inside the sandboxed script -- the
    # SAME real, already-disclosed non-isolating posture
    # DeterministicProvider's own docstring states for this exact
    # executor (network_access=False is NOT enforced by it either);
    # this is not a stronger or weaker sandbox than Implementation
    # execution already uses, deliberately, so verification and
    # execution carry identical real trust assumptions.
    script = (
        "import subprocess, sys\n"
        f"r = subprocess.run({command!r}, shell=True)\n"
        "sys.exit(r.returncode)\n"
    )
    result = await executor.run(script, input_files={}, timeout_seconds=timeout_seconds, network_access=False)
    if result.timed_out:
        return VerificationOutcome(
            method="deterministic_check", state="failed_verification",
            detail=f"verification command {command!r} timed out after {timeout_seconds}s",
        )
    if result.exit_code == 0:
        return VerificationOutcome(
            method="deterministic_check", state="verified",
            detail=f"verification command {command!r} exited 0",
        )
    return VerificationOutcome(
        method="deterministic_check", state="failed_verification",
        detail=f"verification command {command!r} exited {result.exit_code}: {result.stderr[:300]}",
    )


def _run_artifact_inspection(contract: dict, node_result: Optional[NodeResult]) -> VerificationOutcome:
    expected = contract.get("expected_files")
    if not expected or not isinstance(expected, list):
        return VerificationOutcome(
            method="artifact_inspection", state="unverified",
            detail="artifact_inspection contract is missing a real 'expected_files' list -- cannot check, never guessed",
        )
    output_files = (node_result.data.get("output_files") if node_result and node_result.data else None) or {}
    missing = [f for f in expected if f not in output_files]
    if missing:
        return VerificationOutcome(
            method="artifact_inspection", state="failed_verification",
            detail=f"expected output file(s) not produced by the implementation's own real result: {missing}",
        )
    return VerificationOutcome(
        method="artifact_inspection", state="checked",
        detail=f"all {len(expected)} expected output file(s) present in the implementation's real result",
    )


async def run_goal_verification(
    contract: dict, node_result: Optional[NodeResult] = None, *,
    executor: Optional[SandboxExecutor] = None,
) -> VerificationOutcome:
    """The single real entry point `goal_execution.py` calls after an
    Implementation reports `status='success'`. Never auto-passes an
    unsupported or malformed contract -- an unrecognized `method`, or no
    contract at all, is honestly `unverified`, never silently upgraded
    to `verified`."""
    if not contract:
        return _no_contract()

    method = contract.get("method")
    if method == "deterministic_check":
        return await _run_deterministic_check(contract, executor=executor)
    if method == "artifact_inspection":
        return _run_artifact_inspection(contract, node_result)
    if method == "human_review":
        return VerificationOutcome(
            method="human_review", state="needs_human_review",
            detail="this Goal's verification contract requires human review -- never auto-passed",
        )
    return VerificationOutcome(
        method=str(method), state="unverified",
        detail=f"verification method {method!r} is not automatically checkable by this executor (supported: {_SUPPORTED_METHODS})",
    )
