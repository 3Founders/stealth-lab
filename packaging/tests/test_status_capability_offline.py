"""
Capability-score tests for the status surface: hand-computed Wilson
lower-bound cases proven against the REAL backend engine
(app.services.procedure_extraction.capability.compute_capability) via
the status server's evidence->capability path. No database, no
reimplementation -- if the engine's D1 bands move, these names say what
the page will show.
"""

import pytest

pytest.importorskip("fastapi")

import stealthlab_connect as slc  # noqa: E402

slc.get_backend_root()

from stealthlab_connect import status_server  # noqa: E402


def ev_row(**over):
    row = {
        "id": None,
        "target_id": None,
        "target_version": 1,
        "evidence_type": "execution_result",
        "direction": "supports",
        "outcome_status": "success",
        "strength_score": 1.0,
        "strength_method": "recorded_outcome",
        "independence_group": None,
        "context_key": None,
        "failure_class": None,
        "t_valid": None,
    }
    row.update(over)
    return row


def stream(successes, failures, *, groups=(), contexts=()):
    rows = []
    for i in range(successes):
        rows.append(ev_row(
            context_key=contexts[i % len(contexts)] if contexts else f"c{i}",
            independence_group=groups[i % len(groups)] if groups else None,
        ))
    for i in range(failures):
        j = successes + i
        rows.append(ev_row(
            outcome_status="failure",
            context_key=contexts[j % len(contexts)] if contexts else f"c{j}",
            independence_group=groups[j % len(groups)] if groups else None,
        ))
    return rows


def test_no_evidence_is_level_0_unknown():
    cap = status_server.capability_from_evidence_rows([])
    assert cap["level"] == 0
    assert cap["level_label"] == "unknown"
    assert cap["routing"] == "refuse_reuse"
    assert cap["p_lower"] == 0.0 and cap["p_upper"] == 1.0  # maximum ignorance


def test_one_success_is_level_1_observed_and_refused():
    cap = status_server.capability_from_evidence_rows(stream(1, 0))
    assert cap["p_estimate"] == pytest.approx(0.206495, abs=1e-4)  # Wilson lower, n=1 s=1
    assert cap["level"] == 1
    assert cap["level_label"] == "observed"
    assert cap["routing"] == "refuse_reuse"


def test_95_of_100_three_groups_two_environments_is_level_4_offer():
    cap = status_server.capability_from_evidence_rows(
        stream(95, 5, groups=("g1", "g2", "g3"), contexts=("env-a", "env-b"))
    )
    assert cap["p_estimate"] == pytest.approx(0.888235, abs=1e-4)  # >= L4 band 0.85
    assert cap["independent_groups"] == 3
    assert cap["environments_held"] == ["env-a", "env-b"]
    assert cap["level"] == 4
    assert cap["level_label"] == "generalized"
    assert cap["routing"] == "offer_as_candidate"  # below ROUTE_AUTO_THRESHOLD 0.90


def test_single_environment_caps_the_same_stream_at_level_2():
    cap = status_server.capability_from_evidence_rows(
        stream(95, 5, groups=("g1", "g2", "g3"), contexts=("only-env",))
    )
    # band 4 fails the >=2-environments gate; level 3 fails the (honestly
    # unreported) verification-plan gate; level 2's independence gate holds.
    assert cap["level"] == 2
    assert cap["level_label"] == "reproduced"


def test_ungrouped_single_environment_evidence_cannot_climb_despite_perfect_record():
    cap = status_server.capability_from_evidence_rows(stream(100, 0, contexts=("only",)))
    assert cap["independent_groups"] == 0
    # Band 5 descends every gate: no review -> no L5, one environment -> no
    # L4, nothing stores verification plans -> no L3, zero explicit
    # independence groups -> no L2. Statistics alone never confer trust.
    assert cap["level"] == 1
    assert cap["level_label"] == "observed"
    # ...even though P itself would auto-route:
    assert cap["routing"] == "auto_route"


def test_perfect_grouped_two_environment_stream_stops_below_trusted_without_review():
    cap = status_server.capability_from_evidence_rows(
        stream(100, 0, groups=("g1", "g2"), contexts=("env-a", "env-b"))
    )
    assert cap["p_lower"] == pytest.approx(0.963007, abs=1e-4)  # >= L5 band 0.95
    assert cap["routing"] == "auto_route"
    # L5 requires completed_review, which nothing stores yet -> honest L4.
    assert cap["gates_reported"]["completed_review"] is False
    assert cap["level"] == 4
    assert cap["level_label"] == "generalized"


def test_failures_demote_a_stream_that_successes_raised():
    low = status_server.capability_from_evidence_rows(stream(20, 0, groups=("a", "b")))
    demoted = status_server.capability_from_evidence_rows(
        stream(20, 10, groups=("a", "b"), contexts=("e1", "e2"))
    )
    assert demoted["p_estimate"] < low["p_estimate"]
    assert demoted["success_count"] == 20 and demoted["evidence_count"] == 30
