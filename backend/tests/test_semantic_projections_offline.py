"""
Offline, pure-function tests for app.services.semantic_projections.

No DB, no network, no LLM calls -- these are text-building and hashing
primitives only. Run with DATABASE_URL unset (or set, doesn't matter --
nothing here touches a connection).
"""
from __future__ import annotations

from app.services.semantic_projections import (
    claim_embedding_text,
    task_embedding_text,
    procedure_embedding_text,
    content_hash,
    needs_reembedding,
)


# ---------------------------------------------------------------------------
# claim_embedding_text
# ---------------------------------------------------------------------------

class TestClaimEmbeddingText:
    def test_bare_statement_when_no_structured_fields(self):
        text = claim_embedding_text(statement="The API returns 429 under load.")
        assert text == "The API returns 429 under load."

    def test_includes_full_triple_when_all_present(self):
        text = claim_embedding_text(
            statement="The API returns 429 under load.",
            subject="rate_limiter",
            predicate="throttles",
            object="write_endpoint",
        )
        assert text == (
            "The API returns 429 under load.\n\n"
            "subject: rate_limiter\n"
            "predicate: throttles\n"
            "object: write_endpoint"
        )

    def test_partial_triple_includes_only_given_fields(self):
        text = claim_embedding_text(
            statement="The API returns 429 under load.",
            subject="rate_limiter",
        )
        assert text == (
            "The API returns 429 under load.\n\n"
            "subject: rate_limiter"
        )

    def test_predicate_and_object_without_subject(self):
        text = claim_embedding_text(
            statement="Stmt.",
            predicate="throttles",
            object="write_endpoint",
        )
        assert text == (
            "Stmt.\n\n"
            "predicate: throttles\n"
            "object: write_endpoint"
        )

    def test_statement_only_field_is_required(self):
        # statement alone is a valid call, no crash, no trailing junk.
        text = claim_embedding_text(statement="Just a statement.")
        assert "subject" not in text
        assert "predicate" not in text
        assert "object" not in text


# ---------------------------------------------------------------------------
# task_embedding_text
# ---------------------------------------------------------------------------

class TestTaskEmbeddingText:
    def test_name_only(self):
        text = task_embedding_text(name="Extract fields")
        assert text == "Extract fields"

    def test_name_and_description(self):
        text = task_embedding_text(
            name="Extract fields", description="Pull structured fields from raw text."
        )
        assert text == "Extract fields\nPull structured fields from raw text."

    def test_io_schema_keys_folded_in(self):
        text = task_embedding_text(
            name="Extract fields",
            description="Pull structured fields from raw text.",
            io_schema={"input": {"raw_text": "string"}, "output": {"fields": "object"}},
        )
        assert text == (
            "Extract fields\n"
            "Pull structured fields from raw text.\n\n"
            "io: input, output"
        )

    def test_io_schema_without_description(self):
        text = task_embedding_text(
            name="Extract fields",
            io_schema={"input": {}, "output": {}},
        )
        assert text == "Extract fields\n\nio: input, output"

    def test_empty_io_schema_dict_contributes_nothing(self):
        text = task_embedding_text(name="Extract fields", io_schema={})
        assert text == "Extract fields"

    def test_none_io_schema_contributes_nothing(self):
        text = task_embedding_text(name="Extract fields", description="d", io_schema=None)
        assert text == "Extract fields\nd"


# ---------------------------------------------------------------------------
# procedure_embedding_text
# ---------------------------------------------------------------------------

class TestProcedureEmbeddingText:
    def test_name_and_goal_only(self):
        text = procedure_embedding_text(name="Fix flaky retry", goal="Stop the retry loop from spinning forever.")
        assert text == "Fix flaky retry\nStop the retry loop from spinning forever."

    def test_capability_statement_included_when_present(self):
        text = procedure_embedding_text(
            name="Fix flaky retry",
            goal="Stop the retry loop from spinning forever.",
            capability_statement="Bounds retry attempts with exponential backoff and a hard cap.",
        )
        assert text == (
            "Fix flaky retry\n"
            "Stop the retry loop from spinning forever.\n\n"
            "capability: Bounds retry attempts with exponential backoff and a hard cap."
        )

    def test_capability_statement_absent_falls_back(self):
        text = procedure_embedding_text(
            name="Fix flaky retry",
            goal="Stop the retry loop from spinning forever.",
            capability_statement=None,
        )
        assert "capability:" not in text


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_deterministic_same_input_same_hash(self):
        text = "The API returns 429 under load."
        assert content_hash(text) == content_hash(text)

    def test_sensitive_to_any_change(self):
        h1 = content_hash("The API returns 429 under load.")
        h2 = content_hash("The API returns 429 under load!")
        assert h1 != h2

    def test_sensitive_to_whitespace_change(self):
        h1 = content_hash("a\nb")
        h2 = content_hash("a\n\nb")
        assert h1 != h2

    def test_is_sha256_hex_digest(self):
        h = content_hash("anything")
        assert len(h) == 64
        int(h, 16)  # raises if not valid hex

    def test_matches_hashlib_sha256_directly(self):
        import hashlib
        text = "matches reference implementation"
        assert content_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# needs_reembedding
# ---------------------------------------------------------------------------

class TestNeedsReembedding:
    def test_true_when_stored_hash_is_none(self):
        assert needs_reembedding(current_text="anything", stored_hash=None) is True

    def test_true_when_text_changed(self):
        old_hash = content_hash("original text")
        assert needs_reembedding(current_text="changed text", stored_hash=old_hash) is True

    def test_false_when_text_identical(self):
        text = "unchanged text"
        stored_hash = content_hash(text)
        assert needs_reembedding(current_text=text, stored_hash=stored_hash) is False

    def test_false_only_on_exact_match_case_sensitive(self):
        stored_hash = content_hash("Text")
        assert needs_reembedding(current_text="text", stored_hash=stored_hash) is True
