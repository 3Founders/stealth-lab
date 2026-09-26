"""Every default of the recommender in one place. Each can be overridden with the
environment variable named next to it (STEALTH_ROUTING_*)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


CHECK_KINDS = ("benchmark", "tests", "procedure_check", "judge", "self_report")
# Roles of a Procedure step when a single step (a run.md node) is routed on its own.
STEP_ROLES = ("plan", "edit", "verify", "other")

# Prior Beta(a, b) on each check's false-accept (alpha) and false-reject (beta) rate.
# 'benchmark' DEFINES correctness (plan §0.4): both rates are exactly 0 and not learned.
CHECK_PRIORS: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "tests":           ((1.0, 19.0), (1.0, 19.0)),   # ~5% false accept, ~5% false reject
    "procedure_check": ((2.0, 18.0), (1.0, 19.0)),   # ~10% / ~5%
    "judge":           ((2.0, 8.0),  (2.0, 8.0)),    # ~20% / ~20%
    "self_report":     ((3.0, 7.0),  (1.0, 9.0)),    # ~30% / ~10%
}


@dataclass(frozen=True)
class RoutingDefaults:
    # --- model structure
    latent_dims: int = field(default_factory=lambda: _i("STEALTH_ROUTING_LATENT_DIMS", 4))        # K: skill dimensions
    embedding_dims: int = field(default_factory=lambda: _i("STEALTH_ROUTING_EMBED_DIMS", 16))     # P: PCA of Goal embeddings
    draws: int = field(default_factory=lambda: _i("STEALTH_ROUTING_DRAWS", 256))                  # S: stored posterior draws
    # --- inference
    nightly_warmup: int = field(default_factory=lambda: _i("STEALTH_ROUTING_NIGHTLY_WARMUP", 500))
    nightly_chains: int = field(default_factory=lambda: _i("STEALTH_ROUTING_NIGHTLY_CHAINS", 4))
    nightly_samples: int = field(default_factory=lambda: _i("STEALTH_ROUTING_NIGHTLY_SAMPLES", 500))  # per chain; thinned to S
    nuts_max_latents: int = field(default_factory=lambda: _i("STEALTH_ROUTING_NUTS_MAX_LATENTS", 20000))  # above: flow VI
    local_global_draws: int = field(default_factory=lambda: _i("STEALTH_ROUTING_LOCAL_GLOBAL_DRAWS", 16))
    local_warmup: int = field(default_factory=lambda: _i("STEALTH_ROUTING_LOCAL_WARMUP", 300))
    # --- quadrature
    gh_eps_nodes: int = 20        # Gauss-Hermite nodes over the instance difficulty eps
    # --- decision
    max_rungs: int = field(default_factory=lambda: _i("STEALTH_ROUTING_MAX_RUNGS", 3))
    max_candidates: int = field(default_factory=lambda: _i("STEALTH_ROUTING_MAX_CANDIDATES", 16))
    reliability_target: float = field(default_factory=lambda: _f("STEALTH_ROUTING_RHO", 0.90))       # rho
    reliability_confidence: float = field(default_factory=lambda: _f("STEALTH_ROUTING_CONFIDENCE", 0.90))  # 1 - delta
    value_multiplier: float = field(default_factory=lambda: _f("STEALTH_ROUTING_VALUE_MULTIPLIER", 5.0))
    wrong_penalty_ratio: float = field(default_factory=lambda: _f("STEALTH_ROUTING_WRONG_PENALTY_RATIO", 1.0))  # L / V
    default_check_kind: str = field(default_factory=lambda: os.environ.get("STEALTH_ROUTING_CHECK_KIND", "tests"))
    # --- token prior (used until data exists; pooled away by data, plan §4)
    prior_tokens_in: float = field(default_factory=lambda: _f("STEALTH_ROUTING_PRIOR_TOKENS_IN", 60000.0))
    prior_tokens_out: float = field(default_factory=lambda: _f("STEALTH_ROUTING_PRIOR_TOKENS_OUT", 6000.0))
    prior_log_sd: float = 1.0          # spread of log-tokens around the prior mean
    token_pooling_strength: float = 5.0  # kappa: observations needed before a group's own mean dominates


DEFAULTS = RoutingDefaults()


def unit_id(model_key: str, scaffold: str) -> str:
    return f"{model_key}|{scaffold}"


def split_unit(unit: str) -> tuple[str, str]:
    model_key, _, scaffold = unit.partition("|")
    if not model_key or not scaffold:
        raise ValueError(f"unit must be 'model_key|scaffold', got {unit!r}")
    return model_key, scaffold
