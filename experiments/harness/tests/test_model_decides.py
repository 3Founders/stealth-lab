"""Offline proving tests for the model-decides stale-procedure tier (board
Lane MEASURE CLAUDE.md Task 2, implementing
.scratch/research/model-decides-tier-design.md). Zero network."""
from __future__ import annotations

import json
from pathlib import Path

import model_decides as md
import scoring
from conftest import MICRO_FIXTURES, MODEL_DECIDES_FIXTURES


def procs():
    return scoring.load_procedures(MODEL_DECIDES_FIXTURES)


def ep(**over):
    base = {"valid": True, "resolved": False, "reused_procedure_ids": [],
           "followed_memory_ids": [], "refused_procedure_ids": [],
           "reuse_caused_failure": False}
    base.update(over)
    return base


class TestFixtureContract:
    def test_validation_passes_clean(self):
        assert md.validate_model_decides_fixtures(MODEL_DECIDES_FIXTURES) == []

    def test_task_count_and_prefix(self):
        tasks = md.load_tasks(MODEL_DECIDES_FIXTURES)
        assert len(tasks) == 24
        assert all(tid.startswith("dec-") for tid in tasks)

    def test_three_trap_and_three_control_per_domain(self):
        tasks = md.load_tasks(MODEL_DECIDES_FIXTURES)
        by_domain: dict[str, dict[str, int]] = {}
        for t in tasks.values():
            slot = by_domain.setdefault(t["domain"], {"trap": 0, "control": 0})
            slot[md.task_role(t)] += 1
        assert set(by_domain) == {"refunds", "deps", "pdf-etl", "env"}
        for domain, counts in by_domain.items():
            assert counts == {"trap": 3, "control": 3}, domain

    def test_single_offer_shape_never_both_fields(self):
        for tid, t in md.load_tasks(MODEL_DECIDES_FIXTURES).items():
            assert not (t.get("stale_offer") and t.get("applicable_procedure")), \
                f"{tid}: wave-1 is single-offer only (design §4)"

    def test_every_task_carries_situation_text(self):
        for tid, t in md.load_tasks(MODEL_DECIDES_FIXTURES).items():
            assert t.get("situation"), tid

    def test_no_scenarios_json_in_this_pack(self):
        # Deliberate: 24 scenario rows would blow past the micro pack's
        # mandated 8-12 count if they shared that file (see
        # micro_pack.validate_micro_fixtures / test_micro_fixtures.py).
        assert not (MODEL_DECIDES_FIXTURES / "scenarios.json").exists()

    def test_procedures_are_a_content_identical_copy_of_micro(self):
        mine = json.loads((MODEL_DECIDES_FIXTURES / "procedures.json")
                          .read_text(encoding="utf-8"))["procedures"]
        theirs = json.loads((MICRO_FIXTURES / "procedures.json")
                            .read_text(encoding="utf-8"))["procedures"]
        assert mine == theirs, \
            "drift tripwire: keep fixtures/model_decides/procedures.json " \
            "in sync with fixtures/micro/procedures.json, or explain why not"

    def test_rag_corpus_is_a_content_identical_copy_of_micro(self):
        mine = json.loads((MODEL_DECIDES_FIXTURES / "rag_corpus.json")
                          .read_text(encoding="utf-8"))["blobs"]
        theirs = json.loads((MICRO_FIXTURES / "rag_corpus.json")
                            .read_text(encoding="utf-8"))["blobs"]
        assert mine == theirs


class TestValidationCatchesBreakage:
    def _tasks_copy(self):
        return json.loads(json.dumps(md.load_tasks(MODEL_DECIDES_FIXTURES)))

    def _write_and_validate(self, tmp_path, tasks):
        dst = tmp_path / "model_decides"
        dst.mkdir()
        for name in ("procedures.json", "rag_corpus.json"):
            (dst / name).write_text(
                (MODEL_DECIDES_FIXTURES / name).read_text(encoding="utf-8"),
                encoding="utf-8")
        (dst / "tasks.json").write_text(
            json.dumps({"tasks": list(tasks.values())}), encoding="utf-8")
        return md.validate_model_decides_fixtures(dst)

    def test_stale_offer_must_really_be_stale(self, tmp_path):
        tasks = self._tasks_copy()
        tasks["dec-refund-101"]["stale_offer"] = "refund-review-v2"
        problems = self._write_and_validate(tmp_path, tasks)
        assert any("stale=true" in p for p in problems)

    def test_applicable_procedure_must_be_fresh(self, tmp_path):
        tasks = self._tasks_copy()
        tasks["dec-refund-104"]["applicable_procedure"] = "refund-auto-v1"
        problems = self._write_and_validate(tmp_path, tasks)
        assert any("verified and fresh" in p for p in problems)

    def test_dual_offer_rejected_in_wave_1(self, tmp_path):
        tasks = self._tasks_copy()
        tasks["dec-refund-104"]["stale_offer"] = "refund-auto-v1"
        problems = self._write_and_validate(tmp_path, tasks)
        assert any("exactly one of" in p for p in problems)

    def test_missing_bypass_flag_rejected(self, tmp_path):
        tasks = self._tasks_copy()
        tasks["dec-refund-101"]["substrate_bypasses_gate"] = False
        problems = self._write_and_validate(tmp_path, tasks)
        assert any("substrate_bypasses_gate" in p for p in problems)

    def test_missing_situation_rejected(self, tmp_path):
        tasks = self._tasks_copy()
        del tasks["dec-refund-101"]["situation"]
        problems = self._write_and_validate(tmp_path, tasks)
        assert any("no situation text" in p for p in problems)

    def test_wrong_prefix_rejected(self, tmp_path):
        tasks = self._tasks_copy()
        renamed = dict(tasks)
        t = renamed.pop("dec-refund-101")
        t["task_id"] = "mic-refund-101"
        renamed["mic-refund-101"] = t
        problems = self._write_and_validate(tmp_path, renamed)
        assert any("must start with" in p for p in problems)


class TestTaskRole:
    def test_trap_role(self):
        assert md.task_role({"stale_offer": "x", "applicable_procedure": None}) \
            == "trap"

    def test_control_role(self):
        assert md.task_role({"stale_offer": None, "applicable_procedure": "y"}) \
            == "control"

    def test_neither_is_unknown(self):
        assert md.task_role({}) == "unknown"

    def test_both_is_unknown(self):
        assert md.task_role({"stale_offer": "x", "applicable_procedure": "y"}) \
            == "unknown"


class TestTrapAvoidanceRow:
    def test_c_avoided_when_it_refused_the_stale_offer(self):
        task = {"task_id": "dec-refund-101", "domain": "refunds",
                "stale_offer": "refund-auto-v1"}
        row = md.trap_avoidance_row(
            task, ep(reuse_caused_failure=False),
            ep(refused_procedure_ids=["refund-auto-v1"]), procs())
        assert row["C"]["pass"] is True

    def test_c_did_not_avoid_when_it_reused_the_stale_offer(self):
        task = {"task_id": "dec-refund-101", "domain": "refunds",
                "stale_offer": "refund-auto-v1"}
        row = md.trap_avoidance_row(
            task, ep(), ep(reused_procedure_ids=["refund-auto-v1"]), procs())
        assert row["C"]["pass"] is False

    def test_refusing_a_NON_stale_id_does_not_count(self):
        # Ground-truth defense-in-depth: even if refused_procedure_ids
        # somehow carried a fresh procedure's id, that is not "avoided the
        # stale trap" - the check is against procedures_by_id, not just
        # "was anything refused".
        task = {"task_id": "dec-refund-999", "domain": "refunds",
                "stale_offer": "refund-auto-v1"}
        row = md.trap_avoidance_row(
            task, ep(), ep(refused_procedure_ids=["refund-review-v2"]),
            procs())
        assert row["C"]["pass"] is False

    def test_b_avoided_when_it_did_not_let_memory_cause_the_failure(self):
        task = {"task_id": "dec-refund-101", "domain": "refunds",
                "stale_offer": "refund-auto-v1"}
        row = md.trap_avoidance_row(
            task, ep(reuse_caused_failure=False), ep(), procs())
        assert row["B"]["pass"] is True

    def test_b_did_not_avoid_when_memory_caused_the_failure(self):
        task = {"task_id": "dec-refund-101", "domain": "refunds",
                "stale_offer": "refund-auto-v1"}
        row = md.trap_avoidance_row(
            task, ep(reuse_caused_failure=True), ep(), procs())
        assert row["B"]["pass"] is False

    def test_invalid_episode_leaves_the_arm_slot_none(self):
        task = {"task_id": "dec-refund-101", "domain": "refunds",
                "stale_offer": "refund-auto-v1"}
        row = md.trap_avoidance_row(
            task, ep(valid=False), ep(valid=False), procs())
        assert row["B"] is None and row["C"] is None

    def test_missing_episode_leaves_the_arm_slot_none(self):
        task = {"task_id": "dec-refund-101", "domain": "refunds",
                "stale_offer": "refund-auto-v1"}
        row = md.trap_avoidance_row(task, None, None, procs())
        assert row["B"] is None and row["C"] is None


class TestControlFalseRefusalRow:
    def test_false_refusal_when_c_refused_the_fresh_offer(self):
        task = {"task_id": "dec-refund-104", "domain": "refunds",
                "applicable_procedure": "refund-review-v2"}
        row = md.control_false_refusal_row(
            task, ep(refused_procedure_ids=["refund-review-v2"]), procs())
        assert row["C"]["false_refusal"] is True

    def test_no_false_refusal_when_c_reused_it(self):
        task = {"task_id": "dec-refund-104", "domain": "refunds",
                "applicable_procedure": "refund-review-v2"}
        row = md.control_false_refusal_row(
            task, ep(reused_procedure_ids=["refund-review-v2"]), procs())
        assert row["C"]["false_refusal"] is False
        assert row["C"]["reused"] is True

    def test_refusing_a_stale_id_never_counts_as_a_false_refusal_here(self):
        # Defense-in-depth mirror of the trap-side test: ground truth gates
        # the flag, not raw membership in refused_procedure_ids.
        task = {"task_id": "dec-refund-999", "domain": "refunds",
                "applicable_procedure": "refund-review-v2"}
        row = md.control_false_refusal_row(
            task, ep(refused_procedure_ids=["refund-auto-v1"]), procs())
        assert row["C"]["false_refusal"] is False


class TestAnalyze:
    def _tasks(self):
        return md.load_tasks(MODEL_DECIDES_FIXTURES)

    def test_sensitivity_pair_uses_mcnemar_power_verbatim(self):
        # 3 trap tasks: C avoids all 3, B avoids none -> 3 discordant pairs
        # favoring C, matching a hand-computed exact-p from mcnemar_power.
        rows = []
        for tid, stale_pid in (("dec-refund-101", "refund-auto-v1"),
                              ("dec-dep-101", "dep-pinbump-v1"),
                              ("dec-pdf-101", "pdf-sheet-v1")):
            rows.append({
                "task_id": tid,
                "B": ep(reuse_caused_failure=True),
                "C": ep(refused_procedure_ids=[stale_pid]),
            })
        report = md.analyze(rows, self._tasks(), procs())
        assert report["sensitivity_discordant"] == {"B_only": 0, "C_only": 3}
        assert "3 discordant pairs" in report["sensitivity_pair"]
        assert "exact-p=" in report["sensitivity_pair"]

    def test_false_refusal_rate_with_zero_denominator_is_none(self):
        report = md.analyze([], self._tasks(), procs())
        assert report["false_refusal_rate"] is None
        assert report["n_control_valid_c"] == 0

    def test_false_refusal_rate_computed_correctly(self):
        rows = [
            {"task_id": "dec-refund-104",
             "C": ep(refused_procedure_ids=["refund-review-v2"])},
            {"task_id": "dec-refund-105",
             "C": ep(reused_procedure_ids=["refund-review-v2"])},
        ]
        report = md.analyze(rows, self._tasks(), procs())
        assert report["n_control_valid_c"] == 2
        assert report["n_false_refusal"] == 1
        assert report["false_refusal_rate"] == 0.5

    def test_rows_for_other_tiers_task_ids_are_skipped(self):
        rows = [{"task_id": "mic-refund-001", "B": ep(), "C": ep()}]
        report = md.analyze(rows, self._tasks(), procs())
        assert report["n_trap_valid_both_arms"] == 0
        assert report["n_control_valid_c"] == 0

    def test_render_report_never_crashes_on_empty_input(self):
        report = md.analyze([], self._tasks(), procs())
        text = md.render_report(report)
        assert "0/24" in text or "0/" in text  # honest zero, not a crash


class TestLoadRows:
    def test_torn_final_line_is_skipped(self, tmp_path):
        path = tmp_path / "rows.jsonl"
        path.write_text(
            json.dumps({"task_id": "dec-refund-101"}) + "\n{\"broke",
            encoding="utf-8")
        rows = md.load_rows(path)
        assert len(rows) == 1
        assert rows[0]["task_id"] == "dec-refund-101"
