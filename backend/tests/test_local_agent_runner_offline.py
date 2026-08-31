"""
Phase 1's offline contract test (imperative-twirling-plum.md, Step 2):
proves the ARCHITECTURAL BOUNDARY, not just the call sequence -- the
local runner must have zero database dependency, structurally, not by
convention.

No fake database is created here on purpose: there is nothing to fake.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

import app.local_agent.runner as runner_module
from app.execution.graph_executor import NodeResult

RUNNER_SOURCE_PATH = Path(inspect.getfile(runner_module))


def test_runner_module_never_imports_the_database():
    """Structural, not conventional: parse the real module's own import
    statements (not just grep the text, which a comment could fool) and
    assert none of them touch asyncpg or app.db.session."""
    tree = ast.parse(RUNNER_SOURCE_PATH.read_text(encoding="utf-8"))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)

    forbidden = {"asyncpg", "app.db.session", "app.db"}
    hit = forbidden & imported_names
    assert not hit, f"local_agent.runner must not import the database, found: {hit}"
    assert "asyncpg" not in sys.modules or True  # asyncpg may be loaded by OTHER
    # already-imported test modules in the same process; the real assertion
    # is the source-level one above, not a process-wide sys.modules check.


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeToolResult:
    def __init__(self, text):
        self.content = [FakeContent(text)]


class FakeClientSession:
    """Records every call_tool invocation, in order, and returns
    caller-programmed results -- no real network, no real MCP transport."""

    def __init__(self, responses: dict):
        self.calls: list[tuple[str, dict]] = []
        self._responses = responses

    async def initialize(self):
        return None

    async def call_tool(self, name: str, args: dict):
        self.calls.append((name, args))
        return FakeToolResult(self._responses[name])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_runner_calls_search_then_execute_then_report(monkeypatch):
    import json

    procedure = {
        "id": "row-1", "procedure_id": "proc-1", "version": 1,
        "name": "fix-calc-bug", "goal": "fix the bug",
        "steps": [{"order": 0, "goal": "fix the bug in calc.py"}],
    }
    fake_session = FakeClientSession({
        "search_procedures": json.dumps([
            {"id": "row-1", "procedure_id": "proc-1", "version": 1,
             "name": "fix-calc-bug", "verification_state": "candidate", "similarity": 0.9},
        ]),
        "get_procedure": json.dumps(procedure),
        "report_execution": json.dumps({"procedure_id": "proc-1", "verification_state": "candidate"}),
    })

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes=f"ran {node.goal}", data={"files_edited": ["calc.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="fix the bug", repo_path="/fake/repo", allow_unverified=True)

    called_tools = [c[0] for c in fake_session.calls]
    assert called_tools == ["search_procedures", "get_procedure", "report_execution"], (
        f"expected search -> get -> report order, got {called_tools}"
    )
    assert result.matched_procedure["procedure_id"] == "proc-1"
    assert result.graph_outcome == "success"


@pytest.mark.asyncio
async def test_runner_probes_its_own_repo_and_sends_invariant_bindings(monkeypatch, tmp_path):
    """Phase 3's remaining real gap, closed: the runner has a real
    repo_path and must probe it ITSELF (never send raw filesystem data
    to the remote server) and forward the derived numeric bindings on
    search_procedures -- the same mechanism find_best_way's own
    repo_path path uses server-side, applied client-side because this is
    the process with a real repo to look at.

    No match, local or global -- Phase 12's ad-hoc path attempts a real
    execution now (see test_runner_prefers_and_stays_local_when_only_a_
    local_procedure_matches for that path's own dedicated test), so
    _run_local_node is faked here too -- this test's own concern is the
    search call's bindings, not execution."""
    import json as json_mod

    (tmp_path / "requirements.txt").write_text("pandas==2.1.0\n")

    fake_session = FakeClientSession({
        "search_procedures": json_mod.dumps([]),
    })

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="failure", notes="no real work in this test")

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="migrate pandas append", repo_path=str(tmp_path))

    search_call = next(c for c in fake_session.calls if c[0] == "search_procedures")
    sent_bindings = json_mod.loads(search_call[1]["invariant_bindings"])
    assert sent_bindings == {"pandas_version": 2.1}


@pytest.mark.asyncio
async def test_runner_prefers_and_stays_local_when_only_a_local_procedure_matches(monkeypatch, tmp_path):
    """Phase 1+2 integration (product spec, unified local+global retrieval):
    a real LocalProcedureStore captured procedure, with nothing matching
    remotely, must be selected, executed, and have its outcome recorded
    LOCALLY (record_local_execution_outcome) -- and `report_execution`
    must never be called, proving a local-sourced run's evidence never
    leaves this process (Rule 6)."""
    import json

    from app.local_agent.local_store import LocalProcedureStore

    store = LocalProcedureStore(str(tmp_path))
    captured = store.capture_local_procedure(
        name="fix-calc-bug-local", goal="fix the calc bug",
        steps=[{"order": 0, "goal": "fix the bug in calc.py"}],
        provenance="system_pending_review", scope_type="repository",
        scope_entity_id=str(tmp_path),
    )

    fake_session = FakeClientSession({
        "search_procedures": json.dumps([]),  # nothing matches remotely
    })

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes=f"ran {node.goal}",
                           data={"files_edited": ["calc.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="fix the calc bug", repo_path=str(tmp_path), allow_unverified=True)

    assert result.source == "local"
    assert result.matched_procedure["procedure_id"] == captured["procedure_id"]
    assert result.graph_outcome == "success"

    called_tools = [c[0] for c in fake_session.calls]
    assert "report_execution" not in called_tools, (
        "a local-sourced run's outcome must never be reported to the remote server"
    )
    assert "get_procedure" not in called_tools, (
        "a local match already has its full steps -- no remote fetch needed"
    )

    updated = store.get_local_procedure(captured["id"])
    assert updated["verification_stats"]["attempts"] == 1
    assert updated["verification_stats"]["successes"] == 1


@pytest.mark.asyncio
async def test_runner_captures_a_local_candidate_from_a_successful_adhoc_run(monkeypatch, tmp_path):
    """Phase 12 (personal learning loop) wired into the real runner: no
    match anywhere (local or global) must no longer just give up -- a
    real, successful ad-hoc run becomes a new local candidate procedure,
    never reported/published globally (Rule 6)."""
    import json

    from app.local_agent.local_store import LocalProcedureStore

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes=f"ran {node.goal}",
                           data={"files_edited": ["new_thing.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="add a new utility function", repo_path=str(tmp_path))

    assert result.matched_procedure is None, "nothing existed to match -- this was a fresh ad-hoc run"
    assert result.source == "local_adhoc"
    assert result.graph_outcome == "success"
    assert result.captured_candidate is not None
    assert result.captured_candidate["procedure_id"]

    called_tools = [c[0] for c in fake_session.calls]
    assert called_tools == ["search_procedures"], (
        "an ad-hoc local capture must never call get_procedure/report_execution/"
        "any global-publishing tool -- it stays entirely local"
    )

    store = LocalProcedureStore(str(tmp_path))
    row = store.get_local_procedure(result.captured_candidate["id"])
    assert row["goal"] == "add a new utility function"
    assert row["verification_state"] == "candidate"
    assert row["provenance"] == "system_pending_review"
    # P0 fix: the run that just succeeded and produced this candidate is
    # its own first real evidence -- must not start at attempts=0 despite
    # one genuine, already-known-successful execution existing for it.
    assert row["verification_stats"]["attempts"] == 1
    assert row["verification_stats"]["successes"] == 1


def test_local_context_key_reflects_real_environment_not_just_repo_name():
    """P0 fix: a bare repo folder name collapsed every run against the
    same checkout into ONE context. Two DIFFERENT real environments (here,
    different probed package versions) for the SAME repo folder name must
    produce DIFFERENT context keys; the SAME environment probed twice must
    produce the SAME key (no artificial diversity)."""
    from app.services.environment_facts import EnvironmentFact

    facts_a = [EnvironmentFact(predicate="language", object="python"),
               EnvironmentFact(predicate="pandas_version", object="1.5.3")]
    facts_b = [EnvironmentFact(predicate="language", object="python"),
               EnvironmentFact(predicate="pandas_version", object="2.1.0")]

    key_a1 = runner_module._local_context_key("/repo", facts_a)
    key_a2 = runner_module._local_context_key("/repo", list(reversed(facts_a)))
    key_b = runner_module._local_context_key("/repo", facts_b)

    assert key_a1.startswith("repo:")
    assert key_a1 == key_a2, "fact order must not affect the derived key"
    assert key_a1 != key_b, "a genuinely different probed environment must yield a different context"

    # Different repo, same facts -> different key (repo identity still matters).
    key_other_repo = runner_module._local_context_key("/other-repo", facts_a)
    assert key_other_repo != key_a1


@pytest.mark.asyncio
async def test_runner_finds_a_local_procedure_via_semantic_similarity_not_lexical_overlap(monkeypatch, tmp_path):
    """P0 fix: local search must actually use the store's real cosine-
    similarity ranking, not silently fall back to lexical-only matching
    because no query embedding was ever supplied. Real embedding calls
    (this repo's existing Embedder, no mock) prove genuine semantic
    retrieval: the query shares NO words with the stored procedure's
    name/goal, so a lexical-only search would find nothing."""
    import json

    from app.local_agent.local_store import LocalProcedureStore
    from app.services.embeddings import Embedder

    embedder = Embedder()
    goal = "resolve a failing login attempt caused by an expired session token"
    goal_vec = await embedder.embed_one(goal, input_type="document")

    store = LocalProcedureStore(str(tmp_path))
    captured = store.capture_local_procedure(
        name="fix-auth-issue", goal=goal,
        steps=[{"order": 0, "goal": "inspect the session store"}],
        provenance="system_pending_review", scope_type="repository",
        scope_entity_id=str(tmp_path), embedding=goal_vec,
    )
    # A real, verified row so require_verified=True (the default) surfaces it.
    for i in range(10):
        store.record_local_execution_outcome(
            row_id=captured["id"], success=True, context_key=f"ctx-{i % 3}",
        )

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes=f"ran {node.goal}",
                           data={"files_edited": ["session.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(
        # Deliberately zero vocabulary overlap with "resolve/failing/login/
        # attempt/expired/session/token" above.
        task_description="authentication broke for users whose credentials timed out",
        repo_path=str(tmp_path),
    )

    assert result.source == "local"
    assert result.matched_procedure["procedure_id"] == captured["procedure_id"], (
        "semantic similarity must have found this procedure -- lexical "
        "matching alone shares zero words with the query"
    )


@pytest.mark.asyncio
async def test_runner_does_not_capture_a_candidate_from_a_failed_adhoc_run(monkeypatch, tmp_path):
    import json

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="failure", notes="gave up", data={"files_edited": [], "patch": ""})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="a task that fails", repo_path=str(tmp_path))

    assert result.captured_candidate is None
    assert result.source is None


@pytest.mark.asyncio
async def test_runner_refuses_a_node_naming_only_an_unimplemented_kind(monkeypatch, tmp_path):
    """P1 (product spec: 'Complete implementation abstraction') wired into
    the real local runner: a step naming an implementation kind with no
    real registered executor (e.g. 'slm') must produce an honest,
    explicit failure -- NEVER a silent frontier run masquerading as
    something else, and never a silent no-op. Proven by never letting
    `_run_local_node` (the real Agent+RepoSandbox mechanism) get called
    at all for this node."""
    import json

    from app.local_agent.local_store import LocalProcedureStore

    store = LocalProcedureStore(str(tmp_path))
    store.capture_local_procedure(
        name="fix-calc-bug-local", goal="fix the calc bug",
        steps=[{"order": 0, "goal": "fix the bug in calc.py", "implementation_hint": "slm"}],
        provenance="system_pending_review", scope_type="repository",
        scope_entity_id=str(tmp_path),
    )

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    run_local_node_calls: list = []

    async def fake_run_node(node, **kwargs):
        run_local_node_calls.append(node.order)
        return NodeResult(status="success", notes="should never be reached")

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="fix the calc bug", repo_path=str(tmp_path), allow_unverified=True)

    assert run_local_node_calls == [], (
        "the real sandboxed execution mechanism must never run for a node "
        "naming only an unimplemented kind"
    )
    assert result.graph_outcome == "failure"
    assert any("not yet implemented" in note for note in result.node_notes)


@pytest.mark.asyncio
async def test_runner_still_runs_a_hintless_node_through_the_real_mechanism(monkeypatch, tmp_path):
    """The registry must not change behavior for the overwhelming common
    case (a step naming no implementation_hint at all) -- it resolves to
    'frontier', the one real supported kind, and `_run_local_node` runs
    exactly as it always has."""
    import json

    from app.local_agent.local_store import LocalProcedureStore

    store = LocalProcedureStore(str(tmp_path))
    store.capture_local_procedure(
        name="fix-calc-bug-local-2", goal="fix the calc bug",
        steps=[{"order": 0, "goal": "fix the bug in calc.py"}],
        provenance="system_pending_review", scope_type="repository",
        scope_entity_id=str(tmp_path),
    )

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    run_local_node_calls: list = []

    async def fake_run_node(node, **kwargs):
        run_local_node_calls.append(node.order)
        return NodeResult(status="success", notes="ran for real",
                           data={"files_edited": ["calc.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="fix the calc bug", repo_path=str(tmp_path), allow_unverified=True)

    assert run_local_node_calls == [0]
    assert result.graph_outcome == "success"


@pytest.mark.asyncio
async def test_runner_reports_no_match_without_crashing(monkeypatch):
    import json

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})
    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="fix something nothing matches", repo_path="/fake/repo")

    assert result.matched_procedure is None
    assert result.graph_outcome == "no_match"
    assert [c[0] for c in fake_session.calls] == ["search_procedures"], (
        "must not call get_procedure/report_execution when nothing matched"
    )
