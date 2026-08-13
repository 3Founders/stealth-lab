"""
Tests for unified-diff -> SEARCH/REPLACE conversion.

The conversion is only useful if SEARCH is byte-identical to what was in the
file before. A block whose SEARCH text does not match will simply fail to
apply, and the agent burns a step discovering that -- so the round-trip
property below is the point of the file, not the formatting assertions.
"""
from __future__ import annotations

from app.services.patch_format import diff_to_search_replace


SIMPLE = (
    "diff --git a/internal/server/auth/middleware.go b/internal/server/auth/middleware.go\n"
    "index 2e83a40697..b39e564e9f 100644\n"
    "--- a/internal/server/auth/middleware.go\n"
    "+++ b/internal/server/auth/middleware.go\n"
    "@@ -13,7 +15,14 @@ import (\n"
    " )\n"
    "\n"
    "-const authenticationHeaderKey = \"authorization\"\n"
    "+const (\n"
    "+\tauthenticationHeaderKey = \"authorization\"\n"
    "+\tcookieHeaderKey         = \"grpcgateway-cookie\"\n"
    "+)\n"
    "\n"
    " var errUnauthenticated = status.Error(codes.Unauthenticated, \"nope\")\n"
)


class TestConversion:
    def test_no_hunk_headers_survive(self):
        """The whole point: @@ line numbers refer to the PRECEDENT's file,
        not the one being edited, so carrying them over is misleading."""
        out = diff_to_search_replace(SIMPLE)
        assert "@@" not in out
        assert "index 2e83a40697" not in out

    def test_block_shape(self):
        out = diff_to_search_replace(SIMPLE)
        assert "internal/server/auth/middleware.go" in out
        assert "<<<<<<< SEARCH" in out
        assert "=======" in out
        assert ">>>>>>> REPLACE" in out

    def test_search_is_the_old_text_and_replace_the_new(self):
        out = diff_to_search_replace(SIMPLE)
        search = out.split("<<<<<<< SEARCH\n")[1].split("\n=======")[0]
        replace = out.split("=======\n")[1].split("\n>>>>>>> REPLACE")[0]
        # removed line present in SEARCH only
        assert 'const authenticationHeaderKey = "authorization"' in search
        assert "const (" not in search
        # added lines present in REPLACE only
        assert "const (" in replace
        assert 'cookieHeaderKey         = "grpcgateway-cookie"' in replace
        # context appears in BOTH -- that is what makes SEARCH matchable
        assert "var errUnauthenticated" in search
        assert "var errUnauthenticated" in replace

    def test_file_headers_are_not_mistaken_for_content(self):
        """`---`/`+++` start with -/+ but precede the first @@; treating them
        as removed/added lines would corrupt every block."""
        out = diff_to_search_replace(SIMPLE)
        assert "-- a/internal" not in out
        assert "++ b/internal" not in out

    def test_multiple_files(self):
        patch = SIMPLE + (
            "diff --git a/src/api/client.ts b/src/api/client.ts\n"
            "@@ -1,2 +1,2 @@ export function resolveTarget(id: string) {\n"
            "-  return id;\n"
            "+  return id ?? null;\n"
        )
        out = diff_to_search_replace(patch)
        assert out.count("<<<<<<< SEARCH") == 2
        assert "src/api/client.ts" in out
        assert "return id ?? null;" in out

    def test_no_newline_marker_dropped(self):
        patch = SIMPLE + "\\ No newline at end of file\n"
        assert "No newline at end of file" not in diff_to_search_replace(patch)

    def test_pure_addition_has_empty_search_side(self):
        patch = ("diff --git a/new.go b/new.go\n@@ -0,0 +1,2 @@\n"
                 "+package main\n+func main() {}\n")
        out = diff_to_search_replace(patch)
        assert "package main" in out
        assert out.index("<<<<<<< SEARCH") < out.index("package main")

    def test_budgets(self):
        patch = "".join(
            f"diff --git a/f{i}.go b/f{i}.go\n@@ -1 +1 @@\n-old{i}\n+new{i}\n"
            for i in range(10))
        assert diff_to_search_replace(patch, max_blocks=3).count("<<<<<<<") == 3
        assert "omitted" in diff_to_search_replace(patch, max_blocks=3)
        assert len(diff_to_search_replace(patch, max_chars=120)) < 200

    def test_degenerate_inputs(self):
        for value in ("", None, "not a diff at all", "diff --git a/x b/x\n"):
            assert diff_to_search_replace(value) == ""


class TestAgainstRealCorpusPatch:
    def test_search_text_actually_occurs_in_the_original_file(self):
        """Round-trip property. Reconstructed SEARCH must be text that really
        existed, or edit_file can never match it."""
        old_lines = [" )", "", "-const authenticationHeaderKey = \"authorization\"", "",
                     " var errUnauthenticated = status.Error(codes.Unauthenticated, \"nope\")"]
        original = "\n".join(
            l[1:] if l.startswith((" ", "-")) else l for l in old_lines)
        out = diff_to_search_replace(SIMPLE)
        search = out.split("<<<<<<< SEARCH\n")[1].split("\n=======")[0]
        assert search in original
