"""
Tests for the SWE-bench -> knowledge graph extraction.

Every function here failed silently on real data before being fixed, and
silently is the operative word: each produced a plausible-looking string
that only showed up as a bad retrieval number much later.

  normalize_statement  391 of 731 problem statements carry literal
                       backslash-n instead of newlines, so anything
                       splitting on "\n" saw one 3000-character line.
  title_of             produced 116 degenerate titles, 52 of them the bare
                       string "Title" -- 52 nodes sharing a name, colliding
                       in both the lexical index and the vector space.
  patch_facts          the localization signal; empty files means the
                       knowledge node has nothing to point at.

These are cheap, deterministic, and run without a database or an API key.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from graph_ingest import (  # noqa: E402
    knowledge_text, normalize_statement, patch_facts, title_of,
)


class TestNormalizeStatement:
    def test_literal_backslash_n_becomes_a_newline(self):
        """The 391-row case. Without this, nothing downstream ever sees
        more than a single line."""
        assert normalize_statement(r"Title\n\nBody text") == "Title\n\nBody text"

    def test_real_newlines_are_left_alone(self):
        assert normalize_statement("Title\n\nBody") == "Title\n\nBody"

    def test_literal_crlf_and_tabs(self):
        assert normalize_statement(r"a\r\nb\tc") == "a\nb\tc"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_degenerate_inputs(self, value):
        assert normalize_statement(value) == ""


class TestTitleOf:
    def test_bolded_title_shape(self):
        assert title_of('"**Title: Email validation is wrong**\n\n## Description') \
            == "Email validation is wrong"

    def test_heading_label_on_its_own_line_is_skipped(self):
        """The 52-instance case: `# Title` alone, real title on the next
        line. Returning the label gives 52 nodes all named "Title"."""
        assert title_of("# Title\n\nStandardize PlayIterator state\n\n## Description") \
            == "Standardize PlayIterator state"

    def test_same_shape_with_literal_escapes(self):
        assert title_of(r"# Title\n\nHost blocking does not apply to subdomains") \
            == "Host blocking does not apply to subdomains"

    def test_inline_label_prefix_is_stripped(self):
        assert title_of("### Title: tsh login should not change kubectl context") \
            == "tsh login should not change kubectl context"

    def test_issue_title_variant(self):
        assert title_of("**Issue Title**: Improving encapsulation") \
            == "Improving encapsulation"

    def test_falls_back_to_first_real_line(self):
        assert title_of("Crash when diffing edited content\n\nmore") \
            == "Crash when diffing edited content"

    def test_never_returns_a_bare_label(self):
        for shape in ("# Title", "**Title**", "Title:", "## Title\n\n"):
            assert title_of(shape) in ("(untitled)",), shape

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_degenerate_inputs(self, value):
        assert title_of(value) == "(untitled)"

    def test_length_is_bounded(self):
        assert len(title_of("# " + "x" * 500)) <= 160


class TestPatchFacts:
    PATCH = (
        "diff --git a/internal/server/auth/middleware.go b/internal/server/auth/middleware.go\n"
        "--- a/internal/server/auth/middleware.go\n"
        "+++ b/internal/server/auth/middleware.go\n"
        "@@ -10,6 +10,7 @@ func UnaryInterceptor(logger *zap.Logger) grpc.UnaryServerInterceptor {\n"
        "-\told\n+\tnew\n"
        "diff --git a/src/api/client.ts b/src/api/client.ts\n"
        "@@ -1,3 +1,4 @@ export function resolveTarget(id: string) {\n"
        "-\ta\n+\tb\n"
    )

    def test_files_come_from_the_diff_headers(self):
        files, _ = patch_facts(self.PATCH)
        assert files == ["internal/server/auth/middleware.go", "src/api/client.ts"]

    def test_symbols_come_from_hunk_context(self):
        """git writes the enclosing function after `@@ ... @@`. Using it is
        free localization that works across all four corpus languages
        without a per-language parser."""
        _, symbols = patch_facts(self.PATCH)
        assert "UnaryInterceptor" in symbols
        assert "resolveTarget" in symbols

    def test_symbols_are_capped(self):
        patch = "".join(f"@@ -1 +1 @@ func Fn{i}(\n" for i in range(50))
        assert len(patch_facts(patch)[1]) <= 12

    @pytest.mark.parametrize("value", ["", None, "not a diff"])
    def test_degenerate_inputs(self, value):
        assert patch_facts(value) == ([], [])


class TestKnowledgeText:
    def test_paths_and_symbols_lead(self):
        text = knowledge_text("flipt-io/flipt", ["a/b.go"], ["Fn"], "")
        assert "flipt-io/flipt" in text and "a/b.go" in text and "Fn" in text

    def test_interface_included_when_present(self):
        text = knowledge_text("r", ["f.go"], ["S"], "Type: Method\nName: db.mget")
        assert "db.mget" in text

    def test_no_interface_section_when_absent(self):
        assert "interface:" not in knowledge_text("r", ["f.go"], ["S"], "   ")
