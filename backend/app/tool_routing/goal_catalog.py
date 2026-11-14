"""The Goal catalog in process: 30 rows, matched without a database round trip.

Goal ranking is the hot first step of every route. It used to cost two Neon round trips
(exact/alias and K000 full-text, ~0.6-0.9 s each) plus the search-group check. The catalog
here loads the same rows once, builds a tiny in-memory index, and answers exact, alias and
token matches locally.

What is cached:
  * the live K000 Goals (id, name, description, status, home shard);
  * every alias in `goal_names` for those Goals;
  * the top `WAYS_PER_GOAL` procedures per Goal, with their bound cards (the same rows
    `goal_router._ways` returns), so a Goal's ways do not need a second query.

Refresh: `TTL_S` (60 s). `load()` is safe under concurrency (one loader at a time); a failed
refresh keeps the previous catalog. The process can warm it at startup (`app.main`).

The catalog is deliberately small and boring: no embeddings, no LLM. The 30 Goals are a
handful of rows; the expensive part was the network, not the matching.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.goals import normalize_goal_name

TTL_S = 60.0
WAYS_PER_GOAL = 5
WORD = re.compile(r"[a-z0-9]+")
STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "its", "what", "how", "are", "can",
    "use", "you", "your", "all", "one", "out", "get", "up", "or", "of", "to", "in", "on", "a", "an",
    "is", "it", "be", "do", "by", "as", "at", "we", "our", "their", "they", "them", "has", "have",
    "was", "were", "will", "would", "should", "could", "not", "no", "yes", "any", "each", "other",
    "also", "than", "then", "when", "where", "which", "who", "whom", "why", "via", "using", "used",
}


def tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in WORD.findall(str(text or "").lower()) if w not in STOP and len(w) > 1)


@dataclass(frozen=True)
class GoalEntry:
    id: str
    canonical_name: str
    description: str
    status: str
    home_shard: str
    norm: str
    name_tokens: frozenset[str]
    desc_tokens: frozenset[str]


@dataclass
class Catalog:
    entries: list[GoalEntry]
    by_norm: dict[str, GoalEntry]
    aliases: dict[str, GoalEntry]
    idf: dict[str, float]
    ways: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    at: float = 0.0

    def match(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Exact, alias, then token overlap -- the same three answers as the DB legs."""
        q = " ".join(str(query or "").split())[:2000]
        if not q:
            return []
        norm = normalize_goal_name(q)
        found: dict[str, dict[str, Any]] = {}

        def add(entry: GoalEntry, score: float, how: str) -> None:
            current = found.get(entry.id)
            if current is None or score > current["score"]:
                found[entry.id] = {
                    "id": entry.id,
                    "canonical_name": entry.canonical_name,
                    "description": entry.description,
                    "status": entry.status,
                    "home_shard_id": entry.home_shard,
                    "score": float(score),
                    "matched_by": how,
                }

        exact = self.by_norm.get(norm)
        if exact is not None:
            add(exact, 1.0, "exact")
        alias = self.aliases.get(norm)
        if alias is not None:
            add(alias, 0.95, "alias")

        qs = tokens(q)
        if qs:
            scored: list[tuple[float, GoalEntry]] = []
            for entry in self.entries:
                raw = 0.0
                for token in qs:
                    weight = 3.0 if token in entry.name_tokens else 1.0 if token in entry.desc_tokens else 0.0
                    if weight:
                        raw += self.idf.get(token, 1.0) * weight
                if raw > 0:
                    scored.append((raw / (len(qs) or 1), entry))
            if scored:
                top = max(raw for raw, _e in scored) or 1.0
                for raw, entry in scored:
                    # below exact/alias, and below 1.0 so the order is stable
                    add(entry, min(0.9, 0.9 * raw / top), "fts")

        return sorted(found.values(), key=lambda r: (-r["score"], r["canonical_name"]))[: max(1, int(limit))]


_CATALOG: Optional[Catalog] = None
_LOCK = asyncio.Lock()
_WAYS_TASK: Optional[asyncio.Task] = None
_REFRESH_TASK: Optional[asyncio.Task] = None


def cached() -> Optional[Catalog]:
    return _CATALOG


def _schedule_refresh(pool: Any) -> None:
    """Refresh behind a request that found the catalog past its TTL: the caller never waits."""
    global _REFRESH_TASK
    if _REFRESH_TASK is not None and not _REFRESH_TASK.done():
        return

    async def refresh() -> None:
        try:
            await load(pool, force=True)
        except Exception:  # noqa: BLE001 -- the stale catalog is still usable
            return

    _REFRESH_TASK = asyncio.create_task(refresh())
    _REFRESH_TASK.add_done_callback(lambda t: None if t.cancelled() else t.exception())


def _schedule_ways(pool: Any, catalog: Catalog) -> None:
    """Fill the ways beside the catalog: Goal ranking must not wait for 126 procedures."""
    global _WAYS_TASK
    if _WAYS_TASK is not None and not _WAYS_TASK.done():
        return

    async def fill() -> None:
        try:
            catalog.ways = await _load_ways(pool)
        except Exception:  # noqa: BLE001 -- the caller falls back to `_ways`
            return

    _WAYS_TASK = asyncio.create_task(fill())
    _WAYS_TASK.add_done_callback(lambda t: None if t.cancelled() else t.exception())


async def load(pool: Any, *, force: bool = False) -> Catalog:
    """The current catalog. Past the TTL it is served stale while a refresh runs behind it.

    A cold process loads synchronously (the startup warm-up does this before traffic); a
    warm process never waits for a refresh, so the request path stays at the matching cost.
    """
    global _CATALOG
    now = time.monotonic()
    if _CATALOG is not None and not force:
        if now - _CATALOG.at >= TTL_S:
            _schedule_refresh(pool)
        return _CATALOG
    async with _LOCK:
        if _CATALOG is not None and not force:
            return _CATALOG
        try:
            fresh = await _build(pool)
        except Exception:  # noqa: BLE001 -- a failed refresh keeps the old catalog
            if _CATALOG is not None:
                return _CATALOG
            raise
        fresh.at = time.monotonic()
        _CATALOG = fresh
        _schedule_ways(pool, fresh)
        return fresh


async def refresh_now(pool: Any) -> bool:
    """Rebuild at once, so a Goal just created is retrievable by /rank and /route.

    Never raises: the Goal is already written; a failed rebuild only means it appears on the
    next TTL pass. Returns whether the rebuild happened.
    """
    try:
        await load(pool, force=True)
        return True
    except Exception:  # noqa: BLE001 -- the create must not fail because the cache did
        return False


async def _build(pool: Any) -> Catalog:
    rows = await pool.fetch(
        """
        SELECT g.id::text AS id, g.canonical_name, g.description, g.status, g.home_shard_id,
               COALESCE(jsonb_agg(DISTINCT n.canonical_name)
                        FILTER (WHERE n.canonical_name IS NOT NULL), '[]'::jsonb) AS aliases
        FROM goals g
        LEFT JOIN goal_names n ON n.goal_id = g.id
        WHERE g.t_invalid IS NULL
          AND g.status IN ('active', 'candidate')
          AND g.home_shard_id = 'K000'
        GROUP BY g.id
        """
    )
    entries = [
        GoalEntry(
            id=str(r["id"]),
            canonical_name=str(r["canonical_name"] or ""),
            description=str(r["description"] or ""),
            status=str(r["status"] or ""),
            home_shard=str(r["home_shard_id"] or "K000"),
            norm=normalize_goal_name(str(r["canonical_name"] or "")),
            name_tokens=tokens(str(r["canonical_name"] or "")),
            desc_tokens=tokens(str(r["description"] or "")),
        )
        for r in rows
    ]
    by_id = {e.id: e for e in entries}
    by_norm = {e.norm: e for e in entries}

    aliases: dict[str, GoalEntry] = {}
    for r in rows:
        entry = by_id.get(str(r["id"]))
        if entry is None:
            continue
        names = r["aliases"]
        if isinstance(names, str):
            try:
                names = json.loads(names or "[]")
            except ValueError:
                names = []
        for name in names or []:
            aliases.setdefault(normalize_goal_name(str(name or "")), entry)

    # idf over the 30 names and descriptions: a word in every Goal counts for little
    df: dict[str, int] = {}
    for e in entries:
        for token in e.name_tokens | e.desc_tokens:
            df[token] = df.get(token, 0) + 1
    n = max(1, len(entries))
    idf = {t: math.log(1.0 + n / (1.0 + d)) for t, d in df.items()}

    return Catalog(entries=entries, by_norm=by_norm, aliases=aliases, idf=idf)


async def _load_ways(pool: Any) -> dict[str, list[dict[str, Any]]]:
    """The top `WAYS_PER_GOAL` procedures per Goal, with the cards bound to each step."""
    try:
        rows = await pool.fetch(
            """
            WITH ranked AS (
                SELECT pr.id, pr.achieves_goal_id, pr.name, pr.steps, pr.preconditions,
                       pr.verification_state::text AS verification_state,
                       pr.approval_status, pr.availability::text AS availability,
                       row_number() OVER (
                           PARTITION BY pr.achieves_goal_id
                           ORDER BY (pr.verification_state = 'verified') DESC,
                                    COALESCE((pr.verification_stats->>'successes')::int, 0) DESC,
                                    COALESCE((pr.domain_payload->>'goal_score')::float, 0) DESC,
                                    pr.t_created DESC
                       ) AS rn
                FROM procedures pr
                WHERE pr.t_invalid IS NULL
                  AND pr.approval_status <> 'rejected'
                  AND pr.availability = 'active'
                  -- A one-step Way materialized from a tool card is the tool itself, not a procedure: the tool router
                  -- still offers that tool, so the Way list stays for real Ways (2026-11-14).
                  AND COALESCE(pr.provenance, '') <> 'tool_card'
            )
            SELECT r.id::text AS id, r.achieves_goal_id::text AS goal_id, r.name, r.steps,
                   r.preconditions, r.verification_state, r.approval_status, r.availability,
                   COALESCE((SELECT jsonb_agg(jsonb_build_object(
                                        'card_id', pc.card_id, 'name', tc.name, 'title', tc.title,
                                        'server', tc.server, 'step_order', pc.step_order,
                                        'connector_url', tc.extra->>'connector_url',
                                        'score', pc.score)
                                    ORDER BY pc.step_order, pc.score DESC, pc.card_id)
                             FROM procedure_tool_cards pc
                             JOIN tool_cards tc ON tc.id = pc.card_id
                             WHERE pc.procedure_id = r.id), '[]'::jsonb) AS cards
            FROM ranked r
            WHERE r.rn <= $1
            """,
            WAYS_PER_GOAL,
        )
    except Exception:  # noqa: BLE001 -- an older database without db/199: goals still rank
        return {}

    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        cards = r["cards"]
        if isinstance(cards, str):
            try:
                cards = json.loads(cards or "[]")
            except ValueError:
                cards = []
        out.setdefault(str(r["goal_id"]), []).append({
            "procedure_id": str(r["id"]),
            "name": str(r["name"] or ""),
            "steps": list(r["steps"] or []),
            "preconditions": list(r["preconditions"] or []),
            "verification_state": str(r["verification_state"] or ""),
            "availability": str(r["availability"] or ""),
            "approval_status": str(r["approval_status"] or "submitted"),
            "cards": list(cards or []),
        })
    return out
