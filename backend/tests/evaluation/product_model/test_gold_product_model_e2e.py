"""
Live-Postgres gold-eval layer for the Problem/Benchmark/Solution/Evaluation
product model (task spec sections 2-5), going through the REAL production
service (app.services.product_model), never a fabricated row and never a
mock of the thing under test.

Reuses the product suite's own real-lineage fixture helpers
(_make_procedure / _run_executions from tests.test_product_model_e2e) so
this file does not re-derive how to build a real procedure + plan + graph +
executions + evidence chain -- it only asks new product questions of that
same real machinery: ownership/visibility, benchmark versioning/freeze,
solution-type diversity + no-copied-payload, comparability-gated
aggregation, ties/conditional-leaders/no-stored-winner, small-n safety, and
find_best_way/find_best_solution convergence.

Skips itself when DATABASE_URL is unset, matching every other _e2e.py file
in this suite.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.db.session import create_pool  # noqa: E402
from app.services import product_model as pm  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402
from tests.test_product_model_e2e import _make_procedure, _run_executions  # noqa: E402


def with_pool(fn):
    """Each test gets its own real pool, created/closed around the test
    body -- matching tests/test_product_model_e2e.py's own convention
    exactly (a plain @pytest.fixture async generator is not compatible with
    this repo's installed pytest-asyncio strict-mode setup). Deliberately
    does NOT use functools.wraps: it sets __wrapped__, which makes
    inspect.signature (and therefore pytest's fixture-requirement scan)
    see the wrapped function's `pool` parameter instead of this wrapper's
    real zero-argument signature -- exactly the bug this decorator exists
    to avoid.
    """
    async def wrapper():
        p = await create_pool(statement_cache_size=0)
        try:
            await fn(p)
        finally:
            await p.close()
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _tag() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Problem: ownership / visibility / lookup / matching / no-private-leak
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@with_pool
async def test_private_problem_invisible_to_other_user_visible_to_owner(pool):
    tag = _tag()
    problem = await pm.create_problem(
        pool, title=f"[pm-gold {tag}] private problem", proposer="userA",
        owner_id="userA", visibility="private",
    )
    owner_scope = AccessScope.for_user("userA")
    other_scope = AccessScope.for_user("userB")
    anon_scope = AccessScope.anonymous()

    assert await pm.get_problem(pool, problem["id"], scope=owner_scope) is not None
    assert await pm.get_problem(pool, problem["id"], scope=other_scope) is None
    assert await pm.get_problem(pool, problem["id"], scope=anon_scope) is None


@pytest.mark.asyncio
@with_pool
async def test_private_problem_does_not_leak_through_list_or_find(pool):
    tag = _tag()
    problem = await pm.create_problem(
        pool, title=f"[pm-gold {tag}] secret migration rollback procedure",
        objective="rollback a bad migration without downtime",
        proposer="userA", owner_id="userA", visibility="private",
    )
    other_scope = AccessScope.for_user("userB")

    listed = await pm.list_problems(pool, scope=other_scope, status="open")
    assert problem["id"] not in {p["id"] for p in listed}

    found = await pm.find_problem(pool, f"secret migration rollback {tag}", scope=other_scope)
    assert problem["id"] not in {p["id"] for p in found}

    # But the owner's own scope DOES find it -- proves the absence above is
    # a real visibility filter, not a query/index bug that hides everyone.
    found_owner = await pm.find_problem(
        pool, f"secret migration rollback {tag}", scope=AccessScope.for_user("userA")
    )
    assert problem["id"] in {p["id"] for p in found_owner}


@pytest.mark.asyncio
@with_pool
async def test_public_problem_lookup_by_natural_language_goal(pool):
    tag = _tag()
    problem = await pm.create_problem(
        pool, title=f"[pm-gold {tag}] deduplicate flaky retry loops",
        objective="stop retry storms from duplicating side effects", proposer="userA",
    )
    scope = AccessScope.unrestricted()
    found = await pm.find_problem(pool, f"deduplicate flaky retry storms {tag}", scope=scope)
    assert problem["id"] in {p["id"] for p in found}
    # A query sharing no lexeme with the problem must not match it. Deliberately
    # NOT reusing `tag` here -- it appears in the problem's own title, so a query
    # built from it would share a lexeme and this "unrelated" case would be
    # testing nothing.
    unrelated = await pm.find_problem(pool, f"unrelated-{_tag()}-xyzzy-quokka", scope=scope)
    assert problem["id"] not in {p["id"] for p in unrelated}


# ---------------------------------------------------------------------------
# Benchmark: versioning / freeze-immutability / version-change = new context
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@with_pool
async def test_benchmark_freeze_makes_measured_meaning_immutable(pool):
    tag = _tag()
    problem = await pm.create_problem(pool, title=f"[pm-gold {tag}] bench freeze", proposer="userA")
    bench = await pm.create_benchmark(
        pool, problem_id=problem["id"], name="freeze-bench", version=1,
        evaluation_protocol={"verification": "deterministic"},
    )
    frozen = await pm.freeze_benchmark(pool, bench["id"])
    assert frozen["status"] == "frozen" and frozen["frozen_at"] is not None

    # Idempotent freeze (calling again does not error or move frozen_at).
    frozen_again = await pm.freeze_benchmark(pool, bench["id"])
    assert frozen_again["frozen_at"] == frozen["frozen_at"]

    # Directly attempting to change what a frozen benchmark MEASURES must
    # be rejected by the DB trigger (assert_benchmark_frozen_immutable) --
    # this is a real trigger firing against a real UPDATE, not a Python-side
    # check we could accidentally bypass.
    with pytest.raises(Exception, match="frozen"):
        await pool.execute(
            "UPDATE benchmarks SET evaluation_protocol='{\"verification\":\"llm_judge\"}'::jsonb "
            "WHERE id=$1", bench["id"],
        )


@pytest.mark.asyncio
@with_pool
async def test_benchmark_version_bump_is_a_distinct_evaluation_context(pool):
    tag = _tag()
    problem = await pm.create_problem(pool, title=f"[pm-gold {tag}] bench version", proposer="userA")
    v1 = await pm.create_benchmark(
        pool, problem_id=problem["id"], name="vbump-bench", version=1,
        evaluation_protocol={"verification": "deterministic"},
    )
    v2 = await pm.create_benchmark(
        pool, problem_id=problem["id"], name="vbump-bench", version=2,
        evaluation_protocol={"verification": "deterministic"},
    )
    assert v1["id"] != v2["id"]
    benches = await pm.list_problem_benchmarks(pool, problem["id"])
    assert {b["id"] for b in benches} == {v1["id"], v2["id"]}

    # An evaluation against v1 and one against v2 are NOT comparable, even
    # with identical everything else -- a benchmark version pins the case
    # set + protocol (spec section 4).
    ok, reason = pm.evaluations_comparable(
        {"benchmark_id": v1["id"], "status": "completed", "methodology": {}, "environment": {}},
        {"benchmark_id": v2["id"], "status": "completed", "methodology": {}, "environment": {}},
    )
    assert not ok and "benchmark" in reason


@pytest.mark.asyncio
@with_pool
async def test_benchmark_case_wraps_an_existing_task_node_not_a_new_abstraction(pool):
    """Spec section 2: 'the smallest existing executable unit is a Task' --
    proven by inserting a real task_nodes row and a benchmark_cases row that
    references it via FK, exactly as migration 35's own comment says."""
    tag = _tag()
    problem = await pm.create_problem(pool, title=f"[pm-gold {tag}] bench case", proposer="userA")
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="case-bench", version=1)
    node_id = await pool.fetchval(
        "INSERT INTO task_nodes (id, name) VALUES ($1, $2) RETURNING id",
        str(uuid7()), f"pm-gold-{tag}-node",
    )
    case_id = await pool.fetchval(
        "INSERT INTO benchmark_cases (id, benchmark_id, task_node_id, expected_outcome, "
        " verification_criteria) VALUES ($1,$2,$3,$4::jsonb,$5::jsonb) RETURNING id",
        str(uuid7()), bench["id"], node_id,
        json.dumps({"status": "success"}), json.dumps({"predicate": "exit_code_zero"}),
    )
    row = await pool.fetchrow("SELECT * FROM benchmark_cases WHERE id=$1", case_id)
    assert str(row["task_node_id"]) == str(node_id)
    assert row["benchmark_id"] == uuid.UUID(bench["id"])


# ---------------------------------------------------------------------------
# Solution: procedure/task_graph/task-backed, multi-solution, no copied payload
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@with_pool
async def test_solution_supports_all_three_target_types_with_no_copied_payload(pool):
    tag = _tag()
    problem = await pm.create_problem(pool, title=f"[pm-gold {tag}] multi-type solutions", proposer="userA")

    proc_id, proc_row_id = await _make_procedure(pool, f"pm-gold-{tag}-proc")
    sol_proc = await pm.associate_solution(
        pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_id,
    )
    assert sol_proc["target_table"] == "procedures"

    node_id = await pool.fetchval(
        "INSERT INTO task_nodes (id, name) VALUES ($1,$2) RETURNING id",
        str(uuid7()), f"pm-gold-{tag}-task",
    )
    sol_task = await pm.associate_solution(
        pool, problem_id=problem["id"], solution_type="task", target_id=str(node_id),
    )
    assert sol_task["target_table"] == "task_nodes"

    # A real execution_plans row (procedure_id/version/row_id + content hashes
    # are NOT NULL, FK-tied to the procedure version above) -- same minimal
    # fixture shape tests/test_product_model_e2e.py::_run_executions already
    # uses for this table, not a fabricated standalone row.
    proc_version = await pool.fetchval(
        "SELECT version FROM procedures WHERE id=$1", proc_row_id,
    )
    plan_id = await pool.fetchval(
        "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
        " task_description, procedure_content_hash, content_hash, scope_type) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
        str(uuid7()), proc_id, proc_version, proc_row_id,
        f"pm-gold-{tag} plan", f"pch-{uuid.uuid4().hex[:12]}", f"ch-{uuid.uuid4().hex[:12]}",
    )
    graph_id = await pool.fetchval(
        "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
        "VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
        str(uuid7()), plan_id, f"h-{uuid.uuid4().hex[:12]}", json.dumps({"nodes": []}),
    )
    sol_graph = await pm.associate_solution(
        pool, problem_id=problem["id"], solution_type="task_graph", target_id=str(graph_id),
    )
    assert sol_graph["target_table"] == "task_graphs"

    solutions = await pm.list_problem_solutions(pool, problem["id"], scope=AccessScope.unrestricted())
    assert {s["id"] for s in solutions} == {sol_proc["id"], sol_task["id"], sol_graph["id"]}

    # No copied payload: a Solution row's own keys are the association
    # schema only -- no procedure/task/graph content columns exist on it.
    payload_keys = {"steps", "goal", "nodes", "io_schema", "graph_hash", "constraints"}
    for s in solutions:
        assert payload_keys.isdisjoint(s.keys()), f"solution row leaked target payload: {s.keys()}"


@pytest.mark.asyncio
@with_pool
async def test_associate_solution_rejects_a_target_that_does_not_exist(pool):
    tag = _tag()
    problem = await pm.create_problem(pool, title=f"[pm-gold {tag}] bad target", proposer="userA")
    with pytest.raises(ValueError, match="does not exist"):
        await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure",
            target_id="00000000-0000-4000-8000-000000000000",
        )


# ---------------------------------------------------------------------------
# Evaluation lineage: real execution required, caller cannot fabricate
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@with_pool
async def test_completed_evaluation_requires_real_lineage_and_ignores_caller_fabricated_numbers(pool):
    tag = _tag()
    problem = await pm.create_problem(pool, title=f"[pm-gold {tag}] lineage", proposer="userA")
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="lineage-bench", version=1)
    proc_id, proc_row = await _make_procedure(pool, f"pm-gold-{tag}-lineage")
    sol = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_id)

    # A completed status can never be inserted directly without lineage --
    # the DB trigger is the backstop underneath the service-layer check.
    with pytest.raises(Exception, match="lineage|linked execution"):
        await pool.execute(
            "INSERT INTO evaluations (id, problem_id, benchmark_id, solution_id, status, completed_at) "
            "VALUES ($1,$2,$3,$4,'completed', now())",
            str(uuid7()), problem["id"], bench["id"], sol["id"],
        )

    exec_ids = await _run_executions(pool, proc_id, proc_row, n=8, successes=6)
    ev = await pm.request_evaluation(
        pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
        procedure_id=proc_id, procedure_version=1,
    )
    # A caller-supplied success/verified count must be ignored -- only
    # extra_metrics keys the substrate cannot derive are allowed through,
    # and even naming a protected key there must not move the real number.
    done = await pm.complete_evaluation(
        pool, ev["id"], execution_ids=exec_ids,
        extra_metrics={"verified_success_rate": 1.0, "success_rate": 1.0, "run_count": 999},
    )
    assert done["metrics"]["run_count"] == 8
    assert done["metrics"]["verified_success_rate"] == round(6 / 8, 4)
    assert done["verification_summary"]["source"] == "recomputed_from_evaluation_executions"

    with pytest.raises(ValueError, match="not found"):
        await pm.complete_evaluation(pool, ev["id"], execution_ids=["00000000-0000-4000-8000-000000000000"])


# ---------------------------------------------------------------------------
# Comparability gate excludes incompatible evaluations from aggregation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@with_pool
async def test_incomparable_evaluation_is_excluded_from_leaderboard_aggregation(pool):
    tag = _tag()
    problem = await pm.create_problem(pool, title=f"[pm-gold {tag}] incomparable", proposer="userA")
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="incomp-bench", version=1)
    proc_id, proc_row = await _make_procedure(pool, f"pm-gold-{tag}-incomp")
    sol = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_id)

    exec_1 = await _run_executions(pool, proc_id, proc_row, n=10, successes=9)
    exec_2 = await _run_executions(pool, proc_id, proc_row, n=10, successes=9)

    ev1 = await pm.request_evaluation(
        pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
        procedure_id=proc_id, procedure_version=1, environment={"runtime": "linux"},
        methodology={"verification": "deterministic"},
    )
    ev2 = await pm.request_evaluation(
        pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
        procedure_id=proc_id, procedure_version=1, environment={"runtime": "windows"},
        methodology={"verification": "deterministic"},
    )
    await pm.complete_evaluation(pool, ev1["id"], execution_ids=exec_1)
    await pm.complete_evaluation(pool, ev2["id"], execution_ids=exec_2)

    lb = await pm.problem_leaderboard(pool, problem["id"], scope=AccessScope.unrestricted(), benchmark_id=bench["id"])
    entry = next(e for e in lb["leaderboard"] if e["solution_id"] == sol["id"])
    # Only the pivot (first) evaluation's 10 runs count -- the
    # material-environment-incompatible second evaluation is excluded, not
    # silently pooled in.
    assert entry["run_count"] == 10
    assert entry["evaluations"] == 1
    assert entry["incomparable_evaluations"] == 1


# ---------------------------------------------------------------------------
# Ties / conditional leaders / no stored winner / small-n safety
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@with_pool
async def test_ties_conditional_leaders_and_no_stored_winner_field(pool):
    tag = _tag()
    problem = await pm.create_problem(pool, title=f"[pm-gold {tag}] ties", proposer="userA")
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="tie-bench", version=1)

    proc_a, row_a = await _make_procedure(pool, f"pm-gold-{tag}-tie-A")
    proc_b, row_b = await _make_procedure(pool, f"pm-gold-{tag}-tie-B")
    sol_a = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_a)
    sol_b = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_b)

    # Same n, same successes -> effectively identical Wilson lower bounds
    # (within TIE_EPSILON) -> both should appear in current_best as a tie.
    exec_a = await _run_executions(pool, proc_a, row_a, n=40, successes=38)
    exec_b = await _run_executions(pool, proc_b, row_b, n=40, successes=38)
    ev_a = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                        solution_id=sol_a["id"], procedure_id=proc_a, procedure_version=1)
    ev_b = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                        solution_id=sol_b["id"], procedure_id=proc_b, procedure_version=1)
    # B is cheaper (extra_metrics 'cost' the substrate cannot derive on its own).
    await pm.complete_evaluation(pool, ev_a["id"], execution_ids=exec_a, extra_metrics={"cost": 0.50})
    await pm.complete_evaluation(pool, ev_b["id"], execution_ids=exec_b, extra_metrics={"cost": 0.05})

    lb = await pm.problem_leaderboard(pool, problem["id"], scope=AccessScope.unrestricted(), benchmark_id=bench["id"])
    assert lb["current_best_is_tie"] is True
    assert set(lb["current_best"]) == {sol_a["id"], sol_b["id"]}
    # Cost-conditional leader differs from (is a strict subset of) the tied
    # reliability leaders -- this is the "explains WHY" surface the task
    # spec asks for: reliability ties, cost doesn't.
    assert lb["conditional_leaders"]["best_cost"] == sol_b["id"]
    assert lb["conditional_leaders"]["best_reliability"] in lb["current_best"]

    # No stored winner anywhere in the row-level data this all comes from.
    for row in (await pm.get_problem(pool, problem["id"], scope=AccessScope.unrestricted()),
                await pm.get_evaluation(pool, ev_a["id"]), await pm.get_evaluation(pool, ev_b["id"])):
        assert "winner" not in row and "best_solution_id" not in row


@pytest.mark.asyncio
@with_pool
async def test_small_n_perfect_solution_never_reaches_best_verified_through_the_real_leaderboard(pool):
    """Product-level test_product_model_offline.py proves this against
    _band() directly with synthetic numbers; this proves the same safety
    property holds end-to-end through a real insert -> aggregate ->
    leaderboard path, which is new coverage (task spec section 4: 'small
    sample size retained... 100%, n=2 must never dominate')."""
    tag = _tag()
    problem = await pm.create_problem(pool, title=f"[pm-gold {tag}] small-n", proposer="userA")
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="small-n-bench", version=1)
    proc_id, proc_row = await _make_procedure(pool, f"pm-gold-{tag}-small-n")
    sol = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_id)

    exec_ids = await _run_executions(pool, proc_id, proc_row, n=2, successes=2)
    ev = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                      solution_id=sol["id"], procedure_id=proc_id, procedure_version=1)
    await pm.complete_evaluation(pool, ev["id"], execution_ids=exec_ids)

    lb = await pm.problem_leaderboard(pool, problem["id"], scope=AccessScope.unrestricted(), benchmark_id=bench["id"])
    entry = next(e for e in lb["leaderboard"] if e["solution_id"] == sol["id"])
    assert entry["success_rate"] == 1.0
    assert entry["state"] == "INSUFFICIENT_EVIDENCE"
    assert lb["current_best"] == []


# ---------------------------------------------------------------------------
# find_best_way (product-model service) / find_best_solution (MCP) convergence
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@with_pool
async def test_find_best_way_no_matching_problem(pool):
    tag = _tag()
    # find_problem OR's every lexeme in the query (see its own docstring: "an NL
    # goal is not a boolean AND query"), so a real dictionary word here risks
    # matching SOME accumulated row in this shared, never-cleaned-up dev DB --
    # deliberately using only invented non-words (plus the random tag) so this
    # negative case can't collide with any other test's real problem titles.
    result = await pm.find_best_way(pool, f"zzqvix-{tag}-fjwortk-plexnar", scope=AccessScope.unrestricted())
    assert result["result"] == "no matching problem"
    assert result["current_best"] == []
    assert result["matched_problem"] is None


@pytest.mark.asyncio
@with_pool
async def test_find_best_way_problem_matched_but_no_verified_solution(pool):
    tag = _tag()
    problem = await pm.create_problem(
        pool, title=f"[pm-gold {tag}] goal with only weak evidence",
        objective="a problem with a solution that never clears the bar", proposer="userA",
    )
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="weak-bench", version=1)
    proc_id, proc_row = await _make_procedure(pool, f"pm-gold-{tag}-weak")
    sol = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_id)
    exec_ids = await _run_executions(pool, proc_id, proc_row, n=20, successes=8)  # well under HIGH_PERFORMING
    ev = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                      solution_id=sol["id"], procedure_id=proc_id, procedure_version=1)
    await pm.complete_evaluation(pool, ev["id"], execution_ids=exec_ids)

    result = await pm.find_best_way(pool, f"goal with only weak evidence {tag}", scope=AccessScope.unrestricted())
    assert result["matched_problem"]["id"] == problem["id"]
    assert result["result"] == "no verified solution yet"
    assert result["current_best"] == []


@pytest.mark.asyncio
@with_pool
async def test_find_best_way_one_verified_solution_converges_to_it(pool):
    tag = _tag()
    problem = await pm.create_problem(
        pool, title=f"[pm-gold {tag}] goal with one strong verified solution",
        objective="exactly one solution clears BEST_VERIFIED", proposer="userA",
    )
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="strong-bench", version=1)
    proc_id, proc_row = await _make_procedure(pool, f"pm-gold-{tag}-strong")
    sol = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_id)
    exec_ids = await _run_executions(pool, proc_id, proc_row, n=30, successes=29)
    ev = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                      solution_id=sol["id"], procedure_id=proc_id, procedure_version=1)
    await pm.complete_evaluation(pool, ev["id"], execution_ids=exec_ids)

    result = await pm.find_best_way(pool, f"goal with one strong verified solution {tag}", scope=AccessScope.unrestricted())
    assert result["result"] == "verified"
    assert result["current_best"] == [sol["id"]]
    assert "leaderboard" in result and "conditional_leaders" in result


@pytest.mark.asyncio
@with_pool
async def test_find_best_solution_mcp_tool_is_the_product_model_lookup_not_the_htn_agent(pool):
    """find_best_way (this module's service function, surfaced by the MCP
    tool `find_best_solution`) answers 'which known solution is measurably
    best' from evidence -- it is a DIFFERENT tool from the MCP `find_best_way`
    HTN coding-agent tool (retrieval -> plan -> sandboxed execution). Proven
    two ways: (1) they are distinct callables in the MCP server module, and
    (2) find_best_solution's answer is driven by Wilson-lower-bound
    evidence, not by which procedure's text is most similar to the goal --
    a lexically-closer but unverified/weaker procedure must not win over a
    lexically-weaker but verified, evidence-backed one."""
    import app.mcp_server.server as srv

    assert srv.find_best_way is not srv.find_best_solution
    assert srv.find_best_way.__doc__ and "TIER 2" in srv.find_best_way.__doc__ and "sandboxed" in srv.find_best_way.__doc__
    assert "text similarity" in srv.find_best_solution.__doc__

    tag = _tag()
    problem = await pm.create_problem(
        pool, title=f"[pm-gold {tag}] convergence not similarity",
        objective="prove ranking, not lexical closeness, decides the answer", proposer="userA",
    )
    bench = await pm.create_benchmark(pool, problem_id=problem["id"], name="conv-bench", version=1)
    # "closer" text match, weak evidence.
    proc_close, row_close = await _make_procedure(
        pool, f"pm-gold-{tag}-convergence not similarity exact phrase match"
    )
    # "further" text match, strong verified evidence.
    proc_far, row_far = await _make_procedure(pool, f"pm-gold-{tag}-totally-different-name")
    sol_close = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_close)
    sol_far = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure", target_id=proc_far)

    exec_close = await _run_executions(pool, proc_close, row_close, n=20, successes=6)
    exec_far = await _run_executions(pool, proc_far, row_far, n=20, successes=19)
    ev_close = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                            solution_id=sol_close["id"], procedure_id=proc_close, procedure_version=1)
    ev_far = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                          solution_id=sol_far["id"], procedure_id=proc_far, procedure_version=1)
    await pm.complete_evaluation(pool, ev_close["id"], execution_ids=exec_close)
    await pm.complete_evaluation(pool, ev_far["id"], execution_ids=exec_far)

    ctx = _FakeCtx(pool)
    fbs = json.loads(await srv.find_best_solution(f"convergence not similarity {tag}", ctx))
    assert fbs["result"] == "verified"
    assert fbs["current_best"] == [sol_far["id"]], fbs  # evidence wins, not text proximity


class _FakeCtx:
    def __init__(self, pool):
        class _RC:
            pass
        self.request_context = _RC()
        self.request_context.lifespan_context = {"pool": pool}
