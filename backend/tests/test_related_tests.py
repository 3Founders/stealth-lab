"""
Real tests for app/services/related_tests.py -- against a real temp
directory with real files, matching call_graph.py's own test convention
(host-side, filesystem-only, no DB needed).
"""
import os
from pathlib import Path

from app.services.related_tests import related_test_files, related_test_files_for_many


def _touch(root: Path, rel_path: str, content: str = "") -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def test_python_test_prefix_convention_found_in_same_directory(tmp_path):
    _touch(tmp_path, "app/services/foo.py")
    _touch(tmp_path, "app/services/test_foo.py")
    found = related_test_files(str(tmp_path), "app/services/foo.py")
    assert "app/services/test_foo.py" in found


def test_python_tests_subdirectory_convention_found(tmp_path):
    _touch(tmp_path, "app/services/bar.py")
    _touch(tmp_path, "app/services/tests/test_bar.py")
    found = related_test_files(str(tmp_path), "app/services/bar.py")
    assert "app/services/tests/test_bar.py" in found


def test_python_suffix_convention_found(tmp_path):
    _touch(tmp_path, "lib/baz.py")
    _touch(tmp_path, "lib/baz_test.py")
    found = related_test_files(str(tmp_path), "lib/baz.py")
    assert "lib/baz_test.py" in found


def test_python_e2e_suffix_convention_found(tmp_path):
    """This very repo's own real convention -- confirmed missing in an
    earlier version of this function by running it against the repo's
    own real files (procedures.py/applicability.py both came back empty
    despite having real e2e test coverage), not assumed correct."""
    _touch(tmp_path, "app/services/procedures.py")
    _touch(tmp_path, "tests/test_procedures_e2e.py")
    found = related_test_files(str(tmp_path), "app/services/procedures.py")
    assert "tests/test_procedures_e2e.py" in found


def test_go_convention_found(tmp_path):
    _touch(tmp_path, "pkg/handler.go")
    _touch(tmp_path, "pkg/handler_test.go")
    found = related_test_files(str(tmp_path), "pkg/handler.go")
    assert "pkg/handler_test.go" in found


def test_javascript_test_suffix_convention_found(tmp_path):
    _touch(tmp_path, "src/widget.js")
    _touch(tmp_path, "src/widget.test.js")
    found = related_test_files(str(tmp_path), "src/widget.js")
    assert "src/widget.test.js" in found


def test_typescript_spec_suffix_convention_found(tmp_path):
    _touch(tmp_path, "src/service.ts")
    _touch(tmp_path, "src/service.spec.ts")
    found = related_test_files(str(tmp_path), "src/service.ts")
    assert "src/service.spec.ts" in found


def test_javascript_dunder_tests_directory_convention_found(tmp_path):
    _touch(tmp_path, "src/thing.js")
    _touch(tmp_path, "src/__tests__/thing.test.js")
    found = related_test_files(str(tmp_path), "src/thing.js")
    assert "src/__tests__/thing.test.js" in found


def test_nonexistent_candidates_are_not_returned():
    """The core precision guarantee: every candidate is a REAL,
    existence-checked path, never a guessed one returned unconditionally."""
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        _touch(Path(root), "app/lonely.py")
        # No test file created at all.
        found = related_test_files(root, "app/lonely.py")
        assert found == []


def test_unrecognized_language_returns_empty_not_an_error(tmp_path):
    _touch(tmp_path, "README.md")
    found = related_test_files(str(tmp_path), "README.md")
    assert found == []


def test_related_test_files_for_many_unions_and_deduplicates(tmp_path):
    _touch(tmp_path, "app/a.py")
    _touch(tmp_path, "app/test_a.py")
    _touch(tmp_path, "app/b.py")
    _touch(tmp_path, "app/test_b.py")
    found = related_test_files_for_many(str(tmp_path), ["app/a.py", "app/b.py", "app/a.py"])
    assert sorted(found) == ["app/test_a.py", "app/test_b.py"]
    assert len(found) == len(set(found)), "must be deduplicated"


def test_related_test_files_for_many_with_no_matches_returns_empty(tmp_path):
    _touch(tmp_path, "app/orphan.py")
    found = related_test_files_for_many(str(tmp_path), ["app/orphan.py"])
    assert found == []


def test_multiple_real_conventions_can_both_be_found_for_one_file(tmp_path):
    """A repo that happens to have BOTH a co-located test file and a
    tests/ directory version (real, if unusual) must surface both, not
    stop at the first match."""
    _touch(tmp_path, "app/multi.py")
    _touch(tmp_path, "app/test_multi.py")
    _touch(tmp_path, "app/tests/test_multi.py")
    found = related_test_files(str(tmp_path), "app/multi.py")
    assert "app/test_multi.py" in found
    assert "app/tests/test_multi.py" in found


def test_returned_paths_use_forward_slashes(tmp_path):
    """Consistency with call_graph.py/local_retrieval.py's own path
    convention -- always forward slashes, regardless of host OS."""
    _touch(tmp_path, "pkg/sub/thing.go")
    _touch(tmp_path, "pkg/sub/thing_test.go")
    found = related_test_files(str(tmp_path), "pkg/sub/thing.go")
    assert all("\\" not in p for p in found)
