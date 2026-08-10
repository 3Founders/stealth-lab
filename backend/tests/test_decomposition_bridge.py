"""
Tests for decomposition_bridge.py: translating the backend's real
DecompositionService output into htn_agent's subgoal-DAG shape, and the
holdout-leak safety check.

Driven by scripted/stubbed generators, same pattern test_decomposition.py
already uses -- no DB, no LLM, no network. The end-to-end test proves the
two systems actually compose: a scripted ChangeSet, translated by this
bridge, successfully drives HTNAgent.parse_dag(), not just that each half
works alone.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from agent import Usage  # noqa: E402
from decomposition_bridge import (  # noqa: E402
    build_decomposer, changeset_to_subgoals, decompose_issue, derive_query_postconditions,
)

from app.models.change import (  # noqa: E402
    ChangeSet, CreateEdgeOp, CreateKnowledgeNodeOp, CreateTaskNodeOp,
)
from app.services.decomposition import Decomposition, DecompositionService  # noqa: E402


class ScriptedGenerator:
    agent_id, model_id, family = "gen", "mock", "famA"

    def __init__(self, payload):
        self.payload = payload

    async def respond(self, system, user):
        return json.dumps(self.payload)


class StubDecomposer:
    """Bypasses DecompositionService entirely, returning a pre-built
    Decomposition -- used only for the holdout-leak tests below, which need
    to control `reused_nodes` directly rather than going through a real
    retriever."""

    def __init__(self, decomposition: Decomposition):
        self._decomposition = decomposition

    async def decompose(self, problem, query_postconditions=None):
        return self._decomposition


class FakeLeakPool:
    def __init__(self, task_map=None, knowledge_map=None):
        self.task_map = task_map or {}
        self.knowledge_map = knowledge_map or {}

    async def fetch(self, query, ids):
        if "task_nodes" in query:
            return [{"iid": self.task_map[i]} for i in ids if i in self.task_map]
        return [{"iid": self.knowledge_map[i]} for i in ids if i in self.knowledge_map]


class TestChangesetToSubgoals:
    def test_produces_edge_becomes_a_dep(self):
        cs = ChangeSet(ops=[
            CreateTaskNodeOp(ref="t1", name="create the helper file"),
            CreateTaskNodeOp(ref="t2", name="wire it into the caller"),
            CreateEdgeOp(edge_type="PRODUCES", source_ref="t1", target_ref="t2"),
        ])
        subgoals, diag = changeset_to_subgoals(cs)
        assert [s["goal"] for s in subgoals] == [
            "create the helper file", "wire it into the caller"]
        assert subgoals[0]["deps"] == []
        assert subgoals[1]["deps"] == [1]
        assert diag["produces_edges_applied"] == 1

    def test_non_produces_edge_adds_no_dep(self):
        cs = ChangeSet(ops=[
            CreateTaskNodeOp(ref="t1", name="first step"),
            CreateTaskNodeOp(ref="t2", name="second step"),
            CreateEdgeOp(edge_type="REQUIRES", source_ref="t1", target_ref="t2"),
        ])
        subgoals, diag = changeset_to_subgoals(cs)
        assert subgoals[1]["deps"] == []
        assert diag["produces_edges_applied"] == 0

    def test_knowledge_ops_are_dropped_and_counted(self):
        cs = ChangeSet(ops=[
            CreateTaskNodeOp(ref="t1", name="the only action"),
            CreateKnowledgeNodeOp(ref="k1", node_type="fact", name="a fact, not an action"),
        ])
        subgoals, diag = changeset_to_subgoals(cs)
        assert len(subgoals) == 1
        assert diag["knowledge_ops_dropped"] == 1

    def test_empty_changeset_returns_empty(self):
        subgoals, diag = changeset_to_subgoals(ChangeSet(ops=[]))
        assert subgoals == []
        assert diag["task_ops"] == 0

    def test_description_is_folded_into_the_goal_text(self):
        cs = ChangeSet(ops=[
            CreateTaskNodeOp(ref="t1", name="Add validation",
                             description="reject inputs missing the id field"),
        ])
        subgoals, _diag = changeset_to_subgoals(cs)
        assert subgoals[0]["goal"] == "Add validation: reject inputs missing the id field"


class TestDeriveQueryPostconditions:
    def test_includes_lang_and_area_tags(self):
        tags = derive_query_postconditions(
            {"repo_language": "go"}, ["internal/server/auth/middleware.go"])
        assert "lang:go" in tags
        assert "area:internal/server/auth" in tags
        assert "touches_test:false" in tags

    def test_without_seed_files_still_includes_lang(self):
        tags = derive_query_postconditions({"repo_language": "python"}, [])
        assert tags == ["lang:python", "touches_test:false"]


class TestDecomposeIssueEndToEnd:
    def test_valid_decomposition_yields_subgoals(self):
        generator = ScriptedGenerator({
            "feasible": True, "reasoning": "two clear steps",
            "ops": [
                {"op_type": "create_task_node", "ref": "t1", "name": "Add the cookie constant"},
                {"op_type": "create_task_node", "ref": "t2", "name": "Read it in the interceptor"},
                {"op_type": "create_edge", "edge_type": "PRODUCES",
                 "source_ref": "t1", "target_ref": "t2"},
            ],
        })
        service = DecompositionService(generator=generator)
        sample = {"problem_statement": "auth cookie is not read", "repo_language": "go"}

        subgoals, diag = asyncio.run(decompose_issue(service, sample))

        assert subgoals is not None
        assert [s["goal"] for s in subgoals] == [
            "Add the cookie constant", "Read it in the interceptor"]
        assert subgoals[1]["deps"] == [1]
        assert diag["feasible"] is True

    def test_infeasible_input_returns_none(self):
        generator = ScriptedGenerator(
            {"feasible": False, "reasoning": "not a workflow", "ops": []})
        service = DecompositionService(generator=generator)

        subgoals, diag = asyncio.run(
            decompose_issue(service, {"problem_statement": "asdfgh"}))

        assert subgoals is None
        assert diag["feasible"] is False

    def test_bridge_output_drives_the_real_htn_parser(self):
        """The composition claim: a scripted ChangeSet, translated by this
        bridge, must successfully drive HTNAgent.parse_dag -- not just that
        each half works in isolation."""
        from htn_agent import HTNAgent

        generator = ScriptedGenerator({
            "feasible": True, "reasoning": "ok",
            "ops": [
                {"op_type": "create_task_node", "ref": "t1", "name": "create the helper module"},
                {"op_type": "create_task_node", "ref": "t2", "name": "wire the helper into the caller"},
                {"op_type": "create_edge", "edge_type": "PRODUCES",
                 "source_ref": "t1", "target_ref": "t2"},
            ],
        })
        service = DecompositionService(generator=generator)
        subgoals, _diag = asyncio.run(
            decompose_issue(service, {"problem_statement": "x"}))
        assert subgoals is not None

        nodes = HTNAgent.parse_dag(json.dumps(subgoals))
        assert [n.goal for n in nodes] == [
            "create the helper module", "wire the helper into the caller"]
        assert nodes[1].deps == [1]


class TestHoldoutLeakCheck:
    def test_flags_a_leak_when_a_reused_node_belongs_to_the_held_out_instance(self):
        leaked = Decomposition(
            feasible=True, change_set=ChangeSet(ops=[]),
            reused_nodes=[{"id": "node-1", "table": "task_nodes", "name": "x",
                          "similarity": 0.95, "method": "vector"}],
        )
        pool = FakeLeakPool(task_map={"node-1": "held_out_iid"})
        decomposer = StubDecomposer(leaked)

        subgoals, diag = asyncio.run(decompose_issue(
            decomposer, {"problem_statement": "x"}, pool=pool,
            held_out_instance_id="held_out_iid"))

        assert subgoals is None
        assert diag["holdout_leaked"] is True

    def test_does_not_flag_a_match_against_a_different_instance(self):
        clean = Decomposition(
            feasible=True,
            change_set=ChangeSet(ops=[CreateTaskNodeOp(ref="t1", name="Do the thing")]),
            reused_nodes=[{"id": "node-1", "table": "task_nodes", "name": "x",
                          "similarity": 0.95, "method": "vector"}],
        )
        pool = FakeLeakPool(task_map={"node-1": "some_other_iid"})
        decomposer = StubDecomposer(clean)

        subgoals, diag = asyncio.run(decompose_issue(
            decomposer, {"problem_statement": "x"}, pool=pool,
            held_out_instance_id="held_out_iid"))

        assert subgoals is not None
        assert "holdout_leaked" not in diag

    def test_skips_the_check_when_no_pool_is_given(self):
        """held_out_instance_id alone, without a pool, must not attempt a
        DB call -- the caller may not have one to give (e.g. offline
        tests)."""
        leaked = Decomposition(
            feasible=True,
            change_set=ChangeSet(ops=[CreateTaskNodeOp(ref="t1", name="Do the thing")]),
            reused_nodes=[{"id": "node-1", "table": "task_nodes", "name": "x",
                          "similarity": 0.95, "method": "vector"}],
        )
        decomposer = StubDecomposer(leaked)
        subgoals, diag = asyncio.run(decompose_issue(
            decomposer, {"problem_statement": "x"}, held_out_instance_id="held_out_iid"))
        assert subgoals is not None
        assert "holdout_leaked" not in diag


class TestBuildDecomposer:
    def test_without_critique_has_no_critic(self):
        service = build_decomposer("test-model", None, Usage())
        assert service._critic is None
        assert service._generator.model_id == "test-model"

    def test_with_critique_has_a_critic(self):
        service = build_decomposer("test-model", None, Usage(), with_critique=True)
        assert service._critic is not None
        assert service._critic.model_id == "test-model"
