"""
Tests for call_graph.py: name-based static call-graph reachability.

Offline, synthetic Python + Go fixtures (the corpus's two biggest
languages) written to tmp_path -- no DB, no LLM, no real repo checkout.
What matters here is not that it finds SOMETHING, but that it finds the
right cross-file hop, stops at the requested depth, and degrades safely
(dead end, not a crash) on a call it cannot resolve.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from call_graph import (  # noqa: E402
    build_repo_symbol_index, call_targets, reachable_symbols, seed_from_text,
    seeds_in_file,
)


def _write(tmp_path, rel, content):
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


class TestCallTargets:
    def test_finds_a_simple_call(self):
        src = b"def a():\n    b()\n"
        assert call_targets(src, "a.py") == ["b"]

    def test_finds_the_rightmost_name_in_an_attribute_chain(self):
        src = b"def a():\n    obj.helper.method()\n"
        assert call_targets(src, "a.py") == ["method"]

    def test_unsupported_extension_returns_none_not_empty_list(self):
        """None means 'not checked', distinct from a real empty result --
        same contract code_index.outline/syntax_errors already use."""
        assert call_targets(b"whatever", "notes.txt") is None

    def test_go_selector_call_is_found(self):
        src = b"package main\n\nfunc a() {\n\thelper.Do()\n}\n"
        assert call_targets(src, "a.go") == ["Do"]


class TestSymbolIndex:
    def test_indexes_symbols_across_multiple_files(self, tmp_path):
        _write(tmp_path, "a.py", "def caller():\n    pass\n")
        _write(tmp_path, "sub/b.py", "def helper():\n    pass\n")
        idx = build_repo_symbol_index(str(tmp_path))
        assert "caller" in idx.by_name
        assert "helper" in idx.by_name
        assert idx.by_name["helper"][0][0] == "sub/b.py"
        assert idx.files_indexed == 2

    def test_skips_binary_and_ignored_dirs(self, tmp_path):
        _write(tmp_path, "a.py", "def caller():\n    pass\n")
        _write(tmp_path, "node_modules/dep.py", "def should_not_appear():\n    pass\n")
        idx = build_repo_symbol_index(str(tmp_path))
        assert "should_not_appear" not in idx.by_name


class TestReachableSymbols:
    def test_finds_a_one_hop_cross_file_call(self, tmp_path):
        _write(tmp_path, "a.py", "def caller():\n    helper()\n")
        _write(tmp_path, "b.py", "def helper():\n    return 1\n")
        idx = build_repo_symbol_index(str(tmp_path))
        reach = reachable_symbols([("a.py", "caller")], str(tmp_path), idx, max_hops=1)
        assert "b.py" in reach.files
        assert any("helper" in s for s in reach.symbols)

    def test_finds_a_two_hop_call_only_when_max_hops_allows_it(self, tmp_path):
        _write(tmp_path, "a.py", "def caller():\n    middle()\n")
        _write(tmp_path, "b.py", "def middle():\n    deep_helper()\n")
        _write(tmp_path, "c.py", "def deep_helper():\n    return 1\n")
        idx = build_repo_symbol_index(str(tmp_path))

        one_hop = reachable_symbols([("a.py", "caller")], str(tmp_path), idx, max_hops=1)
        assert "b.py" in one_hop.files
        assert "c.py" not in one_hop.files

        two_hop = reachable_symbols([("a.py", "caller")], str(tmp_path), idx, max_hops=2)
        assert "b.py" in two_hop.files
        assert "c.py" in two_hop.files

    def test_unresolvable_call_is_a_dead_end_not_a_crash(self, tmp_path):
        _write(tmp_path, "a.py", "def caller():\n    some_stdlib_function()\n")
        idx = build_repo_symbol_index(str(tmp_path))
        reach = reachable_symbols([("a.py", "caller")], str(tmp_path), idx, max_hops=2)
        assert reach.files == []
        assert reach.symbols == []

    def test_self_recursion_does_not_infinite_loop(self, tmp_path):
        _write(tmp_path, "a.py", "def recurse():\n    recurse()\n")
        idx = build_repo_symbol_index(str(tmp_path))
        # Must terminate at all -- pytest's own timeout isn't configured here,
        # so an infinite loop would hang the whole suite, not just fail.
        reach = reachable_symbols([("a.py", "recurse")], str(tmp_path), idx, max_hops=3)
        assert isinstance(reach.files, list)

    def test_missing_seed_symbol_is_handled_safely(self, tmp_path):
        _write(tmp_path, "a.py", "def real_function():\n    pass\n")
        idx = build_repo_symbol_index(str(tmp_path))
        reach = reachable_symbols([("a.py", "does_not_exist")], str(tmp_path), idx)
        assert reach.files == []


class TestSeeding:
    def test_seeds_in_file_returns_every_top_level_symbol(self, tmp_path):
        _write(tmp_path, "m.py", "def one():\n    pass\n\n\ndef two():\n    pass\n")
        seeds = seeds_in_file(str(tmp_path), "m.py")
        assert set(seeds) == {("m.py", "one"), ("m.py", "two")}

    def test_seeds_in_file_missing_file_returns_empty(self, tmp_path):
        assert seeds_in_file(str(tmp_path), "nope.py") == []

    def test_seed_from_text_finds_named_files(self, tmp_path):
        _write(tmp_path, "lib/thing.py", "def do_it():\n    pass\n")
        seeds = seed_from_text(
            "The bug is in lib/thing.py near the top", str(tmp_path))
        assert ("lib/thing.py", "do_it") in seeds

    def test_seed_from_text_ignores_files_not_present(self, tmp_path):
        seeds = seed_from_text("see missing/file.py for details", str(tmp_path))
        assert seeds == []
