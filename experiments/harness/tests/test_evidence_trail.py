"""Evidence-trail machinery: surface journal, requirement checks, scenario
grading, waiver semantics, and rendering (board MEASURE item 3)."""
import json

import micro_pack
import mcp_surface
from conftest import MICRO_FIXTURES


def episode(**over):
    base = {
        "task_id": "t", "arm": "C", "valid": True, "invalid_reason": None,
        "resolved": True, "reused_procedure_ids": [],
        "followed_memory_ids": [], "refused_procedure_ids": [],
        "reuse_caused_failure": False, "stale_offered": [],
        "tokens_in": 18000, "tokens_out": 3000, "tool_calls": 10,
        "latency_seconds": 5.0, "human_interventions": 0, "unseen_task": False,
    }
    base.update(over)
    return base


def procs():
    return micro_pack.scoring_load_procedures(MICRO_FIXTURES)


class TestSurfaceJournal:
    def test_every_tool_call_is_logged_with_verdicts(self):
        s = mcp_surface.StubSurface(MICRO_FIXTURES)
        hits = s.search("refunds")
        assert hits
        s.get_procedure("refund-auto-v1")
        assert s.check_applicability("refund-auto-v1", {}) is False
        s.record_refusal("refund-auto-v1", "policy")
        tools = [(e["tool"], e.get("procedure_id") or e.get("domain"))
                 for e in s.journal()]
        assert tools == [
            ("search", "refunds"), ("get_procedure", "refund-auto-v1"),
            ("check_applicability", "refund-auto-v1"),
            ("record_refusal", "refund-auto-v1")]
        assert s.journal()[-2]["verdict"] is False

    def test_journal_returns_copies(self):
        s = mcp_surface.StubSurface(MICRO_FIXTURES)
        s.search("deps")
        s.journal().clear()
        assert len(s.journal()) == 1

    def test_ground_truth_stale_still_hidden_from_agents(self):
        s = mcp_surface.StubSurface(MICRO_FIXTURES)
        public = s.get_procedure("env-setup-v1")
        assert "stale" not in public and \
            "staleness_reason" not in public
        assert s.ground_truth_stale("env-setup-v1") is True


class TestRequirementChecks:
    def test_gate_consulted_true_and_false(self):
        j = [{"tool": "check_applicability", "procedure_id": "p1",
              "verdict": False}]
        req = {"id": "e", "type": "gate_consulted", "procedure_id": "p1"}
        assert micro_pack.check_requirement(req, episode(), j, procs())["satisfied"]
        other = dict(req, procedure_id="p2")
        assert not micro_pack.check_requirement(
            other, episode(), j, procs())["satisfied"]

    def test_refusal_recorded_requires_surface_AND_self_report_agreement(self):
        req = {"id": "e", "type": "refusal_recorded", "procedure_id": "p1"}
        both = micro_pack.check_requirement(
            req,
            episode(refused_procedure_ids=["p1"]),
            [{"tool": "record_refusal", "procedure_id": "p1"}], procs())
        assert both["satisfied"] and "surface_refusal=True" in both["detail"]
        # Self-reported refusal with no surface corroboration: contact exists
        # (so no waiver) but the trail assertion cannot be verified -> fail.
        unverified = micro_pack.check_requirement(
            req, episode(refused_procedure_ids=["p1"]), [], procs())
        assert not unverified["satisfied"] and not unverified["waived"]
        # Zero contact at all: waived, never failed.
        waived = micro_pack.check_requirement(req, episode(), [], procs())
        assert waived["waived"]

    def test_no_stale_execution_uses_ground_truth(self):
        req = {"id": "e", "type": "no_stale_execution"}
        clean = micro_pack.check_requirement(
            req, episode(reused_procedure_ids=["refund-review-v2"]), [{}],
            procs())
        assert clean["satisfied"] and clean["detail"] == "clean"
        tripped = micro_pack.check_requirement(
            req, episode(reused_procedure_ids=["refund-auto-v1"],
                         reuse_caused_failure=True), [{}], procs())
        assert not tripped["satisfied"] and "refund-auto-v1" in tripped["detail"]

    def test_reuse_verified_requires_used_verified_AND_resolved(self):
        req = {"id": "e", "type": "reuse_verified_procedure",
               "procedure_id": "refund-review-v2"}
        ok = micro_pack.check_requirement(
            req, episode(reused_procedure_ids=["refund-review-v2"]), [{}],
            procs())
        assert ok["satisfied"]
        unresolved = micro_pack.check_requirement(
            req, episode(reused_procedure_ids=["refund-review-v2"],
                         resolved=False), [{}], procs())
        assert not unresolved["satisfied"]

    def test_max_tokens_always_applies_even_without_contact(self):
        req = {"id": "e", "type": "max_tokens", "budget": 21000}
        v = micro_pack.check_requirement(req, episode(), [], procs())
        assert v["satisfied"] and not v["waived"]
        over = micro_pack.check_requirement(
            {"id": "e", "type": "max_tokens", "budget": 20000},
            episode(), [], procs())
        assert not over["satisfied"]
        assert "tokens_used=21000" in over["detail"]


class TestWaiverSemantics:
    def test_conventional_memory_does_not_count_as_substrate_contact(self):
        # Arm B's whole point: memory WITHOUT a trail. Trail requirements must
        # waive for it, never fail it.
        b_ep = episode(arm="B", followed_memory_ids=["rag-refunds-1"])
        req = {"id": "e", "type": "gate_consulted", "procedure_id": "x"}
        v = micro_pack.check_requirement(req, b_ep, [], procs())
        assert v["waived"] and v["detail"]

    def test_grade_scenario_pass_and_fail_paths(self):
        sc = {"scenario_id": "s", "archetype": "env_drift_staleness",
              "success_criteria": {"must_resolve": True},
              "evidence_requirements": [
                  {"id": "e1", "type": "no_stale_execution"}]}
        good = micro_pack.grade_scenario(
            {}, sc, episode(), [{"tool": "search", "domain": "env"}], procs())
        assert good["scenario_pass"] and good["failed_requirements"] == []
        bad_outcome = micro_pack.grade_scenario(
            {}, sc, episode(resolved=False),
            [{"tool": "search", "domain": "env"}], procs())
        assert bad_outcome["outcome_ok"] is False
        assert bad_outcome["scenario_pass"] is False


class TestRendering:
    def test_failed_requirement_ids_travel_with_the_verdict(self):
        verdicts = [micro_pack.grade_scenario(
            {}, {"scenario_id": "s1", "archetype": "pdf_to_sheet_pipeline",
                 "success_criteria": {"must_resolve": True},
                 "evidence_requirements": [
                     {"id": "cost-guard", "type": "max_tokens",
                      "budget": 1000}]},
            episode(), [], procs())]
        text = micro_pack.render_verdicts(verdicts)
        assert "FAIL" in text and "cost-guard" in text.split("verdict")[1]
        assert "NOT findings" in text

    def test_summarize_pack_counts_per_arm(self):
        verdicts = [
            {"scenario_id": "a", "archetype": "x", "arm": "A",
             "scenario_pass": True, "false_reuse": False},
            {"scenario_id": "b", "archetype": "x", "arm": "A",
             "scenario_pass": False, "false_reuse": True},
        ]
        detail = micro_pack.summarize_pack(verdicts)
        assert detail["by_arm"]["A"] == {"passes": 1, "n": 2,
                                         "false_reuse": 1}
        assert detail["n_verdicts"] == 2
