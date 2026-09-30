"""Where a claim is written (storage layout v2, docs/storage_layout_v2.md).

A public claim lives with its Goal on the Goal's knowledge shard; a private/org claim, or any claim when no remote
knowledge shard exists, stays on the control database (K000) exactly as before. Placement only -- the claim is
written by the unchanged `claims.capture_claim` (same validation, anchoring, embedding, edges), and a remote write
records its route and queues its search projection on the control database, the same two steps
`claim_identity.ingest_claim` performs for its remote writes.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


async def claim_home(pool: Any, *, goal_id: Optional[str], statement: str, visibility: str) -> str:
    """The shard a new claim is written to: its Goal's shard when that can take writes, else rendezvous placement
    (choose_child_shard's rollover); K000 for private/org claims and in a single-database deployment."""
    from app.services.shards import (HOME_SHARD, cached_shards, choose_child_shard, lookup_routes, multi_shard,
                                     writable_shards)

    if visibility != "public" or not await multi_shard(pool):
        return HOME_SHARD
    goal_home = None
    if goal_id:
        goal_home = (await lookup_routes(pool, "goal", [str(goal_id)])).get(str(goal_id))
        if goal_home is None and await pool.fetchval("SELECT 1 FROM goals WHERE id = $1::uuid", str(goal_id)):
            goal_home = HOME_SHARD
    return choose_child_shard(goal_home, f"claim:{goal_id or ''}:{statement}", writable_shards(
        await cached_shards(pool), visibility=visibility))


async def capture_claim_placed(pool: Any, *, goal_id: Optional[str] = None, **kwargs: Any) -> Optional[str]:
    """`capture_claim` on the claim's home shard. `pool` is the control database; `goal_id` defaults to
    properties['goal_id']. Returns the claim id (None when capture_claim declines, as before)."""
    from app.services.claims import capture_claim
    from app.services.search_projection import enqueue
    from app.services.shards import HOME_SHARD, pools_for, record_route

    props = kwargs.get("properties") or {}
    goal_id = goal_id or props.get("goal_id")
    home = await claim_home(pool, goal_id=goal_id, statement=str(kwargs.get("statement") or ""),
                            visibility=str(kwargs.get("visibility") or "public"))
    if home == HOME_SHARD:
        return await capture_claim(pool, **kwargs)           # unchanged path: triggers record route + outbox
    wpool = await pools_for(pool).get(home)
    cid = await capture_claim(wpool, **kwargs)
    if cid:
        await record_route(pool, "claim", str(cid), home)
        await enqueue(pool, "claim", str(cid))
    return str(cid) if cid else None
