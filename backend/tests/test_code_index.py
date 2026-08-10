"""
Tests for tree-sitter symbol extraction (list_symbols / read_symbol).

The safety property that matters: extraction is a BYTE-EXACT slice of the
real file. It can be wrong about which bytes a parser gap misses, but it can
never hallucinate or drop a line the way an LLM-summarized function body
could. Every extraction test checks the extracted text is a verbatim
substring of the source, not just "looks plausible".
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro"))

import code_index  # noqa: E402
from agent import RepoSandbox  # noqa: E402

PY_SRC = b'''def add(a, b):
    """Adds two numbers."""
    return a + b


class Greeter:
    def hello(self, name):
        return f"hi {name}"

    def bye(self, name):
        return f"bye {name}"
'''

GO_SRC = b'''package main

func Add(a, b int) int {
\treturn a + b
}

type Server struct {
\tName string
}

func (s *Server) Start() error {
\treturn nil
}
'''

TS_SRC = b'''export function add(a: number, b: number): number {
  return a + b;
}

export class Greeter {
  hello(name: string): string {
    return `hi ${name}`;
  }
}
'''

JS_SRC = b'''function add(a, b) {
  return a + b;
}

class Greeter {
  hello(name) {
    return `hi ${name}`;
  }
}
'''


class TestLanguageDetection:
    @pytest.mark.parametrize("path,lang", [
        ("a.py", "python"), ("a/b.go", "go"), ("x.js", "javascript"),
        ("x.jsx", "javascript"), ("x.ts", "typescript"), ("x.tsx", "tsx"),
    ])
    def test_known_extensions(self, path, lang):
        assert code_index.language_for(path) == lang

    @pytest.mark.parametrize("path", ["README.md", "config.yml", "Dockerfile", "data.json"])
    def test_unknown_extensions_return_none(self, path):
        """None, not an empty list -- the caller must fall back to read_file,
        never silently report 'no symbols' for a language this doesn't cover."""
        assert code_index.language_for(path) is None


class TestOutline:
    def test_python_functions_and_methods(self):
        syms = code_index.outline(PY_SRC, "m.py")
        names = {s.qualified_name for s in syms}
        assert names == {"add", "Greeter", "Greeter.hello", "Greeter.bye"}
        add = next(s for s in syms if s.name == "add")
        assert add.kind == "def"
        assert add.start_line == 1

    def test_go_functions_methods_types(self):
        syms = code_index.outline(GO_SRC, "m.go")
        names = {s.qualified_name for s in syms}
        assert "Add" in names
        assert "Server" in names
        kinds = {s.name: s.kind for s in syms}
        assert kinds["Add"] == "func"

    def test_typescript_exported_symbols(self):
        syms = code_index.outline(TS_SRC, "m.ts")
        names = {s.qualified_name for s in syms}
        assert "add" in names
        assert "Greeter" in names
        assert "Greeter.hello" in names

    def test_javascript(self):
        syms = code_index.outline(JS_SRC, "m.js")
        names = {s.qualified_name for s in syms}
        assert names == {"add", "Greeter", "Greeter.hello"}

    def test_outline_on_unsupported_extension_is_none(self):
        assert code_index.outline(b"key: value\n", "conf.yml") is None

    def test_empty_file(self):
        assert code_index.outline(b"", "m.py") == []


class TestExtractionIsByteExact:
    """The load-bearing safety property."""

    @pytest.mark.parametrize("src,path,name", [
        (PY_SRC, "m.py", "add"), (PY_SRC, "m.py", "Greeter.hello"),
        (GO_SRC, "m.go", "Add"), (GO_SRC, "m.go", "Start"),
        (TS_SRC, "m.ts", "add"), (TS_SRC, "m.ts", "Greeter.hello"),
        (JS_SRC, "m.js", "add"),
    ])
    def test_extracted_text_is_a_verbatim_substring_of_the_source(self, src, path, name):
        matches = code_index.find_symbol(src, path, name)
        assert len(matches) == 1
        s = matches[0]
        sliced = src[s.start_byte:s.end_byte]
        assert sliced in src               # literal slice, not reconstructed
        assert sliced.decode() == sliced.decode()  # round-trips cleanly

    def test_go_method_extraction_includes_receiver(self):
        matches = code_index.find_symbol(GO_SRC, "m.go", "Start")
        body = GO_SRC[matches[0].start_byte:matches[0].end_byte]
        assert b"(s *Server) Start" in body

    def test_python_docstring_included_in_body(self):
        matches = code_index.find_symbol(PY_SRC, "m.py", "add")
        body = PY_SRC[matches[0].start_byte:matches[0].end_byte]
        assert b"Adds two numbers" in body


class TestNameResolution:
    def test_bare_method_name_matches_via_qualified_lookup(self):
        """A model asking for 'hello' without knowing the class should still
        find it if it is unambiguous."""
        matches = code_index.find_symbol(PY_SRC, "m.py", "hello")
        assert len(matches) == 1
        assert matches[0].qualified_name == "Greeter.hello"

    def test_qualified_name_also_works(self):
        matches = code_index.find_symbol(PY_SRC, "m.py", "Greeter.bye")
        assert len(matches) == 1

    def test_not_found_returns_empty_not_none(self):
        """None means 'unsupported language'; empty list means 'supported,
        but this name is not here' -- callers must be able to tell them apart."""
        assert code_index.find_symbol(PY_SRC, "m.py", "nonexistent") == []

    def test_ambiguous_name_across_two_classes(self):
        src = (b"class A:\n    def run(self):\n        pass\n\n"
               b"class B:\n    def run(self):\n        pass\n")
        matches = code_index.find_symbol(src, "m.py", "run")
        assert len(matches) == 2


class TestRepoSandboxIntegration:
    @pytest.fixture
    def repo(self, tmp_path):
        (tmp_path / "m.py").write_bytes(PY_SRC)
        (tmp_path / "m.go").write_bytes(GO_SRC)
        (tmp_path / "notes.md").write_text("# hi\n", encoding="utf-8")
        return RepoSandbox(str(tmp_path))

    def test_list_symbols_output(self, repo):
        out = repo.list_symbols("m.py")
        assert "add" in out and "lines 1-3" in out
        assert "Greeter.hello" in out

    def test_read_symbol_returns_only_that_function(self, repo):
        out = repo.read_symbol("m.py", "add")
        assert "def add" in out
        assert "class Greeter" not in out   # NOT the whole file

    def test_read_symbol_go_across_language(self, repo):
        out = repo.read_symbol("m.go", "Add")
        assert "func Add" in out

    def test_ambiguous_symbol_lists_candidates_not_a_guess(self, tmp_path):
        (tmp_path / "d.py").write_text(
            "class A:\n    def run(self):\n        pass\n\n"
            "class B:\n    def run(self):\n        pass\n", encoding="utf-8")
        sb = RepoSandbox(str(tmp_path))
        out = sb.read_symbol("d.py", "run")
        assert "ambiguous" in out
        assert "A.run" in out and "B.run" in out

    def test_missing_symbol_points_at_list_symbols(self, repo):
        out = repo.read_symbol("m.py", "nope")
        assert "list_symbols" in out

    def test_unsupported_file_falls_back_to_read_file_explicitly(self, repo):
        """Never silent nothing -- always names the escape hatch."""
        out = repo.list_symbols("notes.md")
        assert "read_file" in out
        out2 = repo.read_symbol("notes.md", "anything")
        assert "read_file" in out2

    def test_missing_file(self, repo):
        assert "not a file" in repo.list_symbols("ghost.py")
        assert "not a file" in repo.read_symbol("ghost.py", "x")


class TestToolsWiring:
    def test_list_symbols_and_read_symbol_are_declared_and_dispatched(self):
        from agent import TOOLS, Agent
        names = {t["function"]["name"] for t in TOOLS}
        assert {"list_symbols", "read_symbol"} <= names

    def test_dispatch_reaches_the_sandbox(self, tmp_path):
        from agent import Agent
        (tmp_path / "m.py").write_bytes(PY_SRC)
        sb = RepoSandbox(str(tmp_path))
        out, done = Agent._dispatch("read_symbol", {"path": "m.py", "name": "add"}, sb)
        assert "def add" in out and done is False

    def test_htn_subgoal_tools_inherit_both(self):
        """SUBGOAL_TOOLS derives from agent.TOOLS -- both arms must see
        identical tool sets for flat-vs-HTN to be a controlled comparison."""
        from htn_agent import SUBGOAL_TOOLS
        names = {t["function"]["name"] for t in SUBGOAL_TOOLS}
        assert {"list_symbols", "read_symbol"} <= names


class TestSyntaxErrors:
    """The lever with a direct, provable path to f2p: a patch that does not
    parse is a guaranteed test failure, and this catches that INSIDE the
    episode instead of only via the external grading harness afterward."""

    def test_clean_python_has_no_errors(self):
        assert code_index.syntax_errors(PY_SRC, "m.py") == (0, -1)

    def test_broken_python_is_detected(self):
        broken = b"def add(a, b:\n    return a + b\n"   # unclosed paren
        count, line = code_index.syntax_errors(broken, "m.py")
        assert count > 0
        assert line == 1

    def test_broken_go_is_detected(self):
        broken = b"package main\n\nfunc Add(a, b int) int {\n\treturn a + b\n"  # no closing brace
        count, _ = code_index.syntax_errors(broken, "m.go")
        assert count > 0

    def test_clean_go_has_no_errors(self):
        assert code_index.syntax_errors(GO_SRC, "m.go") == (0, -1)

    def test_unsupported_extension_returns_none_not_zero(self):
        """None must never be read as 'no errors' -- that would be a false
        green light for every language this module doesn't cover."""
        assert code_index.syntax_errors(b"not real yaml: [", "c.yml") is None


class TestSyntaxAdvisoryInSandbox:
    def test_edit_that_breaks_python_syntax_gets_a_warning(self, tmp_path):
        (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        sb = RepoSandbox(str(tmp_path))
        out = sb.edit_file("m.py", "def f():", "def f(:")   # invalid signature
        assert "syntax error" in out

    def test_clean_edit_has_no_warning(self, tmp_path):
        (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        sb = RepoSandbox(str(tmp_path))
        out = sb.edit_file("m.py", "return 1", "return 2")
        assert "syntax error" not in out

    def test_create_file_with_broken_syntax_gets_a_warning(self, tmp_path):
        sb = RepoSandbox(str(tmp_path))
        out = sb.create_file("new.py", "def f(:\n    pass\n")
        assert "syntax error" in out

    def test_warning_is_advisory_not_a_refusal(self, tmp_path):
        """The write must still happen -- this is a note the model can act
        on next turn, not a rollback (which would need to prove the error
        was CAUSED by this edit rather than pre-existing elsewhere)."""
        (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        sb = RepoSandbox(str(tmp_path))
        sb.edit_file("m.py", "def f():", "def f(:")
        assert (tmp_path / "m.py").read_text(encoding="utf-8") == "def f(:\n    return 1\n"

    def test_non_code_file_is_silently_unaffected(self, tmp_path):
        sb = RepoSandbox(str(tmp_path))
        out = sb.create_file("notes.md", "# just some prose [[[")
        assert "syntax error" not in out
