"""
LIVE test: given the instance's own real gold patch as a SEARCH/REPLACE
precedent (exactly how the real htn_memory arm renders a retrieved fix),
can the real model correctly apply it and land a passing patch -- graded
by the instance's own real FAIL_TO_PASS test?

Every other HTN test (test_htn_agent.py, test_htn_node_telemetry.py,
test_htn_real_swebench_instances.py) is scripted: a FakeClient replays
canned responses, so nothing there proves the MODEL can actually plan and
edit its way to a fix, only that the agent's plumbing is correct when it
does. This file is the one place that removes the FakeClient and asks a
real question, end to end:

    real problem statement + real gold patch as memory_block
    -> real gemma-4-31B-it planner/executor calls -> real repo checkout
    -> real edit_file calls -> real gold test suite

This is deliberately NOT "can it solve this cold" (see
test_htn_real_swebench_instances.py's synthetic-snippet tests for the
scripted version of that question, and this file's own git history for an
earlier draft that tried to ask it for real). Handing the model its own
answer, framed as a precedent, narrows the question to something more
specific and just as real: can the apply-precedent path -- the mechanism
htn_memory actually depends on in production -- correctly recognize a
directly-applicable fix and land it.

Design decisions locked in with the user before writing this (2026-08-16):
  - model: gemma-4-31B-it, via the same general_compute client
    run_graph_experiment.py itself constructs (OpenAI(max_retries=0,
    api_key=settings.general_compute_api_key,
    base_url=settings.general_compute_base_url)) -- REAL, BILLED calls.
  - repo: a real git checkout at the instance's base_commit (shallow
    fetch of that one commit, not the full history) -- NOT a synthetic
    snippet, because grading needs the real FAIL_TO_PASS test to actually
    run, which needs the real importable `sympy` package. Cached under
    ~/.cache/stealthlab_htn_live_tests/<instance_id> and reset to a clean
    base_commit before each run via `git clean -fdx` -- the clone itself
    (and its .git objects) persists across runs so a repeat run is a
    near-instant fetch/checkout, not a fresh clone.
  - grading: the instance's REAL FAIL_TO_PASS test (test_PythonCodePrinter
    in sympy/printing/tests/test_pycode.py), run with the real test_patch
    applied AFTER the agent finishes (mirrors the actual SWE-bench
    protocol: the model never sees the test that grades it).
  - instance: sympy__sympy-22914 -- pure-Python, no C build step, no pip
    install needed (sympy imports straight from a checkout on sys.path),
    which is what keeps this fast even with a real repo.
  - budget: max_steps=72 (this session's real experiment default),
    replans allowed -- a genuine chance to solve it, not a single-shot
    trick question.
  - agent: HTN only (AugmentedHTNAgent). The candidate_files pre-pass
    hint is included automatically, since it's produced internally by
    `_decompose` whenever a real sandbox is passed in.
  - memory: the instance's OWN real gold source patch, rendered through
    the actual production `graph_memory.render_context(include_patches=
    True)` -- the same function and the same SEARCH/REPLACE format
    (`patch_format.diff_to_search_replace`) the real htn_memory arm
    renders a retrieved precedent's fix into. This deliberately shows the
    model its own answer, framed as a prior-resolved-issue precedent, so
    what's under test is narrower and different from "can it solve this
    cold": can the agent correctly recognize a directly-applicable
    precedent and land it via real edit_file calls, matching the real
    apply-precedent execution path htn_memory depends on. No DB
    involved -- GraphHit is constructed directly from the same dataset
    row the problem_statement comes from, not retrieved.
  - gating: OPT-IN ONLY, never runs as part of `pytest tests`. Skips
    itself unless RUN_LIVE_LLM_TESTS=1 is set. Run it explicitly:

        RUN_LIVE_LLM_TESTS=1 python -m pytest \
            backend/tests/test_htn_real_llm_solve.py -v -s

    (`-s` so the real run's stdout -- tool calls, replans -- streams live
    rather than being buffered until the end.)

This is NOT a proof the HTN agent "works" in general -- it is one real
data point on one real small instance. Treat a pass as "didn't fail this
one case", and a fail as equally informative: a real miss on a real small
single-file bug is worth knowing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro"))

from agent import RepoSandbox  # noqa: E402
from graph_memory import GraphHit, render_context  # noqa: E402
from htn_agent import AugmentedHTNAgent  # noqa: E402

if not os.environ.get("RUN_LIVE_LLM_TESTS"):
    pytest.skip(
        "live LLM test -- set RUN_LIVE_LLM_TESTS=1 to run it "
        "(real, billed model calls against a real repo checkout)",
        allow_module_level=True,
    )

from app.config import settings  # noqa: E402

INSTANCE_ID = "sympy__sympy-22914"
REPO = "sympy/sympy"
BASE_COMMIT = "c4e836cdf73fc6aa7bab6a86719a0f08861ffb1d"
FAIL_TO_PASS = "test_PythonCodePrinter"
TEST_FILE = "sympy/printing/tests/test_pycode.py"

# Deliberately NOT a frozen string literal. Two separate hand-transcription
# bugs already came out of trying to freeze real dataset text by hand in
# this file (the test_patch's unified-diff whitespace, then this field's
# CRLF line endings and trailing space) -- freezing invites exactly that
# class of silent drift from the real field. This test already needs
# network access for the git clone, so loading the real problem_statement
# from the dataset at fixture time costs nothing extra and removes
# transcription risk entirely: whatever the real field says is what the
# model sees, byte for byte, guaranteed.
#
# Note for anyone reading this file without running it: the real text
# already contains a near-complete solution as pseudocode (two
# _print_Min/_print_Max method bodies) -- that is a genuine property of
# THIS real issue, not something this fixture adds. The real SWE-bench
# harness hands the model this same text. It describes a DIFFERENT valid
# fix than the gold patch's `_known_functions` dict approach, which is
# exactly why grading runs the real FAIL_TO_PASS test rather than
# diff-comparing against the gold patch -- a model solving it via the
# hinted approach should legitimately pass.


@pytest.fixture(scope="module")
def real_row() -> dict:
    """The full SWE-bench Verified dataset row -- loaded once, byte-exact,
    no hand-transcription. `problem_statement` and `gold_memory_block`
    both derive from this single load."""
    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    row = next((r for r in ds if r["instance_id"] == INSTANCE_ID), None)
    assert row is not None, f"{INSTANCE_ID} not found in SWE-bench Verified"
    return row


@pytest.fixture(scope="module")
def problem_statement(real_row) -> str:
    return real_row["problem_statement"]


@pytest.fixture(scope="module")
def gold_memory_block(real_row) -> str:
    """
    The instance's own real gold patch AND real test_patch -- BOTH, per
    the user's follow-up ("give the test as well") -- concatenated and
    rendered through the actual production graph_memory.render_context
    (include_patches=True): the same function, same header text, same
    SEARCH/REPLACE conversion (patch_format.diff_to_search_replace) the
    real htn_memory arm uses to show the planner a retrieved precedent's
    fix. diff_to_search_replace parses per `diff --git` marker, so two
    concatenated file diffs become two separate SEARCH/REPLACE blocks
    (source file, then test file) in one precedent -- exactly what the
    real GitHub commit for this instance actually contains.

    Framed here as a prior-resolved-issue precedent for THIS SAME
    instance, deliberately -- per the user's request that the gold patch
    be given as real SEARCH/REPLACE ("aider") blocks. This is honest
    about what it changes: it no longer tests whether the model can solve
    the bug cold, only whether it can recognize a directly-applicable
    precedent and land it via real edit_file calls -- the apply-precedent
    path the real htn_memory arm depends on just as much as raw solving.

    Grading is UNCHANGED by this: `_apply_test_patch` still runs after
    the agent finishes, applying the real test_patch to whatever the
    checkout has at that point regardless of whether the model already
    touched the test file itself -- see its own idempotency note for why
    that matters now that the model can see the test precedent too.
    """
    combined_patch = real_row["patch"] + "\n" + real_row["test_patch"]
    hit = GraphHit(
        instance_id=real_row["instance_id"], title=real_row["problem_statement"].splitlines()[0],
        repo=real_row["repo"], language="python", files=[],
        patch=combined_patch,
    )
    # patch_chars default (1400) is sized for a single precedent's source
    # fix in the real pipeline; packing source + test into one precedent
    # here is bigger by construction, and truncating the test half would
    # defeat the point of showing it at all. 4000 comfortably covers this
    # instance's combined SEARCH/REPLACE body (~1.6k chars) with headroom.
    return render_context([hit], include_patches=True, minimal=True, patch_chars=4000)

# Brings in the real test_patch's assertions so the real FAIL_TO_PASS test
# can run. The model now sees this SAME change as part of gold_memory_block
# (the user asked for the test to be given too), so it may legitimately
# have already applied it itself while solving -- unlike the earlier
# source-only version of this fixture, that must now be treated as
# success, not a mismatch. As plain text substitutions rather than a
# hand-typed unified diff: a diff's blank CONTEXT lines need a literal
# single-space prefix, which is exactly the kind of invisible whitespace a
# string literal (or an editor) silently drops; `git apply` then fails
# with "corrupt patch" pointing at a line that looks completely
# unremarkable in the source. Plain substitution has no such failure mode.
_IMPORT_OLD = ("from sympy.functions import acos, KroneckerDelta, "
              "Piecewise, sign, sqrt\n")
_IMPORT_NEW = ("from sympy.functions import acos, KroneckerDelta, "
              "Piecewise, sign, sqrt, Min, Max\n")
_ASSERTS_OLD = '    assert prntr.doprint([2,3]) == "[2, 3]"\n'
_ASSERTS_NEW = (
    '    assert prntr.doprint([2,3]) == "[2, 3]"\n'
    '\n'
    '    assert prntr.doprint(Min(x, y)) == "min(x, y)"\n'
    '    assert prntr.doprint(Max(x, y)) == "max(x, y)"\n'
)


def _apply_test_patch(repo_root: Path) -> None:
    """
    Idempotent: checks whether each change is ALREADY present (the model
    may have applied it itself, now that it can see the test precedent)
    before falling back to the anchor-based substitution, and only
    asserts when neither the old nor the new text is found -- the actual
    "something is wrong with this fixture" case, as opposed to "the model
    got there first".
    """
    path = repo_root / TEST_FILE
    src = path.read_text(encoding="utf-8")

    if _IMPORT_NEW not in src:
        assert _IMPORT_OLD in src, (
            "fixture/base_commit mismatch: neither the old nor the new "
            "import line was found")
        src = src.replace(_IMPORT_OLD, _IMPORT_NEW, 1)

    if _ASSERTS_NEW not in src:
        assert _ASSERTS_OLD in src, (
            "fixture/base_commit mismatch: neither the assert anchor nor "
            "the new assertions were found")
        src = src.replace(_ASSERTS_OLD, _ASSERTS_NEW, 1)

    path.write_text(src, encoding="utf-8")

CACHE_ROOT = Path(os.path.expanduser("~")) / ".cache" / "stealthlab_htn_live_tests"


def _run(cmd: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          timeout=120, **kw)


@pytest.fixture(scope="module")
def real_sympy_checkout() -> Path:
    """
    A real sympy checkout at BASE_COMMIT, reused across runs.

    Shallow fetch of exactly one commit (not the whole history) -- the
    first run pays for that fetch, every later run against the SAME
    commit is git recognizing it already has the objects and doing
    almost nothing. Reset to pristine BASE_COMMIT before handing it back,
    so a previous run's agent edits never leak into the next one -- this
    is destructive, but it is scoped entirely to this cache directory,
    never to the real StealthLab repo.

    `reset --hard`, NOT `checkout -f`: when FETCH_HEAD resolves to the
    SAME commit already checked out (every run after the first, since
    BASE_COMMIT never changes), `git checkout -f <ref-you're-already-on>`
    is a documented no-op for dirty TRACKED files -- it only switches
    refs, and there is no ref switch to make. Confirmed live: a prior
    run's real edits to sympy/printing/pycode.py silently survived this
    exact sequence and made a later dry-run pass for the wrong reason
    (it was grading yesterday's leftover fix, not a fresh one). `reset
    --hard` discards tracked-file modifications regardless of whether
    the ref actually moved; `clean -fdx` still runs after it for
    untracked files (e.g. scratch files the agent created via
    create_file), which `reset` alone does not touch.
    """
    repo_dir = CACHE_ROOT / INSTANCE_ID
    repo_dir.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").exists():
        _run(["git", "init"], repo_dir, check=True)
        _run(["git", "remote", "add", "origin", f"https://github.com/{REPO}.git"],
             repo_dir, check=True)
    fetch = _run(["git", "fetch", "--depth", "1", "origin", BASE_COMMIT], repo_dir)
    if fetch.returncode != 0:
        pytest.skip(f"could not fetch {REPO}@{BASE_COMMIT} (offline?): {fetch.stderr}")
    _run(["git", "reset", "--hard", "FETCH_HEAD"], repo_dir, check=True)
    _run(["git", "clean", "-fdx"], repo_dir, check=True)
    return repo_dir


@pytest.fixture(scope="module")
def real_client():
    from openai import OpenAI
    # Same construction run_graph_experiment.py itself uses -- see that
    # file's comment on why max_retries=0 (the SDK's own retry loop can
    # silently stall well past any timeout you pass it).
    return OpenAI(max_retries=0, api_key=settings.require("general_compute_api_key"),
                 base_url=settings.general_compute_base_url)


class TestRealModelSolvesRealInstance:
    def test_gemma_applies_gold_precedent_via_search_replace(
        self, real_sympy_checkout, real_client, problem_statement, gold_memory_block,
    ):
        sandbox = RepoSandbox(str(real_sympy_checkout))
        instance = {
            "instance_id": INSTANCE_ID, "repo": REPO,
            "problem_statement": problem_statement,
        }
        print(f"\nmemory_block given to the planner:\n{gold_memory_block}\n")

        run = AugmentedHTNAgent(real_client, "gemma-4-31B-it", max_steps=72).run(
            instance, sandbox, "htn_memory", memory_block=gold_memory_block)

        print(f"\nstop_reason={run.stop_reason} "
              f"files_edited={run.files_edited} "
              f"tokens={run.usage.total}")
        for n in run.htn["nodes"]:
            print(f"  node {n['id']} [{n['status']}] attempts={n['attempts']} "
                  f"goal={n['goal'][:100]!r} note={n['note'][:150]!r}")

        _apply_test_patch(real_sympy_checkout)

        result = _run([sys.executable, "-m", "pytest", TEST_FILE,
                       "-k", FAIL_TO_PASS, "-v"], real_sympy_checkout)
        print(result.stdout[-4000:])
        print(result.stderr[-2000:])

        assert result.returncode == 0, (
            f"real gold test {FAIL_TO_PASS} did not pass against the "
            f"model's patch. stop_reason={run.stop_reason} "
            f"files_edited={run.files_edited}\n\n{result.stdout[-3000:]}")
