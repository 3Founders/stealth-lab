"""The credit that must travel with content ingested under an attribution license (BLOCKERS I7).

CC BY 4.0 s3(a) requires the attribution notice to be given whenever the material is shared, so storing it on the
IngestionContext (migration 126) is not enough: every find_ways reply that hands out a Procedure derived from such
content carries that Procedure's notice. `attach_attribution` walks the reply once, looks the notices up in one
query per shard, and adds `"attribution": "<notice>"` next to each `procedure_id` that has one.

Never breaks a reply: a database without migration 126, an unreachable shard or any other failure means no notice is
added (logged). That is safe only because the ingestion gate admits attribution-required licenses solely on paths
that record the notice (`repo_license_policy.classify_spdx(records_attribution=True)`).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Iterator

log = logging.getLogger(__name__)

_NOTICES = (
    "SELECT DISTINCT ON (p.procedure_id) p.procedure_id::text, c.attribution->>'notice' "
    "FROM procedures p JOIN ingestion_contexts c ON c.id = p.ingestion_context_id "
    "WHERE p.procedure_id = ANY($1::uuid[]) AND c.attribution IS NOT NULL "
    "ORDER BY p.procedure_id"
)


def _holders(node: Any) -> Iterator[dict]:
    """Every dict in the reply that names a Procedure."""
    if isinstance(node, dict):
        if node.get("procedure_id"):
            yield node
        for value in node.values():
            yield from _holders(value)
    elif isinstance(node, list):
        for item in node:
            yield from _holders(item)


def _uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


async def notices_for(pool: Any, procedure_ids: list[str]) -> dict[str, str]:
    from app.services import shards

    ids = sorted({u for u in (_uuid(p) for p in procedure_ids) if u})
    if not ids:
        return {}
    found: dict[str, str] = {}
    for shard, p in await shards.all_pools(pool):
        try:
            for pid, notice in await p.fetch(_NOTICES, ids):
                if notice:
                    found.setdefault(pid, notice)
        except Exception as exc:  # noqa: BLE001 -- e.g. migration 126 not applied on this shard
            log.warning("attribution lookup skipped on shard %s: %r", shard, exc)
    return found


async def attach_attribution(pool: Any, body: dict) -> None:
    holders = list(_holders(body))
    if not holders:
        return
    try:
        notices = await notices_for(pool, [str(h["procedure_id"]) for h in holders])
    except Exception as exc:  # noqa: BLE001 -- the credit is an addition; the answer still stands
        log.warning("attribution lookup failed: %r", exc)
        return
    for holder in holders:
        notice = notices.get(_uuid(holder["procedure_id"]) or "")
        if notice:
            holder["attribution"] = notice
