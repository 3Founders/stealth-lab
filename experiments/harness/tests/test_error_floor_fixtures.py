"""Fixture-contract tests for the error-floor excerpt set (board item 4).

The 42-excerpt corpus is the instrument's calibration target: these tests
pin its SHAPE (counts, coverage, uniqueness, authorship discipline) so a
casual edit cannot silently hollow it out. Grading behavior lives in
test_error_floor_grading / _end_to_end.
"""
import json
from pathlib import Path

import pytest

import error_floor
from conftest import ERROR_FLOOR_FIXTURES

EXPECTED_PER_FILE = {
    "files.json": 12,
    "commands.json": 16,
    "semantic.json": 8,
    "negatives.json": 6,
}

#: Deliberate agreed-gold divergences from deterministic_v1's rules; each
#: must keep its notes defense (rubric authorship rule).
DELIBERATE_GAPS = [
    "ef-file-007",   # NotebookEdit touches a file; v1 whitelist misses it
    "ef-cmd-004",    # compound 'cd x && git commit' IS a commit
    "ef-cmd-005",    # flagged 'git -c ... commit' IS a commit
    "ef-cmd-014",    # pip install pytest-cov runs NO tests
]


@pytest.fixture(scope="module")
def excerpts():
    return error_floor.load_excerpts(ERROR_FLOOR_FIXTURES)


class TestCorpusShape:
    def test_count_within_sanctioned_range(self, excerpts):
        assert len(excerpts) == sum(EXPECTED_PER_FILE.values())
        assert error_floor.MIN_EXCERPTS <= len(excerpts) <= error_floor.MAX_EXCERPTS

    def test_per_file_counts(self):
        for name, n in EXPECTED_PER_FILE.items():
            data = json.loads((ERROR_FLOOR_FIXTURES / name).read_text("utf-8"))
            assert len(data["excerpts"]) == n, name

    def test_ids_unique_and_prefixed(self, excerpts):
        ids = [e["excerpt_id"] for e in excerpts]
        assert len(set(ids)) == len(ids)
        assert all(i.startswith("ef-") for i in ids)

    def test_every_type_has_gold_coverage(self, excerpts):
        covered = {g["observation_type"] for e in excerpts for g in e["gold"]}
        assert covered == set(error_floor.KNOWN_TYPES)

    def test_every_excerpt_carries_notes(self, excerpts):
        assert all(e["notes"].strip() for e in excerpts)


class TestGoldComposition:
    def test_hard_negatives_have_empty_gold(self, excerpts):
        by_id = {e["excerpt_id"]: e for e in excerpts}
        for eid in ("ef-file-005", "ef-neg-001", "ef-neg-006", "ef-sem-006"):
            assert by_id[eid]["gold"] == [], eid

    def test_semantic_none_contract_is_layer_scoped(self, excerpts):
        """ef-sem-005: mechanical command fact stands, label warranted NOT."""
        by_id = {e["excerpt_id"]: e for e in excerpts}
        types = [g["observation_type"] for g in by_id["ef-sem-005"]["gold"]]
        assert types == ["command_executed"]

    def test_semantic_excerpts_carry_both_layers(self, excerpts):
        by_id = {e["excerpt_id"]: e for e in excerpts}
        for eid in ("ef-sem-001", "ef-sem-002", "ef-sem-003", "ef-sem-004"):
            types = {g["observation_type"] for g in by_id[eid]["gold"]}
            assert types == {"semantic_label",
                             "file_touched" if eid in ("ef-sem-001", "ef-sem-003")
                             else "command_executed"}, eid

    def test_deliberate_gaps_present_with_defense_notes(self, excerpts):
        by_id = {e["excerpt_id"]: e for e in excerpts}
        for eid in DELIBERATE_GAPS:
            assert eid in by_id, eid
            assert "AGREED" in by_id[eid]["notes"], eid

    def test_gold_key_fields_well_formed(self, excerpts):
        for e in excerpts:
            for g in e["gold"]:
                assert error_floor.observation_key(g) is not None, e["excerpt_id"]


class TestValidationTeeth:
    def test_duplicate_id_rejected(self, tmp_path):
        row = {"excerpt_id": "ef-dup-001", "trace_event": {"tool_name": "Read"},
               "gold": [], "notes": "n"}
        _write(tmp_path, "a.json", [row])
        _write(tmp_path, "b.json", [dict(row)])
        with pytest.raises(error_floor.FixtureError, match="duplicate"):
            error_floor.load_excerpts(tmp_path)

    def test_unknown_type_rejected(self, tmp_path):
        bad = {"excerpt_id": "ef-x-001", "trace_event": {"tool_name": "Read"},
               "gold": [{"observation_type": "vibe", "label": "?", "properties": {}}],
               "notes": "n"}
        _write(tmp_path, "x.json", [bad])
        with pytest.raises(error_floor.FixtureError, match="unknown observation_type"):
            error_floor.load_excerpts(tmp_path)

    def test_missing_notes_rejected(self, tmp_path):
        bad = {"excerpt_id": "ef-x-002", "trace_event": {"tool_name": "Read"},
               "gold": [], "notes": "  "}
        _write(tmp_path, "x.json", [bad])
        with pytest.raises(error_floor.FixtureError, match="notes required"):
            error_floor.load_excerpts(tmp_path)

    def test_typed_observation_requires_its_key(self, tmp_path):
        bad = {"excerpt_id": "ef-x-003", "trace_event": {"tool_name": "Bash"},
               "gold": [{"observation_type": "test_run", "label": "Ran tests",
                         "properties": {"file_path": "nope"}}],
               "notes": "n"}
        _write(tmp_path, "x.json", [bad])
        with pytest.raises(error_floor.FixtureError, match="properties.command"):
            error_floor.load_excerpts(tmp_path)

    def test_empty_dir_rejected(self, tmp_path):
        with pytest.raises(error_floor.FixtureError, match="no excerpt files"):
            error_floor.load_excerpts(tmp_path)


def _write(tmp_path: Path, name: str, rows: list[dict]) -> None:
    (tmp_path / name).write_text(
        json.dumps({"excerpts": rows}), encoding="utf-8")
