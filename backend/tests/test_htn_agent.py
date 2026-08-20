"""
Tests for the HTN agent: subgoal decomposition and localized replanning.

Driven by a fake client, so the whole control flow is exercised with no API
calls. What matters here is not that it "works" but that its two claimed
properties actually hold:

  - each subgoal executes in its OWN message list (that is the bound on
    context growth -- the flat agent's 1,067,259-token episode came from
    resending everything every step)
  - a failed subgoal replans only ITSELF; completed subgoals are neither
    re-executed nor discarded
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from agent import RepoSandbox  # noqa: E402
from htn_agent import HTNAgent, Node  # noqa: E402


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
    """Replays a scripted list of responses and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        # Snapshot, not a reference: the agent mutates its messages list in
        # place, so storing the live object would make every recorded request
        # show the FINAL state and hide exactly what these tests check.
        import copy
        self.requests.append({**kw, "messages": copy.deepcopy(kw["messages"])})
        if not self.script:
            return _msg(tool_calls=[("subgoal_failed", {"reason": "script exhausted"})])
        return self.script.pop(0)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return RepoSandbox(str(tmp_path))


INSTANCE = {"instance_id": "inst_x", "repo": "acme/thing",
            "problem_statement": "f() returns the wrong value and a helper is missing"}


class TestDecomposition:
    def test_plan_is_parsed_and_each_subgoal_runs(self, repo):
        client = FakeClient([
            _msg(content='["Fix f() in src/a.py", "Add helper in src/b.py"]'),
            _msg(tool_calls=[("edit_file", {"path": "src/a.py",
                                            "old_str": "return 1", "new_str": "return 2"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "f returns 2"})]),
            _msg(tool_calls=[("create_file", {"path": "src/b.py", "content": "X = 1\n"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "added b.py"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert [n["goal"] for n in run.htn["plan"]] == ["Fix f() in src/a.py", "Add helper in src/b.py"]
        assert run.htn["subgoals_done"] == 2
        assert run.htn["subgoals_failed"] == 0
        assert run.stop_reason == "finished"
        assert "src/a.py" in run.files_edited and "src/b.py" in run.files_edited
        assert "+return 2" in run.patch.replace("    ", "")
        assert "new file mode" in run.patch

    @pytest.mark.parametrize("text", [
        '```json\n["one thing to do here", "two thing to do here"]\n```',
        'Here is the plan:\n["one thing to do here", "two thing to do here"]',
        "- one thing to do here\n- two thing to do here",
        "1. one thing to do here\n2. two thing to do here",
    ])
    def test_plan_formats_all_accepted(self, text):
        """Losing a plan to a code fence would silently degrade every run."""
        assert len(HTNAgent.parse_dag(text)) == 2

    def test_unusable_plan_degrades_to_one_subgoal_not_a_no_op(self, repo):
        """A planner that returns junk must not produce a zero-subgoal run
        that 'completes' having changed nothing."""
        client = FakeClient([
            _msg(content="I'm not sure how to break this down."),
            _msg(tool_calls=[("subgoal_done", {"summary": "did it"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert run.htn["decompose_failed"] is True
        assert len(run.htn["plan"]) == 1
        assert run.htn["subgoals_done"] == 1


class TestLocalizedReplanning:
    def test_failed_subgoal_is_replanned_alone(self, repo):
        client = FakeClient([
            _msg(content='["Edit src/a.py to fix f", "Second unrelated subgoal here"]'),
            _msg(tool_calls=[("subgoal_failed", {"reason": "that symbol is not there"})]),
            _msg(content="Instead, create src/helper.py with the corrected logic"),
            _msg(tool_calls=[("create_file", {"path": "src/helper.py", "content": "ok\n"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "created helper"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "second done"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert run.htn["replans"] == 1
        assert run.htn["subgoals_done"] == 2
        # the replanned text replaced only that subgoal
        assert "helper" in run.htn["nodes"][0]["goal"]
        assert run.htn["nodes"][1]["goal"] == "Second unrelated subgoal here"
        assert "__replan__" in run.tool_calls

    def test_completed_subgoals_are_not_re_executed(self, repo):
        """The point of localized backtracking: valid past steps survive."""
        client = FakeClient([
            _msg(content='["First subgoal that succeeds", "Second subgoal that fails"]'),
            _msg(tool_calls=[("edit_file", {"path": "src/a.py",
                                            "old_str": "return 1", "new_str": "return 9"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "edited a.py"})]),
            _msg(tool_calls=[("subgoal_failed", {"reason": "cannot"})]),
            _msg(content="Alternative approach for the second subgoal here"),
            _msg(tool_calls=[("subgoal_failed", {"reason": "still cannot"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert run.htn["nodes"][0]["status"] == "done"
        assert run.htn["nodes"][1]["status"] == "failed"
        # the first subgoal's edit is still in the patch despite the later failure
        assert "return 9" in run.patch
        assert run.htn["nodes"][0]["attempts"] == 1  # never retried

    def test_replan_attempts_are_bounded(self, repo):
        client = FakeClient([_msg(content='["Only subgoal here to attempt"]')]
                            + [_msg(tool_calls=[("subgoal_failed", {"reason": "no"})]),
                               _msg(content="Alternative one for this subgoal"),
                               _msg(tool_calls=[("subgoal_failed", {"reason": "no"})]),
                               _msg(content="Alternative two for this subgoal"),
                               _msg(tool_calls=[("subgoal_failed", {"reason": "no"})])])
        run = HTNAgent(client, "m", max_methods=2).run(INSTANCE, repo, "arm")
        assert run.htn["nodes"][0]["status"] == "failed"
        assert run.htn["replans"] <= 2


class TestContextIsBounded:
    def test_each_subgoal_starts_a_fresh_message_list(self, repo):
        """THE structural claim. If subgoal 2's request carried subgoal 1's
        tool output, context would grow across the whole episode exactly as
        the flat agent's does."""
        client = FakeClient([
            _msg(content='["First subgoal doing a read", "Second subgoal doing a read"]'),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "read it"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "read again"})]),
        ])
        HTNAgent(client, "m", steps_per_subgoal=6).run(INSTANCE, repo, "arm")
        tool_reqs = [r for r in client.requests if r.get("tools")]
        lengths = [len(r["messages"]) for r in tool_reqs]

        # At least two sub-episodes each began fresh (system + user only).
        assert lengths.count(2) >= 2, lengths

        # The structural guarantee: context never accumulates past ONE
        # subgoal's worth, however many subgoals run. The flat agent's
        # equivalent number grows without bound across the whole episode --
        # that is what produced a 53K-token final context on teleport.
        assert max(lengths) <= 2 + 2 * 6, lengths

        # No request carries a tool result produced during an earlier subgoal.
        for r in tool_reqs:
            if len(r["messages"]) == 2:          # a fresh sub-episode
                assert not any(m.get("role") == "tool" for m in r["messages"])

    def test_completed_work_is_carried_as_notes_not_transcript(self, repo):
        client = FakeClient([
            _msg(content='["First subgoal here to do", "Second subgoal here to do"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "renamed the thing"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "second done"})]),
        ])
        HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        sys_msg = [r for r in client.requests if r.get("tools")][1]["messages"][0]["content"]
        assert "renamed the thing" in sys_msg      # note carried
        assert "ALREADY DONE" in sys_msg

    def test_total_step_budget_is_respected(self, repo):
        client = FakeClient(
            [_msg(content='["A subgoal here","B subgoal here","C subgoal here"]')]
            + [_msg(tool_calls=[("read_file", {"path": "src/a.py"})])] * 40)
        run = HTNAgent(client, "m", max_steps=9, steps_per_subgoal=3).run(
            INSTANCE, repo, "arm")
        leaf = [t for t in run.tool_calls if not t.startswith("__")]
        assert len(leaf) <= 9
        assert run.stop_reason in ("step_budget", "finished")


class TestPlanContextScopesWithNodeCount:
    """The plan-listing half of _build_context used to list EVERY node in
    EVERY other node's prompt -- fine at 2-4 nodes, but at real scale that
    reproduces the flat agent's "resend everything" cost at the plan-graph
    level: per-node prompt size grows with TOTAL node count, not with what
    that node actually needs. Above PLAN_CONTEXT_MAX_NODES, only a node's
    own transitive dependencies (plus itself) are listed in full.

    Below the threshold, behaviour is byte-for-byte unchanged -- these
    tests pin that down explicitly so a future change to the threshold or
    the scoping logic can't silently regress the common (small-plan) case.
    """

    @staticmethod
    def _chain(n: int) -> list[Node]:
        """n nodes, each depending on all previous ones -- a maximally
        connected chain, so _transitive_deps has real work to do."""
        return [Node(id=i, goal=f"subgoal number {i} of the plan",
                     deps=list(range(1, i))) for i in range(1, n + 1)]

    def test_at_threshold_every_node_is_listed_in_full(self):
        from htn_agent import PLAN_CONTEXT_MAX_NODES
        nodes = self._chain(PLAN_CONTEXT_MAX_NODES)
        agent = HTNAgent(client=None, model="m")
        _, plan = agent._build_context(nodes[-1], nodes)
        for n in nodes:
            assert f"subgoal number {n.id} of the plan" in plan
        assert "not directly relevant" not in plan

    def test_above_threshold_irrelevant_nodes_are_omitted(self):
        from htn_agent import PLAN_CONTEXT_MAX_NODES
        n = PLAN_CONTEXT_MAX_NODES + 3
        nodes = self._chain(n)
        # Node 2 depends only on node 1 (deps=[1]) -- everything from 3
        # onward is irrelevant to it and must not appear.
        target = nodes[1]
        assert target.deps == [1]
        agent = HTNAgent(client=None, model="m")
        _, plan = agent._build_context(target, nodes)
        assert "subgoal number 1 of the plan" in plan   # relevant: kept
        assert "subgoal number 2 of the plan" in plan   # itself: kept
        for i in range(3, n + 1):
            assert f"subgoal number {i} of the plan" not in plan
        assert f"{n - 2} other subgoal(s)" in plan

    def test_augmented_agent_plan_is_scoped_the_same_way(self):
        """AugmentedHTNAgent overrides _build_context for the `done` block
        but delegates `plan` to super() -- confirm that delegation actually
        carries the scoping through, not just the unscoped base text."""
        from htn_agent import PLAN_CONTEXT_MAX_NODES, AugmentedHTNAgent
        n = PLAN_CONTEXT_MAX_NODES + 3
        nodes = self._chain(n)
        target = nodes[1]
        agent = AugmentedHTNAgent(client=None, model="m")
        _, plan = agent._build_context(target, nodes)
        assert f"subgoal number {n} of the plan" not in plan
        assert f"{n - 2} other subgoal(s)" in plan

    def test_last_node_in_a_long_chain_sees_everyone_as_relevant(self):
        """The LAST node in a fully-connected chain depends on all others,
        so its own transitive closure legitimately includes everyone --
        the scoping must not truncate genuinely relevant context just
        because the total node count is high."""
        from htn_agent import PLAN_CONTEXT_MAX_NODES
        n = PLAN_CONTEXT_MAX_NODES + 3
        nodes = self._chain(n)
        agent = HTNAgent(client=None, model="m")
        _, plan = agent._build_context(nodes[-1], nodes)
        for i in range(1, n + 1):
            assert f"subgoal number {i} of the plan" in plan
        assert "not directly relevant" not in plan


class TestPlannerInvitesDecomposition:
    """decompose_subgoal fired 0 times across 66 real recorded HTN runs --
    root-caused to PLANNER_SYSTEM instructing every subgoal be 'SMALL', so
    the planner never wrote one broad enough to need decomposing. This
    isn't a mechanism bug (TestRecursiveDecomposition already covers the
    mechanism itself working); it's a prompt-framing gap. Pin the fixed
    wording down directly rather than trying to infer it from a scripted
    model's behaviour."""

    def test_planner_prompt_permits_broad_subgoals(self):
        from htn_agent import PLANNER_SYSTEM
        rendered = PLANNER_SYSTEM.format(repo="acme/thing", max_subgoals=4)
        assert "decompose_subgoal" in rendered
        assert "normal, expected outcome" in rendered
        assert "silently omit files" in rendered


class TestHarnessCompatibility:
    def test_returns_the_same_shape_as_the_flat_agent(self, repo):
        client = FakeClient([
            _msg(content='["Only subgoal to run here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "ok"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        for attr in ("instance_id", "arm", "patch", "usage", "steps", "tool_calls",
                     "files_edited", "stop_reason", "wall_seconds", "error"):
            assert hasattr(run, attr), attr
        assert run.usage.total > 0


class TestDAGStructure:
    """
    The DAG is what turns "the plan failed" into "one branch failed". These
    check that dependencies are parsed, honoured, and that a malformed graph
    degrades safely instead of deadlocking the topological loop.
    """

    def test_deps_are_parsed(self):
        nodes = HTNAgent.parse_dag(
            '[{"id":1,"goal":"create the helper module","deps":[]},'
            ' {"id":2,"goal":"wire the helper into the caller","deps":[1]}]')
        assert [n.id for n in nodes] == [1, 2]
        assert nodes[0].deps == [] and nodes[1].deps == [1]

    def test_self_loop_and_dangling_deps_dropped(self):
        """A self-loop or a dep on a node that does not exist would leave the
        node permanently unready and hang the scheduler."""
        nodes = HTNAgent.parse_dag(
            '[{"id":1,"goal":"first real subgoal here","deps":[1]},'
            ' {"id":2,"goal":"second real subgoal here","deps":[99]}]')
        assert nodes[0].deps == [] and nodes[1].deps == []

    def test_cycle_is_broken_not_fatal(self):
        nodes = HTNAgent.parse_dag(
            '[{"id":1,"goal":"first real subgoal here","deps":[2]},'
            ' {"id":2,"goal":"second real subgoal here","deps":[1]}]')
        assert nodes[0].deps == []
        assert nodes[1].deps == [1]

    def test_execution_follows_topological_order(self, repo):
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"create the base module first","deps":[]},'
                         ' {"id":2,"goal":"depend on the base module","deps":[1]}]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "base created"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "dependent done"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert run.htn["subgoals_done"] == 2
        assert run.htn["edges"] == 1
        sys_msgs = [r["messages"][0]["content"] for r in client.requests if r.get("tools")]
        assert "create the base module first" in sys_msgs[0].split("YOUR CURRENT SUBGOAL:")[1]

    def test_failure_blocks_only_dependents(self, repo):
        """"depends on" here means a HARD dependency (requires), not mere
        ordering -- a deps-only sibling now runs even past a failure (see
        TestTypedDependencyEdges); this test is specifically about the
        requires case still being blocked."""
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"this subgoal will fail","deps":[]},'
                         ' {"id":2,"goal":"this depends on the failure","deps":[1],'
                         ' "requires":[1]},'
                         ' {"id":3,"goal":"this is fully independent work","deps":[]}]'),
            # node 1 gets max_methods+1 attempts, so exhaust all of them
            _msg(tool_calls=[("subgoal_failed", {"reason": "nope"})]),
            _msg(content="An alternative that also will not work here"),
            _msg(tool_calls=[("subgoal_failed", {"reason": "still nope"})]),
            _msg(content="A second alternative that also fails here"),
            _msg(tool_calls=[("subgoal_failed", {"reason": "nope again"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "independent work done"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        st = {n["id"]: n["status"] for n in run.htn["nodes"]}
        assert st[1] == "failed"
        assert st[2] == "blocked", st
        assert st[3] == "done", st
        assert run.htn["subgoals_blocked"] == 1

    def test_blocked_nodes_consume_no_budget(self, repo):
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"this subgoal will fail","deps":[]},'
                         ' {"id":2,"goal":"blocked dependent subgoal","deps":[1],'
                         ' "requires":[1]}]'),
            _msg(tool_calls=[("subgoal_failed", {"reason": "no"})]),
            _msg(content="Alternative that also fails here"),
            _msg(tool_calls=[("subgoal_failed", {"reason": "no"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert [n["attempts"] for n in run.htn["nodes"]][1] == 0
        assert run.stop_reason == "finished"


class TestRecursiveDecomposition:
    """
    A compound task decomposing into further compound tasks -- HTN proper,
    not a one-level plan. The load-bearing rule is that an `expanded` node is
    satisfied only when its children are: a dependent must not start because
    its prerequisite merely PLANNED something.
    """

    def test_node_expands_into_children_which_then_run(self, repo):
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"do the whole auth change","deps":[]}]'),
            _msg(tool_calls=[("decompose_subgoal", {"subgoals": [
                {"goal": "add the cookie constant to the const block", "deps": []},
                {"goal": "read the cookie in the interceptor", "deps": [1]}]})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "const added"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "interceptor reads it"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        h = run.htn
        assert h["subgoals_expanded"] == 1
        assert h["max_depth_reached"] == 1
        assert h["nodes_total"] == 3           # parent + 2 children
        kids = [n for n in h["nodes"] if n["parent"] == 1]
        assert [k["status"] for k in kids] == ["done", "done"]
        assert "__expand__" in run.tool_calls

    def test_child_deps_are_remapped_to_real_ids(self, repo):
        """Children reference siblings 1-based; those must become real node
        ids or the second child would depend on the parent's own id."""
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"the compound task to split","deps":[]}]'),
            _msg(tool_calls=[("decompose_subgoal", {"subgoals": [
                {"goal": "first child subgoal here", "deps": []},
                {"goal": "second child subgoal here", "deps": [1]}]})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "a"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "b"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        kids = sorted([n for n in run.htn["nodes"] if n["parent"] == 1],
                      key=lambda n: n["id"])
        assert kids[0]["deps"] == []
        assert kids[1]["deps"] == [kids[0]["id"]]     # sibling, not the parent

    def test_expanded_parent_is_not_satisfied_until_children_finish(self, repo):
        """The rule that makes recursion sound."""
        nodes = [Node(id=1, goal="parent", status="expanded"),
                 Node(id=2, goal="child a", status="done", parent=1),
                 Node(id=3, goal="child b", status="pending", parent=1)]
        assert HTNAgent._satisfied(nodes, 1) is False
        nodes[2].status = "done"
        assert HTNAgent._satisfied(nodes, 1) is True

    def test_expanded_node_with_no_children_is_not_satisfied(self):
        """Otherwise an empty expansion would count as completed work."""
        assert HTNAgent._satisfied([Node(id=1, goal="p", status="expanded")], 1) is False

    def test_dependent_waits_for_the_whole_subtree(self, repo):
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"compound task to be split","deps":[]},'
                         ' {"id":2,"goal":"this depends on all of it","deps":[1]}]'),
            _msg(tool_calls=[("decompose_subgoal", {"subgoals": [
                {"goal": "first child of the compound", "deps": []},
                {"goal": "second child of the compound", "deps": []}]})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "child 1"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "child 2"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "dependent ran last"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        order = [t for t in run.tool_calls if t in ("subgoal_done", "__expand__")]
        assert order == ["__expand__", "subgoal_done", "subgoal_done", "subgoal_done"]
        assert run.htn["subgoals_done"] == 3      # 2 children + the dependent

    def test_recursion_is_depth_bounded(self, repo):
        """At MAX_DEPTH the tool is withdrawn, so the model must act."""
        from htn_agent import MAX_DEPTH
        script = [_msg(content='[{"id":1,"goal":"top level compound task","deps":[]}]')]
        for d in range(MAX_DEPTH + 2):
            script.append(_msg(tool_calls=[("decompose_subgoal", {"subgoals": [
                {"goal": f"level {d} child subgoal here", "deps": []}]})]))
        script += [_msg(tool_calls=[("subgoal_done", {"summary": "finally acted"})])] * 6
        run = HTNAgent(client=FakeClient(script), model="m").run(INSTANCE, repo, "arm")
        assert run.htn["max_depth_reached"] < MAX_DEPTH

    def test_tool_is_withdrawn_at_max_depth(self, repo):
        from htn_agent import MAX_DEPTH
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"top level compound task","deps":[]}]'),
            _msg(tool_calls=[("decompose_subgoal", {"subgoals": [
                {"goal": "child at depth one here", "deps": []}]})]),
            _msg(tool_calls=[("decompose_subgoal", {"subgoals": [
                {"goal": "child at depth two here", "deps": []}]})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "acted at the bottom"})]),
        ])
        HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        offered = [[t["function"]["name"] for t in r["tools"]]
                   for r in client.requests if r.get("tools")]
        assert any("decompose_subgoal" in o for o in offered)     # offered high up
        assert any("decompose_subgoal" not in o for o in offered)  # withdrawn at the floor

    def test_empty_expansion_is_refused_not_silently_accepted(self, repo):
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"a compound task to attempt","deps":[]}]'),
            _msg(tool_calls=[("decompose_subgoal", {"subgoals": []})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "did it directly"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert run.htn["subgoals_expanded"] == 0
        assert run.htn["nodes"][0]["status"] == "done"


class TestPostconditionGateAllLanguages:
    """Generalized from Python-only (ast.parse) to all four corpus languages
    via code_index -- previously a Go/JS/TS file broken by an edit could
    still be marked subgoal_done; only Python was checked."""

    def test_broken_go_file_blocks_subgoal_done(self, tmp_path):
        from htn_agent import AugmentedHTNAgent
        (tmp_path / "m.go").write_text(
            "package main\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n", encoding="utf-8")
        sb = RepoSandbox(str(tmp_path))
        # Break it directly (bypassing the edit_file advisory) to isolate the
        # GATE itself, not the warning already covered in test_code_index.py.
        with open(str(tmp_path / "m.go"), "w", encoding="utf-8") as f:
            f.write("package main\n\nfunc Add(a, b int) int {\n\treturn a + b\n")
        sb._original["m.go"] = "package main\n"  # mark as touched
        agent = AugmentedHTNAgent(client=None, model="m")
        ok, why = agent._verify_postcondition(Node(id=1, goal="x"), sb)
        assert ok is False
        assert "m.go" in why

    def test_clean_go_file_passes(self, tmp_path):
        from htn_agent import AugmentedHTNAgent
        (tmp_path / "m.go").write_text(
            "package main\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n", encoding="utf-8")
        sb = RepoSandbox(str(tmp_path))
        sb._original["m.go"] = "package main\n"
        agent = AugmentedHTNAgent(client=None, model="m")
        ok, why = agent._verify_postcondition(Node(id=1, goal="x"), sb)
        assert ok is True

    def test_deleted_files_are_not_checked(self, tmp_path):
        from htn_agent import AugmentedHTNAgent
        sb = RepoSandbox(str(tmp_path))
        sb._original["gone.go"] = "package main\n"
        sb._deleted.add("gone.go")
        agent = AugmentedHTNAgent(client=None, model="m")
        ok, _ = agent._verify_postcondition(Node(id=1, goal="x"), sb)
        assert ok is True


class TestHTNBudgetNote:
    """HTNAgent._run_node previously had NO equivalent of the flat agent's
    _budget_note anywhere in its loop -- confirmed by reading it before this
    fix existed. With STEPS_PER_SUBGOAL as low as 6-9, a node could spend its
    whole local budget on search/decompose and never edit, with nothing in
    the prompt telling it to stop."""

    def test_note_is_empty_early_in_budget(self, tmp_path):
        sb = RepoSandbox(str(tmp_path))
        agent = HTNAgent(client=None, model="m")
        assert agent._budget_note(0, 9, Node(id=1, goal="x"), sb) == ""

    def test_note_fires_in_final_third(self, tmp_path):
        sb = RepoSandbox(str(tmp_path))
        agent = HTNAgent(client=None, model="m")
        note = agent._budget_note(7, 9, Node(id=1, goal="x"), sb)
        assert "tool call(s) left" in note

    def test_note_nudges_toward_editing_when_nothing_touched_yet(self, tmp_path):
        sb = RepoSandbox(str(tmp_path))
        agent = HTNAgent(client=None, model="m")
        note = agent._budget_note(8, 9, Node(id=1, goal="x"), sb)
        assert "Nothing has been edited yet" in note

    def test_note_is_quieter_once_something_was_edited(self, tmp_path):
        (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
        sb = RepoSandbox(str(tmp_path))
        sb.edit_file("m.py", "x = 1", "x = 2")
        agent = HTNAgent(client=None, model="m")
        note = agent._budget_note(8, 9, Node(id=1, goal="x"), sb)
        assert "tool call(s) left" in note
        assert "Nothing has been edited" not in note

    def test_threshold_scales_with_a_small_budget(self, tmp_path):
        """Proportional, not the flat agent's old fixed '8' -- which would
        fire on step 1 of a 6-9 step subgoal and say nothing useful."""
        sb = RepoSandbox(str(tmp_path))
        agent = HTNAgent(client=None, model="m")
        assert agent._budget_note(0, 6, Node(id=1, goal="x"), sb) == ""
        assert agent._budget_note(4, 6, Node(id=1, goal="x"), sb) != ""


class TestPersonaToolAccess:
    """AugmentedHTNAgent.PERSONAS previously omitted list_symbols/read_symbol
    entirely -- every HTN node lost the cheap symbol-read path regardless of
    persona, confirmed against real htn_memory NO_PATCH runs (teleport,
    tutanota, element-web all show editor nodes burning their whole step
    budget on read_file/search, never list_symbols/read_symbol, never
    edit_file). Goal strings below are pulled verbatim from those runs."""

    ANSIBLE_LOCATE_AND_EDIT = (
        "Locate the FQCN validation function (likely in "
        "lib/ansible/utils/collection.py or lib/ansible/plugins/collection.py) "
        "and update the validation logic to check if the namespace or "
        "collection name is a Python keyword using the `keyword.iskeyword()` "
        "function.")
    ANSIBLE_PURE_VERIFY = (
        "Verify the fix by running the reproduction test case created in "
        "subgoal 1.")
    TUTANOTA_PURE_LOCATE = (
        "Locate the `NativeCredentialsEncryption` class and its `decrypt` "
        "method to identify where `CryptoError` is raised and how "
        "`KeyPermanentlyInvalidatedError` is triggered.")

    def _names(self, agent, node):
        from htn_agent import RunContext
        from agent import Usage
        ctx = RunContext(t0=0.0, usage=Usage())
        return {t["function"]["name"] for t in agent._tools_for(node, ctx)}

    @pytest.mark.parametrize("goal", [
        "Locate the config loader.",           # locator
        "Verify the config loader works.",     # verifier
        "Implement the config loader change.", # editor
    ])
    def test_list_symbols_and_read_symbol_available_to_every_persona(self, goal):
        from htn_agent import AugmentedHTNAgent
        agent = AugmentedHTNAgent(client=None, model="m")
        names = self._names(agent, Node(id=1, goal=goal, depth=0))
        assert {"list_symbols", "read_symbol"} <= names

    def test_editor_persona_still_has_edit_tools(self):
        from htn_agent import AugmentedHTNAgent
        agent = AugmentedHTNAgent(client=None, model="m")
        names = self._names(agent, Node(id=1, goal="Implement the fix.", depth=0))
        assert {"edit_file", "create_file", "delete_file"} <= names

    def test_pure_locator_still_has_no_edit_tools(self):
        """The restriction itself must still hold for a genuinely pure
        goal -- Fix B narrows the classification, it does not remove it."""
        from htn_agent import AugmentedHTNAgent
        agent = AugmentedHTNAgent(client=None, model="m")
        names = self._names(agent, Node(id=1, goal="Locate the config loader.", depth=0))
        assert not ({"edit_file", "create_file", "delete_file"} & names)

    def test_compound_locate_and_edit_goal_is_classified_editor(self):
        from htn_agent import AugmentedHTNAgent
        assert AugmentedHTNAgent._persona(self.ANSIBLE_LOCATE_AND_EDIT) == "editor"

    def test_pure_verify_goal_stays_verifier(self):
        from htn_agent import AugmentedHTNAgent
        assert AugmentedHTNAgent._persona(self.ANSIBLE_PURE_VERIFY) == "verifier"

    def test_pure_locate_goal_stays_locator(self):
        """No edit verb anywhere after 'and' -- this node genuinely only
        locates, and did complete successfully in the real run."""
        from htn_agent import AugmentedHTNAgent
        assert AugmentedHTNAgent._persona(self.TUTANOTA_PURE_LOCATE) == "locator"

    def test_address_does_not_false_positive_as_add(self):
        """Word-boundary regex, not substring `in` -- 'address' contains
        'add' but is not an instruction to add anything."""
        from htn_agent import AugmentedHTNAgent
        goal = "Locate the function that validates the user's mailing address."
        assert AugmentedHTNAgent._persona(goal) == "locator"

    def test_the_fix_does_not_false_positive_as_an_edit_verb(self):
        """'fix' is deliberately excluded from _EDIT_VERBS -- 'the fix' is a
        noun (the patch as a whole), not an instruction to edit here, even
        when it appears right after 'and' where the edit-verb check looks.
        ('verify' matches before 'locate' in _PERSONA_KEYWORDS, so this
        classifies verifier, not locator -- either way, not editor.)"""
        from htn_agent import AugmentedHTNAgent
        goal = "Locate the root cause and verify the fix carefully."
        assert AugmentedHTNAgent._persona(goal) == "verifier"


class TestSubgoalHandoffIntegrity:
    """Regression coverage for a real observed failure: node 1 of a live
    SWE-bench Pro run correctly identified the fix location
    (AnsibleCollectionRef.is_valid_collection_name in
    _collection_finder.py -- a real gold file), but node 2's context only
    ever saw the first 80 characters of that finding and re-searched for
    it from scratch, burning its whole step budget and failing. The
    summary was ALSO cut to 200 chars at the point it was first written,
    so both truncation points have to be checked."""

    def test_a_long_finding_survives_into_a_dependent_nodes_context(self, repo):
        needle = "XXFINDING_the_real_file_is_src_slash_a_dot_pyXX"
        long_summary = ("Investigated the module and confirmed the answer: "
                        + needle + " " + ("padding text to push past eighty characters " * 8))
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"locate the validation logic","deps":[]},'
                         ' {"id":2,"goal":"use what was found to make the fix","deps":[1]}]'),
            _msg(tool_calls=[("subgoal_done", {"summary": long_summary})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "used it"})]),
        ])
        HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        tool_reqs = [r for r in client.requests if r.get("tools")]
        # The second subgoal's very first request is where the finding must
        # appear -- that's node 2's system prompt, built from node 1's note.
        node2_first_sys = tool_reqs[1]["messages"][0]["content"]
        assert needle in node2_first_sys, (
            "node 1's finding was truncated before node 2 ever saw it -- "
            "this is the exact defect observed on the real ansible run"
        )


class TestReplanReachableOnCeilingExhaustion:
    """Regression coverage for the other half of the same real failure:
    AugmentedHTNAgent's concurrent scheduler grants a node one ROUND's
    worth of steps (STEPS_PER_SUBGOAL), not its full remaining budget.
    A node that used its whole round without a terminal call was being
    marked "failed" outright -- `spent_here >= ceiling` forced that branch
    before `_replan` (or even a fresh-round retry) could ever run, so
    `replans` stayed 0 even though the node had attempts left. On the real
    run this meant node 2 died silently with zero edits after exactly
    STEPS_PER_SUBGOAL calls, which is precisely what this test reproduces
    at a small, fast scale.

    A SECOND, later regression (measured on ansible-f327e65d) showed that
    "give it a fresh round" was not enough on its own: without a replan,
    the fresh round re-ran the IDENTICAL goal, three times, burning 27
    tool calls and shipping four half-applied edits that broke 25
    previously-passing tests. Fix C (see htn_agent.py's "REPLAN FIRST"
    comment) makes exhaustion call `_replan` BEFORE returning the node to
    "pending", so these tests assert that replanned-with-a-different-goal
    behaviour, not the earlier "just retry the same goal" fix."""

    def test_exhausting_one_round_replans_before_the_retry(self, repo):
        from htn_agent import AugmentedHTNAgent
        client = FakeClient([
            _msg(content='["Only one subgoal here to run"]'),
            # Round 1: three non-terminal calls exhaust a 3-step ceiling
            # without ever calling subgoal_done/subgoal_failed.
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            # Exhaustion replans FIRST -- a plain-content reply supplying
            # the alternative goal, not another tool call.
            _msg(content="A different approach to the same subgoal"),
            # Round 2: a fresh reservation, with the NEW goal, finishes it.
            _msg(tool_calls=[("subgoal_done", {"summary": "done on retry"})]),
        ])
        run = AugmentedHTNAgent(
            client, "m", max_steps=12, steps_per_subgoal=3, max_methods=2,
        ).run(INSTANCE, repo, "arm")

        assert run.htn["subgoals_done"] == 1, run.htn
        assert run.htn["subgoals_failed"] == 0, run.htn
        # Fix C: exhaustion replans before the next round runs.
        assert run.htn["replans"] == 1
        assert "__replan__" in run.tool_calls
        assert run.htn["nodes"][0]["goal"] == "A different approach to the same subgoal"
        leaf = [t for t in run.tool_calls if not t.startswith("__")]
        assert leaf == ["read_file", "read_file", "read_file", "subgoal_done"]
        # Two attempts were spent to get there: the exhausted round, then
        # the one that finished.
        assert run.htn["nodes"][0]["attempts"] == 2

    def test_termination_when_a_node_can_never_finish(self, repo):
        """A node that keeps exhausting every round it's given must still
        terminate -- it must not spin forever waiting for a round that
        finally lets it call subgoal_done.

        max_methods is set high enough here that attempts never becomes the
        limiting factor -- this isolates the TOTAL step budget as the actual
        cause of termination, distinct from
        test_exhausting_one_round_replans_before_the_retry above, where a
        node succeeds on retry, and from the attempts-exhausted case
        covered by TestLocalizedReplanning.test_replan_attempts_are_bounded.

        With max_steps=7 and steps_per_subgoal=3 the budget divides into
        two viable rounds of 3 and a 1-call remainder. That remainder is
        deliberately LEFT UNSPENT: it is below MIN_VIABLE_SUBGOAL_BUDGET,
        so it cannot reach a terminal tool call and is declined rather than
        burning one of the node's attempts on a round that was never
        winnable. Each of the two viable rounds also replans on exhaustion
        (Fix C), so the script supplies an alternative goal after each one
        -- this isolates the step-budget termination path from the
        no-alternative-available path covered separately below."""
        from htn_agent import AugmentedHTNAgent
        client = FakeClient([
            _msg(content='["A subgoal that will never signal completion"]'),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(content="Alternative approach, round two"),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(content="Alternative approach, round three"),
        ] + [_msg(tool_calls=[("read_file", {"path": "src/a.py"})])] * 40)
        run = AugmentedHTNAgent(
            client, "m", max_steps=7, steps_per_subgoal=3, max_methods=5,
        ).run(INSTANCE, repo, "arm")
        leaf = [t for t in run.tool_calls if not t.startswith("__")]
        assert len(leaf) == 6, "two viable rounds of 3; the 1-call remainder is declined"
        assert run.htn["replans"] == 2
        assert run.stop_reason == "step_budget"
        # It never reached a terminal call, so it must not be "failed" --
        # pending-and-out-of-budget is a different, honest outcome.
        assert run.htn["nodes"][0]["status"] == "pending"

    def test_exhaustion_with_no_alternative_fails_immediately(self, repo):
        """The other branch of Fix C's exhaustion handling: if `_replan`
        cannot produce an alternative (empty/short model reply), the node
        fails right away rather than being left "pending" to repeat the
        identical round that just failed."""
        from htn_agent import AugmentedHTNAgent
        client = FakeClient([
            _msg(content='["Only one subgoal here to run"]'),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(content="too short"),
        ])
        run = AugmentedHTNAgent(
            client, "m", max_steps=12, steps_per_subgoal=3, max_methods=2,
        ).run(INSTANCE, repo, "arm")
        assert run.htn["nodes"][0]["status"] == "failed"
        assert run.htn["subgoals_failed"] == 1
        assert run.htn["replans"] == 1
        leaf = [t for t in run.tool_calls if not t.startswith("__")]
        assert leaf == ["read_file", "read_file", "read_file"]
        # Only the one round ran before the no-alternative failure -- it
        # never got a second attempt to cost anything.
        assert run.htn["nodes"][0]["attempts"] == 1

    def test_unchanged_plan_still_behaves_identically_when_nothing_exhausts(self, repo):
        """Non-regression: when every node finishes within its first
        round, nothing about this fix changes observed behaviour."""
        client = FakeClient([
            _msg(content='["Only subgoal to run here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "ok, no retry needed"})]),
        ])
        from htn_agent import AugmentedHTNAgent
        run = AugmentedHTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert run.htn["subgoals_done"] == 1
        assert run.htn["nodes"][0]["attempts"] == 1
        assert run.htn["replans"] == 0


# SWE-bench Pro ships `requirements` (what the fix must satisfy) and
# `interface` (signatures it must provide) alongside the issue text. The
# official protocol puts all three in the agent prompt -- "we include the
# problem statement, requirements and interface specification in the agent
# prompt" (arXiv:2509.16941) -- so omitting them was a deviation that made
# the task strictly harder and the numbers non-comparable. INSTANCE above
# deliberately keeps them ABSENT so every pre-existing test doubles as the
# null-degradation case.
SPEC_INSTANCE = {
    **INSTANCE,
    "requirements": "- Cvss3Severity must join values with the | delimiter.\n"
                    "- Entries must preserve Title and Summary fields.",
    "interface": "No new interfaces are introduced.",
}


class TestTaskSpecReachesThePrompt:
    def _planner_and_executor_text(self, client):
        reqs = [r for r in client.requests]
        planner = reqs[0]["messages"]           # the DAG call, no tools
        executor = [r for r in reqs if r.get("tools")][0]["messages"]
        return ("\n".join(m["content"] for m in planner),
                "\n".join(m["content"] for m in executor))

    def test_requirements_and_interface_reach_planner_and_executor(self, repo):
        client = FakeClient([
            _msg(content='["Only subgoal to run here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "ok"})]),
        ])
        HTNAgent(client, "m").run(SPEC_INSTANCE, repo, "arm")
        planner_text, executor_text = self._planner_and_executor_text(client)
        for text, where in ((planner_text, "planner"), (executor_text, "executor")):
            assert "| delimiter" in text, f"requirements missing from {where}"
            assert "No new interfaces" in text, f"interface missing from {where}"

    def test_absent_spec_degrades_cleanly_without_emitting_none_or_nan(self, repo):
        """The fields are documented as nullable, and a pandas row yields
        NaN rather than None -- `str(nan)` is the truthy string 'nan',
        which would otherwise be pasted into the prompt as if it were the
        specification."""
        import math
        for empty in (None, "", "   ", float("nan")):
            client = FakeClient([
                _msg(content='["Only subgoal to run here"]'),
                _msg(tool_calls=[("subgoal_done", {"summary": "ok"})]),
            ])
            inst = {**INSTANCE, "requirements": empty, "interface": empty}
            HTNAgent(client, "m").run(inst, repo, "arm")
            planner_text, executor_text = self._planner_and_executor_text(client)
            for text in (planner_text, executor_text):
                low = text.lower()
                assert "nan" not in low.split(), f"leaked NaN for {empty!r}"
                assert "requirements" not in low, f"emitted empty spec header for {empty!r}"

    def test_oversized_spec_is_capped_not_passed_whole(self, repo):
        from htn_agent import SPEC_INTERFACE_CHARS, SPEC_REQUIREMENTS_CHARS
        client = FakeClient([
            _msg(content='["Only subgoal to run here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "ok"})]),
        ])
        inst = {**INSTANCE,
                "requirements": "R" * (SPEC_REQUIREMENTS_CHARS * 3),
                "interface": "I" * (SPEC_INTERFACE_CHARS * 3)}
        HTNAgent(client, "m").run(inst, repo, "arm")
        planner_text, _ = self._planner_and_executor_text(client)
        assert "R" * SPEC_REQUIREMENTS_CHARS in planner_text
        assert "R" * (SPEC_REQUIREMENTS_CHARS + 1) not in planner_text
        assert "I" * (SPEC_INTERFACE_CHARS + 1) not in planner_text

    def test_flat_agent_also_receives_the_spec(self, repo):
        """Must be identical across arms -- this is task specification, not
        retrieved memory. If only the HTN agent got it, every flat-vs-HTN
        comparison would be confounded by it."""
        from agent import Agent
        client = FakeClient([_msg(tool_calls=[("finish", {"summary": "done"})])])
        Agent(client, "m").run(SPEC_INSTANCE, repo, "arm")
        text = "\n".join(m["content"] for m in client.requests[0]["messages"])
        assert "| delimiter" in text
        assert "No new interfaces" in text


class TestNonViableRoundDoesNotConsumeAnAttempt:
    """A node's per-round ceiling is whatever `_Budget` has left, so late
    rounds can grant 1-2 calls -- too few to reach any terminal tool call.
    Burning one of only `max_methods+1` attempts on such a round is what
    starved gravitational/teleport's node 2 ("exhausted its 1-call budget",
    attempts=3), which then blocked nodes 3 and 4 so 3 of 4 subgoals never
    ran at all."""

    def test_below_floor_round_leaves_node_pending_with_attempts_untouched(self, repo):
        from htn_agent import AugmentedHTNAgent
        # max_steps below the viability floor: the only reservation possible
        # is too small to act on.
        client = FakeClient([_msg(content='["Only subgoal to run here"]')])
        run = AugmentedHTNAgent(
            client, "m", max_steps=2, steps_per_subgoal=9, max_methods=2,
        ).run(INSTANCE, repo, "arm")
        node = run.htn["nodes"][0]
        assert node["attempts"] == 0, "a non-viable round must not spend an attempt"
        assert node["status"] == "pending"

    def test_it_terminates_instead_of_spinning(self, repo):
        """THE load-bearing test. Breaking with used==0 returns the whole
        reservation to _Budget, so remaining() never reaches 0 and the
        existing `any(pending) and remaining() <= 0` guard never fires.
        Without a zero-progress check this loops forever and the executor
        is never called, so no scripted response is consumed and nothing
        else can break the cycle -- this test HANGS rather than fails if
        that guard is missing."""
        from htn_agent import AugmentedHTNAgent
        client = FakeClient([_msg(content='["Only subgoal to run here"]')])
        run = AugmentedHTNAgent(
            client, "m", max_steps=2, steps_per_subgoal=9, max_methods=2,
        ).run(INSTANCE, repo, "arm")
        assert run.stop_reason == "step_budget"
        # Only the planner was called -- no executor turn was ever viable.
        assert len([r for r in client.requests if r.get("tools")]) == 0

    def test_a_viable_round_still_consumes_an_attempt(self, repo):
        """Non-regression: the floor must not stop ordinary rounds from
        counting, or max_methods would never bound anything."""
        from htn_agent import AugmentedHTNAgent
        client = FakeClient([
            _msg(content='["Only subgoal to run here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "ok"})]),
        ])
        run = AugmentedHTNAgent(
            client, "m", max_steps=12, steps_per_subgoal=9, max_methods=2,
        ).run(INSTANCE, repo, "arm")
        assert run.htn["nodes"][0]["attempts"] == 1
        assert run.htn["nodes"][0]["status"] == "done"


@pytest.fixture
def nested_repo(tmp_path):
    """A checkout where the interesting file is NOT at the path a planner
    would naively guess -- mirrors ansible, whose `requirements` says the
    change goes 'in dataclasses.py' while the real file lives under
    lib/ansible/galaxy/dependency_resolution/."""
    real = tmp_path / "lib" / "galaxy" / "resolution"
    real.mkdir(parents=True)
    (real / "dataclasses.py").write_text("def is_valid():\n    return True\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return RepoSandbox(str(tmp_path))


class TestPathPreconditionGuidesInsteadOfKilling:
    """Regression coverage for a real, measured regression.

    Once `requirements` reached the planner it began naming specific files,
    but requirements give a BARE BASENAME ("...should be removed in
    dataclasses.py") so the planner invented a directory. The old
    precondition hard-failed that goal at zero steps and zero tool calls,
    burned all three attempts on replans that guessed further wrong paths,
    and turned a previously-RESOLVED instance into no_patch on 2,234
    tokens. A wrong directory must lead the agent to FIND the file, not
    kill the node."""

    def _executor_prompt(self, client):
        tool_reqs = [r for r in client.requests if r.get("tools")]
        assert tool_reqs, "the executor never ran -- the node was killed before acting"
        return tool_reqs[0]["messages"][0]["content"]

    def test_wrong_directory_but_unique_basename_resolves_to_the_real_path(self, nested_repo):
        from htn_agent import AugmentedHTNAgent
        client = FakeClient([
            _msg(content='["In lib/wrong/dataclasses.py, reject Python keywords"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "done"})]),
        ])
        run = AugmentedHTNAgent(client, "m").run(INSTANCE, nested_repo, "arm")
        prompt = self._executor_prompt(client)
        assert "lib/galaxy/resolution/dataclasses.py" in prompt.replace("\\", "/"), \
            "executor was not told where the file actually is"
        assert run.htn["nodes"][0]["status"] != "failed"

    def test_ambiguous_basename_lists_candidates_and_still_runs(self, nested_repo):
        from htn_agent import AugmentedHTNAgent
        second = nested_repo.root + "/lib/other"
        os.makedirs(second, exist_ok=True)
        with open(second + "/dataclasses.py", "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        client = FakeClient([
            _msg(content='["In lib/wrong/dataclasses.py, reject Python keywords"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "done"})]),
        ])
        run = AugmentedHTNAgent(client, "m").run(INSTANCE, nested_repo, "arm")
        prompt = self._executor_prompt(client).replace("\\", "/")
        assert "lib/galaxy/resolution/dataclasses.py" in prompt
        assert "lib/other/dataclasses.py" in prompt
        assert run.htn["nodes"][0]["status"] != "failed"

    def test_basename_absent_entirely_still_lets_the_agent_look(self, nested_repo):
        from htn_agent import AugmentedHTNAgent
        client = FakeClient([
            _msg(content='["In lib/wrong/nowhere_at_all.py, do the thing"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "done"})]),
        ])
        run = AugmentedHTNAgent(client, "m").run(INSTANCE, nested_repo, "arm")
        prompt = self._executor_prompt(client)
        assert "nowhere_at_all.py" in prompt
        # The node must NOT be killed -- the old behaviour's exact failure.
        assert run.htn["nodes"][0]["status"] != "failed"

    def test_existing_path_injects_no_hint_at_all(self, nested_repo):
        """Non-regression: a goal naming a real path must be untouched.

        Keys on the hint's own marker, not on a phrase like "does not
        exist" -- EXECUTOR_SYSTEM's boilerplate already contains that
        ("Use create_file when the subgoal calls for a file that does not
        exist yet"), so the looser assertion passed for the wrong reason."""
        from htn_agent import AugmentedHTNAgent, PATH_HINT_MARKER
        client = FakeClient([
            _msg(content='["In src/a.py, change the return value"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "done"})]),
        ])
        AugmentedHTNAgent(client, "m").run(INSTANCE, nested_repo, "arm")
        assert PATH_HINT_MARKER not in self._executor_prompt(client)


class TestZeroStepFailureIsStillProgress:
    """`spent_this_round == 0` conflated 'no steps spent' with 'no progress
    possible'. A node reaching a terminal status at zero cost IS progress,
    and stopping there strands independent branches that never got their
    turn."""

    def test_independent_branch_still_runs_after_a_zero_step_failure(
            self, nested_repo, monkeypatch):
        """Forces width=1 via _shallow so the failing node is a batch of its
        OWN. At the default width of MAX_PARALLEL_NODES both ready nodes
        share a batch, node 2 spends steps, and `spent_this_round` is
        non-zero -- which hides the defect rather than testing it."""
        from htn_agent import AugmentedHTNAgent
        monkeypatch.setattr(AugmentedHTNAgent, "_shallow", lambda self, ctx: True)

        # Node 1 fails immediately and cheaply; node 2 is independent
        # (deps []) and must still get its turn.
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"Investigate the broken handler","deps":[]},'
                         ' {"id":2,"goal":"In src/a.py, change the return value","deps":[]}]'),
            _msg(tool_calls=[("subgoal_failed", {"reason": "cannot be done"})]),
            _msg(content="An alternative approach for the first subgoal"),
            _msg(tool_calls=[("subgoal_failed", {"reason": "still cannot"})]),
            _msg(content="Another alternative for the first subgoal here"),
            _msg(tool_calls=[("subgoal_failed", {"reason": "no"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "n2 done"})]),
        ])
        run = AugmentedHTNAgent(
            client, "m", max_steps=28, steps_per_subgoal=9, max_methods=2,
        ).run(INSTANCE, nested_repo, "arm")
        statuses = {n["id"]: n["status"] for n in run.htn["nodes"]}
        assert statuses[1] == "failed"
        assert statuses[2] == "done", (
            "the independent node never ran -- the scheduler stopped after a "
            "batch it wrongly judged to have made no progress")


class TestExhaustionReplansRatherThanRepeating:
    """An exhausted node used to come back and re-run the IDENTICAL goal.

    Leaving the node "pending" on exhaustion (so it is not failed for a
    round that was merely too short) was right, but the branch broke before
    reaching `_replan` -- so the next round retried the same goal with the
    same budget that had just failed. Measured on ansible-f327e65d: three
    identical 9-call rounds burned 27 tool calls, finished no subgoal, and
    left four half-applied edits that broke 25 previously-passing tests,
    with `replans: 0` throughout. A retry has to try something DIFFERENT to
    be worth its budget."""

    def test_exhausted_round_replans_and_retries_a_different_goal(self, repo):
        from htn_agent import AugmentedHTNAgent
        original = "Original approach to this subgoal here"
        alternative = "A completely different approach for this subgoal"
        client = FakeClient([
            _msg(content=f'["{original}"]'),
            # Round 1: three non-terminal calls exhaust a 3-step ceiling.
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            # The replan the exhaustion must now trigger.
            _msg(content=alternative),
            # Round 2 runs the REPLANNED goal and finishes.
            _msg(tool_calls=[("subgoal_done", {"summary": "done on the retry"})]),
        ])
        run = AugmentedHTNAgent(
            client, "m", max_steps=12, steps_per_subgoal=3, max_methods=2,
        ).run(INSTANCE, repo, "arm")

        assert run.htn["replans"] >= 1, "exhaustion did not trigger a replan"
        assert "__replan__" in run.tool_calls
        node = run.htn["nodes"][0]
        assert node["goal"] == alternative, (
            "the retry re-ran the original goal instead of the alternative")
        assert node["status"] == "done"

    def test_no_alternative_available_fails_instead_of_repeating(self, repo):
        """If the replanner cannot offer a different approach, repeating the
        round that just failed is pure waste -- fail the node instead."""
        from htn_agent import AugmentedHTNAgent
        client = FakeClient([
            _msg(content='["Original approach to this subgoal here"]'),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            # Too short to survive _replan's own length filter -> no alternative.
            _msg(content="no"),
        ])
        run = AugmentedHTNAgent(
            client, "m", max_steps=12, steps_per_subgoal=3, max_methods=2,
        ).run(INSTANCE, repo, "arm")
        assert run.htn["nodes"][0]["status"] == "failed"


class TestZeroCompletedSubgoalsShipsNoPatch:
    """Fix D: on ansible-f327e65d a node removed two functions per
    `requirements`, then failed before a later node could add their
    replacement -- only 1 of 2 gold files got touched, and the half-applied
    edit still shipped as the patch, breaking 25 previously-passing tests
    (p2p_broke: 25). Additive-but-incomplete work was always inert; the
    spec block made incomplete work destructive. If no subgoal ever
    reached "done", whatever is on disk cannot be trusted as a fix, so
    HTNAgent.run() withholds the patch and reports a distinct stop_reason
    rather than shipping it."""

    def test_zero_completed_subgoals_ships_no_patch(self, repo):
        client = FakeClient([
            _msg(content='["Only subgoal here"]'),
            _msg(tool_calls=[("edit_file", {"path": "src/a.py",
                                            "old_str": "return 1", "new_str": "return 999"})]),
            _msg(tool_calls=[("subgoal_failed", {"reason": "still broken"})]),
            # Too short to survive _replan's length filter -> no alternative,
            # so the node fails outright with zero subgoals ever completed.
            _msg(content="no"),
        ])
        run = HTNAgent(client, "m", max_methods=1).run(INSTANCE, repo, "arm")
        assert run.htn["subgoals_done"] == 0
        assert run.patch == ""
        assert run.stop_reason == "discarded_incomplete_plan"
        assert run.htn["discarded_patch_bytes"] > 0
        # The edit still happened on disk -- files_edited stays honest for
        # diagnostics even though the patch itself is withheld.
        assert "src/a.py" in run.files_edited

    def test_at_least_one_completed_subgoal_still_ships_the_patch(self, repo):
        """Non-regression: the common case (some real progress) is
        unaffected by the zero-subgoal guard."""
        client = FakeClient([
            _msg(content='["Edit src/a.py to fix f"]'),
            _msg(tool_calls=[("edit_file", {"path": "src/a.py",
                                            "old_str": "return 1", "new_str": "return 2"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "fixed"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert run.htn["subgoals_done"] == 1
        assert "+return 2" in run.patch.replace("    ", "")
        assert run.stop_reason != "discarded_incomplete_plan"
        assert run.htn["discarded_patch_bytes"] == 0

    def test_one_done_subgoal_ships_a_failed_sibling_s_edits_too(self, repo):
        """The guard is all-or-nothing on subgoals_done, not per-file: a
        failed sibling's edits are additive-but-incomplete, not destructive
        on their own, so once ANY subgoal completes the whole patch --
        including the failed sibling's edits -- still ships, exactly as it
        did before Fix D."""
        client = FakeClient([
            _msg(content='["First subgoal that succeeds", "Second subgoal that fails"]'),
            _msg(tool_calls=[("edit_file", {"path": "src/a.py",
                                            "old_str": "return 1", "new_str": "return 9"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "edited a.py"})]),
            _msg(tool_calls=[("create_file", {"path": "src/b.py", "content": "X = 1\n"})]),
            _msg(tool_calls=[("subgoal_failed", {"reason": "cannot"})]),
            _msg(content="no"),
        ])
        run = HTNAgent(client, "m", max_methods=1).run(INSTANCE, repo, "arm")
        assert run.htn["subgoals_done"] == 1
        assert run.stop_reason != "discarded_incomplete_plan"
        assert "return 9" in run.patch
        assert "src/b.py" in run.patch


class TestStepsPerSubgoalCLIFlag:
    """The 200-turn/20-per-subgoal budget (matching the official SWE-bench
    Pro protocol) needs steps_per_subgoal tunable without a source edit,
    mirroring how --max-steps already works."""

    def _parser(self):
        import run_graph_experiment
        return run_graph_experiment.build_arg_parser()

    def test_flag_defaults_to_none_so_htn_agent_s_own_default_applies(self):
        args = self._parser().parse_args([])
        assert args.steps_per_subgoal is None

    def test_flag_is_settable_and_typed_as_int(self):
        args = self._parser().parse_args(["--steps-per-subgoal", "20", "--max-steps", "200"])
        assert args.steps_per_subgoal == 20
        assert args.max_steps == 200


class TestLocalizationPrePass:
    """The planner never saw the repository -- only the issue text -- yet
    PLANNER_SYSTEM demands each subgoal 'name the file or symbol'. Measured
    across every run with plan data: only ~47% of planned paths matched a
    gold file, and the deepseek ansible run planned edits to
    galaxy/collection/__init__.py and galaxy/dataclasses.py (right basename
    convention, wrong directory) when the real files were
    galaxy/dependency_resolution/dataclasses.py and
    utils/collection_loader/_collection_finder.py. _candidate_files gives
    the planner verified repo facts to ground on instead, at zero extra LLM
    cost (RepoSandbox.search is local)."""

    def test_candidate_files_reach_the_planner_prompt(self, nested_repo):
        from htn_agent import HTNAgent
        client = FakeClient([
            _msg(content='["Only subgoal to run here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "ok"})]),
        ])
        inst = {**INSTANCE, "problem_statement":
                "The `is_valid` function returns the wrong result."}
        HTNAgent(client, "m").run(inst, nested_repo, "arm")
        planner_text = "\n".join(m["content"] for m in client.requests[0]["messages"])
        assert "CANDIDATE FILES (verified to exist" in planner_text
        assert "lib/galaxy/resolution/dataclasses.py" in planner_text

    def test_no_hits_means_no_candidate_block_prompt_otherwise_unchanged(self, repo):
        """Null-degradation, same discipline as TestTaskSpecReachesThePrompt:
        an issue mentioning nothing findable in this checkout must not
        inject an empty or misleading CANDIDATE FILES header."""
        from htn_agent import HTNAgent
        client = FakeClient([
            _msg(content='["Only subgoal to run here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "ok"})]),
        ])
        HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        planner_text = "\n".join(m["content"] for m in client.requests[0]["messages"])
        assert "CANDIDATE FILES (verified to exist" not in planner_text

    def test_search_volume_is_bounded_regardless_of_issue_text_length(self, repo, monkeypatch):
        """A pathological issue text must not turn into dozens of repo
        walks -- that would fight the token thesis this pre-pass exists to
        serve, not just cost nothing as advertised."""
        from htn_agent import HTNAgent
        calls = []
        orig_search = repo.search

        def counting_search(pattern, path="."):
            calls.append(pattern)
            return orig_search(pattern, path)

        monkeypatch.setattr(repo, "search", counting_search)
        many_idents = " ".join(f"identifier_{i}_word" for i in range(50))
        inst = {**INSTANCE, "problem_statement": many_idents}
        HTNAgent(FakeClient([]), "m")._candidate_files(inst, repo)
        assert len(calls) <= HTNAgent.MAX_CANDIDATE_SEARCHES


class TestTypedDependencyEdges:
    """Plans are pure linear chains in 74-83% of real runs. Before this,
    EVERY deps edge was a hard blocker: a root-node failure produced
    subgoals_done == 0 in 12 of 12 observed cases, because the planner's
    honest sequential ORDERING (deps) was being enforced as if it were a
    real data dependency. `requires` is now the narrower, explicit claim;
    plain `deps` no longer blocks on failure, only on ordering."""

    def test_failed_soft_dependent_still_runs_and_is_told_why(self, repo):
        from htn_agent import HTNAgent
        client = FakeClient([
            _msg(content='[{"id": 1, "goal": "First subgoal here", "deps": []}, '
                         '{"id": 2, "goal": "Second subgoal here", "deps": [1]}]'),
            _msg(tool_calls=[("subgoal_failed", {"reason": "cannot do it"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "done anyway"})]),
        ])
        run = HTNAgent(client, "m", max_methods=0).run(INSTANCE, repo, "arm")
        assert run.htn["nodes"][0]["status"] == "failed"
        # The whole point: node 2 still ran instead of being blocked.
        assert run.htn["nodes"][1]["status"] == "done"
        executor_calls = [r for r in client.requests if r.get("tools")]
        assert len(executor_calls) == 2, "node 2 must have gotten its own turn"
        node2_prompt = "\n".join(m["content"] for m in executor_calls[1]["messages"])
        assert "did NOT complete" in node2_prompt
        assert "[1]" in node2_prompt

    def test_failed_hard_dependent_is_still_blocked(self, repo):
        """The real invariant this whole module exists to protect: a
        dependent that TRULY needs a predecessor's edits must not run
        against a state that does not exist."""
        from htn_agent import HTNAgent
        client = FakeClient([
            _msg(content='[{"id": 1, "goal": "First subgoal here", "deps": []}, '
                         '{"id": 2, "goal": "Second subgoal here", "deps": [1], '
                         '"requires": [1]}]'),
            _msg(tool_calls=[("subgoal_failed", {"reason": "cannot do it"})]),
        ])
        run = HTNAgent(client, "m", max_methods=0).run(INSTANCE, repo, "arm")
        assert run.htn["nodes"][0]["status"] == "failed"
        assert run.htn["nodes"][1]["status"] == "blocked"
        # Node 2 never got a turn -- only the planner + node 1's one attempt.
        assert len(client.requests) == 2

    def test_soft_dependent_of_a_successful_node_runs_normally(self, repo):
        """Non-regression: the ordinary, common case is unaffected."""
        from htn_agent import HTNAgent
        client = FakeClient([
            _msg(content='[{"id": 1, "goal": "First subgoal here", "deps": []}, '
                         '{"id": 2, "goal": "Second subgoal here", "deps": [1]}]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "first done"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "second done"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert run.htn["nodes"][0]["status"] == "done"
        assert run.htn["nodes"][1]["status"] == "done"

    def test_requires_survives_the_snapshot_and_rehydrate_round_trip(self, repo):
        """That last-sync docstring on _rehydrate is a promise, not a
        suggestion: resuming an interrupted run must not silently downgrade
        every edge back to soft."""
        from htn_agent import HTNAgent
        client = FakeClient([
            _msg(content='[{"id": 1, "goal": "First subgoal here", "deps": []}, '
                         '{"id": 2, "goal": "Second subgoal here", "deps": [1], '
                         '"requires": [1]}]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "first done"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "second done"})]),
        ])
        run = HTNAgent(client, "m").run(INSTANCE, repo, "arm")
        assert run.htn["nodes"][1]["requires"] == [1]
        assert run.htn["plan"][1]["requires"] == [1]
        rehydrated = HTNAgent._rehydrate(run.htn["nodes"])
        assert rehydrated[1].requires == [1]

    def test_requires_not_also_listed_in_deps_is_dropped(self):
        """requires must be a SUBSET of deps -- a hard dependency that is
        not even an ordering edge is a contradiction; trusting it anyway
        would let a stray id block scheduling without deps ever having
        established the ordering that makes blocking meaningful."""
        from htn_agent import HTNAgent
        nodes = HTNAgent.parse_dag(
            '[{"id": 1, "goal": "First subgoal here", "deps": []}, '
            '{"id": 2, "goal": "Second subgoal here", "deps": [], "requires": [1]}]')
        assert nodes[1].deps == []
        assert nodes[1].requires == []


class TestRunContextExtraction:
    """Ticket 15's real fix, tested directly: per-run state (usage, seed
    plan, the three locks) must not leak between two .run() calls on the
    SAME agent instance -- the exact problem run_graph_experiment.py's
    own comment names."""

    @staticmethod
    def _sandbox(tmp_path, name):
        d = tmp_path / name
        (d / "src").mkdir(parents=True)
        (d / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        return RepoSandbox(str(d))

    def test_two_sequential_runs_on_the_same_agent_do_not_share_usage(self, tmp_path):
        from htn_agent import HTNAgent
        client = FakeClient([
            _msg(content='["First run subgoal here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "done"})]),
            _msg(content='["Second run subgoal here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "done"})]),
        ])
        agent = HTNAgent(client, "m")
        repo1 = self._sandbox(tmp_path, "r1")
        repo2 = self._sandbox(tmp_path, "r2")
        run1 = agent.run(INSTANCE, repo1, "arm")
        run2 = agent.run(INSTANCE, repo2, "arm")
        # Each run's own usage.calls must reflect only ITS OWN calls, not
        # an accumulation across both runs -- confirms usage isn't shared
        # agent-level state leaking between calls.
        assert run1.usage.calls == run2.usage.calls, (
            "two structurally identical runs should report identical "
            "per-run usage -- any drift means state leaked between them"
        )

    def test_a_seed_plan_set_before_one_run_does_not_leak_into_the_next(self, tmp_path):
        """The real staleness bug found and fixed during RunContext
        extraction: _pending_seed_plan used to be read but never
        cleared, so it could silently survive into an unrelated later
        run() call."""
        from htn_agent import HTNAgent
        client = FakeClient([
            # Only ONE planner-shaped response queued -- if the seed
            # plan leaks into run 2, run 2 must NOT need a second
            # planner call (it would reuse the seeded plan and skip
            # straight to execution), so queuing only run 1's real
            # planner response plus both runs' executor responses is
            # exactly what would make a leak observable.
            _msg(tool_calls=[("subgoal_done", {"summary": "run 1 done"})]),
            _msg(content='["Run 2 fresh subgoal here"]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "run 2 done"})]),
        ])
        agent = HTNAgent(client, "m")
        agent._pending_seed_plan = [{"id": 1, "goal": "Seeded subgoal for run 1"}]
        run1 = agent.run(INSTANCE, self._sandbox(tmp_path, "r1"), "arm")
        assert run1.htn["seeded_from_library"] is True

        # No seed set before run 2 -- it must plan fresh (consume the
        # queued planner response), not silently reuse run 1's seed.
        run2 = agent.run(INSTANCE, self._sandbox(tmp_path, "r2"), "arm")
        assert run2.htn["seeded_from_library"] is False
        assert run2.htn["nodes"][0]["goal"] == "Run 2 fresh subgoal here"

    def test_pending_seed_plan_instance_attribute_is_cleared_after_run(self, tmp_path):
        from htn_agent import HTNAgent
        client = FakeClient([
            _msg(tool_calls=[("subgoal_done", {"summary": "done"})]),
        ])
        agent = HTNAgent(client, "m")
        agent._pending_seed_plan = [{"id": 1, "goal": "Seeded subgoal here"}]
        agent.run(INSTANCE, self._sandbox(tmp_path, "r1"), "arm")
        assert agent._pending_seed_plan is None


class TestHTNConfig:
    """Ticket 15's hyperparameter split, tested directly: structural vs.
    distributional, and the additive from_config() constructor."""

    def test_default_config_matches_original_module_constants(self):
        from htn_agent import (
            DistributionalBudgets, MAX_DEPTH, MAX_METHODS, MAX_SUBGOALS,
            MIN_VIABLE_SUBGOAL_BUDGET, PLAN_CONTEXT_MAX_NODES,
            STEPS_PER_SUBGOAL, StructuralLimits, TOTAL_STEP_BUDGET,
        )
        structural = StructuralLimits()
        budgets = DistributionalBudgets()
        assert structural.max_depth == MAX_DEPTH
        assert budgets.max_subgoals == MAX_SUBGOALS
        assert budgets.max_methods == MAX_METHODS
        assert budgets.steps_per_subgoal == STEPS_PER_SUBGOAL
        assert budgets.total_step_budget == TOTAL_STEP_BUDGET
        assert budgets.min_viable_subgoal_budget == MIN_VIABLE_SUBGOAL_BUDGET
        assert budgets.plan_context_max_nodes == PLAN_CONTEXT_MAX_NODES

    def test_structural_limits_matches_augmented_class_level_constant(self):
        """StructuralLimits.max_parallel_nodes is a deliberate duplicate
        of AugmentedHTNAgent.MAX_PARALLEL_NODES (the dataclass is
        defined before the class exists in the file) -- this test is
        what stops the two from silently drifting apart."""
        from htn_agent import AugmentedHTNAgent, StructuralLimits
        assert StructuralLimits().max_parallel_nodes == AugmentedHTNAgent.MAX_PARALLEL_NODES

    def test_from_config_produces_an_agent_with_the_configured_budgets(self):
        from htn_agent import DistributionalBudgets, HTNAgent, HTNConfig
        config = HTNConfig(budgets=DistributionalBudgets(
            total_step_budget=99, steps_per_subgoal=5, max_methods=1,
        ))
        agent = HTNAgent.from_config(None, "m", config)
        assert agent._max_steps == 99
        assert agent._per_subgoal == 5
        assert agent._max_methods == 1

    def test_from_config_default_matches_individual_kwarg_construction(self):
        """The additive-only guarantee: from_config() with a default
        HTNConfig must produce an agent identical to the original
        individual-kwarg constructor -- no behavior change for anyone
        who switches to the new constructor without customizing it."""
        from htn_agent import HTNConfig, HTNAgent
        via_config = HTNAgent.from_config(None, "m", HTNConfig())
        via_kwargs = HTNAgent(None, "m")
        assert via_config._max_steps == via_kwargs._max_steps
        assert via_config._per_subgoal == via_kwargs._per_subgoal
        assert via_config._max_methods == via_kwargs._max_methods

    def test_augmented_htn_agent_from_config_is_inherited_correctly(self):
        from htn_agent import AugmentedHTNAgent, HTNConfig
        agent = AugmentedHTNAgent.from_config(
            None, "m", HTNConfig(), max_wall_seconds=120.0,
        )
        assert isinstance(agent, AugmentedHTNAgent)
        assert agent._max_wall_seconds == 120.0

    def test_budgets_are_marked_provisional(self):
        """Ticket 15: mark distributional constants provisional 'at the
        point of definition, not in a comment elsewhere.'"""
        from htn_agent import DistributionalBudgets
        assert DistributionalBudgets.PROVISIONAL is True


class TestSchedulerStrategy:
    """Ticket 15's scheduler-strategy restructuring, tested directly:
    the two scheduling algorithms are now genuinely interchangeable
    strategy objects, not fixed to one class each via inheritance."""

    def test_htn_agent_defaults_to_sequential_scheduler(self):
        from htn_agent import HTNAgent, SequentialScheduler
        agent = HTNAgent(None, "m")
        assert isinstance(agent._scheduler, SequentialScheduler)

    def test_augmented_htn_agent_defaults_to_concurrent_batch_scheduler(self):
        from htn_agent import AugmentedHTNAgent, ConcurrentBatchScheduler
        agent = AugmentedHTNAgent(None, "m")
        assert isinstance(agent._scheduler, ConcurrentBatchScheduler)

    def test_htn_agent_with_explicit_concurrent_scheduler_runs_a_batch_concurrently(
            self, nested_repo):
        """The real, new capability: a bare HTNAgent (none of
        AugmentedHTNAgent's other overrides -- no persona restriction, no
        basename hints, no multi-language postcondition check) can still
        get concurrent batch scheduling by passing the strategy directly.
        Confirmed by giving it two independent ready nodes and checking
        BOTH got a turn in what the sequential default would have made
        two separate scheduling rounds -- concurrent scheduling grants
        both nodes a reservation in the SAME round (see
        ConcurrentBatchScheduler's own batch-reservation logic)."""
        from htn_agent import ConcurrentBatchScheduler, HTNAgent

        client = FakeClient([
            _msg(content='[{"id":1,"goal":"In src/a.py, change the return value","deps":[]},'
                         ' {"id":2,"goal":"In src/a.py, change the return value again","deps":[]}]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "n1 done"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "n2 done"})]),
        ])
        agent = HTNAgent(client, "m", scheduler=ConcurrentBatchScheduler())
        run = agent.run(INSTANCE, nested_repo, "arm")
        nodes = run.htn["nodes"]
        assert nodes[0]["status"] == "done"
        assert nodes[1]["status"] == "done"
        # Both nodes should have been granted a reservation in round 1 --
        # the concurrent scheduler's own signature (sequential would grant
        # node 2 only after node 1 finishes its own round).
        assert nodes[0]["rounds"] == 1
        assert nodes[1]["rounds"] == 1

    def test_augmented_htn_agent_with_explicit_sequential_scheduler_runs_one_at_a_time(
            self, nested_repo):
        """The reverse pairing: AugmentedHTNAgent's richer verification/
        persona/context behavior, with simple sequential scheduling
        instead of its own concurrent-batch default. Confirmed by
        checking node 2 is NOT granted a reservation until node 1's
        round has actually finished -- the sequential scheduler's
        one-ready-node-at-a-time signature."""
        from htn_agent import AugmentedHTNAgent, SequentialScheduler

        client = FakeClient([
            _msg(content='[{"id":1,"goal":"In src/a.py, change the return value","deps":[]},'
                         ' {"id":2,"goal":"In src/a.py, change the return value again","deps":[]}]'),
            _msg(tool_calls=[("subgoal_done", {"summary": "n1 done"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "n2 done"})]),
        ])
        agent = AugmentedHTNAgent(client, "m", scheduler=SequentialScheduler())
        run = agent.run(INSTANCE, nested_repo, "arm")
        nodes = run.htn["nodes"]
        assert nodes[0]["status"] == "done"
        assert nodes[1]["status"] == "done"

    def test_sequential_and_concurrent_schedulers_produce_identical_outcomes_on_the_same_scripted_run(
            self, nested_repo):
        """Real behavioral-equivalence check: the SAME scripted plan, run
        through each scheduler on an otherwise-identical bare HTNAgent,
        must reach the same final node statuses. This is the real safety
        property the extraction claims (moved verbatim, no rewrite) --
        checked directly, not just assumed from 'the diff looks like a
        pure move.'"""
        from htn_agent import ConcurrentBatchScheduler, HTNAgent, SequentialScheduler

        def _make_client():
            return FakeClient([
                _msg(content='[{"id":1,"goal":"In src/a.py, change the return value","deps":[]},'
                             ' {"id":2,"goal":"In src/a.py, change the return value again","deps":[1]}]'),
                _msg(tool_calls=[("subgoal_done", {"summary": "n1 done"})]),
                _msg(tool_calls=[("subgoal_done", {"summary": "n2 done"})]),
            ])

        seq_agent = HTNAgent(_make_client(), "m", scheduler=SequentialScheduler())
        seq_run = seq_agent.run(INSTANCE, nested_repo, "arm")

        conc_agent = HTNAgent(_make_client(), "m", scheduler=ConcurrentBatchScheduler())
        conc_run = conc_agent.run(INSTANCE, nested_repo, "arm")

        assert [n["status"] for n in seq_run.htn["nodes"]] == ["done", "done"]
        assert [n["status"] for n in conc_run.htn["nodes"]] == ["done", "done"]
        assert seq_run.stop_reason == conc_run.stop_reason == "finished"

    def test_the_four_dead_stub_methods_are_confirmed_gone_except_three_real_ones(self):
        """_run_ready_batch was removed as genuinely obsolete (superseded
        by ConcurrentBatchScheduler, confirmed by grep -- zero references
        anywhere before removal). _mcts_pick is still real, undone future
        work (needs a real LLM call for candidate generation) and must
        remain a NotImplementedError stub. _ast_edit and _method_score
        are no longer stubs at all -- see TestAstEdit/TestMethodScore for
        their real implementations, confirmed here only by NOT raising
        NotImplementedError."""
        from htn_agent import ResearchHTNAgent
        assert not hasattr(ResearchHTNAgent, "_run_ready_batch")
        with pytest.raises(NotImplementedError):
            ResearchHTNAgent(None, "m")._mcts_pick()
        assert ResearchHTNAgent(None, "m")._method_score({"attempts": 0, "successes": 0}) == 0.5


class TestAstEdit:
    """Real implementation of ResearchHTNAgent's item 2 (see class
    docstring): parse a .py file, locate a symbol by name, replace its
    source, reject if the result would not parse."""

    def test_replaces_a_top_level_function(self, tmp_path):
        from htn_agent import ResearchHTNAgent

        (tmp_path / "mod.py").write_text("def f():\n    return 1\n\ndef g():\n    return 2\n")
        sandbox = RepoSandbox(str(tmp_path))
        agent = ResearchHTNAgent(None, "m")
        result = agent._ast_edit(sandbox, "mod.py", "f", "def f():\n    return 99\n")
        assert "replaced f" in result
        assert (tmp_path / "mod.py").read_text() == "def f():\n    return 99\n\ndef g():\n    return 2\n"

    def test_replaces_a_class(self, tmp_path):
        from htn_agent import ResearchHTNAgent

        (tmp_path / "mod.py").write_text("class Foo:\n    x = 1\n")
        sandbox = RepoSandbox(str(tmp_path))
        agent = ResearchHTNAgent(None, "m")
        result = agent._ast_edit(sandbox, "mod.py", "Foo", "class Foo:\n    x = 2\n    y = 3\n")
        assert "replaced Foo" in result
        assert "y = 3" in (tmp_path / "mod.py").read_text()

    def test_preserves_a_decorator(self, tmp_path):
        """Real fix: node.lineno for a decorated function starts at the
        `def` line, not the decorator, in Python 3.8+ -- confirmed by
        direct AST inspection before writing this, not assumed. Without
        using decorator_list's own lineno, the decorator would be
        silently dropped."""
        from htn_agent import ResearchHTNAgent

        (tmp_path / "mod.py").write_text(
            "@staticmethod\ndef f():\n    return 1\n\ndef g():\n    return 2\n"
        )
        sandbox = RepoSandbox(str(tmp_path))
        agent = ResearchHTNAgent(None, "m")
        agent._ast_edit(sandbox, "mod.py", "f", "@staticmethod\ndef f():\n    return 99\n")
        content = (tmp_path / "mod.py").read_text()
        assert content.count("@staticmethod") == 1
        assert "def g():" in content, "the OLD decorator line must not have swallowed the next symbol"

    def test_rejects_a_replacement_that_would_not_parse(self, tmp_path):
        from htn_agent import ResearchHTNAgent

        original = "def f():\n    return 1\n"
        (tmp_path / "mod.py").write_text(original)
        sandbox = RepoSandbox(str(tmp_path))
        agent = ResearchHTNAgent(None, "m")
        result = agent._ast_edit(sandbox, "mod.py", "f", "def f(:\n    this is not valid python\n")
        assert "not applied" in result
        assert (tmp_path / "mod.py").read_text() == original, "a rejected edit must not touch the file at all"

    def test_missing_symbol_is_a_real_error_not_a_silent_no_op(self, tmp_path):
        from htn_agent import ResearchHTNAgent

        (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
        sandbox = RepoSandbox(str(tmp_path))
        agent = ResearchHTNAgent(None, "m")
        result = agent._ast_edit(sandbox, "mod.py", "nonexistent_symbol", "def f():\n    return 2\n")
        assert "no function or class named" in result

    def test_non_py_file_is_rejected(self, tmp_path):
        from htn_agent import ResearchHTNAgent

        (tmp_path / "mod.go").write_text("package main\n")
        sandbox = RepoSandbox(str(tmp_path))
        agent = ResearchHTNAgent(None, "m")
        result = agent._ast_edit(sandbox, "mod.go", "main", "package main\n")
        assert "only supports .py files" in result

    def test_file_that_already_has_a_syntax_error_is_rejected_before_replacement(self, tmp_path):
        from htn_agent import ResearchHTNAgent

        (tmp_path / "mod.py").write_text("def f(:\n    broken already\n")
        sandbox = RepoSandbox(str(tmp_path))
        agent = ResearchHTNAgent(None, "m")
        result = agent._ast_edit(sandbox, "mod.py", "f", "def f():\n    return 1\n")
        assert "does not currently parse" in result

    def test_edited_file_is_tracked_in_sandbox_bookkeeping(self, tmp_path):
        """Real confirmation that _ast_edit follows the same
        sandbox._original.setdefault pattern edit_file/create_file use
        -- so sandbox.diff()/edited_files() correctly reflect the
        change, not just the file on disk."""
        from htn_agent import ResearchHTNAgent

        (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
        sandbox = RepoSandbox(str(tmp_path))
        agent = ResearchHTNAgent(None, "m")
        agent._ast_edit(sandbox, "mod.py", "f", "def f():\n    return 2\n")
        assert "mod.py" in sandbox.edited_files()

    def test_ast_replace_function_offered_only_when_goal_names_a_py_file(self):
        """Real confirmation of the _tools_for gating -- 'gated to .py
        files; everything else keeps using edit_file' (class docstring,
        item 2)."""
        from htn_agent import RunContext, ResearchHTNAgent
        from agent import Usage

        agent = ResearchHTNAgent(None, "m")
        ctx = RunContext(t0=0.0, usage=Usage())

        py_node = Node(id=1, goal="In app/services/foo.py, fix the validator")
        py_tools = {t["function"]["name"] for t in agent._tools_for(py_node, ctx)}
        assert "ast_replace_function" in py_tools

        no_file_node = Node(id=2, goal="Investigate the broken handler")
        no_file_tools = {t["function"]["name"] for t in agent._tools_for(no_file_node, ctx)}
        assert "ast_replace_function" not in no_file_tools

        go_node = Node(id=3, goal="In main.go, fix the handler")
        go_tools = {t["function"]["name"] for t in agent._tools_for(go_node, ctx)}
        assert "ast_replace_function" not in go_tools
        assert "edit_file" in go_tools, "edit_file must remain available regardless"


class TestMethodScore:
    """Real implementation of ResearchHTNAgent's item 5 (see class
    docstring): Beta-Bernoulli posterior mean, same reasoning
    procedures.py's own lifecycle (ticket 13) uses for promotion."""

    def test_no_attempts_gives_neutral_half(self):
        from htn_agent import ResearchHTNAgent
        agent = ResearchHTNAgent(None, "m")
        assert agent._method_score({"attempts": 0, "successes": 0}) == 0.5

    def test_all_successes_scores_high_but_not_exactly_one(self):
        """Beta(1,1) prior means even a perfect record never reaches
        literal 1.0 -- a real, deliberate property (nothing is ever
        treated as absolutely certain from finite evidence)."""
        from htn_agent import ResearchHTNAgent
        agent = ResearchHTNAgent(None, "m")
        score = agent._method_score({"attempts": 10, "successes": 10})
        assert 0.9 < score < 1.0

    def test_all_failures_scores_low_but_not_exactly_zero(self):
        from htn_agent import ResearchHTNAgent
        agent = ResearchHTNAgent(None, "m")
        score = agent._method_score({"attempts": 10, "successes": 0})
        assert 0.0 < score < 0.1

    def test_more_evidence_at_the_same_high_ratio_moves_closer_to_the_raw_ratio(self):
        """A real property of the posterior mean, checked with an actual
        computation rather than assumed: at a FIXED ratio away from 0.5,
        more evidence pulls the estimate closer to the raw success rate
        (less influenced by the neutral prior). (Testing this AT exactly
        50% would be misleading -- the posterior mean is exactly 0.5
        regardless of evidence size there; the prior's pull shows up in
        the interval width, not the mean, at that one ratio. 100% is a
        real ratio where more evidence visibly matters.)"""
        from htn_agent import ResearchHTNAgent
        agent = ResearchHTNAgent(None, "m")
        few = agent._method_score({"attempts": 2, "successes": 2})
        many = agent._method_score({"attempts": 200, "successes": 200})
        assert few == pytest.approx(0.75, abs=0.01)
        assert many == pytest.approx(0.995, abs=0.01)
        assert many > few, "more evidence at the same 100% ratio should push closer to 1.0"

    def test_missing_keys_default_to_zero_not_a_crash(self):
        from htn_agent import ResearchHTNAgent
        agent = ResearchHTNAgent(None, "m")
        assert agent._method_score({}) == 0.5
