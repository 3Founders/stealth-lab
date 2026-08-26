"""Band 1.9a proving tests -- evidence contracts, offline (no DB).

Covers:
  Appendix C #3   every verified procedure has evidence: the transition
                  gate refuses when the evidence view returns zero rows
                  of required types (None row, zero independent support,
                  malformed rows); the view itself is pinned statically
                  to live supporting execution_result/reproduction rows.
  Appendix C #12  capability is evidence-based, not model-brand-based:
                  identical outcome streams differing ONLY in producer
                  identity produce byte-identical stats-relevant rows,
                  and db/24's stats view SELECT reads no producer/brand
                  column at all.
  Appendix C #13  outcomes distinguishable from self-reports: a success
                  without explicit success criteria is refused at the
                  boundary AND unwritable at the engine ('{}' ban);
                  failures classify via §36's failure_class or nothing.

Plus the independence-group semantics (same-group rows collapse in the
view's counting expression; NULL = self-grouped; blank groups refused),
the append-only/tombstone freeze, and model round trips -- all by the
same method test_band1_7_plans.py established: run the contract logic
directly, and statically pin the DDL text it depends on (the DB
application itself is integration work -- board queue item 2).
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.execution.evidence import (
    DIRECTIONS,
    EVIDENCE_TYPES,
    FAILURE_CLASSES,
    OUTCOME_BEARING_TYPES,
    REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED,
    EvidenceViolation,
    assert_verified_requires_evidence,
    outcome_to_evidence,
    validate_evidence,
)
from app.models.evidence import Evidence

PROC_ID = uuid4()
PROC_ROW_ID = uuid4()


def _target(**overrides):
    ref = {"target_type": "procedure", "target_id": PROC_ROW_ID, "target_version": 3}
    ref.update(overrides)
    return ref


def _outcome(**overrides):
    kwargs = dict(
        evidence_type="execution_result",
        target=_target(),
        outcome_status="success",
        success_criteria={"predicate": "healthcheck returns 200"},
        context_key="repo_42:staging",
        extractor_version="outcome_recorder@1",
    )
    kwargs.update(overrides)
    return outcome_to_evidence(**kwargs)


# ------------------------------------------- Appendix C #3: verified gate


def test_gate_refuses_no_stats_row_at_all():
    """The literal Appendix C #3 wording: the view returning ZERO rows of
    required types is exactly 'no row for this procedure version'."""
    with pytest.raises(EvidenceViolation, match="zero rows of required types"):
        assert_verified_requires_evidence(None)


@pytest.mark.parametrize("row", [
    {"independent_supporting_required": 0},
    {"independent_supporting_required": "0"},
])
def test_gate_refuses_zero_independent_required_rows(row):
    with pytest.raises(EvidenceViolation, match="every verified procedure has evidence"):
        assert_verified_requires_evidence(row)


@pytest.mark.parametrize("count", [1, 4])
def test_gate_accepts_when_required_evidence_exists(count):
    assert_verified_requires_evidence({"independent_supporting_required": count})


def test_gate_demands_a_readable_count():
    with pytest.raises(EvidenceViolation, match="procedure_evidence_stats row verbatim"):
        assert_verified_requires_evidence({"something_else": 3})
    with pytest.raises(EvidenceViolation, match="readable"):
        assert_verified_requires_evidence({"independent_supporting_required": "many"})


def _ddl() -> str:
    p = Path(__file__).resolve().parents[1] / "db" / "24_evidence.sql"
    return p.read_text(encoding="utf-8")


def _view_sql() -> str:
    ddl = _ddl()
    # PG has no CREATE VIEW IF NOT EXISTS -- idempotency comes from
    # CREATE OR REPLACE (engine-verified fix, was a real chain-run failure).
    start = ddl.index("CREATE OR REPLACE VIEW procedure_evidence_stats AS")
    end = ddl.index(";", start)
    return ddl[start:end]


def test_ddl_view_counts_live_supporting_required_type_rows():
    sql = _view_sql()
    assert "e.t_invalid IS NULL" in sql, "retracted evidence must drop out of stats"
    assert "e.direction = 'supports'" in sql, "contradicting rows are not verification"
    required_types_clause = re.search(
        r"evidence_type IN \(([^)]*)\)", sql
    ).group(1)
    for t in REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED:
        assert f"'{t}'" in required_types_clause, t
    assert set(REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED) == set(OUTCOME_BEARING_TYPES), (
        "required types for verification and outcome-bearing types must agree"
    )


def test_ddl_python_config_matches_the_engine_vocabulary():
    """The gate's Python config and the migration's closed vocabularies
    must never drift apart silently."""
    ddl = _ddl()
    enum_block = re.search(
        r"CREATE TYPE evidence_kind AS ENUM \((.*?)\);", ddl, re.DOTALL
    ).group(1)
    db_types = re.findall(r"'(\w+)'", enum_block)
    assert tuple(db_types) == EVIDENCE_TYPES, "spec §11's nine types, both sides"

    failure_block = re.search(r"failure_class IN \((.*?)\)\)", ddl, re.DOTALL).group(1)
    db_classes = re.findall(r"'(\w+)'", failure_block)
    assert sorted(db_classes) == sorted(FAILURE_CLASSES), "§36's causes + false_reuse"


# --------------------------------- Appendix C #12: no brand in computation


def test_identical_streams_from_different_brands_are_identical():
    """Two producers whose outcome streams differ ONLY in who ran them
    must produce evidence rows that are indistinguishable to every
    statistic -- brand lives in created_by provenance, nowhere else."""
    def stream(created_by):
        rows = []
        for i, status in enumerate(["success"] * 9 + ["failure"]):
            kwargs = dict(
                target=_target(),
                outcome_status=status,
                context_key=f"ctx_{i % 3}",
                created_by=created_by,
                success_criteria={"predicate": "tests pass"} if status == "success" else None,
                failure_class="environment_changed" if status == "failure" else None,
            )
            rows.append(_outcome(**kwargs).to_row())
        return rows

    a, b = stream("model:claude-x@1"), stream("model:gpt-y@2")

    # The columns db/24's stats view reads -- and NOTHING else.
    STATS_COLUMNS = (
        "evidence_type", "target_type", "target_id", "target_version",
        "direction", "outcome_status", "success_criteria", "failure_class",
        "independence_group", "context_key",
    )
    for ra, rb in zip(a, b):
        assert {k: ra[k] for k in STATS_COLUMNS} == {k: rb[k] for k in STATS_COLUMNS}
    assert a[0]["created_by"] != b[0]["created_by"], "sanity: streams really differ in brand"


def test_outcome_builder_takes_no_brand_argument():
    import inspect

    params = inspect.signature(outcome_to_evidence).parameters
    for forbidden in ("brand", "model", "model_id", "producer"):
        assert forbidden not in params, forbidden


def test_ddl_stats_view_reads_no_producer_identity_column():
    sql = _view_sql()
    for forbidden in ("created_by", "owner_id", "brand", "model_id", "extractor_version"):
        assert forbidden not in sql, (
            f"stats view must not read producer identity column {forbidden!r} "
            "(invariant #12)"
        )


# ------------------------- Appendix C #13: outcomes vs model self-reports


def test_bare_asserted_success_refused_empty_criteria():
    with pytest.raises(EvidenceViolation, match="bare model-asserted"):
        _outcome(success_criteria={})


def test_bare_asserted_success_refused_non_criteria_payloads():
    with pytest.raises(EvidenceViolation, match="explicit success criteria"):
        _outcome(success_criteria={"note": "the model said it worked"})
    with pytest.raises(EvidenceViolation, match="explicit success criteria"):
        _outcome(success_criteria={"predicate": "   ", "metrics": {}})


def test_explained_successes_accepted():
    assert _outcome(success_criteria={"predicate": "exit code == 0"}) is not None
    assert _outcome(success_criteria={"metrics": {"p95_ms": 812}}) is not None


def test_engine_bans_the_unexplained_success_too():
    """Boundary messages are only half the teeth; direct-SQL writers face
    the '{}' ban as a named CHECK constraint."""
    chk = re.search(
        r"conname = 'evidence_success_criteria_chk'\s*\)\s*THEN\s*"
        r"ALTER TABLE evidence ADD CONSTRAINT evidence_success_criteria_chk\s*"
        r"CHECK \((.*?)\);",
        _ddl(),
        re.DOTALL,
    )
    assert chk, "named CHECK missing"
    check_body = chk.group(1)
    assert "'{}'::jsonb" in check_body
    assert "outcome_status IS DISTINCT FROM 'success'" in check_body


def test_failure_rows_classify_but_never_carry_criteria():
    with pytest.raises(EvidenceViolation, match="failure_class instead"):
        _outcome(outcome_status="failure", failure_class=None,
                 success_criteria={"predicate": "x"})
    assert _outcome(outcome_status="failure", failure_class="input_abnormal",
                    success_criteria=None) is not None


def test_failure_class_is_null_until_a_failure():
    with pytest.raises(EvidenceViolation, match="null until a failure"):
        _outcome(failure_class="external_failure")  # outcome_status='success'
    with pytest.raises(EvidenceViolation, match="unknown failure_class"):
        _outcome(outcome_status="failure", failure_class="vibes")


# ------------------------------------------------- outcome shape discipline


def test_execution_results_must_state_terminal_status():
    with pytest.raises(EvidenceViolation, match="terminal status"):
        _outcome(outcome_status=None, direction="supports")


def test_non_outcome_types_may_not_carry_outcomes():
    with pytest.raises(EvidenceViolation, match="carries no execution outcome"):
        validate_evidence(
            evidence_type="human_review",
            target=_target(target_type="claim", target_version=None),
            direction="supports",
            strength_score=0.8,
            strength_method="expert",
            outcome_status="success",
            success_criteria={"predicate": "looks right"},
        )


def test_needs_rework_states_direction_explicitly():
    with pytest.raises(EvidenceViolation, match="neither support nor contradiction"):
        _outcome(outcome_status="needs_rework")
    row = _outcome(outcome_status="needs_rework", direction="contradicts",
                   success_criteria=None)
    assert row.direction == "contradicts"


def test_default_direction_follows_the_honest_arrow():
    assert _outcome().direction == "supports"
    failed = _outcome(outcome_status="failure", failure_class="implementation_wrong",
                      success_criteria=None)
    assert failed.direction == "contradicts"


# ------------------------------------------------------------ independence


def test_blank_independence_group_refused_null_allowed():
    with pytest.raises(EvidenceViolation, match="self-grouped"):
        _outcome(independence_group="   ")
    assert _outcome(independence_group=None) is not None
    assert _outcome(independence_group="run-77") is not None


def test_ddl_counts_distinct_groups_not_raw_rows():
    """Same-group evidence never counts as independent (§11): the view's
    counting expression must collapse groups, while attempts stays raw."""
    sql = _view_sql()
    collapse = "COUNT(DISTINCT COALESCE(e.independence_group, e.id::text))"
    assert sql.lower().count(collapse.lower()) >= 3, (
        "independent counts must dedupe by COALESCE(independence_group, id)"
    )
    assert "count(*)                    AS attempts" in sql
    chk = re.search(
        r"conname = 'evidence_independence_group_chk'\s*\)\s*THEN.*?"
        r"CHECK \(independence_group IS NULL OR length\(trim\(independence_group\)\) > 0\)",
        _ddl(), re.DOTALL,
    )
    assert chk, "blank-group engine guard missing"


# ------------------------------------------------------- reference + vocab


def test_unversioned_procedure_target_refused():
    with pytest.raises(EvidenceViolation, match="exact"):
        _outcome(target=_target(target_version=None))
    # claims are row-id-addressed; implementations have no chain yet
    assert _outcome(target=_target(target_type="claim", target_version=None)) is not None


def test_unknown_vocabularies_refused_with_valid_options():
    with pytest.raises(EvidenceViolation, match="not 'gut_feeling'"):
        _outcome(evidence_type="gut_feeling")
    with pytest.raises(EvidenceViolation, match="unknown evidence_type"):
        validate_evidence(
            evidence_type="gut_feeling",
            target=_target(),
            direction="supports",
            strength_score=1.0,
            strength_method="m",
        )
    with pytest.raises(EvidenceViolation, match="unknown direction"):
        _outcome(direction="sideways")
    with pytest.raises(EvidenceViolation, match="unknown outcome_status"):
        _outcome(outcome_status="vibes", direction="supports")


def test_strength_pair_required_and_bounded():
    with pytest.raises(EvidenceViolation, match="strength"):
        _outcome(strength_score=1.5)
    with pytest.raises(EvidenceViolation, match="strength"):
        _outcome(strength_method="")
    with pytest.raises(EvidenceViolation, match="strength"):
        _outcome(strength_method=None)


def test_unknown_fields_and_bad_visibility_refused():
    with pytest.raises(EvidenceViolation, match="unknown field"):
        validate_evidence(
            evidence_type="document",
            target=_target(target_type="claim", target_version=None),
            direction="supports",
            strength_score=1.0,
            strength_method="doc",
            brnad="typo-guard",
        )
    with pytest.raises(EvidenceViolation, match="visibility"):
        _outcome(visibility="internal")


def test_extractor_stamp_optional_but_never_blank():
    assert _outcome(extractor_version=None).extractor_version is None
    with pytest.raises(EvidenceViolation, match="extractor_version"):
        _outcome(extractor_version="  ")


def test_witness_type_round_trip_without_outcome_baggage():
    review = validate_evidence(
        evidence_type="human_review",
        target={"target_type": "claim", "target_id": uuid4()},
        direction="supports",
        strength_score=0.8,
        strength_method="domain_expert_review",
        created_by="alice",
    )
    assert review.outcome_status is None
    assert review.success_criteria == {}
    assert Evidence.from_row(review.to_row()) == review


def test_outcome_row_model_round_trip():
    original = _outcome()
    rt = Evidence.from_row(original.to_row())
    assert rt == original
    assert rt.strength.method == "recorded_outcome"


# ----------------------------------------------- append-only birth discipline


def test_ddl_freezes_evidence_except_tombstone():
    ddl = _ddl()
    assert "tg_evidence_append_only" in ddl
    assert "sl_evidence_only_tombstone" in ddl
    trigger = re.search(
        r"CREATE TRIGGER tg_evidence_append_only\s+"
        r"BEFORE UPDATE OR DELETE ON evidence\s+"
        r"FOR EACH ROW EXECUTE FUNCTION sl_evidence_only_tombstone\(\);",
        ddl,
    )
    assert trigger, "trigger must cover BOTH update and delete"
    fn = ddl[ddl.index("sl_evidence_only_tombstone() RETURNS trigger"):]
    body = fn[: fn.index("$$ LANGUAGE")]
    assert "TG_OP = 'DELETE'" in body
    assert "OLD.t_invalid IS NOT NULL" in body
    assert "only the t_invalid retraction tombstone may change" in body
    # tombstone comparison enumerates real columns -- a NEW column must
    # be added there explicitly, so future edits cannot silently widen
    # what tombstones may touch
    assert "NEW.independence_group" in body and "OLD.independence_group" in body


def test_migration_has_no_backfills_and_next_number_is_free():
    ddl = _ddl()
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", ddl, re.IGNORECASE), (
        "fresh-start ruling: no data backfills (tombstone-trigger text "
        "mentions UPDATE; SET-assignments would too)"
    )
    assert "23 was highest" in ddl


def test_scope_check_present_for_evidence():
    assert "scope_type_chk_evidence" in _ddl()
