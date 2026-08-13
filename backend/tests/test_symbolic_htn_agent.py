"""
Tests for symbolic_htn_agent.py's two neuro-symbolic agent variants.

NeuroSymbolicWrapperHTNAgent: proves the fallback chain (method-library hit
short-circuits the bridge; a miss falls through to the bridge; a double
miss leaves `_pending_seed_plan` unset so the unmodified planner runs).

TypedPreconditionHTNAgent: proves the advisory hint appears in the prompt
exactly when reachability finds something, and that the opt-in strict gate
blocks only the keyword-corroborated case -- a generic reachable file, or
one the issue text never mentions, must NOT be blocked.

No DB, no LLM, no Docker: app.services.method_library.find_reusable_plan is mocked
directly (it is imported locally inside the function under test, so
patching the module attribute is visible at call time); the decomposition
bridge is driven by a stub returning a pre-built Decomposition, same
pattern test_decomposition_bridge.py uses.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from agent import RepoSandbox  # noqa: E402
from htn_agent import Node  # noqa: E402
from symbolic_htn_agent import NeuroSymbolicWrapperHTNAgent, TypedPreconditionHTNAgent  # noqa: E402

from app.models.change import ChangeSet, CreateTaskNodeOp  # noqa: E402
from app.services.decomposition import Decomposition  # noqa: E402


def _msg(content=None, tool_calls=None):
    tc = []
    for i, (name, args) in enumerate(tool_calls or []):
        tc.append(types.SimpleNamespace(
            id=f"c{i}", function=types.SimpleNamespace(
                name=name, arguments=json.dumps(args))))
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content, tool_calls=tc or None))],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        if not self.script:
            return _msg(tool_calls=[("subgoal_failed", {"reason": "script exhausted"})])
        return self.script.pop(0)


class _StubDecomposer:
    """Bypasses DecompositionService -- returns a pre-built Decomposition,
    same pattern test_decomposition_bridge.py uses."""

    def __init__(self, decomposition: Decomposition):
        self._decomposition = decomposition

    async def decompose(self, problem, query_postconditions=None):
        return self._decomposition


_FEASIBLE = Decomposition(
    feasible=True,
    change_set=ChangeSet(ops=[CreateTaskNodeOp(ref="t1", name="from the bridge")]),
)
_INFEASIBLE = Decomposition(feasible=False, reasoning="nothing describable here")


class TestWrapperFallbackChain:
    def test_method_library_hit_short_circuits_the_bridge(self):
        agent = NeuroSymbolicWrapperHTNAgent(client=None, model="m")

        class _ExplodingDecomposer:
            async def decompose(self, problem, query_postconditions=None):
                raise AssertionError("bridge must not be called on a method-library hit")

        with patch("app.services.method_library.find_reusable_plan", new=AsyncMock(
                return_value={"decomposition": [{"id": 1, "goal": "reused step", "deps": []}]})):
            hit = asyncio.run(agent._synthesize_plan(
                pool=object(), embedder=object(),
                sample={"problem_statement": "x", "instance_id": "i1"},
                decomposer=_ExplodingDecomposer()))

        assert hit is True
        assert agent._pending_seed_plan == [{"id": 1, "goal": "reused step", "deps": []}]

    def test_bridge_hit_when_method_library_misses(self):
        agent = NeuroSymbolicWrapperHTNAgent(client=None, model="m")
        with patch("app.services.method_library.find_reusable_plan", new=AsyncMock(return_value=None)):
            hit = asyncio.run(agent._synthesize_plan(
                pool=object(), embedder=object(),
                sample={"problem_statement": "x", "instance_id": "i1"},
                decomposer=_StubDecomposer(_FEASIBLE)))

        assert hit is True
        assert agent._pending_seed_plan == [{"id": 1, "goal": "from the bridge", "deps": []}]

    def test_double_miss_leaves_pending_seed_plan_unset(self):
        agent = NeuroSymbolicWrapperHTNAgent(client=None, model="m")
        with patch("app.services.method_library.find_reusable_plan", new=AsyncMock(return_value=None)):
            hit = asyncio.run(agent._synthesize_plan(
                pool=object(), embedder=object(),
                sample={"problem_statement": "x", "instance_id": "i1"},
                decomposer=_StubDecomposer(_INFEASIBLE)))

        assert hit is False
        assert getattr(agent, "_pending_seed_plan", None) is None

    def test_no_decomposer_given_and_method_library_misses(self):
        agent = NeuroSymbolicWrapperHTNAgent(client=None, model="m")
        with patch("app.services.method_library.find_reusable_plan", new=AsyncMock(return_value=None)):
            hit = asyncio.run(agent._synthesize_plan(
                pool=object(), embedder=object(), sample={"problem_statement": "x"}))
        assert hit is False


class TestTypedPreconditionContextInjection:
    def test_hint_appears_when_reachability_finds_something(self, tmp_path):
        (tmp_path / "a.py").write_text("def caller():\n    helper()\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        sandbox = RepoSandbox(str(tmp_path))
        agent = TypedPreconditionHTNAgent(client=None, model="m")
        node = Node(id=1, goal="Fix the bug in a.py")

        agent._verify_precondition(node, sandbox)
        extra = agent._system_prompt_extra(node)

        assert "call-graph analysis" in extra
        assert "b.py" in extra

    def test_no_hint_when_nothing_is_reachable(self, tmp_path):
        (tmp_path / "a.py").write_text("def lonely():\n    return 1\n", encoding="utf-8")
        sandbox = RepoSandbox(str(tmp_path))
        agent = TypedPreconditionHTNAgent(client=None, model="m")
        node = Node(id=1, goal="Fix the bug in a.py")

        agent._verify_precondition(node, sandbox)
        extra = agent._system_prompt_extra(node)

        assert "call-graph analysis" not in extra

    def test_no_hint_before_verify_precondition_has_run(self):
        agent = TypedPreconditionHTNAgent(client=None, model="m")
        extra = agent._system_prompt_extra(Node(id=1, goal="Implement the fix."))
        assert "call-graph analysis" not in extra


class TestIndexCaching:
    def test_index_is_built_once_per_sandbox_root(self, tmp_path):
        (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        sandbox = RepoSandbox(str(tmp_path))
        agent = TypedPreconditionHTNAgent(client=None, model="m")
        idx1 = agent._get_index(sandbox)
        idx2 = agent._get_index(sandbox)
        assert idx1 is idx2


class TestStrictCallgraphGate:
    def _agent_and_sandbox(self, tmp_path, strict: bool):
        (tmp_path / "a.py").write_text("def caller():\n    helper()\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        sandbox = RepoSandbox(str(tmp_path))
        sandbox._original["a.py"] = "def caller():\n    helper()\n"   # mark a.py as edited
        agent = TypedPreconditionHTNAgent(client=None, model="m", strict_callgraph_gate=strict)
        return agent, sandbox

    def test_off_by_default(self, tmp_path):
        agent, sandbox = self._agent_and_sandbox(tmp_path, strict=False)
        agent._current_problem_text = "the bug is that helper() returns the wrong value"
        ok, _why = agent._verify_postcondition(Node(id=1, goal="Fix the bug in a.py"), sandbox)
        assert ok is True

    def test_blocks_when_issue_text_corroborates_the_missed_symbol(self, tmp_path):
        agent, sandbox = self._agent_and_sandbox(tmp_path, strict=True)
        agent._current_problem_text = "the bug is that helper() returns the wrong value"

        ok, why = agent._verify_postcondition(Node(id=1, goal="Fix the bug in a.py"), sandbox)

        assert ok is False
        assert "helper" in why
        assert "b.py" in why

    def test_does_not_block_a_reachable_file_the_issue_never_mentions(self, tmp_path):
        """The corroboration requirement is the whole point of the narrow
        gate -- a merely-reachable file must not block on its own."""
        agent, sandbox = self._agent_and_sandbox(tmp_path, strict=True)
        agent._current_problem_text = "the login page shows a typo in the button label"

        ok, _why = agent._verify_postcondition(Node(id=1, goal="Fix the bug in a.py"), sandbox)

        assert ok is True

    def test_does_not_block_when_the_reachable_file_was_already_edited(self, tmp_path):
        agent, sandbox = self._agent_and_sandbox(tmp_path, strict=True)
        agent._current_problem_text = "the bug is that helper() returns the wrong value"
        sandbox._original["b.py"] = "def helper():\n    return 1\n"   # b.py already edited too

        ok, _why = agent._verify_postcondition(Node(id=1, goal="Fix the bug in a.py"), sandbox)

        assert ok is True


class TestRunCapturesProblemText:
    def test_problem_text_is_set_before_scheduling_begins(self, tmp_path):
        (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        sandbox = RepoSandbox(str(tmp_path))
        client = FakeClient([
            _msg(content='["Only subgoal here to run"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "done"})]),
        ])
        agent = TypedPreconditionHTNAgent(client, "m")
        instance = {"instance_id": "i1", "repo": "acme/thing",
                   "problem_statement": "helper() is broken"}

        run = agent.run(instance, sandbox, "htn_typed")

        assert agent._current_problem_text == "helper() is broken"
        assert run.htn["subgoals_done"] == 1
