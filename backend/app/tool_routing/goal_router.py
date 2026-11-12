"""Goal-first routing: the task (Goal) is the routing key, not the tool.

A request is matched to a canonical Goal first (exact name, alias, full-text, optional vector),
then the Goal's live Procedures ("ways") and the tool cards mapped to that Goal are returned.
Tools are implementations; the Goal is what a person searches for.

The Goal index is sharded: `goal_search_index` / `goal_names` live on the control database and
the canonical Goal row lives on its home shard. This module uses the existing search path
(`app.services.goals.search_goals`, which hydrates from the home shard) and `shards.home_pool`
for the ways, so it works with the real deployment instead of assuming every Goal is on K000.

    from app.tool_routing.goal_router import route_goal
    hits = await route_goal(pool, "check what the page looks like after login")

Each hit is a GoalHit: goal fields, the ways (procedures) that achieve it, and the tool cards
mapped to it by `tool_card_goals` (db/186_tool_card_goals.sql).
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.services.access import AccessScope
from app.services.goals import normalize_goal_name, search_goals
from app.services.shards import HOME_SHARD, ShardUnavailable, pools_for

RRF_K = 60
EXACT_SCORE = 1.0
ALIAS_SCORE = 0.95
DEFAULT_LIMIT = 5
MAX_WAYS_PER_GOAL = 5
MAX_TOOLS_PER_GOAL = 8


@dataclass
class ToolHit:
    card_id: str
    name: str
    server: str
    title: str
    description: str
    source: str
    score: float
    rank_score: Optional[float] = None                         # P3-03, GOAL_RANKING=trust; None in the fit order
    rank_inputs: dict[str, Any] = field(default_factory=dict)  # trust, exploration, cost, latency, risk, trial
    trial: bool = False                                        # shown as "New - free trial" (P3-02)


@dataclass
class WayHit:
    procedure_id: str
    name: str
    steps: list[Any]
    preconditions: list[Any]
    verification_state: str
    availability: str
    approval_status: str = "submitted"   # submitted < proposed < approved; rejected is never shown
    cards: list[dict[str, Any]] = field(default_factory=list)  # procedure_tool_cards: the binding targets


@dataclass
class GoalHit:
    goal_id: str
    canonical_name: str
    description: str
    status: str
    score: float
    matched_by: str
    home_shard: str
    ways: list[WayHit] = field(default_factory=list)
    tools: list[ToolHit] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


def _hit(row: Any, score: float, matched_by: str) -> dict[str, Any]:
    return {
        "goal_id": str(row["id"]),
        "canonical_name": str(row["canonical_name"] or ""),
        "description": str(row.get("description") or row.get("short_description") or ""),
        "status": str(row.get("status") or ""),
        "score": float(score),
        "matched_by": matched_by,
        "home_shard": str(row.get("home_shard_id") or HOME_SHARD),
    }


def _merge(out: dict[str, dict[str, Any]], row: Any, score: float, matched_by: str) -> None:
    hit = _hit(row, score, matched_by)
    current = out.get(hit["goal_id"])
    if current is None or hit["score"] > current["score"]:
        out[hit["goal_id"]] = hit
    elif current["matched_by"] == "fts" and matched_by == "vector":
        current["matched_by"] = "fts+vector"


async def _exact(pool: Any, normalized: str, limit: int) -> list[Any]:
    """Exact name from the cross-shard `goal_names` registry, hydrated from the home shard."""
    rows = await pool.fetch(
        """
        SELECT goal_id::text AS id, home_shard_id, canonical_name
        FROM goal_names
        WHERE normalize_goal_name(canonical_name) = $1
        LIMIT $2
        """,
        normalized, limit,
    )
    if not rows:
        return []
    pools = pools_for(pool)
    out: list[Any] = []
    for row in rows:
        shard = str(row["home_shard_id"] or HOME_SHARD)
        try:
            owner = pool if shard == HOME_SHARD else await pools.get(shard)
            goal = await owner.fetchrow(
                """
                SELECT id::text AS id, canonical_name, description, status, home_shard_id
                FROM goals
                WHERE id = $1::uuid AND t_invalid IS NULL
                """,
                row["id"],
            )
        except ShardUnavailable:
            goal = None
        if goal is not None:
            out.append(goal)
    return out


async def _search(pool: Any, query: str, limit: int, query_embedding: Optional[Sequence[float]],
                  scope: AccessScope) -> list[Any]:
    """The existing goal search: lexical + optional vector, hydrated from the home shard."""
    return await search_goals(
        pool,
        query_text=query,
        query_embedding=list(query_embedding) if query_embedding is not None else None,
        scope=scope,
        limit=limit,
        require_procedures=False,
    )


async def _k000_full_text(pool: Any, query: str, limit: int) -> list[Any]:
    """OR full-text over the Goals that live on the control database itself (K000).

    `search_goals` uses the global search group; a deployment without its members configured
    (a local checkout, or before the group is backfilled) still has K000 Goals, including the
    25 tool-goal taxonomy Goals, and this keeps them searchable. The canonical name is weighted
    above the description so "Search the web" outranks a longer Goal that only shares "search".
    """
    from app.services.identity_resolution import fts_or_query

    q = fts_or_query(query)
    if not q:
        return []
    return await pool.fetch(
        """
        WITH d AS (
            SELECT id, canonical_name, description, status, home_shard_id,
                   setweight(to_tsvector('english', canonical_name), 'A')
                   || setweight(to_tsvector('english', COALESCE(description, '')), 'B') AS doc
            FROM goals
            WHERE t_invalid IS NULL
              AND status IN ('active', 'candidate')
              AND home_shard_id = $3
        )
        SELECT id::text AS id, canonical_name, description, status, home_shard_id,
               ts_rank_cd(doc, to_tsquery('english', $1)) AS rank
        FROM d
        WHERE doc @@ to_tsquery('english', $1)
        ORDER BY rank DESC
        LIMIT $2
        """,
        q, limit, HOME_SHARD,
    )


async def _ways(pool: Any, hits: Sequence[dict[str, Any]]) -> dict[str, list[WayHit]]:
    if not hits:
        return {}
    pools = pools_for(pool)
    out: dict[str, list[WayHit]] = {}
    for hit in hits:
        shard = str(hit.get("home_shard") or HOME_SHARD)
        try:
            owner = pool if shard == HOME_SHARD else await pools.get(shard)
            rows = await owner.fetch(
                """
                SELECT pr.id::text AS id, pr.name, pr.steps, pr.preconditions,
                       pr.verification_state::text AS verification_state,
                       pr.approval_status,
                       pr.availability::text AS availability,
                       COALESCE((SELECT jsonb_agg(jsonb_build_object(
                                            'card_id', pc.card_id, 'name', tc.name,
                                            'server', tc.server, 'step_order', pc.step_order,
                                            'score', pc.score)
                                        ORDER BY pc.step_order, pc.score DESC, pc.card_id)
                                 FROM procedure_tool_cards pc
                                 JOIN tool_cards tc ON tc.id = pc.card_id
                                 WHERE pc.procedure_id = pr.id), '[]'::jsonb) AS cards
                FROM procedures pr
                WHERE pr.achieves_goal_id = $1::uuid
                  AND pr.t_invalid IS NULL
                  AND pr.approval_status <> 'rejected'
                  AND pr.availability = 'active'
                ORDER BY (pr.verification_state = 'verified') DESC,
                         COALESCE((pr.verification_stats->>'successes')::int, 0) DESC,
                         COALESCE((pr.domain_payload->>'goal_score')::float, 0) DESC,
                         pr.t_created DESC
                LIMIT $2
                """,
                hit["goal_id"], MAX_WAYS_PER_GOAL,
            )
        except ShardUnavailable:
            rows = []

        def _cards(row: Any) -> list[dict[str, Any]]:
            cards = row["cards"]
            if isinstance(cards, str):
                try:
                    cards = json.loads(cards or "[]")
                except ValueError:
                    cards = []
            return list(cards or [])

        out[hit["goal_id"]] = [
            WayHit(
                procedure_id=str(row["id"]),
                name=str(row["name"] or ""),
                steps=list(row["steps"] or []),
                preconditions=list(row["preconditions"] or []),
                verification_state=str(row["verification_state"] or ""),
                availability=str(row["availability"] or ""),
                approval_status=str(row["approval_status"] or "submitted"),
                cards=_cards(row),
            )
            for row in rows
        ]
    return out


# -- P3-03: rank a goal's tools by verified reuse --------------------------------------------------------------------
#
#   rank = trust_lower_bound + exploration_bonus - cost_penalty - latency_penalty - risk_penalty
#
# Goal fit is a gate (a card must be mapped to the goal with at least GOAL_FIT_MIN), not the score. Trust is the Beta
# lower bound from app/tool_routing/trust.py (an unproven card sits at the prior's 0.05); the exploration bonus is
# EXPLORE / (1 + judged), so a new card can be tried but never outranks a proven one. Cost comes from tool_listings
# (P3-06) when it exists, latency from the tool arm's runs, risk from tool_policies (an unknown risk counts as R2).
# A provider-funded trial card appears only inside its share of requests (trials.offer), marked as a trial. Stars and
# "verified" are not inputs. GOAL_RANKING=trust turns this on; anything else keeps the Phase 1 order (goal fit, stars).
CANDIDATES_PER_GOAL = 40
GOAL_FIT_MIN = float(os.environ.get("GOAL_FIT_MIN", "1.0"))
EXPLORE = 0.05
RISK_PENALTY = {"R0": 0.0, "R1": 0.02, "R2": 0.1, "R3": 0.3, "R4": 0.5}
UNKNOWN_RISK = "R2"
TOOL_GOAL_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")   # scripts/map_tool_cards_to_goals.py


def trust_ranking() -> bool:
    return os.environ.get("GOAL_RANKING", "fit").strip().lower() == "trust"


def _goal_key(goal_id: str) -> str:
    """A tool-goal Goal's taxonomy key ("web"), else the uuid itself."""
    from app.tool_routing.goals import TOOL_GOALS

    for key in TOOL_GOALS:
        if str(uuid.uuid5(TOOL_GOAL_NS, f"kell:tool-goal:{key}")) == goal_id:
            return key
    return goal_id


async def _optional_fetch(pool: Any, sql: str, *args: Any) -> list[Any]:
    """A query against a table a later migration adds; a database without it is no data, not an error."""
    try:
        return await pool.fetch(sql, *args)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ in ("UndefinedTableError", "UndefinedColumnError", "UndefinedObjectError"):
            return []
        raise


async def _trial_card(pool: Any, key: str, excluded: set[str], owner_id: Optional[str],
                      request_id: Optional[str]) -> Optional[ToolHit]:
    """The trial card for this request, only when the request falls inside a trial's share (P3-02)."""
    if not owner_id or not request_id:
        return None
    from app.tool_routing import trials as _trials

    offered = await _trials.offer(pool, key, owner_id=owner_id, request_id=request_id)
    if not offered:
        return None
    row = await pool.fetchrow("SELECT id, name, server, title, description, source FROM tool_cards WHERE id = $1",
                              offered["card_id"])
    if row is None or row["id"] in excluded:
        return None
    return ToolHit(card_id=str(row["id"]), name=str(row["name"] or ""), server=str(row["server"] or ""),
                   title=str(row["title"] or ""), description=str(row["description"] or ""),
                   source=str(row["source"] or ""), score=0.0, trial=True,
                   rank_inputs={"trial": offered.get("label", "New - free trial"), "trial_id": offered["id"]})


async def _rank(pool: Any, goal_id: str, cards: list[ToolHit], *, owner_id: Optional[str],
                request_id: Optional[str]) -> list[ToolHit]:
    from app.tool_routing import trust as _trust

    key = _goal_key(goal_id)
    cards = [c for c in cards if c.score >= GOAL_FIT_MIN]
    excluded: set[str] = set()
    demoted: dict[str, float] = {}
    try:
        from app.tool_routing import lifecycle as _lifecycle   # P3-04: retired cards leave, retiring ones drop

        excluded = set(await _lifecycle.excluded(pool, key))
        demoted = await _lifecycle.demotions(pool, key)
    except (ImportError, AttributeError):
        pass
    trial_ids = {str(r["card_id"]) for r in await _optional_fetch(
        pool, "SELECT card_id FROM tool_trials WHERE status = 'active' AND goal = ANY($1::text[])", [key, goal_id])}
    trial = await _trial_card(pool, key, excluded, owner_id, request_id) if trial_ids else None
    # a trial card is shown only as the trial, inside its share; never as an ordinary result
    cards = [c for c in cards if c.card_id not in excluded and c.card_id not in trial_ids]

    ids = [c.card_id for c in cards]
    evidence = {r["card_id"]: r for r in await _trust.rows(pool, goal=key, card_ids=ids, limit=len(ids))} if ids else {}
    prior = _trust.lower_bound(0, 0)
    risks = {str(r["server"]): str(r["risk"]) for r in await _optional_fetch(
        pool, "SELECT DISTINCT ON (server) server, risk FROM tool_policies "
              "WHERE enabled AND server = ANY($1::text[]) AND tool IS NULL AND goal_id IS NULL "
              "ORDER BY server, (effect = 'deny') DESC, priority DESC", [c.server for c in cards])}
    prices = {str(r["card_id"]): float(r["price_usd"] or 0) for r in await _optional_fetch(
        pool, "SELECT card_id, price_usd::float8 AS price_usd FROM tool_listings WHERE card_id = ANY($1::text[]) "
              "AND status = 'active'", ids)}
    latency = {str(r["card_id"]): float(r["ms"] or 0) for r in await _optional_fetch(
        pool, "SELECT card_id, avg(latency_ms)::float8 AS ms FROM task_runs WHERE arm = 'tool' AND status = 'pass' "
              "AND card_id = ANY($1::text[]) GROUP BY card_id", ids)}
    for c in cards:
        ev = evidence.get(c.card_id)
        judged = int(ev["judged"]) if ev else 0
        lb = float(ev["lower_bound"]) if ev else prior
        risk = risks.get(c.server, UNKNOWN_RISK)
        inputs = {"trust_lb": round(lb, 4), "judged": judged, "wins": int(ev["wins"]) if ev else 0,
                  "exploration": round(EXPLORE / (1 + judged), 4),
                  "cost_penalty": round(min(0.2, prices.get(c.card_id, 0.0) * 10), 4),
                  "latency_penalty": round(min(0.1, latency.get(c.card_id, 0.0) / 100_000), 4),
                  "risk": risk if c.server in risks else f"{risk}?", "risk_penalty": RISK_PENALTY.get(risk, 0.1),
                  "goal_fit": round(c.score, 3)}
        if c.card_id in demoted:
            inputs["retiring_penalty"] = demoted[c.card_id]
        c.rank_inputs = inputs
        c.rank_score = round(lb + inputs["exploration"] - inputs["cost_penalty"] - inputs["latency_penalty"]
                             - inputs["risk_penalty"] - inputs.get("retiring_penalty", 0.0), 4)
    cards.sort(key=lambda c: (-(c.rank_score or 0.0), -c.score, c.card_id))
    cards = cards[:MAX_TOOLS_PER_GOAL]
    if trial is not None:
        cards.insert(min(1, len(cards)), trial)     # after the proven leader, never instead of it
        cards = cards[:MAX_TOOLS_PER_GOAL]
    return cards


async def _tools(pool: Any, goal_ids: Sequence[str], *, owner_id: Optional[str] = None,
                 request_id: Optional[str] = None) -> dict[str, list[ToolHit]]:
    if not goal_ids:
        return {}
    ranked = trust_ranking()
    per_goal = CANDIDATES_PER_GOAL if ranked else MAX_TOOLS_PER_GOAL
    try:
        rows = await pool.fetch(
            """
            WITH ranked_cards AS (
                SELECT g.goal_id::text AS goal_id, t.id, t.name, t.server, t.title, t.description,
                       t.source, g.score,
                       row_number() OVER (PARTITION BY g.goal_id
                                          ORDER BY g.score DESC,
                                                   NULLIF(t.extra->>'stars', '')::int DESC NULLS LAST,
                                                   t.id) AS rn
                FROM tool_card_goals g
                JOIN tool_cards t ON t.id = g.card_id
                WHERE g.goal_id = ANY($1::uuid[])
            )
            SELECT goal_id, id, name, server, title, description, source, score
            FROM ranked_cards
            WHERE rn <= $2
            ORDER BY goal_id, rn
            """,
            list(goal_ids), per_goal,
        )
    except Exception:  # noqa: BLE001 -- an older database without db/186: goals still work
        return {}
    out: dict[str, list[ToolHit]] = {}
    for row in rows:
        bucket = out.setdefault(str(row["goal_id"]), [])
        if len(bucket) >= per_goal:
            continue
        bucket.append(ToolHit(
            card_id=str(row["id"]),
            name=str(row["name"] or ""),
            server=str(row["server"] or ""),
            title=str(row["title"] or ""),
            description=str(row["description"] or ""),
            source=str(row["source"] or ""),
            score=float(row["score"] or 0.0),
        ))
    if ranked:
        from app.tool_routing import ranking as _ranking

        stored = await _ranking.stored(pool, list(out))
        # The trial overlay is the only per-request part: one query for all Goals, then a trial card
        # only for a Goal that actually has an active trial.
        trials = await _optional_fetch(pool, "SELECT card_id, goal FROM tool_trials WHERE status = 'active'")
        trial_goals = {str(r["goal"]) for r in trials}
        for goal_id in list(out):
            if stored.get(goal_id):
                out[goal_id] = _ranking.tools(stored[goal_id], out[goal_id])
                key = _goal_key(goal_id)
                if owner_id and request_id and (key in trial_goals or goal_id in trial_goals):
                    trial = await _trial_card(pool, key, set(), owner_id, request_id)
                    if trial is not None:
                        out[goal_id].insert(min(1, len(out[goal_id])), trial)
                        out[goal_id] = out[goal_id][:MAX_TOOLS_PER_GOAL]
            else:
                out[goal_id] = await _rank(pool, goal_id, out[goal_id], owner_id=owner_id,
                                           request_id=request_id)
    return out


async def route_goal(
    pool: Any,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    query_embedding: Optional[Sequence[float]] = None,
    scope: Optional[AccessScope] = None,
    include_ways: bool = True,
    include_tools: bool = True,
    explain: bool = False,
    owner_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> list[GoalHit]:
    """Match a task to Goals, then attach their ways and mapped tools.

    `query_embedding` is optional: without it the router uses exact/alias + full-text only.
    Pass the same embedding model that populated `goal_search_index.embedding` (the search
    group records the model), or the vector leg will not be comparable.

    `explain=True` fills `GoalHit.trace` with one entry per leg that returned the Goal:
    `{leg, rank, score, name}`. It is for the recommend_goal CLI and costs nothing when off.
    """
    text = " ".join(str(query or "").split())[:2000]
    if not text:
        return []
    scope = scope or AccessScope.unrestricted()

    found: dict[str, dict[str, Any]] = {}
    traces: dict[str, list[dict[str, Any]]] = {}

    def note(row: Any, leg: str, rank: int, score: float) -> None:
        if explain:
            traces.setdefault(str(row["id"]), []).append({
                "leg": leg,
                "rank": rank,
                "score": float(score),
                "name": str(row["canonical_name"] or ""),
            })

    normalized = normalize_goal_name(text)
    for rank, row in enumerate(await _exact(pool, normalized, max(limit * 2, 10))):
        exact = normalize_goal_name(str(row["canonical_name"])) == normalized
        score = EXACT_SCORE if exact else ALIAS_SCORE
        _merge(found, row, score, "exact" if exact else "alias")
        note(row, "exact" if exact else "alias", rank, score)

    for rank, row in enumerate(await _k000_full_text(pool, text, max(limit * 4, 20))):
        score = 1.25 / (RRF_K + rank + 1)
        _merge(found, row, score, "fts")
        note(row, "k000_fts", rank, score)

    search_leg = "fts+vector" if query_embedding is not None else "fts"
    for rank, row in enumerate(await _search(pool, text, max(limit * 4, 20), query_embedding, scope)):
        score = 1.0 / (RRF_K + rank + 1)
        _merge(found, row, score, search_leg)
        note(row, search_leg, rank, score)

    ranked = sorted(found.values(), key=lambda h: (-h["score"], h["canonical_name"]))[: max(1, int(limit))]
    if not ranked:
        return []

    ways = await _ways(pool, ranked) if include_ways else {}
    tools = await _tools(pool, [h["goal_id"] for h in ranked], owner_id=owner_id,
                         request_id=request_id) if include_tools else {}

    # A one-step Way is its tool: build the Ways from the ranked tools so the first Way is the
    # first tool (trust when GOAL_RANKING=trust, goal fit otherwise). A tool with no materialized
    # procedure gets a synthesized one-step Way; a real multi-step Way is kept when one of its
    # cards is in the ranked list.
    if include_ways and include_tools:
        for goal_id, hits in tools.items():
            by_card: dict[str, Any] = {}
            for w in ways.get(goal_id, []):
                for c in (w.cards or []):
                    by_card.setdefault(str(c.get("card_id") or ""), w)
            built: list[Any] = []
            for t in hits:
                w = by_card.get(t.card_id)
                if w is None:
                    w = WayHit(
                        procedure_id=f"tool:{t.card_id}",
                        name=t.title or t.name or t.server,
                        steps=[{"order": 0, "goal": f"Use {t.name or t.server}", "card_id": t.card_id}],
                        preconditions=[],
                        verification_state="candidate",
                        availability="active",
                        approval_status="submitted",
                        cards=[{"card_id": t.card_id, "name": t.name, "server": t.server,
                                "step_order": 0}],
                    )
                built.append(w)
            ways[goal_id] = built[:MAX_WAYS_PER_GOAL]

    return [
        GoalHit(
            goal_id=h["goal_id"],
            canonical_name=h["canonical_name"],
            description=h["description"],
            status=h["status"],
            score=h["score"],
            matched_by=h["matched_by"],
            home_shard=h["home_shard"],
            ways=ways.get(h["goal_id"], []),
            tools=tools.get(h["goal_id"], []),
            trace=traces.get(h["goal_id"], []) if explain else [],
        )
        for h in ranked
    ]
