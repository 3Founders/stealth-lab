"""The real parts of the task lookup's Goal tree walk (`app/services/goal_search_tree.py` holds the walk itself).

`find_ways` asks three things of kell's Goal tree here, and every one of them already had an implementation:

  * does this Goal serve the request?   `retrieval_service.search_goals`'s own `task_goal` judgment. It judged only
                                        the top `rerank_top_k` fused hits; this judges every fused hit it scanned,
                                        because a Goal the request is *narrower* than does not collect match tokens
                                        and was lost inside that cut. The same prompt, the same answer.
  * would looking below it help?        one more `task_goal` question: A = the request, B = this Goal plus
                                        `goal_search_tree.DESCEND_SUFFIX`. Same relation vocabulary, so JEV answers
                                        it the same way. Asked only for a Goal that already serves the request.
  * what is below it?                   accepted `SPECIALIZES` children (`goal_relations`), ranked by the child's own
                                        text against the request (`retrieval_service._fetch_hierarchy_edges`, which
                                        already applies the visibility and tenant predicates).

Nothing is written. A Goal admitted below Level 0 is handed back to the procedure tier exactly like a flat match, so
its Ways are judged against the request as before (`goal_choice`). One walk per lookup: nothing is cached, because a
different request's branch is not this request's answer.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from app.services import goal_search_tree as tree
from app.services.access import AccessScope, TenantScope
from app.services.retrieval_service import QueryContext, RetrievalConfig, RetrievalMeta, _judge_all

log = logging.getLogger(__name__)

FANOUT = 8                  # children read per Goal and level: the same fan-out the hierarchy search uses
JEV_CONCURRENCY = 4         # the retrieval tier's `judge_concurrency`


def _text(name: str, description: Optional[str]) -> str:
    return f"{name}: {description}".strip(": ") if description else name


async def judge_scanned(
    hits: Sequence[Any], judge: Any, ctx: QueryContext, *, cfg: RetrievalConfig, meta: RetrievalMeta,
) -> None:
    """Judge every scanned flat hit against the request, in place (one batch per `judge_batch_size`).

    Only the top `rerank_top_k` hits were judged by `search_goals`. The rest are candidates the fused search already
    ranked for this request; judging them is what lets a Goal narrower than the request be admitted at all. A judge
    that does not answer leaves them unjudged, and the walk then does not descend them."""
    pending = [h for h in hits if not getattr(h, "judged", False) and getattr(h, "hierarchy", None) is None]
    if not pending or judge is None:
        return
    await _judge_all(judge, "task_goal", ctx.text, pending, cfg, meta, stage="goal_scan")


def level0(hits: Sequence[Any]) -> list[tree.Node]:
    """The walk's Level 0: the fused hits as nodes, in the judged order a flat answer would use.

    Each admitted hit carries the search's own verdict. A hit no judge answered is returned (that is today's
    `unjudged` behaviour -- carry the top fused Goals) but never descended: `Verdict.descend` is False for it in
    `goal_search_tree._ranked_admitted`, because the descent question is asked, and it was not."""
    nodes: list[tree.Node] = []
    seen: set[str] = set()
    for h in hits:
        goal_id = str(getattr(h, "id", "") or "")
        if not goal_id or goal_id in seen or getattr(h, "hierarchy", None) is not None:
            continue                            # hierarchy-run hits are not flat; they are not a starting point
        seen.add(goal_id)
        judged = bool(getattr(h, "judged", False))
        verdict = tree.Verdict(
            admitted=judged and h.relation in tree.ADMIT_RELATIONS
                     and (h.relation == "matches" or (h.confidence or 0.0) >= tree.FIRM),
            relation=h.relation if judged else None,
            confidence=h.confidence if judged else None,
            via="search",
        )
        nodes.append(tree.Node(goal_id=goal_id, name=str(getattr(h, "name", "") or goal_id), depth=0,
                               score=float(h.confidence or 0.0), text=str(getattr(h, "text", "") or ""),
                               verdict=verdict))
    nodes.sort(key=lambda n: (-n.score, n.goal_id))
    return nodes


def jev_judge(judge: Any) -> Optional[tree.Judge]:
    """The walk's judge: two `task_goal` questions per Goal, both through the chain that already answers them.

    Returns None when there is no judge at all, so the caller skips the walk (fail closed: the flat answer stands)."""
    if judge is None:
        return None

    async def ask(request_text: str, node: tree.Node) -> tree.Verdict:
        gate = asyncio.Semaphore(JEV_CONCURRENCY)

        async def one(b: str) -> Optional[dict]:
            async with gate:
                result = await judge.judge_identity("task_goal", request_text, b)
            if not (result is not None and getattr(result, "ok", False)
                    and isinstance(getattr(result, "value", None), dict)):
                return None
            answer = dict(result.value)
            answer["via"] = f"{getattr(result, 'provider', '')}:{getattr(result, 'model', '')}".strip(":")
            return answer

        task = await one(node.text or node.name)
        if task is None:
            return tree.NO_ANSWER
        descent = await one(f"{node.text or node.name}{tree.DESCEND_SUFFIX}")
        return tree.decide(task, descent)

    return ask


async def children_of(
    pool: Any, ctx: QueryContext, scope: AccessScope, *, tenant_scope: Optional[TenantScope] = None,
    fanout: int = FANOUT,
) -> tree.ChildrenOf:
    """Accepted `SPECIALIZES` children of each parent, ranked by the child's own text against the request."""
    from app.services.retrieval_service import _fetch_hierarchy_edges

    tenant_scope = tenant_scope if tenant_scope is not None else TenantScope.commons()

    async def read(goal_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        ids = [str(g) for g in goal_ids if g]
        if not ids:
            return {}
        rows = await _fetch_hierarchy_edges(
            pool, up_ids=[], down_ids=ids, scope=scope, tenant_scope=tenant_scope, fanout=fanout, ctx=ctx,
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for raw in rows:
            row = dict(raw)
            if str(row.get("side") or "") != "children":
                continue
            parent = str(row.get("node_id") or row.get("abstract_goal_id") or "")
            child = str(row.get("neighbour_id") or row.get("specific_goal_id") or "")
            if not parent or not child:
                continue
            name = str(row.get("neighbour_name") or child)
            out.setdefault(parent, []).append({
                "goal_id": child,
                "canonical_name": name,
                "text": _text(name, row.get("specific_description") or row.get("neighbour_description")),
                # the same two relevance signals the flat search fuses, lexical scaled down (it is a count, not a
                # distance); ties keep the SQL's own per-parent order
                "score": float(row.get("neighbour_semantic") or 0.0)
                         + float(row.get("neighbour_lexical") or 0.0) / 1000.0,
            })
        return out

    return read


async def descended_hits(pool: Any, walk: tree.Walk) -> list[Any]:
    """The descended Goals the walk kept, as retrieval `Hit`s the procedure tier accepts.

    Read live from `goals` (a Goal deleted or made inactive since the edge was written is dropped on its own); ids
    and names come back from one query. The walk's own ordering is preserved."""
    from app.services.retrieval_service import Hit
    from app.services.shards import HOME_SHARD

    kept = walk.descended
    if not kept:
        return []
    try:
        rows = await pool.fetch(
            "SELECT id::text AS id, canonical_name, description, home_shard_id FROM goals "
            "WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL AND status IN ('active', 'candidate')",
            [n.goal_id for n in kept])
    except Exception:  # noqa: BLE001 -- an unreadable Goal is no Goal; nothing else is affected
        log.info("descended goals could not be read", exc_info=True)
        return []
    live = {str(r["id"]): r for r in rows}
    out: list[Hit] = []
    for node in kept:
        row = live.get(node.goal_id)
        if row is None:
            continue
        name = str(row.get("canonical_name") or node.name)
        out.append(Hit(id=node.goal_id, name=name, text=_text(name, row.get("description")),
                       home_shard_id=str(row.get("home_shard_id") or HOME_SHARD), rrf=node.score,
                       relation=node.verdict.relation, confidence=node.verdict.confidence, judged=True,
                       hierarchy={"origin": "tree", "depth": node.depth, "parent_id": node.parent_id,
                                  "via": node.verdict.via}))
    return out


async def descend_from(
    pool: Any, hits: Sequence[Any], *, ctx: QueryContext, judge: Any, scope: AccessScope,
    cfg: RetrievalConfig, meta: RetrievalMeta, tenant_scope: Optional[TenantScope] = None,
) -> tuple[tree.Walk, list[Any]]:
    """The whole step: judge the scanned hits, walk down while JEV says to, read the descended Goals back.

    Returns (walk, new hits). Never raises: a failure leaves the flat answer in place and is recorded in `meta`."""
    if judge is None:
        return tree.Walk(stopped="no_judge"), []
    await judge_scanned(hits, judge, ctx, cfg=cfg, meta=meta)
    anchors = level0(hits)
    asker = jev_judge(judge)
    if asker is None or not anchors:
        return tree.Walk(nodes=anchors, stopped="no_judge"), []
    walk = await tree.descend(
        anchors, await children_of(pool, ctx, scope, tenant_scope=tenant_scope), asker, ctx.text,
        max_depth=cfg.tree_max_depth, descend_per_level=cfg.tree_descend_per_level,
        goals_per_level=cfg.tree_goals_per_level, max_goals=cfg.tree_max_goals, firm=cfg.min_confidence,
    )
    if walk.stopped == "judge_unavailable":
        meta.degrade("goal tree: the judge stopped answering; the flat answer stands")
    new_hits = await descended_hits(pool, walk)
    return walk, new_hits


__all__ = ["children_of", "descend_from", "descended_hits", "jev_judge", "judge_scanned", "level0"]
