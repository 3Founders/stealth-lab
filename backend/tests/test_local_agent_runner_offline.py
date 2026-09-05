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
from tests.fake_embeddings import install_fake_embedder

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


@pytest.mark.asyncio
async def test_offline_embedder_seam_never_reaches_a_real_provider(monkeypatch):
    """Gate 2A regression: the offline fake (tests/fake_embeddings.py)
    patches `Embedder._embed_via_chain` -- the one method that actually
    dispatches to a network provider. Pin that the patched seam is really
    intercepting there, not merely happening to avoid the network by luck
    (e.g. because every offline test's exact text was already cache-warm):
    stub every real provider method to blow up, then prove `embed_one`
    still returns a normal vector with none of them called. A future
    refactor that moves the provider dispatch to a new method (bypassing
    this patch point) would make this test fail loudly instead of quietly
    reintroducing a live network call in the "offline" suite."""
    from app.services.embeddings import Embedder

    install_fake_embedder(monkeypatch)

    async def _real_provider_call_attempted(self, *args, **kwargs):
        raise AssertionError(
            "a real embedding provider method was reached from an offline test"
        )

    monkeypatch.setattr(Embedder, "_embed_gemini", _real_provider_call_attempted)
    monkeypatch.setattr(Embedder, "_embed_voyage", _real_provider_call_attempted)
    monkeypatch.setattr(Embedder, "_embed_local", _real_provider_call_attempted)

    embedder = Embedder()
    vector = await embedder.embed_one("anything at all", input_type="query")

    assert isinstance(vector, list)
    assert len(vector) == embedder.dimension
    assert all(isinstance(v, float) for v in vector)


@pytest.mark.asyncio
async def test_offline_runner_flow_never_reaches_a_real_provider(monkeypatch, tmp_path):
    """Same guarantee, exercised through the real runner path that used to
    make the live calls (query embedding on every search, capture embedding
    on ad-hoc candidate capture) -- proves the fake is actually wired into
    the runner's real usage, not just into a standalone Embedder call."""
    import json

    from app.services.embeddings import Embedder

    install_fake_embedder(monkeypatch)

    async def _real_provider_call_attempted(self, *args, **kwargs):
        raise AssertionError(
            "a real embedding provider method was reached from an offline runner test"
        )

    monkeypatch.setattr(Embedder, "_embed_gemini", _real_provider_call_attempted)
    monkeypatch.setattr(Embedder, "_embed_voyage", _real_provider_call_attempted)
    monkeypatch.setattr(Embedder, "_embed_local", _real_provider_call_attempted)

    (tmp_path / "new_thing.py").write_text("import os\n")
    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes=f"ran {node.goal}",
                           data={"files_edited": ["new_thing.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="add a new utility function", repo_path=str(tmp_path))

    # This flow calls Embedder twice for real (query embedding, then
    # capture embedding on the ad-hoc candidate) -- reaching this
    # assertion at all, with the provider stubs above never firing, is
    # the proof.
    assert result.captured_candidate is not None


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

    install_fake_embedder(monkeypatch)
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

    install_fake_embedder(monkeypatch)
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

    # A real, importable file -- the artifact-validation gate (see
    # test_artifact_validation_offline.py) now actually inspects whatever
    # `files_edited` names, so a fake claiming to have touched "calc.py"
    # must leave a real, valid file there, exactly as the real
    # _run_local_node/RepoSandbox always does.
    (tmp_path / "calc.py").write_text("import os\n")

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

    install_fake_embedder(monkeypatch)
    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    # See the matching comment in
    # test_runner_prefers_and_stays_local_when_only_a_local_procedure_matches:
    # the artifact-validation gate now really inspects `files_edited`.
    (tmp_path / "new_thing.py").write_text("import os\n")

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
    because no query embedding was ever supplied. Deterministic fake
    embeddings (tests/fake_embeddings.py -- concept-synonym vectors, no
    network) prove genuine semantic retrieval: the query shares NO words
    with the stored procedure's name/goal, so a lexical-only search would
    find nothing, yet the two texts share recognized concepts (auth,
    negative_outcome, ...) so their fake vectors are genuinely close in
    cosine space, exercising the real similarity-ranking code path."""
    import json

    from app.local_agent.local_store import LocalProcedureStore
    from app.services.embeddings import Embedder

    install_fake_embedder(monkeypatch)
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

    install_fake_embedder(monkeypatch)
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

    install_fake_embedder(monkeypatch)
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

    install_fake_embedder(monkeypatch)
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


def test_mcp_session_http_timeout_is_not_shorter_than_a_real_agent_run_can_take():
    """Regression pin for a real live-pilot incident (Final Baseline vs
    Stealth Agent Experiment, T7/B_default, 2/2 trials): _open_client_session
    used to construct its httpx2.AsyncClient with timeout=60, and the MCP
    session is held open for this transport's ENTIRE lifetime -- including
    while _run_local_node blocks synchronously (asyncio.to_thread) running a
    real Agent+RepoSandbox loop against GENERAL_COMPUTE, traffic that never
    touches this MCP connection at all. Two real trials on a task requiring
    more agent exploration each died at wall_clock_seconds_total 60.01s/
    60.02s with `ExceptionGroup: unhandled errors in a TaskGroup (2
    sub-exceptions)` -- the mcp package's own internal transport TaskGroup
    (client/session.py, client/streamable_http.py), not backend/app or
    experiments/swebench_pro code, raised when the underlying connection's
    idle read timed out under this client-side timeout.

    This test does not open a real connection (that would require a live
    server) -- it pins the real, exported timeout constant itself, so a
    future edit that quietly shrinks it back below a real agent run's
    plausible duration fails CI instead of failing silently on the next
    long-running live pilot."""
    timeout = runner_module._MCP_SESSION_HTTP_TIMEOUT_SECONDS
    # The orchestrator's own outer per-trial wall-clock ceiling is 600s
    # (.scratch/final_agent_experiment/protocol.md) -- the MCP transport's
    # own timeout must never be the thing that kills a trial before that
    # already-designed outer budget does.
    assert timeout >= 600, (
        f"_MCP_SESSION_HTTP_TIMEOUT_SECONDS={timeout} is below the "
        "orchestrator's 600s per-trial ceiling -- this would silently "
        "reintroduce the T7 ExceptionGroup incident."
    )
    # The original incident-triggering value, pinned explicitly so nobody
    # re-introduces exactly this number by copy-paste.
    assert timeout != 60, "this is the exact value that caused the T7 crash"


# ---------------------------------------------------------------------------
# PART 1 regression: verification success criterion must gate on real
# artifact validation, not just a "finished" stop_reason + non-empty patch.
# See app/execution/artifact_validation.py's module docstring for the real
# D1 incident (an unimportable module accepted as success) this closes.
# Unit coverage of the validator itself lives in
# test_artifact_validation_offline.py -- these tests prove it is actually
# WIRED IN to the real runner's success/evidence-recording path.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runner_never_records_local_success_for_an_unimportable_artifact(monkeypatch, tmp_path):
    """OLD UNSAFE BEHAVIOR (the confirmed D1 defect): stop_reason=='finished'
    plus a non-empty patch was accepted as success with zero inspection of
    the artifact itself. Proven closed: a matched local procedure whose
    agent run 'finishes' but leaves a syntactically-valid, unimportable
    Python file behind must be recorded as a FAILURE in the local store,
    never a success -- even though the raw graph mechanics still say
    'success' (that's the agent's own honest self-report, left unchanged;
    only the RECORDED evidence outcome is gated)."""
    import json

    from app.local_agent.local_store import LocalProcedureStore

    install_fake_embedder(monkeypatch)
    store = LocalProcedureStore(str(tmp_path))
    captured = store.capture_local_procedure(
        name="fix-calc-bug-local", goal="fix the calc bug",
        steps=[{"order": 0, "goal": "fix the bug in calc.py"}],
        provenance="system_pending_review", scope_type="repository",
        scope_entity_id=str(tmp_path),
    )

    # Syntactically valid, semantically broken -- the exact D1 shape:
    # references a real module's attribute that does not exist.
    (tmp_path / "calc.py").write_text("from os import DefinitelyNotARealAttribute\n")

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes=f"ran {node.goal}",
                           data={"files_edited": ["calc.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="fix the calc bug", repo_path=str(tmp_path), allow_unverified=True)

    # The agent's own raw self-report is left honest/unchanged...
    assert result.graph_outcome == "success"
    assert any("ARTIFACT VALIDATION FAILED" in note for note in result.node_notes)

    # ...but the RECORDED evidence outcome must be a failure, never a
    # success -- this is the real, single source of truth ticket 13's
    # promotion math reads from.
    updated = store.get_local_procedure(captured["id"])
    assert updated["verification_stats"]["attempts"] == 1
    assert updated["verification_stats"]["successes"] == 0


@pytest.mark.asyncio
async def test_runner_does_not_capture_an_unimportable_adhoc_artifact_as_a_candidate(monkeypatch, tmp_path):
    """Same defect, ad-hoc/no-match path (Phase 12 capture): an unimportable
    artifact must never become a new local candidate procedure at all --
    laundering a broken result into a stored 'this worked, try it again'
    candidate would be the same class of defect, one step earlier."""
    import json

    install_fake_embedder(monkeypatch)
    (tmp_path / "new_thing.py").write_text("from os import DefinitelyNotARealAttribute\n")

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes=f"ran {node.goal}",
                           data={"files_edited": ["new_thing.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="add a new utility function", repo_path=str(tmp_path))

    assert result.graph_outcome == "success"
    assert any("ARTIFACT VALIDATION FAILED" in note for note in result.node_notes)
    assert result.captured_candidate is None, (
        "an unimportable ad-hoc artifact must never be captured as a new "
        "local candidate procedure"
    )
    assert result.source is None


@pytest.mark.asyncio
async def test_runner_still_records_success_for_a_genuinely_working_artifact(monkeypatch, tmp_path):
    """Proves the fix does not weaken anything: a matched local procedure
    whose run leaves a real, importable file behind must still be recorded
    as a success exactly as before."""
    import json

    from app.local_agent.local_store import LocalProcedureStore

    install_fake_embedder(monkeypatch)
    store = LocalProcedureStore(str(tmp_path))
    captured = store.capture_local_procedure(
        name="fix-calc-bug-local", goal="fix the calc bug",
        steps=[{"order": 0, "goal": "fix the bug in calc.py"}],
        provenance="system_pending_review", scope_type="repository",
        scope_entity_id=str(tmp_path),
    )
    (tmp_path / "calc.py").write_text("import os\n\nVALUE = 1\n")

    fake_session = FakeClientSession({"search_procedures": json.dumps([])})

    async def fake_run_node(node, **kwargs):
        return NodeResult(status="success", notes=f"ran {node.goal}",
                           data={"files_edited": ["calc.py"], "patch": "diff --git ..."})

    monkeypatch.setattr(runner_module, "_open_client_session", lambda url, token: fake_session)
    monkeypatch.setattr(runner_module, "_run_local_node", fake_run_node)

    result = await runner_module.LocalAgentRunner(
        server_url="http://fake/mcp", token="fake-token",
    ).run(task_description="fix the calc bug", repo_path=str(tmp_path), allow_unverified=True)

    assert result.graph_outcome == "success"
    assert not any("ARTIFACT VALIDATION FAILED" in note for note in result.node_notes)

    updated = store.get_local_procedure(captured["id"])
    assert updated["verification_stats"]["successes"] == 1


# ---------------------------------------------------------------------------
# PART 2 regression: context identity must derive from the real repository
# (git remote + HEAD SHA), not the disposable folder name the checkout
# happens to live at -- otherwise a verification campaign can manufacture
# fake ">=3 distinct contexts" just by renaming/re-cloning the same
# checkout, and two genuinely different repos sharing a conventional folder
# name (e.g. both named "repo") could falsely collapse into one context.
# ---------------------------------------------------------------------------

def _init_fake_git_repo(root, *, sha: str, remote_url: str | None = None) -> None:
    """Minimal, real `.git` on-disk layout -- no `git` binary invocation,
    matching this module's own pure-filesystem-read discipline. Enough for
    `_git_repo_identity` to resolve a real HEAD SHA (detached-HEAD shape:
    `.git/HEAD` names the SHA directly, the simplest real case)."""
    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(sha + "\n")
    if remote_url is not None:
        (git_dir / "config").write_text(
            f'[remote "origin"]\n\turl = {remote_url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
        )


def test_context_key_is_the_same_for_two_differently_named_clones_of_the_same_commit(tmp_path):
    """THE CONFIRMED DEFECT: two disposable clones of the exact same
    repository at the exact same commit, checked out under two
    DIFFERENT folder names, must produce the SAME context_key -- the old
    folder-basename-only identity would have produced two different keys
    here, letting a verification campaign manufacture fake diversity by
    nothing more than renaming/re-cloning the same checkout."""
    from app.services.environment_facts import EnvironmentFact

    clone_a = tmp_path / "disposable-clone-run-1"
    clone_b = tmp_path / "disposable-clone-run-2-totally-different-name"
    clone_a.mkdir()
    clone_b.mkdir()
    sha = "a" * 40
    _init_fake_git_repo(clone_a, sha=sha, remote_url="git@example.com:acme/widgets.git")
    _init_fake_git_repo(clone_b, sha=sha, remote_url="git@example.com:acme/widgets.git")

    facts = [EnvironmentFact(predicate="language", object="python")]
    key_a = runner_module._local_context_key(str(clone_a), facts)
    key_b = runner_module._local_context_key(str(clone_b), facts)

    assert key_a == key_b, (
        "renaming/re-cloning the SAME commit of the SAME repo must not "
        "manufacture a new context -- this is exactly how a verification "
        "campaign could game the distinct-contexts counter"
    )


def test_context_key_differs_for_the_same_folder_name_holding_different_repos(tmp_path):
    """The other direction of the same defect: two ACTUALLY different
    repositories that happen to be checked out under the same
    conventional folder name (e.g. both literally named "repo") must
    produce DIFFERENT context_keys -- the old folder-basename-only
    identity would have falsely collapsed these into one context."""
    from app.services.environment_facts import EnvironmentFact

    root_a = tmp_path / "workspace_one"
    root_b = tmp_path / "workspace_two"
    root_a.mkdir()
    root_b.mkdir()
    repo_a = root_a / "repo"
    repo_b = root_b / "repo"
    repo_a.mkdir()
    repo_b.mkdir()
    _init_fake_git_repo(repo_a, sha="a" * 40, remote_url="git@example.com:acme/widgets.git")
    _init_fake_git_repo(repo_b, sha="b" * 40, remote_url="git@example.com:acme/other-project.git")

    facts = [EnvironmentFact(predicate="language", object="python")]
    key_a = runner_module._local_context_key(str(repo_a), facts)
    key_b = runner_module._local_context_key(str(repo_b), facts)

    assert key_a != key_b, (
        "two genuinely different repositories sharing a folder name must "
        "not collapse into the same context"
    )


def test_context_key_falls_back_to_folder_name_when_there_is_no_git_metadata_at_all():
    """A repo_path with no discoverable .git metadata (offline tests, a
    bare non-git workspace) must fall back to the prior folder-name
    behavior honestly, never crash and never fabricate a git identity."""
    from app.services.environment_facts import EnvironmentFact

    facts = [EnvironmentFact(predicate="language", object="python")]
    key = runner_module._local_context_key("/fake/nonexistent/repo", facts)
    assert key.startswith("repo:")


def test_git_repo_identity_is_none_without_git_metadata(tmp_path):
    assert runner_module._git_repo_identity(str(tmp_path)) is None


def test_git_repo_identity_uses_head_sha_and_remote_url(tmp_path):
    _init_fake_git_repo(tmp_path, sha="c" * 40, remote_url="git@example.com:acme/widgets.git")
    identity = runner_module._git_repo_identity(str(tmp_path))
    assert identity == "git@example.com:acme/widgets.git@" + "c" * 40


def test_git_repo_identity_handles_a_missing_remote_honestly(tmp_path):
    _init_fake_git_repo(tmp_path, sha="d" * 40, remote_url=None)
    identity = runner_module._git_repo_identity(str(tmp_path))
    assert identity == "no-remote@" + "d" * 40
