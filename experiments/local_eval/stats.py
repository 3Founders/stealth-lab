"""Pure statistics for the pre-registered analysis (PREREGISTRATION.md section 7). No I/O.

Paired design: every task is run in every arm, so arms are compared WITHIN task. Tasks from the same repo are
not independent, so confidence intervals use a repo-cluster bootstrap and the robustness p-value a repo-cluster
sign-flip test; the exact McNemar test is the pre-registered primary test.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy import stats as st


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar: b = only arm 1 succeeded, c = only arm 2 succeeded."""
    n = b + c
    if n == 0:
        return 1.0
    return float(min(1.0, 2 * st.binom.cdf(min(b, c), n, 0.5)))


def discordant(x: np.ndarray, y: np.ndarray) -> tuple[int, int]:
    """(b, c) for binary x (baseline) and y (treatment): b = x only, c = y only."""
    x, y = np.asarray(x, bool), np.asarray(y, bool)
    return int((x & ~y).sum()), int((~x & y).sum())


def odds_ratio_ci(b: int, c: int) -> tuple[float, float, float]:
    """Conditional (McNemar) odds ratio c/b with an exact CI from the binomial CI of c/(b+c)."""
    n = b + c
    if n == 0:
        return (float("nan"),) * 3
    lo = st.beta.ppf(0.025, c, b + 1) if c > 0 else 0.0
    hi = st.beta.ppf(0.975, c + 1, b) if b > 0 else 1.0
    f = lambda p: p / (1 - p) if p < 1 else float("inf")  # noqa: E731
    return (c / b if b else float("inf"), f(lo), f(hi))


def cohens_h(p1: float, p2: float) -> float:
    return 2 * math.asin(math.sqrt(p2)) - 2 * math.asin(math.sqrt(p1))


def _cluster_sums(d: np.ndarray, clusters: list[str]) -> tuple[np.ndarray, np.ndarray]:
    groups = defaultdict(list)
    for v, c in zip(d, clusters):
        groups[c].append(v)
    sums = np.array([sum(v) for v in groups.values()], dtype=float)
    sizes = np.array([len(v) for v in groups.values()], dtype=float)
    return sums, sizes


def cluster_bootstrap_ci(d, clusters: list[str], n_boot: int = 10000, seed: int = 0,
                         level: float = 0.95) -> tuple[float, float, float]:
    """Mean paired difference with a percentile CI, resampling whole repos (clusters) with replacement."""
    d = np.asarray(d, float)
    sums, sizes = _cluster_sums(d, clusters)
    rng = np.random.default_rng(seed)
    k = len(sums)
    idx = rng.integers(0, k, size=(n_boot, k))
    means = sums[idx].sum(1) / sizes[idx].sum(1)
    a = (1 - level) / 2
    return float(d.mean()), float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


def cluster_signflip_p(d, clusters: list[str], n_perm: int = 20000, seed: int = 0) -> float:
    """Two-sided randomisation test of mean difference = 0, flipping the sign of whole repos (valid under the
    null that arm labels are exchangeable within task)."""
    d = np.asarray(d, float)
    sums, sizes = _cluster_sums(d, clusters)
    obs = abs(sums.sum() / sizes.sum())
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(n_perm, len(sums)))
    null = np.abs((signs * sums).sum(1) / sizes.sum())
    return float((1 + (null >= obs - 1e-12).sum()) / (n_perm + 1))


def wilcoxon_p(d) -> float:
    d = np.asarray(d, float)
    if np.allclose(d, 0):
        return 1.0
    return float(st.wilcoxon(d, zero_method="wilcox").pvalue)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for r, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - r) * p))
        out[k] = running
    return out


def benjamini_hochberg(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, prev = {}, 1.0
    for r in range(m - 1, -1, -1):
        k, p = items[r]
        prev = min(prev, p * m / (r + 1))
        out[k] = min(1.0, prev)
    return out


def cohen_kappa(a: list[bool], b: list[bool]) -> float:
    a, b = np.asarray(a, bool), np.asarray(b, bool)
    if len(a) == 0:
        return float("nan")
    po = (a == b).mean()
    pe = a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean())
    return float((po - pe) / (1 - pe)) if pe < 1 else 1.0


def mcnemar_sample_size(p_disc: float, delta: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Tasks needed to detect a paired difference `delta` when a fraction `p_disc` of tasks are discordant
    (Connor 1987 normal approximation)."""
    za, zb = st.norm.ppf(1 - alpha / 2), st.norm.ppf(power)
    n = (za * math.sqrt(p_disc) + zb * math.sqrt(p_disc - delta ** 2)) ** 2 / delta ** 2
    return int(math.ceil(n))


def design_effect(mean_cluster_size: float, icc: float) -> float:
    return 1 + (mean_cluster_size - 1) * icc


def compare_binary(x, y, clusters: list[str], seed: int = 0) -> dict:
    """Full pre-registered comparison of two arms on a binary outcome (x = baseline, y = treatment)."""
    x, y = np.asarray(x, bool), np.asarray(y, bool)
    b, c = discordant(x, y)
    n = len(x)
    diff, lo, hi = cluster_bootstrap_ci(y.astype(float) - x.astype(float), clusters, seed=seed)
    orr, olo, ohi = odds_ratio_ci(b, c)
    return {"n": n, "rate_baseline": float(x.mean()), "rate_treatment": float(y.mean()),
            "baseline_ci": wilson(int(x.sum()), n), "treatment_ci": wilson(int(y.sum()), n),
            "diff": diff, "diff_ci": [lo, hi], "only_baseline": b, "only_treatment": c,
            "p_mcnemar": mcnemar_exact(b, c),
            "p_cluster_signflip": cluster_signflip_p(y.astype(float) - x.astype(float), clusters, seed=seed),
            "odds_ratio": orr, "odds_ratio_ci": [olo, ohi], "cohens_h": cohens_h(float(x.mean()), float(y.mean()))}


def compare_continuous(x, y, clusters: list[str], seed: int = 0) -> dict:
    """Paired comparison of a continuous outcome (score, tokens, cost): y - x."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    d = y - x
    diff, lo, hi = cluster_bootstrap_ci(d, clusters, seed=seed)
    sd = d.std(ddof=1) if len(d) > 1 else float("nan")
    return {"n": len(d), "mean_baseline": float(x.mean()), "mean_treatment": float(y.mean()),
            "median_baseline": float(np.median(x)), "median_treatment": float(np.median(y)),
            "diff": diff, "diff_ci": [lo, hi], "relative": diff / x.mean() if x.mean() else float("nan"),
            "cohens_dz": float(d.mean() / sd) if sd and sd > 0 else float("nan"),
            "p_wilcoxon": wilcoxon_p(d), "p_cluster_signflip": cluster_signflip_p(d, clusters, seed=seed)}
