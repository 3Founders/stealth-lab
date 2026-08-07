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
