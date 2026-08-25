"""
Capability computation v1 (Band 1.9b / ROADMAP Band-2 item 3): levels as
a banding over P, per spec v4 §16 as amended by the RATIFIED D1 ruling
(BAND0_DECISIONS.md, founder quiz 2026-08-25).

Spec §16's single canonical representation:

    Capability = P(required outcome | state, procedure, implementation)

The ordinal ladder (0 unknown → 1 observed → 2 reproduced → 3 validated
→ 4 generalized → 5 trusted) is NOT a separate representation -- it is a
banding over P. All routing thresholds apply to **P itself**; the level
label is presentation, never a routing input (§16, verbatim). The two
named routing tiers are D1-ratified product behavior and live here ONLY
as named config constants -- never as inline literals at a call site.

WHY THIS MODULE LIVES IN procedure_extraction/: this lane's ownership is
`procedure_extraction/**` plus four named sibling files; a new top-level
services/capability.py would fall outside every owned path, and file
ownership is absolute (board rule). This package is where procedures are
born, so rating their reliability from outcome streams is its natural
downstream concern; relocation to services/capability.py is a one-line
import change for callers whenever ownership is redrawn (logged on the
board as a numbered question rather than decided unilaterally).

HONEST SCOPE:
- PURE computation over a caller-supplied outcome stream. No DB reads,
  no persistence: the [D] companion storage for computed capability is
  CORE-A's territory (1.9a evidence table / schema needs route through
  that lane; board rule: no new migrations from this lane).
- P is estimated as the LOWER bound of the Wilson score interval at the
  named z. Spec §16 says level bands correspond to "P intervals whose
  bounds tighten as evidence volume grows (sequential-testing
  semantics)" -- an interval estimate is exactly that. Banding and
  routing consume the LOWER bound deliberately: the optimistic upper
  bound would let three lucky successes auto-route (UB=1.0) while real
  uncertainty is huge, which inverts the system's fail-closed posture.
  A sequential test (SPRT) refinement is deferred; ticket 13's SPRT
  promotion logic in procedures.py stays untouched and authoritative
  for lifecycle transitions -- this module computes the ROUTING input,
  it does not own lifecycle.
- Not yet wired into retrieval/routing call sites. "Replaces raw
  counters as the routing input" is a caller-side migration (procedures
  verification_stats consumers), deliberately not smuggled into this
  change; nothing existing changes behavior until a caller switches.
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Named config -- D1 ratified numbers. These are THE definitions; no other
# module may restate them as literals (the tests prove they are read
# dynamically, i.e. retuning here retunes behavior everywhere).
# ---------------------------------------------------------------------------

# Routing tiers written into §16 by D1: auto-route >= 0.90 · offer
# 0.70–0.90 · refuse < 0.70. Applied to P, never the level label.
ROUTE_AUTO_THRESHOLD = 0.90
ROUTE_OFFER_THRESHOLD = 0.70

# D1 Option B band boundaries (level = band of P):
LEVEL_2_P_THRESHOLD = 0.50   # reproduced
LEVEL_3_P_THRESHOLD = 0.70   # validated
LEVEL_4_P_THRESHOLD = 0.85   # generalized
LEVEL_5_P_THRESHOLD = 0.95   # trusted

# Wilson score interval z for the default 95% two-sided interval. Named,
# not inlined: retuning the confidence retunes every band at once.
WILSON_Z_95 = 1.959963984540054

# §16 additional gates (per-level, all REQUIRED beyond the P band):
MIN_INDEPENDENT_GROUPS_FOR_REPRODUCED = 2   # L2: distinct independence groups
MIN_ENVIRONMENTS_FOR_GENERALIZED = 2        # L4/L5: holds in >= 2 environments

LEVEL_NAMES = {
    0: "unknown",
    1: "observed",
    2: "reproduced",
    3: "validated",
    4: "generalized",
    5: "trusted",
}


class OutcomeRecord(BaseModel):
    """
    One entry of an outcome stream feeding capability computation.

    `independence_group`: caller's grouping of outcomes that do NOT
    count as independent evidence of each other (e.g. same environment
    replay, same fixture). Only EXPLICIT groups establish independence
    for the L2 gate -- a None group contributes to P but can never
    satisfy ">= 2 independent executions", because absence of a stated
    group must not silently become a claim of independence (fail-closed,
    same posture as the applicability cascade).

    `environment`: where this execution ran. Drives the L4/L5 "holds in
    >= 2 environments" gate, counted as environments with >= 1 SUCCESS.

    `source_metadata`: passthrough provenance (model brand, latency,
    cost, ...). DELIBERATELY NEVER READ by any computation in this
    module -- Appendix C #12: identical outcome streams from different
    model brands MUST yield identical capability trajectories; brand is
    presentation/provenance, never an input. The field exists so callers
    don't stuff brand into success/environment to make it fit.
    """

    success: bool
    environment: str = Field(min_length=1)
    independence_group: Optional[str] = None
    source_metadata: dict = Field(default_factory=dict)


class CapabilityScope(BaseModel):
    """
    The conditioning context every capability record MUST carry --
    Appendix C #5: "Every capability has defined task + evaluation
    criterion" -> non-null task/state/environment/input context fields.
    An empty or whitespace-only value fails construction: a capability
    conditioned on nothing is not conservative, it is meaningless.
    """

    task: str = Field(min_length=1)
    state_signature: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    input_signature: str = Field(min_length=1)
    evaluation_criterion: str = Field(min_length=1)

    @field_validator("task", "state_signature", "environment",
                     "input_signature", "evaluation_criterion",
                     check_fields=True)
    @classmethod
    def _reject_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("capability scope fields must be non-blank")
        return v


class RoutingDecision(str, Enum):
    AUTO_ROUTE = "auto_route"
    OFFER_AS_CANDIDATE = "offer_as_candidate"
    REFUSE_REUSE = "refuse_reuse"


def wilson_interval(
    successes: int, total: int, z: float = WILSON_Z_95,
) -> tuple[float, float]:
    """
    Wilson score interval for a binomial proportion. total == 0 returns
    the maximum-ignorance interval (0, 1): no evidence supports no claim
    in either direction. Lower bound is clamped to [0, 1] only via the
    formula's natural range; no smoothing, no priors.
    """
    if total <= 0:
        return (0.0, 1.0)
    p_hat = successes / total
    z_sq = z * z
    denom = 1.0 + z_sq / total
    center = (p_hat + z_sq / (2 * total)) / denom
    half = (z * math.sqrt(p_hat * (1 - p_hat) / total + z_sq / (4 * total * total))) / denom
    lower = max(0.0, center - half)
    upper = min(1.0, center + half)
    return (lower, upper)


def band_for_p(p: float) -> int:
    """
    The D1 band boundaries applied to a P value ALONE -- pure ladder, no
    gates (gates cap the level in compute_capability). Level 1 is any
    positive P with evidence; callers handle the no-evidence case before
    banding (it is level 0 regardless of any interval endpoint).
    """
    if p >= LEVEL_5_P_THRESHOLD:
        return 5
    if p >= LEVEL_4_P_THRESHOLD:
        return 4
    if p >= LEVEL_3_P_THRESHOLD:
        return 3
    if p >= LEVEL_2_P_THRESHOLD:
        return 2
    return 1


def route_for_p(p: float) -> RoutingDecision:
    """
    §16 routing semantics on P itself. Reads the threshold CONSTANTS at
    call time so retuning the named config retunes behavior (proven by
    test); the level label appears nowhere in this function.
    """
    if p >= ROUTE_AUTO_THRESHOLD:
        return RoutingDecision.AUTO_ROUTE
    if p >= ROUTE_OFFER_THRESHOLD:
        return RoutingDecision.OFFER_AS_CANDIDATE
    return RoutingDecision.REFUSE_REUSE


class CapabilityRecord(BaseModel):
    """The [D] companion shape: everything computed, plus the context it
    is conditional on (#5). Level + label are PRESENTATION fields;
    `routing` is the only decision-bearing output, derived from P."""

    scope: CapabilityScope
    p_estimate: float                  # conservative: Wilson LOWER bound
    p_lower: float
    p_upper: float
    evidence_count: int
    success_count: int
    independent_groups: int            # explicit distinct groups seen
    environments_held: list[str]       # sorted envs with >= 1 success
    verification_plan_satisfied: bool
    completed_review: bool
    level: int                         # gated band, 0..5
    level_label: str                   # LEVEL_NAMES[level]
    routing: RoutingDecision           # from P alone


def compute_capability(
    outcomes: list[OutcomeRecord],
    scope: CapabilityScope,
    *,
    verification_plan_satisfied: bool = False,
    completed_review: bool = False,
) -> CapabilityRecord:
    """
    Compute the capability record for ONE conditioning context from its
    full cumulative outcome stream. Bidirectionality falls out of
    recomputation over the stream: appended failures lower the Wilson
    lower bound (demotion), later successes raise it again -- see
    capability_trajectory() for the per-step view the proving tests use.

    Gate semantics (§16 table, fail-closed interpretations documented):

    - L2 ">= 2 independent executions (distinct independence groups)":
      counts DISTINCT non-null independence_group values among ALL
      recorded outcomes. Un-grouped outcomes cannot establish
      independence (absence of a statement != a statement).
    - L4/L5 "holds in >= 2 environments": DISTINCT outcome environments
      containing at least one SUCCESS. Failures somewhere do not hold
      anywhere; successes only hold where they happened.
    - L5 additionally requires completed_review=True -- §16 verbatim:
      statistics alone never confer the highest trust tier.
    - L3 requires verification_plan_satisfied=True (caller-owned fact;
      modeling verification plans is not this module's job).
    """
    total = len(outcomes)
    successes = sum(1 for o in outcomes if o.success)

    independent_groups = len({o.independence_group for o in outcomes if o.independence_group})
    environments_held = sorted({o.environment for o in outcomes if o.success})

    p_lower, p_upper = wilson_interval(successes, total)
    # Conservative point estimate == the bound decisions consume. With no
    # evidence, wilson_interval returns (0, 1) -> estimate 0.0 -> level 0.
    p_estimate = p_lower

    if total == 0 or successes == 0:
        # Level 0 "unknown": no evidence, or no recorded achievement --
        # the ladder has no "known bad" rung, and §16's L1 interval is
        # strictly "> 0".
        band = 0
    else:
        band = band_for_p(p_estimate)

    gate_by_level = {
        2: independent_groups >= MIN_INDEPENDENT_GROUPS_FOR_REPRODUCED,
        3: verification_plan_satisfied,
        4: len(environments_held) >= MIN_ENVIRONMENTS_FOR_GENERALIZED,
        5: (
            completed_review
            and len(environments_held) >= MIN_ENVIRONMENTS_FOR_GENERALIZED
        ),
    }
    level = band
    while level > 1 and not gate_by_level.get(level, True):
        level -= 1

    return CapabilityRecord(
        scope=scope,
        p_estimate=p_estimate,
        p_lower=p_lower,
        p_upper=p_upper,
        evidence_count=total,
        success_count=successes,
        independent_groups=independent_groups,
        environments_held=environments_held,
        verification_plan_satisfied=verification_plan_satisfied,
        completed_review=completed_review,
        level=level,
        level_label=LEVEL_NAMES[level],
        routing=route_for_p(p_estimate),
    )


def capability_trajectory(
    outcomes: list[OutcomeRecord],
    scope: CapabilityScope,
    *,
    verification_plan_satisfied: bool = False,
    completed_review: bool = False,
) -> list[CapabilityRecord]:
    """
    The stream replayed cumulatively: record i is capability computed
    over outcomes[0..i]. This is the shape Appendix C #10 ("inject
    failure outcome -> capability level drops") and #12 ("identical
    outcome streams ... identical capability trajectories") are proven
    against -- a trajectory is comparable step-by-step, a final snapshot
    is not.
    """
    return [
        compute_capability(
            outcomes[: i + 1], scope,
            verification_plan_satisfied=verification_plan_satisfied,
            completed_review=completed_review,
        )
        for i in range(len(outcomes))
    ]
