"""
Implementation selection.

    probe cache (task + input fingerprint)
      hit  -> return the cached (implementation, params)
      miss -> candidates = enabled implementations for the task
              drop those measured below the quality bar
              sort by (cost_estimate, latency_estimate_ms)
              return the first, with the rest as the escalation order

**The routing decision is a lookup, never an inference call.** Using a model
to decide whether to use a model spends the saving the routing exists to
capture, and adds a failure mode to the one component that has to be
dependable.

The store is a protocol rather than a pool so the selection logic is testable
with no database -- which matters, because selection order and the escalation
cap are exactly the sort of thing that quietly regresses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from uuid import UUID

from app.config import settings
from app.models.task import Implementation
from app.services.cache import CacheHit

log = logging.getLogger(__name__)


class RouterStore(Protocol):
    async def enabled_implementations(self, task_node_id: UUID) -> list[Implementation]: ...

    async def latest_eval_scores(self, task_node_id: UUID) -> dict[UUID, float]: ...

    async def probe_cache(self, task_node_id: UUID, fingerprint: str) -> Optional[CacheHit]: ...

    async def record_cache_hit(self, entry_id: UUID) -> None: ...


@dataclass
class RouteDecision:
    selected: Optional[Implementation] = None
    # Escalation order: the next-cheapest candidates, already filtered and
    # sorted. The executor walks these when a stage fails its criteria.
    alternatives: list[Implementation] = field(default_factory=list)
    from_cache: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def found(self) -> bool:
        return self.selected is not None


class Router:
    def __init__(self, store: RouterStore, tolerance: Optional[float] = None):
        self._store = store
        self._tolerance = settings.quality_bar_tolerance if tolerance is None else tolerance

    async def route(
        self,
        task_node_id: UUID,
        fingerprint: Optional[str] = None,
        quality_bar: Optional[float] = None,
        max_cost: Optional[float] = None,
        ignore_cache: bool = False,
    ) -> RouteDecision:
        # A cache hit skips candidate selection entirely, which also skips
        # the quality bar and the cost ceiling. That is fine when the
        # caller supplied neither, and wrong when they did: the same
        # request would then succeed or fail purely on cache state, and
        # the constraint would be honoured only on the cold path.
        constrained = quality_bar is not None or max_cost is not None
        if fingerprint and not ignore_cache and not constrained:
            hit = await self._store.probe_cache(task_node_id, fingerprint)
            if hit is not None:
                await self._store.record_cache_hit(hit.entry_id)
                impl = Implementation(
                    id=hit.implementation_id,
                    task_node_id=task_node_id,
                    name="(cached)",
                    kind="python",  # overwritten by the executor's hydration
                    spec={},
                )
                return RouteDecision(
                    selected=impl,
                    alternatives=[],
                    from_cache=True,
                    params=dict(hit.params),
                    reason=f"cache hit ({hit.hits} prior)",
                )

        candidates = await self._store.enabled_implementations(task_node_id)
        if not candidates:
            return RouteDecision(reason="no enabled implementation for this task")

        scores = await self._store.latest_eval_scores(task_node_id)
        eligible, reason = self._apply_quality_bar(candidates, scores, quality_bar)
        if not eligible:
            return RouteDecision(reason=reason)

        if max_cost is not None:
            affordable = [c for c in eligible if c.cost_estimate <= max_cost]
            if affordable:
                eligible = affordable
            else:
                return RouteDecision(
                    reason=f"every implementation exceeds max_cost {max_cost}"
                )

        ordered = sorted(eligible, key=lambda i: (i.cost_estimate, i.latency_estimate_ms))
        return RouteDecision(
            selected=ordered[0],
            alternatives=ordered[1:],
            from_cache=False,
            reason=reason,
        )

    def _apply_quality_bar(
        self,
        candidates: list[Implementation],
        scores: dict[UUID, float],
        quality_bar: Optional[float],
    ) -> tuple[list[Implementation], str]:
        if not scores:
            # No eval has ever run for this task. Order by cost and let
            # escalation deal with a cheap implementation that turns out not
            # to work -- which is the only way a first score gets recorded.
            return candidates, "no eval data; ordered by cost"

        bar = quality_bar if quality_bar is not None else max(scores.values()) - self._tolerance

        # An implementation with no recorded score is unmeasured, not failed.
        # Excluding it would mean a newly registered implementation could
        # never run, so could never be scored, so could never be included.
        eligible = [c for c in candidates if scores.get(c.id, bar) >= bar]

        if not eligible:
            # A bar the caller asked for is a constraint; a bar we derived
            # from eval history is a heuristic. Ignoring the caller's would
            # silently run something they explicitly excluded, so that case
            # fails the stage instead.
            if quality_bar is not None:
                return [], f"no implementation meets the requested quality bar {bar:.3f}"
            log.warning(
                "no implementation clears the derived quality bar %.3f; falling back "
                "to the full candidate set rather than failing the stage untried",
                bar,
            )
            return candidates, f"nothing cleared the derived bar {bar:.3f}; ignoring it"

        return eligible, f"cleared quality bar {bar:.3f}"


class PostgresRouterStore:
    """The real store. `Router` never sees a connection."""

    def __init__(self, pool, cache_store):
        self._pool = pool
        self._cache = cache_store

    async def enabled_implementations(self, task_node_id: UUID) -> list[Implementation]:
        rows = await self._pool.fetch(
            """
            SELECT id, task_node_id, name, kind, spec, cost_estimate,
                   latency_estimate_ms, enabled
            FROM implementations
            WHERE task_node_id = $1 AND enabled AND t_invalid IS NULL
            """,
            task_node_id,
        )
        return [Implementation.from_row(r) for r in rows]

    async def latest_eval_scores(self, task_node_id: UUID) -> dict[UUID, float]:
        """
        The most recent score per implementation for this task.

        DISTINCT ON rather than a correlated subquery per implementation:
        one round trip regardless of how many implementations a task has.
        """
        rows = await self._pool.fetch(
            """
            SELECT DISTINCT ON (r.implementation_id) r.implementation_id, r.score
            FROM eval_results r
            JOIN implementations i ON i.id = r.implementation_id
            WHERE i.task_node_id = $1
            ORDER BY r.implementation_id, r.ran_at DESC
            """,
            task_node_id,
        )
        return {r["implementation_id"]: float(r["score"]) for r in rows}

    async def probe_cache(self, task_node_id: UUID, fingerprint: str) -> Optional[CacheHit]:
        return await self._cache.probe(task_node_id, fingerprint)

    async def record_cache_hit(self, entry_id: UUID) -> None:
        await self._cache.record_hit(entry_id)

    async def implementation_by_id(self, implementation_id: UUID) -> Optional[Implementation]:
        row = await self._pool.fetchrow(
            """
            SELECT id, task_node_id, name, kind, spec, cost_estimate,
                   latency_estimate_ms, enabled
            FROM implementations WHERE id = $1 AND t_invalid IS NULL
            """,
            implementation_id,
        )
        return Implementation.from_row(row) if row else None

    async def implementation_by_name(
        self, task_node_id: UUID, name: str
    ) -> Optional[Implementation]:
        row = await self._pool.fetchrow(
            """
            SELECT id, task_node_id, name, kind, spec, cost_estimate,
                   latency_estimate_ms, enabled
            FROM implementations
            WHERE task_node_id = $1 AND name = $2 AND t_invalid IS NULL
            """,
            task_node_id,
            name,
        )
        return Implementation.from_row(row) if row else None
