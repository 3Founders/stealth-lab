"""
DB-free coverage for app.stealth.artifacts -- the real local
`.stealth/artifacts/` store (Prompt 2 Sec 5). Real local filesystem I/O
via `tmp_path`, same convention test_stealth_journal_offline.py already
establishes -- no DB, no network.
"""
from __future__ import annotations

import hashlib
import os

from app.stealth.artifacts import artifacts_dir, list_artifacts, read_artifact, write_execution_artifacts


def test_empty_output_files_writes_nothing(tmp_path):
    manifest = write_execution_artifacts(str(tmp_path), "G-1", "E-1", {})
    assert manifest == []
    assert not os.path.exists(artifacts_dir(str(tmp_path), "G-1", "E-1"))


def test_writes_real_files_to_disk_with_correct_content_and_manifest(tmp_path):
    ws = str(tmp_path)
    content = b"hello world"
    manifest = write_execution_artifacts(ws, "G-1", "E-1", {"out.txt": content})
    assert len(manifest) == 1
    entry = manifest[0]
    assert entry["filename"] == "out.txt"
    assert entry["size_bytes"] == len(content)
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    with open(entry["path"], "rb") as f:
        assert f.read() == content


def test_writes_multiple_files_preserving_each_real_content(tmp_path):
    ws = str(tmp_path)
    files = {"a.txt": b"AAA", "b.bin": b"\x00\x01\x02"}
    manifest = write_execution_artifacts(ws, "G-1", "E-1", files)
    assert len(manifest) == 2
    by_name = {m["filename"]: m for m in manifest}
    with open(by_name["a.txt"]["path"], "rb") as f:
        assert f.read() == b"AAA"
    with open(by_name["b.bin"]["path"], "rb") as f:
        assert f.read() == b"\x00\x01\x02"


def test_nested_relative_path_is_preserved_under_the_artifacts_dir(tmp_path):
    ws = str(tmp_path)
    manifest = write_execution_artifacts(ws, "G-1", "E-1", {"sub/dir/out.txt": b"x"})
    assert manifest[0]["path"].endswith(os.path.join("sub", "dir", "out.txt"))
    assert os.path.exists(manifest[0]["path"])


def test_path_escape_attempt_is_dropped_not_written_outside_the_tree(tmp_path):
    ws = str(tmp_path)
    manifest = write_execution_artifacts(ws, "G-1", "E-1", {"../../escape.txt": b"x", "safe.txt": b"y"})
    assert [m["filename"] for m in manifest] == ["safe.txt"]
    assert not os.path.exists(os.path.join(ws, "escape.txt"))


def test_different_goals_and_executions_get_separate_directories(tmp_path):
    ws = str(tmp_path)
    write_execution_artifacts(ws, "G-1", "E-1", {"out.txt": b"one"})
    write_execution_artifacts(ws, "G-2", "E-1", {"out.txt": b"two"})
    p1 = os.path.join(artifacts_dir(ws, "G-1", "E-1"), "out.txt")
    p2 = os.path.join(artifacts_dir(ws, "G-2", "E-1"), "out.txt")
    with open(p1, "rb") as f:
        assert f.read() == b"one"
    with open(p2, "rb") as f:
        assert f.read() == b"two"


# ---------------------------------------------------------------------
# list_artifacts / read_artifact (Sec 14 read side)
# ---------------------------------------------------------------------


def test_list_artifacts_on_empty_workspace_is_an_honest_empty_list(tmp_path):
    assert list_artifacts(str(tmp_path)) == []


def test_list_artifacts_finds_every_real_written_file(tmp_path):
    ws = str(tmp_path)
    write_execution_artifacts(ws, "G-1", "E-1", {"out.txt": b"hello"})
    write_execution_artifacts(ws, "G-2", "E-1", {"a.bin": b"\x00\x01"})
    entries = list_artifacts(ws)
    assert len(entries) == 2
    by_goal = {(e["goal_id"], e["filename"]): e for e in entries}
    assert by_goal[("G-1", "out.txt")]["execution_id"] == "E-1"
    assert by_goal[("G-1", "out.txt")]["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert by_goal[("G-2", "a.bin")]["size_bytes"] == 2


def test_read_artifact_returns_real_bytes_back(tmp_path):
    ws = str(tmp_path)
    write_execution_artifacts(ws, "G-1", "E-1", {"out.txt": b"hello world"})
    assert read_artifact(ws, "G-1", "E-1", "out.txt") == b"hello world"


def test_read_artifact_missing_file_returns_none_not_an_error(tmp_path):
    assert read_artifact(str(tmp_path), "G-1", "E-1", "nope.txt") is None


def test_read_artifact_path_escape_attempt_returns_none(tmp_path):
    ws = str(tmp_path)
    write_execution_artifacts(ws, "G-1", "E-1", {"out.txt": b"x"})
    assert read_artifact(ws, "G-1", "E-1", "../../../etc/passwd") is None
