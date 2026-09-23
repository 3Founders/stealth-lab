"""
DB-free coverage for scripts/migrate.py's file ordering. Regression test
for a real bug found during the claim/bootstrap feature's E2E acceptance
run: a pure lexical sort on migration filenames put "100_..." before
"93_...", which let 101_preserved_script_identity.sql (needs the `role`
column migration 98 adds) run before 98 ever did, and it failed with
UndefinedColumnError against the real database.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrate.py"
_spec = importlib.util.spec_from_file_location("_migrate_under_test", _SCRIPT_PATH)
migrate = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("_migrate_under_test", migrate)
_spec.loader.exec_module(migrate)  # type: ignore[union-attr]


def _touch(directory: Path, name: str) -> None:
    (directory / name).write_text("-- test migration\n")


def test_real_files_sorts_numerically_not_lexically(tmp_path):
    for name in (
        "93_a.sql", "94_b.sql", "98_c.sql", "99_d.sql",
        "100_e.sql", "101_f.sql", "107_g.sql",
    ):
        _touch(tmp_path, name)

    ordered = [p.name for p in migrate._real_files(tmp_path)]
    assert ordered == [
        "93_a.sql", "94_b.sql", "98_c.sql", "99_d.sql",
        "100_e.sql", "101_f.sql", "107_g.sql",
    ]


def test_real_files_keeps_letter_suffixed_variants_in_order(tmp_path):
    for name in ("07_x.sql", "08a_y.sql", "08b_z.sql", "09_w.sql"):
        _touch(tmp_path, name)

    ordered = [p.name for p in migrate._real_files(tmp_path)]
    assert ordered == ["07_x.sql", "08a_y.sql", "08b_z.sql", "09_w.sql"]


def test_real_files_reproduces_the_exact_bug_scenario(tmp_path):
    """98 (which 101 depends on for its `role` column) must sort before
    101, even though "101" < "98" as a plain string."""
    _touch(tmp_path, "98_remove_implementations.sql")
    _touch(tmp_path, "101_preserved_script_identity.sql")

    ordered = [p.name for p in migrate._real_files(tmp_path)]
    assert ordered.index("98_remove_implementations.sql") < ordered.index("101_preserved_script_identity.sql")
