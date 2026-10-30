"""The tool catalog in the control database (db/160_tool_cards.sql): filled by the server, asked per request.

sync_catalog (in the background after startup, one process at a time under an advisory lock): the registry snapshot
shipped in the image (app/tool_routing/data/*.json.gz) is upserted into tool_cards; a card whose text changed loses its
embedding; cards gone from the snapshot are deleted; then every card without an embedding is embedded (the local
vectors file when it is the same model, else DeepInfra in batches, each batch written as it lands, so a restart
resumes). Once done, the process drops its in-memory copy of the catalog.

candidates (per request): full-text search on search_tsv with the request's rare words (tool_terms), and the nearest
neighbours by an exact scan of tool_card_vectors (the leading 256 dimensions, in a narrow table), each with its full-vector cosine, as
cards for app/tool_routing/retrieve.py rank(). The ranking itself is unchanged. Measured on 48k cards: an OR of all the
request's words matched 22k of them and ranking those took 4.6 s; an HNSW index took 3-18 s once it no longer fit the
buffers.

Before this the catalog lived in each process: about 2 minutes and 430 MB at every start, and 35 minutes of embedding
after every deploy (the container's disk is thrown away).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any, Callable, Optional, Sequence

import numpy as np

from app.tool_routing.catalog import ToolCard, registry_cards, words
from app.tool_routing.providers import ToolEmbedder
from app.tool_routing.retrieve import _INDEXES, STOP, registry_vectors

log = logging.getLogger(__name__)

LOCK_KEY = 0x7001_C4D5               # pg_advisory_lock key: one catalog sync at a time across processes and replicas
UPSERT_BATCH = 1000
EMBED_BATCH = 256
FTS_LIMIT = 200
VECTOR_LIMIT = 400
READY_TTL_S = 60.0
SMALL = 256                          # tool_card_vectors.small's dimensions
RARE = 0.05                          # a search word held by more than this share of the cards is not searched
RARE_WORDS = 8

_COLS = "id, source, name, title, description, server, goals, how, url, expansion, tools, tools_text, extra"
_ready: dict[str, Any] = {"at": 0.0, "value": False}


def text_hash(c: ToolCard) -> str:
    return hashlib.sha256(c.text[:2000].encode("utf-8", "replace")).hexdigest()[:32]


def card_hash(c: ToolCard) -> str:
    """Of everything a row holds: an unchanged card is not written again (a rewritten row is re-inserted into the
    HNSW index, and rewriting all 48k at every start kept the database busy for half an hour, 2026-10-30)."""
    body = json.dumps([c.source, c.name, c.title, c.description, c.server, list(c.goals), c.how, c.url, c.expansion,
                       list(c.tools), c.tools_text, c.extra], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:32]


def _vec(v: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.6g}" for x in v) + "]"


def card_from_row(r: Any) -> ToolCard:
    extra = r["extra"]
    if isinstance(extra, str):
        extra = json.loads(extra)
    return ToolCard(id=r["id"], source=r["source"], name=r["name"], title=r["title"], description=r["description"],
                    server=r["server"], goals=tuple(r["goals"] or ()), how=r["how"], url=r["url"],
                    expansion=r["expansion"], tools=tuple(r["tools"] or ()), tools_text=r["tools_text"],
                    extra=dict(extra or {}))


async def ready(pool: Any) -> bool:
    """Whether the database holds a catalog to search (remembered for a minute)."""
    now = time.monotonic()
    if now - _ready["at"] < READY_TTL_S:
        return bool(_ready["value"])
    try:
        async with pool.acquire() as conn:
            value = bool(await conn.fetchval("SELECT EXISTS (SELECT 1 FROM tool_cards)"))
    except Exception:  # noqa: BLE001 -- no table (an older database), no connection: the files serve
        value = False
    _ready.update(at=now, value=value)
    return value


def tsquery(query: str) -> str:
    """The request's words for to_tsvector: only [a-z0-9] words, so nothing in it is query syntax."""
    ws = sorted({w for w in words(query) if w not in STOP and len(w) > 1})[:32]
    return " ".join(ws)


def small(v: Sequence[float]) -> np.ndarray:
    s = np.asarray(v, dtype=np.float32)[:SMALL]
    return s / (np.linalg.norm(s) + 1e-9)


async def candidates(pool: Any, query: str, qvec: Optional[np.ndarray]) -> tuple[list[ToolCard], dict[str, float]]:
    """(cards, card id -> cosine to qvec) from full-text search and nearest neighbours; ({}, ...) parts are skipped
    when there is no query vector or no searchable word."""
    out: dict[str, ToolCard] = {}
    sims: dict[str, float] = {}
    q = tsquery(query)
    v = _vec(qvec) if qvec is not None else None
    async with pool.acquire() as conn:
        if v is not None:
            for r in await conn.fetch(
                    f"SELECT {_COLS}, 1 - (t.embedding <=> $2::vector) AS sim FROM "
                    f"(SELECT id FROM tool_card_vectors ORDER BY small <=> $1::vector LIMIT {VECTOR_LIMIT}) s "
                    f"JOIN tool_cards t USING (id)",
                    _vec(small(qvec)), v):
                out[r["id"]] = card_from_row(r)
                sims[r["id"]] = float(r["sim"])
        if q:
            # the request's rare words only (fewer than RARE of the cards hold them): an OR of every word matched 22k
            # cards for a Stripe request and ranking them took 4.6 s
            sim_sql = "CASE WHEN t.embedding IS NULL THEN NULL ELSE 1 - (t.embedding <=> $3::vector) END" if v else "NULL"
            args = (q, RARE, v) if v else (q, RARE)    # $2::float8: inferred from the integer column it was 0
            for r in await conn.fetch(f"""
                    WITH n AS (SELECT count(*) AS n FROM tool_cards),
                    words AS (SELECT w.lexeme FROM unnest(to_tsvector('english', $1)) u
                              JOIN tool_terms w ON w.lexeme = u.lexeme, n
                              WHERE w.ndoc < $2::float8 * n.n ORDER BY w.ndoc LIMIT {RARE_WORDS}),
                    q AS (SELECT string_agg(quote_literal(lexeme), ' | ')::tsquery AS query FROM words)
                    SELECT {_COLS}, {sim_sql} AS sim FROM tool_cards t, q
                    WHERE q.query IS NOT NULL AND t.search_tsv @@ q.query
                    ORDER BY ts_rank_cd(t.search_tsv, q.query) DESC LIMIT {FTS_LIMIT}""", *args):
                if r["id"] not in out:
                    out[r["id"]] = card_from_row(r)
                if r["sim"] is not None:
                    sims[r["id"]] = float(r["sim"])
    return list(out.values()), sims


async def sync_catalog(pool: Any, embedder: Optional[ToolEmbedder] = None,
                       say: Callable[[str], None] = log.info) -> bool:
    """Fill tool_cards from the snapshot and embed what lacks an embedding. False: another process holds the lock, or
    there is no snapshot."""
    async with pool.acquire() as lock_conn:
        if not await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
            say("tool catalog: another process is syncing it")
            return False
        try:
            return await _sync(pool, embedder or ToolEmbedder(), say)
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)


async def _sync(pool: Any, e: ToolEmbedder, say: Callable[[str], None]) -> bool:
    # every CPU-heavy step runs in a thread: parsing 48k cards on the event loop kept the server from answering its
    # readiness check for about a minute after each deploy (2026-10-30)
    cards = await asyncio.to_thread(registry_cards)
    if not cards:
        say("tool catalog: no registry snapshot in this image")
        return False
    t0 = time.monotonic()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, text_hash, card_hash FROM tool_cards")
    have = {r["id"]: r["text_hash"] for r in rows}
    stored = {r["id"]: r["card_hash"] for r in rows}
    # only cards that changed are written (stars and liveness change without the text changing); the embedding is kept
    # when the embedded text is the same
    write = await asyncio.to_thread(lambda: [c for c in cards if stored.get(c.id) != card_hash(c)])
    for i in range(0, len(write), UPSERT_BATCH):
        part = write[i:i + UPSERT_BATCH]
        payload = await asyncio.to_thread(_payload, part)
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO tool_cards (id, source, name, title, description, server, goals, how, url, expansion,
                                        tools, tools_text, extra, text_hash, card_hash, search_tsv, updated_at)
                SELECT u.id, u.source, u.name, u.title, u.description, u.server,
                       ARRAY(SELECT jsonb_array_elements_text(u.goals)), u.how, u.url, u.expansion,
                       ARRAY(SELECT jsonb_array_elements_text(u.tools)), u.tools_text, u.extra, u.text_hash, u.card_hash,
                       to_tsvector('english', u.body), now()
                FROM jsonb_to_recordset($1::text::jsonb) AS u(id text, source text, name text, title text, description text,
                     server text, goals jsonb, how text, url text, expansion text, tools jsonb, tools_text text,
                     extra jsonb, text_hash text, card_hash text, body text)
                ON CONFLICT (id) DO UPDATE SET
                    source = EXCLUDED.source, name = EXCLUDED.name, title = EXCLUDED.title,
                    description = EXCLUDED.description, server = EXCLUDED.server, goals = EXCLUDED.goals,
                    how = EXCLUDED.how, url = EXCLUDED.url, expansion = EXCLUDED.expansion, tools = EXCLUDED.tools,
                    tools_text = EXCLUDED.tools_text, extra = EXCLUDED.extra, search_tsv = EXCLUDED.search_tsv,
                    embedding = CASE WHEN tool_cards.text_hash = EXCLUDED.text_hash THEN tool_cards.embedding END,
                    embedding_model = CASE WHEN tool_cards.text_hash = EXCLUDED.text_hash THEN tool_cards.embedding_model END,
                    text_hash = EXCLUDED.text_hash, card_hash = EXCLUDED.card_hash, updated_at = now()
            """, payload)
    if write:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tool_card_vectors v USING tool_cards t WHERE v.id = t.id AND t.embedding IS NULL")
    ids = [c.id for c in cards]
    async with pool.acquire() as conn:
        gone = await conn.execute("DELETE FROM tool_cards WHERE source = 'registry' AND NOT (id = ANY($1::text[]))", ids)
    changed = await asyncio.to_thread(lambda: sum(1 for c in cards if have.get(c.id) != text_hash(c)))
    async with pool.acquire() as conn:
        if write or gone != "DELETE 0" or not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM tool_terms)"):
            async with conn.transaction():
                await conn.execute("DELETE FROM tool_terms")
                await conn.execute("INSERT INTO tool_terms (lexeme, ndoc) "
                                   "SELECT word, ndoc FROM ts_stat('SELECT search_tsv FROM tool_cards')")
    say(f"tool catalog: {len(cards)} cards, {len(write)} written ({changed} with new text; {gone}) "
        f"in {time.monotonic() - t0:.0f} s")
    done = await _embed_missing(pool, cards, e, say)
    _ready.update(at=0.0)
    # the database serves from now on: this process's copy is not needed
    registry_cards.cache_clear()
    registry_vectors.cache_clear()
    _INDEXES.clear()
    return done


def _payload(part: Sequence[ToolCard]) -> str:
    """One upsert batch as JSON text (sent as text: the app pool's jsonb codec would encode a string again)."""
    return json.dumps([{
        "id": c.id, "source": c.source, "name": c.name, "title": c.title, "description": c.description,
        "server": c.server, "goals": list(c.goals), "how": c.how, "url": c.url, "expansion": c.expansion,
        "tools": list(c.tools), "tools_text": c.tools_text, "extra": c.extra, "text_hash": text_hash(c),
        "card_hash": card_hash(c), "body": " ".join(words(c.text)),
    } for c in part], ensure_ascii=False, default=str).replace("\\u0000", "")  # publishers' NULs: text can't hold them


async def _embed_missing(pool: Any, cards: Sequence[ToolCard], e: ToolEmbedder, say: Callable[[str], None]) -> bool:
    model = e.embedding_model_id() if e.available() else ""
    async with pool.acquire() as conn:
        todo_ids = {r["id"] for r in await conn.fetch(
            "SELECT t.id FROM tool_cards t LEFT JOIN tool_card_vectors v USING (id) "
            "WHERE t.embedding IS NULL OR v.id IS NULL OR t.embedding_model IS DISTINCT FROM $1", model)}
    todo = [c for c in cards if c.id in todo_ids]
    if not todo:
        say("tool catalog: every card has its embedding")
        return True
    return await _fill(pool, todo, model, e, say)


async def _fill(pool: Any, todo: Sequence[ToolCard], model: str, e: ToolEmbedder, say: Callable[[str], None]) -> bool:
    rows, mat, file_model = await asyncio.to_thread(registry_vectors)   # a vectors file of the same model saves embedding
    from_file = [c for c in todo if mat is not None and file_model == model and c.id in rows]
    if from_file:
        await _write_vectors(pool, [(c.id, mat[rows[c.id]]) for c in from_file], file_model)
        say(f"tool catalog: {len(from_file)} embeddings from the local vectors file")
    filled = {c.id for c in from_file}
    left = [c for c in todo if c.id not in filled]
    if left and not e.available():
        say(f"tool catalog: {len(left)} cards without an embedding and no DEEPINFRA_KEY: keywords only for them")
        return False
    t0 = time.monotonic()
    for n, i in enumerate(range(0, len(left), EMBED_BATCH), 1):
        part = left[i:i + EMBED_BATCH]
        vecs = await e.embed([c.text[:2000] for c in part], input_type="document")
        await _write_vectors(pool, [(c.id, np.asarray(v, dtype=np.float32)) for c, v in zip(part, vecs)], model)
        if n % 10 == 0 or i + EMBED_BATCH >= len(left):
            say(f"tool catalog: embedded {min(i + EMBED_BATCH, len(left))}/{len(left)} ({time.monotonic() - t0:.0f} s)")
    return True


async def _write_vectors(pool: Any, pairs: Sequence[tuple[str, np.ndarray]], model: str) -> None:
    for i in range(0, len(pairs), 500):
        part = pairs[i:i + 500]
        ids, full, short = await asyncio.to_thread(
            lambda: ([p[0] for p in part], [_vec(p[1]) for p in part], [_vec(small(p[1])) for p in part]))
        async with pool.acquire() as conn:
            await conn.execute("""
                WITH u AS (SELECT * FROM unnest($1::text[], $2::text[], $3::text[]) AS u(id, v, s)),
                full_ AS (UPDATE tool_cards t SET embedding = u.v::vector, embedding_model = $4 FROM u WHERE t.id = u.id)
                INSERT INTO tool_card_vectors (id, small) SELECT u.id, u.s::vector FROM u
                ON CONFLICT (id) DO UPDATE SET small = EXCLUDED.small
            """, ids, full, short, model)


# --- outcomes (db/162_tool_observations.sql) ------------------------------------------------------------------------
# A server is named across users by its package or its URL host -- what the registry card and an installed server's
# config both carry -- never by a user's own name for it ("gh", "my-github") or a URL path (it can hold a key).

OUTCOME_TTL_S = 300.0
_outcomes: dict[str, tuple[float, dict[str, tuple[int, int]]]] = {}
_PKG = re.compile(r"^(@[a-z0-9._-]+/[a-z0-9._-]+|[a-z0-9._-]+)(?:@[^/\s]*)?$")


def norm_ref(value: str) -> str:
    """'@playwright/mcp@latest' -> '@playwright/mcp'; 'https://mcp.context7.com/mcp' or 'mcp.context7.com' -> the host;
    '' when it is neither."""
    v = str(value or "").strip().lower()
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^/?#\s:]+)", v)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", v) and not v.endswith((".js", ".py", ".ts")):
        return v                                                         # a bare host
    m = _PKG.match(v)
    return m.group(1) if m else ""


def card_refs(card: ToolCard) -> set[str]:
    """The refs a card answers to: its connector URL's host and the package in its install command (registry cards),
    or the package / host in its description (an installed server, as lib/tool_note.mjs describes it)."""
    refs = set()
    ex = card.extra or {}
    if ex.get("connector_url"):
        refs.add(norm_ref(ex["connector_url"]))
    m = re.search(r"(?:npx -y|uvx)\s+(\S+)", str(ex.get("install") or ""))
    if m:
        refs.add(norm_ref(m.group(1)))
    m = re.search(r"(?:package|remote server at)\s+(\S+)", card.description or "")
    if m:
        refs.add(norm_ref(m.group(1)))
    return {r for r in refs if r}


async def record_outcome(pool: Any, rows: Sequence[dict[str, Any]]) -> int:
    """Insert tool_observations rows (already validated: ref, used, suggested, outcome, ...). -> how many."""
    rows = [r for r in rows if r.get("ref")][:50]
    if not rows:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO tool_observations (tool_goal, ref, tool, used, suggested, outcome, check_kind, client, goal_id, owner_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """, [(r.get("tool_goal"), r["ref"][:200], str(r.get("tool") or "")[:120], bool(r.get("used")),
               bool(r.get("suggested")), r.get("outcome") or "unknown", r.get("check_kind") or "self_report",
               str(r.get("client") or "")[:40], r.get("goal_id"), r.get("owner_id")) for r in rows])
    for goal in {r.get("tool_goal") for r in rows}:
        _outcomes.pop(str(goal), None)
    return len(rows)


async def tool_outcomes(pool: Any, goal: Optional[str]) -> dict[str, tuple[int, int]]:
    """ref -> (passes, judged) for the tools USED on tasks of this goal (unknown outcomes left out). Cached briefly."""
    key = str(goal)
    hit = _outcomes.get(key)
    if hit and time.monotonic() - hit[0] < OUTCOME_TTL_S:
        return hit[1]
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT ref, count(*) FILTER (WHERE outcome = 'pass') AS wins,
                       count(*) FILTER (WHERE outcome IN ('pass', 'fail')) AS judged
                FROM tool_observations WHERE used AND tool_goal IS NOT DISTINCT FROM $1 GROUP BY ref
            """, goal)
        out = {r["ref"]: (int(r["wins"]), int(r["judged"])) for r in rows if r["judged"]}
    except Exception:  # noqa: BLE001 -- no table yet: no outcomes
        out = {}
    _outcomes[key] = (time.monotonic(), out)
    return out
