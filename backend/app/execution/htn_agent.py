"""
HTN agent: LLM decomposition into a DAG, bounded local execution per node.

TICKET 15 (memory-substrate map, HTN relocation) -- relocated here from
experiments/swebench_pro/htn_agent.py, which now re-exports everything
from this module as a back-compat shim (the ~50 existing `from htn_agent
import X` call sites across this repo's tests and experiment scripts
keep working unchanged; new code should import from here directly).

RunContext extraction (this move's concrete structural fix, not just a
file relocation): every piece of state that used to live on `self` and
get silently overwritten by the NEXT .run() call -- `self._t0`,
`self._run_usage`, `self._pending_seed_plan`, and the three locks
(`self._usage_lock`, `self._nodes_lock`, `self._sandbox_lock`) -- now
lives on a RunContext created fresh by run() itself. This is the direct
fix for the exact problem run_graph_experiment.py's own comment names:
"HTNAgent.run() stashes per-run state on self... two .run() calls on
the SAME agent object at once would stomp each other's budget
bookkeeping." Concurrent .run() calls on one agent instance are now
safe -- see RunContext's own docstring for the two bugs this also fixes
along the way (a lock-sharing contention bug and a stale-seed-plan
leak), found while doing the extraction, not assumed away.

Scheduler-strategy restructuring (dissolving the HTNAgent ->
AugmentedHTNAgent -> ResearchHTNAgent inheritance chain into one engine
with a pluggable scheduler) is ticket 15's OTHER named structural flaw
and is explicitly NOT done in this pass -- deferred to a dedicated
follow-up given the real risk of touching _schedule's concurrent
scheduling logic, which has several hard-won regression fixes documented
in its own comments (see that method). The class hierarchy below is
unchanged from the original file.

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
    MAX_RETRIES, MAX_TOOL_CHARS, REQUEST_TIMEOUT, SPEC_INTERFACE_CHARS,
    SPEC_REQUIREMENTS_CHARS, TOOLS, AgentRun, RepoSandbox, Usage, _spec_field,
    backoff_seconds, is_transient, spec_block,
)

# Re-exported so both agents' callers and tests read the caps from one
# place; the definitions live in agent.py because the flat agent needs
# them too and htn_agent already depends on that module, not the reverse.
__all_spec_caps__ = (SPEC_REQUIREMENTS_CHARS, SPEC_INTERFACE_CHARS)

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
# MAX_SUBGOALS * STEPS_PER_SUBGOAL = 36 is the minimum budget that can fund
# ONE attempt for every planned node; 28 (this file's old value, "same leaf
# budget as the flat agent") could not, and measured on 11 real runs it
# starved 7 of 32 planned nodes down to zero tool calls (never even attempted)
# and left 8 of 11 runs ending in stop_reason=step_budget with only 2
# reaching "finished". 72 = MAX_SUBGOALS * STEPS_PER_SUBGOAL * 2, funding one
# retry (max_methods allows up to 3) for every node, not just the first.
TOTAL_STEP_BUDGET = 72
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

# Fewest tool calls a round must offer before it is worth spending one of a
# node's `max_methods + 1` attempts on. A node's per-round ceiling is
# whatever _Budget has left, so late rounds shrink -- and a 1-call round
# cannot read anything AND then act, so it can never reach subgoal_done or
# subgoal_failed. Charging an attempt for it is charging for a round that
# was never winnable: gravitational/teleport's node 2 died at attempts=3
# with the note "exhausted its 1-call budget", which blocked nodes 3 and 4
# and left 3 of its 4 subgoals never run.
#
# 3 = one read, one edit, one terminal call. Below that, _run_turn returns
# the node to "pending" WITHOUT consuming an attempt so the scheduler can
# grant it a full reservation next round.
MIN_VIABLE_SUBGOAL_BUDGET = 3

# Below this many total nodes, _build_context's plan listing shows every
# node in full -- unchanged from the original behaviour, so existing small
# plans (the common case) see no difference. Above it, listing every node
# in every OTHER node's prompt is exactly the flat agent's "resend
# everything" problem recreated at the plan-graph level: prompt size grows
# with TOTAL node count instead of with what's actually relevant to the
# node being executed. Past this threshold, only the current node's own
# transitive dependencies are listed in full; everything else collapses to
# one count line. Set just above the old MAX_SUBGOALS=4 ceiling so a plan
# at the old size is never affected by this at all.
PLAN_CONTEXT_MAX_NODES = 6


# Ticket 15 (memory-substrate map, HTN relocation) -- hyperparameter split
# by transferability. Every constant above carries an in-source comment
# citing a measurement taken in the SWE-bench domain; the map has since
# placed that domain out of scope for this project, making "are these
# values still justified" a real, not rhetorical, question. The
# literature split the ticket adopted decides it:
#
#   "Structural limits transfer well across task distributions...
#    Budgets, thresholds and stopping criteria are highly
#    distribution-sensitive."
#
# HONEST SCOPE: this is additive config, not a behavior change. Every
# existing caller (run_graph_experiment.py's `htn_kwargs`, every test in
# this repo) constructs HTNAgent/AugmentedHTNAgent with the SAME
# individual keyword arguments as before, at the SAME default values --
# nothing here alters what any existing call site does. What's new is a
# real, usable object (HTNConfig) a caller CAN construct instead, via
# HTNAgent.from_config(), plus the explicit provisional/structural
# labeling ticket 15 asked for "at the point of definition, not in a
# comment elsewhere." Per-procedure override (reading these from a
# procedure's own domain_payload once one is selected) is NOT wired in
# this pass -- that needs a real caller passing a selected procedure
# through to construction, which doesn't exist without the
# scheduler-strategy engine this pass explicitly deferred.
@dataclass(frozen=True)
class StructuralLimits:
    """
    Ticket 15: "Structural limits transfer well across task
    distributions -- MAX_DEPTH, parallelism cap." These describe the
    SHAPE of what any procedure/plan can express (recursion depth, how
    many independent branches may run at once), not a distribution-
    sensitive stopping threshold -- carried over as sound, static
    config, not marked provisional. Frozen: nothing in this pass gives
    a reason to override these per-procedure or per-run.
    """
    max_depth: int = MAX_DEPTH
    max_parallel_nodes: int = 4  # AugmentedHTNAgent.MAX_PARALLEL_NODES's value, duplicated
    # here deliberately rather than imported from the class (which isn't
    # defined yet at this point in the module) -- both are asserted equal
    # by test_htn_config_matches_class_level_constants below, so they
    # cannot silently drift apart.


@dataclass
class DistributionalBudgets:
    """
    Ticket 15: "Budgets, thresholds and stopping criteria are highly
    distribution-sensitive -- MAX_SUBGOALS, retry attempts, per-subtask
    and total step budgets, MIN_VIABLE_SUBGOAL_BUDGET." Carried over
    from SWE-bench-domain measurements (see each constant's own
    historical comment above this class), now explicitly marked
    PROVISIONAL for whatever domain this engine actually runs against
    next -- per the ticket's own instruction to state that "at the
    point of definition," not bury it in a comment elsewhere. Mutable
    (not frozen), since ticket 15 describes these as becoming "config
    with per-procedure override" once procedures exist to override them.

    Adaptive per-instance derivation (empirical-Bayes shrinkage toward a
    global mean, the ticket's own named long-term answer) is explicitly
    deferred on the ticket's own stated grounds: it needs ~30-50
    executions per procedure type before it outperforms a static
    default, which nothing in this repo has accumulated yet. These
    values ARE that static default -- unchanged from the file's
    original constants, just now labeled honestly instead of implicitly
    trusted.
    """
    max_subgoals: int = MAX_SUBGOALS
    max_methods: int = MAX_METHODS
    steps_per_subgoal: int = STEPS_PER_SUBGOAL
    total_step_budget: int = TOTAL_STEP_BUDGET
    min_viable_subgoal_budget: int = MIN_VIABLE_SUBGOAL_BUDGET
    # NOT explicitly named in ticket 15's own structural/distributional
    # split -- grouped here as a judgement call I'm making explicit, not
    # a resolved ticket decision. Its own in-source comment ties its
    # value directly to MAX_SUBGOALS ("set just above the old
    # MAX_SUBGOALS=4 ceiling"), and MAX_SUBGOALS is itself
    # distributional, so treating this one as structural would be
    # inconsistent with how its own value was derived.
    plan_context_max_nodes: int = PLAN_CONTEXT_MAX_NODES

    PROVISIONAL: bool = field(default=True, repr=False, compare=False)


@dataclass
class HTNConfig:
    """
    The real, usable object ticket 15 asks for -- pass to
    HTNAgent.from_config()/AugmentedHTNAgent.from_config() instead of
    individual keyword arguments. Existing individual-kwarg construction
    remains fully supported and unchanged; this is an additive
    alternative, not a replacement.
    """
    structural: StructuralLimits = field(default_factory=StructuralLimits)
    budgets: DistributionalBudgets = field(default_factory=DistributionalBudgets)

# Prefix on every path-resolution hint. Exists so tests (and a human
# reading a transcript) can tell an injected hint from EXECUTOR_SYSTEM's
# own boilerplate, which already contains the phrase "does not exist"
# ("Use create_file when the subgoal calls for a file that does not exist
# yet") -- keying on that phrase matches the wrong text.
PATH_HINT_MARKER = "PATH CHECK:"

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
lookup" is executable. If a CANDIDATE FILES list is given below, prefer a \
path from it -- those are verified to exist in this checkout. If the fix \
genuinely needs a different or new file, say so explicitly rather than \
guessing a plausible-looking path.
- RIGHT-SIZED, NOT ARTIFICIALLY SMALL: prefer a few reads and one or two \
edits when that's genuinely enough. But if the fix plausibly touches \
several files or you are not yet sure how many edits it needs, it is \
BETTER to write one subgoal naming the right area than to guess narrowly \
and silently omit files it will turn out to need -- the executor can call \
decompose_subgoal once it has seen the actual code and knows the real \
shape of the work. A subgoal that decomposes further is a normal, \
expected outcome, not a planning failure.
- HONEST ABOUT DEPENDENCIES: `deps` lists the ids of subgoals this one is \
ORDERED after -- use it freely, most real fixes have a natural sequence. \
`requires` is the narrower, stricter claim that this subgoal cannot even \
START until that one's edits physically exist (e.g. it edits a function the \
other one creates) -- list an id there ONLY if that is true; most `deps` \
should NOT also be in `requires`. A subgoal blocked by a `deps`-only \
predecessor's failure still gets to run; one blocked by a `requires` \
predecessor's failure does not, so overusing `requires` needlessly kills \
work that could otherwise proceed.

If the fix needs a file that does not exist, say so -- creating one is a \
normal subgoal.

Reply with ONLY a JSON array, no prose or fences:
[{{"id": 1, "goal": "...", "deps": []}}, \
{{"id": 2, "goal": "...", "deps": [1], "requires": [1]}}]"""

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
{spec_block}
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
    # SUBSET of deps meaning "cannot even start until that one's edits
    # physically landed" -- e.g. this subgoal edits a function the other
    # one creates. deps alone is just ORDERING: the planner's own honest
    # sequencing of subgoals, most of which do not need this. The
    # distinction is what _block_dependents/_satisfied act on: a failed
    # requires-predecessor blocks this node (the real invariant -- never
    # edit against a state that does not exist); a failed deps-only
    # predecessor does not, this node still gets its turn. See
    # _block_dependents's own docstring for the measured cost of treating
    # every deps edge as a hard blocker.
    requires: list[int] = field(default_factory=list)
    # pending | done | failed | blocked | expanded
    # `expanded` is a COMPOUND task: it did no work itself, its children did.
    # It counts as satisfied only when all of its children are satisfied,
    # which is what makes the recursion sound -- a dependent must not start
    # because its prerequisite merely *planned* something.
    status: str = "pending"
    attempts: int = 0
    note: str = ""
    # Advisory text from the path precondition, e.g. "the file is actually
    # at X". Set by _verify_precondition (which runs before the executor's
    # messages are built) and rendered by _system_prompt_extra.
    path_hint: str = ""
    depth: int = 0
    parent: Optional[int] = None
    # Last tool result seen before this node gave up, set by _run_node
    # regardless of subclass. Unused by HTNAgent itself -- it exists so
    # AugmentedHTNAgent's replanner can ground its alternative in a real
    # error instead of the model's one-line paraphrase of it.
    last_evidence: str = ""

    # ---- instrumentation only; never read by any scheduling/planning
    # decision in this file. Written by exactly ONE thread at a time: each
    # scheduler (_schedule, both the base and concurrent versions) submits
    # exactly one _run_turn per distinct node, the only OTHER cross-thread
    # writes to a Node anywhere are _block_dependents's status/note (under
    # _nodes_lock) and child-node construction on expand, and run() reads
    # these fields only after the ThreadPoolExecutor context has exited --
    # a happens-before edge. That confinement is why plain += below needs
    # no lock; a future change that lets two turns for one node overlap, or
    # that submits the same node twice in one round, would silently break
    # this invariant.
    steps_used: int = 0             # tool calls charged to this node, all rounds/attempts
    budget_granted: int = 0         # sum of _Budget reservations made for this node
    rounds: int = 0                 # scheduling rounds it received a reservation in
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Occupancy (summed across turns), not span -- under concurrent
    # scheduling sum(node.wall_seconds) / run.wall_seconds is the achieved-
    # parallelism measurement, otherwise unobtainable from the run alone.
    wall_seconds: float = 0.0
    started_at: Optional[float] = None   # epoch, first turn entered
    ended_at: Optional[float] = None     # epoch, last turn left
    tool_calls: list[str] = field(default_factory=list)     # this node's own, in order
    files_edited: list[str] = field(default_factory=list)   # repo-relative, deduped, ordered


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


class _NodeUsage:
    """
    Write-through view of a run's real Usage that ALSO charges one Node.

    Duck-types the one method `_chat` calls (`.add(u)`), and is constructed
    to REPLACE the `usage` local inside `_run_turn` for that node's turn --
    so `_run_node` (unmodified) and `_replan` (unmodified) both charge the
    node automatically, without either needing to know a node is being
    tracked.

    `_chat` already mutates the shared Usage under `ctx.usage_lock`
    (see RunContext) -- `add` here runs the node-side write inside that
    SAME critical section, so no second lock or lock ordering is
    introduced.

    Deliberately NOT "a fresh per-node Usage(), merged into the global one
    when the turn ends": `_run_turn` can raise (a worker's exception
    re-raises at `fut.result()`), and `run()` still builds an AgentRun from
    the real `usage` on that path (see `run()`'s except clause) -- deferred
    merging would silently drop that turn's tokens from `run.usage.total`
    on exactly the api_error rows most worth diagnosing. It would also be
    wrong to merge with `Usage.add`, since that increments `.calls` by 1
    per call, not by the merged object's own `.calls` -- merging two Usage
    objects with the same `.add` double-counts nothing meaningfully; it
    undercounts silently. Charging both counters at the true call site
    avoids the whole class of bug.
    """

    def __init__(self, shared: Usage, node: Node):
        self._shared = shared
        self._node = node

    def add(self, u) -> None:
        self._shared.add(u)
        self._node.prompt_tokens += u.prompt_tokens
        self._node.completion_tokens += u.completion_tokens
        self._node.llm_calls += 1

    # Anything reading run totals through this wrapper (there is no such
    # call site today, but a future one should not silently see zeros)
    # keeps seeing the real, shared numbers.
    @property
    def prompt_tokens(self) -> int:
        return self._shared.prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self._shared.completion_tokens

    @property
    def calls(self) -> int:
        return self._shared.calls

    @property
    def total(self) -> int:
        return self._shared.total


@dataclass
class RunContext:
    """
    Ticket 15's RunContext extraction: every piece of state that used to
    live on `self` and get silently overwritten by the NEXT .run() call
    on the same agent instance. Created fresh inside run() itself;
    nothing here is agent-level configuration (that stays a constructor
    argument, since it's genuinely shared and read-only across runs).

    Two real bugs fixed by this extraction, found while doing it rather
    than assumed away:

    1. LOCK CONTENTION ACROSS UNRELATED RUNS. The three locks
       (usage_lock, nodes_lock, sandbox_lock) were previously agent-level
       (self._usage_lock etc), shared across every .run() call on that
       instance. Two concurrent runs against DIFFERENT sandboxes/usage/
       node-lists still needlessly contended on the SAME lock objects --
       not a correctness bug (each run's own data was never shared), but
       a real, avoidable serialization point that defeats the entire
       purpose of enabling concurrent runs. Each RunContext now gets its
       own three locks.
    2. STALE SEED-PLAN LEAK. `_pending_seed_plan` was read by
       `_seed_plan()` via `getattr(self, "_pending_seed_plan", None)`
       but never cleared -- so a seed plan set before one `.run()` call
       (ResearchHTNAgent._synthesize_method's documented pattern) would
       silently survive and get reused by a SUBSEQUENT, unrelated
       `.run()` call on the same agent instance that never set one of
       its own. `run()` now reads-and-clears this attribute atomically
       at the very start, before any other per-run state exists.
    """
    t0: float
    usage: Usage
    usage_lock: threading.Lock = field(default_factory=threading.Lock)
    nodes_lock: threading.Lock = field(default_factory=threading.Lock)
    sandbox_lock: threading.Lock = field(default_factory=threading.Lock)
    pending_seed_plan: Optional[list[dict]] = None


def _node_row(n: Node) -> dict:
    """
    Serialise one Node for `run.htn["nodes"]`. Keeps the original 11 keys
    IN PLACE (additive-only contract -- every existing consumer of a result
    row, and every already-written .jsonl file, keeps working unchanged)
    and appends the instrumentation fields.
    """
    return {
        "id": n.id, "goal": n.goal, "deps": n.deps, "requires": n.requires,
        "status": n.status, "attempts": n.attempts, "note": n.note,
        "last_evidence": n.last_evidence, "path_hint": n.path_hint,
        "depth": n.depth, "parent": n.parent,
        "steps_used": n.steps_used, "budget_granted": n.budget_granted,
        "rounds": n.rounds, "llm_calls": n.llm_calls,
        "prompt_tokens": n.prompt_tokens, "completion_tokens": n.completion_tokens,
        "total_tokens": n.prompt_tokens + n.completion_tokens,
        "wall_seconds": round(n.wall_seconds, 3),
        "started_at": round(n.started_at, 3) if n.started_at is not None else None,
        "ended_at": round(n.ended_at, 3) if n.ended_at is not None else None,
        "tool_calls": list(n.tool_calls), "n_tool_calls": len(n.tool_calls),
        "files_edited": list(n.files_edited),
        "replans": n.tool_calls.count("__replan__"),
    }


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
        # Real, internal-only handoff slot for ResearchHTNAgent's
        # _synthesize_method (set before .run(), read-and-cleared by
        # run() itself into that call's own RunContext -- see
        # RunContext's docstring for the staleness bug this fixes).
        # NOT per-run state itself; it is the mechanism by which
        # pre-fetched async work reaches the next run() call, which is
        # exactly the sync-core/async-shell boundary pattern ticket 15
        # adopts uniformly, not a special case.
        self._pending_seed_plan: Optional[list[dict]] = None

    @classmethod
    def from_config(cls, client, model: str, config: "HTNConfig", **kwargs) -> "HTNAgent":
        """
        Ticket 15's real, usable config object -- construct from an
        HTNConfig instead of individual keyword arguments.

        HONEST LIMIT, not silently papered over: only
        total_step_budget/steps_per_subgoal/max_methods are threaded
        through here, because those are the only DistributionalBudgets
        fields that were ALREADY per-instance constructor state in the
        original file. max_subgoals, min_viable_subgoal_budget, and
        plan_context_max_nodes are bare MODULE-LEVEL constants
        (MAX_SUBGOALS, MIN_VIABLE_SUBGOAL_BUDGET, PLAN_CONTEXT_MAX_NODES)
        referenced directly inside parse_dag/_run_turn/_build_context --
        never instance attributes at all in the code this relocation
        moved. Making those three genuinely per-instance/per-config
        would be real behavior-widening surgery to those methods, not a
        safe additive wrapper, and is out of scope for this pass --
        flagged here rather than silently claimed as done.
        max_depth/max_parallel_nodes (StructuralLimits) are likewise not
        threaded through: max_depth is a module constant never varied
        per-instance in the original file either, and max_parallel_nodes
        is AugmentedHTNAgent-only (base HTNAgent runs one node at a time
        by design).
        """
        return cls(
            client, model,
            max_steps=config.budgets.total_step_budget,
            steps_per_subgoal=config.budgets.steps_per_subgoal,
            max_methods=config.budgets.max_methods,
            **kwargs,
        )

    # ---------------------------------------------------------------- llm
    def _chat(self, messages, usage: Usage, ctx: "RunContext", tools=None, max_tokens=1500):
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
                    # mutation needs to be serialized. Per-run lock (see
                    # RunContext) -- two concurrent runs no longer
                    # contend on each other's usage bookkeeping at all.
                    with ctx.usage_lock:
                        usage.add(r.usage)
                return r
            except Exception as exc:  # noqa: BLE001
                last = exc
                # Classification and backoff live in agent.py's
                # is_transient/backoff_seconds -- this method used to carry
                # its own inline copy, and the two copies drifted: agent.py's
                # got fixed for "Request timed out." (APITimeoutError's
                # actual message; "timeout" is not a substring of "timed
                # out") and this one didn't, so a Stage 5 sweep died on
                # every htn_memory arm at "tok=0 tools=0" while the flat
                # agent, calling the fixed copy, completed the SAME instance
                # normally. One shared function is what makes that
                # unrepeatable.
                if not is_transient(exc) or attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(backoff_seconds(exc, attempt))
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
                        requires = [int(d) for d in (item.get("requires") or [])]
                    else:
                        goal, nid, deps, requires = str(item).strip(), i, [], []
                    if len(goal) > 10:
                        nodes.append(Node(id=nid, goal=goal, deps=deps, requires=requires))
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
            n.requires = [d for d in n.requires if d in ids and d != n.id]
        # Break cycles by keeping only edges that point backwards in the
        # planner's own ordering. A cycle is unschedulable, and refusing the
        # whole plan over one bad edge would throw away a usable decomposition.
        order = {n.id: i for i, n in enumerate(nodes)}
        for n in nodes:
            n.deps = [d for d in n.deps if order[d] < order[n.id]]
            # requires must be a SUBSET of deps -- a hard dependency that
            # is not even an ordering edge is a contradiction, and trusting
            # it anyway would let a stray requires id block scheduling
            # without deps ever having established the ordering that makes
            # blocking meaningful.
            n.requires = [d for d in n.requires if d in n.deps]
        return nodes

    def _seed_plan(self, ctx: "RunContext") -> Optional[list[dict]]:
        """
        A pre-fetched decomposition to use INSTEAD OF calling the planner
        LLM. None (the default, always) means plan fresh every time -- set
        by a caller that has already looked one up, via the plain instance
        attribute `_pending_seed_plan`, before calling `.run()`. See
        ResearchHTNAgent._synthesize_method for the real implementation
        (graph-backed method-library reuse); HTNAgent itself has no
        database dependency and this hook stays unused unless a subclass
        or caller sets that attribute.

        Reads from `ctx.pending_seed_plan`, not `self._pending_seed_plan`
        directly -- run() reads-and-clears the instance attribute into
        the RunContext at the very start of the call (see RunContext's
        docstring for the staleness bug this fixes: the instance
        attribute alone would silently survive into an unrelated later
        run() call that never set one of its own).
        """
        return ctx.pending_seed_plan

    # Identifiers worth searching the repo for: backtick-quoted tokens (SWE-
    # bench Pro's `interface` field names exact symbols this way) and bare
    # snake_case/CamelCase/dotted identifiers of at least 4 chars -- short
    # enough to catch real names, long enough that "a", "id", "is" do not
    # turn into a repo-wide fishing expedition.
    _IDENTIFIER_RE = re.compile(
        r"`([^`]{3,80})`|\b([a-zA-Z_][a-zA-Z0-9_]{3,40}(?:\.[a-zA-Z_][a-zA-Z0-9_]{2,40})*)\b")
    MAX_CANDIDATE_SEARCHES = 12
    MAX_CANDIDATE_FILES = 8

    def _candidate_files(self, instance: dict, sandbox: RepoSandbox) -> list[str]:
        """
        Verified repo paths to hand the planner, so it plans against files
        that actually exist instead of an imagined layout.

        THE GAP THIS CLOSES: the planner never sees the repository -- only
        the issue text -- yet PLANNER_SYSTEM demands each subgoal "name the
        file or symbol". Measured across every run with plan data: only
        ~47% of planned paths matched a gold file, and the miss is not
        random -- the deepseek ansible run planned edits to
        `galaxy/collection/__init__.py` and `galaxy/dataclasses.py`, right
        basename conventions, wrong directory; the REAL files were
        `galaxy/dependency_resolution/dataclasses.py` and
        `utils/collection_loader/_collection_finder.py`. path_hint cannot
        catch this: those planned paths exist, just not for this fix.

        Zero extra LLM calls -- RepoSandbox.search is local os.walk+regex,
        already capped by MAX_SEARCH_HITS/MAX_SEARCH_FILES -- so this serves
        the token thesis rather than costing against it.
        """
        text = (f"{instance.get('problem_statement', '')} "
                f"{_spec_field(instance, 'requirements')} "
                f"{_spec_field(instance, 'interface')}")
        seen: set[str] = set()
        idents: list[str] = []
        for m in self._IDENTIFIER_RE.finditer(text):
            ident = m.group(1) or m.group(2)
            if ident and ident.lower() not in seen:
                seen.add(ident.lower())
                idents.append(ident)
        counts: dict[str, int] = {}
        for ident in idents[:self.MAX_CANDIDATE_SEARCHES]:
            hits = sandbox.search(re.escape(ident))
            if hits in ("no matches", "") or hits.startswith("bad regex"):
                continue
            for line in hits.splitlines():
                path = line.split(":", 1)[0]
                if path and not path.startswith("..."):
                    counts[path] = counts.get(path, 0) + 1
        ranked = sorted(counts, key=counts.get, reverse=True)
        return ranked[:self.MAX_CANDIDATE_FILES]

    def _decompose(self, instance: dict, memory_block: str, usage: Usage,
                   trace: dict, ctx: "RunContext", sandbox: Optional[RepoSandbox] = None) -> list[Node]:
        seed = self._seed_plan(ctx)
        if seed:
            nodes = self.parse_dag(json.dumps(seed))
            if nodes:
                # Reused through the SAME validation a freshly-planned DAG
                # gets (cycle-breaking, dangling-dep removal, MAX_SUBGOALS
                # cap) -- a stored plan earns no less scrutiny than a new one.
                trace["seeded_from_library"] = True
                return nodes
        # `sandbox` is Optional only so _decompose keeps working for any
        # caller/test that predates the localization pre-pass; every real
        # run() call site has a sandbox by construction.
        candidates = self._candidate_files(instance, sandbox) if sandbox else []
        # Recorded so the gold-file-omission fork (does the pre-pass miss
        # the file, or does the planner ignore a file it WAS shown?) is
        # answerable from data instead of guessed -- this was previously
        # discarded the moment candidate_block was built.
        trace["candidate_files"] = candidates
        candidate_block = (
            "\n\nCANDIDATE FILES (verified to exist in this checkout, ranked "
            "by relevance -- prefer one of these; if the fix genuinely needs "
            "a different file, say so explicitly):\n" + "\n".join(candidates)
        ) if candidates else ""
        # The spec goes to the PLANNER, not just the executor: it is what
        # turns "deduplicate the entries" into a subgoal naming the actual
        # delimiter, ordering and fields the tests check. A plan written
        # without it is under-specified before any executor runs.
        msgs = [{"role": "system", "content": PLANNER_SYSTEM.format(
            repo=instance["repo"], max_subgoals=MAX_SUBGOALS)},
            {"role": "user", "content":
                f"{instance['problem_statement']}"
                f"{spec_block(instance)}{candidate_block}\n{memory_block}"}]
        trace["planner_calls"] += 1
        nodes = self.parse_dag(
            (self._chat(msgs, usage, ctx).choices[0].message.content or ""))
        if not nodes:
            trace["decompose_failed"] = True
            nodes = [Node(id=1, goal=f"Fix the issue as described: "
                                     f"{str(instance['problem_statement'])[:400]}")]
        return nodes

    def _replan(self, instance: dict, node: Node, reason: str,
                usage: Usage, trace: dict, ctx: "RunContext") -> Optional[str]:
        msgs = [{"role": "system", "content": REPLAN_SYSTEM.format(
            repo=instance["repo"], subgoal=node.goal,
            reason=self._replan_evidence(node, reason)[:400])},
            {"role": "user", "content": str(instance["problem_statement"])[:1200]}]
        trace["planner_calls"] += 1
        trace["replans"] += 1
        alt = [a.strip() for a in
               ((self._chat(msgs, usage, ctx, max_tokens=300).choices[0].message.content
                 or "").strip().splitlines()) if len(a.strip()) > 15]
        return alt[0] if alt else None

    # ------------------------------------------------------- extension points
    # Every method below is a no-op / pass-through in HTNAgent -- calling it
    # reproduces today's behaviour exactly. They exist so AugmentedHTNAgent
    # (below) can add real checks without duplicating _run_node's loop.
    @staticmethod
    def _transitive_deps(node: Node, nodes: list[Node]) -> set[int]:
        """Every node id `node` depends on, directly or indirectly (via
        `deps`, the ordering edge -- a superset of `requires`). Shared by
        `_build_context` (both the base plan-listing scope below and
        AugmentedHTNAgent's `done`-block scope) so the two stay consistent
        rather than each walking the graph its own slightly different way."""
        by_id = {n.id: n for n in nodes}
        relevant: set[int] = set()
        frontier = list(node.deps)
        while frontier:
            d = frontier.pop()
            if d in relevant or d not in by_id:
                continue
            relevant.add(d)
            frontier.extend(by_id[d].deps)
        return relevant

    def _build_context(self, node: Node, nodes: list[Node]) -> tuple[str, str]:
        """(done, plan) blocks for the executor prompt.

        `plan` lists every OTHER node's goal/status -- useful orientation
        when the graph is small, but at PLAN_CONTEXT_MAX_NODES+ nodes,
        listing all of them in every single node's prompt reproduces the
        flat agent's "resend everything" cost at the plan-graph level:
        per-node prompt size grows with TOTAL node count, not with what
        that node actually needs. Past the threshold, only this node's own
        transitive dependencies (plus itself) are listed in full; the rest
        collapse into one count line -- deliberately not filtered to zero,
        since a bare "N other subgoals exist elsewhere" is still useful
        orientation without paying per-node cost for it.
        """
        done = "\n".join(f"  - [{n.id}] {n.goal[:80]} -> {n.note[:SUBGOAL_NOTE_CONTEXT_CHARS]}"
                         for n in nodes if n.status == "done") or "  (nothing yet)"
        if len(nodes) <= PLAN_CONTEXT_MAX_NODES:
            plan_nodes, omitted = nodes, 0
        else:
            relevant = self._transitive_deps(node, nodes) | {node.id}
            plan_nodes = [n for n in nodes if n.id in relevant]
            omitted = len(nodes) - len(plan_nodes)
        plan = "\n".join(
            f"  [{n.id}] ({n.status}){' deps=' + str(n.deps) if n.deps else ''} "
            f"{n.goal[:100]}" for n in plan_nodes)
        if omitted:
            plan += (f"\n  ... {omitted} other subgoal(s) elsewhere in the plan, "
                     f"not directly relevant to this one")
        # A deps-only (soft) predecessor's failure no longer transitively
        # blocks this node (see _block_dependents) -- it gets to run, but
        # needs to know that predecessor's edits are NOT on disk, in case
        # its own goal assumed they were. A `requires`-listed predecessor's
        # failure never reaches here: that blocks this node before it is
        # ever scheduled, via the strict _satisfied/_dep_met check.
        failed_soft = [n2 for n2 in nodes if n2.id in node.deps
                       and n2.id not in node.requires and n2.status == "failed"]
        if failed_soft:
            warn = "\n".join(
                f"  NOTE: subgoal [{n2.id}] ({n2.goal[:80]}) did NOT complete "
                f"({n2.note[:120]}) -- its changes are NOT on disk. You may "
                f"need to do that work yourself if this subgoal depends on it."
                for n2 in failed_soft)
            plan = f"{plan}\n{warn}"
        return done, plan

    def _tools_for(self, node: Node, ctx: "RunContext") -> list[dict]:
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

    @staticmethod
    def _fingerprint(sandbox: RepoSandbox, path: str) -> tuple[str, Optional[bytes]]:
        """Repo-relative path plus the target file's current bytes (None if
        absent), for an exact before/after edit comparison. Never raises --
        a path-escape rejection or any other read failure must not be able
        to fail the turn it is only instrumenting; on any error this
        returns ("", None), which the caller's `if rel and ...` guard
        treats as "nothing to attribute"."""
        if not path:
            return "", None
        try:
            full = sandbox._resolve(path)
            rel = os.path.relpath(full, sandbox.root).replace("\\", "/")
            if os.path.isfile(full):
                with open(full, "rb") as f:
                    return rel, f.read()
            return rel, None
        except Exception:  # noqa: BLE001
            return "", None

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
                  tool_log: list[str], ctx: "RunContext") -> tuple[str, object, int]:
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
                subgoal=node.goal, spec_block=spec_block(instance),
                steps=budget) + self._system_prompt_extra(node)},
            {"role": "user", "content": "Begin this subgoal."},
        ]
        steps = 0
        while steps < budget:
            tools = self._tools_for(node, ctx)
            resp = self._chat(messages, usage, ctx, tools=tools, max_tokens=2000)
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
                node.tool_calls.append(name)
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
                    with ctx.sandbox_lock:
                        # Exact attribution, taken INSIDE the lock that
                        # already serialises every mutation -- no race
                        # window, unlike a snapshot around an unlocked LLM
                        # call. Byte-compare rather than a
                        # sandbox._original delta: _original.setdefault
                        # (agent.py) records only the FIRST node ever to
                        # touch a file, so a delta would silently miss the
                        # second node to edit the same file -- precisely
                        # the HTN conflict case worth knowing about.
                        # _fingerprint never raises (instrumentation must
                        # never be able to fail a turn -- same discipline
                        # as the failure_capture call site in
                        # run_graph_experiment.py).
                        rel, before = self._fingerprint(sandbox, args.get("path", ""))
                        result, _ = Agent._dispatch(name, args, sandbox)
                        _, after = self._fingerprint(sandbox, args.get("path", ""))
                        if rel and after != before and rel not in node.files_edited:
                            node.files_edited.append(rel)
                else:
                    result, _ = Agent._dispatch(name, args, sandbox)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": result[:MAX_TOOL_CHARS]
                                 + self._budget_note(steps, budget, node, sandbox)})
        node.last_evidence = self._last_tool_result(messages)
        return False, f"exhausted its {budget}-call budget", steps

    @staticmethod
    def _satisfied(nodes: list[Node], nid: int) -> bool:
        """A HARD dependency (also listed in the dependent's `requires`) is
        met only if the work actually happened.

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
    def _future_competitor(nodes: list[Node], m: "Node") -> bool:
        """Whether `m` is a genuine future draw on the step budget even
        while it isn't ready yet -- the divisor `_schedule` uses to give a
        currently-running node a fair (not starved, not unopposed) share.

        A node blocked only by unmet SOFT (`deps`) predecessors becomes
        ready as soon as those predecessors settle, WIN OR LOSE (see
        `_dep_met`'s docstring) -- it is a guaranteed future competitor, so
        it must count even before it's ready; excluding it left a
        deps-only downstream node starved of any budget once its failing
        predecessor finally exhausted every attempt (regression case:
        `test_htn_node_telemetry.py`'s 3-node chain, node 2 stuck at
        attempts==0 because node 1 alone, uncontested, was free to spend
        the whole run on itself).

        A node blocked by an unmet HARD (`requires`) predecessor that has
        NOT YET succeeded is different: it can only ever run if that
        predecessor succeeds, an outcome that is still unknown. Counting
        it reserves real budget for a node that may never get to spend it
        -- measured live on ansible-f327e65d/gravitational-teleport/
        tutao-tutanota, where node 1's `requires`-blocked dependents were
        counted as competitors throughout node 1's own retries, throttling
        every attempt down to 3-4 steps and burning all of MAX_METHODS+1
        on starvation rather than a real shot at finishing. Once that
        predecessor actually settles, this resolves itself either way: on
        success the dependent becomes `_dep_met` and joins the divisor
        honestly; on failure `_block_dependents` marks it `blocked`,
        dropping it out of consideration entirely."""
        return all(HTNAgent._satisfied(nodes, d) for d in m.requires)

    @staticmethod
    def _dep_met(nodes: list[Node], dependent: Node, dep_id: int) -> bool:
        """Whether ONE of `dependent`'s deps is met, given the soft/hard
        split (see Node.requires's docstring).

        A HARD dep (dep_id in dependent.requires) uses the strict
        _satisfied check above -- it must have genuinely landed.

        A SOFT dep (ordering only) is met once its predecessor reaches ANY
        terminal status. `pending` is deliberately excluded: a soft
        successor still waits its turn rather than racing a predecessor
        that has not been attempted yet or is still mid-retry, so `deps`
        keeps meaning ordering even though it no longer means blocking on
        failure. This is what lets a `deps`-only predecessor's failure
        stop cascading -- _block_dependents no longer marks this node
        `blocked` for it, so once the predecessor settles (however it
        settles), this node becomes schedulable."""
        if dep_id in dependent.requires:
            return HTNAgent._satisfied(nodes, dep_id)
        dep = next((x for x in nodes if x.id == dep_id), None)
        return dep is None or dep.status != "pending"

    @staticmethod
    def _block_dependents(nodes: list[Node], failed_id: int) -> int:
        """Mark the transitive HARD dependents of a failed node as blocked.

        Cascades along `requires` only, not `deps`. Containment is the
        point of the DAG: a node that never REQUIRED the failure keeps its
        turn -- running one whose prerequisite's edits do not exist would
        corrupt the patch, but most `deps` are the planner's honest
        ordering, not a real data dependency, and a plan is nearly always a
        linear chain (measured: 74-83% of real plans), so treating every
        `deps` edge as a hard blocker meant a single root-node failure
        produced subgoals_done == 0 in 12 of 12 observed cases -- the DAG's
        whole containment story never engaged because there was nothing
        left un-blocked to contain. A `deps`-only dependent still runs; see
        _satisfied's soft-vs-hard split, which is what makes this safe --
        it becomes SCHEDULABLE once its failed predecessor settles, but is
        told via its note that the predecessor did not land, in case it
        needs to redo that work itself."""
        blocked, changed = {failed_id}, True
        while changed:
            changed = False
            for n in nodes:
                if n.status == "pending" and any(d in blocked for d in n.requires):
                    n.status, n.note = "blocked", f"prerequisite {n.requires} did not complete"
                    blocked.add(n.id)
                    changed = True
        return sum(1 for n in nodes if n.status == "blocked")

    @staticmethod
    def _rehydrate(state: list[dict]) -> list[Node]:
        """
        Reconstruct a Node graph from an EARLIER run's `run.htn["nodes"]`
        snapshot -- same shape that dict already has (id/goal/deps/requires/
        status/attempts/note/last_evidence/path_hint/depth/parent), so no
        separate resume format exists to keep in sync. The topological loop
        in `run()` only ever picks a node
        with status=='pending'; a done/failed/blocked/expanded node from the
        earlier session is left exactly as it was and simply never
        reconsidered, so resuming falls out of the scheduler's ordinary logic
        for free -- there is no separate resume-specific code path to trust.

        HTNAgent has no filesystem or session persistence of its own: the
        CALLER is responsible for the sandbox already reflecting whatever the
        done nodes did (e.g. re-apply the earlier run's `AgentRun.patch` to a
        fresh checkout, or keep working in the same checkout across the
        interruption) before passing this run's snapshot back in.

        Every instrumentation field below uses `.get(key, default)`, not a
        bare index -- both because a snapshot from BEFORE instrumentation
        existed (any of the ~20 result files already on disk) has none of
        these keys, and because a resumed run must not silently report
        zeros for a previously-done node's real cost while `attempts`
        round-trips correctly; `.get` with the dataclass's own zero-value
        defaults keeps both cases from raising.
        """
        return [Node(id=int(n["id"]), goal=str(n["goal"]),
                     deps=[int(d) for d in (n.get("deps") or [])],
                     requires=[int(d) for d in (n.get("requires") or [])],
                     status=n.get("status", "pending"),
                     attempts=int(n.get("attempts", 0)),
                     note=str(n.get("note", "")),
                     last_evidence=str(n.get("last_evidence", "")),
                     path_hint=str(n.get("path_hint", "")),
                     depth=int(n.get("depth", 0)),
                     parent=n.get("parent"),
                     steps_used=int(n.get("steps_used", 0)),
                     budget_granted=int(n.get("budget_granted", 0)),
                     rounds=int(n.get("rounds", 0)),
                     llm_calls=int(n.get("llm_calls", 0)),
                     prompt_tokens=int(n.get("prompt_tokens", 0)),
                     completion_tokens=int(n.get("completion_tokens", 0)),
                     wall_seconds=float(n.get("wall_seconds", 0.0)),
                     started_at=n.get("started_at"),
                     ended_at=n.get("ended_at"),
                     tool_calls=list(n.get("tool_calls") or []),
                     files_edited=list(n.get("files_edited") or [])) for n in state]

    def _run_turn(self, instance: dict, sandbox: RepoSandbox, ready: Node,
                  nodes: list[Node], usage: Usage, tool_log: list[str],
                  trace: dict, ceiling: int, ctx: "RunContext") -> int:
        """
        One node's full turn: attempt, and on failure replan and retry, up
        to max_methods, never spending more than `ceiling` tool calls in
        total for this node. Mutates `ready` in place (status/note/attempts/
        goal) and, on expansion, appends children to `nodes` under
        `ctx.nodes_lock` -- always taken, even in HTNAgent's own
        single-threaded caller below, so this method needs no separate
        parallel-only version: AugmentedHTNAgent's concurrent scheduler
        calls this exact same method from multiple threads at once.

        If `ceiling` runs out before the node reaches a terminal status,
        `ready.status` is left "pending" -- the caller reads that back to
        know this node did not finish, distinct from an explicit failure.

        Returns steps actually used, so callers sharing a `_Budget` across
        several concurrent turns can `.release()` whatever went unspent.

        Rebinds `usage` to a `_NodeUsage` wrapping the real, shared `usage`
        -- see that class's docstring. This means `_run_node` and
        `_replan` below (both unmodified) automatically charge THIS node's
        token/call counters through the same write-through, so a replan's
        cost is attributed to the node that needed it.
        """
        usage = _NodeUsage(usage, ready)
        t_turn = time.time()
        try:
            spent_here = 0
            while ready.attempts <= self._max_methods:
                budget = min(self._per_subgoal, ceiling - spent_here)
                if budget < MIN_VIABLE_SUBGOAL_BUDGET:
                    # Too few calls to reach ANY terminal tool call, so this
                    # round cannot produce information. Leave the node
                    # "pending" and, crucially, do NOT charge it an attempt --
                    # see MIN_VIABLE_SUBGOAL_BUDGET. Callers must treat a turn
                    # that returns 0 steps as no-progress and stop, or this
                    # becomes an infinite loop: with used == 0 the scheduler
                    # releases the entire reservation, so _Budget.remaining()
                    # never falls and the round would repeat forever.
                    # AugmentedHTNAgent._schedule has that guard; HTNAgent's
                    # own loop already returns "step_budget" on a pending node.
                    break
                ready.attempts += 1
                outcome, payload, used = self._run_node(
                    instance, sandbox, ready, nodes, budget, usage, tool_log, ctx)
                spent_here += used
                if outcome == "expand":
                    # RECURSION: this compound task is replaced by children that
                    # must all finish before anything depending on it may run.
                    # The parent keeps its own deps; children with no siblings
                    # named inherit them, so the graph stays connected rather
                    # than the subtree floating free.
                    with ctx.nodes_lock:
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
                    ready.tool_calls.append("__expand__")
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
                    # only out of THIS round. Leave status "pending" so the
                    # scheduler grants a fresh reservation next round.
                    #
                    # REPLAN FIRST, though. An earlier version of this branch
                    # just broke, which meant the next round re-ran the SAME
                    # goal -- and a goal that could not be finished in `budget`
                    # calls is not going to be finished by the identical
                    # `budget` calls again. Measured on ansible-f327e65d: three
                    # identical 9-call rounds spent 27 tool calls, completed no
                    # subgoal, and left four half-applied edits that broke 25
                    # previously-passing tests, with replans: 0 the whole time.
                    # The replan machinery exists precisely to supply a
                    # DIFFERENT approach; it was simply never reachable from
                    # here.
                    alt = self._replan(instance, ready, str(payload), usage, trace, ctx)
                    if not alt:
                        # No alternative approach available -- retrying the same
                        # goal would repeat the round that just failed.
                        ready.status = "failed"
                    else:
                        tool_log.append("__replan__")
                        ready.tool_calls.append("__replan__")
                        ready.goal = alt
                    break
                alt = self._replan(instance, ready, str(payload), usage, trace, ctx)
                if not alt:
                    ready.status = "failed"
                    break
                tool_log.append("__replan__")
                ready.tool_calls.append("__replan__")
                ready.goal = alt
            if ready.status == "failed":
                with ctx.nodes_lock:
                    self._block_dependents(nodes, ready.id)
            return spent_here
        finally:
            # try/finally, not a trailing block: a raising turn (e.g. _chat
            # exhausts retries and raises) still reports what it spent --
            # otherwise the api_error rows, the ones most worth diagnosing,
            # are exactly the ones with no node-level data.
            ready.steps_used += spent_here
            ready.wall_seconds += time.time() - t_turn
            if ready.started_at is None:
                ready.started_at = t_turn
            ready.ended_at = time.time()

    def _schedule(self, instance: dict, sandbox: RepoSandbox, nodes: list[Node],
                 usage: Usage, tool_log: list[str], trace: dict, ctx: "RunContext") -> str:
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
        # Cap what any single node can draw to its own worst-case allotment
        # (one attempt per method, per_subgoal steps each) rather than
        # granting `budget.remaining()` outright -- the latter let whichever
        # node happened to be ready first consume the ENTIRE run budget
        # across all of ITS OWN retries before a later, dependent node was
        # even looked at (the more extreme form of the bug fixed in
        # AugmentedHTNAgent._schedule, which reserves per-round instead).
        node_cap = self._per_subgoal * (self._max_methods + 1)
        while True:
            ready_all = [n for n in nodes if n.status == "pending"
                        and all(self._dep_met(nodes, n, d) for d in n.deps)]
            ready = ready_all[0] if ready_all else None
            if ready is None:
                return "finished"
            # FAIR SHARE -- see AugmentedHTNAgent._schedule's longer comment
            # for the measured failure this answers and why a fixed
            # protective floor (this scheduler's earlier version) doesn't
            # generalize to many never-run nodes: dividing whatever budget
            # remains by how many nodes still need a first look self-
            # corrects as nodes finish, where a fixed floor sized for one
            # topology does not. `max(1, ...)` guarantees a non-zero ask
            # while real budget remains; a too-small resulting grant is
            # left to `_run_turn`'s own MIN_VIABLE_SUBGOAL_BUDGET decline.
            #
            # Filtered by _future_competitor, NOT plain status=="pending"
            # -- see its docstring and AugmentedHTNAgent._schedule's
            # matching comment: a node blocked on an unresolved HARD
            # requires cannot possibly compete this round or any round
            # before its predecessor settles, and counting it anyway
            # starves the node that IS running for a phantom competitor.
            never_run = [n for n in nodes if n.attempts == 0 and n.status == "pending"
                        and self._future_competitor(nodes, n)]
            # A first attempt is already counted in never_run (it's pending
            # with attempts==0, so it's a member of its own list). A retry
            # is NOT in never_run (attempts>=1), so it needs its own "+1"
            # slot IN the divisor -- but only when never_run is non-empty:
            # a solo retry with nobody else to protect must get the full
            # per-node cap, not be halved against a phantom competitor.
            d = max(1, len(never_run) + (1 if ready.attempts >= 1 else 0))
            want = min(node_cap, max(1, budget.remaining() // d))
            ceiling = budget.reserve(want)
            if ceiling <= 0:
                return "step_budget"
            ready.budget_granted += ceiling
            ready.rounds += 1
            used = self._run_turn(instance, sandbox, ready, nodes, usage,
                                  tool_log, trace, ceiling, ctx)
            budget.release(ceiling - used)
            if ready.status == "pending":
                return "step_budget"

    def run(self, instance: dict, sandbox: RepoSandbox, arm: str,
            memory_block: str = "", retrieved: Optional[list[str]] = None,
            resume_state: Optional[list[dict]] = None) -> AgentRun:
        t0 = time.time()
        usage = Usage()
        # RunContext extraction (ticket 15): read-and-clear
        # self._pending_seed_plan HERE, atomically, before any other
        # per-run state exists -- this is what fixes the staleness bug
        # (see RunContext's docstring): a seed plan set before THIS call
        # can never leak into a later, unrelated run() call that never
        # set one of its own, because the instance attribute is cleared
        # the moment this call reads it, not left for the next call to
        # find.
        pending_seed_plan, self._pending_seed_plan = self._pending_seed_plan, None
        ctx = RunContext(t0=t0, usage=usage, pending_seed_plan=pending_seed_plan)
        # Still set for AugmentedHTNAgent's SLA gate and any external
        # introspection that predates this extraction -- but no longer
        # the SOURCE of truth _schedule/_run_turn/_chat/_run_node read
        # from; ctx is. Kept as a plain, best-effort mirror, not a
        # second copy anything below relies on.
        self._t0, self._run_usage = t0, usage
        tool_log: list[str] = []
        trace = {"planner_calls": 0, "replans": 0, "decompose_failed": False,
                 "seeded_from_library": False, "resumed": bool(resume_state),
                 "candidate_files": []}
        error, stop_reason = None, "finished"

        try:
            # Steps spent inside `_schedule` below are THIS call's own step
            # budget, separate from whatever an earlier, interrupted call
            # already used -- a caller tracking cumulative cost across
            # resumes adds the two AgentRun.usage/steps together itself;
            # HTNAgent has no memory of a prior call to add them to.
            nodes = (self._rehydrate(resume_state) if resume_state
                     else self._decompose(instance, memory_block, usage, trace, ctx, sandbox))
        except Exception as exc:  # noqa: BLE001
            return AgentRun(instance_id=instance["instance_id"], arm=arm,
                            patch=sandbox.diff(), usage=usage, steps=usage.calls,
                            tool_calls=tool_log, files_edited=sandbox.edited_files(),
                            stop_reason="api_error", wall_seconds=time.time() - t0,
                            retrieved=retrieved or [],
                            error=f"{type(exc).__name__}: {exc}")

        plan_snapshot = [{"id": n.id, "goal": n.goal, "deps": n.deps,
                          "requires": n.requires} for n in nodes]
        try:
            stop_reason = self._schedule(instance, sandbox, nodes, usage, tool_log, trace, ctx)
        except Exception as exc:  # noqa: BLE001
            error, stop_reason = f"{type(exc).__name__}: {exc}", "api_error"

        subgoals_done = sum(1 for n in nodes if n.status == "done")
        patch = sandbox.diff()
        discarded_patch_bytes = 0
        # No subgoal ever reached "done" -- whatever edits are on disk are
        # necessarily partial (a node mid-edit that then failed, or one
        # still "pending"/"blocked"). Before this guard those edits still
        # shipped as the patch: on ansible-f327e65d a node that removed two
        # functions per `requirements` but never got to add their
        # replacement broke 25 previously-passing tests (p2p_broke: 25) --
        # additive-but-incomplete work is inert, but the spec block can
        # make incomplete work destructive. An empty patch scores the same
        # as `no_patch` on the benchmark either way, so this trades a
        # (already-losing) attempt for the never-breaks-working-code
        # property that held across all 19 baseline runs. error/api_error
        # runs are left alone -- that is a different, already-diagnosable
        # failure mode, not a plan that silently ran out of subgoals.
        if subgoals_done == 0 and stop_reason != "api_error" and patch:
            discarded_patch_bytes = len(patch)
            patch = ""
            stop_reason = "discarded_incomplete_plan"

        run = AgentRun(
            instance_id=instance["instance_id"], arm=arm, patch=patch,
            usage=usage, steps=usage.calls, tool_calls=tool_log,
            files_edited=sandbox.edited_files(), stop_reason=stop_reason,
            wall_seconds=time.time() - t0, retrieved=retrieved or [], error=error)
        run.htn = {  # type: ignore[attr-defined]
            "plan": plan_snapshot,
            "nodes": [_node_row(n) for n in nodes],
            "replans": trace["replans"], "planner_calls": trace["planner_calls"],
            "decompose_failed": trace["decompose_failed"],
            "seeded_from_library": trace["seeded_from_library"],
            "resumed": trace["resumed"],
            "candidate_files": trace["candidate_files"],
            "subgoals_done": subgoals_done,
            "subgoals_failed": sum(1 for n in nodes if n.status == "failed"),
            "subgoals_blocked": sum(1 for n in nodes if n.status == "blocked"),
            "subgoals_expanded": sum(1 for n in nodes if n.status == "expanded"),
            "max_depth_reached": max((n.depth for n in nodes), default=0),
            "nodes_total": len(nodes),
            "edges": sum(len(n.deps) for n in nodes),
            "discarded_patch_bytes": discarded_patch_bytes,
            # Per-node roll-ups. "starved" here means the SCHEDULER-level
            # signal (never attempted at all) -- run_graph_experiment.py's
            # node_metrics() splits this further into budget-starved vs
            # dependency-blocked, which needs `status` too and so cannot
            # be computed from this count alone.
            "nodes_never_ran": sum(1 for n in nodes if n.attempts == 0),
            "nodes_unbudgeted": sum(1 for n in nodes if n.budget_granted == 0),
            "node_steps_total": sum(n.steps_used for n in nodes),
            "node_tokens_total": sum(n.prompt_tokens + n.completion_tokens for n in nodes),
            "node_llm_calls_total": sum(n.llm_calls for n in nodes),
            "node_wall_total": round(sum(n.wall_seconds for n in nodes), 3),
            # usage.total includes planner/replan calls, which are charged
            # to the run-global Usage directly in _decompose (never
            # rebound to a node) -- so this is exactly the planning
            # overhead a node-level view cannot see.
            "overhead_tokens": usage.total - sum(
                n.prompt_tokens + n.completion_tokens for n in nodes),
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
    # Ceiling on the basename index walk, mirroring call_graph's own
    # MAX_INDEX_FILES. Truncation only weakens a hint; it never fails a run.
    MAX_INDEXED_FILES = 4000
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
    def _shallow(self, ctx: "RunContext") -> bool:
        """
        Reads ctx.t0/ctx.usage -- NOT self._t0/self._run_usage. Those
        instance attributes are still set by run() as a best-effort
        mirror for external introspection, but using them HERE would
        silently reintroduce exactly the cross-run contamination
        RunContext exists to prevent: two concurrent .run() calls on
        this same agent instance would each see whichever call's mirror
        happened to be written most recently, not their own real t0/
        usage. ctx is per-call and cannot be stomped by a sibling run.
        """
        if self._max_wall_seconds and time.time() - ctx.t0 > 0.7 * self._max_wall_seconds:
            return True
        if self._max_token_budget and ctx.usage.total > 0.7 * self._max_token_budget:
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

    def _tools_for(self, node: Node, ctx: "RunContext") -> list[dict]:
        base = super()._tools_for(node, ctx)
        if self._shallow(ctx):
            base = [t for t in base if t['function']['name'] != 'decompose_subgoal']
        allowed = self.PERSONAS[self._persona(node.goal)]["tools"] | {
            "subgoal_done", "subgoal_failed", "decompose_subgoal"}
        return [t for t in base if t['function']['name'] in allowed]

    def _system_prompt_extra(self, node: Node) -> str:
        extra = self.PERSONAS[self._persona(node.goal)]["prompt"]
        if node.path_hint:
            extra = f"{extra}\n{node.path_hint}"
        return extra

    def _basename_index(self, sandbox: RepoSandbox) -> dict[str, list[str]]:
        """basename -> [relative path, ...] over the whole checkout, cached.

        Same lazy per-sandbox shape TypedPreconditionHTNAgent._get_index
        uses, and it walks with code_index.SKIP_DIRS so it sees exactly the
        tree `search`/`list_dir` do -- a hint pointing at a file the agent's
        own tools cannot reach would be worse than no hint. Bounded like
        call_graph's index so a huge checkout cannot eat the per-instance
        budget; a truncated index only weakens hints, never breaks a run.

        REAL BUG FIXED as part of the RunContext extraction (found while
        auditing every self.-scoped attribute, not called out by ticket
        15's own text): this used to be a single `self._bn_index = (root,
        index)` tuple, keyed by whichever sandbox last populated it. Two
        concurrent .run() calls against DIFFERENT sandboxes on the same
        agent instance would race on that single slot -- one run's cache
        entry silently overwriting another's, so a run could get served
        the WRONG sandbox's basename index (pointing a path hint at a
        file that exists in a different checkout entirely). Now a real
        dict keyed by sandbox.root, so concurrent runs against different
        sandboxes each get their own cache entry and never collide.
        Populated once per distinct root; a redundant rebuild under a
        genuine race (two runs against the SAME new root at once) is
        wasted work, not corruption -- the dict assignment itself is
        atomic under the GIL, and both racing builds compute the same
        answer from the same filesystem.
        """
        cache = self.__dict__.setdefault("_bn_index_cache", {})
        cached = cache.get(sandbox.root)
        if cached is not None:
            return cached
        index: dict[str, list[str]] = {}
        seen = 0
        for dirpath, dirnames, filenames in os.walk(sandbox.root):
            dirnames[:] = [d for d in dirnames if d not in code_index.SKIP_DIRS]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), sandbox.root)
                index.setdefault(fn, []).append(rel.replace("\\", "/"))
                seen += 1
                if seen >= self.MAX_INDEXED_FILES:
                    break
            if seen >= self.MAX_INDEXED_FILES:
                break
        cache[sandbox.root] = index
        return index

    # -- 1. static pre/postcondition checks --------------------------------
    def _verify_precondition(self, node: Node, sandbox: RepoSandbox) -> tuple[bool, str]:
        """ADVISORY, never fatal -- and that is a deliberate reversal.

        This used to hard-fail any goal naming a path that does not exist,
        returning zero steps used. That was survivable while the planner
        wrote vague goals ("search collection_loader/ for the validation"),
        but once `requirements` reached the planner it started naming
        specific files -- and requirements give a BARE BASENAME ("...should
        be removed in dataclasses.py"), so the planner supplies a directory
        it has never verified. Measured consequence on ansible-f327e65d: the
        planner guessed lib/ansible/utils/dataclasses.py, the real file was
        lib/ansible/galaxy/dependency_resolution/dataclasses.py, the node
        was killed before a single tool call, all three attempts went on
        replans that guessed further wrong paths, and a previously RESOLVED
        instance became no_patch on 2,234 tokens.

        The file was there the whole time. So resolve by basename and TELL
        the executor, rather than refusing to let it look.
        """
        if any(h in node.goal.lower() for h in self._CREATE_HINTS):
            return True, ""
        hints: list[str] = []
        for m in self._FILE_RE.finditer(node.goal):
            path = m.group(1)
            try:
                full = sandbox._resolve(path)
            except ValueError:
                continue
            if os.path.isfile(full) or os.path.isdir(full):
                continue
            matches = self._basename_index(sandbox).get(os.path.basename(path), [])
            if len(matches) == 1:
                hints.append(f"'{path}' does not exist; the file is at "
                             f"'{matches[0]}' -- use that path.")
            elif matches:
                shown = ", ".join(f"'{p}'" for p in matches[:5])
                hints.append(f"'{path}' does not exist; candidates with that "
                             f"name: {shown}. Confirm which one before editing.")
            else:
                hints.append(f"'{path}' does not exist anywhere in the repo. "
                             f"Locate the right file first, or create it if "
                             f"the subgoal genuinely needs a new one.")
        node.path_hint = (PATH_HINT_MARKER + " " + " ".join(hints)) if hints else ""
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
        relevant = self._transitive_deps(node, nodes)
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
                 usage: Usage, tool_log: list[str], trace: dict, ctx: "RunContext") -> str:
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
                           and all(self._dep_met(nodes, n, d) for d in n.deps)]
            if not ready_batch:
                return "finished"
            # SLA-tight runs fall back to one node at a time -- the same
            # withdrawal `_tools_for` already does for decompose_subgoal,
            # applied here too: when budget is nearly gone, a wide batch's
            # reservations would starve later nodes in the SAME round rather
            # than let them run at full size in the next one.
            width = 1 if self._shallow(ctx) else self.MAX_PARALLEL_NODES
            batch = ready_batch[:width]

            # Reserved ONE AT A TIME, synchronously, before any thread
            # starts -- see _Budget's docstring for why that (not a
            # check-then-spend read inside each thread) is what keeps a
            # concurrent round from overshooting the total step budget.
            #
            # FAIR SHARE, not a fixed floor. The first version of this fix
            # (a fixed MIN_VIABLE_SUBGOAL_BUDGET reserved per never-run
            # node, applied only to a node's SECOND+ attempt) stopped one
            # RETRYING node from starving others -- measured on
            # ansible-f327e65d, node 1's three retries (9+9+9=27 of 28
            # steps) left node 2 a 1-step grant, below
            # MIN_VIABLE_SUBGOAL_BUDGET, declined, run ended with node 2
            # never attempted -- but it left every FIRST attempt
            # unthrottled. That has the same failure shape one level up:
            # with 10 independent leaf nodes at width 4, rounds 1-2 (nodes
            # 1-8, all first attempts) each drew the full per-round cap
            # unthrottled, leaving nothing for nodes 9-10's first attempt
            # in round 3 -- the exact starvation this fix exists to
            # prevent, just via siblings instead of retries.
            #
            # The fix that covers both shapes: divide whatever budget
            # remains by how many nodes still need a first look (a retry
            # counts as needing one MORE slot in that same shared pool,
            # since it's drawing from it too), every round, for every
            # grant -- not a fixed protective amount sized for one
            # scenario. `divisor` self-corrects as nodes finish (fewer
            # nodes left => bigger shares for whoever remains), and
            # `max(1, ...)` guarantees `reserve()` is never asked for 0
            # while real budget remains, so a too-small resulting share is
            # left to `_run_turn`'s own MIN_VIABLE_SUBGOAL_BUDGET decline
            # (a real, already-tested "not viable yet, no attempt charged"
            # path) rather than needing a second, separate escape hatch
            # here. The arithmetic happens before a single `reserve()`
            # call, not as a separate remaining()-then-reserve() pair, so
            # nothing here depends on this reservation loop staying
            # single-threaded (documented as such above, but this makes
            # the safety property survive a future refactor).
            # Filtered by _future_competitor, NOT plain status=="pending"
            # over the whole graph: a node blocked only by unmet SOFT deps
            # is a guaranteed future competitor (it becomes ready once its
            # predecessor settles, win or lose) and must still count, but
            # a node blocked by an unresolved HARD requires cannot
            # possibly compete until that predecessor actually succeeds --
            # counting it anyway starves whoever IS running for a phantom
            # competitor. Measured live on ansible-f327e65d,
            # gravitational-teleport and tutao-tutanota: node 1 alone in
            # ready_batch, divided by 2-3 requires-blocked siblings that
            # could not run until node 1 itself succeeded, so each retry
            # got ~3-4 steps instead of per_node_cap=9 and burned all
            # MAX_METHODS+1 attempts on starvation (19-23 of a 72 step
            # budget) rather than a real shot at finishing. See
            # _future_competitor's docstring for the full split and the
            # regression case (a deps-only chain) it also has to satisfy.
            never_run = [m for m in nodes if m.attempts == 0 and m.status == "pending"
                        and self._future_competitor(nodes, m)]
            reservations: dict[int, int] = {}
            for n in batch:
                if budget.remaining() <= 0:
                    break
                # A first attempt is already counted in never_run (it's
                # pending with attempts==0, a member of its own list). A
                # retry is NOT in never_run, so it needs its own "+1" slot
                # -- but only when never_run is non-empty: a solo retry
                # with nobody else to protect must get the full per-node
                # cap, not be halved against a phantom competitor.
                d = max(1, len(never_run) + (1 if n.attempts >= 1 else 0))
                want = min(per_node_cap, max(1, budget.remaining() // d))
                grant = budget.reserve(want)
                if grant <= 0:
                    break
                reservations[n.id] = grant
                n.budget_granted += grant
                n.rounds += 1
            if not reservations:
                return "step_budget"
            batch = [n for n in batch if n.id in reservations]

            spent_this_round = 0
            if len(batch) == 1:
                # No concurrency to pay thread-pool overhead for.
                n = batch[0]
                used = self._run_turn(instance, sandbox, n, nodes, usage,
                                      tool_log, trace, reservations[n.id], ctx)
                spent_this_round += used
                budget.release(reservations[n.id] - used)
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    futures = {pool.submit(self._run_turn, instance, sandbox, n, nodes,
                                           usage, tool_log, trace, reservations[n.id], ctx): n
                              for n in batch}
                    for fut in concurrent.futures.as_completed(futures):
                        n = futures[fut]
                        used = fut.result()  # re-raises a worker's exception here
                        spent_this_round += used
                        budget.release(reservations[n.id] - used)

            # TERMINATION GUARD, and the reason MIN_VIABLE_SUBGOAL_BUDGET is
            # safe to have at all. A round in which every node declined its
            # reservation as non-viable spends nothing, so `release` returns
            # the whole grant and `remaining()` is unchanged -- the next
            # iteration would reserve the same too-small amount and decline
            # again, forever, without ever calling the model (so no scripted
            # or real response could break the cycle either).
            #
            # But zero STEPS is not the same as zero PROGRESS. A node can
            # reach a terminal status without spending a tool call at all
            # (a failed precondition, an executor that answers with no tool
            # call), and that genuinely advances the graph: it can unblock a
            # replan, or simply leave an INDEPENDENT branch still waiting
            # its turn. Stopping there stranded that branch. Only a round
            # that both spent nothing AND left every node exactly as it
            # found them is truly stuck.
            progressed = spent_this_round > 0 or any(
                n.status != "pending" for n in batch)
            if not progressed:
                return "step_budget"

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
