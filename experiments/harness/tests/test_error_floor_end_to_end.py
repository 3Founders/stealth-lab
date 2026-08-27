"""End-to-end error-floor runs against the real 42-excerpt corpus.

The expectation matrix below IS the instrument contract, hand-derived by
walking all 42 excerpts against deterministic_v1's published rules (see
fixtures' notes): any drift means the mirror, the rubric, or a fixture
changed — never noise. The perfect-predictions run proves the plumbing's
ceiling; the raising adapter proves partial runs refuse to masquerade as
a floor.
"""
import json

import pytest

import demo_extractor
import error_floor
import run_error_floor
from conftest import ERROR_FLOOR_FIXTURES

# ---- hand-derived from the fixture notes (do not regenerate blindly) ----
OVERALL = {
    "n_excerpts": 42, "n_gold": 30, "n_predictions": 25,
    "tp": 22, "fp": 3, "fn": 8,
    "precision": 0.88, "recall": 0.7333, "f1": 0.8,
}
PER_TYPE = {
    #              gold pred tp fp fn  P       R       f1
    "file_touched":     (8,   7,  7, 0, 1, 1.0,    0.875,  0.9333),
    "commit_made":      (5,   3,  3, 0, 2, 1.0,    0.6,    0.75),
    "test_run":         (7,   8,  7, 1, 0, 0.875,  1.0,    0.9333),
    "command_executed": (6,   7,  5, 2, 1, 0.7143, 0.8333, 0.7692),
    "semantic_label":   (4,   0,  0, 0, 4, None,   0.0,    None),
}
DISCREPANCY_IDS = {
    "ef-file-007", "ef-cmd-004", "ef-cmd-005", "ef-cmd-014",
    "ef-sem-001", "ef-sem-002", "ef-sem-003", "ef-sem-004",
}


@pytest.fixture(scope="module")
def demo_summary():
    excerpts = error_floor.load_excerpts(ERROR_FLOOR_FIXTURES)
    graded, errors, summary = run_error_floor.run(
        excerpts,
        lambda ex: demo_extractor.deterministic_v1_demo(ex["trace_event"]),
        demo_extractor.DEMO_EXTRACTOR_NAME)
    assert errors == []
    return summary


class TestDemoBaselineFloor:
    def test_overall_confusion_matches_hand_derivation(self, demo_summary):
        for k, v in OVERALL.items():
            assert demo_summary[k] == v, k

    def test_per_type_matrix(self, demo_summary):
        for otype, (gold, pred, tp, fp, fn, p, r, f1) in PER_TYPE.items():
            m = demo_summary["per_type"][otype]
            got = (m["gold"], m["predictions"], m["tp"], m["fp"], m["fn"],
                   m["precision"], m["recall"], m["f1"])
            assert got == (gold, pred, tp, fp, fn, p, r, f1), otype

    def test_discrepancy_excerpts_exactly_the_known_gap_set(self, demo_summary):
        got = {d["excerpt_id"] for d in demo_summary["discrepancies"]}
        assert got == DISCREPANCY_IDS

    def test_each_gap_has_its_documented_reason_codes(self, demo_summary):
        by_id = {d["excerpt_id"]: d for d in demo_summary["discrepancies"]}
        assert by_id["ef-file-007"]["fn"] == [{
            "observation_type": "file_touched",
            "key": "analysis/scratch.ipynb",
            "reason": "missing_no_candidate"}]
        for eid in ("ef-cmd-004", "ef-cmd-005"):
            assert by_id[eid]["fp"][0]["reason"] == "type_mismatch_vs_gold"
            assert by_id[eid]["fn"][0]["reason"] == \
                "type_mismatch_in_predictions"
        assert by_id["ef-cmd-014"]["fp"][0] == {
            "observation_type": "test_run",
            "key": "pip install pytest-cov",
            "reason": "type_mismatch_vs_gold"}
        for eid in ("ef-sem-001", "ef-sem-002", "ef-sem-003", "ef-sem-004"):
            assert by_id[eid]["fn"][0]["reason"] == "missing_no_candidate"
            assert by_id[eid]["fp"] == []

    def test_floor_is_not_perfect_by_construction(self, demo_summary):
        # The whole point: five known v1 quirks must be VISIBLE as numbers.
        assert demo_summary["precision"] < 1.0 and demo_summary["recall"] < 1.0


class TestSemanticJaccardOnRealGolds:
    """A scripted 'model-ish' adapter proves the free-text rule end-to-end."""

    LABELS = {
        "ef-sem-001": "the authentication implementation was modified today",
        "ef-sem-002": "deleted the build output directory",
        "ef-sem-003": "added continuous integration pipeline configuration",
        "ef-sem-004": "database container started successfully",
    }

    def test_near_paraphrase_labels_all_match_with_no_semantic_fp(self):
        excerpts = [e for e in
                    error_floor.load_excerpts(ERROR_FLOOR_FIXTURES)
                    if e["excerpt_id"].startswith("ef-sem-")]

        def scripted(excerpt):
            mech = demo_extractor.deterministic_v1_demo(excerpt["trace_event"])
            label = self.LABELS.get(excerpt["excerpt_id"])
            if label:
                mech.append({"observation_type": "semantic_label",
                             "label": label, "properties": {"tool_name": "?"}})
            return mech

        graded = [error_floor.grade_excerpt(e, scripted(e)) for e in excerpts]
        s = error_floor.summarize(graded)
        sem = s["per_type"]["semantic_label"]
        assert (sem["tp"], sem["fp"], sem["fn"]) == (4, 0, 0)
        assert s["per_type"]["command_executed"]["fp"] == 0  # NONE respected

    def test_off_topic_label_is_key_mismatch_not_silence(self):
        excerpt = next(e for e in
                       error_floor.load_excerpts(ERROR_FLOOR_FIXTURES)
                       if e["excerpt_id"] == "ef-sem-001")
        pred = [{"observation_type": "semantic_label",
                 "label": "tests were refactored", "properties": {}}]
        g = error_floor.grade_excerpt(excerpt, pred)
        assert g["fn_detail"][1]["reason"] == "key_mismatch_same_type"


class TestCliModes:
    def test_adapter_run_writes_results_and_prints_section(
            self, tmp_path, capsys):
        out = tmp_path / "ef_results.jsonl"
        rc = run_error_floor.main([
            "--adapter", "demo_extractor:deterministic_v1_demo",
            "--out", str(out)])
        assert rc == 0
        text = capsys.readouterr().out
        assert "EXTRACTION ERROR FLOOR" in text
        assert "FN file_touched key='analysis/scratch.ipynb'" in text
        detail = json.loads(
            out.with_name("ef_results_detail.json").read_text("utf-8"))
        assert detail["extractor"] == "demo_extractor:deterministic_v1_demo"
        for k, v in OVERALL.items():
            assert detail[k] == v, k

    def test_predictions_mode_perfect_extractor_hits_ceiling(
            self, tmp_path, capsys):
        excerpts = error_floor.load_excerpts(ERROR_FLOOR_FIXTURES)
        preds = tmp_path / "preds.jsonl"
        with preds.open("w", encoding="utf-8") as f:
            for e in excerpts:
                f.write(json.dumps({"excerpt_id": e["excerpt_id"],
                                    "observations": e["gold"]}) + "\n")
        out = tmp_path / "ceiling.jsonl"
        rc = run_error_floor.main(["--predictions", str(preds),
                                   "--out", str(out)])
        assert rc == 0
        detail = json.loads(
            out.with_name("ceiling_detail.json").read_text("utf-8"))
        assert (detail["precision"], detail["recall"], detail["f1"]) == (1.0, 1.0, 1.0)
        assert detail["discrepancies"] == []

    def test_predictions_missing_excerpts_exit_2(self, tmp_path):
        preds = tmp_path / "partial.jsonl"
        preds.write_text(
            json.dumps({"excerpt_id": "ef-not-a-real-id",
                        "observations": []}) + "\n", encoding="utf-8")
        rc = run_error_floor.main(["--predictions", str(preds)])
        assert rc == 2

    def test_excerpt_ids_grades_only_the_named_subset(self, tmp_path):
        preds = tmp_path / "subset_preds.jsonl"
        excerpts = error_floor.load_excerpts(ERROR_FLOOR_FIXTURES)
        sem_ids = {"ef-sem-001", "ef-sem-002", "ef-sem-003", "ef-sem-004",
                   "ef-sem-005", "ef-sem-006", "ef-sem-007", "ef-sem-008"}
        with preds.open("w", encoding="utf-8") as f:
            for e in excerpts:
                if e["excerpt_id"] in sem_ids:
                    f.write(json.dumps({"excerpt_id": e["excerpt_id"],
                                        "observations": e["gold"]}) + "\n")
        out = tmp_path / "subset.jsonl"
        rc = run_error_floor.main([
            "--predictions", str(preds),
            "--excerpt-ids", ",".join(sorted(sem_ids)),
            "--out", str(out)])
        assert rc == 0
        detail = json.loads(
            out.with_name("subset_detail.json").read_text("utf-8"))
        assert detail["n_excerpts"] == len(sem_ids)
        assert (detail["precision"], detail["recall"]) == (1.0, 1.0)

    def test_excerpt_ids_unknown_id_exits_2(self, tmp_path):
        rc = run_error_floor.main([
            "--adapter", "demo_extractor:deterministic_v1_demo",
            "--excerpt-ids", "ef-not-a-real-id",
            "--out", str(tmp_path / "x.jsonl")])
        assert rc == 2

    def test_raising_adapter_never_scores_as_zero_observations(
            self, tmp_path, capsys):
        out = tmp_path / "boom.jsonl"

        import sys
        sys.path.insert(0, str(tmp_path))
        (tmp_path / "boom_adapter.py").write_text(
            "def explode(event):\n"
            "    if event['event_id'] == 'ev-f007':\n"
            "        raise RuntimeError('model timeout')\n"
            "    return []\n", encoding="utf-8")
        try:
            rc = run_error_floor.main(["--adapter", "boom_adapter:explode",
                                       "--out", str(out)])
        finally:
            sys.path.pop(0)
        assert rc == 1
        text = capsys.readouterr().out
        assert "ADAPTER ERRORS" in text and "ef-file-007" in text
        detail = json.loads(
            out.with_name("boom_detail.json").read_text("utf-8"))
        assert detail["n_error_excerpts"] == 1
