"""Scoreboard wiring for the error-floor section (board MEASURE item 4).

The contract: every future extractor change prints precision/recall —
via scoreboard.format_error_floor() directly, or by appending the
section to a §40 run through --error-floor-results. Numerator/denominator
beside every rate; '-' never a fake rate when a denominator is zero.
"""
import json
from pathlib import Path

import error_floor
import scoreboard


def _summary_with_semantic_null():
    """Tiny hand-built detail: one clean file_touched TP, one unanswered
    semantic gold (precision denominator zero for that type)."""
    graded = [
        error_floor.grade_excerpt(
            {"excerpt_id": "ef-x-1",
             "trace_event": {"event_id": "e", "tool_name": "Edit"},
             "gold": [file_obs("a.py")], "notes": "t"},
            [file_obs("a.py")]),
        error_floor.grade_excerpt(
            {"excerpt_id": "ef-x-2",
             "trace_event": {"event_id": "e", "tool_name": "Read"},
             "gold": [{"observation_type": "semantic_label",
                       "label": "something happened",
                       "properties": {}}], "notes": "t"},
            []),
    ]
    return error_floor.summarize(graded, extractor_name="unit")


def file_obs(path):
    return {"observation_type": "file_touched", "label": f"Modified {path}",
            "properties": {"file_path": path}}


class TestFormatErrorFloor:
    def test_header_counts_and_rates_with_numerators(self):
        lines = scoreboard.format_error_floor(_summary_with_semantic_null())
        text = "\n".join(lines)
        assert "EXTRACTION ERROR FLOOR" in text
        assert "golds=2 predictions=1 TP=1 FP=0 FN=1" in text
        assert "precision 1/1 (1.000)" in text
        assert "recall 1/2 (0.500)" in text

    def test_zero_denominator_renders_dash_not_fake_rate(self):
        text = "\n".join(scoreboard.format_error_floor(_summary_with_semantic_null()))
        sem_row = next(l for l in text.splitlines() if l.startswith("semantic_label"))
        assert "0/0 (-)" in sem_row      # precision: zero predictions of the type
        assert "0/1 (0.000)" in sem_row  # recall: real zero over real gold, printed

    def test_precision_numerator_is_tp_never_tp_plus_fp(self):
        """Regression: overall line once printed TP+FP over TP+FP (2/2)."""
        graded = [error_floor.grade_excerpt(
            {"excerpt_id": "ef-x-3",
             "trace_event": {"event_id": "e", "tool_name": "Edit"},
             "gold": [file_obs("a.py")], "notes": "t"},
            [file_obs("a.py"), file_obs("stray.py")])]
        text = "\n".join(scoreboard.format_error_floor(
            error_floor.summarize(graded)))
        assert "precision 1/2 (0.500)" in text
        assert "recall 1/1 (1.000)" in text

    def test_every_per_type_row_carries_both_denominators(self):
        # summarize() always emits one row per KNOWN_TYPE, even at zero.
        lines = scoreboard.format_error_floor(_summary_with_semantic_null())
        rows = lines[4:]
        assert len(rows) == len(error_floor.KNOWN_TYPES)
        assert all(l.startswith(tuple(error_floor.KNOWN_TYPES)) for l in rows)


def _arm_row(path):
    """One fully-valid three-arm task (empty JSONLs hit scoreboard's
    pre-existing zero-row render limitation — not this lane's bug)."""
    row = {"task_id": "t1"}
    for arm in ("A", "B", "C"):
        row[arm] = {"task_id": "t1", "arm": arm, "valid": True,
                    "invalid_reason": None, "resolved": True,
                    "reused_procedure_ids": [], "followed_memory_ids": [],
                    "refused_procedure_ids": [], "reuse_caused_failure": False,
                    "stale_offered": [], "tokens_in": 1000, "tokens_out": 100,
                    "tool_calls": 3, "latency_seconds": 1.0,
                    "human_interventions": 0, "unseen_task": False}
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return path


class TestCliIntegration:
    def test_main_appends_section_and_persists_detail(
            self, tmp_path, capsys):
        ef_detail = _summary_with_semantic_null()
        ef_path = tmp_path / "ef_detail.json"
        ef_path.write_text(json.dumps(ef_detail), encoding="utf-8")

        results = _arm_row(tmp_path / "results.jsonl")

        rc = scoreboard.main([
            str(results), "--fixtures-dir", str(Path(__file__).parent.parent / "fixtures"),
            "--banner", "WIRED RUN", "--error-floor-results", str(ef_path)])
        out = capsys.readouterr().out
        assert rc == 0 and "WIRED RUN" in out
        assert out.index("POWER-ANALYSIS FOOTER") < out.index(
            "EXTRACTION ERROR FLOOR")
        detail_path = results.with_name("results_scoreboard.json")
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
        assert detail["error_floor"]["tp"] == 1

    def test_flag_absent_leaves_classic_output_unchanged(self, tmp_path, capsys):
        results = _arm_row(tmp_path / "r.jsonl")
        rc = scoreboard.main([str(results), "--fixtures-dir",
                              str(Path(__file__).parent.parent / "fixtures")])
        out = capsysread(capsys)
        assert rc == 0
        assert "EXTRACTION ERROR FLOOR" not in out
        assert "SPEC 40 SCOREBOARD" in out


def capsysread(capsys):
    return capsys.readouterr().out
