"""
Pure, DB-free coverage for app/services/implementation_goals.py --
the deterministic goal/verification-contract classifier wired into
skill_ingestion.py::_persist_package_relations (migration 80's real first
writer). No client, no pool: every function here is a pure string/dict
transform, so these tests need nothing else.
"""
from app.services.implementation_goals import (
    VERIFICATION_CONTRACT_TYPES,
    classify_skill_package_script,
    default_verification_contract,
    normalize_goal_from_path,
)


def test_normalize_goal_from_path_recognizes_test_scripts():
    assert normalize_goal_from_path("scripts/run_tests.py") == "test_execution"
    assert normalize_goal_from_path("scripts/test_auth.sh") == "test_execution"


def test_normalize_goal_from_path_recognizes_verification_scripts():
    assert normalize_goal_from_path("scripts/check_schema.py") == "verification"
    assert normalize_goal_from_path("scripts/verify_migration.py") == "verification"
    assert normalize_goal_from_path("scripts/validate_config.py") == "verification"


def test_normalize_goal_from_path_recognizes_static_analysis_scripts():
    assert normalize_goal_from_path("scripts/scan_deps.py") == "static_analysis"
    assert normalize_goal_from_path("scripts/audit_secrets.py") == "static_analysis"
    assert normalize_goal_from_path("scripts/lint.sh") == "static_analysis"


def test_normalize_goal_from_path_ignores_directory_segments():
    """`scripts/` itself must never contribute a goal -- only the
    basename does. This is the directive's own "do not encode environment
    into the goal name" rule applied to path segments."""
    assert normalize_goal_from_path("scripts/helper.py") is None


def test_normalize_goal_from_path_returns_none_when_nothing_matches():
    """Never a guess -- an unrecognized filename gets no goal at all,
    rather than a fabricated default."""
    assert normalize_goal_from_path("skills/foo/scripts/helper.py") is None
    assert normalize_goal_from_path("bar.py") is None


def test_default_verification_contract_for_deterministic_kind():
    contract = default_verification_contract("deterministic")
    assert contract == {"type": "deterministic", "check": "exit_code_zero"}
    assert contract["type"] in VERIFICATION_CONTRACT_TYPES


def test_default_verification_contract_returns_none_for_unknown_kind():
    """No real evidence for a human/llm/api-backed mechanism's contract
    exists at this call site -- honestly None, never a fabricated guess."""
    assert default_verification_contract("frontier_llm") is None
    assert default_verification_contract("human") is None


def test_classify_skill_package_script_heuristic_when_goal_recognized():
    result = classify_skill_package_script("scripts/check_schema.py")
    assert result == {
        "goal": "verification",
        "goal_spec": None,
        "expected_outcome": None,
        "verification_contract": {"type": "deterministic", "check": "exit_code_zero"},
        "classification": "heuristic",
    }


def test_classify_skill_package_script_unclassified_when_nothing_matches():
    """Verification contract is still populated (a deterministic script's
    honest floor, independent of whether a goal was found), but
    classification stays 'unclassified' and goal stays None -- no
    fabrication just because SOME field could be filled in."""
    result = classify_skill_package_script("scripts/helper.py")
    assert result["goal"] is None
    assert result["classification"] == "unclassified"
    assert result["expected_outcome"] is None
    assert result["verification_contract"] == {"type": "deterministic", "check": "exit_code_zero"}
