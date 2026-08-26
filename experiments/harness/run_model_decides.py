"""
CLI for the model-decides stale-procedure tier (board Lane MEASURE CLAUDE.md
Task 2). Two jobs, mirroring run_error_floor.py's split between collection
and grading:

  1. --validate-only: fixture-contract check (model_decides.
     validate_model_decides_fixtures), no network, no key. Run before any
     live sweep.
  2. --results PATH: analyze an existing real-arms sweep JSONL (produced by
     run_real_arms.py --fixtures-dir fixtures/model_decides, which needs no
     changes of its own - this tier is entirely a fixture-content addition,
     design §2) and print the sensitivity/specificity report.

The live sweep itself is just:
    python run_real_arms.py --fixtures-dir fixtures/model_decides \\
        --out model_decides_results.jsonl --auto-resume
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import model_decides  # noqa: E402
import scoring  # noqa: E402

DEFAULT_FIXTURES_DIR = HERE / "fixtures" / "model_decides"


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures-dir", default=str(DEFAULT_FIXTURES_DIR))
    ap.add_argument("--results", default=None,
                    help="real-arms sweep JSONL to analyze")
    ap.add_argument("--validate-only", action="store_true",
                    help="just run the fixture-contract check and exit")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    fixtures = Path(args.fixtures_dir)

    problems = model_decides.validate_model_decides_fixtures(fixtures)
    if problems:
        print("MODEL-DECIDES TIER REFUSES TO RUN - fixture validation failed:")
        for p in problems:
            print(f"  - {p}")
        return 2
    print(f"fixtures ok: {fixtures}")

    if args.validate_only:
        return 0
    if not args.results:
        print("no --results given; nothing to analyze "
              "(pass --results <sweep.jsonl>, or --validate-only)")
        return 0

    tasks = model_decides.load_tasks(fixtures)
    procedures_by_id = scoring.load_procedures(fixtures)
    rows = model_decides.load_rows(args.results)
    report = model_decides.analyze(rows, tasks, procedures_by_id)
    print(model_decides.render_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
