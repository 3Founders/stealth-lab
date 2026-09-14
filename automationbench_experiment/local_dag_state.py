"""
Local-first execution state, per the agreed design: Postgres is a source
of truth (durable Procedures/Claims/Evidence, the historical record of
past Executions) but NOT the live source during a session -- the live
state of a run in progress lives here, in a plain file, read/written
directly with zero Postgres round-trips per step.

Modeled on app/stealth/exploration.py's shape (a local journal that only
promotes something durable to Postgres at a natural completion point,
never mid-flight): `.stealth/run.md` is the live DAG -- node list, deps,
status -- and `flush_run()` (in local_dag_flush.py) is the one place that
ever writes the finished result to Postgres.

File format (plain, grep/edit-friendly):
    # run: <run_id>
    1. [ ] <goal>                deps=[]
    2. [ ] <goal>                deps=[1]
    3. [x] <goal>                deps=[1]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_LINE_RE = re.compile(r"^(\d+)\.\s*\[( |x|!)\]\s*(.*?)(?:\s+deps=\[([\d,\s]*)\])?\s*$")

STATUS_CHARS = {" ": "pending", "x": "succeeded", "!": "failed"}
CHAR_FOR_STATUS = {"pending": " ", "succeeded": "x", "failed": "!"}


@dataclass
class DagNode:
    order: int
    goal: str
    status: str  # "pending" | "succeeded" | "failed"
    deps: tuple[int, ...] = field(default_factory=tuple)


def create_run(path: Path, run_id: str, nodes: list[tuple[str, list[int]]]) -> None:
    """nodes: list of (goal, deps) in order; order is assigned 1..N here."""
    lines = [f"# run: {run_id}", ""]
    for i, (goal, deps) in enumerate(nodes, 1):
        dep_str = f"  deps=[{','.join(str(d) for d in deps)}]" if deps else ""
        lines.append(f"{i}. [ ] {goal}{dep_str}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_run(path: Path) -> list[DagNode]:
    nodes: list[DagNode] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        order, status_char, goal, deps_str = m.groups()
        deps = tuple(int(d) for d in deps_str.split(",") if d.strip()) if deps_str else ()
        nodes.append(DagNode(order=int(order), goal=goal.strip(), status=STATUS_CHARS[status_char], deps=deps))
    return nodes


def mark_node(path: Path, order: int, status: str) -> None:
    """status: 'succeeded' or 'failed'. Raises ValueError if the node isn't pending."""
    if status not in ("succeeded", "failed"):
        raise ValueError(f"status must be 'succeeded' or 'failed', got {status!r}")
    lines = path.read_text(encoding="utf-8").splitlines()
    char = CHAR_FOR_STATUS[status]
    for i, line in enumerate(lines):
        m = _LINE_RE.match(line.strip())
        if m and int(m.group(1)) == order:
            if m.group(2) != " ":
                raise ValueError(f"node {order} is not pending (already {STATUS_CHARS[m.group(2)]!r})")
            lines[i] = re.sub(rf"^{order}\.\s*\[ \]", f"{order}. [{char}]", line, count=1)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    raise ValueError(f"node {order} not found in {path}")


def next_actionable_node(path: Path) -> Optional[DagNode]:
    """First pending node whose every dep has already succeeded. None if
    nothing is currently actionable (either done, or blocked on a failed dep)."""
    nodes = read_run(path)
    by_order = {n.order: n for n in nodes}
    for n in nodes:
        if n.status != "pending":
            continue
        if all(by_order[d].status == "succeeded" for d in n.deps if d in by_order):
            return n
    return None


def is_complete(path: Path) -> bool:
    return all(n.status != "pending" for n in read_run(path))
