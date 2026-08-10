"""
Tests for the SWE-bench agent's RepoSandbox -- specifically the tools whose
failure mode is silence rather than an error.

`search` returning "no matches" is indistinguishable, from inside the
episode, between "this symbol does not exist" and "this tool cannot read
this language". The agent responds to both by looking somewhere else, so a
blind spot costs steps and then grades as a reasoning failure. That is not
hypothetical: the original allowlist could not see .go, .ts, .tsx or .js,
which is 69% of every file the gold patches touch, and it went unnoticed
because the only measured run was 9/9 ansible.

So the language coverage assertions below are the point of this file.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from agent import RepoSandbox  # noqa: E402

# Matches both the snake_case and camelCase spellings the fixture uses, so a
# miss below means the file was unreadable rather than merely named
# differently in that language's convention.
SYMBOL = "(?i)resolve_?target"


@pytest.fixture
def repo(tmp_path):
    """A checkout shaped like the corpus: four languages, a build dir that
    holds no answers, and a vendor dir that does."""
    files = {
        "lib/handler.py": "def resolve_target(x):\n    return x\n",
        "internal/server/auth.go": "func ResolveTarget(c *Conn) error {\n\treturn nil\n}\n",
        "src/api/client.ts": "export function resolveTarget(id: string) {}\n",
        "src/ui/Panel.tsx": "const ResolveTarget = () => <div/>;\n",
        "public/legacy.js": "function resolveTarget(o) { return o; }\n",
        "config/values.yml": "resolve_target: true\n",
        "Dockerfile": "RUN echo resolveTarget\n",
        "vendor/dep/vendored.go": "// resolveTarget lives here too\n",
        "build/generated.go": "// resolveTarget generated\n",
        "dist/bundle.js": "function resolveTarget(){}\n",
        "node_modules/pkg/index.js": "function resolveTarget(){}\n",
    }
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00resolveTarget")
    # Binary content behind an innocent extension -- the extension denylist
    # cannot catch this one, the NUL sniff has to.
    (tmp_path / "assets" / "blob.json").write_bytes(b"\x00\x01resolveTarget\x00")
    return RepoSandbox(str(tmp_path))


class TestSearchLanguageCoverage:
    @pytest.mark.parametrize("path", [
        "lib/handler.py",
        "internal/server/auth.go",
        "src/api/client.ts",
        "src/ui/Panel.tsx",
        "public/legacy.js",
        "config/values.yml",
        "Dockerfile",  # extensionless files are real gold-patch targets
    ])
    def test_every_corpus_language_is_searchable(self, repo, path):
        assert path in repo.search(SYMBOL)

    def test_go_is_visible(self, repo):
        """Go is 38% of the corpus -- the single largest language."""
        assert "internal/server/auth.go" in repo.search("ResolveTarget")


class TestSearchExclusions:
    def test_build_output_is_skipped(self, repo):
        out = repo.search(SYMBOL)
        assert "dist/bundle.js" not in out
        assert "node_modules" not in out

    def test_vendor_and_build_are_not_skipped(self, repo):
        """11 gold files live under vendor/ and 26 under build/, so excluding
        them to make search faster would hide real answers."""
        out = repo.search(SYMBOL)
        assert "vendor/dep/vendored.go" in out
        assert "build/generated.go" in out

    def test_binary_files_are_not_matched(self, repo):
        out = repo.search(SYMBOL)
        assert "logo.png" not in out          # caught by extension
        assert "blob.json" not in out         # caught by the NUL sniff

    def test_path_argument_narrows_the_search(self, repo):
        out = repo.search(SYMBOL, "src")
        assert "src/api/client.ts" in out
        assert "lib/handler.py" not in out


class TestEditAndDiff:
    def test_edit_then_diff_is_well_formed(self, repo):
        assert "edited" in repo.edit_file(
            "lib/handler.py", "return x", "return x or None")
        diff = repo.diff()
        assert diff.startswith("diff --git a/lib/handler.py b/lib/handler.py")
        assert "-    return x\n" in diff and "+    return x or None\n" in diff

    def test_ambiguous_old_str_is_refused(self, repo):
        """Silently editing the first of several matches is how an agent
        corrupts a file it never read."""
        repo.edit_file("lib/handler.py", "def resolve_target(x):",
                       "def resolve_target(x):\n    pass\n    pass")
        assert "appears" in repo.edit_file("lib/handler.py", "pass", "return")

    def test_missing_old_str_reports_rather_than_creates(self, repo):
        assert "not found" in repo.edit_file("lib/handler.py", "nonexistent", "x")

    def test_no_edits_means_empty_diff(self, repo):
        assert repo.diff() == ""

    def test_path_traversal_is_refused(self, repo):
        with pytest.raises(ValueError):
            repo._resolve("../../etc/passwd")


class TestFileCreationAndDeletion:
    """
    243 of the 731 corpus instances (33.2%) are fixed by ADDING a file, and 18
    by deleting one. Without these tools those instances were not hard, they
    were impossible -- the hidden test imported a module the agent had no way
    to write -- and they polluted every ablation with guaranteed concordant
    failures that contribute no discordant pairs.
    """

    def test_create_makes_nested_directories(self, repo):
        """New modules routinely land in a new package dir (src/hooks/,
        conf/mime/); failing on the missing directory would reinstate the
        same dead end one level up."""
        assert "created" in repo.create_file(
            "src/hooks/useWindowWidth.ts", "export const x = 1;\n")
        assert "src/hooks/useWindowWidth.ts" in repo.edited_files()

    def test_create_refuses_to_clobber(self, repo):
        assert "already exists" in repo.create_file("lib/handler.py", "x")

    def test_created_file_diff_has_git_new_file_header(self, repo):
        """`git apply` rejects an add whose header claims a source file, and a
        patch that fails to apply grades identically to a wrong answer."""
        repo.create_file("src/new.go", "package main\n")
        diff = repo.diff()
        assert "diff --git a/src/new.go b/src/new.go" in diff
        assert "new file mode 100644" in diff
        assert "--- /dev/null" in diff
        assert "+package main" in diff

    def test_deleted_file_diff_has_git_deleted_header(self, repo):
        repo.delete_file("lib/handler.py")
        diff = repo.diff()
        assert "deleted file mode 100644" in diff
        assert "+++ /dev/null" in diff
        assert "-def resolve_target(x):" in diff

    def test_delete_missing_file_reports(self, repo):
        assert "not a file" in repo.delete_file("nope.py")

    def test_edit_on_missing_file_points_at_create(self, repo):
        """The old message was a dead end. It must name the way out."""
        out = repo.edit_file("src/brand_new.ts", "a", "b")
        assert "create_file" in out

    def test_create_then_edit_round_trip(self, repo):
        repo.create_file("src/a.ts", "const a = 1;\n")
        assert "edited" in repo.edit_file("src/a.ts", "const a = 1;", "const a = 2;")
        diff = repo.diff()
        assert "new file mode" in diff  # still an addition, not a modification
        assert "+const a = 2;" in diff
        assert "const a = 1;" not in diff.split("+++")[1]

    def test_creation_is_visible_to_search(self, repo):
        repo.create_file("src/created.go", "func ResolveTarget() {}\n")
        assert "src/created.go" in repo.search(SYMBOL)

    def test_path_traversal_refused_on_create_and_delete(self, repo):
        with pytest.raises(ValueError):
            repo.create_file("../../evil.txt", "x")
        with pytest.raises(ValueError):
            repo.delete_file("../../etc/passwd")


class TestWhitespaceTolerantMatching:
    """
    Measured failure this fixes: 16 edit_file calls across three episodes,
    every one rejected, in the rhythm `edit -> read -> edit -> read`. The
    agent had found the right code and could not retype its indentation. Go
    is 38% of the corpus and tab-indented, so a model emitting spaces could
    never match. Those runs graded `no_patch` -- a formatting problem recorded
    as a reasoning failure.
    """

    @pytest.fixture
    def gorepo(self, tmp_path):
        # tab-indented, as real Go is
        (tmp_path / "srv.go").write_text(
            "func Handle(w http.ResponseWriter) {\n"
            "\tif token == \"\" {\n"
            "\t\treturn errUnauthenticated\n"
            "\t}\n"
            "}\n", encoding="utf-8")
        return RepoSandbox(str(tmp_path))

    def test_spaces_instead_of_tabs_still_matches(self, gorepo):
        out = gorepo.edit_file(
            "srv.go",
            '    if token == "" {\n        return errUnauthenticated\n    }',
            '    if token == "" {\n        return errMissingToken\n    }')
        assert "edited" in out and "ignoring indentation" in out
        assert gorepo.tolerant_edits == 1

    def test_replacement_is_reindented_to_the_files_own_style(self, gorepo):
        """Applying the model's spaces verbatim would corrupt a tab-indented
        file even though the edit 'succeeded'."""
        gorepo.edit_file(
            "srv.go",
            '    if token == "" {\n        return errUnauthenticated\n    }',
            '    if token == "" {\n        return errMissingToken\n    }')
        body = (gorepo.root and open(os.path.join(gorepo.root, "srv.go"),
                                     encoding="utf-8").read())
        assert "\t\treturn errMissingToken" in body
        assert "        return errMissingToken" not in body

    def test_trailing_whitespace_ignored(self, gorepo):
        assert "edited" in gorepo.edit_file(
            "srv.go", "\tif token == \"\" {   ", "\tif token != \"\" {")

    def test_exact_match_still_preferred_and_not_counted(self, gorepo):
        out = gorepo.edit_file("srv.go", "\t\treturn errUnauthenticated",
                               "\t\treturn errNope")
        assert out.endswith("srv.go")            # plain message, no re-indent note
        assert gorepo.tolerant_edits == 0

    def test_ambiguous_tolerant_match_is_refused(self, tmp_path):
        (tmp_path / "d.py").write_text("if x:\n    pass\nif y:\n    pass\n",
                                       encoding="utf-8")
        sb = RepoSandbox(str(tmp_path))
        assert "matches 2 places" in sb.edit_file("d.py", "        pass", "    return")

    def test_content_differences_are_still_rejected(self, gorepo):
        """Whitespace-insensitive, NOT content-insensitive. It must never
        guess which code was meant."""
        out = gorepo.edit_file("srv.go", "\tif tokenX == \"\" {", "\tif ok {")
        assert "not found" in out
        assert gorepo.tolerant_edits == 0

    def test_all_blank_anchor_refused(self, gorepo):
        assert "not found" in gorepo.edit_file("srv.go", "\n   \n", "x")

    def test_tolerant_edit_produces_appliable_diff(self, gorepo):
        gorepo.edit_file(
            "srv.go",
            '    if token == "" {\n        return errUnauthenticated\n    }',
            '    if token == "" {\n        return errMissingToken\n    }')
        diff = gorepo.diff()
        assert diff.startswith("diff --git a/srv.go b/srv.go")
        assert "-\t\treturn errUnauthenticated" in diff
        assert "+\t\treturn errMissingToken" in diff


class TestIndentationDepthIsPreserved:
    """
    Regression for a bug that was WORSE than the one it replaced. Computing
    depth as `len(relative_indent) // len(guessed_unit)` floored to 0 for
    every nested line whenever the snippet sat deeper than one level, so an
    edit inside any method flattened its body onto the anchor. The tool
    reported "edited" and the file no longer parsed -- IndentationError,
    0 tests collected, graded as a reasoning failure.
    """

    def test_nested_block_keeps_its_depth(self, tmp_path):
        (tmp_path / "m.py").write_text(
            "class A:\n"
            "    def probe(self):\n"
            "        cmd = 1\n"
            "        if cmd == 0:\n"
            "            self.debug()\n"
            "            return True\n"
            "        return False\n", encoding="utf-8")
        sb = RepoSandbox(str(tmp_path))
        # model reproduces the block with 4 extra leading spaces throughout
        old = ("            cmd = 1\n            if cmd == 0:\n"
               "                self.debug()\n                return True")
        new = ("            cmd = 2\n            if cmd == 0:\n"
               "                self.debug2()\n                return True")
        assert "edited" in sb.edit_file("m.py", old, new)
        body = (tmp_path / "m.py").read_text(encoding="utf-8")
        import ast
        ast.parse(body)  # raises if the block was flattened
        assert "            self.debug2()" in body

    def test_indent_unit_is_the_step_not_the_shallowest_width(self):
        # a file whose shallowest indented line is at 8 spaces still steps by 4
        assert RepoSandbox._file_indent_unit(
            "def f():\n        a = 1\n            b = 2\n") == "    "
        assert RepoSandbox._file_indent_unit("func f() {\n\tx()\n}\n") == "\t"


class TestNoNewlineAtEndOfFile:
    """
    `git apply` is all-or-nothing, so one newline-less file killed an entire
    multi-file patch. 127 of 4853 files in the ansible tree (2.6%) have no
    trailing newline -- including changelog fragments, which its gold patches
    always touch. Deleting such a file failed every single time.
    """

    @staticmethod
    def _applies(tmp_path, mutate) -> bool:
        import subprocess
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-qm", "i"], check=True)
        sb = RepoSandbox(str(tmp_path))
        mutate(sb)
        patch = sb.diff()
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "--", "."], check=True)
        p = tmp_path / "p.diff"
        p.write_text(patch, encoding="utf-8", newline="")
        return subprocess.run(["git", "-C", str(tmp_path), "apply", "--check", str(p)],
                              capture_output=True).returncode == 0

    def test_edit_near_eof_of_newlineless_file(self, tmp_path):
        (tmp_path / "f.txt").write_text("one\ntwo", encoding="utf-8", newline="")
        assert self._applies(tmp_path, lambda sb: sb.edit_file("f.txt", "two", "TWO"))

    def test_delete_newlineless_file(self, tmp_path):
        (tmp_path / "f.txt").write_text("only line", encoding="utf-8", newline="")
        assert self._applies(tmp_path, lambda sb: sb.delete_file("f.txt"))

    def test_marker_is_emitted(self, tmp_path):
        (tmp_path / "f.txt").write_text("one\ntwo", encoding="utf-8", newline="")
        sb = RepoSandbox(str(tmp_path))
        sb.edit_file("f.txt", "two", "TWO")
        assert "\\ No newline at end of file" in sb.diff()

    def test_lines_are_not_fused(self, tmp_path):
        """The failure signature was `-two+TWO` on one line."""
        (tmp_path / "f.txt").write_text("one\ntwo", encoding="utf-8", newline="")
        sb = RepoSandbox(str(tmp_path))
        sb.edit_file("f.txt", "two", "TWO")
        assert "-two+TWO" not in sb.diff()


class TestToolsExposedToTheModel:
    def test_new_tools_are_declared(self):
        """A tool the sandbox implements but TOOLS does not declare is
        invisible to the model -- the capability would exist and never be
        used, which is exactly the bug being fixed here."""
        from agent import TOOLS
        names = {t["function"]["name"] for t in TOOLS}
        assert {"create_file", "delete_file"} <= names
        assert {"list_dir", "search", "read_file", "edit_file", "finish"} <= names
