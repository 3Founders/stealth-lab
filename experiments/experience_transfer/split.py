"""Grouped, domain-stratified k-fold split of AutomationBench tasks.

Tasks that could leak into each other always share a fold: they are unioned into one group
when they share a named fixture (a spreadsheet / document / policy title in initial_state)
or their task names are near-duplicates (token Jaccard >= 0.5, e.g. hr.visa_expiry_tracking
vs hr.visa_expiration_monitoring). Groups are then assigned to folds greedily (largest
first, to the smallest fold) per domain, so every task is held out exactly once.

    python split.py --domains finance,hr --folds 2 --out split_finance_hr.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "AutomationBench"))

_TITLE_KEYS = ("title", "name", "spreadsheet_name", "file_name", "filename", "subject")
_GENERIC = {"inbox", "sent", "general", "default", "sheet1", "main", "notes"}


def _fixtures(state, out: set[str]) -> set[str]:
    """Named things in initial_state that another task could also use (sheets, docs, policies)."""
    if isinstance(state, dict):
        for k, v in state.items():
            if k in _TITLE_KEYS and isinstance(v, str) and 4 <= len(v) <= 80 and v.lower() not in _GENERIC:
                if any(w in v.lower() for w in ("sheet", "policy", "rates", "directory", "tracker", "log", "sop",
                                                "register", "template", "schedule", "handbook", "matrix", "list")):
                    out.add(v.strip().lower())
            _fixtures(v, out)
    elif isinstance(state, list):
        for v in state:
            _fixtures(v, out)
    return out


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[._\-]+", name.lower())[1:] if len(t) > 2}


def build_split(domains: list[str], folds: int) -> dict:
    from automationbench.domains import get_domain_dataset

    tasks = []
    for d in domains:
        for row in get_domain_dataset(d):
            info = json.loads(row["info"]) if isinstance(row["info"], str) else row["info"]
            tasks.append({"name": info["task_name"], "domain": d, "fixtures": _fixtures(info.get("initial_state"), set())})

    parent = list(range(len(tasks)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(tasks)):
        for j in range(i + 1, len(tasks)):
            a, b = tasks[i], tasks[j]
            ta, tb = _tokens(a["name"]), _tokens(b["name"])
            near_dup = ta and tb and len(ta & tb) / len(ta | tb) >= 0.5
            if a["fixtures"] & b["fixtures"] or near_dup:
                parent[find(i)] = find(j)

    groups: dict[int, list[dict]] = {}
    for i, t in enumerate(tasks):
        groups.setdefault(find(i), []).append(t)

    assignment: dict[str, int] = {}
    sizes = [0] * folds
    for g in sorted(groups.values(), key=lambda g: (-len(g), g[0]["name"])):   # deterministic
        f = min(range(folds), key=lambda k: (sizes[k], k))
        for t in g:
            assignment[t["name"]] = f
        sizes[f] += len(g)

    fold_tasks = [[n for n, f in sorted(assignment.items()) if f == k] for k in range(folds)]
    return {
        "domains": domains, "folds": folds, "fold_tasks": fold_tasks,
        "groups": [[t["name"] for t in g] for g in groups.values() if len(g) > 1],
        "fold_sizes": sizes,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", default="finance,hr")
    ap.add_argument("--folds", type=int, default=2)
    ap.add_argument("--out", default="split_finance_hr.json")
    a = ap.parse_args()
    s = build_split(a.domains.split(","), a.folds)
    Path(a.out).write_text(json.dumps(s, indent=1))
    print(f"fold sizes {s['fold_sizes']}, linked groups {len(s['groups'])} "
          f"(largest {max((len(g) for g in s['groups']), default=0)})")
