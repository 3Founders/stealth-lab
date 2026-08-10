"""
Live check for sandboxed repo execution. Needs a real Docker daemon.

Everything in app/services/repo_execution.py that unit tests can reach is
already covered in tests/test_repo_execution.py. This covers what they
can't: whether containers actually start, whether `--network none` really
severs the network on THIS host, and whether a real SWE-bench image runs a
real test suite to a correct verdict.

Nothing here has been run yet -- there was no Docker daemon in the
environment this was written in. Treat a passing run as the first real
evidence, not a formality.

    python integration_check_v2_repo_execution.py              # checks 1-3, no dataset needed
    python integration_check_v2_repo_execution.py --instance-file inst.json   # + full end-to-end
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from app.services.repo_execution import (
    DockerUnavailable,
    docker_available,
    docker_command,
    evaluate_patch,
    prefetch_checkout,
    run_tests,
)

PASS, FAIL = "  [ok]", "  [FAIL]"


def check_docker() -> bool:
    print("\n1. Docker daemon reachable")
    if not docker_available():
        print(FAIL, "no Docker daemon. Everything below is blocked on this.")
        print("       This module fails closed -- it will refuse to run patches,")
        print("       not run them unsandboxed. That's the intended behaviour.")
        return False
    print(PASS, "daemon responds")
    return True


def check_network_is_really_severed() -> bool:
    """
    The single most important check here. `--network none` is the whole
    security argument for running untrusted patches at all, and it is
    asserted rather than verified everywhere else. Same standard sandbox.py
    held itself to: prove the isolation actually blocks, don't trust the flag.
    """
    print("\n2. --network none actually blocks egress")
    script = "getent hosts github.com >/dev/null 2>&1 && echo REACHED || echo BLOCKED"
    try:
        with_net = subprocess.run(
            ["docker", "run", "--rm", "-i", "alpine", "sh", "-s"],
            input=script, capture_output=True, text=True, timeout=120,
        )
        without_net = subprocess.run(
            docker_command("alpine", script), input=script,
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(FAIL, f"could not run the comparison: {exc}")
        return False

    got_net = "REACHED" in with_net.stdout
    got_blocked = "BLOCKED" in without_net.stdout

    # Both halves matter. If the control run ALSO can't reach the network,
    # the check proves nothing -- it would "pass" on a host with no
    # connectivity at all, which is exactly the kind of false confidence
    # this project has been bitten by before.
    if not got_net:
        print(FAIL, "control container had no network either -- check is inconclusive,")
        print("       not proof of isolation. Fix host connectivity and re-run.")
        return False
    if not got_blocked:
        print(FAIL, "network was NOT blocked with --network none. Do not run patches here.")
        return False
    print(PASS, "reachable without the flag, blocked with it -- isolation is real")
    return True


def check_resource_limits() -> bool:
    print("\n3. Resource limits actually enforced")
    # A genuine over-allocation must be killed, not merely slowed.
    script = "python3 -c \"a=' '*(3*1024**3)\" 2>&1 | head -1; echo EXIT=$?"
    try:
        proc = subprocess.run(
            docker_command("python:3.11-slim", script, memory="256m"),
            input=script, capture_output=True, text=True, timeout=180,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(FAIL, f"could not run: {exc}")
        return False
    if proc.returncode == 0 and "MemoryError" not in proc.stdout and "Killed" not in (
        proc.stdout + proc.stderr
    ):
        print(FAIL, "a 3GB allocation succeeded under a 256m cap -- limits are not applied")
        return False
    print(PASS, "over-limit allocation was stopped")
    return True


def check_end_to_end(instance_path: str) -> bool:
    """
    The real thing: a genuine instance, its own gold patch, a real verdict.

    Uses the GOLD patch deliberately -- it is known-correct, so the only
    honest expected result is `resolved=True`. If the harness reports
    anything else, the harness is wrong, not the patch. A candidate patch
    couldn't distinguish those two cases.
    """
    print("\n4. End-to-end on a real instance (gold patch must resolve)")
    with open(instance_path) as f:
        instance = json.load(f)

    print(f"     instance: {instance['instance_id']}")
    try:
        path = prefetch_checkout(instance["repo"], instance["base_commit"])
        print(PASS, f"checkout cached at {path}")
    except RuntimeError as exc:
        print(FAIL, f"checkout failed: {exc}")
        return False

    try:
        result = evaluate_patch(instance, instance["patch"])
    except DockerUnavailable as exc:
        print(FAIL, f"isolation unavailable: {exc}")
        return False

    print(f"     outcome={result.outcome} resolved={result.resolved}")
    print(f"     F2P passed={len(result.fail_to_pass.passed)} "
          f"failed={len(result.fail_to_pass.failed)} "
          f"missing={len(result.fail_to_pass.missing)}")

    if result.fail_to_pass.missing:
        # Almost always means the image's test runner differs from the
        # pytest default, or test_patch didn't apply -- a plumbing problem
        # worth naming precisely rather than reading as a failed fix.
        print(FAIL, "expected tests never ran. Check the test command for this repo,")
        print(f"       and that test_patch applied. Missing: "
              f"{sorted(result.fail_to_pass.missing)[:3]}")
        return False
    if not result.resolved:
        print(FAIL, "the gold patch did not resolve. The harness is wrong, not the patch.")
        return False
    print(PASS, "gold patch resolves -- harness produces correct ground truth")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-file", help="JSON file with one SWE-bench instance row")
    args = ap.parse_args()

    if not check_docker():
        return 1
    results = [check_network_is_really_severed(), check_resource_limits()]
    if args.instance_file:
        results.append(check_end_to_end(args.instance_file))
    else:
        print("\n4. End-to-end: skipped (pass --instance-file to run it)")

    print("\n" + ("all checks passed" if all(results) else "SOME CHECKS FAILED"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
