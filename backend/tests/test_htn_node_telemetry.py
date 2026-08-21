"""
Tests for per-node HTN instrumentation and the step-budget reservation floor.

Two real, measured defects motivate this file (see the plan doc / dist.md
for the full evidence): (1) TOTAL_STEP_BUDGET (28) could not fund even one
attempt per planned subgoal (MAX_SUBGOALS * STEPS_PER_SUBGOAL = 36 > 28), and
a single node's retries could legally consume the whole run's budget before
a later node was ever attempted -- measured on ansible-f327e65d: node 1's
three 9-step rounds (27 of 28 steps) left node 2 a 1-step grant, below
MIN_VIABLE_SUBGOAL_BUDGET, so it was declined and the run ended with node 2
at attempts=0. (2) Nothing recorded per-node cost or effect, so there was no
way to tell WHICH node's tokens were wasted, or whether a node ever touched
a gold file.

Uses `_msg`'s fixed prompt_tokens=10/completion_tokens=5 per call (same
fixture shape as test_htn_agent.py) so every token assertion below is an
exact equality, not an inequality.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import types

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from agent import RepoSandbox  # noqa: E402
from htn_agent import (  # noqa: E402
    MIN_VIABLE_SUBGOAL_BUDGET, AugmentedHTNAgent, HTNAgent, Node, _node_row,
)


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
    """Replays a scripted list of responses. Safe ONLY when calls are
    strictly sequential (single node ready at a time) -- see
    ByGoalFakeClient below for anything that runs nodes concurrently."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        import copy
        self.requests.append({**kw, "messages": copy.deepcopy(kw["messages"])})
        if not self.script:
            return _msg(tool_calls=[("subgoal_failed", {"reason": "script exhausted"})])
        return self.script.pop(0)


class ByGoalFakeClient:
    """
    Routes each chat call by which registered goal substring appears in the
    node's OWN subgoal section -- the only way to script per-node behaviour
    once nodes run concurrently through a ThreadPoolExecutor. A shared
    list-popping FakeClient cannot express "node A gets THESE responses"
    under real thread interleaving: two threads popping the same list race,
    and which thread gets which scripted response is not controlled by the
    test.

    Routing must key on the text AFTER EXECUTOR_SYSTEM's own
    "YOUR CURRENT SUBGOAL:" marker, not anywhere in the whole message: the
    same prompt's "THE PLAN:" section (`_build_context`'s `plan` string)
    lists every node's goal text, including every OTHER node's -- searching
    the whole content routes every node's very first call to whichever
    key happens to be checked first, since all of them are substrings of
    that shared plan listing. Confirmed by direct reproduction before
    fixing: with a whole-content search, a 2-node run with keys "Fix a.py
    return value" and "Fix b.py return value" put BOTH nodes' calls through
    node 1's script.

    Everything that is not an executor call (the planner call, and any
    _replan call) gets `plan_response`.

    `barrier`, if given, is waited on by each node's FIRST executor call --
    released only once every node this test cares about has made its first
    call, which is what proves the two threads were genuinely in flight at
    the same time rather than merely finishing in some order a slow test
    might not exercise.
    """

    _MARKER = "YOUR CURRENT SUBGOAL:"

    def __init__(self, plan_response, node_scripts: dict, barrier=None):
        self._plan_response = plan_response
        self._scripts = {k: list(v) for k, v in node_scripts.items()}
        self._barrier = barrier
        self._released = set()
        self._lock = threading.Lock()
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        content = kw["messages"][0]["content"]
        idx = content.find(self._MARKER)
        if idx < 0:
            return self._plan_response
        # The subgoal text itself is short; 400 chars is generous headroom
        # without risking spilling into the NEXT node's plan-listing line.
        section = content[idx + len(self._MARKER): idx + len(self._MARKER) + 400]
        key = next((k for k in self._scripts if k in section), None)
        assert key is not None, f"no script registered for subgoal: {section[:200]}"
        if self._barrier is not None:
            with self._lock:
                first = key not in self._released
                if first:
                    self._released.add(key)
            if first:
                self._barrier.wait(timeout=5)
        with self._lock:
            script = self._scripts[key]
            if not script:
                return _msg(tool_calls=[("subgoal_failed", {"reason": "script exhausted"})])
            return script.pop(0)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return RepoSandbox(str(tmp_path))


@pytest.fixture
def two_file_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    return RepoSandbox(str(tmp_path))


INSTANCE = {"instance_id": "inst_x", "repo": "acme/thing",
            "problem_statement": "f() returns the wrong value and a helper is missing"}


class RoutedFakeClient:
    """Per-node isolated scripts, immune to the cross-node/cross-round
    message leakage a single shared queue is prone to (see
    ByGoalFakeClient's docstring, and the TestBudgetStarvationRedFirst
    debugging history: the fair-share reservation formula grants a
    DIFFERENT, budget-dependent amount each round, so any script hand-sized
    to an exact old grant silently desyncs the moment the formula changes
    -- exactly the fragility this class exists to remove).

    Executor calls (identified by EXECUTOR_SYSTEM's own
    "YOUR CURRENT SUBGOAL:" marker) route to whichever registered node
    script's goal-substring appears in that marker's own text -- never
    searched against the whole prompt, since "THE PLAN:" lists every
    OTHER node's goal too (see ByGoalFakeClient's docstring for the
    concrete misrouting this caused before that fix).

    Everything else (the planner call, and every _replan call regardless
    of which node it's for) gets a fresh, generic alt-approach reply --
    replan calls don't carry the executor marker, so they can't be routed
    by node, and they don't need to be: a single generic handler is
    correct for all of them.
    """

    _MARKER = "YOUR CURRENT SUBGOAL:"

    def __init__(self, plan_response, node_scripts: dict, barrier=None):
        self._plan_response = plan_response
        self._scripts = {k: list(v) for k, v in node_scripts.items()}
        self._barrier = barrier
        self._released = set()
        self._lock = threading.Lock()
        self._planned = False
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        content = kw["messages"][0]["content"]
        idx = content.find(self._MARKER)
        if idx < 0:
            with self._lock:
                if not self._planned:
                    self._planned = True
                    return self._plan_response
            # A replan call. _replan REPLACES node.goal with whatever text
            # is returned here, so echoing back the SAME "Original
            # subgoal:" text (rather than inventing new text) is what
            # keeps a node's goal -- and therefore which script it routes
            # to -- stable across every retry. REPLAN_SYSTEM always
            # includes it verbatim (htn_agent.py's REPLAN_SYSTEM template).
            marker = "Original subgoal: "
            start = content.find(marker)
            end = content.find("\n", start)
            original = content[start + len(marker):end if end >= 0 else None].strip()
            return _msg(content=original)
        section = content[idx + len(self._MARKER): idx + len(self._MARKER) + 400]
        key = next((k for k in self._scripts if k in section), None)
        assert key is not None, f"no script registered for subgoal: {section[:200]}"
        if self._barrier is not None:
            with self._lock:
                first = key not in self._released
                if first:
                    self._released.add(key)
            if first:
                self._barrier.wait(timeout=5)
        with self._lock:
            script = self._scripts[key]
            if not script:
                return _msg(tool_calls=[("subgoal_failed", {"reason": "script exhausted"})])
            return script.pop(0)


class TestBudgetStarvationRedFirst:
    """Replays the ansible-f327e65d shape: a linear 3-node plan where node 1
    never succeeds (an unbounded supply of non-terminal read_file calls, no
    subgoal_done anywhere in its script) and so genuinely exhausts every
    round it's granted, across all `max_methods + 1` attempts, before
    finally failing outright.

    Before the reservation-floor/fair-share fix, this reproduces the real
    bug exactly: node 1's retries could consume the whole run's budget
    before node 2/3 ever got a first attempt, leaving them at attempts=0
    with the run ending "step_budget". Revert the fair-share block in
    `AugmentedHTNAgent._schedule` (and its base-class counterpart) to
    confirm this test goes red.

    Deps are SOFT (`deps=[1]`, not `requires=[1]`) so node 2/3 become ready
    once node 1 reaches ANY terminal status, including "failed" -- this
    isolates the budget question from the separate blocked-vs-starved
    question covered by TestBlockedIsNotStarved below.

    Uses RoutedFakeClient specifically because the fair-share formula
    grants a DIFFERENT amount each round depending on how much budget
    remains -- a hand-sized shared-queue script can never predict those
    exact counts without duplicating the scheduler's own arithmetic, and
    every attempt to do so by hand this session got the numbers wrong at
    least once. A generous, uniform oversupply of read_file responses per
    node sidesteps needing to predict them at all.
    """

    def test_downstream_nodes_get_a_real_attempt(self, repo):
        plan = _msg(content='[{"id":1,"goal":"Investigate and fix a.py","deps":[]},'
                            ' {"id":2,"goal":"Add a helper in b.py","deps":[1]},'
                            ' {"id":3,"goal":"Wire the helper into a.py","deps":[2]}]')
        client = RoutedFakeClient(plan_response=plan, node_scripts={
            # Never succeeds -- forces node 1 through every one of its
            # max_methods+1 attempts, each genuinely exhausting whatever
            # ceiling it's granted that round, regardless of the exact size.
            "Investigate and fix a.py": [
                _msg(tool_calls=[("read_file", {"path": "src/a.py"})])
                for _ in range(60)
            ],
            "Add a helper in b.py": [_msg(tool_calls=[("subgoal_done", {"summary": "helper added"})])],
            "Wire the helper into a.py": [_msg(tool_calls=[("subgoal_done", {"summary": "wired in"})])],
        })
        run = AugmentedHTNAgent(
            client, "m", max_steps=28, steps_per_subgoal=9, max_methods=2,
        ).run(INSTANCE, repo, "arm")

        nodes = {n["id"]: n for n in run.htn["nodes"]}
        assert nodes[1]["status"] == "failed"
        assert nodes[1]["attempts"] == 3, nodes[1]   # max_methods(2) + 1

        # THE regression assertion: node 2 and node 3 must have been
        # attempted at all. Pre-fix this is 0/0 and the run ends at
        # "step_budget" before either node ever runs.
        assert nodes[2]["attempts"] >= 1, run.htn
        assert nodes[3]["attempts"] >= 1, run.htn
        assert nodes[2]["status"] == "done"
        assert nodes[3]["status"] == "done"
        assert run.htn["nodes_never_ran"] == 0
        assert run.stop_reason == "finished"

        # The mechanism, not just the outcome: node 1 must not have been
        # granted its full per-round cap (steps_per_subgoal=9) on every one
        # of its 3 rounds -- if it had, it would have consumed all 27 of
        # the 28-step budget the way the pre-fix bug did, and node 2/3
        # could not both have gotten a real attempt within what's left.
        assert nodes[1]["budget_granted"] < 27, (
            f"node 1 claimed {nodes[1]['budget_granted']} of 28 steps across "
            f"its 3 rounds -- fair-share should have shrunk at least one of "
            f"them to leave real budget for nodes 2 and 3")


class TestConcurrentNodeAttribution:
    """The crux correctness property: under REAL concurrent execution (two
    nodes provably in flight via a Barrier), each node's own tool_calls,
    files_edited, and token counters must contain only ITS OWN activity --
    never a sibling's, and never a snapshot racing an unlocked mutation.
    """

    def test_each_node_gets_only_its_own_calls_files_and_tokens(self, two_file_repo):
        barrier = threading.Barrier(2, timeout=5)
        plan = _msg(content='[{"id":1,"goal":"Fix a.py return value","deps":[]},'
                            ' {"id":2,"goal":"Fix b.py return value","deps":[]}]')
        client = ByGoalFakeClient(
            plan_response=plan,
            node_scripts={
                "Fix a.py return value": [
                    _msg(tool_calls=[("read_file", {"path": "src/a.py"})]),
                    _msg(tool_calls=[("edit_file", {
                        "path": "src/a.py", "old_str": "return 1", "new_str": "return 2"})]),
                    _msg(tool_calls=[("subgoal_done", {"summary": "a fixed"})]),
                ],
                "Fix b.py return value": [
                    _msg(tool_calls=[("read_file", {"path": "src/b.py"})]),
                    _msg(tool_calls=[("edit_file", {
                        "path": "src/b.py", "old_str": "return 1", "new_str": "return 2"})]),
                    _msg(tool_calls=[("subgoal_done", {"summary": "b fixed"})]),
                ],
            },
            barrier=barrier,
        )
        # Both nodes have deps=[], so the default width (MAX_PARALLEL_NODES)
        # puts them in the SAME batch, run through a real ThreadPoolExecutor.
        run = AugmentedHTNAgent(client, "m", max_steps=28).run(INSTANCE, two_file_repo, "arm")

        nodes = {n["id"]: n for n in run.htn["nodes"]}
        assert nodes[1]["status"] == "done" and nodes[2]["status"] == "done"

        assert nodes[1]["tool_calls"] == ["read_file", "edit_file", "subgoal_done"]
        assert nodes[2]["tool_calls"] == ["read_file", "edit_file", "subgoal_done"]
        assert nodes[1]["files_edited"] == ["src/a.py"]
        assert nodes[2]["files_edited"] == ["src/b.py"]

        # Exact, not approximate: 3 LLM calls each at the fixture's fixed
        # 10 prompt / 5 completion tokens.
        assert nodes[1]["llm_calls"] == 3
        assert nodes[2]["llm_calls"] == 3
        assert nodes[1]["prompt_tokens"] == 30 and nodes[1]["completion_tokens"] == 15
        assert nodes[2]["prompt_tokens"] == 30 and nodes[2]["completion_tokens"] == 15
        assert nodes[1]["steps_used"] == 3 and nodes[2]["steps_used"] == 3

        # The shared planner call (1 call, 10/5 tokens) is charged to the
        # run-global Usage but to NEITHER node -- _decompose never rebinds
        # usage to a _NodeUsage. run.usage.total = 6 executor calls * 15 +
        # 1 planner call * 15 = 105; node_tokens_total = 6*15 = 90.
        assert run.usage.total == 105
        assert run.htn["node_tokens_total"] == 90
        assert run.htn["overhead_tokens"] == 15


class TestFilesEditedAttributionExactness:
    def test_two_nodes_editing_the_same_file_both_attribute_it(self, repo):
        """Justifies byte-comparison over a sandbox._original delta:
        _original.setdefault (agent.py) records only the FIRST node ever to
        touch a file, so a delta-based attribution would silently miss the
        second node's edit. Sequential (deps=[1]) so no concurrency is
        needed to exercise this -- it is about the fingerprint comparison,
        not about thread-safety."""
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"First pass on a.py","deps":[]},'
                         ' {"id":2,"goal":"Second pass on a.py","deps":[1]}]'),
            _msg(tool_calls=[("edit_file", {
                "path": "src/a.py", "old_str": "return 1", "new_str": "return 2"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "first pass done"})]),
            _msg(tool_calls=[("edit_file", {
                "path": "src/a.py", "old_str": "return 2", "new_str": "return 3"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "second pass done"})]),
        ])
        run = AugmentedHTNAgent(client, "m", max_steps=28).run(INSTANCE, repo, "arm")
        nodes = {n["id"]: n for n in run.htn["nodes"]}
        assert nodes[1]["files_edited"] == ["src/a.py"]
        assert nodes[2]["files_edited"] == ["src/a.py"]

    def test_rejected_edit_is_not_attributed(self, repo):
        """A byte-mismatched old_str is rejected by edit_file -- the tool
        call is recorded, but nothing changed on disk, so files_edited must
        stay empty."""
        client = FakeClient([
            _msg(content='["Try to edit a.py with a stale old_str"]'),
            _msg(tool_calls=[("edit_file", {
                "path": "src/a.py", "old_str": "this text is not in the file",
                "new_str": "return 2"})]),
            _msg(tool_calls=[("subgoal_failed", {"reason": "edit was rejected"})]),
        ])
        run = AugmentedHTNAgent(client, "m", max_steps=28).run(INSTANCE, repo, "arm")
        node = run.htn["nodes"][0]
        assert "edit_file" in node["tool_calls"]
        assert node["files_edited"] == []


class TestBlockedIsNotStarved:
    def test_hard_dependency_failure_blocks_without_charging_an_attempt(self, repo):
        """A HARD (`requires`) predecessor's failure blocks its dependent
        via _block_dependents -- the dependent never becomes schedulable at
        all, so it must show attempts=0/status=blocked, which is a
        dependency cascade, NOT budget starvation. Conflating the two would
        blame the step budget for what is actually a planning failure."""
        client = FakeClient([
            _msg(content='[{"id":1,"goal":"A subgoal that cannot be done","deps":[]},'
                         ' {"id":2,"goal":"Depends hard on subgoal 1","deps":[1],'
                         ' "requires":[1]}]'),
            _msg(tool_calls=[("subgoal_failed", {"reason": "cannot be done"})]),
            _msg(content="too short"),  # no viable alternative -> fails outright
        ])
        run = AugmentedHTNAgent(
            client, "m", max_steps=28, max_methods=1,
        ).run(INSTANCE, repo, "arm")
        nodes = {n["id"]: n for n in run.htn["nodes"]}
        assert nodes[1]["status"] == "failed"
        assert nodes[2]["status"] == "blocked"
        assert nodes[2]["attempts"] == 0

        import run_graph_experiment as rge
        metrics = rge.node_metrics(run.htn, ["src/a.py"], run.wall_seconds)
        assert metrics["n_blocked_unrun"] == 1
        assert metrics["n_budget_starved"] == 0


class TestRehydrateRoundTrip:
    def test_new_fields_survive_a_round_trip(self, repo):
        client = FakeClient([
            _msg(content='["Only one subgoal here"]'),
            _msg(tool_calls=[("edit_file", {
                "path": "src/a.py", "old_str": "return 1", "new_str": "return 2"})]),
            _msg(tool_calls=[("subgoal_done", {"summary": "done"})]),
        ])
        run = AugmentedHTNAgent(client, "m", max_steps=28).run(INSTANCE, repo, "arm")
        original = run.htn["nodes"][0]
        assert original["steps_used"] == 2
        assert original["llm_calls"] == 2   # edit_file call, then subgoal_done call
        assert original["files_edited"] == ["src/a.py"]

        rehydrated = HTNAgent._rehydrate(run.htn["nodes"])
        assert len(rehydrated) == 1
        node = rehydrated[0]
        assert node.steps_used == original["steps_used"]
        assert node.llm_calls == original["llm_calls"]
        assert node.prompt_tokens == original["prompt_tokens"]
        assert node.completion_tokens == original["completion_tokens"]
        assert node.files_edited == original["files_edited"]
        assert node.tool_calls == original["tool_calls"]

    def test_legacy_snapshot_without_telemetry_keys_rehydrates_to_zeros(self):
        """A snapshot from before this instrumentation existed (any of the
        ~20 result files already on disk) has only the original 11 keys --
        _rehydrate must not raise, and must default the new fields."""
        legacy = [{"id": 1, "goal": "an old subgoal", "deps": [], "requires": [],
                   "status": "done", "attempts": 1, "note": "did the thing",
                   "last_evidence": "", "path_hint": "", "depth": 0, "parent": None}]
        nodes = HTNAgent._rehydrate(legacy)
        assert len(nodes) == 1
        n = nodes[0]
        assert n.steps_used == 0 and n.budget_granted == 0 and n.rounds == 0
        assert n.llm_calls == 0 and n.prompt_tokens == 0 and n.completion_tokens == 0
        assert n.wall_seconds == 0.0
        assert n.started_at is None and n.ended_at is None
        assert n.tool_calls == [] and n.files_edited == []
        # _node_row must also not raise on a rehydrated legacy node.
        row = _node_row(n)
        assert row["status"] == "done" and row["total_tokens"] == 0


class TestNodeMetricsLegacyCompat:
    """node_metrics() and summarise() must run over the ~20 existing result
    files, none of which have any of these keys, without raising."""

    def test_node_metrics_none_for_flat_arm(self):
        import run_graph_experiment as rge
        assert rge.node_metrics(None, ["src/a.py"], 10.0) is None

    def test_node_metrics_none_for_empty_plan(self):
        import run_graph_experiment as rge
        assert rge.node_metrics({"nodes": []}, ["src/a.py"], 10.0) is None

    def test_node_metrics_tolerates_legacy_node_shape(self):
        """A pre-instrumentation node dict: only the original 11 keys."""
        import run_graph_experiment as rge
        legacy_htn = {"nodes": [
            {"id": 1, "goal": "g", "deps": [], "requires": [], "status": "done",
             "attempts": 1, "note": "", "last_evidence": "", "path_hint": "",
             "depth": 0, "parent": None},
        ]}
        metrics = rge.node_metrics(legacy_htn, ["src/a.py"], 10.0)
        assert metrics is not None
        assert metrics["n_nodes"] == 1
        assert metrics["n_budget_starved"] == 0
        assert metrics["n_nodes_editing"] == 0  # no files_edited key -> not counted

    def test_summarise_over_a_telemetry_free_row_does_not_raise(self):
        import run_graph_experiment as rge
        row = {
            "instance_id": "inst_legacy", "gold_files": ["src/a.py"],
            "no_memory": {"valid": True, "resolved": True, "total_tokens": 100,
                          "n_tool_calls": 3, "status": "resolved"},
            "htn_memory": {"valid": True, "resolved": False, "total_tokens": 80,
                          "n_tool_calls": 5, "status": "no_patch",
                          "htn": {"plan": [{"id": 1, "goal": "g"}], "replans": 0,
                                  "subgoals_done": 0, "subgoals_failed": 1,
                                  "decompose_failed": False}},
        }
        summary = rge.summarise([row], ["no_memory", "htn_memory"])
        assert summary["n_usable"] == 1
        assert summary["htn"]["starvation_rate"] is None
        assert summary["htn"]["node_gold_hit_rate"] is None
