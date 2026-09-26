"""BigCodeBench adapter + split (app/benchmarks): parsing, domain placement, the
visible/gold test split, exclusions, name collisions, deterministic stratified split."""
from __future__ import annotations

from app.benchmarks import bigcodebench as bcb
from app.benchmarks.importer import summarize
from app.benchmarks.tasks import assign_splits, choose_visible
from tests.benchmark_fixtures import ROWS, row


def test_rows_become_tasks_with_domains_and_a_visible_check():
    tasks = {t.external_id: t for t in bcb.tasks_from_rows(ROWS)}
    t0, t1, t2 = tasks["BigCodeBench/0"], tasks["BigCodeBench/1"], tasks["BigCodeBench/2"]
    assert t0.goal_name == "Calculate the average of the sums of absolute differences for permutations of a list."
    assert t0.domains == ["Write core Python algorithms"] and t0.libs == ["random", "itertools"]
    # most specific domain first: plotting before tabular data, never the helper `random`
    assert t1.domains == ["Visualize data with Python plotting libraries", "Analyze tabular data with pandas and NumPy"]
    assert t2.domains[0] == "Make HTTP requests, scrape pages and serve web apps in Python"
    # visible = a stable ~1/3 of the tests, never all; gold = every test
    assert len(t0.test_names) == 5 and 1 <= len(t0.visible_tests) < 5
    assert set(t0.visible_tests) | set(t0.hidden_tests) == set(t0.test_names)
    assert t0.visible_tests == choose_visible("BigCodeBench/0", t0.test_names)
    # the reference solution is never kept, only its hash
    assert t0.reference_sha256 and "return 0" not in repr(t0.__dict__)
    assert t0.goal_description.startswith("Calculate the average") and "self-contained" in t0.goal_description


def test_too_few_tests_are_excluded_with_the_reason():
    t3 = next(t for t in bcb.tasks_from_rows(ROWS) if t.external_id == "BigCodeBench/3")
    assert t3.excluded_reason and "fewer than 3" in t3.excluded_reason and t3.visible_tests == []


def test_colliding_goal_names_get_their_task_id():
    names = {t.external_id: t.goal_name for t in bcb.tasks_from_rows(ROWS)}
    assert names["BigCodeBench/4"].endswith("[BigCodeBench/4]") and names["BigCodeBench/5"].endswith("[BigCodeBench/5]")


def test_split_is_deterministic_stratified_and_skips_excluded():
    many = [row(i, f"Task number {i} does something with dataframes.", "['pandas']") for i in range(20)]
    many += [row(100 + i, f"Task number {i} plots something.", "['matplotlib']") for i in range(10)]
    a, b = bcb.tasks_from_rows(many), bcb.tasks_from_rows(many)
    assign_splits(a)
    assign_splits(b)
    assert [t.split for t in a] == [t.split for t in b]
    pandas_fit = sum(1 for t in a if t.domains[0].startswith("Analyze") and t.split == "fit")
    plot_fit = sum(1 for t in a if t.domains[0].startswith("Visualize") and t.split == "fit")
    assert (pandas_fit, plot_fit) == (12, 6)                                    # 60% of each domain
    tasks = bcb.tasks_from_rows(ROWS)
    assign_splits(tasks)
    assert next(t for t in tasks if t.external_id == "BigCodeBench/3").split is None
    s = summarize(tasks)
    assert s["tasks"] == 6 and s["usable"] == 5 and s["excluded"] == 1


def test_libs_and_names_parse_robustly():
    assert bcb.parse_libs("['os', 'sys']") == ["os", "sys"]
    assert bcb.parse_libs(["re"]) == ["re"] and bcb.parse_libs(None) == []
    assert bcb.domains_for([]) == [bcb.FALLBACK_DOMAIN]
    long = "Compute " + "a very long description " * 20 + "."
    assert len(bcb.goal_name_from(long)) <= 141 and bcb.goal_name_from(long).endswith("…")
