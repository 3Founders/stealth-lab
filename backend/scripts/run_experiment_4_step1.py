"""
Experiment 4, Step 1: before any model is involved, prove the harness
conventions themselves are understood correctly -- fetch real task
files for pm/edit-pdf, set up the exact directory layout data_generator.py
/ solve.sh / solution.py / test_outputs.py expect, run the REFERENCE
solution (deterministic Python, no model), and confirm test_outputs.py
passes.

This deliberately does NOT involve any LLM yet. solution.py is not a
model output -- it's regex + PyMuPDF calls at fixed coordinates. Running
it only proves the plumbing (input paths, output path, working
directories, how the test file is invoked) is right, which Step 2 (an
actual code-generating agent) will depend on getting exactly correct.
Same "prove infrastructure works on a known-correct case first"
discipline as every other experiment this project has run.

Real directory layout, confirmed from the actual repo file listing --
not guessed:

  TASK_ROOT/
    task.toml, instruction.md, solve.sh, data_generator.py, solution.py
    source_artifacts/source_task/inputs/{input.pdf, input.txt}   <- real inputs live here
    tests/test_outputs.py
    environment/data/                                             <- data_generator.py populates this
    output/output.pdf                                             <- solution.py writes here

Requires: pip install PyMuPDF pypdf pytest --break-system-packages
(fitz is solution.py's dependency; pypdf is test_outputs.py's)

Run from backend/ (or anywhere -- writes into ./after_task_root/):
    python scripts/run_experiment_4_step1.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO = "DavydenkoGr/AFTER"
TASK = "tasks/pm/edit-pdf"
TASK_ROOT = Path("after_task_root")


def fetch(remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cached = hf_hub_download(REPO, filename=remote_path, repo_type="dataset")
    shutil.copy2(cached, local_path)


def main() -> int:
    if TASK_ROOT.exists():
        print(f"{TASK_ROOT} already exists -- removing for a clean run")
        shutil.rmtree(TASK_ROOT)
    TASK_ROOT.mkdir(parents=True)

    print("[1/5] fetching real task files...")
    files = {
        f"{TASK}/task.toml": TASK_ROOT / "task.toml",
        f"{TASK}/instruction.md": TASK_ROOT / "instruction.md",
        f"{TASK}/data_generator.py": TASK_ROOT / "data_generator.py",
        f"{TASK}/solution.py": TASK_ROOT / "solution.py",
        f"{TASK}/tests/test_outputs.py": TASK_ROOT / "tests" / "test_outputs.py",
        f"{TASK}/source_artifacts/source_task/inputs/input.pdf":
            TASK_ROOT / "source_artifacts" / "source_task" / "inputs" / "input.pdf",
        f"{TASK}/source_artifacts/source_task/inputs/input.txt":
            TASK_ROOT / "source_artifacts" / "source_task" / "inputs" / "input.txt",
    }
    for remote, local in files.items():
        fetch(remote, local)
        print(f"  {remote} -> {local}")

    print("\n[2/5] running data_generator.py (populates environment/data/)...")
    result = subprocess.run(
        [sys.executable, "data_generator.py"], cwd=TASK_ROOT,
        capture_output=True, text=True,
    )
    print(result.stdout, result.stderr)
    if result.returncode != 0:
        print("data_generator.py FAILED -- stopping here")
        return 1
    data_files = sorted((TASK_ROOT / "environment" / "data").iterdir())
    print(f"  environment/data/ now contains: {[f.name for f in data_files]}")

    print("\n[3/5] running solution.py (the REFERENCE solution -- deterministic, no model)...")
    output_dir = TASK_ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    result = subprocess.run(
        [sys.executable, str((TASK_ROOT / "solution.py").resolve())],
        cwd=output_dir, capture_output=True, text=True,
    )
    print(result.stdout, result.stderr)
    if result.returncode != 0:
        print("solution.py FAILED -- stopping here")
        return 1
    if not (output_dir / "output.pdf").exists():
        print("solution.py ran but did not produce output/output.pdf -- stopping here")
        return 1
    print(f"  {output_dir / 'output.pdf'} created "
          f"({(output_dir / 'output.pdf').stat().st_size} bytes)")

    print("\n[4/5] running test_outputs.py against the reference solution's output...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_outputs.py", "-v"],
        cwd=TASK_ROOT, capture_output=True, text=True,
    )
    print(result.stdout, result.stderr)

    print("\n[5/5] RESULT")
    if result.returncode == 0:
        print("PASS -- harness conventions confirmed correct against a known-correct solution. "
              "Safe to build Step 2 (a real code-generating agent) on top of this layout.")
    else:
        print("FAIL -- the reference solution did not pass its own test. This means either the "
              "harness setup here has a real bug (wrong paths, wrong cwd, missing dependency), "
              "or something about this specific task/environment differs from what was assumed. "
              "Do NOT proceed to Step 2 until this passes -- an agent's real failures would be "
              "indistinguishable from a broken harness otherwise.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
