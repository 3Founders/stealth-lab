"""The Goal tree walk a task lookup descends: breadth first, one level at a time, and JEV decides whether to go deeper.

WHY THIS EXISTS (the bug it fixes, 2026-11-16). `find_ways` found Goals by one flat search: the search returns the
Goals whose text is closest to the request, and the judge (`task_goal`) picks among *those*. A Goal that is not in the
top-k of that search is invisible, whatever it is. A Goal that is *narrower* than the request always loses that
comparison: "survey the cancer genomics literature and propose an experiment" is not a "search the web" task, so the
abstract Goal wins, and the biology-specific Goal hanging below it -- with the Ways that actually do the work -- is
never looked at. The hierarchy was only reached as a fallback (`retrieval_service._route_goal_candidates`, aimed
upward at parents and capped at 2 hops), so the one direction that matters, *down into specialisations*, was reached
by accident, if at all.

So a lookup now descends the tree instead of reading one level of it:

    level 0   the search's Goals. They are the request's own breadth -- the abstract research Goal, say -- and
              are judged once, as they always were (`retrieval_service.search_goals`).
    decide    JEV: for each Goal, is a narrower Goal of it one of this request's parts? `identity_judgment` below,
              one `task_goal` question (A = the request, B = this Goal + "it would be done BY a more specific goal")
    level 1   the accepted SPECIALIZES children of the Goals JEV said to descend, in the children's own relevance order
    decide    JEV on level 1, then level 2, up to `max_depth`
    stop      when JEV says not to descend, a level has nothing new, a cap is hit, or the judge stops answering

A child is kept only if JEV admits it in its own right ("does it serve this request?") -- descent is not how a Goal
earns its place, it is how a Goal gets *seen*. Fail closed: with no judge, or a judge that did not answer, nothing is
descended and the answer is exactly today's flat one.

This module is pure (no database, no model): the caller passes the request's context text, the walk asks a `judge`
callable, and reads children through a `children_of` callable, so both sides can be the real thing or a fake.
`find_ways` (`app/mcp_server/server.py`) is the caller: it feeds the walk `retrieval_service.search_goals`' judged
hits and the accepted edges in `goal_relations`, and hands the admitted Goals back to the same procedure tier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Sequence

MAX_DEPTH = 2                 # levels below the search's Goals (SPECIALIZES hops), BFS
MAX_DESCEND_PER_LEVEL = 4     # Goals per level whose children are asked about (the judge's fan-out)
MAX_GOALS_PER_LEVEL = 8       # children asked about per level, in relevance order
MAX_GOALS = 16                # every Goal the walk may return, the search's own included
HOP_SCORE_FACTOR = 0.85       # a child's score is its own relevance, scaled down per hop: breadth beats depth on ties

Judge = Callable[[str, "Node"], Awaitable[Any]]   # (request text, node) -> verdict (see `decide`)
ChildrenOf = Callable[[Sequence[str]], Awaitable[dict[str, list[dict[str, Any]]]]]


@dataclass(frozen=True)
class Verdict:
    """What JEV said about one Goal, split into the two questions the walk asks.

    `admitted`: the Goal serves this request -- a "matches" answer, or a "partial" one at firm confidence. Only an
    admitted Goal is returned; a Goal under a good parent is still judged on its own.
    `descend`: a narrower Goal of this one is plausibly part of the request -- the "descent" question, asked only for
    an admitted Goal, because descending an unrelated branch spends judge calls on Goals that cannot be admitted.

    `via` names the provider and model that answered (for the reply's trace). `relation` is the raw `task_goal`
    relation ("matches" | "partial" | "unrelated") the answer came from, or None when the judge did not answer."""

    admitted: bool
    descend: bool = False
    relation: Optional[str] = None
    confidence: Optional[float] = None
    descend_confidence: Optional[float] = None
    via: str = ""

    @property
    def answered(self) -> bool:
        return self.relation is not None


NO_ANSWER = Verdict(admitted=False, descend=False)

# The second question's wording. The judge is asked `task_goal` with A = the request and B = this text, so the answer
# keeps the relation vocabulary every `task_goal` call already uses ("matches" = yes, descending would find the work).
DESCEND_SUFFIX = "\nThis goal may be achieved by a more specific goal below it in the goal tree."
ADMIT_RELATIONS = frozenset({"matches", "partial"})
FIRM = 0.6                    # retrieval_service.RetrievalConfig().min_confidence: the same "firm" bar everywhere


def decide(task: Optional[dict], descent: Optional[dict], *, firm: float = FIRM) -> Verdict:
    """Turn the two `task_goal` answers into a Verdict. `task`: {relation, confidence} for "does this Goal serve the
    request"; `descent`: the same for "would a narrower Goal of this one be part of the request". A missing or invalid
    answer is NO_ANSWER in that half -- never a guess."""
    relation, confidence, via = _answer(task)
    admitted = relation in ADMIT_RELATIONS and (
        relation == "matches" or (confidence or 0.0) >= firm)
    d_relation, d_confidence, d_via = _answer(descent)
    # A narrow child may be judged "partial" against the whole request (it is part of it), so a partial answer is a
    # reason to look one level down too, not only a firm "matches".
    descend = d_relation == "matches" or (
        d_relation == "partial" and (d_confidence or 0.0) >= firm)
    return Verdict(admitted=admitted, descend=descend, relation=relation, confidence=confidence,
                   descend_confidence=d_confidence, via=via or d_via)


def _answer(answer: Any) -> tuple[Optional[str], Optional[float], str]:
    if not isinstance(answer, dict):
        return None, None, ""
    relation = answer.get("relation")
    if relation not in ADMIT_RELATIONS | {"unrelated"}:
        return None, None, ""
    try:
        confidence = None if answer.get("confidence") is None else float(answer["confidence"])
    except (TypeError, ValueError):
        confidence = None
    return str(relation), confidence, str(answer.get("via") or "")


@dataclass
class Node:
    """One Goal in the walk: where it came from, what JEV said about it, and the children it led to."""

    goal_id: str
    name: str
    depth: int                       # 0 = the search's own Goal
    score: float
    parent_id: Optional[str] = None
    text: str = ""                   # the name and description the judge reads
    verdict: Verdict = field(default_factory=lambda: NO_ANSWER)
    children: list[str] = field(default_factory=list)

    @property
    def admitted(self) -> bool:
        return self.verdict.admitted

    def as_dict(self) -> dict[str, Any]:
        out = {"goal_id": self.goal_id, "canonical_name": self.name, "depth": self.depth,
               "score": round(self.score, 4), "parent_id": self.parent_id,
               "judged": {"relation": self.verdict.relation, "confidence": self.verdict.confidence,
                          "admitted": self.verdict.admitted, "descend": self.verdict.descend,
                          "via": self.verdict.via}}
        if self.children:
            out["expanded_to"] = list(self.children)
        return out


@dataclass
class Walk:
    """The outcome: every Goal kept (the search's own first, then each level), the edges walked, a trace of each
    decision, and why the walk stopped."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    stopped: str = ""

    @property
    def goal_ids(self) -> list[str]:
        return [n.goal_id for n in self.nodes]

    @property
    def descended(self) -> list[Node]:
        return [n for n in self.nodes if n.depth > 0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "tree" if self.descended else "flat",
            "stopped": self.stopped,
            "goals": [n.as_dict() for n in self.nodes],
            "edges": [{"parent_id": p, "child_id": c} for p, c in self.edges],
            "trace": list(self.trace),
        }


async def descend(
    anchors: Sequence[Node],
    children_of: ChildrenOf,
    judge: Optional[Judge],
    request_text: str,
    *,
    max_depth: int = MAX_DEPTH,
    descend_per_level: int = MAX_DESCEND_PER_LEVEL,
    goals_per_level: int = MAX_GOALS_PER_LEVEL,
    max_goals: int = MAX_GOALS,
    firm: float = FIRM,
) -> Walk:
    """Walk down from `anchors` while JEV says to.

    `anchors`: the search's Goals, judged by the search, most relevant first -- `Node(goal_id, name, depth=0, score,
    verdict=<the search's verdict>)`. `children_of(goal_ids)` returns the accepted children of each parent, already in
    the children's own relevance order: `{parent_id: [{goal_id, canonical_name, text, score}]}`.

    `judge(request_text, node)` asks the two questions (`decide`) and must not raise; a raise is NO_ANSWER. When the
    judge is None, or answers nothing on a level, the walk stops there and keeps what it has -- a request served by
    no judge gets the flat answer, not a guess.
    """
    walk = Walk(nodes=list(anchors))
    seen = {n.goal_id for n in walk.nodes}
    if judge is None or max_depth <= 0:
        walk.stopped = "no_judge" if judge is None else "max_depth"
        return walk
    # Level 0 was admitted by the search's own judgment; the descent question is asked here, once, for every admitted
    # anchor (asking a Goal that JEV did not admit would spend a call on a branch that cannot be returned).
    for n in anchors:
        if not n.verdict.admitted:
            continue
        try:
            answer = await judge(request_text, n)
        except Exception:  # noqa: BLE001 -- no answer: the anchor is simply not descended
            answer = None
        again = answer if isinstance(answer, Verdict) else _verdict_from(answer)
        # the search's admission stands; only the descent half is taken from this answer
        n.verdict = Verdict(admitted=n.verdict.admitted, descend=again.descend, relation=n.verdict.relation,
                            confidence=n.verdict.confidence, descend_confidence=again.descend_confidence,
                            via=again.via or n.verdict.via)
    frontier = _ranked_admitted(list(anchors))[:descend_per_level]
    for n in list(anchors):
        walk.trace.append({"goal_id": n.goal_id, "name": n.name, "depth": 0, "decision": "anchor",
                           "judged": n.as_dict()["judged"]})
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        try:
            children = await children_of([n.goal_id for n in frontier])
        except Exception:  # noqa: BLE001 -- an unreadable level is no branch; what was found stands
            walk.stopped = "children_unavailable"
            break
        # One candidate list per level, in the parents' relevance order then the children's own order.
        candidates: list[tuple[Node, dict[str, Any]]] = []
        for parent in frontier:
            for child in (children or {}).get(parent.goal_id, []):
                goal_id = str(child.get("goal_id") or "")
                if goal_id and goal_id not in seen:
                    candidates.append((parent, dict(child, goal_id=goal_id)))
        if not candidates:
            walk.stopped = "no_children"
            break
        # A child reached from two parents this level is asked once.
        unique: dict[str, tuple[Node, dict[str, Any]]] = {}
        for parent, child in candidates:
            unique.setdefault(child["goal_id"], (parent, child))
        level_nodes: list[Node] = []
        for parent, child in list(unique.values())[:goals_per_level]:
            node = Node(goal_id=child["goal_id"], name=str(child.get("canonical_name") or child["goal_id"]),
                        depth=depth, score=float(child.get("score") or 0.0) * (HOP_SCORE_FACTOR ** depth),
                        parent_id=parent.goal_id, text=str(child.get("text") or child.get("canonical_name") or ""))
            try:
                answer = await judge(request_text, node)
            except Exception:  # noqa: BLE001 -- the judge did not answer; the level is judged by what did
                answer = None
            node.verdict = answer if isinstance(answer, Verdict) else _verdict_from(answer)
            level_nodes.append(node)

        answered = [n for n in level_nodes if n.verdict.answered]
        if not answered:
            walk.stopped = "judge_unavailable"
            walk.trace.append({"depth": depth, "decision": "judge_unavailable",
                               "candidates": [n.name for n in level_nodes]})
            break
        kept: list[Node] = []
        for node in level_nodes:
            walk.edges.append((node.parent_id or "", node.goal_id))
            wanted = node.verdict.admitted
            if wanted and len(walk.nodes) >= max_goals:
                walk.trace.append({"goal_id": node.goal_id, "name": node.name, "parent_id": node.parent_id,
                                   "depth": depth, "decision": "goal_limit_reached",
                                   "judged": node.as_dict()["judged"]})
                continue
            decision = ("kept" if wanted else "left_out") if node.verdict.answered else "not_answered"
            walk.trace.append({"goal_id": node.goal_id, "name": node.name, "parent_id": node.parent_id,
                               "depth": depth, "decision": decision, "judged": node.as_dict()["judged"]})
            if wanted:
                walk.nodes.append(node)
                seen.add(node.goal_id)
                kept.append(node)
                if node.parent_id:
                    parent = next((p for p in frontier if p.goal_id == node.parent_id), None)
                    if parent is not None:
                        parent.children.append(node.goal_id)
        if len(walk.nodes) >= max_goals:
            walk.stopped = "goal_limit"
            break
        next_frontier = [n for n in _ranked(kept) if n.verdict.descend][:descend_per_level]
        if not next_frontier:
            walk.stopped = "judge_did_not_descend"
            break
        frontier = next_frontier
    if not walk.stopped:
        if frontier:
            walk.stopped = "max_depth"
        elif anchors and not any(n.verdict.descend for n in anchors):
            walk.stopped = "judge_did_not_descend"
        else:
            walk.stopped = "complete"
    return walk


def _ranked(nodes: Sequence[Node]) -> list[Node]:
    return sorted(nodes, key=lambda n: (-n.score, n.goal_id))


def _ranked_admitted(nodes: Sequence[Node]) -> list[Node]:
    """The Goals a level's children are asked about: the ones JEV said to descend. An unjudged Goal is not one of
    them: the descent question was never answered for it, so the walk does not guess (it still returns it, if it is
    one of the search's own Goals)."""
    return _ranked([n for n in nodes if n.verdict.admitted and n.verdict.descend])


def _verdict_from(answer: Any) -> Verdict:
    """A judge that returned a plain dict ({"task": {...}, "descent": {...}}) is read the same way as `decide`."""
    if not isinstance(answer, dict):
        return NO_ANSWER
    if "task" in answer or "descent" in answer:
        return decide(answer.get("task"), answer.get("descent"))
    return decide(answer, None)
