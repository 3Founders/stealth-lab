"""
MCP hardening B36: pure-logic half of multi-agent coordination --
`_paths_overlap`/`_glob_prefix` need no database.
"""
from app.execution.coordination import _glob_prefix, _paths_overlap, _symbols_overlap


def test_glob_prefix_stops_at_first_wildcard_char():
    assert _glob_prefix("src/*.py") == "src/"
    assert _glob_prefix("src/foo/**") == "src/foo/"
    assert _glob_prefix("no_wildcard_here") == "no_wildcard_here"
    assert _glob_prefix("?leading") == ""


def test_exact_vs_exact_overlap():
    assert _paths_overlap(["a.py"], [], ["a.py"], []) == ["a.py"]
    assert _paths_overlap(["a.py"], [], ["b.py"], []) == []


def test_exact_vs_glob_overlap():
    assert _paths_overlap(["src/foo.py"], [], [], ["src/*.py"]) == ["src/foo.py"]
    assert _paths_overlap([], ["src/*.py"], ["src/foo.py"], []) == ["src/foo.py"]
    assert _paths_overlap(["docs/readme.md"], [], [], ["src/*.py"]) == []


def test_glob_vs_glob_shared_prefix_is_flagged_conservatively():
    overlap = _paths_overlap([], ["src/*.py"], [], ["src/foo/*.py"])
    assert overlap == ["src/*.py ~ src/foo/*.py"]


def test_glob_vs_glob_unrelated_prefixes_do_not_overlap():
    assert _paths_overlap([], ["src/*.py"], [], ["docs/*.md"]) == []


def test_identical_globs_overlap():
    assert _paths_overlap([], ["src/*.py"], [], ["src/*.py"]) == ["src/*.py ~ src/*.py"]


def test_no_declared_paths_on_either_side_means_no_overlap():
    assert _paths_overlap([], [], [], []) == []


def test_symbols_overlap_is_exact_name_match():
    assert _symbols_overlap(["process_payment"], ["process_payment"]) == ["process_payment"]
    assert _symbols_overlap(["process_payment"], ["ProcessPayment"]) == [], \
        "exact, case-sensitive match only -- no fuzzy/casefold matching"
    assert _symbols_overlap(["process_payment"], ["payments.process_payment"]) == [], \
        "no qualified-name resolution -- different strings never match"


def test_symbols_overlap_empty_on_either_side_is_no_overlap():
    assert _symbols_overlap([], ["foo"]) == []
    assert _symbols_overlap(["foo"], []) == []
    assert _symbols_overlap([], []) == []


def test_symbols_overlap_multiple_shared_names_sorted():
    assert _symbols_overlap(["b", "a", "c"], ["a", "b"]) == ["a", "b"]
