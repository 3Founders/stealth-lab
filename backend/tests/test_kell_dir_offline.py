"""keळ's folder was renamed from `.stealth` to `.kell`; a workspace that still has only the old one keeps working."""
from __future__ import annotations

import os

from app.stealth.legacy_context import LEGACY_DIRNAME, STEALTH_DIRNAME, workspace_dir


def test_names():
    assert STEALTH_DIRNAME == ".kell"
    assert LEGACY_DIRNAME == "." + "stealth"


def test_old_folder_is_renamed(tmp_path):
    old = tmp_path / LEGACY_DIRNAME
    old.mkdir()
    (old / "claims.md").write_text("R-001|fact\n", encoding="utf-8")
    d = workspace_dir(str(tmp_path))
    assert d == os.path.join(str(tmp_path), ".kell")
    assert not old.exists()
    assert (tmp_path / ".kell" / "claims.md").read_text(encoding="utf-8") == "R-001|fact\n"


def test_both_folders_left_as_they_are(tmp_path):
    (tmp_path / LEGACY_DIRNAME).mkdir()
    (tmp_path / ".kell").mkdir()
    assert workspace_dir(str(tmp_path)) == os.path.join(str(tmp_path), ".kell")
    assert (tmp_path / LEGACY_DIRNAME).exists()


def test_no_folder_yet(tmp_path):
    assert workspace_dir(str(tmp_path)) == os.path.join(str(tmp_path), ".kell")
    assert not (tmp_path / ".kell").exists()
