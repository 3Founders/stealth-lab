"""
Exact McNemar + power analysis for the §40 scoreboard.

Board rule being enforced here: "discordant-pair counts printed beside every
p-value; never bare point estimates". Every formatting helper in this module
takes the raw pair counts so a p-value CANNOT be rendered without its input.

Adapted from experiments/swebench_pro/run_graph_experiment.py::mcnemar:
exact binomial on the DISCORDANT pairs only — concordant pairs carry no
information about a difference. With zero discordant pairs there is no test,
and reporting p=1.0 would imply evidence of no difference when there is
simply no evidence, so that case returns p=None + an explanatory note.

Power analysis answers the question the reference run learned the hard way
(9 ansible instances -> ZERO discordant pairs -> "not 'no significant
difference', no information"): given n discordant pairs, what split could we
even have detected? Pure stdlib (math.comb), no scipy dependency.
"""
from __future__ import annotations

import math


def two_sided_exact_p(k_le: int, n: int) -> float:
    """Two-sided exact binomial p, small-tail doubling (the McNemar exact
    test statistic): p = min(1, 2 * P(X <= min(k, n-k)) under Bin(n, .5))."""
    k = min(k_le, n - k_le)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def mcnemar_exact(first_only: int, second_only: int) -> tuple[float | None, str]:
    """(p, note) for one paired comparison. p is None iff there are zero
    discordant pairs — the test has no input."""
    n = first_only + second_only
    if n == 0:
        return None, "no discordant pairs — the test has no input"
    p = two_sided_exact_p(min(first_only, second_only), n)
    note = (f"{n} discordant pairs "
            f"(first-arm-only {first_only}, second-arm-only {second_only})")
    return p, note


def rejection_region(n: int, alpha: float = 0.05) -> set[int]:
    """Discordant-count outcomes k (of n) that reject H0 at level alpha.
    Empty when n is too small to reach significance at all — itself the
    headline of the power footer in that regime."""
    return {k for k in range(n + 1) if two_sided_exact_p(k, n) <= alpha}


def power(n: int, q: float, alpha: float = 0.05) -> float:
    """Power against alternative 'first arm wins q of the n discordant
    pairs' (q > .5 favors the first arm; use 1-q symmetrically)."""
    if n == 0:
        return 0.0
    region = rejection_region(n, alpha)
    if not region:
        return 0.0
    return sum(math.comb(n, k) * (q ** k) * ((1 - q) ** (n - k))
               for k in region)


def min_detectable_q(n: int, alpha: float = 0.05,
                     target_power: float = 0.8) -> float | None:
    """Smallest asymmetry q > .5 detectable with `target_power` given exactly
    n discordant pairs. None when even q->1 cannot reach the target (or no
    rejection region exists) — report that honestly rather than a number."""
    if n == 0 or power(n, 1.0, alpha) < target_power:
        return None
    lo, hi = 0.5, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if power(n, mid, alpha) >= target_power:
            hi = mid
        else:
            lo = mid
    return hi


def required_n(q: float, alpha: float = 0.05, target_power: float = 0.8,
               max_n: int = 100_000) -> int | None:
    """Smallest total discordant-pair count reaching `target_power` against
    alternative q (probability the first arm wins a discordant pair). Assumes
    the observed ratio persists as more pairs accrue — the standard planning
    assumption for McNemar sample size. None beyond max_n."""
    if not (0.5 < q < 1.0):
        return None
    for n in range(4, max_n + 1):
        if power(n, q, alpha) >= target_power:
            return n
    return None


def discordant_counts(rows: list[dict], arm_first: str, arm_second: str,
                      outcome_key: str = "pass") -> tuple[int, int]:
    """(first_only_wins, second_only_wins) over rows where BOTH arms have the
    outcome key. Pairing discipline from the reference summarise(): a row
    missing either arm cannot contribute a paired comparison."""
    first_only = sum(
        1 for r in rows
        if r.get(arm_first, {}).get(outcome_key)
        and not r.get(arm_second, {}).get(outcome_key))
    second_only = sum(
        1 for r in rows
        if r.get(arm_second, {}).get(outcome_key)
        and not r.get(arm_first, {}).get(outcome_key))
    return first_only, second_only


def format_pair(name_first: str, name_second: str, first_only: int,
                second_only: int, alpha: float = 0.05,
                target_power: float = 0.8) -> str:
    """ONE line per comparison. The counts travel WITH the p-value — board
    rule: never a bare point estimate."""
    label = f"{name_first} vs {name_second}"
    p, note = mcnemar_exact(first_only, second_only)
    n = first_only + second_only
    head = f"{label}: {note}"
    if p is None:
        return f"{head} | p=N/A"

    parts = [head, f"exact-p={p:.4f}"]

    q_obs = max(first_only, second_only) / n
    pw_obs = power(n, q_obs, alpha)
    parts.append(f"power@observed-split(q={q_obs:.3f},n={n})={pw_obs:.2f}")

    mde_q = min_detectable_q(n, alpha, target_power)
    if mde_q is None:
        parts.append(f"MDE@{int(target_power * 100)}%: unreachable with {n} "
                     f"discordant pairs")
    else:
        k_equiv = math.ceil(mde_q * n)
        parts.append(f"MDE@{int(target_power * 100)}%: q>={mde_q:.3f} "
                     f"(~{k_equiv}/{n} pairs favoring one arm)")

    if q_obs <= 0.5:
        parts.append(f"n-for-{int(target_power * 100)}%: even split — no "
                     f"asymmetry to plan around")
    else:
        need = required_n(q_obs, alpha, target_power)
        parts.append(
            f"n-for-{int(target_power * 100)}%@observed-ratio="
            + ("beyond planning cap" if need is None else str(need)))

    return " | ".join(parts)


def format_footer(comparisons: list[tuple[str, str, int, int]],
                  alpha: float = 0.05,
                  target_power: float = 0.8) -> str:
    """The POWER-ANALYSIS FOOTER block: every comparison, counts attached."""
    lines = ["POWER-ANALYSIS FOOTER "
             f"(exact McNemar, alpha={alpha}, target power={target_power})"]
    lines += [
        "  " + format_pair(f, s, fo, so, alpha, target_power)
        for f, s, fo, so in comparisons]
    return "\n".join(lines)
