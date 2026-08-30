"""
Phase 2 of the local/global runtime split (product spec): unified
retrieval over BOTH a workspace's LocalProcedureStore and the remote
global MCP server's `search_procedures`, ranked by one real, explainable
policy.

STRUCTURAL PRIVACY GUARANTEE. A local procedure never leaves this
process: `orchestrate_unified_search` sends the remote server only
`task_description` / `invariant_bindings` / `require_verified` / `limit`
-- the same shape LocalAgentRunner.run() already sends today -- and never
serializes a LocalProcedureStore row into any remote call. Verified by
test_unified_retrieval_offline.py against the ACTUAL args a fake session
receives, not by convention.

RANKING POLICY (real, not a vague heuristic):
  1. HARD GATE, non-compensatory, same discipline applicability.py names
     for its own cascade: an inapplicable candidate from EITHER source is
     excluded before ranking, never merely scored low.
       - local candidates: app.local_agent.local_applicability.
         check_local_hard_constraints (this process's own DB-free cascade).
       - global candidates: trusted as already applicability-filtered by
         the remote search_procedures call (its own find_applicable_
         procedures cascade already ran server-side against Postgres --
         re-running it here is impossible without a DB connection this
         module structurally must not have, and would duplicate work the
         server already did). A global candidate carrying an explicit
         `"applicable": False` is still excluded defensively (lets a
         caller/test simulate a server-side rejection without a live
         round trip); one carrying no such key is trusted.
  2. Among survivors, rank by:
       a. verification_state tier: verified > candidate > retired.
       b. capability (successes / attempts from verification_stats,
          0.0 with zero attempts -- undefined-as-zero, not undefined-as-
          best), bucketed into CAPABILITY_TIE_BUCKET-wide bands so two
          candidates within the same band count as "comparably capable"
          rather than let float noise decide the whole ranking.
       c. freshness: 'fresh' ranks above 'revalidating' (a stale
          candidate never reaches this stage -- it was excluded at the
          hard gate).
       d. specificity tie-break: a procedure's own scope_type, ranked by
          its position in v0_gate.SCOPE_TYPES (broad -> narrow, e.g.
          'global' before 'repository' before 'entity') -- a repository-
          scoped local procedure legitimately outranks a same-tier,
          comparably-capable global/generic one, and the reverse is
          equally true (a global procedure with the SAME specificity or
          higher, e.g. an 'entity'-scoped global one, is never penalized
          just for being global). Neither source wins by construction --
          only by what its own row actually says.
     Ties remaining after (a)-(d) preserve input order (Python's stable
     sort) -- local candidates are listed before global ones in the
     merge, so a genuine, otherwise-indistinguishable tie favors
     whichever this process already knows about first, not an unstated
     "prefer local" rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from app.local_agent.local_applicability import check_local_hard_constraints
from app.local_agent.local_store import LocalProcedureStore
from app.services.v0_gate import SCOPE_TYPES

CAPABILITY_TIE_BUCKET = 0.05

_VERIFICATION_RANK = {"verified": 2, "candidate": 1, "retired": 0}
_FRESHNESS_RANK = {"fresh": 1, "revalidating": 0}


@dataclass
class RankedCandidate:
    source: str  # "local" | "global"
    procedure: dict
    rank_key: tuple


def _capability_score(verification_stats: Optional[dict]) -> float:
    stats = verification_stats or {}
    attempts = stats.get("attempts", 0) or 0
    successes = stats.get("successes", 0) or 0
    if attempts <= 0:
        return 0.0
    return successes / attempts


def _specificity_rank(scope_type: Optional[str]) -> int:
    try:
        return SCOPE_TYPES.index(scope_type)
    except (ValueError, TypeError):
        return 0


def _rank_key(procedure: dict) -> tuple:
    verification_rank = _VERIFICATION_RANK.get(procedure.get("verification_state"), 0)
    capability_bucket = round(_capability_score(procedure.get("verification_stats")) / CAPABILITY_TIE_BUCKET)
    freshness_rank = _FRESHNESS_RANK.get(procedure.get("staleness", "fresh"), 0)
    specificity_rank = _specificity_rank(procedure.get("scope_type"))
    return (verification_rank, capability_bucket, freshness_rank, specificity_rank)


def rank_unified_candidates(
    local_candidates: list[dict],
    global_candidates: list[dict],
    *,
    current_scope: Optional[dict] = None,
    invariant_bindings: Optional[dict[str, float]] = None,
    require_verified: bool = True,
    limit: int = 10,
) -> list[RankedCandidate]:
    """
    Pure ranking policy over already-fetched candidate lists -- no
    network, no DB, easily unit-tested with fakes on both sides. See
    module docstring for the real policy.
    """
    survivors: list[RankedCandidate] = []

    for proc in local_candidates:
        result = check_local_hard_constraints(
            proc, current_scope=current_scope, require_verified=require_verified,
            invariant_bindings=invariant_bindings,
        )
        if not result.applicable:
            continue
        survivors.append(RankedCandidate(source="local", procedure=proc, rank_key=_rank_key(proc)))

    for proc in global_candidates:
        if proc.get("applicable") is False:
            continue
        survivors.append(RankedCandidate(source="global", procedure=proc, rank_key=_rank_key(proc)))

    survivors.sort(key=lambda c: c.rank_key, reverse=True)
    return survivors[:limit]


class _CallToolSession(Protocol):
    async def call_tool(self, name: str, args: dict) -> Any: ...


async def orchestrate_unified_search(
    session: _CallToolSession,
    store: LocalProcedureStore,
    *,
    task_description: str,
    current_scope: Optional[dict] = None,
    invariant_bindings: Optional[dict[str, float]] = None,
    require_verified: bool = True,
    limit: int = 10,
    query_embedding: Optional[list[float]] = None,
) -> list[RankedCandidate]:
    """
    Real orchestration: query the local store AND the remote MCP session's
    `search_procedures`, then rank the merged, applicability-gated
    candidate set via rank_unified_candidates.

    `session` is duck-typed to the one method this function actually
    calls (`call_tool`) -- the same real ClientSession
    LocalAgentRunner._open_client_session constructs, or a fake in tests.
    The remote call's payload is EXACTLY {"task", "require_verified",
    "limit", "invariant_bindings"} -- no local store content, ever (see
    module docstring's structural privacy guarantee).
    """
    import json

    local_hits = store.search_local_procedures(
        task_description, query_embedding=query_embedding, limit=limit,
    )

    search_result = await session.call_tool(
        "search_procedures",
        {
            "task": task_description,
            "require_verified": require_verified,
            "limit": limit,
            "invariant_bindings": json.dumps(invariant_bindings or {}),
        },
    )
    global_hits = json.loads(search_result.content[0].text)

    return rank_unified_candidates(
        local_hits, global_hits,
        current_scope=current_scope, invariant_bindings=invariant_bindings,
        require_verified=require_verified, limit=limit,
    )
