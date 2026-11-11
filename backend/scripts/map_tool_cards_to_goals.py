"""Map tool cards to canonical Goals (db/186_tool_card_goals.sql).

Two mappings, in order of precision:

1. **Taxonomy (always):** every tool card already carries `tool_cards.goals`, the 25 abstract
   tool goals from app/tool_routing/goals.py ("web", "database", "browser", ...). This script
   ensures one canonical Goal per tool goal exists on K000 and maps each card to those Goals.
   This is the reliable base: a card tagged "database" is mapped to the "Query or change a
   database" Goal.
2. **Lexical (optional, `--lexical`):** a card's name/title/server is matched against every
   live Goal's canonical name, with a coverage requirement (at least half the Goal's words must
   be in the card). This adds specific issue-shaped Goals where the names genuinely overlap.

Rebuildable: drop the rows and run the script again.

    python scripts/map_tool_cards_to_goals.py --dry-run
    python scripts/map_tool_cards_to_goals.py --replace
    python scripts/map_tool_cards_to_goals.py --replace --lexical --max-per-card 3
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import re
import sys
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg
from dotenv import dotenv_values

from app.tool_routing.goals import TOOL_GOALS

WORD = re.compile(r"[a-z0-9]+")
STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "its", "what", "how", "are", "can",
    "use", "you", "your", "all", "one", "out", "get", "up", "or", "of", "to", "in", "on", "a", "an",
    "is", "it", "be", "do", "by", "as", "at", "we", "our", "their", "they", "them", "has", "have",
    "was", "were", "will", "would", "should", "could", "not", "no", "yes", "any", "each", "other",
    "also", "than", "then", "when", "where", "which", "who", "whom", "why", "via", "using", "used",
}

# Human-readable Goal names for the 25 tool goals. The id is stable; the name is what a person
# searches for. Keep these task-shaped ("Search the web..."), not tool-shaped ("Exa").
TOOL_GOAL_TITLES: dict[str, str] = {
    "web": "Search the web or read a web page",
    "docs": "Look up a library's, framework's or API's documentation",
    "code_search": "Search code beyond this repository",
    "packages": "Look up package versions, dependencies and changelogs",
    "browser": "Drive a real browser: open pages, click, fill forms, take screenshots",
    "database": "Query or change a database",
    "vcs": "Work with GitHub or GitLab: pull requests, issues, branches, releases",
    "ci_cd": "Check CI pipelines, build status, test runs and deploys",
    "cloud": "Work with cloud infrastructure: AWS, GCP, Azure, Kubernetes, Docker, Terraform",
    "observability": "Read logs, errors, metrics and traces of running software",
    "security": "Check security: vulnerabilities, CVEs, secrets, auth and access policies",
    "design": "Work with design files and systems: Figma frames, components, design tokens",
    "generate": "Create images, illustrations, logos, banners, video, music or speech",
    "files_media": "Work with existing files: PDFs, spreadsheets, documents, images and audio",
    "data": "Get outside data: scraping, company or people enrichment, finance, maps, weather",
    "messaging": "Talk to people: Slack, Discord, Teams, email, SMS, calendars",
    "tickets": "Work with project and issue tracking: Jira, Linear, Asana, Trello",
    "workspace": "Work with notes, wikis and shared documents: Notion, Confluence, Drive",
    "analytics": "Read product and web analytics: events, funnels, dashboards",
    "crm": "Work with customers and sales: CRM records, leads, deals, contacts",
    "payments": "Work with payments and billing: Stripe, invoices, subscriptions, checkout",
    "commerce": "Work with online stores: products, orders, inventory, storefronts",
    "social": "Read or post on social platforms: X, LinkedIn, Reddit, YouTube, Instagram",
    "sandbox": "Run code in an isolated cloud machine",
    "ai_models": "Call other AI models: chat and embedding APIs, model hubs, fine-tuning",
}

TOOL_GOAL_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # UUID namespace URL


def tool_goal_id(gid: str) -> str:
    return str(uuid.uuid5(TOOL_GOAL_NS, f"kell:tool-goal:{gid}"))


def words(text: str) -> list[str]:
    return [w for w in WORD.findall(str(text or "").lower()) if w not in STOP and len(w) > 1]


def singular(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def goal_text(row) -> str:
    return str(row["canonical_name"] or "")


def card_text(row) -> str:
    return " ".join(str(p) for p in (row["name"], row["title"], row["server"]) if p)


def score_cards(goals, cards, *, max_per_card: int, min_score: float) -> list[tuple[str, str, float]]:
    goal_words: list[set[str]] = []
    index: dict[str, list[int]] = defaultdict(list)
    for i, goal in enumerate(goals):
        ws = {singular(w) for w in words(goal_text(goal))}
        goal_words.append(ws)
        for w in ws:
            index[w].append(i)
    n = max(1, len(goals))
    idf = {w: math.log(1 + (n - len(ids) + 0.5) / (len(ids) + 0.5)) for w, ids in index.items()}
    norm = [math.sqrt(max(1, len(ws))) for ws in goal_words]

    out: list[tuple[str, str, float]] = []
    for card in cards:
        card_words = {singular(w) for w in words(card_text(card))}
        if not card_words:
            continue
        scores: dict[int, float] = defaultdict(float)
        shared: dict[int, int] = defaultdict(int)
        for w in card_words:
            for gi in index.get(w, ()):
                scores[gi] += idf.get(w, 1.0)
                shared[gi] += 1
        if not scores:
            continue
        ranked = sorted(
            ((gi, raw) for gi, raw in scores.items() if shared[gi] / max(1, len(goal_words[gi])) >= 0.5),
            key=lambda kv: -kv[1],
        )[:max_per_card]
        for gi, raw in ranked:
            score = raw / norm[gi]
            if score >= min_score:
                out.append((str(card["id"]), str(goals[gi]["id"]), round(score, 4)))
    return out


async def ensure_tool_goals(conn) -> dict[str, str]:
    """One canonical Goal per tool goal on K000; returns tool goal id -> Goal uuid."""
    out: dict[str, str] = {}
    for gid, title in TOOL_GOAL_TITLES.items():
        description = TOOL_GOALS[gid][0]
        goal_id = tool_goal_id(gid)
        await conn.execute("""
            INSERT INTO goals (id, canonical_name, description, status, provenance, created_from,
                               scope_type, visibility, home_shard_id, version)
            VALUES ($1::uuid, $2, $3, 'active', 'tool_goal_taxonomy', 'tool_goals',
                    'global', 'public', 'K000', 1)
            ON CONFLICT (id) DO UPDATE
                SET canonical_name = EXCLUDED.canonical_name,
                    description = EXCLUDED.description,
                    status = 'active',
                    t_invalid = NULL
        """, goal_id, title, description)
        out[gid] = goal_id
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max cards to map (0 = all)")
    ap.add_argument("--max-per-card", type=int, default=3)
    ap.add_argument("--min-score", type=float, default=3.0)
    ap.add_argument("--lexical", action="store_true", help="also map to specific issue-shaped Goals")
    ap.add_argument("--replace", action="store_true", help="delete existing rows first")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        if args.dry_run:
            tool_goals = {gid: tool_goal_id(gid) for gid in TOOL_GOAL_TITLES}
        else:
            tool_goals = await ensure_tool_goals(conn)

        cards = await conn.fetch(f"""
            SELECT id, name, title, description, server, tools_text, goals
            FROM tool_cards
            ORDER BY updated_at DESC
            {"LIMIT " + str(int(args.limit)) if args.limit else ""}
        """)
        print(f"cards: {len(cards)}  tool goals: {len(tool_goals)}")

        pairs: list[tuple[str, str, float, str]] = []
        # 1. taxonomy: the card's own tool_goal tags, weighted by how many of that goal's
        # marker words the card's own text carries (a "postgres" card scores higher for
        # "database" than a generic "data" card).
        marker_words = {gid: {singular(w) for w in TOOL_GOALS[gid][1]} for gid in TOOL_GOALS}
        for card in cards:
            card_words = {singular(w) for w in words(" ".join(str(p) for p in (
                card["name"], card["title"], card["description"], card["tools_text"],
            ) if p))}
            for gid in (card["goals"] or []):
                if gid not in tool_goals:
                    continue
                markers = marker_words.get(gid, set())
                hits = len(card_words & markers)
                score = 1.0 + (hits / max(1, len(markers))) * 5.0
                pairs.append((str(card["id"]), tool_goals[gid], round(score, 4), "taxonomy"))

        # 2. optional lexical: specific issue-shaped Goals
        if args.lexical:
            goals = await conn.fetch("""
                SELECT goal_id::text AS id, canonical_name
                FROM goal_search_index
                WHERE status IN ('active', 'candidate')
                ORDER BY t_created DESC NULLS LAST
            """)
            print(f"lexical goals: {len(goals)}")
            lexical = score_cards(goals, cards, max_per_card=args.max_per_card, min_score=args.min_score)
            pairs.extend((c, g, s, "lexical") for c, g, s in lexical)

        # dedupe: taxonomy wins, then highest score
        best: dict[tuple[str, str], tuple[float, str]] = {}
        for card_id, goal_id, score, source in pairs:
            key = (card_id, goal_id)
            if key not in best or source == "taxonomy" or score > best[key][0]:
                best[key] = (score, source)
        rows = [(c, g, s, src) for (c, g), (s, src) in best.items()]
        print(f"pairs: {len(rows)}")

        if args.dry_run:
            for card_id, goal_id, score, source in rows[:20]:
                print(f"  {card_id}  ->  {goal_id}  ({score}, {source})")
            return
        if args.replace:
            await conn.execute("DELETE FROM tool_card_goals")
        await conn.executemany("""
            INSERT INTO tool_card_goals (card_id, goal_id, score, source)
            VALUES ($1, $2::uuid, $3, $4)
            ON CONFLICT (card_id, goal_id) DO UPDATE
                SET score = EXCLUDED.score, source = EXCLUDED.source
        """, rows)
        print(f"wrote {len(rows)} mappings")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
