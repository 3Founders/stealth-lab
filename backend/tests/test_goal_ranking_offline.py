"""Offline proving tests for centralized explainable ranking."""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from scipy.stats import beta

from app.economy import ranking as economy_ranking
from app.services import goal_ranking as goal_ranking_module
from app.services.access import AccessScope
from app.services.goal_ranking import (
    GoalRankingService,
    ProcedureRankingService,
    bayesian_reliability,
    build_goal_ranking_query,
    canonical_outcome_evidence,
    demand_factor_from_commitments,
    freshness_percentile,
    is_canonical_outcome_evidence,
    public_goal_ranking,
    rank_goal_candidates,
    rank_procedure_candidates,
    resolved_usefulness_score,
    score_goal_candidate,
    score_procedure_candidate,
    unmet_need_from_procedure_coverage,
    unresolved_opportunity_score,
)
from app.services.procedures import OUTCOME_WRITER_STAMP


NOW = datetime.now(timezone.utc)


def _run(value):
    return asyncio.run(value)


def _procedure(row_id: str, *, version: int = 1, **overrides: Any) -> dict[str, Any]:
    row = {
        "id": row_id,
        "procedure_id": row_id,
        "version": version,
        "name": row_id,
        "goal": "complete the requested outcome",
        "display_name": row_id,
        "display_description": "description",
        "preconditions": [],
        "scope": {},
        "exclusions": [],
        "invariants": [],
        "verification_state": "candidate",
        "staleness": "fresh",
        "availability": "active",
        "approval_status": "proposed",
        "verification_stats": {},
        "t_valid": NOW - timedelta(days=1),
        "t_invalid": None,
    }
    row.update(overrides)
    return row


def _evidence(
    row_id: str,
    *,
    target_id: str = "procedure-1",
    version: int = 1,
    status: str = "success",
    writer: str = OUTCOME_WRITER_STAMP,
    **overrides: Any,
) -> dict[str, Any]:
    row = {
        "id": row_id,
        "evidence_type": "execution_result",
        "target_type": "procedure",
        "target_id": target_id,
        "target_version": version,
        "direction": "supports" if status == "success" else "contradicts",
        "outcome_status": status,
        "created_by": writer,
        "independence_group": row_id,
        "context_key": row_id,
        "t_created": NOW,
        "t_invalid": None,
    }
    row.update(overrides)
    return row


def test_bayesian_statistic_is_exact_jeffreys_lower_credible_bound():
    result = bayesian_reliability(1, 0)
    expected = float(beta.ppf(0.025, 2, 1))
    assert result["prior"] == "Jeffreys Beta(1,1)"
    assert result["method"] == "scipy.stats.beta.ppf"
    assert result["credible_lower_bound"] == pytest.approx(expected)
    assert result["credible_lower_95"] == pytest.approx(expected)


def test_bayesian_order_places_one_of_one_below_ninety_five_of_one_hundred():
    one = score_procedure_candidate(
        _procedure("one"), [_evidence("one-evidence", target_id="one")],
    )
    many = score_procedure_candidate(
        _procedure("many"),
        [_evidence(f"success-{index}", target_id="many") for index in range(95)]
        + [_evidence(f"failure-{index}", target_id="many", status="failure") for index in range(5)],
    )
    assert one["credible_lower_bound"] < many["credible_lower_bound"]
    assert one["score"] < many["score"]
    assert one["raw"]["successes"] == 1
    assert many["raw"]["successes"] == 95
    assert many["raw"]["failures"] == 5


def test_only_canonical_trusted_exact_version_evidence_is_retained():
    rows = [
        _evidence("trusted-success"),
        _evidence("trusted-failure", status="failure"),
        _evidence("self-report", writer="record_execution_outcome@1:self_report"),
        _evidence("empty-criteria", success_criteria={}),
        _evidence("wrong-direction", direction="contradicts"),
        _evidence("document", evidence_type="document"),
        _evidence("derivative", metadata={"derived_from": "another-row"}),
        _evidence("wrong-version", version=2),
        _evidence("retracted", t_invalid=NOW),
        _evidence("trusted-success"),
    ]
    retained = canonical_outcome_evidence(
        rows, procedure_row_id="procedure-1", procedure_version=1,
    )
    assert [row["id"] for row in retained] == ["trusted-success", "trusted-failure"]
    assert all(is_canonical_outcome_evidence(row) for row in retained)
    assert not is_canonical_outcome_evidence(rows[2])
    stats = score_procedure_candidate(_procedure("procedure-1"), rows)["reliability"]
    assert stats["successes"] == 1
    assert stats["failures"] == 1
    assert stats["attempts"] == 2
    assert stats["independent_evidence"] == 2


def test_unknown_is_cold_start_and_cannot_receive_a_quality_score():
    result = score_procedure_candidate(_procedure("unknown"), [])
    assert result["lane"] == "promising_needs_evidence"
    assert result["bucket"] == "needs_evidence"
    assert result["score"] == 0.0
    assert result["quality_score"] == 0.0
    assert result["raw"]["attempts"] == 0
    assert "promising_needs_evidence" in result["explanation_codes"]


def test_procedure_ranking_uses_reliability_volume_independence_then_stable_ties():
    one = _procedure("z-one")
    many = _procedure("a-many")
    tied_a = _procedure("b-tied")
    tied_b = _procedure("c-tied")
    ranked = rank_procedure_candidates(
        [tied_b, one, many, tied_a],
        {
            "z-one": [_evidence("one", target_id="z-one")],
            "a-many": [_evidence(f"many-{index}", target_id="a-many") for index in range(95)]
            + [_evidence(f"bad-{index}", target_id="a-many", status="failure") for index in range(5)],
            "b-tied": [_evidence("tied-b", target_id="b-tied")],
            "c-tied": [_evidence("tied-c", target_id="c-tied")],
        },
    )
    assert [item["procedure_row_id"] for item in ranked] == [
        "a-many", "b-tied", "c-tied", "z-one",
    ]
    assert [item["rank"] for item in ranked] == [1, 2, 3, 4]
    assert ranked[1]["procedure_row_id"] < ranked[2]["procedure_row_id"]


def test_context_match_is_explanatory_not_a_context_multiplier():
    general = _procedure("general")
    specialist = _procedure("specialist", applicability_summary="kubernetes clusters")
    ranked = rank_procedure_candidates(
        [general, specialist],
        {"general": [_evidence("general")], "specialist": [_evidence("specialist")]},
        context_key="kubernetes",
    )
    by_id = {item["procedure_row_id"]: item for item in ranked}
    assert by_id["specialist"]["context_matched"] is True
    assert by_id["general"]["context_matched"] is False
    assert by_id["specialist"]["score"] == by_id["general"]["score"]


class _ProcedurePool:
    def __init__(self, procedures: list[dict[str, Any]], evidence: list[dict[str, Any]]):
        self.procedures = procedures
        self.evidence = evidence
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *params: Any):
        self.calls.append((" ".join(sql.split()), params))
        if "FROM evidence" in sql or "evidence_aggregate" in sql:
            return self.evidence
        return self.procedures


def test_procedure_service_excludes_stale_disabled_and_inapplicable_candidates():
    procedures = [
        _procedure("good"),
        _procedure("stale", staleness="stale"),
        _procedure("disabled", availability="disabled"),
        _procedure("inapplicable"),
    ]
    evidence = [_evidence("good-evidence", target_id="good")]

    async def checker(pool, procedure, **kwargs):
        return procedure["id"] != "inapplicable"

    pool = _ProcedurePool(procedures, evidence)
    result = _run(ProcedureRankingService(
        pool,
        scope=AccessScope.unrestricted(),
        applicability_checker=checker,
    ).rank(["good", "stale", "disabled", "inapplicable"]))
    assert [item["procedure_row_id"] for item in result] == ["good"]
    assert result[0]["raw"]["attempts"] == 1


def test_procedure_service_preserves_exact_requested_version_when_stable_handle_is_also_present():
    procedures = [
        _procedure("row-v1", version=1, procedure_id="stable"),
        _procedure("row-v2", version=2, procedure_id="stable"),
    ]
    evidence = [
        _evidence("v1", target_id="row-v1", version=1),
        _evidence("v2", target_id="row-v2", version=2),
    ]
    pool = _ProcedurePool(procedures, evidence)
    result = _run(ProcedureRankingService(
        pool,
        applicability_checker=lambda pool, procedure, **kwargs: True,
    ).rank(["row-v1", "stable"]))
    ids = {item["procedure_row_id"]: item for item in result}
    assert set(ids) == {"row-v1"}
    assert ids["row-v1"]["version"] == 1
    assert ids["row-v1"]["raw"]["attempts"] == 1


def test_demand_is_neutral_without_commitments_and_credits_are_not_a_demand_signal():
    factor, available, signals = demand_factor_from_commitments()
    assert factor == 1.0
    assert available is False
    assert signals["credits_used_as_demand"] is False
    with_ledger = score_goal_candidate({
        "goal_id": "goal-1",
        "credit_ledger_rows_observed": True,
        "metadata": {},
    })
    without_ledger = score_goal_candidate({"goal_id": "goal-1", "metadata": {}})
    assert with_ledger["score"] == without_ledger["score"]
    assert "demand_unavailable" in with_ledger["explanation_codes"]
    assert "app.economy.credits" not in inspect.getsource(goal_ranking_module)


def test_unmet_need_uses_strongest_credible_procedure_coverage():
    empty, empty_signals = unmet_need_from_procedure_coverage([])
    assert empty == 1.0
    assert empty_signals["credible_procedure_count"] == 0
    strong = {
        "procedure_row_id": "strong",
        "lane": "candidate",
        "eligible": True,
        "credible_lower_bound": 0.8,
        "raw": {"attempts": 100},
    }
    need, signals = unmet_need_from_procedure_coverage([
        strong,
        {"lane": "promising_needs_evidence", "credible_lower_bound": 0.99},
    ])
    assert need == pytest.approx(0.2)
    assert signals["strongest_procedure"] == "strong"


def test_unresolved_opportunity_is_the_exact_four_factor_product():
    assert unresolved_opportunity_score(0.8, 0.5, 0.4, 0.25) == pytest.approx(0.04)
    assert unresolved_opportunity_score(None, 0.5, None, 0.25) == pytest.approx(0.125)
    assert unresolved_opportunity_score(0, 1, 1, 1) == 0.0


def test_resolved_usefulness_combines_signals_by_product_and_retains_raw_counts():
    result = resolved_usefulness_score({
        "successes": 8,
        "failures": 2,
        "attempts": 10,
        "independent_contexts": 3,
        "verified_independent_usage": 4,
        "independent_users": 2,
        "latest_activity_at": NOW,
    })
    expected = float(beta.ppf(0.025, 9, 3))
    assert result["score"] == pytest.approx(expected)
    assert result["raw"]["successes"] == 8
    assert result["raw"]["verified_independent_usage"] == 4
    assert result["method"].startswith("no-weight product")


def test_freshness_is_scale_free_percentile_not_half_life():
    values = [NOW - timedelta(days=3650), NOW]
    assert freshness_percentile(values) == [0.0, 1.0]
    assert freshness_percentile([None, NOW]) == [1.0, 1.0]


def test_goal_resolution_uses_resolved_at_only():
    result = score_goal_candidate({
        "goal_id": "legacy-solved-label",
        "status": "solved",
        "resolved_at": None,
    })
    assert result["state"] == "unresolved"
    assert result["resolved_at"] is None
    assert public_goal_ranking(result)["state"] == "unresolved"


def test_goal_ranking_does_not_treat_user_metadata_as_tractability():
    untrusted = score_goal_candidate({
        "goal_id": "metadata-tractability",
        "metadata": {"tractability": 0.0},
    })
    assert untrusted["signals"]["tractability"] == 1.0
    assert "tractability_unavailable" in untrusted["explanation_codes"]

    scalar = score_goal_candidate({
        "goal_id": "scalar-tractability",
        "tractability": 0.1,
    })
    assert scalar["signals"]["tractability"] == 1.0

    trusted = score_goal_candidate({
        "goal_id": "trusted-tractability",
        "tractability": {
            "normalized": 0.4,
            "source": "structured_assessment",
            "trusted": True,
        },
    })
    assert trusted["signals"]["tractability"] == pytest.approx(0.4)


def test_public_goal_ranking_has_the_frontend_contract():
    raw = score_goal_candidate({
        "goal_id": "contract-goal",
        "resolved_at": None,
        "successes": 2,
        "failures": 1,
    })
    public = public_goal_ranking(raw)
    assert set(public) == {"score", "state", "signals", "explanation"}
    assert public["state"] == "unresolved"
    assert isinstance(public["explanation"], str)
    assert all(set(signal) == {"label", "value", "available"} for signal in public["signals"].values())
    assert all(signal["value"] is None or isinstance(signal["value"], (int, float)) for signal in public["signals"].values())


def test_goal_ranks_are_recalculated_per_resolution_state():
    ranked = rank_goal_candidates([
        {
            "goal_id": "unresolved-one",
            "resolved_at": None,
            "normalized_demand": 0.9,
        },
        {
            "goal_id": "resolved-one",
            "resolved_at": NOW,
            "successes": 8,
            "failures": 2,
            "attempts": 10,
            "independent_contexts": 2,
            "verified_independent_usage": 1,
            "latest_activity_at": NOW,
        },
        {
            "goal_id": "unresolved-two",
            "resolved_at": None,
            "normalized_demand": 0.8,
        },
        {
            "goal_id": "resolved-two",
            "resolved_at": NOW,
            "successes": 4,
            "failures": 1,
            "attempts": 5,
            "independent_contexts": 1,
            "verified_independent_usage": 1,
            "latest_activity_at": NOW,
        },
    ])
    by_id = {item["goal_id"]: item for item in ranked}
    assert by_id["unresolved-one"]["rank"] == 1
    assert by_id["unresolved-two"]["rank"] == 2
    assert by_id["resolved-one"]["rank"] == 1
    assert by_id["resolved-two"]["rank"] == 2
    assert by_id["unresolved-one"]["of"] == 2
    assert by_id["resolved-one"]["of"] == 2


def test_goal_query_is_bounded_aggregated_exact_version_and_does_not_use_credits_amounts():
    sql, params = build_goal_ranking_query(
        goal_ids=["00000000-0000-4000-8000-000000000001"],
        limit=7,
        scope=AccessScope.anonymous(),
    )
    normalized = " ".join(sql.split())
    assert "LIMIT $" in normalized
    assert "GROUP BY" in normalized
    assert "e.target_version = pb.procedure_version" in normalized
    assert "e.created_by = $" in normalized
    assert "credit_ledger_events" in normalized
    assert "credit_balances" not in normalized
    assert "standing_score" not in normalized
    assert "amount" not in normalized
    assert params[-1] == 7
    assert params[0] == ["00000000-0000-4000-8000-000000000001"]


def test_goal_service_shapes_sql_aggregates_and_marks_ledger_as_non_commitment():
    row = {
        "goal_id": "goal-1",
        "canonical_name": "A goal",
        "status": "candidate",
        "resolved_at": None,
        "metadata": {},
        "t_created": NOW,
        "procedure_row_id": "proc-1",
        "procedure_id": "proc-1",
        "procedure_version": 1,
        "successes": 3,
        "failures": 1,
        "attempts": 4,
        "contexts": 2,
        "independent_evidence": 2,
        "verified_independent_usage": 1,
        "independent_users": 1,
        "latest_evidence_at": NOW,
        "credit_ledger_rows_observed": True,
    }
    result = _run(GoalRankingService(
        _ProcedurePool([], [row]),
    ).rank(["goal-1"]))
    assert len(result) == 1
    assert result[0]["state"] == "unresolved"
    assert result[0]["credit_ledger_is_not_commitment"] is True
    assert result[0]["raw"]["successes"] == 3
    assert "demand_unavailable" in result[0]["explanation_codes"]


def test_wilson_fields_remain_available_without_dranking_bayesian_score():
    from app.services.procedure_extraction.capability import wilson_interval

    result = score_procedure_candidate(
        _procedure("wilson"),
        [_evidence("success", target_id="wilson"), _evidence("failure", target_id="wilson", status="failure")],
    )
    expected_lower, expected_upper = wilson_interval(1, 2)
    assert result["wilson_lower_bound"] == pytest.approx(expected_lower)
    assert result["wilson_upper_bound"] == pytest.approx(expected_upper)
    assert result["p_lower"] == pytest.approx(expected_lower)
    assert result["credible_lower_bound"] == pytest.approx(float(beta.ppf(0.025, 2, 2)))
    assert result["score"] == result["credible_lower_bound"]


def test_pure_procedure_ranking_rejects_structural_ineligibility():
    ranked = rank_procedure_candidates([
        _procedure("stale", staleness="stale"),
        _procedure("disabled", availability="disabled"),
        _procedure("eligible"),
    ], {"eligible": [_evidence("eligible-evidence", target_id="eligible")]})
    assert [item["procedure_row_id"] for item in ranked] == ["eligible"]


def test_resolved_goal_uses_aggregated_procedure_contexts():
    result = score_goal_candidate(
        {
            "goal_id": "resolved-goal",
            "resolved_at": NOW,
            "successes": 8,
            "failures": 2,
            "attempts": 10,
            "verified_independent_usage": 2,
            "latest_activity_at": NOW,
        },
        procedure_scores=[{
            "procedure_row_id": "proc",
            "lane": "candidate",
            "eligible": True,
            "credible_lower_bound": float(beta.ppf(0.025, 9, 3)),
            "raw": {"successes": 8, "failures": 2, "attempts": 10, "contexts": 2},
        }],
    )
    assert result["state"] == "resolved"
    assert result["signals"]["context_factor"] == 1.0
    assert result["raw"]["independent_contexts"] == 2


def test_economy_adapter_calls_central_service(monkeypatch):
    calls: list[tuple[Any, ...]] = []

    class FakeService:
        def __init__(self, pool, **kwargs):
            calls.append(("init", pool, kwargs))

        async def rank(self, ids, **kwargs):
            calls.append(("rank", tuple(ids), kwargs))
            return [{"procedure_row_id": "central"}]

    monkeypatch.setattr(economy_ranking, "ProcedureRankingService", FakeService)
    result = _run(economy_ranking.rank_procedures_for_goal(
        object(), ["p1"], scope=AccessScope.unrestricted(), context_key="ctx",
    ))
    assert result == [{"procedure_row_id": "central"}]
    assert calls[-1][0] == "rank"
    assert calls[-1][1] == ("p1",)
