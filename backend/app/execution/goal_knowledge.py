"""
Resolved Goal tree -> the knowledge `find_ways` hands the planner agent
(final_architecture.md).

The server does not compile a plan. It does not pick node order, assign node
ids, or write anything into the user's repo. It returns what it knows: for each
Goal in the tree, the chosen Procedure with every step in full, the
alternatives it kept, and why the repo facts supported or blocked the choice.
The planner agent, which can actually see the repo, turns this into
`.stealth/procedures.md` and `.stealth/run.md` itself.

Each step comes back as one of three kinds:
  action      -- the step carries a binding (a script/tool to run) and a
                 source locator the agent can fetch and read.
  subgoal     -- the step is itself a Goal with its own chosen Procedure;
                 `subgoal_id` points at another entry in `procedures`.
  instruction -- no binding and no known sub-Procedure: the step's own text is
                 the instruction. The agent does it directly. (The old server
                 executor treated this as a failure; an agent doesn't have to.)

Pure and DB-free: it only reads the `ResolvedGoalNode` tree `resolve_goal`
already built.
"""
from __future__ import annotations

from typing import Any, Optional


def step_needs(step: dict) -> dict:
    """What a step's binding needs to run -- runtime, entrypoint, network,
    credentials -- in plain keys. Empty when the step has no binding."""
    binding = step.get("binding") if isinstance(step, dict) else None
    if not isinstance(binding, dict):
        return {}
    needs: dict[str, Any] = {}
    for key in ("runtime", "entrypoint", "sandbox_policy"):
        if binding.get(key):
            needs[key] = binding[key]
    resources = binding.get("resources")
    if isinstance(resources, dict):
        needs.update({k: v for k, v in resources.items() if v})
    return needs


def step_check(step: dict) -> Optional[Any]:
    """How to tell the step worked, when the Procedure says so: its expected
    outcome, else a step/binding verifier. `None` means the Procedure gives no
    check and the planner has to write one -- never invented here."""
    if not isinstance(step, dict):
        return None
    binding = step.get("binding") if isinstance(step.get("binding"), dict) else {}
    return step.get("expected_outcome") or step.get("verifier") or binding.get("verifier") or None


def _alt(row: dict) -> dict:
    return {
        "procedure_id": str(row.get("procedure_id") or row.get("id")), "version_id": str(row.get("id")),
        "name": row.get("name"), "version": row.get("version"),
        **({"repo_fit": row["_repo_fit"]} if row.get("_repo_fit") else {}),
    }


def goal_tree_to_knowledge(tree: Any) -> dict:
    """`{"goal": {...}, "procedures": [...], "unresolved": [...]}`.

    `procedures` lists every Goal in the tree that got a Procedure, root
    first, then depth-first in step order -- a reading order, not an execution
    order. The planner decides the order."""
    procedures: list[dict] = []
    unresolved: list[dict] = []

    def walk(node: Any, parent: Optional[dict]) -> None:
        if node.chosen != "procedure" or not node.procedure:
            unresolved.append({"goal_id": node.goal_id, "goal_name": node.goal_name,
                               "parent": parent, "reason": node.unresolved_reason})
            return
        proc = node.procedure
        steps = [s for s in (proc.get("steps") or []) if isinstance(s, dict)]
        entry = {
            "goal_id": node.goal_id, "goal_name": node.goal_name, "depth": node.depth, "parent": parent,
            "procedure_id": proc.get("procedure_id"), "version_id": proc.get("id"),
            "name": proc.get("name"), "version": proc.get("version"),
            "what_it_does": proc.get("description") or proc.get("goal"),
            "verification_state": proc.get("verification_state"),
            "preconditions": proc.get("preconditions") or [],
            "goal_check": node.verification_requirement or None,
            "why_chosen": node.rationale,
            "repo_fit": proc.get("repo_fit"),
            "alternatives": [_alt(a) for a in node.procedure_alternates],
            "steps": [],
        }
        procedures.append(entry)
        # _resolve_procedure_children builds exactly one child per step, in
        # the same sorted order -- so they line up one to one.
        children = list(node.children)
        later: list[tuple[Any, dict]] = []
        for i, step in enumerate(steps):
            child = children[i] if i < len(children) else None
            out = {
                "order": step.get("order"),
                "do": step.get("goal") or step.get("description") or step.get("action"),
                "description": step.get("description"),
                "source_locator": step.get("source_locator"),
                "binding": step.get("binding"),
                "needs": step_needs(step),
                "check": step_check(step),
            }
            if step.get("binding"):
                out["kind"] = "action"
            elif child is not None and child.chosen == "procedure":
                out["kind"], out["subgoal_id"] = "subgoal", child.goal_id
                later.append((child, {"goal_id": node.goal_id, "step_order": step.get("order")}))
            else:
                out["kind"] = "instruction"
                if child is not None and child.unresolved_reason:
                    out["note"] = child.unresolved_reason
            entry["steps"].append(out)
        for child, where in later:
            walk(child, where)

    walk(tree, None)
    return {
        "goal": {"goal_id": tree.goal_id, "name": tree.goal_name,
                 "check": tree.verification_requirement or None},
        "procedures": procedures,
        "unresolved": unresolved,
    }
