"""
Cost model + ROI/break-even calculator (evaluation-suite Phase 6, spec
section 26). Pure functions over recorded/estimated cost data -- this
module never spends anything and never invents a number: LLM-call cost
comes from openrouter_arms.SpendLog's real JSONL rows (spend_log_cost()),
and every other cost category spec section 26 names (ingestion,
embedding, extraction, claim processing, generalization, storage,
revalidation, execution overhead) is nothing this codebase tracks
anywhere yet, so this module takes them as explicit caller-supplied
inputs rather than guessing. Building/wiring that tracking, and running
this against a live workload, is future work -- see evaluation/README.md's
Known limitations. This pass builds the calculator and proves it correct
against synthetic numbers.

Nothing here imports backend/** (lane rule, same as the rest of this
directory).

Definitions match evaluation/METRICS.md's Economics section exactly:
  gross_savings = baseline_cost - stealth_cost (ignoring the one-time
                  memory-building cost)
  net_savings   = gross_savings - amortized memory-building cost
  ROI           = net_savings / stealth_cost
  break_even_reuses = the number of task reuses before net_savings
                  crosses zero
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryBuildingCost:
    """One-time cost to build the procedural memory a workload will reuse."""

    ingestion: float = 0.0
    embedding: float = 0.0
    extraction: float = 0.0
    claim_processing: float = 0.0
    generalization: float = 0.0

    @property
    def total(self) -> float:
        return (self.ingestion + self.embedding + self.extraction
                + self.claim_processing + self.generalization)


@dataclass
class PerTaskStealthOverhead:
    """Recurring, per-task cost Stealth adds on top of baseline execution
    cost -- add PerTaskStealthOverhead.total to a baseline per-task cost
    to get stealth_cost_per_task for evaluate_workload(), when a caller
    wants the categories broken out rather than pre-summed.

    durable_retry_overhead and product_model_read_overhead are additive
    (evaluation-suite Phase 8b, task spec §20): the hardened Final-V1
    candidate's two new recurring cost surfaces this model didn't have a
    labeled slot for -- durable per-node retry/resume bookkeeping
    (app/execution/durable_run.py) and the extra Problem/Benchmark/
    Solution/Evaluation reads find_best_way's product-model path now does
    (app/services/product_model.py) beyond what execution_overhead already
    named. Labeled input fields only, same as every other category here --
    no calculation logic changes, no real number computed or claimed.
    """

    retrieval: float = 0.0
    storage: float = 0.0
    revalidation: float = 0.0
    execution_overhead: float = 0.0
    durable_retry_overhead: float = 0.0
    product_model_read_overhead: float = 0.0

    @property
    def total(self) -> float:
        return (self.retrieval + self.storage + self.revalidation
                + self.execution_overhead + self.durable_retry_overhead
                + self.product_model_read_overhead)


def spend_log_cost(spend_rows: list[dict]) -> float:
    """Sum of cost_usd across real openrouter_arms.SpendLog rows -- the
    LLM-call component of any cost figure below. spend_rows is whatever
    SpendLog.rows (or a JSONL file read back into dicts) actually holds;
    this never re-derives cost from tokens itself, it trusts the ledger."""
    return round(sum(r.get("cost_usd", 0.0) for r in spend_rows), 6)


@dataclass
class WorkloadResult:
    baseline_cost: float
    stealth_cost: float
    gross_savings: float
    net_savings: float
    roi: float | None  # None when stealth_cost == 0 (undefined, not infinite)
    break_even_reuses: float | None  # None when per-task cost never recoups the build cost


def evaluate_workload(
    *,
    baseline_cost_per_task: float,
    stealth_cost_per_task: float,
    num_tasks: int,
    memory_building_cost: MemoryBuildingCost | float = 0.0,
) -> WorkloadResult:
    """Compare running `num_tasks` similar tasks under baseline vs under
    Stealth, which pays `memory_building_cost` once up front then
    `stealth_cost_per_task` per task thereafter (already inclusive of
    whatever per-task Stealth overhead the caller measured/estimated --
    see PerTaskStealthOverhead for the category breakdown).

    Every workload shape spec section 26 names is this same function with
    different inputs: a one-off is num_tasks=1 (break_even_reuses will
    almost always exceed 1, honestly showing a one-off rarely recoups its
    build cost); repetitive is a large num_tasks with a real per-task
    saving; mixed is this function called once per task type and the
    results combined by the caller; long-lived/changing-environment is
    this function called at successive checkpoints with a rising
    stealth_cost_per_task (revalidation cost creeping up as the
    environment drifts) -- see demo_workload_shapes() below for all four,
    worked against synthetic numbers.
    """
    build_cost = (
        memory_building_cost.total if isinstance(memory_building_cost, MemoryBuildingCost)
        else memory_building_cost
    )

    baseline_cost = baseline_cost_per_task * num_tasks
    stealth_cost = stealth_cost_per_task * num_tasks + build_cost

    gross_savings = (baseline_cost_per_task - stealth_cost_per_task) * num_tasks
    net_savings = gross_savings - build_cost

    roi = (net_savings / stealth_cost) if stealth_cost > 0 else None

    per_task_saving = baseline_cost_per_task - stealth_cost_per_task
    if per_task_saving > 0:
        break_even_reuses = build_cost / per_task_saving
    elif build_cost == 0:
        # Nothing to recoup, so it's break-even from the first task even
        # though there's no per-task saving to speak of.
        break_even_reuses = 0.0
    else:
        # stealth_cost_per_task >= baseline_cost_per_task: this workload
        # NEVER recoups the build cost, no matter how many times it runs.
        # Reporting None here (not a huge finite number, not infinity) is
        # the honest answer -- a caller must not average this away.
        break_even_reuses = None

    return WorkloadResult(
        baseline_cost=round(baseline_cost, 6),
        stealth_cost=round(stealth_cost, 6),
        gross_savings=round(gross_savings, 6),
        net_savings=round(net_savings, 6),
        roi=round(roi, 6) if roi is not None else None,
        break_even_reuses=round(break_even_reuses, 3) if break_even_reuses is not None else None,
    )


def cumulative_curve(
    *, baseline_cost_per_task: float, stealth_cost_per_task: float,
    memory_building_cost: float, max_tasks: int,
) -> list[tuple[int, float, float]]:
    """spec section 26's 'compounding curve': for n = 1..max_tasks,
    (n, cumulative_baseline_cost, cumulative_stealth_cost) -- lets a
    caller find/plot the exact crossover point directly, rather than
    only reading the single break_even_reuses summary number."""
    if max_tasks < 1:
        raise ValueError("max_tasks must be >= 1")
    return [
        (
            n,
            round(baseline_cost_per_task * n, 6),
            round(stealth_cost_per_task * n + memory_building_cost, 6),
        )
        for n in range(1, max_tasks + 1)
    ]


def demo_workload_shapes() -> dict[str, WorkloadResult]:
    """Worked example against SYNTHETIC numbers (never live data) for the
    four workload shapes spec section 26 explicitly names. Numbers are
    illustrative -- chosen to make each shape's qualitative story land,
    not measured from a real run. Real evaluation-results/v1-baseline
    numbers, once ingestion/embedding/extraction/etc. cost tracking
    exists, replace these inputs without changing this function's shape.
    """
    build = MemoryBuildingCost(
        ingestion=0.40, embedding=0.15, extraction=0.90,
        claim_processing=0.25, generalization=0.30,
    )  # total 2.00

    results = {}

    # One-off: a single task, never reused. Stealth pays the full build
    # cost for zero amortization -- this SHOULD come back a bad deal, and
    # showing that honestly is the point (not every workload benefits).
    results["one_off"] = evaluate_workload(
        baseline_cost_per_task=3.00, stealth_cost_per_task=3.20,
        num_tasks=1, memory_building_cost=build,
    )

    # Repetitive: the same procedure reused often, real per-task savings
    # from reuse (skip re-solving from scratch).
    results["repetitive"] = evaluate_workload(
        baseline_cost_per_task=3.00, stealth_cost_per_task=0.85,
        num_tasks=200, memory_building_cost=build,
    )

    # Mixed: half the tasks are genuinely novel (near-baseline cost, small
    # retrieval overhead only), half are real reuse hits -- combined by
    # calling evaluate_workload per slice and summing, exactly as a real
    # caller would for a heterogeneous workload.
    novel_slice = evaluate_workload(
        baseline_cost_per_task=3.00, stealth_cost_per_task=3.10,
        num_tasks=100, memory_building_cost=0.0,  # build cost charged once, on the reuse slice below
    )
    reuse_slice = evaluate_workload(
        baseline_cost_per_task=3.00, stealth_cost_per_task=0.85,
        num_tasks=100, memory_building_cost=build,
    )
    results["mixed"] = WorkloadResult(
        baseline_cost=round(novel_slice.baseline_cost + reuse_slice.baseline_cost, 6),
        stealth_cost=round(novel_slice.stealth_cost + reuse_slice.stealth_cost, 6),
        gross_savings=round(novel_slice.gross_savings + reuse_slice.gross_savings, 6),
        net_savings=round(novel_slice.net_savings + reuse_slice.net_savings, 6),
        roi=None,  # recomputed below once combined stealth_cost is known
        break_even_reuses=reuse_slice.break_even_reuses,
    )
    combined_stealth = results["mixed"].stealth_cost
    results["mixed"].roi = (
        round(results["mixed"].net_savings / combined_stealth, 6) if combined_stealth > 0 else None
    )

    # Long-lived / changing-environment: revalidation cost creeps up over
    # the workload's life as the environment drifts and previously-fresh
    # procedures need re-checking more often -- modeled as a per-task cost
    # that rises with num_tasks rather than staying flat, still a single
    # evaluate_workload() call since the function takes a scalar per-task
    # cost (a caller modeling a real rising curve would call
    # cumulative_curve() at successive checkpoints instead; shown here for
    # the checkpoint AFTER drift has meaningfully set in).
    results["long_lived_changing_environment"] = evaluate_workload(
        baseline_cost_per_task=3.00, stealth_cost_per_task=1.35,  # elevated by revalidation overhead
        num_tasks=150, memory_building_cost=build,
    )

    return results
