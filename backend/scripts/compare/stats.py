"""Statistics for paired arm comparisons (docs/goal_router.md P2-07). Standard library only.

    python -m scripts.compare.stats --selfcheck     # a known effect recovers its sign; a zero effect's CI holds 0

paired_bootstrap: a percentile CI for the mean of paired differences (tool - procedure on the same task and attempt),
resampling the pairs. mcnemar: an exact two-sided test on the discordant pass/fail pairs. cost_per_success: the
total spend of an arm divided by its verified passes (infinite with no pass, never a division by zero dressed up as
a number). Deterministic: every random draw comes from a seeded generator.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass
from statistics import mean
from typing import Optional, Sequence

DEFAULT_RESAMPLES = 5000
DEFAULT_ALPHA = 0.05


@dataclass
class Interval:
    estimate: float
    low: float
    high: float
    n: int

    def contains(self, x: float) -> bool:
        return self.low <= x <= self.high

    def __str__(self) -> str:
        if self.n == 0:
            return "n/a (no pairs)"
        return f"{self.estimate:+.3f} [{self.low:+.3f}, {self.high:+.3f}] (n={self.n})"


def paired_bootstrap(diffs: Sequence[float], *, resamples: int = DEFAULT_RESAMPLES, alpha: float = DEFAULT_ALPHA,
                     seed: int = 7) -> Interval:
    d = [float(x) for x in diffs]
    n = len(d)
    if n == 0:
        return Interval(0.0, float("nan"), float("nan"), 0)
    est = mean(d)
    if n == 1 or len(set(d)) == 1:
        # one pair, or identical pairs: no spread to resample; the interval is the point (and says so via n)
        return Interval(est, est, est, n)
    rng = random.Random(seed)
    means = sorted(mean(rng.choices(d, k=n)) for _ in range(resamples))
    lo = means[max(0, int(math.floor(alpha / 2 * resamples)))]
    hi = means[min(resamples - 1, int(math.ceil((1 - alpha / 2) * resamples)) - 1)]
    return Interval(est, lo, hi, n)


@dataclass
class McNemar:
    tool_only: int          # pairs where the tool passed and the procedure failed
    procedure_only: int     # the reverse
    p_value: float

    def __str__(self) -> str:
        return f"tool-only passes {self.tool_only}, procedure-only passes {self.procedure_only}, exact p={self.p_value:.3g}"


def mcnemar(pairs: Sequence[tuple[bool, bool]]) -> McNemar:
    """pairs: (tool passed, procedure passed). Exact binomial test on the discordant pairs, two-sided."""
    b = sum(1 for t, p in pairs if t and not p)
    c = sum(1 for t, p in pairs if p and not t)
    n = b + c
    if n == 0:
        return McNemar(b, c, 1.0)
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return McNemar(b, c, min(1.0, 2 * tail))


def cost_per_success(costs: Sequence[float], passed: Sequence[bool]) -> float:
    wins = sum(1 for p in passed if p)
    total = float(sum(costs))
    return total / wins if wins else math.inf


def describe_cost(value: float) -> str:
    return "no verified success" if math.isinf(value) else f"${value:.4f}"


def selfcheck() -> bool:
    rng = random.Random(11)
    ok = True
    # a known effect: the tool is better by 0.3 on average, with noise
    known = [0.3 + rng.gauss(0, 0.2) for _ in range(60)]
    ci = paired_bootstrap(known)
    print(f"known +0.3 effect: {ci}  -> sign recovered: {ci.low > 0}")
    ok &= ci.low > 0
    # a known negative effect
    neg = [-0.25 + rng.gauss(0, 0.2) for _ in range(60)]
    ci = paired_bootstrap(neg)
    print(f"known -0.25 effect: {ci}  -> sign recovered: {ci.high < 0}")
    ok &= ci.high < 0
    # zero effect: symmetric noise around 0
    zero = [rng.gauss(0, 0.2) for _ in range(60)]
    ci = paired_bootstrap(zero)
    print(f"zero effect: {ci}  -> CI contains 0: {ci.contains(0.0)}")
    ok &= ci.contains(0.0)
    m = mcnemar([(True, False)] * 12 + [(False, True)] * 2 + [(True, True)] * 30)
    print(f"McNemar 12 vs 2 discordant: {m}  -> significant: {m.p_value < 0.05}")
    ok &= m.p_value < 0.05
    m = mcnemar([(True, False)] * 5 + [(False, True)] * 5)
    print(f"McNemar 5 vs 5 discordant: {m}  -> not significant: {m.p_value >= 0.05}")
    ok &= m.p_value >= 0.05
    print(f"cost per success with no pass: {describe_cost(cost_per_success([0.01, 0.02], [False, False]))}")
    print("selfcheck:", "ok" if ok else "FAILED")
    return bool(ok)


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Paired statistics for the comparison harness.")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args(argv)
    if args.selfcheck:
        sys.exit(0 if selfcheck() else 1)
    ap.print_help()


if __name__ == "__main__":
    main()
