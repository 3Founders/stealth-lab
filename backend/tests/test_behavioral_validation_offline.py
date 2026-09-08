"""Gate 2B: deterministic, hermetic tests for the BEHAVIORAL validation
gate (app/execution/behavioral_validation.py) and its composition with the
existing artifact validation gate.

No DB, no LLM, no network: real fixture modules on real tmp_path repos,
verified by real subprocess execution of the produced artifacts -- the
same hermetic posture as test_artifact_validation_offline.py.

The scenario matrix (per the Gate 2B verification contract):
  1. correct implementation passes
  2. schema leakage in the default listing fails
  3. broken targeted full-schema retrieval fails
  4. malformed/unusable implementation fails
  5. ordinary tool operation broken fails
  6. an execution already marked failure can NEVER be upgraded to success
  7. artifact validation and behavioral validation compose correctly
     (artifact gate first, behavioral gate second, neither alone suffices)
plus: honest-unvalidated for an unregistered capability kind, fail-closed
for a malformed contract, and the real runner wiring end-to-end.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.local_agent.runner as runner_module
from app.execution.behavioral_validation import (
    BehavioralValidationResult,
    extract_behavioral_contract,
    gate_execution_success_with_behavior,
)

ECHO_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _contract(module: str = "tool_server.py") -> dict:
    return {
        "capability_kind": "lazy_tool_schema",
        "module": module,
        "expected_tools": {"echo": {"input_schema": ECHO_SCHEMA}},
        "tool_call": {"name": "echo", "args": {"text": "hello"},
                      "expect_contains": "hello"},
    }


GOOD_SERVER = '''
ECHO_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

def list_tools():
    """Default listing: lightweight metadata only -- NO full input schema."""
    return [{"name": "echo", "description": "Echo text back"}]

def get_tool_schema(name):
    """Targeted mechanism: the full JSON schema, only when needed."""
    if name == "echo":
        return ECHO_SCHEMA
    raise KeyError(name)

def call_tool(name, args):
    """Ordinary usage keeps working."""
    if name == "echo":
        return {"echoed": args["text"]}
    raise KeyError(name)
'''

SCHEMA_LEAK_SERVER = GOOD_SERVER.replace(
    'return [{"name": "echo", "description": "Echo text back"}]',
    'return [{"name": "echo", "description": "Echo text back", "input_schema": ECHO_SCHEMA}]',
)

BROKEN_TARGETED_SERVER = GOOD_SERVER.replace(
    "return ECHO_SCHEMA",
    'return {"type": "object", "properties": {"wrong": {"type": "number"}}}',
)

BROKEN_ORDINARY_SERVER = GOOD_SERVER.replace(
    'return {"echoed": args["text"]}',
    "raise RuntimeError('ordinary usage is broken')",
)

NO_TARGETED_MECHANISM_SERVER = GOOD_SERVER.replace("def get_tool_schema(name):", "def _get_tool_schema_unused(name):")


def _make_repo(tmp_path: Path, server_source: str) -> str:
    (tmp_path / "tool_server.py").write_text(server_source, encoding="utf-8")
    return str(tmp_path)


def _verdict(tmp_path: Path, server_source: str, contract: dict | None = None,
             declared_success: bool = True) -> tuple[bool, str | None]:
    return gate_execution_success_with_behavior(
        repo_root=_make_repo(tmp_path, server_source),
        declared_success=declared_success,
        files_edited=["tool_server.py"],
        behavioral_contract=contract if contract is not None else _contract(),
    )


# --- 1. correct implementation passes -------------------------------------
def test_correct_implementation_passes(tmp_path):
    ok, reason = _verdict(tmp_path, GOOD_SERVER)
    assert ok, f"a genuinely correct lazy-schema implementation must pass: {reason}"


# --- 2. schema leakage fails -----------------------------------------------
def test_schema_leak_in_default_listing_fails(tmp_path):
    ok, reason = _verdict(tmp_path, SCHEMA_LEAK_SERVER)
    assert not ok
    assert "input_schema" in (reason or ""), (
        "the failure reason must name the leaked full-schema key")


# --- 3. broken targeted retrieval fails ------------------------------------
def test_broken_targeted_schema_retrieval_fails(tmp_path):
    ok, reason = _verdict(tmp_path, BROKEN_TARGETED_SERVER)
    assert not ok
    assert "schema" in (reason or "").lower()

    # Missing mechanism entirely: also a failure, not a silent skip.
    ok, reason = _verdict(tmp_path, NO_TARGETED_MECHANISM_SERVER)
    assert not ok
    assert "get_tool_schema" in (reason or "")


# --- 5. ordinary tool operation broken fails -------------------------------
def test_broken_ordinary_usage_fails(tmp_path):
    ok, reason = _verdict(tmp_path, BROKEN_ORDINARY_SERVER)
    assert not ok
    assert "ordinary" in (reason or "").lower()


# --- 6. declared failure is never upgraded ---------------------------------
def test_declared_failure_is_never_upgraded_to_success(tmp_path):
    ok, reason = gate_execution_success_with_behavior(
        repo_root=_make_repo(tmp_path, GOOD_SERVER),
        declared_success=False,          # agent already failed
        files_edited=["tool_server.py"],
        behavioral_contract=_contract(),
    )
    assert ok is False
    assert reason is None  # nothing ran; failure stands as-is


def test_declared_failure_never_upgraded_even_with_perfect_artifact(tmp_path):
    ok, _ = gate_execution_success_with_behavior(
        repo_root=_make_repo(tmp_path, GOOD_SERVER),
        declared_success=False, files_edited=[], behavioral_contract=None,
    )
    assert ok is False


# --- 4/7. composition with artifact validation -----------------------------
def test_malformed_artifact_fails_at_artifact_gate_before_behavioral(tmp_path):
    """A syntactically broken implementation fails at the ARTIFACT gate;
    the behavioral verifier never runs (its reason would be a confusing
    second-order effect). Composition order is: artifact first."""
    ok, reason = _verdict(tmp_path, "def broken(:\n")
    assert ok is False
    assert "behavioral validation" not in (reason or "")
    assert reason  # the artifact gate's own parse/import reason


def test_valid_artifact_with_wrong_behavior_fails_at_behavioral_gate(tmp_path):
    """The mirror case: the artifact imports cleanly, so the artifact gate
    passes -- only the BEHAVIORAL gate catches the real defect. This is
    exactly the hole Gate 2B closes."""
    ok, reason = _verdict(tmp_path, SCHEMA_LEAK_SERVER)
    assert ok is False
    assert "behavioral validation (lazy_tool_schema)" in reason


def test_composed_gate_requires_both_gates(tmp_path):
    ok, reason = _verdict(tmp_path, GOOD_SERVER)
    assert ok is True and reason is None


# --- honesty rules ----------------------------------------------------------
def test_unregistered_capability_kind_is_honestly_unvalidated(tmp_path):
    ok, reason = gate_execution_success_with_behavior(
        repo_root=_make_repo(tmp_path, GOOD_SERVER),
        declared_success=True, files_edited=["tool_server.py"],
        behavioral_contract={"capability_kind": "some_future_kind",
                             "module": "x.py"},
    )
    # Success stands (tightening-only precedent), and the gap is named --
    # it must never be fabricated as a behavioral pass.
    assert ok is True


def test_malformed_contract_fails_closed(tmp_path):
    ok, reason = gate_execution_success_with_behavior(
        repo_root=_make_repo(tmp_path, GOOD_SERVER),
        declared_success=True, files_edited=["tool_server.py"],
        behavioral_contract={"capability_kind": "lazy_tool_schema"},  # missing keys
    )
    assert ok is False
    assert "missing required key" in (reason or "")


def test_contract_targeting_nonexistent_module_fails(tmp_path):
    ok, reason = _verdict(tmp_path, GOOD_SERVER,
                          contract=_contract(module="does_not_exist.py"))
    assert ok is False
    assert "does_not_exist.py" in (reason or "")


def test_extract_behavioral_contract_shapes():
    assert extract_behavioral_contract(None) is None
    assert extract_behavioral_contract({}) is None
    assert extract_behavioral_contract({"domain_payload": {}}) is None
    assert extract_behavioral_contract({"domain_payload": {"behavioral_contract": {}}}) is None
    contract = {"capability_kind": "lazy_tool_schema", "module": "m.py"}
    assert extract_behavioral_contract(
        {"domain_payload": {"behavioral_contract": contract}}) == contract


# --- real runner wiring, end-to-end (offline) -------------------------------
class _Content:
    def __init__(self, text: str):
        self.text = text


class _Resp:
    def __init__(self, text: str):
        self.content = [_Content(text)]


class _FakeSession:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return _Resp(self.responses[name])


def _procedure_payload(contract: dict | None) -> dict:
    return {
        "procedure_id": "p-lazy-1", "id": "row-1",
        "name": "mcp-lazy-tool-schema-loading",
        "steps": [{"order": 0, "goal": "implement lazy tool schema loading"}],
        "domain_payload": ({"behavioral_contract": contract} if contract else {}),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("server_source,should_succeed", [
    (GOOD_SERVER, True),
    (SCHEMA_LEAK_SERVER, False),
])
async def test_runner_composes_behavioral_gate_end_to_end(
        monkeypatch, tmp_path, server_source, should_succeed):
    """The REAL LocalAgentRunner path: a matched procedure with a declared
    behavioral contract has its composed gate decide the run's success --
    a schema-leaking implementation that 'finished with a patch' is
    downgraded to a failure exactly like the artifact gate would."""
    from tests.fake_embeddings import install_fake_embedder

    install_fake_embedder(monkeypatch)

    procedure = _procedure_payload(_contract())
    session = _FakeSession({
        "search_procedures": json.dumps([{"procedure_id": "p-lazy-1"}]),
        "get_procedure": json.dumps(procedure),
        "report_execution": json.dumps({"verification_state": "candidate"}),
    })

    async def fake_run_node(node, **kwargs):
        (tmp_path / "tool_server.py").write_text(server_source, encoding="utf-8")
        return runner_module.NodeResult(
            status="success", notes="implemented lazy schema loading",
            data={"files_edited": ["tool_server.py"], "patch": "diff --git ..."},
        )

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(
        task_description="make tools/list lazy-load full tool schemas",
        repo_path=str(tmp_path), allow_unverified=True,
    )

    # report_execution must have been reached with the GATED verdict.
    report_calls = [c for c in session.calls if c[0] == "report_execution"]
    assert len(report_calls) == 1, "the runner must report the gated outcome"
    assert report_calls[0][1]["success"] is should_succeed
    if not should_succeed:
        assert any("POST-EXECUTION VALIDATION FAILED" in n for n in result.node_notes)
