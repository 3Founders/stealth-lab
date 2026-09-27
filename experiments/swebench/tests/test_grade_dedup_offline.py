"""Offline: grades are copied from A0 only for attempts generate.py reused from A0, with identical patches."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import grade  # noqa: E402
import swe_env  # noqa: E402


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(swe_env, "RUNS", tmp_path)
    _jsonl(tmp_path / "predictions_test_A0.jsonl", [
        {"instance_id": "a", "model_patch": "PATCH-A"}, {"instance_id": "b", "model_patch": "PATCH-B"},
        {"instance_id": "c", "model_patch": "PATCH-C"}, {"instance_id": "d", "model_patch": "PATCH-D"}])
    (tmp_path / "grades_test_A0.json").write_text(json.dumps({
        "a": {"resolved": True, "status": "resolved"}, "b": {"resolved": False, "status": "unresolved"},
        "c": {"resolved": False, "status": "error"}, "d": {"resolved": True, "status": "resolved"}}), encoding="utf-8")


def test_copies_only_reused_identical_and_real_verdicts(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _jsonl(tmp_path / "attempts_test_K.jsonl", [
        {"instance_id": "a", "reused_from": "A0"},                     # reused, identical -> copied
        {"instance_id": "b", "reused_from": "A0"},                     # reused, identical, unresolved -> copied
        {"instance_id": "c", "reused_from": "A0"},                     # A0 errored -> NOT copied (re-graded)
        {"instance_id": "d"},                                          # fresh attempt -> graded
        {"instance_id": "e", "reused_from": "A0", "environmental_failure": True}])
    preds = {"a": "PATCH-A", "b": "PATCH-B", "c": "PATCH-C", "d": "PATCH-D-NEW"}
    got = grade.reused_a0_grades("test_K", preds)
    assert set(got) == {"a", "b"}
    assert got["a"] == {"resolved": True, "status": "resolved", "copied_from": "test_A0"}
    assert got["b"]["resolved"] is False and got["b"]["copied_from"] == "test_A0"


def test_patch_mismatch_is_never_copied(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _jsonl(tmp_path / "attempts_test_E.jsonl", [{"instance_id": "a", "reused_from": "A0"}])
    assert grade.reused_a0_grades("test_E", {"a": "SOMETHING-ELSE"}) == {}


def test_fresh_arms_and_missing_a0_grades_copy_nothing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for arm in ("A0", "A0r", "KP"):
        _jsonl(tmp_path / f"attempts_test_{arm}.jsonl", [{"instance_id": "a", "reused_from": "A0"}])
        assert grade.reused_a0_grades(f"test_{arm}", {"a": "PATCH-A"}) == {}
    (tmp_path / "grades_test_A0.json").unlink()
    _jsonl(tmp_path / "attempts_test_K.jsonl", [{"instance_id": "a", "reused_from": "A0"}])
    assert grade.reused_a0_grades("test_K", {"a": "PATCH-A"}) == {}
