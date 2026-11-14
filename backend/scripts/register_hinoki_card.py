"""Register Hinoki's tool card (docs/goal_router.md P1-09), so tool search and the router can recommend it.

    python scripts/register_hinoki_card.py --dry-run     # print the card, write nothing
    python scripts/register_hinoki_card.py               # upsert it into tool_cards (and embed it, when an embedder is set)

The card is source "kell" with server "hinoki": the catalog sync never deletes it (it only prunes registry cards), and
use_tool(server="hinoki", ...) reaches the adapter (app/tool_providers/hinoki), which the gate holds to the R3 policy
row db/188 seeds for server 'hinoki'. Uses DATABASE_URL from the environment, else backend/.env: check which database
that is before writing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import dotenv_values

PIN_SCORE = 9.0   # Exa's pin to the web Goal


def card():
    from app.tool_providers.hinoki import TOOLS
    from app.tool_providers.hinoki.mcp_server import list_tools
    from app.tool_routing.catalog import ToolCard, tag_goals

    description = ("Scan a repository for security vulnerabilities: Hinoki's security model reviews a public GitHub "
                   "repository's code and reports findings (injection, auth flaws, SSRF, path traversal, secrets), "
                   "reviews a diff for the vulnerabilities it introduces, explains a finding and proposes a fix as a "
                   "patch that is never applied. Run by kell with its own key, no install. Read-only; R3, so it needs "
                   "a task certificate and an approval.")
    tools_text = " ".join(f"{t['name']}: {t['description']}." for t in list_tools())
    c = ToolCard(id="kell:hinoki", source="kell", name="hinoki", title="Hinoki security review (via kell)",
                 description=description, server="hinoki", how="use_tool", url="https://hinoki.security",
                 expansion="scan this repository for vulnerabilities; security audit of a codebase; find security "
                           "bugs; static application security testing; SAST; review a pull request for security "
                           "issues; is this code vulnerable; injection, XSS, SSRF, path traversal; secure code review",
                 tools=tuple(TOOLS), tools_text=tools_text,
                 extra={"risk": "R3", "via_kell": True, "adapter": "app.tool_providers.hinoki"})
    return ToolCard(**{**c.__dict__, "goals": tag_goals(c.text)})


async def run(args: argparse.Namespace) -> int:
    c = card()
    print(json.dumps({k: v for k, v in c.__dict__.items()}, indent=2, default=list))
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    dsn = os.environ.get("DATABASE_URL") or dotenv_values(Path(__file__).resolve().parents[1] / ".env").get(
        "DATABASE_URL") or ""
    if not dsn:
        raise SystemExit("no DATABASE_URL in the environment or backend/.env")
    import asyncpg

    from app.tool_routing import store
    from app.tool_routing.providers import ToolEmbedder

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=30)
    try:
        # the router recommends a Goal's mapped cards: pin the card to the security Goal, as Exa is pinned to web
        # (a manual pin survives map_tool_cards_to_goals.py --replace)
        from map_tool_cards_to_goals import tool_goal_id

        goal_id = tool_goal_id("security")
        async with pool.acquire() as conn:
            await store.upsert_cards(conn, [c])
            if not await conn.fetchval("SELECT 1 FROM goals WHERE id = $1::uuid AND t_invalid IS NULL", goal_id):
                raise SystemExit("no security tool Goal: run scripts/map_tool_cards_to_goals.py first")
            await conn.execute("""INSERT INTO tool_card_goals (card_id, goal_id, score, source)
                                  VALUES ($1, $2::uuid, $3, 'manual')
                                  ON CONFLICT (card_id, goal_id) DO UPDATE SET score = EXCLUDED.score,
                                                                              source = 'manual'""",
                               c.id, goal_id, PIN_SCORE)
        print(f"written: {c.id}, pinned to the security Goal {goal_id} ({PIN_SCORE}, manual)")
        from app.tool_routing import ranking

        await ranking.mark_dirty(pool, [goal_id], reason="hinoki card")
        try:
            await ranking.drain(pool, limit=1, min_age_s=0)
        except Exception as exc:  # noqa: BLE001 -- the server's own drain recomputes it later
            print(f"ranking not recomputed now ({type(exc).__name__}); the server's drain will")
        e = ToolEmbedder()
        if e.available():
            await store._embed_missing(pool, [c], e, print)
        else:
            print("no embedder configured: the card is found by its words until the next catalog sync embeds it")
    finally:
        await pool.close()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Register Hinoki's tool card.")
    ap.add_argument("--dry-run", action="store_true")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
