"""Storage-capacity guard for knowledge shards (docs/sharding.md).

Each shard is a hosted database with a hard size limit (`knowledge_shards.
capacity_bytes`, e.g. a 500 MB Neon project). Placement spreads new objects evenly,
so without a guard every shard fills at about the same rate and the provider
starts refusing writes everywhere at once. The guard measures each shard and marks
it `full` once it crosses `STEALTH_SHARD_FULL_RATIO` (default 0.85) of its limit:
`full` is the existing rollover lever -- NEW objects are placed elsewhere, existing
ones stay and remain readable. It never un-marks a shard (an operator decides).

The control database (K000) is never marked full: private/org knowledge has no
other home and routing/projections live there. When it nears its limit the guard
reports `control_database_near_capacity`, for an operator to act on (larger plan,
retention, K000 placement weight 0).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from app.services.shards import HOME_SHARD, ShardUnavailable, list_shards, pools_for, set_shard_status

log = logging.getLogger(__name__)

SIZE_SQL = "SELECT pg_database_size(current_database())::bigint"


def full_ratio() -> float:
    try:
        value = float(os.environ.get("STEALTH_SHARD_FULL_RATIO", "0.85"))
    except ValueError:
        return 0.85
    return min(max(value, 0.05), 1.0)


async def enforce_shard_capacity(pool: Any, *, apply: bool = True, ratio: Optional[float] = None) -> list[dict[str, Any]]:
    """Measure every non-retired shard against its `capacity_bytes`; mark an
    `active` remote shard `full` at the threshold (when `apply`). Returns one report
    row per shard. A shard that cannot be reached is reported, never guessed."""
    threshold = full_ratio() if ratio is None else ratio
    pools = pools_for(pool)
    report: list[dict[str, Any]] = []
    for info in await list_shards(pool):
        if info.status == "retired":
            continue
        entry: dict[str, Any] = {"shard_id": info.shard_id, "status": info.status,
                                 "capacity_bytes": info.capacity_bytes, "action": None}
        try:
            shard_pool = pool if info.shard_id == HOME_SHARD else await pools.get(info.shard_id)
            size = int(await shard_pool.fetchval(SIZE_SQL))
        except (ShardUnavailable, OSError) as exc:
            entry["action"] = "unreachable"
            entry["error"] = str(exc)[:200]
            report.append(entry)
            continue
        entry["size_bytes"] = size
        if not info.capacity_bytes:
            entry["used_ratio"] = None
            report.append(entry)
            continue
        used = size / info.capacity_bytes
        entry["used_ratio"] = round(used, 4)
        if used >= threshold:
            if info.shard_id == HOME_SHARD:
                entry["action"] = "control_database_near_capacity"
                log.warning("control database at %.0f%% of its capacity", used * 100)
            elif info.status == "active":
                if apply:
                    await set_shard_status(pool, info.shard_id, "full")
                    log.warning("shard %s at %.0f%% of capacity: marked full (new objects roll over)",
                                info.shard_id, used * 100)
                entry["action"] = "marked_full" if apply else "would_mark_full"
        report.append(entry)
    return report
