"""
DB-free coverage for app.execution.goal_resolution -- the recursive
Goal->Procedure compiler (bound steps are the executable leaves) (execu.md Sec 8). Every real
dependency (goals table reads, check_hard_constraints) is monkeypatched at the module level, proving
this module's own recursion/cycle/depth/decision logic in isolation --

"""
from __future__ import annotations

import asyncio

import app.execution.goal_resolution as gr
from app.services.access import AccessScope
from app.services.applicability import ApplicabilityResult


def _run(coro):
    return asyncio.run(coro)


SCOPE = AccessScope.unrestricted()


class _FakePool:
    """Answers only `SELECT * FROM goals WHERE id = $1::uuid ...` --
    every other real query this module would issue
    (select_implementation_for_goal_id, check_hard_constraints,
    the achieves_goal_id procedure lookup) is monkeypatched away
    entirely in these tests, so this pool never needs to answer them."""

    def __init__(self, goals: dict[str, dict]):
        self._goals = goals

    async def fetchrow(self, sql, *params):
        n = " ".join(sql.split())
        if "FROM goals" in n:
            return self._goals.get(str(params[0]))
        raise AssertionError(f"unexpected fetchrow: {n[:80]}")

    async def fetch(self, sql, *params):
        raise AssertionError(f"unexpected fetch: {' '.join(sql.split())[:80]}")


def _goal(goal_id: str, name: str, **overrides) -> dict:
    row = {"id": goal_id, "canonical_name": name, "scope_type": "global", "scope_entity_id": None}
    row.update(overrides)
    return row


# ---------------------------------------------------------------------
# A. root not found
# ---------------------------------------------------------------------


def test_root_goal_not_found_raises():
    pool = _FakePool({})
    try:
        _run(gr.resolve_goal(pool, "missing", scope=SCOPE))
        assert False, "should have raised"
    except gr.GoalResolutionError as exc:
        assert "missing" in str(exc)


def test_non_root_goal_not_found_is_an_honest_unresolved_leaf(monkeypatch):
    # simulated via a direct resolve_goal call at depth=1 (as the
    # recursive child path would do) against a pool with nothing in it
    pool = _FakePool({})
    node = _run(gr.resolve_goal(pool, "missing-child", scope=SCOPE, depth=1))
    assert node.chosen == "unresolved"
    assert "not found" in node.unresolved_reason


# ---------------------------------------------------------------------
# B. a step carrying a binding is an executable leaf
# ---------------------------------------------------------------------


def _bound_proc(pid="P-1"):
    return {"id": pid, "procedure_id": pid, "name": "one-step", "version": 1,
            "steps": [{"order": 0, "goal": "run the check", "binding": {"kind": "command", "command": "make check"}}]}


def test_one_step_procedure_resolves_to_a_bound_step_leaf(monkeypatch):
    pool = _FakePool({"G-1": _goal("G-1", "find references", verification_requirement={"method": "deterministic_check", "command": "true"})})

    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        return [(_bound_proc(), True)]
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)
    node = _run(gr.resolve_goal(pool, "G-1", scope=SCOPE))
    assert node.chosen == "procedure" and len(node.children) == 1
    leaf = node.children[0]
    assert leaf.chosen == "step" and leaf.step["binding"]["command"] == "make check"
    assert leaf.step["procedure_id"] == "P-1" and leaf.goal_id == "G-1#s0"
    assert node.verification_requirement == {"method": "deterministic_check", "command": "true"}


def test_bound_step_wins_over_a_goal_text_match(monkeypatch):
    pool = _FakePool({"G-1": _goal("G-1", "x")})

    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        return [(_bound_proc(), True)]

    async def boom(*a, **k):
        raise AssertionError("a bound step must not be re-resolved as a goal")
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)
    monkeypatch.setattr(gr, "resolve_goal_id_for_text", boom)
    assert _run(gr.resolve_goal(pool, "G-1", scope=SCOPE)).children[0].chosen == "step"


def test_goal_with_no_verification_requirement_has_an_honest_empty_dict(monkeypatch):
    pool = _FakePool({"G-1": _goal("G-1", "find references")})
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", lambda *a, **k: _async_result([]))
    assert _run(gr.resolve_goal(pool, "G-1", scope=SCOPE)).verification_requirement == {}


# ---------------------------------------------------------------------
# C. procedure decomposition + recursion
# ---------------------------------------------------------------------


def test_procedure_decomposes_into_child_goals(monkeypatch):
    pool = _FakePool({
        "G-parent": _goal("G-parent", "safely modify generated API"),
        "G-child": _goal("G-child", "regenerate bindings"),
    })

    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        if goal_id == "G-parent":
            return [({
                "id": "P-1", "procedure_id": "P-1", "name": "modify-generated-api", "version": 1,
                "steps": [{"order": 0, "goal": "regenerate bindings"}],
            }, True)]
        return []
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)

    async def fake_text_lookup(pool, text, *, scope_type, scope_entity_id):
        return {"id": "G-child"} if text == "regenerate bindings" else None
    monkeypatch.setattr(gr, "resolve_goal_id_for_text", fake_text_lookup)

    # the recursive call into G-child should also find no direct impl
    # and no procedure, landing as unresolved -- proven by leaving
    # _feasible_procedures_for_goal returning [] for it above.
    node = _run(gr.resolve_goal(pool, "G-parent", scope=SCOPE))
    assert node.chosen == "procedure"
    assert node.procedure["name"] == "modify-generated-api"
    assert len(node.children) == 1
    child = node.children[0]
    assert child.goal_id == "G-child"
    assert child.depth == 1
    assert child.chosen == "unresolved"


def test_procedure_node_keeps_real_alternate_feasible_procedures(monkeypatch):
    pool = _FakePool({"G-parent": _goal("G-parent", "safely modify generated API")})

    proc_a = {"id": "P-1", "procedure_id": "P-1", "name": "strategy-a", "version": 1, "steps": []}
    proc_b = {"id": "P-2", "procedure_id": "P-2", "name": "strategy-b", "version": 1, "steps": []}

    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        return [(proc_a, True), (proc_b, True)]
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)

    node = _run(gr.resolve_goal(pool, "G-parent", scope=SCOPE))
    assert node.procedure["name"] == "strategy-a"
    assert [p["name"] for p in node.procedure_alternates] == ["strategy-b"]


def test_procedure_cost_score_hook_is_called_but_inert(monkeypatch):
    """Prompt 2 Sec 11 placeholder: the hook is real and exercised, but
    its (always-None) result must not reorder or otherwise change which
    procedure is chosen -- feasible[0] wins exactly as before."""
    pool = _FakePool({"G-parent": _goal("G-parent", "safely modify generated API")})

    proc_a = {"id": "P-1", "procedure_id": "P-1", "name": "strategy-a", "version": 1, "steps": []}
    proc_b = {"id": "P-2", "procedure_id": "P-2", "name": "strategy-b", "version": 1, "steps": []}
    calls = []

    def spy_cost_score(proc):
        calls.append(proc["name"])
        return None

    monkeypatch.setattr(gr, "_procedure_cost_score", spy_cost_score)

    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        return [(proc_a, True), (proc_b, True)]
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)

    node = _run(gr.resolve_goal(pool, "G-parent", scope=SCOPE))
    assert sorted(calls) == ["strategy-a", "strategy-b"]
    assert node.procedure["name"] == "strategy-a"  # unchanged: still feasible[0]


def test_resolve_goal_via_procedure_resolves_a_specific_alternate(monkeypatch):
    pool = _FakePool({
        "G-parent": _goal("G-parent", "safely modify generated API"),
        "G-child": _goal("G-child", "regenerate bindings"),
    })
    proc_b = {
        "id": "P-2", "procedure_id": "P-2", "name": "strategy-b", "version": 1,
        "steps": [{"order": 0, "goal": "regenerate bindings"}],
    }
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", lambda *a, **k: _async_result([]))

    async def fake_text_lookup(pool, text, *, scope_type, scope_entity_id):
        return {"id": "G-child"} if text == "regenerate bindings" else None
    monkeypatch.setattr(gr, "resolve_goal_id_for_text", fake_text_lookup)

    node = _run(gr.resolve_goal_via_procedure(pool, "G-parent", proc_b, scope=SCOPE))
    assert node.chosen == "procedure"
    assert node.procedure["name"] == "strategy-b"
    assert len(node.children) == 1
    assert node.children[0].goal_id == "G-child"


async def _async_result(value):
    return value


def test_step_with_no_matching_goal_text_is_an_honest_unresolved_child(monkeypatch):
    pool = _FakePool({"G-parent": _goal("G-parent", "do the thing")})

    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        return [({
            "id": "P-1", "procedure_id": "P-1", "name": "p", "version": 1,
            "steps": [{"order": 0, "goal": "some totally unmatched free text"}],
        }, True)]
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)

    async def fake_text_lookup(pool, text, *, scope_type, scope_entity_id):
        return None  # nothing ever matches
    monkeypatch.setattr(gr, "resolve_goal_id_for_text", fake_text_lookup)

    node = _run(gr.resolve_goal(pool, "G-parent", scope=SCOPE))
    assert node.chosen == "procedure"
    assert len(node.children) == 1
    assert node.children[0].goal_id == "-"
    assert node.children[0].chosen == "unresolved"
    assert "does not match any canonical Goal" in node.children[0].unresolved_reason


# ---------------------------------------------------------------------
# D. cycle prevention + recursion limit
# ---------------------------------------------------------------------


def test_cycle_is_detected_not_infinite_looped(monkeypatch):
    pool = _FakePool({
        "G-A": _goal("G-A", "a"),
        "G-B": _goal("G-B", "b"),
    })

    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        other = "G-B" if goal_id == "G-A" else "G-A"
        return [({"id": f"P-{goal_id}", "procedure_id": f"P-{goal_id}", "name": "p", "version": 1,
                  "steps": [{"order": 0, "goal": other}]}, True)]
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)

    async def fake_text_lookup(pool, text, *, scope_type, scope_entity_id):
        return {"id": text}  # "G-A" -> {"id": "G-A"}, "G-B" -> {"id": "G-B"}
    monkeypatch.setattr(gr, "resolve_goal_id_for_text", fake_text_lookup)

    node = _run(gr.resolve_goal(pool, "G-A", scope=SCOPE, max_depth=10))
    # G-A -> procedure -> child G-B -> procedure -> child G-A (cycle!)
    assert node.chosen == "procedure"
    child = node.children[0]
    assert child.goal_id == "G-B"
    assert child.chosen == "procedure"
    grandchild = child.children[0]
    assert grandchild.goal_id == "G-A"
    assert grandchild.chosen == "unresolved"
    assert "cycle detected" in grandchild.unresolved_reason


def test_recursion_limit_is_honored(monkeypatch):
    pool = _FakePool({f"G-{i}": _goal(f"G-{i}", f"goal {i}") for i in range(10)})

    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        n = int(goal_id.split("-")[1])
        return [({"id": f"P-{n}", "procedure_id": f"P-{n}", "name": "p", "version": 1,
                  "steps": [{"order": 0, "goal": f"G-{n + 1}"}]}, True)]
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)

    async def fake_text_lookup(pool, text, *, scope_type, scope_entity_id):
        return {"id": text} if text in pool._goals else None
    monkeypatch.setattr(gr, "resolve_goal_id_for_text", fake_text_lookup)

    node = _run(gr.resolve_goal(pool, "G-0", scope=SCOPE, max_depth=3))
    # depth 0 (G-0) -> 1 (G-1) -> 2 (G-2) -> 3 would be G-3 but hits the limit
    d = node
    depths_seen = []
    while d.chosen == "procedure":
        depths_seen.append(d.depth)
        d = d.children[0]
    depths_seen.append(d.depth)
    assert d.chosen == "unresolved"
    assert "recursion limit" in d.unresolved_reason
    assert d.depth == 3


# ---------------------------------------------------------------------
# E. nothing feasible at all
# ---------------------------------------------------------------------


def test_no_feasible_procedure_is_honest_unresolved(monkeypatch):
    pool = _FakePool({"G-1": _goal("G-1", "impossible goal")})

    async def fake_feasible(pool, goal_id, *, current_scope, access_scope):
        return []
    monkeypatch.setattr(gr, "_feasible_procedures_for_goal", fake_feasible)

    node = _run(gr.resolve_goal(pool, "G-1", scope=SCOPE))
    assert node.chosen == "unresolved"
    assert "no procedure linked" in node.unresolved_reason
