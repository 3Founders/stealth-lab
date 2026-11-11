"""Search the goal graph first, then show the ways and tools mapped to each Goal.

    python scripts/search_goals.py "check what the page looks like after login"
    python scripts/search_goals.py "send a slack message" --k 8
    python scripts/search_goals.py "..." --json

Read-only. It uses app/tool_routing/goal_router.py: exact/alias + full-text, optionally
semantic if --semantic is passed and the embedding model matches goals.embedding.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values

from app.tool_routing.goal_router import route_goal


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


async def run(args: argparse.Namespace) -> None:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    query_embedding = None
    if args.semantic:
        from app.tool_routing.providers import ToolEmbedder

        embedder = ToolEmbedder()
        if not embedder.available():
            raise SystemExit("--semantic needs the tool embedder (DEEPINFRA_KEY)")
        query_embedding = await embedder.embed_one(args.query, input_type="query")

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        hits = await route_goal(conn, args.query, limit=args.k, query_embedding=query_embedding)
    finally:
        await conn.close()

    if args.json:
        print(json.dumps([asdict(h) for h in hits], indent=2, default=str))
        return

    if not hits:
        print("no goals matched")
        return
    for i, hit in enumerate(hits, 1):
        print(f"\n{i}. {hit.canonical_name}  [{hit.matched_by}, score {hit.score:.3f}]")
        if hit.description:
            print(f"   {hit.description[:300]}")
        if hit.ways:
            print("   ways:")
            for way in hit.ways:
                print(f"     - {way.name}  ({way.verification_state}/{way.availability})")
        else:
            print("   ways: none")
        if hit.tools:
            print("   tools:")
            for tool in hit.tools:
                label = tool.title or tool.name or tool.server
                print(f"     - {label}  [{tool.server or tool.source}]  (score {tool.score:.3f})")
        else:
            print("   tools: none")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--semantic", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not args.query.strip():
        print("usage: python scripts/search_goals.py \"<task>\" [--k 5] [--semantic] [--json]")
        raise SystemExit(2)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
