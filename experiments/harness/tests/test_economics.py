"""Unit tests for economics.py's pure functions (spec section 26).
Synthetic inputs only -- no live calls, no real spend."""
import economics


def test_spend_log_cost_sums_only_cost_usd_and_ignores_failed_rows_correctly():
    rows = [
        {"cost_usd": 0.01, "tokens_in": 100, "tokens_out": 50},
        {"cost_usd": 0.02, "tokens_in": 200, "tokens_out": 80},
        {"cost_usd": 0.0, "status": None, "error": "TimeoutError"},  # a failed attempt, real row shape
    ]
    assert economics.spend_log_cost(rows) == 0.03


def test_memory_building_cost_total_sums_every_category():
    cost = economics.MemoryBuildingCost(
        ingestion=0.1, embedding=0.2, extraction=0.3,
        claim_processing=0.4, generalization=0.5,
    )
    assert cost.total == 1.5


def test_per_task_stealth_overhead_total_sums_every_category():
    overhead = economics.PerTaskStealthOverhead(
        retrieval=0.01, storage=0.02, revalidation=0.03, execution_overhead=0.04,
    )
    assert overhead.total == 0.10


def test_evaluate_workload_repetitive_shows_positive_roi_and_a_real_break_even_point():
    result = economics.evaluate_workload(
        baseline_cost_per_task=3.00, stealth_cost_per_task=0.50,
        num_tasks=100, memory_building_cost=2.00,
    )
    assert result.baseline_cost == 300.00
    assert result.stealth_cost == 52.00  # 0.50*100 + 2.00
    assert result.gross_savings == 250.00  # (3.00-0.50)*100
    assert result.net_savings == 248.00  # 250 - 2 build cost
    assert result.roi is not None and result.roi > 0
    # break-even: build_cost / per_task_saving = 2.00 / 2.50 = 0.8 reuses
    assert result.break_even_reuses == 0.8


def test_evaluate_workload_one_off_with_no_real_saving_reports_no_break_even_honestly():
    """A one-off task where Stealth costs MORE per task than baseline (the
    realistic one-off story: overhead with no reuse to amortize against)
    must report break_even_reuses=None, not a huge finite number and not
    a negative one -- there is genuinely no number of reuses within this
    workload (num_tasks=1) that recoups anything, and pretending
    otherwise would misrepresent a bad deal as a delayed good one."""
    result = economics.evaluate_workload(
        baseline_cost_per_task=3.00, stealth_cost_per_task=3.20,
        num_tasks=1, memory_building_cost=2.00,
    )
    assert result.net_savings < 0
    assert result.break_even_reuses is None


def test_evaluate_workload_with_zero_build_cost_and_positive_savings_breaks_even_immediately():
    result = economics.evaluate_workload(
        baseline_cost_per_task=1.00, stealth_cost_per_task=0.80,
        num_tasks=10, memory_building_cost=0.0,
    )
    assert result.break_even_reuses == 0.0


def test_evaluate_workload_zero_stealth_cost_reports_roi_as_none_not_infinity():
    result = economics.evaluate_workload(
        baseline_cost_per_task=1.00, stealth_cost_per_task=0.0,
        num_tasks=5, memory_building_cost=0.0,
    )
    assert result.stealth_cost == 0.0
    assert result.roi is None


def test_evaluate_workload_accepts_a_memory_building_cost_dataclass_or_a_bare_float_identically():
    from_dataclass = economics.evaluate_workload(
        baseline_cost_per_task=2.0, stealth_cost_per_task=1.0, num_tasks=10,
        memory_building_cost=economics.MemoryBuildingCost(ingestion=1.0, embedding=1.0),
    )
    from_float = economics.evaluate_workload(
        baseline_cost_per_task=2.0, stealth_cost_per_task=1.0, num_tasks=10,
        memory_building_cost=2.0,
    )
    assert from_dataclass == from_float


def test_cumulative_curve_crosses_from_stealth_more_expensive_to_baseline_more_expensive():
    """The curve's whole purpose: find where cumulative stealth cost drops
    below cumulative baseline cost. Early on (build cost dominates),
    stealth > baseline; later, baseline > stealth. Confirm the crossover
    actually exists in the generated curve for a workload designed to
    have one."""
    curve = economics.cumulative_curve(
        baseline_cost_per_task=1.00, stealth_cost_per_task=0.90,
        memory_building_cost=2.00, max_tasks=25,
    )
    assert len(curve) == 25
    n1, base1, stealth1 = curve[0]
    assert n1 == 1 and stealth1 > base1  # task 1: 2.90 stealth vs 1.00 baseline -- build cost dominates
    n25, base25, stealth25 = curve[-1]
    assert stealth25 < base25  # by task 25, past the 20-reuse break-even, stealth is cheaper cumulatively


def test_cumulative_curve_rejects_max_tasks_below_one():
    try:
        economics.cumulative_curve(
            baseline_cost_per_task=1.0, stealth_cost_per_task=1.0,
            memory_building_cost=0.0, max_tasks=0,
        )
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_demo_workload_shapes_covers_all_four_named_shapes_with_the_expected_qualitative_story():
    demo = economics.demo_workload_shapes()
    assert set(demo) == {"one_off", "repetitive", "mixed", "long_lived_changing_environment"}

    # One-off: bad deal, honestly reported (no break-even within one task).
    assert demo["one_off"].net_savings < 0
    assert demo["one_off"].break_even_reuses is None

    # Repetitive: clearly good deal.
    assert demo["repetitive"].net_savings > 0
    assert demo["repetitive"].roi > 0
    assert demo["repetitive"].break_even_reuses < 5  # recoups fast at this per-task saving

    # Mixed: positive overall (the reuse slice carries the novel slice's
    # small overhead), but weaker than pure-repetitive.
    assert demo["mixed"].net_savings > 0
    assert demo["mixed"].net_savings < demo["repetitive"].net_savings

    # Long-lived/changing-environment: still net positive despite elevated
    # per-task revalidation overhead, but with a real (not None) break-even.
    assert demo["long_lived_changing_environment"].net_savings > 0
    assert demo["long_lived_changing_environment"].break_even_reuses is not None
