"""Load and check the comparison task corpus (scripts/compare/tasks/*.jsonl, docs/goal_router.md P2-01).

    python -m scripts.compare.corpus --check           # validate every task; exit 1 on any problem
    python -m scripts.compare.corpus --list --goal web

One task per line. A task is rejected, by id, when it has no verifier, an unknown verifier kind, a missing verifier
key, a goal outside the corpus goals, a non-public data class, a non-positive budget, or an id already used.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from scripts.compare.contracts import DATA_CLASSES, GOALS, VERIFIERS, Task

TASKS_DIR = Path(__file__).resolve().parent / "tasks"
EXPECT_TASKS = 60
EXPECT_GOALS = 6


class CorpusError(ValueError):
    pass


def problems(raw: dict) -> list[str]:
    out: list[str] = []
    for key in ("id", "prompt", "goal_hint"):
        if not str(raw.get(key) or "").strip():
            out.append(f"missing {key}")
    if raw.get("goal_hint") and raw["goal_hint"] not in GOALS:
        out.append(f"goal_hint {raw['goal_hint']!r} is not one of {', '.join(GOALS)}")
    verifier = raw.get("verifier")
    if not isinstance(verifier, dict) or not verifier.get("kind"):
        out.append("no verifier")
    elif verifier["kind"] not in VERIFIERS:
        out.append(f"unknown verifier kind {verifier['kind']!r}")
    else:
        missing = [k for k in VERIFIERS[verifier["kind"]] if k not in verifier]
        if missing:
            out.append(f"verifier {verifier['kind']} needs {', '.join(missing)}")
        elif verifier["kind"] == "sql_result":
            try:   # the fixture must build, so a task cannot fail for a reason that is not the answer
                db = sqlite3.connect(":memory:")
                db.executescript(verifier["fixture_sql"])
                db.close()
            except sqlite3.Error as exc:
                out.append(f"fixture_sql does not run: {exc}")
    if raw.get("data_class", "PUBLIC_SOURCE") not in DATA_CLASSES:
        out.append(f"data_class {raw.get('data_class')!r} is not public")
    try:
        if float(raw.get("budget_usd", 0.05)) <= 0:
            out.append("budget_usd must be positive")
    except (TypeError, ValueError):
        out.append("budget_usd is not a number")
    return out


def read(paths: Optional[Iterable[Path]] = None) -> tuple[list[Task], list[str]]:
    """-> (valid tasks, error lines). Duplicate ids are errors; the first occurrence is kept."""
    tasks: list[Task] = []
    errors: list[str] = []
    seen: set[str] = set()
    for path in sorted(paths or TASKS_DIR.glob("*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path.name}:{n}"
            try:
                raw = json.loads(line)
            except ValueError as exc:
                errors.append(f"{where}: not JSON ({exc})")
                continue
            tid = str(raw.get("id") or f"<{where}>")
            found = problems(raw)
            if tid in seen:
                found.append("duplicate id")
            if found:
                errors.append(f"{tid} ({where}): " + "; ".join(found))
                continue
            seen.add(tid)
            fields = {k: raw[k] for k in Task.__dataclass_fields__ if k in raw}
            tasks.append(Task(**fields))
    return tasks, errors


def load(*, ids: Optional[Iterable[str]] = None, goal: Optional[str] = None) -> list[Task]:
    """Valid tasks, filtered; raises CorpusError if the corpus has any problem, so a run never starts on a bad one."""
    tasks, errors = read()
    if errors:
        raise CorpusError("corpus problems:\n  " + "\n  ".join(errors))
    want = {i.strip() for i in ids} if ids else None
    if want:
        unknown = want - {t.id for t in tasks}
        if unknown:
            raise CorpusError(f"unknown task ids: {', '.join(sorted(unknown))}")
    return [t for t in tasks if (want is None or t.id in want) and (goal is None or t.goal_hint == goal)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Check or list the comparison corpus.")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--goal", choices=GOALS)
    args = ap.parse_args()
    tasks, errors = read()
    if args.list:
        for t in tasks:
            if args.goal is None or t.goal_hint == args.goal:
                print(f"{t.id:<10} {t.goal_hint:<12} {t.verifier['kind']:<12} {t.prompt[:90]}")
    per_goal = Counter(t.goal_hint for t in tasks)
    kinds = Counter(t.verifier["kind"] for t in tasks)
    print(f"{len(tasks)} tasks, {len(per_goal)} goals ({', '.join(f'{g} {per_goal[g]}' for g in GOALS)}); "
          f"verifiers: {', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}; "
          f"holdout {sum(t.holdout for t in tasks)}")
    for e in errors:
        print(f"REJECTED {e}")
    if args.check:
        short = len(tasks) < EXPECT_TASKS or len(per_goal) < EXPECT_GOALS
        if errors or short:
            if short:
                print(f"FAILED: expected at least {EXPECT_TASKS} tasks across {EXPECT_GOALS} goals")
            sys.exit(1)
        print("ok: no duplicate ids, every task has a verifier")


if __name__ == "__main__":
    main()
