"""Gauss-Hermite rules for expectations over a standard normal: E[f(X)], X ~ N(0, 1).

Used for the instance difficulty (epsilon) and the attempt noise (eta). Both are
one-dimensional, so quadrature is exact to high precision for the smooth logistic
integrands here -- no Monte Carlo error in the ladder maths."""
from __future__ import annotations

from functools import lru_cache

import numpy as np


@lru_cache(maxsize=16)
def standard_normal_rule(n: int) -> tuple[np.ndarray, np.ndarray]:
    """(nodes, weights) with sum(weights) == 1 and sum(w * f(x)) ~= E[f(X)]."""
    if n < 1:
        raise ValueError("need at least one node")
    nodes, weights = np.polynomial.hermite_e.hermegauss(n)
    weights = weights / weights.sum()
    nodes.setflags(write=False)
    weights.setflags(write=False)
    return nodes, weights
