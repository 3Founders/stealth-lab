"""Fixture-contract tests for the micro-experiment pack (board MEASURE
item 3): the pack must stay within the mandated 8-12 scenario count, cover
all four founder-named archetypes, and cross-reference cleanly."""
from pathlib import Path

import json

import micro_pack
from conftest import MICRO_FIXTURES


def _tasks():
    return {t["task_id"]: t for t in json.loads(
        (MICRO_FIXTURES / "tasks.json").read_text(encoding="utf-8"))["tasks"]}


def _procs():
    return micro_pack.scoring_load_procedures(MICRO_FIXTURES)


class TestSchema:
    def test_validation_passes_clean(self):
        assert micro_pack.validate_micro_fixtures(MICRO_FIXTURES) == []

    def test_scenario_count_within_mandate(self):
        assert 8 <= len(micro_pack.load_scenarios(MICRO_FIXTURES)) <= 12

    def test_all_four_named_archetypes_present(self):
        archs = {s["archetype"]
                 for s in micro_pack.load_scenarios(MICRO_FIXTURES).values()}
        assert set(micro_pack.ARCHETYPES) == archs, \
            "founder-named archetypes must all appear, and only those"

    def test_every_task_has_a_scenario_and_vice_versa(self):
        tasks, scenarios = _tasks(), micro_pack.load_scenarios(MICRO_FIXTURES)
        assert set(tasks) == set(scenarios)


class TestGroundTruthDiscipline:
    def test_stale_offers_are_ground_truth_stale(self):
        procs = _procs()
        for tid, t in _tasks().items():
            if t.get("stale_offer"):
                assert procs[t["stale_offer"]]["stale"] is True, tid

    def test_applicable_procedures_verified_and_fresh(self):
        procs = _procs()
        for tid, t in _tasks().items():
            pid = t.get("applicable_procedure")
            if pid:
                assert procs[pid]["verified"] is True and \
                    procs[pid]["stale"] is False, tid

    def test_staleness_reasons_exist_for_every_stale_procedure(self):
        for pid, p in _procs().items():
            if p["stale"]:
                assert p.get("staleness_reason"), f"{pid} lacks a reason"

    def test_refund_archetype_encodes_the_policy_trap(self):
        sc = micro_pack.load_scenarios(MICRO_FIXTURES)["mic-refund-001"]
        assert sc["archetype"] == "adversarial_policy_violation"
        types = {r["type"] for r in sc["evidence_requirements"]}
        assert {"gate_consulted", "refusal_recorded",
                "no_stale_execution"} <= types


class TestValidationCatchesBreakage:
    def _write_broken(self, tmp_path, mutate_tasks=None, mutate_scenarios=None):
        src = Path(MICRO_FIXTURES)
        dst = tmp_path / "micro"
        dst.mkdir()
        for name in ("procedures.json", "rag_corpus.json"):
            (dst / name).write_text(
                (src / name).read_text(encoding="utf-8"), encoding="utf-8")
        tasks = json.loads((src / "tasks.json").read_text(encoding="utf-8"))
        scen = json.loads((src / "scenarios.json").read_text(encoding="utf-8"))
        if mutate_tasks:
            mutate_tasks(tasks)
        if mutate_scenarios:
            mutate_scenarios(scen)
        (dst / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
        (dst / "scenarios.json").write_text(json.dumps(scen), encoding="utf-8")
        return dst

    def test_stale_offer_must_really_be_stale(self, tmp_path):
        def break_it(tasks):
            tasks["tasks"][0]["stale_offer"] = "refund-review-v2"
        dst = self._write_broken(tmp_path, mutate_tasks=break_it)
        problems = micro_pack.validate_micro_fixtures(dst)
        assert any("stale=true" in p for p in problems)

    def test_unknown_requirement_type_rejected(self, tmp_path):
        def break_it(scen):
            scen["scenarios"][0]["evidence_requirements"].append(
                {"id": "eX", "type": "vibes"})
        dst = self._write_broken(tmp_path, mutate_scenarios=break_it)
        problems = micro_pack.validate_micro_fixtures(dst)
        assert any("unknown requirement type" in p for p in problems)
