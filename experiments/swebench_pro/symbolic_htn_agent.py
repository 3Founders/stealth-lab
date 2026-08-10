"""
Two neuro-symbolic HTN agent variants, both built entirely on htn_agent.py's
documented extension points (_verify_precondition, _verify_postcondition,
_system_prompt_extra, _seed_plan/_pending_seed_plan) -- zero edits to
htn_agent.py itself. Both keep HTNAgent.run()'s exact signature and
AgentRun return shape, so pro_harness.py still cannot tell which arm ran.

WRAPPER (NeuroSymbolicWrapperHTNAgent, intended arm name "htn_wrapper"):
composes what already exists -- method-library reuse, the tree-sitter
syntax-error hard gate, the static file-existence precondition, persona-
scoped tools -- under one name. Its only new code is a two-tier lookup
(method library, then the backend's real decomposition pipeline) that can
never do worse than plain htn_memory: on a double miss it falls through to
the unmodified planner LLM exactly as today.

DEEP (TypedPreconditionHTNAgent, intended arm name "htn_typed"): adds a real
mechanism, not just composition -- best-effort static call-graph reachability
(call_graph.py) gives the executor a typed, derived touch-set instead of
relying on the model's own say-so or a bare text-similarity match. Aimed at
Pattern A (GRAPH_EXPERIMENT.md section 8): the largest unfixed failure mode
is an agent editing only some of the files a fix needs, because the missing
one is reachable only by tracing a call the issue text never mentions.

Honesty about the mechanism, stated once here rather than re-litigated at
every call site: call-graph resolution is NAME-BASED, not type-resolved, so
it over-approximates (two unrelated same-named methods collide). That is why
it is advisory by default (injected into the prompt as a hint) and only
becomes a hard gate when `strict_callgraph_gate=True` is explicitly passed,
narrowed further by requiring the issue text to independently corroborate
the missed symbol's name. See callgraph_check.py for measuring this against
real Pattern-A instances before trusting the strict gate at all.
"""
from __future__ import annotations

import threading
from typing import Optional

import call_graph
import decomposition_bridge
from htn_agent import AugmentedHTNAgent, Node, ResearchHTNAgent


class NeuroSymbolicWrapperHTNAgent(ResearchHTNAgent):
    """
    Drop-in: identical constructor and .run() signature to ResearchHTNAgent.
    Intended arm name: "htn_wrapper".

    Adds `_synthesize_plan`, a two-tier lookup driven by the CALLER before
    `.run()`, same two-step pattern ResearchHTNAgent._synthesize_method
    already establishes and for the same reason (HTNAgent.run() is
    synchronous; asyncpg is not):

        agent = NeuroSymbolicWrapperHTNAgent(client, model)
        await agent._synthesize_plan(pool, embedder, sample, decomposer=decomposer)
        run = agent.run(sample, sandbox, "htn_wrapper", memory_block=memory_block)

    Tier 1: method_library.find_reusable_plan (inherited, zero LLM calls on
    a hit). Tier 2: decomposition_bridge.decompose_issue, which drives the
    backend's actual DecompositionService -- decomposition.py, dedup.py,
    subtask_reuse.py, precondition_gate.py, change.py's capability boundary
    -- the "other parts of backend" this arm exists to exercise and test.
    Both miss -> self._pending_seed_plan stays unset -> HTNAgent._decompose's
    unmodified planner LLM runs exactly as it does for plain htn_memory. This
    arm can therefore never plan worse than htn_memory, only cheaper on a hit.
    """

    async def _synthesize_plan(
        self, pool, embedder, sample: dict,
        decomposer: Optional["decomposition_bridge.DecompositionService"] = None,
    ) -> bool:
        if await self._synthesize_method(pool, embedder, sample["problem_statement"]):
            return True
        if decomposer is None:
            return False
        subgoals, _diag = await decomposition_bridge.decompose_issue(
            decomposer, sample, pool=pool,
            held_out_instance_id=sample.get("instance_id"))
        if subgoals:
            self._pending_seed_plan = subgoals
            return True
        return False


class TypedPreconditionHTNAgent(AugmentedHTNAgent):
    """
    Drop-in: identical constructor (plus the two keyword-only options below)
    and .run() signature to AugmentedHTNAgent. Intended arm name: "htn_typed".

    `strict_callgraph_gate` (default False): see module docstring. Off means
    reachability is advisory-only (injected as a prompt hint); on adds a
    narrow hard gate in _verify_postcondition.
    `callgraph_max_hops` (default call_graph.MAX_HOPS): how far the BFS
    walks from a subgoal's named files before stopping.
    """

    def __init__(self, *args, strict_callgraph_gate: bool = False,
                 callgraph_max_hops: int = call_graph.MAX_HOPS, **kwargs):
        super().__init__(*args, **kwargs)
        self._strict_callgraph_gate = strict_callgraph_gate
        self._callgraph_max_hops = callgraph_max_hops
        # sandbox.root -> SymbolIndex. AugmentedHTNAgent's speculative
        # scheduler runs several nodes concurrently against the SAME
        # sandbox, so this is built once per run and shared, not once per
        # node -- see _get_index.
        self._index_cache: dict[str, call_graph.SymbolIndex] = {}
        self._index_lock = threading.Lock()
        self._current_sandbox = None            # set in _verify_precondition
        self._current_problem_text = ""          # set in run(), see below

    def run(self, instance: dict, sandbox, arm: str, memory_block: str = "",
            retrieved: Optional[list[str]] = None,
            resume_state: Optional[list[dict]] = None):
        # The only override of run() itself, and it does nothing but capture
        # the issue text for the strict gate's keyword corroboration (see
        # _verify_postcondition) -- htn_agent.py's hooks never receive
        # `instance`, and this is set once, before any node executes, so
        # concurrent _run_node calls all see the same already-set value.
        self._current_problem_text = str(instance.get("problem_statement", ""))
        return super().run(instance, sandbox, arm, memory_block=memory_block,
                           retrieved=retrieved, resume_state=resume_state)

    def _get_index(self, sandbox) -> call_graph.SymbolIndex:
        with self._index_lock:
            idx = self._index_cache.get(sandbox.root)
            if idx is None:
                idx = call_graph.build_repo_symbol_index(sandbox.root)
                self._index_cache[sandbox.root] = idx
            return idx

    def _reachability_for(self, node: Node, sandbox) -> call_graph.Reachability:
        seeds = call_graph.seed_from_text(node.goal, sandbox.root)
        if not seeds:
            return call_graph.Reachability()
        index = self._get_index(sandbox)
        return call_graph.reachable_symbols(
            seeds, sandbox.root, index, max_hops=self._callgraph_max_hops)

    # -- advisory tier: always on -------------------------------------------
    def _verify_precondition(self, node: Node, sandbox) -> tuple[bool, str]:
        self._current_sandbox = sandbox
        return super()._verify_precondition(node, sandbox)

    def _system_prompt_extra(self, node: Node) -> str:
        base = super()._system_prompt_extra(node)
        sandbox = self._current_sandbox
        if sandbox is None:
            return base
        reach = self._reachability_for(node, sandbox)
        if not reach.symbols:
            return base
        hint = (
            "\n\nStatic call-graph analysis found these related symbols that "
            "may also need a change -- a hint from a name-based, best-effort "
            "scan, not a command; verify before acting on it:\n"
            + "\n".join(f"  - {s}" for s in reach.symbols[:12]))
        return base + hint

    # -- strict tier: opt-in only --------------------------------------------
    def _verify_postcondition(self, node: Node, sandbox) -> tuple[bool, str]:
        ok, why = super()._verify_postcondition(node, sandbox)
        if not ok or not self._strict_callgraph_gate:
            return ok, why
        reach = self._reachability_for(node, sandbox)
        if not reach.trace:
            return ok, why
        edited = set(sandbox.edited_files())
        problem_lower = self._current_problem_text.lower()
        for rel, qualname, _hop in reach.trace:
            if rel in edited:
                continue
            leaf = qualname.rsplit(".", 1)[-1].lower()
            # Corroboration, not just reachability: the issue text must
            # independently name this symbol too, narrowing the class of
            # false positives a purely name-based scan would otherwise
            # produce (two unrelated same-named methods in different files).
            if len(leaf) >= 4 and leaf in problem_lower:
                return False, (
                    f"static call-graph analysis found '{qualname}' in {rel}, "
                    f"reachable from this subgoal and named in the issue text, "
                    f"but {rel} has not been edited -- check whether it also "
                    f"needs a change before marking this done")
        return ok, why

    # -- synthesis: caller-driven, before .run(), same pattern as
    # ResearchHTNAgent._synthesize_method / NeuroSymbolicWrapperHTNAgent -----
    async def _synthesize_plan(
        self, pool, embedder, sample: dict, sandbox,
        decomposer: Optional["decomposition_bridge.DecompositionService"] = None,
    ) -> bool:
        """
        Like NeuroSymbolicWrapperHTNAgent._synthesize_plan, but derives real
        touch-set tags from call-graph reachability seeded off the issue
        text and passes them as query_postconditions -- method_library's
        Rule 1 gate then requires a stored method's OWN touch-set to
        overlap this issue's PREDICTED one, not just that the goal text
        read similarly (method_library._passes_gate). This is the deep
        variant's actual differentiator over the wrapper's plain
        text-similarity reuse.
        """
        problem = str(sample.get("problem_statement", ""))
        seeds = call_graph.seed_from_text(problem, sandbox.root)
        query_postconditions: Optional[list[str]] = None
        seed_files: Optional[list[str]] = None
        if seeds:
            index = self._get_index(sandbox)
            reach = call_graph.reachable_symbols(
                seeds, sandbox.root, index, max_hops=self._callgraph_max_hops)
            seed_files = sorted({rel for rel, _name in seeds} | set(reach.files))
            query_postconditions = [f"touches:{f}" for f in seed_files]

        from method_library import find_reusable_plan
        match = await find_reusable_plan(pool, embedder, problem, query_postconditions)
        if match and match.get("decomposition"):
            self._pending_seed_plan = match["decomposition"]
            return True

        if decomposer is None:
            return False
        subgoals, _diag = await decomposition_bridge.decompose_issue(
            decomposer, sample, seed_files=seed_files,
            query_postconditions=query_postconditions, pool=pool,
            held_out_instance_id=sample.get("instance_id"))
        if subgoals:
            self._pending_seed_plan = subgoals
            return True
        return False


def touch_tags_from_run(run) -> list[str]:
    """
    Persistable touch tags from a COMPLETED run's actual edited files --
    ground truth once the run is done, deliberately not the predicted
    reachable set used mid-run (that is a hint for while the agent still
    has budget to check it, not a claim about what actually changed).
    Pass to method_library.persist_plan(..., touch_tags=...) after checking
    the run actually succeeded, same caller-driven pattern
    ResearchHTNAgent's docstring already shows for persist_plan itself.
    """
    return [f"touches:{f}" for f in run.files_edited]
