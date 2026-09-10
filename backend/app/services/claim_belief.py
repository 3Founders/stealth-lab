"""
Claim belief engine (B8) + claim-status convergence (B9).

WHAT THIS CLOSES
----------------
`knowledge_nodes.belief_score REAL` / `belief_method TEXT` (migration 21)
had zero readers and zero writers in `app/` -- nothing turned a claim's
`evidence` rows (`target_type='claim'`, written by
`claim_evidence.record_claim_evidence`) into a belief number. And claim
status was represented two independent ways: `properties.truth_state`
(IN/OUT, flipped by `relate_claims`) plus a read-time
`get_claim_lifecycle_state()` compute, AND a separate `claim_status`
column written only by `procedure_extraction/failure_handlers.py`.

This module makes belief a real, evidence-derived number and makes
`claim_status` a DERIVED projection of (belief + open-conflict state) --
B9's convergence -- rather than an independently-written field.

SPEC POSTURE (§8 Step 2, §10/§11)
--------------------------------
- Observation confidence and claim belief are SEPARATE. Model / LLM
  confidence must never flow into belief. `compute_belief` consults NO
  input named `confidence` / `model_confidence`; a row may carry such a
  key and it is ignored (a test pins this).
- Belief is EVIDENCE-based only: evidence direction (supports /
  contradicts), evidence CLASS (executed >> documentary >> weak),
  strength_score, and INDEPENDENCE. Never producer identity / brand.
- Documentary-only support has a hard low ceiling: "the author says X"
  can never be promoted to "X works" no matter how many copies of the
  document exist (§8 Step 2). Encoded as `_CEILING_BY_CLASS`.
- A belief update CITES the evidence that caused it: `recompute_claim_
  belief` records a ChangeSet whose operation detail carries the exact
  `cited_evidence_ids` the number was computed from.

INDEPENDENCE DE-DUP (mandatory, mirrors db/24_evidence.sql)
----------------------------------------------------------
Aggregators over `evidence` count
`DISTINCT COALESCE(independence_group, id::text)` -- rows sharing a named
`independence_group` are ONE piece of evidence, never independent
corroboration of each other. `compute_belief` performs that collapse in
Python from the rows `get_claim_evidence` returns: each group contributes
once, taking that group's MAX `strength_score`, its strongest evidence
class, and (from the strongest row) its direction.

STATISTICAL APPROACH
--------------------
`procedure_extraction/capability.py` owns the Wilson-lower-bound /
capability-banding idiom for PROCEDURE reliability from an outcome
stream. Claim belief is a different quantity -- a signed aggregate of
for/against evidence weighted by class and strength, not a binomial
success rate -- so it is not a Wilson interval. What is reused verbatim
from that module in spirit: the non-compensatory, fail-closed posture
(absence of a stated independence group is never a claim of
independence) and the "identical evidence streams -> identical output,
brand-blind" rule (Appendix C #12). No new probability model is invented
here; the score is a bounded saturating function of net evidence weight.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import asyncpg

from app.services.access import TenantScope, tenant_transaction

logger = logging.getLogger(__name__)

# The named method stamp written into knowledge_nodes.belief_method. Bump
# the @vN suffix if the scoring rules below change in a way that makes old
# stored scores incomparable to new ones.
BELIEF_METHOD = "evidence_aggregate@v1"

# ---------------------------------------------------------------------------
# Evidence classes by epistemic weight (§11's nine types bucketed).
#   strong    -- a run actually happened / was reproduced / measured
#   document  -- somebody wrote it down / a human said so / an external src
#   weak      -- a bare observation, an artifact reference
# Unknown / unlisted types fall to `weak` (fail-closed).
# ---------------------------------------------------------------------------
_STRONG_TYPES = frozenset({"execution_result", "reproduction", "benchmark", "experiment"})
_DOCUMENT_TYPES = frozenset({"document", "external_source", "human_review"})
_WEAK_TYPES = frozenset({"observation", "artifact"})

_CLASS_WEIGHT = {"strong": 1.0, "document": 0.5, "weak": 0.3}
_CLASS_RANK = {"weak": 0, "document": 1, "strong": 2}

# Hard ceilings keyed by the STRONGEST supporting evidence class. A claim
# whose best support is only documentary can never read above "plausible"
# (§8 Step 2). A claim with only weak support is capped lower still.
_CEILING_BY_CLASS = {"strong": 1.0, "document": 0.5, "weak": 0.4}
# No independent support at all (only contradiction, or nothing countable):
# belief cannot rise out of the low "uncertain / source-derived" band.
_NO_SUPPORT_CEILING = 0.30

# Zero evidence rows -> a low floor, method still set. Never 0, never high:
# "uncertain / source-derived", not "known false".
_ZERO_EVIDENCE_FLOOR = 0.10

# Net-weight saturation constant: score = floor + (1-floor) * net/(net+K).
# K=1.0 => one independent strong support (net weight 1.0) lands at 0.55
# ("moderate"); two lands at 0.70; more climbs toward (but never reaches)
# the class ceiling.
_SATURATION_K = 1.0

# status_from_belief: a claim needs belief at/above this AND no
# contradiction to read as `supported` rather than `candidate`.
_SUPPORTED_STATUS_THRESHOLD = 0.65
_CANDIDATE_STATUS_FLOOR = 0.30


def _evidence_class(evidence_type: Optional[str]) -> str:
    if evidence_type in _STRONG_TYPES:
        return "strong"
    if evidence_type in _DOCUMENT_TYPES:
        return "document"
    return "weak"


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_belief(evidence_rows: list[dict]) -> dict:
    """
    PURE. No DB. Aggregate one claim's live evidence rows into a belief.

    Returns::

        {
          "score": float in [0, 1],
          "method": BELIEF_METHOD,
          "supporting_independent": int,
          "contradicting_independent": int,
          "strongest_supporting_type": str | None,
          "cited_evidence_ids": [str, ...],
        }

    Rules (see module docstring for the why):

    - Independence de-dup: rows are collapsed by
      `COALESCE(independence_group, <per-row key>)`. A group counts once,
      taking its MAX strength_score, its strongest evidence class, and the
      direction of its strongest row (strength tie between opposing
      directions -> `contradicts`, fail-closed).
    - Each independent group contributes weight
      `_CLASS_WEIGHT[class] * strength` to support or contradiction.
    - `net = support_weight - contra_weight`. For `net > 0`,
      `score = floor + (1-floor) * net/(net+K)` (saturating, never
      reaches 1). For `net <= 0` (contradiction dominates or balances),
      the score sits in a low "disputed" band near the floor.
    - Documentary-only support is capped at 0.5, weak-only at 0.4
      (`_CEILING_BY_CLASS`) -- more copies never lift it.
    - Zero evidence rows -> `_ZERO_EVIDENCE_FLOOR` (0.1), method still set.
    - NO input named `confidence` / `model_confidence` is read. Ever.
    """
    rows = list(evidence_rows or [])
    cited = [str(r["id"]) for r in rows if r.get("id") is not None]

    if not rows:
        return {
            "score": _ZERO_EVIDENCE_FLOOR,
            "method": BELIEF_METHOD,
            "supporting_independent": 0,
            "contradicting_independent": 0,
            "strongest_supporting_type": None,
            "cited_evidence_ids": [],
        }

    # ---- collapse independence groups to one entry each ----
    groups: dict[str, dict] = {}
    for i, r in enumerate(rows):
        raw_group = r.get("independence_group")
        key = raw_group if raw_group else f"__self_{i}"
        etype = r.get("evidence_type") or "observation"
        ecls = _evidence_class(etype)
        strength = _as_float(r.get("strength_score"), 1.0)
        strength = max(0.0, min(1.0, strength))
        direction = str(r.get("direction") or "supports").lower()
        if direction not in ("supports", "contradicts"):
            direction = "supports"

        g = groups.get(key)
        if g is None:
            groups[key] = {
                "strength": strength,
                "direction": direction,
                "class": ecls,
                "type": etype,
            }
            continue
        # Same group == ONE piece of evidence.
        if _CLASS_RANK[ecls] > _CLASS_RANK[g["class"]]:
            g["class"] = ecls
            g["type"] = etype
        if strength > g["strength"]:
            g["strength"] = strength
            g["direction"] = direction
        elif strength == g["strength"] and direction != g["direction"]:
            g["direction"] = "contradicts"  # fail-closed on a tie

    support_weight = 0.0
    contra_weight = 0.0
    supporting_independent = 0
    contradicting_independent = 0
    best_support: Optional[dict] = None

    for g in groups.values():
        w = _CLASS_WEIGHT[g["class"]] * g["strength"]
        if g["direction"] == "contradicts":
            contra_weight += w
            contradicting_independent += 1
        else:
            support_weight += w
            supporting_independent += 1
            rank = _CLASS_RANK[g["class"]]
            if best_support is None or (rank, w) > (best_support["rank"], best_support["weight"]):
                best_support = {"rank": rank, "weight": w, "type": g["type"]}

    net = support_weight - contra_weight

    if net > 0:
        frac = net / (net + _SATURATION_K)
        score = _ZERO_EVIDENCE_FLOOR + (1.0 - _ZERO_EVIDENCE_FLOOR) * frac
    else:
        # contradiction dominates or exactly balances support: a low
        # "disputed" band just above the floor, sliding down as
        # contradiction outweighs support.
        score = max(_ZERO_EVIDENCE_FLOOR, 0.35 + 0.10 * net)
        score = min(score, 0.45)

    # ---- ceilings ----
    if supporting_independent > 0:
        cls_name = {0: "weak", 1: "document", 2: "strong"}[best_support["rank"]]
        score = min(score, _CEILING_BY_CLASS[cls_name])
    else:
        score = min(score, _NO_SUPPORT_CEILING)

    score = round(max(0.0, min(1.0, score)), 4)

    return {
        "score": score,
        "method": BELIEF_METHOD,
        "supporting_independent": supporting_independent,
        "contradicting_independent": contradicting_independent,
        "strongest_supporting_type": best_support["type"] if best_support else None,
        "cited_evidence_ids": cited,
    }


# claim_status vocabulary (migration 21 kn_claim_status_chk):
#   candidate | supported | disputed | uncertain | stale | superseded
#   | invalid | retracted
# This function derives the FIRST FOUR from belief + conflict state. The
# terminal states (stale / superseded / invalid / retracted) are owned by
# other mechanisms (lifecycle staleness, relate_claims supersession,
# failure_handlers) and are never produced here -- B9 convergence is
# scoped to "is this claim believed, contested, or thin", not to the
# terminal lifecycle.
def status_from_belief(belief: dict, *, has_open_conflict: bool) -> str:
    """
    PURE. Map a `compute_belief` result + open-conflict flag onto one
    `claim_status` value.

      - has_open_conflict                          -> disputed
      - no evidence at all                         -> candidate
      - contradiction present, support weak/absent -> disputed
      - contradiction present, support dominates   -> supported (only if
        support >= 2x contradiction AND score >= threshold) else disputed
      - support only, score >= threshold           -> supported
      - support only, score >= candidate floor     -> candidate
      - support only, thin                         -> uncertain
    """
    si = int(belief.get("supporting_independent", 0))
    ci = int(belief.get("contradicting_independent", 0))
    score = float(belief.get("score", _ZERO_EVIDENCE_FLOOR))

    if has_open_conflict:
        return "disputed"
    if si == 0 and ci == 0:
        return "candidate"
    if ci >= 1 and si == 0:
        return "disputed"
    if ci >= 1:
        if si >= 2 * ci and score >= _SUPPORTED_STATUS_THRESHOLD:
            return "supported"
        return "disputed"
    # ci == 0, si >= 1
    if score >= _SUPPORTED_STATUS_THRESHOLD:
        return "supported"
    if score >= _CANDIDATE_STATUS_FLOOR:
        return "candidate"
    return "uncertain"


async def recompute_claim_belief(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    changeset_reason: str = "evidence changed",
    actor: str = "claim_belief",
) -> dict:
    """
    Read this claim's live evidence, recompute belief, and persist
    `belief_score` / `belief_method` / `claim_status` for the claim
    THROUGH A CHANGESET (belief is a `[V]` mutation, invariant #7).

    - Evidence is read via `claim_evidence.get_claim_evidence` -- the one
      claim-keyed reader; no ad-hoc SQL here.
    - `claim_status` is written as `status_from_belief(...)` -- the B9
      convergence: the column becomes a derived projection, not an
      independently-authored field.
    - The write runs inside `tenant_transaction(pool,
      TenantScope.commons())` so the tenant is bound (SET LOCAL) before
      the UPDATE.
    - The ChangeSet operation detail carries `cited_evidence_ids` -- the
      belief number names the evidence that produced it (§10/§11).
    - Idempotent in effect: same evidence -> same three values, so
      repeated calls converge and are safe. Each call does record a
      ChangeSet (every call site is a real evidence/relation change; the
      recompute audit trail is intentional, unlike mark_procedure_stale's
      dedup guard).

    Returns the `compute_belief` dict.
    """
    from app.services.claim_evidence import get_claim_evidence

    evidence_rows = await get_claim_evidence(pool, claim_id)
    belief = compute_belief(evidence_rows)

    has_conflict = await _has_open_conflict(pool, claim_id)
    new_status = status_from_belief(belief, has_open_conflict=has_conflict)

    async with tenant_transaction(pool, TenantScope.commons()) as conn:
        await conn.execute(
            "UPDATE knowledge_nodes "
            "SET belief_score = $2, belief_method = $3, claim_status = $4 "
            "WHERE id = $1::uuid AND node_type = 'claim' AND t_invalid IS NULL",
            claim_id,
            belief["score"],
            belief["method"],
            new_status,
        )

    from app.services.changeset_record import ChangeOperation, record_change_set

    await record_change_set(
        pool,
        author=actor,
        reason=changeset_reason,
        operations=[
            ChangeOperation(
                operation="status_change",
                target_table="knowledge_nodes",
                target_id=str(claim_id),
                detail={
                    "belief_score": belief["score"],
                    "belief_method": belief["method"],
                    "claim_status": new_status,
                    "supporting_independent": belief["supporting_independent"],
                    "contradicting_independent": belief["contradicting_independent"],
                    # the evidence the number is computed from -- §10/§11
                    "cited_evidence_ids": belief["cited_evidence_ids"],
                },
            )
        ],
    )
    return belief


async def _has_open_conflict(pool: asyncpg.Pool, claim_id: str) -> bool:
    """Lazy indirection to `claims.has_open_conflict_trigger` -- imported
    inside the function so `claims` <-> `claim_belief` stay free of an
    import cycle. A read failure here must not sink a belief recompute, so
    it degrades to "no known conflict"."""
    try:
        from app.services.claims import has_open_conflict_trigger

        return await has_open_conflict_trigger(pool, claim_id)
    except Exception:  # pragma: no cover - defensive
        logger.warning("recompute_claim_belief: open-conflict check failed for %s", claim_id)
        return False


async def get_claim_belief(pool: asyncpg.Pool, claim_id: str) -> Optional[dict]:
    """
    Return the STORED belief for a claim (does not recompute).

      {"belief_score": float|None, "belief_method": str|None,
       "claim_status": str|None, "stale": bool}

    `stale` is True when `belief_method IS NULL` -- belief was never
    computed for this claim. Returns None when `claim_id` does not resolve
    to a live claim row.
    """
    row = await pool.fetchrow(
        "SELECT belief_score, belief_method, claim_status FROM knowledge_nodes "
        "WHERE id = $1::uuid AND node_type = 'claim' AND t_invalid IS NULL",
        claim_id,
    )
    if row is None:
        return None
    return {
        "belief_score": row["belief_score"],
        "belief_method": row["belief_method"],
        "claim_status": row["claim_status"],
        "stale": row["belief_method"] is None,
    }
