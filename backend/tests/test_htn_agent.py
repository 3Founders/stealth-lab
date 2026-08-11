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
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"this subgoal will fail","deps":[]},'
                         ' {"id":2,"goal":"this depends on the failure","deps":[1]},'
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
                         ' {"id":2,"goal":"blocked dependent subgoal","deps":[1]}]'),
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
        return {t["function"]["name"] for t in agent._tools_for(node)}

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
    at a small, fast scale."""

    def test_exhausting_one_round_retries_instead_of_failing(self, repo):
        from htn_agent import AugmentedHTNAgent
        client = FakeClient([
            _msg(content='["Only one subgoal here to run"]'),
            # Round 1: three non-terminal calls exhaust a 3-step ceiling
            # without ever calling subgoal_done/subgoal_failed.
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
            # Round 2: a fresh reservation lets it actually finish.
            _msg(tool_calls=[("subgoal_done", {"summary": "done on retry"})]),
        ])
        run = AugmentedHTNAgent(
            client, "m", max_steps=12, steps_per_subgoal=3, max_methods=2,
        ).run(INSTANCE, repo, "arm")

        assert run.htn["subgoals_done"] == 1, run.htn
        assert run.htn["subgoals_failed"] == 0, run.htn
        # No replan was needed -- the node simply got a second round with a
        # fresh budget, exactly as _schedule's own docstring promises.
        assert run.htn["replans"] == 0
        assert "__replan__" not in run.tool_calls
        # Every scripted tool call actually ran -- nothing was abandoned.
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
        cause of termination (7 = 3 + 3 + 1: two full rounds, then a last
        partial round of exactly what remains), distinct from
        test_exhausting_one_round_retries_instead_of_failing above, where a
        node succeeds on retry, and from the attempts-exhausted case
        covered by TestLocalizedReplanning.test_replan_attempts_are_bounded."""
        from htn_agent import AugmentedHTNAgent
        client = FakeClient(
            [_msg(content='["A subgoal that will never signal completion"]')]
            + [_msg(tool_calls=[("read_file", {"path": "src/a.py"})])] * 40
        )
        run = AugmentedHTNAgent(
            client, "m", max_steps=7, steps_per_subgoal=3, max_methods=5,
        ).run(INSTANCE, repo, "arm")
        leaf = [t for t in run.tool_calls if not t.startswith("__")]
        assert len(leaf) == 7          # every step of the total budget spent
        assert run.stop_reason == "step_budget"
        # It never reached a terminal call, so it must not be "failed" --
        # pending-and-out-of-budget is a different, honest outcome.
        assert run.htn["nodes"][0]["status"] == "pending"

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
