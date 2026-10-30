"""Find the tools for a request: BM25 over the cards' words plus embedding similarity, fused by reciprocal rank.

Why both: BM25 finds exact names ("playwright", "postgres", "sentry") that an embedding blurs; embeddings find the tool
for a request that names none ("check what the page looks like after login" -> a browser server). Reciprocal-rank
fusion (score = sum of 1/(K + rank) over the two lists) needs no calibration between them. Then:
  * a card serving the request's goal (the triage's, or the best-matching goal's) ranks higher;
  * a tool the user already has installed ranks above one they would have to install.
No embedder, or one that fails: BM25 alone, said in the result.

Registry cards' vectors are built offline (scripts/build_tool_vectors.py -> data/registry_vectors.npz) in the embedder's
space; installed and kell cards (a few dozen) are embedded per call through the embedding cache.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional, Sequence

import numpy as np

from app.tool_routing.catalog import DATA, ToolCard, popularity, publisher, quality, words
from app.tool_routing.goals import TOOL_GOALS

RRF_K = 60
GOAL_BONUS = 1.0 / (RRF_K + 2)      # about what being 2nd in one list adds
INSTALLED_BONUS = 1.0 / (RRF_K + 4)
QUALITY_WEIGHT = 1.0 / (RRF_K + 3)     # a full-quality card over an empty one: about a few ranks, never more than relevance
VECTORS_FILE = DATA / "registry_vectors.npz"
RERANK_POOL = 40                    # first-stage candidates the cross-encoder reads
# after the cross-encoder (its scores are 0..1): what moves a relevant card up. An installed tool the reranker finds
# relevant comes first -- the session can call it now; then the widely used and the official (2026-10-30: by relevance
# alone, pay-per-call wrappers outranked Microsoft's Playwright server and Context7)
INSTALLED_MIN = 0.10                # an installed tool the reranker scores at least this comes first
RELEVANT = 0.5                      # the reranker's yes/no point: above it, a card fits the request
FUSE_K = 10                         # RRF constant for relevance x popularity among the relevant (small: the top matters)
BRAND_LIFT = 0.10
GOAL_POPULAR = 15                   # the goal's most used servers, among those related by meaning, that reach the reranker
ONLINE_MAX = 300                    # registry cards without offline vectors embedded per call, at most


class BM25:
    """Okapi BM25 (k1 1.2, b 0.75) over the cards' words, as an inverted index: a query touches only the cards that
    contain its words (the registry is 15k cards and ToolRet 43k; a scan per query took seconds)."""

    def __init__(self, docs: Sequence[list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b, self.n = k1, b, len(docs)
        self.len = np.array([len(d) for d in docs], dtype=np.float32)
        self.avg = float(self.len.mean()) if self.n else 0.0
        post: dict[str, tuple[list[int], list[int]]] = {}
        for i, d in enumerate(docs):
            for t, f in Counter(d).items():
                ids, tfs = post.setdefault(t, ([], []))
                ids.append(i)
                tfs.append(f)
        self.post = {t: (np.array(ids, dtype=np.int32), np.array(tfs, dtype=np.float32)) for t, (ids, tfs) in post.items()}
        self.idf = {t: math.log(1 + (self.n - len(ids) + 0.5) / (len(ids) + 0.5)) for t, (ids, _f) in self.post.items()}
        self.norm = self.k1 * (1 - self.b + self.b * self.len / (self.avg or 1))

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.n, dtype=np.float32)
        for t in set(query):
            p = self.post.get(t)
            if p is None:
                continue
            ids, tf = p
            out[ids] += self.idf[t] * tf * (self.k1 + 1) / (tf + self.norm[ids])
        return out

    def score_docs(self, query: list[str], docs: Sequence[list[str]]) -> np.ndarray:
        """`docs` outside the index, scored with its statistics (a word it has never seen counts as its rarest)."""
        top = max(self.idf.values(), default=1.0)
        out = np.zeros(len(docs), dtype=np.float32)
        q = set(query)
        for j, d in enumerate(docs):
            norm = self.k1 * (1 - self.b + self.b * len(d) / (self.avg or 1))
            for t, f in Counter(d).items():
                if t in q:
                    out[j] += self.idf.get(t, top) * f * (self.k1 + 1) / (f + norm)
        return out


_INDEXES: dict[int, BM25] = {}
_ONLINE: dict[tuple[str, str], np.ndarray] = {}   # (model, card text) -> vector, for cards embedded per request


def index_for(cards: Sequence[ToolCard]) -> BM25:
    """The BM25 index of a card list, built once per distinct list (the registry's is reused across requests)."""
    key = hash(tuple(c.id for c in cards))
    idx = _INDEXES.get(key)
    if idx is None:
        if len(_INDEXES) >= 8:
            _INDEXES.pop(next(iter(_INDEXES)))
        idx = _INDEXES[key] = BM25([words(c.text) for c in cards])
    return idx


def _bm25_scores(qw: list[str], cards: Sequence[ToolCard]) -> np.ndarray:
    """BM25 over `cards`. The caller's own few (installed, kell) are scored against the index of the rest, so a request
    with a different installed list reuses the registry's index instead of rebuilding 48k cards' (2026-10-30)."""
    own = [i for i, c in enumerate(cards) if c.source in ("installed", "kell")]
    if not own or len(own) == len(cards):
        return index_for(cards).scores(qw)
    mine = set(own)
    rest = [i for i in range(len(cards)) if i not in mine]
    idx = index_for([cards[i] for i in rest])
    out = np.zeros(len(cards), dtype=np.float32)
    out[rest] = idx.scores(qw)
    out[own] = idx.score_docs(qw, [words(cards[i].text) for i in own])
    return out


@dataclass
class Hit:
    card: ToolCard
    score: float
    bm25_rank: Optional[int]
    vector_rank: Optional[int]
    goal_match: bool
    rerank: Optional[float] = None


@lru_cache(maxsize=1)
def registry_vectors() -> tuple[dict[str, int], Optional[np.ndarray], str]:
    """(card id -> row, unit vectors, embedding model id) of the bundled registry cards; ({}, None, "") without them."""
    try:
        with np.load(VECTORS_FILE, allow_pickle=False) as z:
            ids = [str(x) for x in z["ids"]]
            m = z["vectors"].astype(np.float32)
            model = str(z["model"])
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        return {cid: i for i, cid in enumerate(ids)}, m, model
    except (OSError, KeyError, ValueError):
        return {}, None, ""


def best_goal(query_words: list[str]) -> Optional[str]:
    """The goal whose description and marker words a request shares most words with (None: none shared)."""
    qs = set(query_words)
    best, best_n = None, 0
    for gid, (desc, markers) in TOOL_GOALS.items():
        n = len(qs & set(words(" ".join(markers)))) * 2 + len(qs & set(words(desc)) - STOP)
        if n > best_n:
            best, best_n = gid, n
    return best


STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "its", "what", "how", "are", "can", "use", "you",
        "your", "all", "one", "out", "get", "up", "or", "of", "to", "in", "on", "a", "an", "is", "it", "be", "do"}


async def rank(query: str, cards: Sequence[ToolCard], *, goal: Optional[str] = None, embedder: Any = None, k: int = 8,
               min_bm25: float = 0.0, reranker: Any = None,
               vector_store: Optional[tuple[dict[str, int], np.ndarray, str]] = None,
               known_sims: Optional[dict[str, float]] = None,
               query_vector: Optional[np.ndarray] = None) -> tuple[list[Hit], dict[str, Any]]:
    """-> (the top k hits, how they were found). `goal`: the triage's tool goal; None picks the best-matching one.
    `known_sims`: card id -> cosine to the query, already computed (the database's candidates, app/tool_routing/store.py);
    `query_vector`: need_text()'s vector, already embedded."""
    info: dict[str, Any] = {"cards": len(cards), "lexical": True, "semantic": False}
    given_goal = goal if goal in TOOL_GOALS else None
    if not cards or not query.strip():
        return [], info
    qw = [w for w in words(query) if w not in STOP]
    bm = _bm25_scores(qw, cards)
    # the triage's goal says what KIND of tool is needed; the meaning and the reranker read it with the request, asked
    # as "which server", or they rank by the request's topic: "search the web for how teams handle rate limiting" put
    # a rate-limiting server first, and "show the React 19 docs" scored Context7 0.08 (a cross-encoder asks whether a
    # text answers the question). Asked this way: Context7 0.66, the rate-limiting server 0.13 (2026-10-30)
    need = need_text(query, given_goal)
    order = [i for i in np.argsort(-bm) if bm[i] > min_bm25]
    bm_rank = {int(i): r for r, i in enumerate(order)}

    vec_rank: dict[int, int] = {}
    qvec = None
    if embedder is not None:
        try:
            qvec = query_vector if query_vector is not None else await _query_vector(need, embedder)
            vec_rank = await _vector_ranks(qvec, cards, embedder, store=vector_store, top=1000, known=known_sims)
            info["semantic"] = bool(vec_rank)
        except Exception as exc:  # noqa: BLE001 -- lexical alone, and said so
            info["degraded"] = f"embedding unavailable: {type(exc).__name__}"
    # the goal: the triage's; else by meaning (the goal descriptions' vectors), else by shared words. By words alone,
    # "check the page renders after login" read as web, not browser (2026-10-30)
    if goal not in TOOL_GOALS:
        goal = (await _goal_by_meaning(qvec, embedder) if qvec is not None else None) or best_goal(qw)
    info["goal"] = goal
    hits: list[Hit] = []
    for i, c in enumerate(cards):
        b, v = bm_rank.get(i), vec_rank.get(i)
        # a card serving the triage's goal is a candidate even when it shares no word with the request ("look at the
        # page" -> a browser server); it competes on the goal and quality bonuses alone
        if b is None and v is None and not (given_goal and given_goal in c.goals):
            continue
        s = (1.0 / (RRF_K + b + 1) if b is not None else 0.0) + (1.0 / (RRF_K + v + 1) if v is not None else 0.0)
        gm = bool(goal and goal in c.goals)
        s += GOAL_BONUS if gm else 0.0
        s += INSTALLED_BONUS if c.source == "installed" else 0.0
        s += QUALITY_WEIGHT * quality(c)
        hits.append(Hit(c, s, b, v, gm))
    hits.sort(key=lambda h: -h.score)
    # the cross-encoder reads the request with each of the first stage's best (app/tool_routing/rerank.py); the same
    # bonuses then apply on its 0..1 scale. No reranker, or it fails: the first-stage order
    if reranker is not None and hits:
        # the installed ones always reach the reranker: there are few, and one sharing no word with the request
        # ("playwright" for "check the page in a real browser") otherwise never makes the first stage's 40
        pool = hits[:RERANK_POOL]
        pool += [h for h in hits[RERANK_POOL:] if h.card.source == "installed"][:20]
        # and so do the goal's most used servers among those the meaning finds related: a general one (Context7 for
        # "the React 19 docs") loses on words and meaning to dozens of narrow ones, and only the reranker can tell that it
        # fits. Not the goal's tag alone: 4,844 cards are tagged web, browser-use and tldraw among the most used (2026-10-30)
        if goal:
            seen = {id(h.card) for h in pool}
            popular = sorted((h for h in hits if goal in h.card.goals and h.vector_rank is not None
                              and popularity(h.card) > 0 and id(h.card) not in seen),
                             key=lambda h: -(popularity(h.card) * quality(h.card)))
            pool += popular[:GOAL_POPULAR]
        rel = await reranker(need, [h.card.text[:1500] for h in pool])
        if rel:
            for h, r in zip(pool, rel):
                h.rerank = r
            hits = _fuse(pool, qw) + [h for h in hits if id(h) not in {id(x) for x in pool}]
            info["reranked"] = True
        else:
            info["reranked"] = False
    return hits[:k], info


def need_text(query: str, goal: Optional[str]) -> str:
    """What the meaning and the reranker read: with the triage's goal, asked as which server can do it."""
    if goal in TOOL_GOALS:
        return f"Which MCP server gives a coding agent the ability to {TOOL_GOALS[goal][0]}? The agent's task: {query}"
    return query


def _fuse(pool: list[Hit], qw: list[str]) -> list[Hit]:
    """The reranked pool in order. The reranker's scores saturate (a dozen docs servers all at ~0.99), so among the
    cards it calls relevant, its order and the order by use (stars, liveness, a verified publisher) are fused by RRF:
    relevance alone ranked 12-star servers over Context7 and Brave. Relevant installed tools come first -- the session
    can call them now; then the relevant, fused; then the rest by relevance. An official server named in the request
    ("stripe", "sentry") is lifted."""
    brands = {w for w in qw if len(w) >= 4}
    for h in pool:
        pub = publisher(h.card)
        h.score = float(h.rerank or 0.0) + 0.10 * quality(h.card) + (0.03 if h.goal_match else 0.0) + (
            BRAND_LIFT if pub and h.card.extra.get("verified") and any(b in pub for b in brands) else 0.0)
    mine = sorted((h for h in pool if h.card.source == "installed" and (h.rerank or 0) >= INSTALLED_MIN), key=lambda h: -h.score)
    taken = {id(h) for h in mine}
    good = [h for h in pool if id(h) not in taken and (h.rerank or 0) >= RELEVANT]
    by_rel = {id(h): i for i, h in enumerate(sorted(good, key=lambda h: -h.score))}
    by_use = {id(h): i for i, h in enumerate(sorted(good, key=lambda h: (-(popularity(h.card) * quality(h.card)), -h.score)))}
    for h in good:
        h.score = 1.0 / (FUSE_K + by_rel[id(h)] + 1) + 1.0 / (FUSE_K + by_use[id(h)] + 1)
    good.sort(key=lambda h: (-h.score, -(popularity(h.card) * quality(h.card))))   # a tie goes to the more used
    taken |= {id(h) for h in good}
    rest = sorted((h for h in pool if id(h) not in taken), key=lambda h: -h.score)
    return mine + good + rest


async def _query_vector(query: str, embedder: Any) -> np.ndarray:
    q = np.array(await embedder.embed_one(query, input_type="query"), dtype=np.float32)
    return q / (np.linalg.norm(q) + 1e-9)


_GOAL_VECTORS: dict[str, np.ndarray] = {}


async def _goal_by_meaning(q: np.ndarray, embedder: Any, floor: float = 0.0) -> Optional[str]:
    """The goal whose description is closest to the request (the 15 descriptions are embedded once per process)."""
    model = _model_id(embedder)
    if model not in _GOAL_VECTORS:
        vecs = await embedder.embed([desc for desc, _m in TOOL_GOALS.values()], input_type="document")
        m = np.array(vecs, dtype=np.float32)
        _GOAL_VECTORS[model] = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
    sims = _GOAL_VECTORS[model] @ q
    i = int(np.argmax(sims))
    return list(TOOL_GOALS)[i] if sims[i] > floor else None


async def _vector_ranks(q: np.ndarray, cards: Sequence[ToolCard], embedder: Any, top: int = 200,
                        store: Optional[tuple[dict[str, int], np.ndarray, str]] = None,
                        known: Optional[dict[str, float]] = None) -> dict[int, int]:
    """Card index -> rank by cosine to the query vector: cards in `known` (similarities the database computed) from it,
    then cards in the vector store (the bundled registry vectors, or the caller's), the rest embedded now."""
    rows, mat, model = store or (({}, None, "") if known is not None else registry_vectors())
    same_space = mat is not None and model == _model_id(embedder) and mat.shape[1] == q.shape[0]
    sims = np.full(len(cards), -np.inf, dtype=np.float32)
    todo = []
    idx = np.array([rows.get(c.id, -1) if same_space else -1 for c in cards])
    have = idx >= 0
    if have.any():
        sims[have] = mat[idx[have]] @ q
    for i, c in enumerate(cards):
        if known and known.get(c.id) is not None:
            sims[i], have[i] = known[c.id], True
    todo = [int(i) for i in np.nonzero(~have)[0]]
    # registry cards are embedded offline (scripts/build_tool_vectors.py); without those vectors only a small catalog is
    # embedded per call -- thousands would cost a minute and the embedding quota on every request
    if len(todo) > ONLINE_MAX:
        todo = [i for i in todo if cards[i].source in ("installed", "kell")][:ONLINE_MAX]
    if todo:
        # the caller's own cards come back with every prompt: their vectors are remembered (by model and text), so only
        # the first prompt pays for them (1-2 s a request, 2026-10-30)
        mid = _model_id(embedder)
        keys = [(mid, cards[i].text[:2000]) for i in todo]
        missing = [k for k in dict.fromkeys(keys) if k not in _ONLINE]
        if missing:
            for k, v in zip(missing, await embedder.embed([k[1] for k in missing], input_type="document")):
                if len(_ONLINE) >= 4096:
                    _ONLINE.pop(next(iter(_ONLINE)))
                _ONLINE[k] = np.array(v, dtype=np.float32)
        for i, k in zip(todo, keys):
            v = _ONLINE[k]
            sims[i] = float(v @ q / (np.linalg.norm(v) + 1e-9))
    order = [int(i) for i in np.argsort(-sims) if np.isfinite(sims[i])][:top]
    return {i: r for r, i in enumerate(order)}


def _model_id(embedder: Any) -> str:
    try:
        return str(embedder.embedding_model_id())
    except Exception:  # noqa: BLE001
        return ""
