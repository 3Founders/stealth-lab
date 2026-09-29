"""A benchmark task's tests as a frozen Kel Benchmark on the task's Goal.

A SWE task comes with everything a Benchmark needs: the environment (docker image, repository, base commit), the
protocol (apply the candidate patch and the test patch, run the test command), and the success criteria (every
FAIL_TO_PASS test passes, every PASS_TO_PASS test keeps passing). Stored frozen, so its meaning never drifts; Kel can
then evaluate any Procedure against it with its own runs (Evaluations require Kel-executed runs, by design).

A task with no runnable environment (no docker image: SWE-bench-extra, SWE-Gym) gets no Benchmark -- a Benchmark that
cannot be run would be a promise Kel cannot keep. The caller records that as a reason.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class TaskTests:
    task_key: str
    source: str               # e.g. "hf:nebius/SWE-rebench@89cdfbab..."
    repo: str
    base_commit: str
    docker_image: Optional[str]
    test_cmd: Optional[str]
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    test_patch: str
    language: Optional[str] = None
    fail_to_fail: tuple[str, ...] = ()      # tests that fail before AND after the fix: known failures, not graded
    pass_to_fail: tuple[str, ...] = ()      # tests the accepted fix breaks

    @property
    def runnable(self) -> bool:
        """A task Kel can faithfully evaluate against: a runnable image, tests that must flip, and an accepted fix
        that breaks nothing. Always-failing tests (FAIL_TO_FAIL) are listed in the criteria as ignored, not graded."""
        return bool(self.docker_image and self.fail_to_pass and self.test_patch and not self.pass_to_fail)


async def ensure_task_benchmark(pool: Any, *, goal_id: str, tests: TaskTests) -> Optional[str]:
    """The task's frozen Benchmark id on `goal_id`, created on first use; None when the task is not runnable."""
    from app.services.product_model import create_benchmark, freeze_benchmark

    if not tests.runnable:
        return None
    existing = await pool.fetchval(
        "SELECT id::text FROM benchmarks WHERE goal_id = $1::uuid AND metadata->>'task_key' = $2 "
        "ORDER BY created_at LIMIT 1", goal_id, tests.task_key)
    if existing:
        return existing
    test_patch_sha = hashlib.sha256(tests.test_patch.encode("utf-8")).hexdigest()
    created = await create_benchmark(
        pool, goal_id=goal_id, name=f"Task tests: {tests.task_key}",
        description=f"The upstream tests for {tests.task_key} ({tests.repo} at {tests.base_commit[:12]}).",
        evaluation_protocol={
            "kind": "swe_task_tests", "steps": [
                "check out the repository at base_commit inside the docker image",
                "apply the candidate patch", "apply the test patch", "run the test command",
            ],
            "test_cmd": tests.test_cmd, "test_patch_sha256": test_patch_sha, "test_patch": tests.test_patch,
        },
        environment_specification={"docker_image": tests.docker_image, "repo": tests.repo,
                                   "base_commit": tests.base_commit, "language": tests.language},
        success_criteria={"all_pass": list(tests.fail_to_pass), "still_pass": list(tests.pass_to_pass),
                          "ignored_known_failures": list(tests.fail_to_fail),
                          "rule": "every FAIL_TO_PASS test passes and every PASS_TO_PASS test still passes; "
                                  "tests that fail with and without the accepted fix are not graded"},
        comparison_policy={"metric": "resolved", "direction": "higher_is_better"},
        provenance=tests.source,
        metadata={"task_key": tests.task_key, "source": tests.source},
    )
    await freeze_benchmark(pool, created["id"])
    return str(created["id"])
