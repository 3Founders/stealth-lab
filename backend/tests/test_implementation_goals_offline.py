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
import json
import types

import pytest

from app.services.implementation_goals import (
    VERIFICATION_CONTRACT_TYPES,
    ImplementationClassificationTransientFailure,
    _find_step_text,
    classify_skill_package_script,
    default_verification_contract,
    enrich_pending_skill_package_implementations,
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


# --- _find_step_text: real matching rule, mirrors _persist_package_relations ---

def test_find_step_text_matches_full_path_or_basename():
    steps = [
        {"order": 0, "goal": "List the top-level directories."},
        {"order": 1, "goal": "Run scripts/reconcile.py to compare schemas."},
    ]
    assert _find_step_text(steps, "skills/x/scripts/reconcile.py") == steps[1]["goal"]


def test_find_step_text_returns_none_when_unmentioned():
    steps = [{"order": 0, "goal": "Do something unrelated."}]
    assert _find_step_text(steps, "scripts/reconcile.py") is None


def test_find_step_text_handles_json_string_and_empty_input():
    steps_json = json.dumps([{"order": 0, "goal": "Run scripts/x.py first."}])
    assert _find_step_text(steps_json, "scripts/x.py") == "Run scripts/x.py first."
    assert _find_step_text(None, "scripts/x.py") is None
    assert _find_step_text([], "scripts/x.py") is None


# --- enrich_pending_skill_package_implementations: real, resumable pass ---

class _EnrichmentFakePool:
    """Real rows shaped exactly like the JOIN this function issues --
    (locator, kind, scope_type, scope_entity_id, skill_name,
    skill_purpose, steps) per implementation id -- and captures every
    UPDATE so the real per-row outcome can be asserted without a real DB.

    `fetchrow` fakes find_or_create_goal's own two-statement shape (a
    dedup SELECT, always a miss here, then an INSERT ... RETURNING) --
    good enough to prove the enrichment job WIRES goal resolution in,
    without needing a real `goals` table. Real dedup behavior is covered
    separately in tests/test_goals_offline.py."""

    def __init__(self, rows):
        self._rows = rows
        self.updates: list[tuple] = []
        self.goal_inserts: list[tuple] = []

    async def fetch(self, sql, *params):
        if "simhash IS NOT NULL" in " ".join(sql.split()):
            return []  # find_or_create_goal's tier 2.5 shortlist -- none here
        return self._rows

    async def execute(self, sql, *params):
        self.updates.append(params)

    async def fetchrow(self, sql, *params):
        if sql.strip().upper().startswith("SELECT"):
            return None  # find_or_create_goal's dedup check: always a miss here
        self.goal_inserts.append(params)
        return {"id": f"fake-goal-{len(self.goal_inserts)}", "canonical_name": params[1]}


def test_enrich_pending_implementations_classifies_a_heuristic_match():
    """A row nobody attempted the (now-existing) deterministic pass on
    yet -- migration 80 backfilled it BEFORE this classifier existed --
    gets a real, free, no-LLM classification on this very first pass."""
    rows = [{
        "id": "11111111-1111-1111-1111-111111111111",
        "locator": {"path": "skills/x/scripts/check_schema.py"},
        "kind": "deterministic", "scope_type": None, "scope_entity_id": None,
        "skill_name": "x", "skill_purpose": "purpose", "steps": None,
    }]
    pool = _EnrichmentFakePool(rows)

    counts = _run(enrich_pending_skill_package_implementations(pool, limit=10, client=None))

    assert counts == {
        "attempted": 1, "heuristic": 1, "llm_classified": 0,
        "unclassified": 0, "needs_enrichment": 0, "errors": 0,
    }
    (row_id, goal, goal_spec, expected_outcome, verification_contract, classification, goal_id) = pool.updates[0]
    assert row_id == rows[0]["id"]
    assert goal == "verification"
    assert classification == "heuristic"
    # migration 83 wiring: a real (fake-DB) Goal row was resolved and
    # linked, not just the free-text `goal` column.
    assert goal_id == "fake-goal-1"
    assert pool.goal_inserts[0][1] == "verification"  # canonical_name passed through


def test_enrich_pending_implementations_uses_real_procedure_context_for_the_llm_path():
    """The skill_name/skill_purpose/step_text come from the REAL join
    result (procedures, via procedure_implementations) -- never a
    network refetch of the original SKILL.md."""
    rows = [{
        "id": "22222222-2222-2222-2222-222222222222",
        "locator": {"path": "skills/x/scripts/reconcile.py"},
        "kind": "deterministic", "scope_type": None, "scope_entity_id": None,
        "skill_name": "schema-guard", "skill_purpose": "Keep migrations consistent.",
        "steps": json.dumps([{"order": 0, "goal": "Run scripts/reconcile.py before committing."}]),
    }]
    pool = _EnrichmentFakePool(rows)
    client = FakeClient(['{"goal": "verification", "expected_outcome": "schema matches migrations"}'])

    counts = _run(enrich_pending_skill_package_implementations(pool, limit=10, client=client))

    assert counts["llm_classified"] == 1
    assert len(client.requests) == 1
    prompt = client.requests[0]["messages"][1]["content"]
    assert "schema-guard" in prompt
    assert "Keep migrations consistent." in prompt
    assert "Run scripts/reconcile.py before committing." in prompt


def test_enrich_pending_implementations_counts_errors_without_aborting_the_batch():
    """A malformed row (no locator path) must not stop the rest of the
    batch from being attempted -- real per-row isolation, same discipline
    process_pending_jobs() already applies to ingestion jobs."""
    rows = [
        {"id": "a", "locator": {}, "kind": "deterministic",
         "scope_type": None, "scope_entity_id": None,
         "skill_name": None, "skill_purpose": None, "steps": None},
        {"id": "b", "locator": {"path": "scripts/check_schema.py"}, "kind": "deterministic",
         "scope_type": None, "scope_entity_id": None,
         "skill_name": None, "skill_purpose": None, "steps": None},
    ]
    pool = _EnrichmentFakePool(rows)

    counts = _run(enrich_pending_skill_package_implementations(pool, limit=10, client=None))

    assert counts["errors"] == 1
    assert counts["heuristic"] == 1
    assert counts["attempted"] == 2
    assert len(pool.updates) == 1
