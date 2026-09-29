"""Which trajectories of the corpus become items: one per (task, outcome).

The corpus holds 67,074 runs of 6,306 tasks; a resolved task has a median of 8 successful runs (up to 34). Kel
needs one way per task, not 8 copies of it (plan: "keep the best resolved trajectory per task, plus at most one
failed trajectory for failure claims").

  resolved item  -- among runs with resolved == 1: the fewest messages (the most direct successful path; fewer
                    detours means cleaner steps), then a run that ended with the agent's own `submit`, then the
                    smallest trajectory_id. Deterministic, so a resumed run picks the same one.
  failed item    -- among runs with resolved == 0 AND exit_status == "submit": the agent finished and believed it
                    had solved the task, and the tests said no. That is a real wrong approach worth a failure Claim.
                    Runs that crashed, timed out or hit the turn cap are infrastructure or budget endings, not a
                    wrong approach, and are never selected. Tie-break as above.

Selection needs every run's message count, which lives in the large `trajectory` column; the list lengths are read
with pyarrow without materialising the messages. The result is cached per revision (it is a pure function of the
pinned file).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

RESOLVED, FAILED = "resolved", "failed"


@dataclass(frozen=True)
class Selected:
    instance_id: str
    repo: str
    trajectory_id: str
    outcome: str            # resolved | failed
    row_group: int
    row_index: int          # index within the row group
    messages: int
    exit_status: str
    runs_total: int = 0        # every recorded run of this task in the corpus
    runs_resolved: int = 0     # how many of them passed the task's tests

    @property
    def item_key(self) -> str:
        return f"{self.instance_id}:{self.outcome}"

    @property
    def dedup_key(self) -> str:
        # identity across trajectory corpora: one written run per task and outcome
        return f"swe-task:{self.instance_id}:{self.outcome}"


def pick(runs: Iterable[dict[str, Any]]) -> list[Selected]:
    """Pure: runs are dicts with instance_id, repo, trajectory_id, resolved, exit_status, messages, row_group,
    row_index. Returns items sorted by (instance_id, outcome)."""
    by_task: dict[str, list[dict]] = {}
    for run in runs:
        by_task.setdefault(str(run["instance_id"]), []).append(run)

    def key(run: dict) -> tuple:
        return (int(run["messages"]), 0 if run.get("exit_status") == "submit" else 1, str(run["trajectory_id"]))

    out: list[Selected] = []
    for instance_id in sorted(by_task):
        runs_of = by_task[instance_id]
        good = [r for r in runs_of if int(r["resolved"] or 0) == 1]
        bad = [r for r in runs_of if int(r["resolved"] or 0) == 0 and r.get("exit_status") == "submit"]
        for outcome, pool in ((RESOLVED, good), (FAILED, bad)):
            if pool:
                best = min(pool, key=key)
                out.append(Selected(instance_id, str(best["repo"]), str(best["trajectory_id"]), outcome,
                                    int(best["row_group"]), int(best["row_index"]), int(best["messages"]),
                                    str(best.get("exit_status") or ""), len(runs_of), len(good)))
    return out


def scan(path: Path) -> list[dict[str, Any]]:
    """Every run's selection fields, including its message count. Blocking."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    handle = pq.ParquetFile(str(path))
    runs: list[dict[str, Any]] = []
    for rg in range(handle.num_row_groups):
        table = handle.read_row_group(rg, columns=["trajectory_id", "instance_id", "repo", "resolved",
                                                   "exit_status", "trajectory"])
        lengths = pc.list_value_length(table.column("trajectory")).to_pylist()
        light = table.drop_columns(["trajectory"]).to_pylist()
        for i, (row, n) in enumerate(zip(light, lengths)):
            runs.append({**row, "messages": int(n or 0), "row_group": rg, "row_index": i})
    return runs


def selection(path: Path, *, revision: str, cache_dir: Path) -> list[Selected]:
    """The cached selection for this pinned file, computed on first use. Blocking."""
    cache = cache_dir / f"openhands_selection_v2@{revision}.json"   # v2: run counts per task
    if cache.exists():
        return [Selected(**d) for d in json.loads(cache.read_text(encoding="utf-8"))]
    picked = pick(scan(path))
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(s) for s in picked]), encoding="utf-8")
    tmp.replace(cache)
    return picked


def read_row(path: Path, item: Selected) -> dict[str, Any]:
    """The full row of one selected run, and a check that it is the run selection chose. Blocking."""
    import pyarrow.parquet as pq

    table = pq.ParquetFile(str(path)).read_row_group(item.row_group)
    row = table.slice(item.row_index, 1).to_pylist()[0]
    if row.get("trajectory_id") != item.trajectory_id:
        raise RuntimeError(f"selection cache does not match the pinned file at {item.row_group}/{item.row_index}")
    return row
