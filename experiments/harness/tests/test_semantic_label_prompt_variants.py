"""Offline proving tests for the semantic_label prompt variants prepared
2026-08-27 while the OpenRouter account has no spendable balance (board
budget-wall entry). Zero network - these tests only check the PROMPTS
themselves, never call a model. When funds return, each variant is run via
live_extractor.py --prompt-variant <name> per that module's docstring."""
from __future__ import annotations

import live_extractor
import semantic_label_prompt_variants as variants

MECHANICAL_MARKERS = (
    "file_touched:", "commit_made:", "test_run:", "command_executed:",
)

GOLD_EXEMPLAR_LABELS = (
    "authentication implementation was modified",
    "build output directory was deleted",
    "continuous integration pipeline configuration added",
    "database container started",
)


class TestRegistryShape:
    def test_expected_variant_names(self):
        assert set(variants.PROMPT_VARIANTS) == {
            "terse_v2", "few_shot", "vocab_discipline",
            "strict_noun_phrase", "combined"}

    def test_candidate_variants_excludes_the_baseline(self):
        assert "terse_v2" not in variants.CANDIDATE_VARIANTS
        assert set(variants.CANDIDATE_VARIANTS) == {
            "few_shot", "vocab_discipline", "strict_noun_phrase", "combined"}

    def test_four_candidates_prepared(self):
        assert len(variants.CANDIDATE_VARIANTS) == 4

    def test_no_variant_text_is_empty(self):
        for name, text in variants.PROMPT_VARIANTS.items():
            assert text and len(text) > 100, name


class TestBaselineFidelity:
    def test_terse_v2_is_the_actual_shipped_prompt_not_a_copy(self):
        # Reference equality (via import), not a hand-copied string that
        # could silently drift from what live_extractor.py really sends.
        assert variants.PROMPT_VARIANTS["terse_v2"] is live_extractor.SYSTEM_PROMPT


class TestMechanicalLayerUnchangedAcrossVariants:
    """Only the semantic_label instruction should vary between variants -
    the mechanical layer is already near-perfect (board: P 25/44->28/46,
    R 25/30->28/30 after the terse-label fix) and must not be disturbed by
    an experiment aimed at a different failure mode."""

    def test_every_variant_carries_all_four_mechanical_definitions(self):
        for name, text in variants.PROMPT_VARIANTS.items():
            for marker in MECHANICAL_MARKERS:
                assert marker in text, f"{name} missing {marker!r}"

    def test_every_variant_ends_with_the_json_only_contract(self):
        for name, text in variants.PROMPT_VARIANTS.items():
            assert "Reply with ONLY this JSON object" in text, name
            assert variants.EXTRACT_SCHEMA in text, name

    def test_every_variant_bans_inventing_facts(self):
        for name, text in variants.PROMPT_VARIANTS.items():
            assert "Do not invent facts" in text, name


class TestFewShotVariant:
    def test_contains_every_gold_exemplar_label_verbatim(self):
        text = variants.PROMPT_VARIANTS["few_shot"]
        for label in GOLD_EXEMPLAR_LABELS:
            assert label in text

    def test_contains_the_none_contract_negative_exemplar(self):
        text = variants.PROMPT_VARIANTS["few_shot"]
        assert "ls src" in text and "NO LABEL" in text

    def test_combined_variant_also_carries_the_exemplars(self):
        text = variants.PROMPT_VARIANTS["combined"]
        for label in GOLD_EXEMPLAR_LABELS:
            assert label in text


class TestVocabDisciplineVariant:
    def test_bans_the_ci_abbreviation_specifically(self):
        # Directly targets the ef-sem-003 miss: gold "continuous
        # integration..." vs terse-v2's own live prediction "CI workflow...".
        text = variants.PROMPT_VARIANTS["vocab_discipline"]
        assert "'continuous integration'" in text
        assert "never 'CI'" in text

    def test_combined_variant_also_carries_vocab_discipline(self):
        text = variants.PROMPT_VARIANTS["combined"]
        assert "never 'CI'" in text


class TestStrictNounPhraseVariant:
    def test_tighter_word_ceiling_than_terse_v2(self):
        text = variants.PROMPT_VARIANTS["strict_noun_phrase"]
        assert "2 to 5 words" in text
        # terse_v2's own ceiling ("never more than 8") must NOT be the
        # ceiling quoted in this stricter variant.
        assert "never more than 8" not in text

    def test_requires_a_self_count_step(self):
        text = variants.PROMPT_VARIANTS["strict_noun_phrase"]
        assert "COUNT your words" in text


class TestFileFamilyLimitationDisclosed:
    def test_module_docstring_discloses_the_out_of_scope_family(self):
        # Task 1's own instruction: don't guess at rubric changes
        # unilaterally. No variant should claim to fix the file-family
        # over-application finding; the module docstring must say so.
        doc = semantic_label_prompt_variants_doc()
        assert "files.json" in doc
        assert "rubric-scope" in doc and "integrator" in doc


def semantic_label_prompt_variants_doc() -> str:
    return variants.__doc__ or ""
