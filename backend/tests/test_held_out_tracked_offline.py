"""Offline: step 6's held-out loader uses the tracked list when a design is missing, and still fails closed with
neither (BLOCKERS.md I15)."""
from __future__ import annotations

import json

import pytest

from app.services.ingestion_sources.held_out import TRACKED_IDS, HeldOutUnavailable, load_held_out


def _tracked(root, splits=("test", "calibration")):
    path = root / TRACKED_IDS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"splits": list(splits), "sources": [{"design": "x", "sha256": "0"}],
                                "instance_ids": ["astropy__astropy-14096"], "scored_repos": ["django/django"]}),
                    encoding="utf-8")


def test_no_design_and_no_tracked_list_fails_closed(tmp_path):
    with pytest.raises(HeldOutUnavailable, match="tracked list"):
        load_held_out(tmp_path)


def test_tracked_list_replaces_missing_designs(tmp_path):
    _tracked(tmp_path)
    held = load_held_out(tmp_path)
    assert held.is_held_out("astropy__astropy-14096")
    assert held.is_held_out("django/django@abc")
    assert held.missing == ()
    assert held.snapshots and held.snapshots[0].path.endswith("held_out_ids.json")


def test_one_missing_design_uses_the_whole_tracked_list_not_a_mix(tmp_path):
    design = tmp_path / "experiments" / "swebench" / "runs" / "design.json"
    design.parent.mkdir(parents=True)
    design.write_text(json.dumps({"test": ["only-in-design"], "calibration": []}), encoding="utf-8")
    _tracked(tmp_path)
    held = load_held_out(tmp_path)
    assert held.is_held_out("astropy__astropy-14096"), "the tracked list, which covers every design"
    assert not held.is_held_out("only-in-design")


def test_tracked_list_missing_a_split_fails_closed(tmp_path):
    _tracked(tmp_path, splits=("test",))
    with pytest.raises(HeldOutUnavailable, match="splits"):
        load_held_out(tmp_path)
