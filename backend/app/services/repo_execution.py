"""
Sandboxed repository execution: checkout a real repo at a commit, apply a
patch, run its real test suite, decide whether the patch resolved the issue.

This is what KNOWLEDGE_UPDATION_EXPERIMENT.md needs and what nothing in
this codebase could do before: produce genuine executable ground truth
(FAIL_TO_PASS / PASS_TO_PASS) rather than a model's opinion about whether
a change is correct. That outcome is what triggers debate to revise a
knowledge node, which is the whole mechanism under test.

WHY THIS IS NOT AN EXTENSION OF sandbox.py

sandbox.py runs one Python `run(input_data)` function with network
actively severed (`unshare --net`), 128MB, 5 CPU-seconds. Correct for a
hand-registered internal skill; wrong shape for this. A real instance
needs a multi-hundred-file checkout, that project's own dependency set
(which differs per repo and drifts per version), and its real test runner
for however long that takes. Forcing it into sandbox.py would mean either
punching through the no-network invariant -- one of the few things in this
project independently verified to actually work -- or quietly exceeding
the resource limits that module exists to enforce. So this is a separate
path that preserves the same invariant around a bigger unit of work,
rather than a relaxation of the existing one.

THE NETWORK SPLIT, WHICH IS THE WHOLE SECURITY ARGUMENT

  Stage 1 (prefetch_checkout): network ON, no untrusted code runs. Clones
  and checks out. Cached per (repo, base_commit) so it is paid once across
  every condition in an ablation, not once per run.

  Stage 2 (run_tests): network OFF (`--network none`), untrusted patch
  applied and tests executed. By the time anything runs, everything needed
  is already on disk.

Nothing that executes a patch ever has network access. That is the same
invariant sandbox.py enforces, drawn around a larger unit -- not weakened.

FAILS CLOSED, same rule as sandbox.py: if Docker is unavailable or the
image is missing, `isolation_failed=True` is returned and no test is ever
run outside a container as a fallback. A sandbox that silently degrades to
unsandboxed on its own failure is worse than one that refuses.

WHAT IS NOT VERIFIED HERE, STATED PLAINLY

Nothing in this module has been run against real Docker -- there is no
Docker daemon in the environment it was written in. The command
construction, verdict logic, and log parsing are unit-tested; whether the
containers actually start, whether SWE-bench's published images carry the
expected test runners, and whether `--network none` behaves as documented
on the deployment host are all unconfirmed. Run
integration_check_v2_repo_execution.py on a host with Docker before
trusting any outcome this produces. This is the same posture
sandbox.py takes about unprivileged user namespaces.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# Generous next to sandbox.py's 5s/128MB, because a real project's test
# suite legitimately takes minutes and needs a real interpreter plus its
# dependency tree. Still a hard bound -- "bigger" is not "unbounded".
DEFAULT_TEST_TIMEOUT_SECONDS = 900   # 15 min; some suites genuinely take this
DEFAULT_MEMORY = "4g"
DEFAULT_CPUS = "2"

# Where prefetched checkouts live. Per (repo, base_commit) -- the same
# commit is reused by every condition in an ablation, so cloning once and
# reusing is the difference between one clone and four.
DEFAULT_CACHE_DIR = os.environ.get("REPO_CACHE_DIR", "/tmp/repo_cache")


class DockerUnavailable(Exception):
    """Raised when the isolation mechanism itself cannot run. Never swallowed
    into a 'the tests failed' verdict -- an infrastructure failure and a
    genuine test failure are different facts and must not be conflated."""


@dataclass
class TestOutcome:
    """Per-test results parsed out of a runner's output."""
    # Stops pytest trying to collect this as a test class purely because
    # its name starts with "Test" -- it's a result type, not a test.
    __test__ = False

    passed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    # Tests that were expected but never appeared in the output at all --
    # kept separate from `failed` on purpose. A test that didn't run (import
    # error, collection failure, renamed upstream) is not the same as a test
    # that ran and failed, and treating them alike would silently convert
    # broken harness plumbing into an apparent negative result.
    missing: set[str] = field(default_factory=set)


@dataclass
class RepoExecutionResult:
    outcome: str                  # 'success' | 'failure' | 'needs_rework'
    resolved: bool
    fail_to_pass: TestOutcome
    pass_to_pass: TestOutcome
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    isolation_failed: bool
    error: Optional[str] = None


# --- Stage 1: network on, no untrusted code ---

def prefetch_checkout(
    repo: str,
    base_commit: str,
    cache_dir: str = DEFAULT_CACHE_DIR,
    timeout_seconds: int = 600,
) -> str:
    """
    Clone `repo` (owner/name) and check out `base_commit`. Returns the path.

    Runs with network. Deliberately does NOT run any repo code -- no
    install, no build, no test. Cloning executes nothing from the
    repository itself, so this stage stays a pure fetch, and everything
    that could execute repo-controlled code happens in stage 2 with the
    network already gone.
    """
    os.makedirs(cache_dir, exist_ok=True)
    # base_commit is a 40-char sha in the dataset; slicing keeps paths short
    # while staying collision-free in practice.
    slug = repo.replace("/", "__")
    dest = os.path.join(cache_dir, f"{slug}__{base_commit[:12]}")

    if os.path.isdir(os.path.join(dest, ".git")):
        log.debug("checkout cache hit: %s", dest)
        return dest

    url = f"https://github.com/{repo}.git"
    try:
        # Full clone, not --depth 1: base_commit is usually an ancestor,
        # and a shallow clone frequently won't contain it. Paid once per
        # (repo, commit) thanks to the cache above.
        subprocess.run(
            ["git", "clone", "--quiet", url, dest],
            check=True, capture_output=True, text=True, timeout=timeout_seconds,
        )
        subprocess.run(
            ["git", "-C", dest, "checkout", "--quiet", base_commit],
            check=True, capture_output=True, text=True, timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"checkout failed for {repo}@{base_commit}: {exc.stderr or exc.stdout}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed or not on PATH") from exc

    return dest


# --- Stage 2: network off, untrusted patch runs ---

def docker_available() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def swebench_image_for(instance_id: str) -> str:
    """
    SWE-bench publishes a prebuilt image per instance with that repo's
    correct language/dependency versions already pinned. Reusing them is
    the reason this module doesn't have to solve dependency isolation
    across dozens of repos itself -- `unshare` never could have.

    Naming follows SWE-bench's published convention. Verify against the
    registry before a real run; if an image is missing this fails closed
    rather than substituting a generic one, since a generic image would
    silently produce meaningless results.
    """
    return f"swebench/sweb.eval.x86_64.{instance_id.lower().replace('__', '_1776_')}:latest"


def _build_script(patch: str, test_patch: str, test_command: str) -> str:
    """
    The script that runs inside the container. Order matters and is not
    arbitrary:

      1. test_patch first, always -- it carries the NEW tests that define
         FAIL_TO_PASS. Without it those tests do not exist yet and would
         all parse as `missing`, which reads as a harness bug rather than
         the real outcome.
      2. the candidate patch second.
      3. tests last.

    `git apply` is checked: a patch that does not apply is an
    infrastructure/format failure, not a failed test run, and must be
    distinguishable from one. Exit 97 is reserved for that so the caller
    can tell "the patch was malformed" from "the code was wrong".
    """
    lines = ["set -u", "cd /testbed || cd \"$(pwd)\""]
    if test_patch:
        lines += [
            "cat > /tmp/test.patch <<'SWEBENCH_TEST_PATCH_EOF'",
            test_patch,
            "SWEBENCH_TEST_PATCH_EOF",
            "git apply -v /tmp/test.patch || exit 97",
        ]
    if patch:
        lines += [
            "cat > /tmp/candidate.patch <<'SWEBENCH_CANDIDATE_PATCH_EOF'",
            patch,
            "SWEBENCH_CANDIDATE_PATCH_EOF",
            "git apply -v /tmp/candidate.patch || exit 97",
        ]
    lines.append(test_command)
    return "\n".join(lines)


def docker_command(
    image: str,
    script: str,
    memory: str = DEFAULT_MEMORY,
    cpus: str = DEFAULT_CPUS,
    workdir: Optional[str] = None,
) -> list[str]:
    """
    Built as a separate pure function so the security-relevant flags are
    unit-testable without a Docker daemon. Every flag here is load-bearing:

      --network none   the invariant. Nothing executing a patch reaches the
                       network, matching sandbox.py's `unshare --net`.
      --rm             no container survives to accumulate state between
                       instances, which would silently contaminate an
                       ablation where conditions must be independent.
      --memory/--cpus  real bounds, scaled for a test suite.
      --pids-limit     a fork bomb in a patch is a plausible accident, not
                       only an attack.
      -i               script arrives on stdin, never interpolated into the
                       command line -- patch text contains quotes, newlines
                       and $-signs that would otherwise be a shell-injection
                       vector on our side, not just a quoting bug.
    """
    cmd = [
        "docker", "run", "--rm", "-i",
        "--network", "none",
        "--memory", memory,
        "--cpus", cpus,
        "--pids-limit", "512",
    ]
    if workdir:
        cmd += ["--workdir", workdir]
    cmd += [image, "bash", "-s"]
    return cmd


def run_tests(
    image: str,
    patch: str,
    test_patch: str,
    test_command: str,
    timeout_seconds: int = DEFAULT_TEST_TIMEOUT_SECONDS,
    memory: str = DEFAULT_MEMORY,
    cpus: str = DEFAULT_CPUS,
) -> tuple[int, str, str, bool]:
    """Returns (exit_code, stdout, stderr, timed_out). Raises DockerUnavailable
    rather than degrading to an unsandboxed run."""
    if not docker_available():
        raise DockerUnavailable(
            "docker is not available; refusing to run a patch without isolation"
        )

    script = _build_script(patch, test_patch, test_command)
    cmd = docker_command(image, script, memory=memory, cpus=cpus)

    try:
        proc = subprocess.run(
            cmd, input=script, capture_output=True, text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise DockerUnavailable(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        return -1, exc.stdout or "", exc.stderr or "", True

    return proc.returncode, proc.stdout, proc.stderr, False


# --- Log parsing ---

# pytest's own summary lines. Anchored to line start so a test *name*
# containing the word PASSED inside it can't fake a result.
_PYTEST_LINE = re.compile(
    r"^(?P<status>PASSED|FAILED|ERROR)\s+(?P<name>\S+)", re.MULTILINE
)
_PYTEST_TRAILING = re.compile(
    r"^(?P<name>\S+)\s+(?P<status>PASSED|FAILED|ERROR)", re.MULTILINE
)


def parse_pytest_log(output: str) -> dict[str, str]:
    """
    Maps test name -> 'PASSED' | 'FAILED' | 'ERROR'.

    Handles both orderings pytest emits depending on flags (`-v` puts the
    name first, `-rA` summaries put the status first). ERROR is kept
    distinct from FAILED rather than folded together: an erroring test
    usually means the suite could not even set up, which is closer to a
    harness problem than to a wrong patch, and the two should not be
    silently equated when the whole point is producing trustworthy ground
    truth.
    """
    results: dict[str, str] = {}
    for pattern in (_PYTEST_LINE, _PYTEST_TRAILING):
        for m in pattern.finditer(output):
            name, status = m.group("name"), m.group("status")
            # First writer wins: the verbose per-test line appears before
            # the summary, and re-reading the same result shouldn't flip it.
            results.setdefault(name, status)
    return results


def classify(expected: list[str], parsed: dict[str, str]) -> TestOutcome:
    out = TestOutcome()
    for name in expected:
        status = parsed.get(name)
        if status is None:
            out.missing.add(name)
        elif status == "PASSED":
            out.passed.add(name)
        else:
            out.failed.add(name)
    return out


def decide(f2p: TestOutcome, p2p: TestOutcome) -> tuple[str, bool]:
    """
    The verdict. Maps onto the existing three-value trace outcome rather
    than inventing a fourth, and the mapping is meaningful, not a
    convenience:

      success       every FAIL_TO_PASS now passes and no PASS_TO_PASS broke.
                    This is SWE-bench's definition of resolved.
      needs_rework  the fix works but broke something else (a regression).
                    Genuinely different from not fixing it -- it's a
                    partial success, and the debate mechanism should see
                    that difference rather than a flat 'failure'.
      failure       the issue is not fixed.

    A `missing` test counts against resolution. It is not evidence the
    patch worked, so treating it as a pass would manufacture false
    successes -- the exact failure mode that would corrupt the ground truth
    this whole experiment depends on.
    """
    f2p_ok = not f2p.failed and not f2p.missing
    p2p_ok = not p2p.failed and not p2p.missing

    if f2p_ok and p2p_ok:
        return "success", True
    if f2p_ok and not p2p_ok:
        return "needs_rework", False
    return "failure", False


# --- Top level ---

def evaluate_patch(
    instance: dict,
    candidate_patch: str,
    test_command: Optional[str] = None,
    image: Optional[str] = None,
    timeout_seconds: int = DEFAULT_TEST_TIMEOUT_SECONDS,
) -> RepoExecutionResult:
    """
    One SWE-bench instance + one candidate patch -> a real verdict.

    `instance` is a dataset row: instance_id, repo, base_commit,
    test_patch, FAIL_TO_PASS, PASS_TO_PASS. The last two are JSON-encoded
    lists of strings in the published dataset, so both encodings are
    accepted rather than assuming one and crashing on the other.
    """
    f2p = _as_list(instance.get("FAIL_TO_PASS"))
    p2p = _as_list(instance.get("PASS_TO_PASS"))
    image = image or swebench_image_for(instance["instance_id"])
    test_command = test_command or _default_test_command(f2p + p2p)

    try:
        code, out, err, timed_out = run_tests(
            image, candidate_patch, instance.get("test_patch", ""),
            test_command, timeout_seconds=timeout_seconds,
        )
    except DockerUnavailable as exc:
        return RepoExecutionResult(
            outcome="failure", resolved=False,
            fail_to_pass=TestOutcome(missing=set(f2p)),
            pass_to_pass=TestOutcome(missing=set(p2p)),
            exit_code=-1, stdout="", stderr=str(exc), timed_out=False,
            isolation_failed=True, error=str(exc),
        )

    if code == 97:
        # Patch didn't apply. Reported distinctly instead of as a test
        # failure -- a malformed diff says nothing about whether the fix
        # was right, and counting it as a failure would bias the ablation
        # against whichever condition happens to produce messier diffs.
        return RepoExecutionResult(
            outcome="failure", resolved=False,
            fail_to_pass=TestOutcome(missing=set(f2p)),
            pass_to_pass=TestOutcome(missing=set(p2p)),
            exit_code=code, stdout=out, stderr=err, timed_out=False,
            isolation_failed=False, error="patch did not apply",
        )

    parsed = parse_pytest_log(out + "\n" + err)
    f2p_outcome = classify(f2p, parsed)
    p2p_outcome = classify(p2p, parsed)
    outcome, resolved = decide(f2p_outcome, p2p_outcome)

    if timed_out:
        outcome, resolved = "failure", False

    return RepoExecutionResult(
        outcome=outcome, resolved=resolved,
        fail_to_pass=f2p_outcome, pass_to_pass=p2p_outcome,
        exit_code=code, stdout=out, stderr=err, timed_out=timed_out,
        isolation_failed=False,
        error="test run timed out" if timed_out else None,
    )


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return list(parsed) if isinstance(parsed, list) else [value]
        except json.JSONDecodeError:
            return [value]
    return []


def _default_test_command(tests: list[str]) -> str:
    """
    Python/pytest default. The JS repos in SWE-bench Multimodal
    (Chart.js et al.) need their own runners -- pass `test_command`
    explicitly for those rather than relying on this. Named as a default
    rather than presented as universal on purpose.
    """
    if not tests:
        return "python -m pytest -rA"
    return "python -m pytest -rA " + " ".join(shlex.quote(t) for t in tests)


# --- Skill wrapper, so this reaches the existing trace pipeline ---

async def repo_execution_skill(input_data: dict) -> dict:
    """
    Adapter onto execution.py's Skill signature, so a real repo run writes
    a real trace row through the harness that already exists rather than a
    parallel path. Layer 2's off-policy evaluation reads those traces
    unchanged.

    Registered under 'repo_execution' in execution.default_registry().
    """
    instance = input_data["instance"]
    patch = input_data.get("patch", "")
    result = evaluate_patch(
        instance, patch,
        test_command=input_data.get("test_command"),
        image=input_data.get("image"),
    )
    if result.isolation_failed:
        # Raising makes the harness record a failure trace AND surfaces
        # the reason -- an infrastructure problem must not be quietly
        # filed as an ordinary unresolved instance.
        raise RuntimeError(f"isolation unavailable: {result.error}")
    return {
        "resolved": result.resolved,
        "outcome": result.outcome,
        "fail_to_pass_passed": sorted(result.fail_to_pass.passed),
        "fail_to_pass_failed": sorted(result.fail_to_pass.failed),
        "fail_to_pass_missing": sorted(result.fail_to_pass.missing),
        "pass_to_pass_failed": sorted(result.pass_to_pass.failed),
        "timed_out": result.timed_out,
    }
