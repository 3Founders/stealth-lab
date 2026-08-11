"""
SWE-bench Pro execution harness -- the ground-truth half of the experiment.

This is deliberately a faithful port of scaleapi/SWE-bench_Pro-os's
`swe_bench_pro_eval.py` (its `create_entryscript` / `eval_with_docker`
path), not an independent reimplementation. Grading a benchmark with your
own harness and then reporting the number as that benchmark's score is how
results stop being comparable to anyone else's; the ordering inside the
entryscript in particular is load-bearing and non-obvious, so it is copied
rather than reasoned about afresh:

    reset -> checkout base_commit -> apply candidate patch -> THEN check out
    the test files from the solution commit

The candidate patch lands *before* the tests do, so a patch that edits a
test file has that edit overwritten. That is what stops an agent passing by
rewriting the assertions, and it only works in this order.

Two deviations from upstream, both narrowing:

1. `--network none` on the test container. Upstream leaves network on by
   default. ansible/ansible's run_script was checked and touches the
   network nowhere, so blocking it costs nothing here and preserves the
   invariant repo_execution.py exists to enforce: nothing that executes an
   untrusted patch gets a socket. Repos whose run_script does `npm install`
   (NodeBB) cannot honour this and were excluded at selection time, not
   quietly downgraded.
2. ENV comes from `docker image inspect` rather than by re-parsing the
   Dockerfiles. Same values, and it drops a dependency on a dockerfiles/
   tree whose paths exceed MAX_PATH on Windows.

WINDOWS: every file written into the mounted workspace uses newline="\\n"
explicitly. A CRLF shebang line inside a container fails as
`bash\\r: No such file or directory`, which surfaces later as an empty
result rather than an error, i.e. as a silently wrong accuracy number.
"""
from __future__ import annotations

import json
import os
import re
import shutil
<<<<<<< HEAD
=======

from safe_fs import safe_rmtree
>>>>>>> 9f329aa6c2de1314c7a0c1690dd82ec5b50d7123
import subprocess
from dataclasses import dataclass, field
from typing import Optional

IMAGE_NS = "jefzda/sweap-images"
<<<<<<< HEAD
DEFAULT_TIMEOUT = 1800  # 30 min; ansible-test units on a cold container is slow
=======
DEFAULT_TIMEOUT = 2700  # 45 min, up from 30 -- a real run timed out on
                          # protonmail/webclients (a JS/TS monorepo, likely a
                          # heavier install/build step than ansible-test).
                          # This is a modest bump based on n=1 real observed
                          # timeout, not a comprehensive per-repo sizing --
                          # a smarter repo-aware policy is a natural next
                          # step once more real timeout data exists. Still
                          # overridable per-call via evaluate()'s timeout arg.
>>>>>>> 9f329aa6c2de1314c7a0c1690dd82ec5b50d7123
DEFAULT_MEMORY = "6g"
DEFAULT_CPUS = "4"


class HarnessError(Exception):
    """Infrastructure failed. Never folded into a 'tests failed' verdict --
    an image that won't pull and a patch that doesn't work are different
    facts, and conflating them biases whichever arm hit the bad luck."""


@dataclass
class EvalResult:
    instance_id: str
    resolved: bool
    # 'resolved' | 'f2p_failed' | 'p2p_broke' | 'patch_failed' | 'no_output' | 'timeout'
    status: str
    f2p_passed: list[str] = field(default_factory=list)
    f2p_missing: list[str] = field(default_factory=list)
    p2p_broke: list[str] = field(default_factory=list)
    n_tests_parsed: int = 0
    apply_status: str = ""
    exit_code: int = 0
    error: Optional[str] = None

    def summary(self) -> str:
        return (
            f"{self.instance_id[:60]:60s} {'RESOLVED' if self.resolved else 'no':>8s} "
            f"[{self.status}] f2p={len(self.f2p_passed)}ok/{len(self.f2p_missing)}bad "
            f"p2p_broke={len(self.p2p_broke)} parsed={self.n_tests_parsed}"
        )


def pylist(value) -> list[str]:
    """Pro stores list columns as Python reprs (single quotes), not JSON."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    s = str(value).strip()
    if not s:
        return []
    try:
        return list(json.loads(s))
    except json.JSONDecodeError:
        import ast

        try:
            return list(ast.literal_eval(s))
        except (ValueError, SyntaxError):
            return []


def image_for(sample: dict) -> str:
    return f"{IMAGE_NS}:{sample['dockerhub_tag']}"


# --- docker plumbing ---

def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          errors="replace")


def image_present(image: str) -> bool:
    return _run(["docker", "image", "inspect", image]).returncode == 0


def pull_image(image: str, timeout: int = 3600) -> None:
    if image_present(image):
        return
    proc = _run(["docker", "pull", "-q", image], timeout=timeout)
    if proc.returncode != 0:
        raise HarnessError(f"pull failed for {image}: {proc.stderr.strip()[:400]}")


def remove_image(image: str) -> None:
    """Called after every instance. Pro images share zero layers (verified:
    two ansible instances, 12 and 15 layers, intersection empty), so keeping
    them accumulates ~1GB each and this machine has 36GB free."""
    _run(["docker", "rmi", "-f", image])


def image_env(image: str) -> list[str]:
    proc = _run(["docker", "image", "inspect", image, "--format", "{{json .Config.Env}}"])
    if proc.returncode != 0:
        raise HarnessError(f"inspect failed for {image}")
    return json.loads(proc.stdout.strip() or "[]")


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def strip_binary_hunks(patch: str) -> str:
    """Upstream's helper: binary hunks can't be `git apply`-ed from a plain
    diff and abort the whole patch if left in."""
    if not patch:
        return patch
    kept = []
    for section in re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE):
        if not section.strip():
            continue
        if re.search(r"^Binary files .* differ$", section, re.MULTILINE):
            continue
        if re.search(r"^GIT binary patch$", section, re.MULTILINE):
            continue
        kept.append(section)
    return "".join(kept)


def build_entryscript(sample: dict, env: list[str]) -> str:
    """
    Upstream's entryscript with the patch-apply outcome recorded explicitly.

    Upstream lets `git apply` fail silently into a run where every expected
    test is simply absent, which grades identically to "the fix was wrong".
    Those are different facts: a malformed diff says nothing about whether
    the model understood the bug, and counting it as a wrong answer biases
    against whichever arm happens to emit messier diffs. Writing the outcome
    to apply_status keeps them separable at analysis time.
    """
    exports = "\n".join(f"export {shell_quote_env(e)}" for e in env)
    before = str(sample["before_repo_set_cmd"]).strip().split("\n")[-1]
    tests = ",".join(pylist(sample["selected_test_files_to_run"]))
    return f"""\
{exports}
cd /app
git reset --hard {sample['base_commit']} > /workspace/setup.log 2>&1
git checkout {sample['base_commit']} >> /workspace/setup.log 2>&1

if [ -s /workspace/patch.diff ]; then
  if git apply -v /workspace/patch.diff >> /workspace/setup.log 2>&1; then
    echo applied > /workspace/apply_status
  else
    echo failed > /workspace/apply_status
  fi
else
  echo empty > /workspace/apply_status
fi

{before} >> /workspace/setup.log 2>&1

bash /workspace/run_script.sh '{tests}' > /workspace/stdout.log 2> /workspace/stderr.log
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log \
    /workspace/output.json >> /workspace/setup.log 2>&1
"""


def shell_quote_env(entry: str) -> str:
    """`KEY=value with spaces` -> `KEY='value with spaces'`. PYTEST_ADDOPTS
    in these images is `--tb=short -v --continue-on-collection-errors
    --reruns=3`; unquoted, the shell splits it and the export silently
    exports only the first word."""
    key, _, value = entry.partition("=")
    return f"{key}='{value}'"


def _mount_path(path: str) -> str:
    """Docker Desktop accepts C:/x/y but not C:\\x\\y in -v."""
    return os.path.abspath(path).replace("\\", "/")


def evaluate(
    sample: dict,
    patch: str,
    scripts_dir: str,
    workspace: str,
    timeout: int = DEFAULT_TIMEOUT,
    block_network: bool = True,
    keep_image: bool = False,
) -> EvalResult:
    """One instance + one candidate patch -> a graded verdict from real tests."""
    iid = sample["instance_id"]
    image = image_for(sample)
    src = os.path.join(scripts_dir, iid)
    if not os.path.isdir(src):
        raise HarnessError(f"no run_scripts/{iid} -- instance not in the OS harness")

    if os.path.isdir(workspace):
<<<<<<< HEAD
        shutil.rmtree(workspace, ignore_errors=True)
=======
        safe_rmtree(workspace)  # was ignore_errors=True -- a real disk-exhaustion
                                  # incident (OSError, no space left on device)
                                  # happened mid-run with zero prior signal that
                                  # cleanup might be failing silently somewhere
>>>>>>> 9f329aa6c2de1314c7a0c1690dd82ec5b50d7123
    os.makedirs(workspace, exist_ok=True)

    pull_image(image)
    try:
        env = image_env(image)
        _write(os.path.join(workspace, "patch.diff"), strip_binary_hunks(patch or ""))
        _write(os.path.join(workspace, "run_script.sh"),
               _read(os.path.join(src, "run_script.sh")))
        _write(os.path.join(workspace, "parser.py"),
               _read(os.path.join(src, "parser.py")))
        _write(os.path.join(workspace, "entryscript.sh"), build_entryscript(sample, env))

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{_mount_path(workspace)}:/workspace",
            "--memory", DEFAULT_MEMORY, "--cpus", DEFAULT_CPUS,
            "--pids-limit", "2048",
            "--entrypoint", "/bin/bash",
        ]
        if block_network:
            cmd += ["--network", "none"]
        cmd += [image, "-c", "bash /workspace/entryscript.sh"]

        timed_out = False
        try:
            proc = _run(cmd, timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out, code = True, -1

        return _grade(sample, workspace, code, timed_out)
    finally:
        if not keep_image:
            remove_image(image)


def _grade(sample: dict, workspace: str, exit_code: int, timed_out: bool) -> EvalResult:
    """
    Upstream's rule: resolved iff (F2P | P2P) is a subset of the tests that
    PASSED. A test that never appeared in the output counts against
    resolution -- absence is not evidence the patch worked, and treating it
    as a pass would manufacture successes in exactly the place the ground
    truth has to be trustworthy.
    """
    iid = sample["instance_id"]
    apply_status = _read(os.path.join(workspace, "apply_status")).strip()
    f2p = set(pylist(sample["fail_to_pass"]))
    p2p = set(pylist(sample["pass_to_pass"]))

    raw = _read(os.path.join(workspace, "output.json"))
    if not raw:
        status = "timeout" if timed_out else (
            "patch_failed" if apply_status == "failed" else "no_output"
        )
        return EvalResult(iid, False, status, [], sorted(f2p), [], 0,
                          apply_status, exit_code,
                          error="parser produced no output.json")

    try:
        passed = {t["name"] for t in json.loads(raw)["tests"] if t["status"] == "PASSED"}
        n_parsed = len(json.loads(raw)["tests"])
    except (json.JSONDecodeError, KeyError) as exc:
        return EvalResult(iid, False, "no_output", [], sorted(f2p), [], 0,
                          apply_status, exit_code, error=f"bad output.json: {exc}")

    f2p_ok = sorted(f2p & passed)
    f2p_bad = sorted(f2p - passed)
    p2p_bad = sorted(p2p - passed)
    resolved = not f2p_bad and not p2p_bad

    if resolved:
        status = "resolved"
    elif apply_status == "failed":
        status = "patch_failed"
    elif f2p_bad:
        status = "f2p_failed"
    else:
        status = "p2p_broke"

    return EvalResult(iid, resolved, status, f2p_ok, f2p_bad, p2p_bad,
                      n_parsed, apply_status, exit_code)
