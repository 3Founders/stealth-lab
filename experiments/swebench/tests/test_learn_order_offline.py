"""Offline: per-repo extraction keeps, within every repo, exactly the sequential run's order."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import learn  # noqa: E402


def test_group_by_repo_preserves_sequential_order_within_repo():
    inst = {"b__b-2": {"repo": "b/b"}, "a__a-10": {"repo": "a/a"}, "a__a-2": {"repo": "a/a"}, "b__b-1": {"repo": "b/b"}}
    groups = learn.group_by_repo(inst.keys(), inst)
    sequential = sorted(inst)
    for repo, ids in groups.items():
        assert ids == [i for i in sequential if inst[i]["repo"] == repo]
    assert sorted(i for ids in groups.values() for i in ids) == sequential
