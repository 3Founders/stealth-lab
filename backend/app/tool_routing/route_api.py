"""The HTTP shape of the goal router: Goal -> Way -> Tool, in one call.

Two surfaces serve it -- the REST app (`GET /v1/goals/route`, app/api/goals.py) and the MCP
server's desktop route (`GET /v1/goals/route`, app/mcp_server/room_agent_routes.py). Both call
this function, so the website, the desktop app and the CLI cannot drift.

It is deliberately read-only: no plan is compiled, nothing is written. The gate is not called
here either -- this answers "what are the ways and the tools for this task", and a call still
goes through `app.tool_routing.gate.authorize`.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

DEFAULT_LIMIT = 5
MAX_LIMIT = 20


async def route_payload(
    pool: Any,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    include_ways: bool = True,
    include_tools: bool = True,
    owner_id: Optional[str] = None,
    request_id: Optional[str] = None,
    scope: Any = None,
) -> dict[str, Any]:
    """The router's answer as plain JSON: `{query, goals: [GoalHit, ...]}`."""
    from app.tool_routing.goal_router import route_goal

    text = " ".join(str(query or "").split())[:2000]
    if not text:
        return {"query": "", "goals": []}
    k = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    hits = await route_goal(
        pool, text,
        limit=k,
        scope=scope,
        include_ways=include_ways,
        include_tools=include_tools,
        owner_id=owner_id,
        request_id=request_id,
    )
    payload: dict[str, Any] = {"query": text, "goals": [asdict(hit) for hit in hits]}
    if not hits and include_tools:
        # No Goal matched: the tool tier can still answer ("whatsapp" has no Goal in the
        # taxonomy). Say so, and show the tools it found rather than an empty page.
        from app.tool_routing.goal_router import search_tools

        payload["tools"] = [asdict(t) for t in await search_tools(pool, text, limit=max(k, 8))]
    return payload


async def rank_payload(
    pool: Any,
    query: str,
    *,
    limit: int = 10,
    scope: Any = None,
) -> dict[str, Any]:
    """Goals only, from the in-process catalog: the sub-second first tier.

    `route_payload` adds the ways and tools (and the tool-tier search). This one answers
    "which Goals does this task mean" without touching the network beyond the cached
    catalog refresh.
    """
    from app.tool_routing.goal_router import rank_goals

    text = " ".join(str(query or "").split())[:2000]
    if not text:
        return {"query": "", "goals": []}
    k = max(1, min(int(limit or 10), 50))
    hits = await rank_goals(pool, text, limit=k)
    return {"query": text, "goals": [asdict(hit) for hit in hits]}
