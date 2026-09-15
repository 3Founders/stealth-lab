"""
Pure, DB-free coverage for app/services/implementation_goals.py --
the deterministic-first, LLM-fallback goal/verification-contract
classifier wired into skill_ingestion.py::_persist_package_relations
(migration 80's real first writer). The deterministic-path tests need
nothing at all; the LLM-path tests use a scripted FakeClient, the same
convention test_strategies_offline.py already established for
GroundedHybridExtractor.
"""
import asyncio
import types

import pytest

from app.services.implementation_goals import (
    VERIFICATION_CONTRACT_TYPES,
    ImplementationClassificationTransientFailure,
    classify_skill_package_script,
    default_verification_contract,
    normalize_goal_from_path,
)


def _run(coro):
    return asyncio.run(coro)


class FakeClient:
    def __init__(self, script=None, raises=False):
        self.script = list(script or [])
        self.requests = []
        self._raises = raises
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        if self._raises:
            raise self._raises if isinstance(self._raises, Exception) else RuntimeError("upstream call failed")
        content = self.script.pop(0) if self.script else '{"abstain": true}'
        msg = types.SimpleNamespace(content=content)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])


# --- deterministic path (no client involved at all) ---

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
    """A recognized filename short-circuits BEFORE any client is touched
    -- no client is even passed here, proving the deterministic path
    never needs one."""
    result = _run(classify_skill_package_script("scripts/check_schema.py"))
    assert result == {
        "goal": "verification",
        "goal_spec": None,
        "expected_outcome": None,
        "verification_contract": {"type": "deterministic", "check": "exit_code_zero"},
        "classification": "heuristic",
    }


def test_classify_skill_package_script_unclassified_with_no_client():
    """No filename match AND no client configured -- stays 'unclassified',
    never blocks on a call that was never going to happen."""
    result = _run(classify_skill_package_script("scripts/helper.py"))
    assert result["goal"] is None
    assert result["classification"] == "unclassified"
    assert result["expected_outcome"] is None
    assert result["verification_contract"] == {"type": "deterministic", "check": "exit_code_zero"}


# --- LLM fallback path (only reached when the deterministic pass finds nothing) ---

def test_classify_skill_package_script_llm_classified_on_success():
    client = FakeClient([
        '{"goal": "data_migration_check", "expected_outcome": "the target schema matches the '
        'declared migration state"}',
    ])
    result = _run(classify_skill_package_script(
        "scripts/helper.py", client=client, model="a-model",
        skill_name="schema-guard", skill_purpose="Keep migrations consistent.",
        step_text="Run scripts/helper.py before committing.",
    ))
    assert result["goal"] == "data_migration_check"
    assert result["expected_outcome"] == "the target schema matches the declared migration state"
    assert result["classification"] == "llm_classified"
    assert result["verification_contract"] == {"type": "deterministic", "check": "exit_code_zero"}
    assert len(client.requests) == 1
    assert client.requests[0]["model"] == "a-model"
    assert "schema-guard" in client.requests[0]["messages"][1]["content"]


def test_classify_skill_package_script_deterministic_match_skips_the_client_entirely():
    """The client is never even called when the filename already matched
    -- proving §10's own "never use an LLM to rediscover deterministic
    structure unnecessarily" rule is actually honored, not just claimed."""
    client = FakeClient(['{"goal": "wrong", "expected_outcome": "wrong"}'])
    result = _run(classify_skill_package_script("scripts/check_schema.py", client=client))
    assert result["goal"] == "verification"
    assert result["classification"] == "heuristic"
    assert client.requests == []


def test_classify_skill_package_script_unclassified_on_genuine_llm_abstain():
    client = FakeClient(['{"abstain": true}'])
    result = _run(classify_skill_package_script("scripts/helper.py", client=client))
    assert result["goal"] is None
    assert result["expected_outcome"] is None
    assert result["classification"] == "unclassified"


def test_classify_skill_package_script_needs_enrichment_on_transient_failure():
    """No silent fallback here either: an LLM call that errors must NEVER
    fabricate a goal/expected_outcome -- it lands as 'needs_enrichment',
    a real, later-revisitable state, same discipline
    GroundedHybridExtractor already established for procedure extraction."""
    client = FakeClient(raises=True)
    result = _run(classify_skill_package_script("scripts/helper.py", client=client))
    assert result["goal"] is None
    assert result["expected_outcome"] is None
    assert result["classification"] == "needs_enrichment"
    assert result["verification_contract"] == {"type": "deterministic", "check": "exit_code_zero"}


def test_classify_skill_package_script_needs_enrichment_on_malformed_response():
    client = FakeClient(["this is not json at all"])
    result = _run(classify_skill_package_script("scripts/helper.py", client=client))
    assert result["classification"] == "needs_enrichment"


def test_classify_via_llm_raises_transient_failure_directly():
    """Lower-level unit proof of the exception type itself, independent of
    how the entry point handles it."""
    from app.services.implementation_goals import _classify_via_llm

    client = FakeClient(raises=True)
    with pytest.raises(ImplementationClassificationTransientFailure):
        _run(_classify_via_llm(
            client, "a-model", resource_path="scripts/helper.py",
            skill_name=None, skill_purpose=None, step_text=None,
        ))
