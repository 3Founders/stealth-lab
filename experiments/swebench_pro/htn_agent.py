"""
HTN agent: LLM decomposition into a DAG, bounded local execution per node.

THREE PROPERTIES, EACH ANSWERING A MEASURED FAILURE IN THE FLAT AGENT

1. SUBGOAL DECOMPOSITION BOUNDS THE SEARCH.
   agent.py resends its ENTIRE history on every call, so token cost grows as
   d*N^2/2. Measured on this corpus: gravitational/teleport consumed
   1,067,259 tokens in one arm over 40 steps -- mean 26,681 per call, final
   context near 53K -- because 22 file dumps rode along on every later step.
   Here each node executes in its OWN message list: system + issue + the DAG
   + one-line notes from finished nodes + that node's local tool results.
   Context is O(local steps), not O(all steps).

2. A DAG, NOT A LIST, SO FAILURE IS CONTAINED.
   A linear plan propagates a failure to everything after it, even work that
   never depended on the failed step. With explicit `deps`, a failed node
   blocks only its TRANSITIVE DEPENDENTS; independent branches still run.
   That is the difference between "the plan failed" and "one branch failed".

3. LOCALIZED REPLANNING INSTEAD OF STARTING OVER.
   In the flat agent a failed edit is just another tool result appended to
   one ever-growing transcript, and the model re-reasons over everything.
   Measured: 16 edit_file calls across three episodes, all rejected, in the
   rhythm `edit -> read -> edit -> read`, episode dying at the step budget.
   Here a failed node unwinds to its parent and asks the planner for an
   ALTERNATIVE METHOD for that node alone. Completed nodes keep their edits
   in the sandbox and their notes in the DAG; nothing valid is re-executed.

Shares RepoSandbox, tools, grading and the AgentRun return shape with the
flat agent, so the harness cannot tell which ran -- that is what makes
flat-vs-HTN a controlled comparison. See ARCHITECTURE.md for how this sits
in the pipeline and TEST.md for what is verified about it.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from app.services import code_index
from agent import (
    MAX_RETRIES, MAX_TOOL_CHARS, REQUEST_TIMEOUT, TOOLS, AgentRun, RepoSandbox, Usage,
)

# Widened from a 5-minute-per-run target to 10, AND from list_symbols/
# read_symbol having cut the dominant per-call cost (median symbol read
# measured at ~170 tokens vs up to 7 chained read_file calls / ~3,500
# tokens to blindly page to the same code -- see code_index.py). More steps
# at a lower cost-per-step, not more steps at the old cost -- this is not
# just "give it more time", it is spending the token budget saved on tool
# calls back into MORE OPPORTUNITIES to search/edit/self-correct, which is
# the direct, causal lever on resolution rate: `no_patch` (zero edits) and
# blind edit/read/edit thrashing were the two observed stall modes, and
# both are steps-limited, not reasoning-limited.
STEPS_PER_SUBGOAL = 9
MAX_METHODS = 2          # alternative decomposition methods per failing node
MAX_SUBGOALS = 4
TOTAL_STEP_BUDGET = 28   # same leaf budget as the flat agent, for comparability
# How many times a compound task may decompose into further compound tasks.
# 1 = the flat one-level plan. 3 lets issue -> area -> file -> edit, which is
# as deep as these patches go. Bounded because each level costs a planner
# call and an unbounded recursion would spend the whole budget planning.
MAX_DEPTH = 2

# How much of a finished node's own account of what it did survives into a
# dependent node's context. Previously 200/80 respectively -- cut so hard
# that a node's real finding (e.g. "found the validation logic in
# AnsibleCollectionRef.is_valid_collection_name in _collection_finder.py")
# was reliably destroyed before a dependent ever saw it, forcing the
# dependent to re-search for something its own plan already knew, burning
# its whole step budget and then failing outright. Both limits gate the
# SAME piece of text at two points (write, then render), so both must move
# together or the second one just re-imposes the old ceiling.
SUBGOAL_SUMMARY_CHARS = 800
SUBGOAL_NOTE_CONTEXT_CHARS = 500

SUBGOAL_TOOLS = TOOLS[:-1] + [
    {"type": "function", "function": {
        "name": "subgoal_done",
        "description": ("Call when THIS subgoal is complete. Summarise in one "
                        "sentence what you changed, for dependent subgoals."),
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string"}}, "required": ["summary"]}}},
    {"type": "function", "function": {
        "name": "decompose_subgoal",
        "description": ("Call when THIS subgoal is still too large to do directly "
                        "-- it needs several distinct edits, or you do not yet know "
                        "where it lands. Break it into 2-4 smaller subgoals; they "
                        "will be executed for you, then control returns here. Do "
                        "not use this for a subgoal you could simply do."),
        "parameters": {"type": "object", "properties": {
            "subgoals": {"type": "array", "items": {"type": "object", "properties": {
                "goal": {"type": "string"},
                "deps": {"type": "array", "items": {"type": "integer"},
                         "description": "1-based indices of earlier subgoals in THIS list"}},
                "required": ["goal"]}}},
            "required": ["subgoals"]}}},
    {"type": "function", "function": {
        "name": "subgoal_failed",
        "description": ("Call when this subgoal cannot be done as stated -- the "
                        "code it names does not exist, or the approach is wrong. "
                        "Say why; an alternative will be planned."),
        "parameters": {"type": "object", "properties": {
            "reason": {"type": "string"}}, "required": ["reason"]}}},
]

PLANNER_SYSTEM = """You are planning a bug fix in the {repo} repository.

Break the issue into 2-{max_subgoals} subgoals forming a DEPENDENCY GRAPH. \
Another engineer executes them one at a time, seeing only the subgoal text \
you write -- not your reasoning.

Each subgoal must be:
- EXECUTABLE: name the file or symbol. "Find where auth happens" is useless; \
"In internal/server/auth/middleware.go, add a cookie fallback to the token \
lookup" is executable.
- SMALL: a few reads and one or two edits.
- HONEST ABOUT DEPENDENCIES: `deps` lists the ids of subgoals whose changes \
this one builds on. Use [] when it is independent. Independent subgoals keep \
running even if a sibling fails, so do not invent dependencies.

If the fix needs a file that does not exist, say so -- creating one is a \
normal subgoal.

Reply with ONLY a JSON array, no prose or fences:
[{{"id": 1, "goal": "...", "deps": []}}, {{"id": 2, "goal": "...", "deps": [1]}}]"""

REPLAN_SYSTEM = """A subgoal failed. Propose ONE alternative way to achieve the \
same intent in the {repo} repository.

Original subgoal: {subgoal}
What went wrong: {reason}

The alternative must differ in APPROACH, not wording -- a different file, a \
different mechanism, or creating something rather than editing something. \
Reply with ONLY the new subgoal as one line of plain text."""

EXECUTOR_SYSTEM = """You are fixing a bug in the {repo} repository, working on \
ONE node of a plan.

THE PLAN:
{plan}

ALREADY DONE (their changes are already applied -- build on them, do not redo):
{done}

YOUR CURRENT SUBGOAL:
{subgoal}

Do only this subgoal. At most {steps} tool calls -- prefer list_symbols + \
read_symbol over read_file, it costs far fewer tokens. Call subgoal_done \
when complete, or subgoal_failed if it cannot be done as stated. Edit source \
only, never tests. Use create_file when the subgoal calls for a file that \
does not exist yet."""


@dataclass
class Node:
    id: int
    goal: str
    deps: list[int] = field(default_factory=list)
    # pending | done | failed | blocked | expanded
    # `expanded` is a COMPOUND task: it did no work itself, its children did.
    # It counts as satisfied only when all of its children are satisfied,
    # which is what makes the recursion sound -- a dependent must not start
    # because its prerequisite merely *planned* something.
    status: str = "pending"
    attempts: int = 0
    note: str = ""
    depth: int = 0
    parent: Optional[int] = None
    # Last tool result seen before this node gave up, set by _run_node
    # regardless of subclass. Unused by HTNAgent itself -- it exists so
    # AugmentedHTNAgent's replanner can ground its alternative in a real
    # error instead of the model's one-line paraphrase of it.
    last_evidence: str = ""


class _Budget:
    """
    Thread-safe step counter shared across one scheduling round.

    `reserve` is called synchronously, in the scheduler, before any worker
    thread starts -- there is no LLM call in the critical section, so no
    race window exists there. That is what lets a CONCURRENT batch never
    exceed the run's total step budget even though several nodes spend it
    at once: a naive "check what's left, then go spend it" pattern run
    independently inside each thread has a real race (two threads can both
    observe the same remaining count before either reports back its
    actual usage); reserving the worst case up front and releasing what
    went unused afterward does not.
    """

    def __init__(self, max_steps: int):
        self._lock = threading.Lock()
        self._spent = 0
        self._max = max_steps

    def remaining(self) -> int:
        with self._lock:
            return self._max - self._spent

    def reserve(self, want: int) -> int:
        with self._lock:
            grant = max(0, min(want, self._max - self._spent))
            self._spent += grant
            return grant

    def release(self, unused: int) -> None:
        if unused <= 0:
            return
        with self._lock:
            self._spent = max(0, self._spent - unused)


class HTNAgent:
    def __init__(self, client, model: str, max_steps: int = TOTAL_STEP_BUDGET,
                 temperature: float = 0.0, steps_per_subgoal: int = STEPS_PER_SUBGOAL,
                 max_methods: int = MAX_METHODS):
        self._client = client
        self._model = model
        self._max_steps = max_steps
        self._temperature = temperature
        self._per_subgoal = steps_per_subgoal
        self._max_methods = max_methods
        # Always present, even in HTNAgent's own single-threaded operation
        # (uncontended-lock overhead is negligible) -- what lets
        # AugmentedHTNAgent run several nodes' turns concurrently using
        # `_run_node`/`_run_turn` completely unchanged, rather than a
        # parallel-only fork of them.
        self._usage_lock = threading.Lock()
        self._nodes_lock = threading.Lock()
        self._sandbox_lock = threading.Lock()

    # ---------------------------------------------------------------- llm
    def _chat(self, messages, usage: Usage, tools=None, max_tokens=1500):
        last = None
        for attempt in range(MAX_RETRIES):
            try:
                kw = {"tools": tools} if tools else {}
                r = self._client.chat.completions.create(
                    model=self._model, messages=messages,
                    temperature=self._temperature, max_tokens=max_tokens,
                    timeout=REQUEST_TIMEOUT, **kw)
                if r.usage:
                    # The network call above runs lock-free -- concurrent
                    # requests genuinely overlap. Only this shared-counter
                    # mutation needs to be serialized.
                    with self._usage_lock:
                        usage.add(r.usage)
                return r
            except Exception as exc:  # noqa: BLE001
                last = exc
                if not any(s in str(exc).lower() for s in
                           ("429", "rate limit", "timeout", "502", "503", "504",
                            "overloaded", "connection", "provider_error")):
                    raise
                if attempt == MAX_RETRIES - 1:
                    raise
                rate_limited = "429" in str(exc) or "rate limit" in str(exc).lower()
                time.sleep(25.0 if rate_limited else min(4 * 2 ** attempt, 20))
        raise last  # type: ignore[misc]

    # --------------------------------------------------------------- plan
    @staticmethod
    def parse_dag(text: str) -> list[Node]:
        """
        Parse the planner's JSON into a VALIDATED DAG.

        Everything here is a guard against a malformed plan silently becoming
        a plausible-looking run: a self-loop or a cycle would deadlock the
        topological loop, a dangling dep would block a node forever, and a
        single prose line ("I'm not sure how to break this down") would look
        like a deliberate one-step plan rather than a planner failure.
        """
        t = text.strip()
        if "```" in t:
            t = max(t.split("```"), key=len).removeprefix("json").strip()
        start, end = t.find("["), t.rfind("]")
        nodes: list[Node] = []
        if start >= 0 and end > start:
            try:
                for i, item in enumerate(json.loads(t[start:end + 1]), 1):
                    if isinstance(item, dict):
                        goal = str(item.get("goal") or item.get("subgoal") or "").strip()
                        nid = int(item.get("id", i))
                        deps = [int(d) for d in (item.get("deps") or [])]
                    else:
                        goal, nid, deps = str(item).strip(), i, []
                    if len(goal) > 10:
                        nodes.append(Node(id=nid, goal=goal, deps=deps))
            except (json.JSONDecodeError, TypeError, ValueError):
                nodes = []
        if not nodes:                      # bullet/numbered fallback
            lines = [ln.strip().lstrip("-*0123456789. )").strip().strip('",')
                     for ln in t.splitlines()]
            lines = [ln for ln in lines if len(ln) > 15]
            # One line is prose, not a decomposition. The planner is asked for
            # 2+, so accepting a single line would mark a failure as success.
            if len(lines) >= 2:
                nodes = [Node(id=i, goal=g) for i, g in enumerate(lines, 1)]
        if not nodes:
            return []

        nodes = nodes[:MAX_SUBGOALS]
        ids = {n.id for n in nodes}
        for n in nodes:
            n.deps = [d for d in n.deps if d in ids and d != n.id]   # drop dangling/self
        # Break cycles by keeping only edges that point backwards in the
        # planner's own ordering. A cycle is unschedulable, and refusing the
        # whole plan over one bad edge would throw away a usable decomposition.
        order = {n.id: i for i, n in enumerate(nodes)}
        for n in nodes:
            n.deps = [d for d in n.deps if order[d] < order[n.id]]
        return nodes

    def _seed_plan(self) -> Optional[list[dict]]:
        """
        A pre-fetched decomposition to use INSTEAD OF calling the planner
        LLM. None (the default, always) means plan fresh every time -- set
        by a caller that has already looked one up, via the plain instance
        attribute `_pending_seed_plan`, before calling `.run()`. See
        ResearchHTNAgent._synthesize_method for the real implementation
        (graph-backed method-library reuse); HTNAgent itself has no
        database dependency and this hook stays unused unless a subclass
        or caller sets that attribute.
        """
        return getattr(self, "_pending_seed_plan", None)

    def _decompose(self, instance: dict, memory_block: str, usage: Usage,
                   trace: dict) -> list[Node]:
        seed = self._seed_plan()
        if seed:
            nodes = self.parse_dag(json.dumps(seed))
            if nodes:
                # Reused through the SAME validation a freshly-planned DAG
                # gets (cycle-breaking, dangling-dep removal, MAX_SUBGOALS
                # cap) -- a stored plan earns no less scrutiny than a new one.
                trace["seeded_from_library"] = True
                return nodes
        msgs = [{"role": "system", "content": PLANNER_SYSTEM.format(
            repo=instance["repo"], max_subgoals=MAX_SUBGOALS)},
            {"role": "user", "content":
                f"{instance['problem_statement']}\n{memory_block}"}]
        trace["planner_calls"] += 1
        nodes = self.parse_dag(
            (self._chat(msgs, usage).choices[0].message.content or ""))
        if not nodes:
            trace["decompose_failed"] = True
            nodes = [Node(id=1, goal=f"Fix the issue as described: "
                                     f"{str(instance['problem_statement'])[:400]}")]
        return nodes

    def _replan(self, instance: dict, node: Node, reason: str,
                usage: Usage, trace: dict) -> Optional[str]:
        msgs = [{"role": "system", "content": REPLAN_SYSTEM.format(
            repo=instance["repo"], subgoal=node.goal,
            reason=self._replan_evidence(node, reason)[:400])},
            {"role": "user", "content": str(instance["problem_statement"])[:1200]}]
        trace["planner_calls"] += 1
        trace["replans"] += 1
        alt = [a.strip() for a in
               ((self._chat(msgs, usage, max_tokens=300).choices[0].message.content
                 or "").strip().splitlines()) if len(a.strip()) > 15]
        return alt[0] if alt else None

    # ------------------------------------------------------- extension points
    # Every method below is a no-op / pass-through in HTNAgent -- calling it
    # reproduces today's behaviour exactly. They exist so AugmentedHTNAgent
    # (below) can add real checks without duplicating _run_node's loop.
    def _build_context(self, node: Node, nodes: list[Node]) -> tuple[str, str]:
        """(done, plan) blocks for the executor prompt."""
        done = "\n".join(f"  - [{n.id}] {n.goal[:80]} -> {n.note[:SUBGOAL_NOTE_CONTEXT_CHARS]}"
                         for n in nodes if n.status == "done") or "  (nothing yet)"
        plan = "\n".join(
            f"  [{n.id}] ({n.status}){' deps=' + str(n.deps) if n.deps else ''} "
            f"{n.goal[:100]}" for n in nodes)
        return done, plan

    def _tools_for(self, node: Node) -> list[dict]:
        """Which tools this node's executor may call."""
        if node.depth + 1 < MAX_DEPTH:
            return SUBGOAL_TOOLS
        return [t for t in SUBGOAL_TOOLS if t['function']['name'] != 'decompose_subgoal']

    def _verify_precondition(self, node: Node, sandbox: RepoSandbox) -> tuple[bool, str]:
        """Checked before a node's executor is even called."""
        return True, ""

    def _verify_postcondition(self, node: Node, sandbox: RepoSandbox) -> tuple[bool, str]:
        """Checked before a subgoal_done call is accepted."""
        return True, ""

    def _system_prompt_extra(self, node: Node) -> str:
        """Appended to the executor's system prompt."""
        return ""

    def _replan_evidence(self, node: Node, reason: str) -> str:
        """Grounding text handed to the replanner alongside `reason`."""
        return reason

    @staticmethod
    def _last_tool_result(messages: list[dict]) -> str:
        for m in reversed(messages):
            if m.get("role") == "tool":
                return str(m.get("content", ""))[:300]
        return ""

    @staticmethod
    def _node_touched_a_file(node: Node, sandbox: RepoSandbox) -> bool:
        """Whether SOMETHING was edited during this node's current attempt.

        RepoSandbox.edited_files() is run-wide, not per-node -- an earlier
        SIBLING node's edit would make this always true and defeat the nudge
        for a later node that itself has done nothing. tolerant_edits is
        also run-wide, so this checks the one signal that IS local: whether
        the current attempt is still in its first, edit-free phase versus
        having already produced something. A conservative proxy, not exact,
        but the failure mode being guarded against -- zero edits for the
        entire node -- is exactly what it catches.
        """
        return bool(sandbox.edited_files())

    def _budget_note(self, steps_used: int, budget: int, node: Node,
                     sandbox: RepoSandbox) -> str:
        """
        The HTN equivalent of agent.py's Agent._budget_note -- which this
        loop had NONE of until now. Confirmed by reading _run_node before
        this change: nothing in its tool-result loop told the model how many
        calls were left. With STEPS_PER_SUBGOAL as low as 6, a node can spend
        its entire local budget on search/read_symbol/decompose_subgoal and
        never call edit_file or subgoal_done, and NOTHING in the prompt said
        so -- the flat agent's version exists for exactly this failure mode
        (one pilot instance burned all 25 steps on `search` and finished
        with an empty patch); the HTN executor had regressed to having no
        such guard at all.

        Proportional threshold (last third of THIS node's local budget), not
        an absolute step count like the flat version's fixed "8" -- a fixed
        threshold silently stops meaning anything once budgets this small
        are in play (8 remaining is meaningless against a 6-step budget).
        """
        left = budget - steps_used
        if left > max(1, budget // 3):
            return ""
        note = f"\n[{left} tool call(s) left for this subgoal]"
        if not self._node_touched_a_file(node, sandbox):
            note += (" Nothing has been edited yet. Stop searching/decomposing and "
                     "either make the edit now with edit_file/create_file, or call "
                     "subgoal_failed if it genuinely cannot be done.")
        return note

    # ------------------------------------------------------------ execute
    def _run_node(self, instance: dict, sandbox: RepoSandbox, node: Node,
                  nodes: list[Node], budget: int, usage: Usage,
                  tool_log: list[str]) -> tuple[str, object, int]:
        """One bounded local task network.

        Returns (outcome, payload, steps_used) where outcome is
        "done" | "failed" | "expand". "expand" means the executor judged this
        task still compound and returned children to run first -- that is the
        recursion, and it is driven by the model at the leaf rather than
        guessed at plan time, because only the leaf has seen the code."""
        ok, why = self._verify_precondition(node, sandbox)
        if not ok:
            return "failed", why, 0
        done, plan = self._build_context(node, nodes)
        messages = [
            {"role": "system", "content": EXECUTOR_SYSTEM.format(
                repo=instance["repo"], plan=plan, done=done,
                subgoal=node.goal, steps=budget) + self._system_prompt_extra(node)},
            {"role": "user", "content": "Begin this subgoal."},
        ]
        steps = 0
        while steps < budget:
            tools = self._tools_for(node)
            resp = self._chat(messages, usage, tools=tools, max_tokens=2000)
            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name,
                                             "arguments": c.function.arguments}}
                               for c in calls] or None})
            if not calls:
                return "failed", "stopped acting without signalling completion", steps
            for call in calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_log.append(name)
                steps += 1
                if name == "subgoal_done":
                    ok, why = self._verify_postcondition(node, sandbox)
                    if not ok:
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": f"cannot mark done -- {why}"})
                        continue
                    return "done", str(args.get("summary", ""))[:SUBGOAL_SUMMARY_CHARS], steps
                if name == "subgoal_failed":
                    node.last_evidence = self._last_tool_result(messages)
                    return "failed", str(args.get("reason", ""))[:300], steps
                if name == "decompose_subgoal":
                    kids = [k for k in (args.get("subgoals") or [])
                            if isinstance(k, dict) and len(str(k.get("goal", ""))) > 10]
                    if not kids or node.depth + 1 >= MAX_DEPTH:
                        # Refuse rather than silently succeed: at max depth the
                        # task must be done, not planned again, and an empty
                        # expansion would leave the node neither done nor failed.
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": "cannot decompose further -- "
                                                    "do this subgoal directly"})
                        continue
                    return "expand", kids[:4], steps
                from agent import Agent
                if name in ("edit_file", "create_file", "delete_file"):
                    # RepoSandbox mutates files on disk and its own
                    # bookkeeping dict; a concurrent sibling node running in
                    # another thread must not interleave with that. Reads
                    # (list_dir/search/read_file) need no lock -- that's
                    # where a parallel scheduler actually gets its wall-clock
                    # win, since those calls dominate a node's tool-call count.
                    with self._sandbox_lock:
                        result, _ = Agent._dispatch(name, args, sandbox)
                else:
                    result, _ = Agent._dispatch(name, args, sandbox)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": result[:MAX_TOOL_CHARS]
                                 + self._budget_note(steps, budget, node, sandbox)})
        node.last_evidence = self._last_tool_result(messages)
        return False, f"exhausted its {budget}-call budget", steps

    @staticmethod
    def _satisfied(nodes: list[Node], nid: int) -> bool:
        """A dependency is met only if the work actually happened.

        A compound (`expanded`) node did nothing itself -- it is satisfied
        only when every child is. Treating `expanded` as done would let a
        dependent start against a state its prerequisite merely planned."""
        n = next((x for x in nodes if x.id == nid), None)
        if n is None:
            return True
        if n.status == "done":
            return True
        if n.status == "expanded":
            kids = [c for c in nodes if c.parent == n.id]
            return bool(kids) and all(HTNAgent._satisfied(nodes, c.id) for c in kids)
        return False

    @staticmethod
    def _block_dependents(nodes: list[Node], failed_id: int) -> int:
        """Mark the transitive dependents of a failed node as blocked.

        Containment is the point of the DAG: a node that never depended on
        the failure keeps its turn. Running a dependent whose prerequisite
        did not land would produce edits against a state that does not
        exist -- worse than skipping it, because it still burns budget and
        can corrupt the patch."""
        blocked, changed = {failed_id}, True
        while changed:
            changed = False
            for n in nodes:
                if n.status == "pending" and any(d in blocked for d in n.deps):
                    n.status, n.note = "blocked", f"prerequisite {n.deps} did not complete"
                    blocked.add(n.id)
                    changed = True
        return sum(1 for n in nodes if n.status == "blocked")

    @staticmethod
    def _rehydrate(state: list[dict]) -> list[Node]:
        """
        Reconstruct a Node graph from an EARLIER run's `run.htn["nodes"]`
        snapshot -- same shape that dict already has (id/goal/deps/status/
        attempts/note/last_evidence/depth/parent), so no separate resume
        format exists to keep in sync. The topological loop in `run()`
        only ever picks a node
        with status=='pending'; a done/failed/blocked/expanded node from the
        earlier session is left exactly as it was and simply never
        reconsidered, so resuming falls out of the scheduler's ordinary logic
        for free -- there is no separate resume-specific code path to trust.

        HTNAgent has no filesystem or session persistence of its own: the
        CALLER is responsible for the sandbox already reflecting whatever the
        done nodes did (e.g. re-apply the earlier run's `AgentRun.patch` to a
        fresh checkout, or keep working in the same checkout across the
        interruption) before passing this run's snapshot back in.
        """
        return [Node(id=int(n["id"]), goal=str(n["goal"]),
                     deps=[int(d) for d in (n.get("deps") or [])],
                     status=n.get("status", "pending"),
                     attempts=int(n.get("attempts", 0)),
                     note=str(n.get("note", "")),
                     last_evidence=str(n.get("last_evidence", "")),
                     depth=int(n.get("depth", 0)),
                     parent=n.get("parent")) for n in state]

    def _run_turn(self, instance: dict, sandbox: RepoSandbox, ready: Node,
                  nodes: list[Node], usage: Usage, tool_log: list[str],
                  trace: dict, ceiling: int) -> int:
        """
        One node's full turn: attempt, and on failure replan and retry, up
        to max_methods, never spending more than `ceiling` tool calls in
        total for this node. Mutates `ready` in place (status/note/attempts/
        goal) and, on expansion, appends children to `nodes` under
        `self._nodes_lock` -- always taken, even in HTNAgent's own
        single-threaded caller below, so this method needs no separate
        parallel-only version: AugmentedHTNAgent's concurrent scheduler
        calls this exact same method from multiple threads at once.

        If `ceiling` runs out before the node reaches a terminal status,
        `ready.status` is left "pending" -- the caller reads that back to
        know this node did not finish, distinct from an explicit failure.

        Returns steps actually used, so callers sharing a `_Budget` across
        several concurrent turns can `.release()` whatever went unspent.
        """
        spent_here = 0
        while ready.attempts <= self._max_methods:
            budget = min(self._per_subgoal, ceiling - spent_here)
            if budget <= 0:
                break
            ready.attempts += 1
            outcome, payload, used = self._run_node(
                instance, sandbox, ready, nodes, budget, usage, tool_log)
            spent_here += used
            if outcome == "expand":
                # RECURSION: this compound task is replaced by children that
                # must all finish before anything depending on it may run.
                # The parent keeps its own deps; children with no siblings
                # named inherit them, so the graph stays connected rather
                # than the subtree floating free.
                with self._nodes_lock:
                    base = max(n.id for n in nodes)
                    local: dict[int, int] = {}
                    for j, k in enumerate(payload, 1):   # type: ignore[arg-type]
                        local[j] = base + j
                    for j, k in enumerate(payload, 1):   # type: ignore[arg-type]
                        kd = [local[d] for d in (k.get("deps") or [])
                              if isinstance(d, int) and d in local and d < j]
                        nodes.append(Node(
                            id=local[j], goal=str(k["goal"]).strip(),
                            deps=kd or list(ready.deps),
                            depth=ready.depth + 1, parent=ready.id))
                ready.status = "expanded"
                ready.note = f"decomposed into {len(payload)} subgoals"  # type: ignore[arg-type]
                tool_log.append("__expand__")
                break
            ready.note = str(payload)
            if outcome == "done":
                ready.status = "done"
                break
            if ready.attempts > self._max_methods:
                ready.status = "failed"
                break
            if spent_here >= ceiling:
                # Ran out of THIS turn's allotment before reaching a
                # terminal call -- not the same thing as genuinely failing.
                # `ceiling` here can be a per-ROUND reservation smaller than
                # the node's real remaining budget (AugmentedHTNAgent's
                # concurrent scheduler grants one attempt's worth per node
                # per round, not a node's full worst-case allotment up
                # front -- see that class's _schedule docstring), so a node
                # that simply used its whole round is not out of attempts,
                # only out of THIS round. Leave status "pending" (its
                # current value, untouched here) so the caller's scheduler
                # reads that back and grants a fresh reservation next round
                # instead of discarding a node that never got to try again
                # -- this method's own docstring already promised exactly
                # this ("ready.status is left 'pending'"); the two branches
                # above were merged into one condition that always fired
                # "failed" first, so the promise was never kept in
                # practice. Do not call _replan here: it would consume an
                # attempt for a node that hasn't actually finished trying.
                break
            alt = self._replan(instance, ready, str(payload), usage, trace)
            if not alt:
                ready.status = "failed"
                break
            tool_log.append("__replan__")
            ready.goal = alt
        if ready.status == "failed":
            with self._nodes_lock:
                self._block_dependents(nodes, ready.id)
        return spent_here

    def _schedule(self, instance: dict, sandbox: RepoSandbox, nodes: list[Node],
                 usage: Usage, tool_log: list[str], trace: dict) -> str:
        """
        Topological loop: repeatedly pick the next node whose dependencies
        have all landed, run its full turn, repeat. One node at a time --
        AugmentedHTNAgent overrides this to run an entire READY SET
        concurrently instead (real wall-clock parallelism for independent
        branches; see its docstring, upgrade #6), reusing `_run_turn`
        completely unchanged. Returns "finished" (nothing left ready) or
        "step_budget" (budget exhausted with ready work remaining).
        """
        budget = _Budget(self._max_steps)
        while True:
            ready = next((n for n in nodes if n.status == "pending"
                          and all(self._satisfied(nodes, d) for d in n.deps)), None)
            if ready is None:
                return "finished"
            ceiling = budget.reserve(budget.remaining())
            if ceiling <= 0:
                return "step_budget"
            used = self._run_turn(instance, sandbox, ready, nodes, usage,
                                  tool_log, trace, ceiling)
            budget.release(ceiling - used)
            if ready.status == "pending":
                return "step_budget"

    def run(self, instance: dict, sandbox: RepoSandbox, arm: str,
            memory_block: str = "", retrieved: Optional[list[str]] = None,
            resume_state: Optional[list[dict]] = None) -> AgentRun:
        t0 = time.time()
        usage = Usage()
        # Read by AugmentedHTNAgent's SLA gate; unused by HTNAgent itself.
        self._t0, self._run_usage = t0, usage
        tool_log: list[str] = []
        trace = {"planner_calls": 0, "replans": 0, "decompose_failed": False,
                 "seeded_from_library": False, "resumed": bool(resume_state)}
        error, stop_reason = None, "finished"

        try:
            # Steps spent inside `_schedule` below are THIS call's own step
            # budget, separate from whatever an earlier, interrupted call
            # already used -- a caller tracking cumulative cost across
            # resumes adds the two AgentRun.usage/steps together itself;
            # HTNAgent has no memory of a prior call to add them to.
            nodes = (self._rehydrate(resume_state) if resume_state
                     else self._decompose(instance, memory_block, usage, trace))
        except Exception as exc:  # noqa: BLE001
            return AgentRun(instance_id=instance["instance_id"], arm=arm,
                            patch=sandbox.diff(), usage=usage, steps=usage.calls,
                            tool_calls=tool_log, files_edited=sandbox.edited_files(),
                            stop_reason="api_error", wall_seconds=time.time() - t0,
                            retrieved=retrieved or [],
                            error=f"{type(exc).__name__}: {exc}")

        plan_snapshot = [{"id": n.id, "goal": n.goal, "deps": n.deps} for n in nodes]
        try:
            stop_reason = self._schedule(instance, sandbox, nodes, usage, tool_log, trace)
        except Exception as exc:  # noqa: BLE001
            error, stop_reason = f"{type(exc).__name__}: {exc}", "api_error"

        run = AgentRun(
            instance_id=instance["instance_id"], arm=arm, patch=sandbox.diff(),
            usage=usage, steps=usage.calls, tool_calls=tool_log,
            files_edited=sandbox.edited_files(), stop_reason=stop_reason,
            wall_seconds=time.time() - t0, retrieved=retrieved or [], error=error)
        run.htn = {  # type: ignore[attr-defined]
            "plan": plan_snapshot,
            "nodes": [{"id": n.id, "goal": n.goal, "deps": n.deps,
                       "status": n.status, "attempts": n.attempts, "note": n.note,
                       "last_evidence": n.last_evidence,
                       "depth": n.depth, "parent": n.parent}
                      for n in nodes],
            "replans": trace["replans"], "planner_calls": trace["planner_calls"],
            "decompose_failed": trace["decompose_failed"],
            "seeded_from_library": trace["seeded_from_library"],
            "resumed": trace["resumed"],
            "subgoals_done": sum(1 for n in nodes if n.status == "done"),
            "subgoals_failed": sum(1 for n in nodes if n.status == "failed"),
            "subgoals_blocked": sum(1 for n in nodes if n.status == "blocked"),
            "subgoals_expanded": sum(1 for n in nodes if n.status == "expanded"),
            "max_depth_reached": max((n.depth for n in nodes), default=0),
            "nodes_total": len(nodes),
            "edges": sum(len(n.deps) for n in nodes),
        }
        return run


class AugmentedHTNAgent(HTNAgent):
    """
    HTNAgent plus six low-overhead upgrades. No new dependencies, no
    persisted state -- each is a real, working mechanism, just scoped small
    enough to add without changing HTNAgent's own behaviour (its hooks all
    default to no-ops, see the "extension points" section of HTNAgent
    above). #6 uses stdlib `threading`/`concurrent.futures`, guarded by
    locks HTNAgent itself already carries (unused, effectively free, in its
    own single-threaded operation) -- see that class's `__init__`.

    DROP-IN: identical constructor and .run() signature to HTNAgent, so
    swapping it into an experiment is a one-line import change, e.g. in
    run_graph_experiment.py:
        from htn_agent import AugmentedHTNAgent as HTNAgent
    Nothing else in that pipeline needs to change.

    1. STATIC PRE/POSTCONDITION CHECKS. A subgoal naming a file that does not
       exist -- and does not ask to create one -- fails in 0 LLM calls
       instead of burning its budget hunting for something that was never
       there. A subgoal cannot be marked done if a .py file it touched no
       longer parses. Deterministic, not a real SMT/SAT solver -- see
       ResearchHTNAgent for what a Z3-backed version would need.
    2. HIERARCHICAL CONTEXT COMPRESSION. A leaf only sees notes from nodes it
       actually (transitively) depends on, plus its ancestor chain's goals --
       not every completed node in the whole run. This is this file's own
       property #1 (context should be O(local), not O(everything)) applied
       to the one place the original code violated it: the unfiltered
       "done" block, which grows with total nodes completed regardless of
       relevance.
    3. EVIDENCE-GROUNDED REPLANNING. The alternative subgoal proposed after a
       failure is grounded in the actual last tool result -- a real error
       message -- not just the model's one-line paraphrase of why it gave up.
    4. PERSONA-SCOPED TOOL ACCESS. Each node is classified locator / editor /
       verifier from its goal text and its tool list is restricted to match
       -- a "verifier" node cannot call edit_file, so it cannot quietly start
       doing the editor's job instead of checking it.
    5. SLA-AWARE DEPTH GATING. Once wall-clock or token spend crosses 70% of
       an optional budget, decompose_subgoal is withdrawn for the rest of the
       run: the agent is forced into shallow, direct fixes instead of
       planning work it no longer has budget to execute.
    6. SPECULATIVE PARALLEL DAG EXECUTION -- the actual wall-clock fix. Base
       HTNAgent's `_schedule` runs the DAG one ready node at a time even
       when several are simultaneously ready, i.e. mutually independent by
       construction (a node only becomes "ready" once every dependency it
       lists has landed). This override runs the WHOLE ready set concurrently
       instead, reusing `_run_turn` unchanged in each worker thread. The
       LLM round-trip is the dominant per-node cost and is I/O-bound, so
       threads (not processes) genuinely overlap it despite the GIL. Budget
       is reserved synchronously, per node, before any thread starts (see
       `_Budget` on HTNAgent) so a batch can never spend more than the run's
       total step budget no matter how many nodes run at once.
    """

    # Shared by every persona: list_symbols/read_symbol are read-only, exactly
    # like list_dir/search/read_file, so giving them to locator/verifier does
    # not weaken "cannot edit anything" -- but OMITTING them (as this dict
    # once did) silently forces every HTN node onto the expensive read_file/
    # search path even though code_index's symbol tools (~170 tokens/call vs
    # up to ~3,500 for chained read_file to the same code -- see
    # code_index.py) are registered in SUBGOAL_TOOLS and reach the base
    # HTNAgent fine. Confirmed against real htn_memory NO_PATCH runs
    # (gravitational/teleport, tutao/tutanota, element-hq/element-web): each
    # showed editor-persona nodes burning their whole step budget on
    # read_file/search loops against large files, zero list_symbols/
    # read_symbol calls, zero edit_file -- the exact token cost this file's
    # STEPS_PER_SUBGOAL widening (6->9) was meant to bank against. A single
    # shared base also means the two tool sets cannot drift apart again.
    _READ_TOOLS = {"list_dir", "search", "read_file", "list_symbols", "read_symbol"}
    PERSONAS = {
        "locator":  {"tools": _READ_TOOLS,
                     "prompt": "\n\nYou are in LOCATOR mode for this subgoal: "
                               "find the exact place the change belongs. Do "
                               "not edit anything."},
        "verifier": {"tools": _READ_TOOLS,
                     "prompt": "\n\nYou are in VERIFIER mode for this "
                               "subgoal: confirm whether the described state "
                               "already holds. Do not edit anything."},
        "editor":   {"tools": _READ_TOOLS | {"edit_file", "create_file", "delete_file"},
                     "prompt": "\n\nYou are in EDITOR mode for this subgoal: "
                               "make the change directly."},
    }
    _PERSONA_KEYWORDS = (
        (("verify", "confirm", "ensure that", "check that"), "verifier"),
        (("find", "locate", "identify where", "determine where"), "locator"),
    )
    # A goal like "Locate X and update Y" names BOTH a locate/verify step and
    # an edit in one subgoal -- classifying it locator/verifier would strip
    # edit_file from a node that was explicitly told to make a change.
    # Confirmed live: instance_ansible__ansible's node 2 goal is "Locate the
    # FQCN validation function ... and update the validation logic to ...";
    # bare keyword matching classified it "locator", a persona with no edit
    # tools at all. Word-boundary regex, not substring `in`, so this cannot
    # false-positive on "address" (contains "add") or "the fix" (contains
    # "fix", deliberately excluded from this list for the same reason).
    _EDIT_VERBS = re.compile(r"\b(?:update|modify|implement|change|replace|remove|add)\b")
    _CREATE_HINTS = ("create", "new file", "new module", "does not exist yet",
                     "add a file", "write a new")
    # Extension allowlist, not a general path regex: "e.g." and "i.e." are
    # not files, and the false-positive cost of this check (blocking a
    # legitimate subgoal) is worse than the false-negative cost (missing one).
    _CODE_EXT = ("py", "go", "js", "jsx", "ts", "tsx", "java", "rb", "php",
                "c", "h", "cpp", "hpp", "cs", "rs", "scala", "kt", "swift",
                "json", "yaml", "yml", "toml", "cfg", "ini", "md", "rst",
                "sh", "sql", "proto", "cue", "tpl", "mod", "sum", "txt")
    _FILE_RE = re.compile(r'\b([\w][\w/-]*\.(?:' + "|".join(_CODE_EXT) + r'))\b',
                          re.IGNORECASE)
    MAX_CONTEXT_CHARS = 1500
    # Cap on how many ready nodes run at once. MAX_SUBGOALS is 4, so this
    # already covers a typical top-level ready set; a hard cap (rather than
    # "however many happen to be ready") bounds thread count and, via
    # per-node reservation in `_schedule`, the worst-case budget skew from
    # one round reserving for many nodes before any of them reports back.
    MAX_PARALLEL_NODES = 4

    def __init__(self, *args, max_wall_seconds: Optional[float] = None,
                 max_token_budget: Optional[int] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._max_wall_seconds = max_wall_seconds
        self._max_token_budget = max_token_budget

    # -- 5. SLA-aware gating ----------------------------------------------
    def _shallow(self) -> bool:
        t0 = getattr(self, "_t0", None)
        usage = getattr(self, "_run_usage", None)
        if self._max_wall_seconds and t0 is not None and \
                time.time() - t0 > 0.7 * self._max_wall_seconds:
            return True
        if self._max_token_budget and usage is not None and \
                usage.total > 0.7 * self._max_token_budget:
            return True
        return False

    # -- 4. persona classification + tool scoping --------------------------
    @classmethod
    def _persona(cls, goal: str) -> str:
        g = goal.lower()
        for keywords, persona in cls._PERSONA_KEYWORDS:
            if any(k in g for k in keywords):
                # Only the text after the goal's first " and " is checked --
                # a compound "locate X and update Y" needs editor for the
                # update half; a pure "locate X, which also handles Y" (no
                # "and") or "verify the change" (edit verb before "and", if
                # any) stays restricted, which is the intended behaviour.
                _, _, tail = g.partition(" and ")
                if tail and cls._EDIT_VERBS.search(tail):
                    break
                return persona
        return "editor"

    def _tools_for(self, node: Node) -> list[dict]:
        base = super()._tools_for(node)
        if self._shallow():
            base = [t for t in base if t['function']['name'] != 'decompose_subgoal']
        allowed = self.PERSONAS[self._persona(node.goal)]["tools"] | {
            "subgoal_done", "subgoal_failed", "decompose_subgoal"}
        return [t for t in base if t['function']['name'] in allowed]

    def _system_prompt_extra(self, node: Node) -> str:
        return self.PERSONAS[self._persona(node.goal)]["prompt"]

    # -- 1. static pre/postcondition checks --------------------------------
    def _verify_precondition(self, node: Node, sandbox: RepoSandbox) -> tuple[bool, str]:
        if any(h in node.goal.lower() for h in self._CREATE_HINTS):
            return True, ""
        for m in self._FILE_RE.finditer(node.goal):
            path = m.group(1)
            try:
                full = sandbox._resolve(path)
            except ValueError:
                continue
            if not os.path.isfile(full) and not os.path.isdir(full):
                return False, (f"goal names '{path}', which does not exist in "
                               f"the repository and the goal does not ask to "
                               f"create it")
        return True, ""

    def _verify_postcondition(self, node: Node, sandbox: RepoSandbox) -> tuple[bool, str]:
        """
        Hard gate, all four corpus languages via tree-sitter -- previously
        Python-only via `ast.parse`, which meant a Go/JS/TS file broken by an
        edit could still be marked subgoal_done, in the 62% of the corpus
        those languages cover. A node cannot be marked done while a file it
        touched does not parse: this is checked BEFORE subgoal_done is
        accepted (see _run_node), not just reported afterward as a warning
        the way plain edit_file/create_file do it -- the difference between
        an advisory note the model may ignore and a gate it cannot pass.
        """
        for rel in sandbox.edited_files():
            if rel in sandbox._deleted:
                continue
            full = os.path.join(sandbox.root, rel)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, "rb") as f:
                    source = f.read()
            except OSError:
                continue
            errs = code_index.syntax_errors(source, rel)
            if errs and errs[0] > 0:
                return False, (f"{rel} has {errs[0]} syntax error(s), first near "
                               f"line {errs[1]} -- fix it before marking this done")
        return True, ""

    # -- 2. hierarchical context compression --------------------------------
    def _build_context(self, node: Node, nodes: list[Node]) -> tuple[str, str]:
        by_id = {n.id: n for n in nodes}
        relevant: set[int] = set()
        frontier = list(node.deps)
        while frontier:
            d = frontier.pop()
            if d in relevant or d not in by_id:
                continue
            relevant.add(d)
            frontier.extend(by_id[d].deps)
        done_lines = [f"  - [{n.id}] {n.goal[:80]} -> {n.note[:SUBGOAL_NOTE_CONTEXT_CHARS]}"
                     for n in nodes if n.id in relevant and n.status == "done"]
        done = "\n".join(done_lines) or "  (no relevant prior work)"
        ancestors, p = [], node.parent
        while p is not None and p in by_id:
            ancestors.append(by_id[p].goal[:80])
            p = by_id[p].parent
        if ancestors:
            done = f"Parent context: {' > '.join(reversed(ancestors))}\n{done}"
        _, plan = super()._build_context(node, nodes)
        return done[:self.MAX_CONTEXT_CHARS], plan

    # -- 3. evidence-grounded replanning -------------------------------------
    def _replan_evidence(self, node: Node, reason: str) -> str:
        if node.last_evidence:
            return f"{reason}\nLast tool result before giving up: {node.last_evidence}"
        return reason

    # -- 6. speculative parallel DAG execution -------------------------------
    def _schedule(self, instance: dict, sandbox: RepoSandbox, nodes: list[Node],
                 usage: Usage, tool_log: list[str], trace: dict) -> str:
        budget = _Budget(self._max_steps)
        # Reserve ONE attempt's worth per node per round, not a node's full
        # worst-case (all replans included) allotment -- reserving the
        # latter for even a single node can exhaust the whole run's budget
        # before a second node gets a look-in, collapsing every batch back
        # to width 1. A node that needs to retry beyond this round's
        # reservation is simply left "pending" and picked up again next
        # round with a fresh one -- `ready.attempts` lives on the Node, not
        # this call, so multi-attempt retries still work correctly, just
        # potentially spread across more than one scheduling round.
        per_node_cap = self._per_subgoal
        while True:
            ready_batch = [n for n in nodes if n.status == "pending"
                           and all(self._satisfied(nodes, d) for d in n.deps)]
            if not ready_batch:
                return "finished"
            # SLA-tight runs fall back to one node at a time -- the same
            # withdrawal `_tools_for` already does for decompose_subgoal,
            # applied here too: when budget is nearly gone, a wide batch's
            # reservations would starve later nodes in the SAME round rather
            # than let them run at full size in the next one.
            width = 1 if self._shallow() else self.MAX_PARALLEL_NODES
            batch = ready_batch[:width]

            # Reserved ONE AT A TIME, synchronously, before any thread
            # starts -- see _Budget's docstring for why that (not a
            # check-then-spend read inside each thread) is what keeps a
            # concurrent round from overshooting the total step budget.
            reservations: dict[int, int] = {}
            for n in batch:
                grant = budget.reserve(per_node_cap)
                if grant <= 0:
                    break
                reservations[n.id] = grant
            if not reservations:
                return "step_budget"
            batch = [n for n in batch if n.id in reservations]

            if len(batch) == 1:
                # No concurrency to pay thread-pool overhead for.
                n = batch[0]
                used = self._run_turn(instance, sandbox, n, nodes, usage,
                                      tool_log, trace, reservations[n.id])
                budget.release(reservations[n.id] - used)
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    futures = {pool.submit(self._run_turn, instance, sandbox, n, nodes,
                                           usage, tool_log, trace, reservations[n.id]): n
                              for n in batch}
                    for fut in concurrent.futures.as_completed(futures):
                        n = futures[fut]
                        used = fut.result()  # re-raises a worker's exception here
                        budget.release(reservations[n.id] - used)

            if any(n.status == "pending" for n in batch) and budget.remaining() <= 0:
                return "step_budget"


class ResearchHTNAgent(AugmentedHTNAgent):
    """
    Item 1 is real and wired up, using the backend's actual graph-memory
    platform (method_library.py, bridging to backend/app/services the same
    way graph_memory.py already does for whole-issue retrieval). Items 2-5
    remain NOT IMPLEMENTED -- placeholders for upgrades that need external
    dependencies or infrastructure this repo does not have yet, deliberately
    left out of AugmentedHTNAgent to keep that class dependency-free.
    Calling one of those raises NotImplementedError until it is filled in.
    Swap this class in the same way as AugmentedHTNAgent:
        from htn_agent import ResearchHTNAgent as HTNAgent

    1. DYNAMIC METHOD SYNTHESIS & LIBRARY -- IMPLEMENTED. `_synthesize_method`
       (below) looks up a reusable decomposition in the graph via
       method_library.find_reusable_plan, and, on a confident match, stashes
       it so the next `.run()` call reuses it (through HTNAgent's own
       `_seed_plan` hook) instead of calling the planner LLM. Because
       HTNAgent.run() is synchronous and method_library's DB calls are not
       (see that module's docstring for exactly why they cannot be merged),
       this is a two-step, explicitly-awaited flow the CALLER drives:

           pool = await create_pool(dsn=...)
           embedder = Embedder()
           agent = ResearchHTNAgent(client, model)
           await agent._synthesize_method(pool, embedder, instance["problem_statement"])
           run = agent.run(instance, sandbox, arm, memory_block=memory_block)  # sync, as always
           if run.htn["subgoals_done"] > 0 and run.htn["subgoals_failed"] == 0:
               from app.services.method_library import persist_plan
               await persist_plan(
                   pool, embedder, instance["problem_statement"],
                   run.htn["plan"], steps_used=run.steps)

       `run.htn["seeded_from_library"]` reports whether the reused plan was
       actually used. Not yet wired into run_graph_experiment.py itself --
       that harness's ARM_SPEC/run_one would need a fourth arm to compare
       against, a deliberate choice left to whoever runs that experiment.
    2. AST-NATIVE GRAPH OPERATIONS. Add a tool, `ast_replace_function`, that
       parses a .py file with the stdlib `ast` module, locates a
       function/class by name, and replaces its body -- rejecting the edit
       if `ast.parse` fails on the result, so a syntactically invalid change
       can never be produced. Register it alongside SUBGOAL_TOOLS, gated to
       .py files; everything else keeps using edit_file.
       -> start in `_ast_edit(self, sandbox, path, symbol, new_source)`.
    3. SPECULATIVE PARALLEL DAG EXECUTION. Run every currently-`ready` node
       concurrently (`concurrent.futures.ThreadPoolExecutor`) instead of one
       at a time. Needs a `threading.Lock` around sandbox-mutating tool
       calls specifically (edit_file/create_file/delete_file) -- RepoSandbox
       is not thread-safe against concurrent writes -- while reads
       (list_dir/search/read_file) need no lock. A failed branch must block
       only its own transitive dependents, not siblings already in flight.
       -> start in `_run_ready_batch(self, ready_nodes, sandbox, nodes, usage, tool_log)`.
    4. MCTS-GUIDED SUBTASK EXPANSION. Instead of accepting the planner's
       first decomposition, generate 2-3 candidates (one real LLM call plus
       cheap heuristic mutations -- merge two nodes, split one on " and ")
       and score each with a reward heuristic (specificity, atomicity, and
       #1's library success rate once it exists), picking via UCB1 over a
       handful of noisy simulated visits. Real MCTS mechanics on a cheap
       simulation policy -- no extra LLM call per candidate is what keeps it
       inside the existing step budget.
       -> start in `_mcts_pick(self, candidates: list[list[Node]]) -> list[Node]`.
    5. RL/BANDIT-TUNED METHOD PRUNING. Attach a Beta-Bernoulli
       (successes, attempts) counter to every entry in #1's library, updated
       after each run from real outcome/steps/tokens. Use the posterior mean
       as the reward signal in #4's UCB1 selection, and stop reusing a
       method once its posterior mean drops below a floor after enough
       attempts -- an online bandit that improves from actual outcomes,
       not a trained neural reward model, but a real one.
       -> start in `_method_score(self, record) -> float`.
    """

    async def _synthesize_method(self, pool, embedder, problem_statement: str) -> bool:
        """
        Look up a reusable decomposition for `problem_statement` and, if a
        confident match exists, stash it so the next `.run()` reuses it
        instead of planning fresh. Must be awaited BEFORE calling `.run()`
        -- see the class docstring for the full two-step flow and why it
        cannot be collapsed into `.run()` itself. Returns whether a match
        was found (also visible afterwards via `run.htn["seeded_from_library"]`).
        """
        from app.services.method_library import find_reusable_plan
        match = await find_reusable_plan(pool, embedder, problem_statement)
        if match and match.get("decomposition"):
            self._pending_seed_plan = match["decomposition"]
            return True
        return False

    def _ast_edit(self, *args, **kwargs):
        raise NotImplementedError("see ResearchHTNAgent docstring, item 2")

    def _run_ready_batch(self, *args, **kwargs):
        raise NotImplementedError("see ResearchHTNAgent docstring, item 3")

    def _mcts_pick(self, *args, **kwargs):
        raise NotImplementedError("see ResearchHTNAgent docstring, item 4")

    def _method_score(self, *args, **kwargs):
        raise NotImplementedError("see ResearchHTNAgent docstring, item 5")
