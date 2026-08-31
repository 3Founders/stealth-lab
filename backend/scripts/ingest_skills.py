#!/usr/bin/env python
"""
Phase 2 (Global Procedural Library ingestion compiler, prompts.md) --
agent D's deliverable in that session's own subagent split
(.scratch/phase2_ingestion_plan.md), landed here after that session hung
before writing it.

Thin CLI over the real, already-tested compiler
(app/services/skill_ingestion.py::run_skill_ingestion) and the real
source adapters (app/services/ingestion_sources/skill_md.py). No new
business logic here -- same "extend the current CLI/scripts" discipline
run_ingestion.py already established.

Usage:
    python scripts/ingest_skills.py skill-dir ./path/to/skills [--domain D] [--dry-run]
    python scripts/ingest_skills.py skill-repo https://github.com/owner/repo [--ref R] [--domain D]
    python scripts/ingest_skills.py search "explore an unfamiliar repository" [--k 5]

Windows note (same as every other script in this repo): run with
`python`, not `python3`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.embeddings import Embedder
from app.services.ingestion_sources.skill_md import GitHubSkillSource, LocalDirSkillSource
from app.services.skill_ingestion import run_skill_ingestion


async def _cmd_skill_dir(args: argparse.Namespace) -> None:
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        adapter = LocalDirSkillSource(args.path)
        if args.dry_run:
            discovered = list(adapter.discover())
            print(json.dumps(
                {"dry_run": True, "sources_seen": len(discovered),
                 "uris": [ref.uri for ref in discovered]},
                indent=2,
            ))
            return
        result = await run_skill_ingestion(
            pool, adapter, embedder=Embedder(), domain=args.domain,
            created_by="ingest_skills_cli",
        )
        print(json.dumps({"run_id": result["run_id"], "metrics": result["metrics"]}, indent=2))
    finally:
        await pool.close()


async def _cmd_skill_repo(args: argparse.Namespace) -> None:
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        adapter = GitHubSkillSource(args.repo_url, ref=args.ref)
        result = await run_skill_ingestion(
            pool, adapter, embedder=Embedder(), domain=args.domain,
            created_by="ingest_skills_cli",
        )
        print(json.dumps({"run_id": result["run_id"], "metrics": result["metrics"]}, indent=2))
    finally:
        await pool.close()


async def _cmd_search(args: argparse.Namespace) -> None:
    from app.services.applicability import find_applicable_procedures

    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        embedder = Embedder()
        query_vec = await embedder.embed_one(args.query, input_type="query")
        matches = await find_applicable_procedures(
            pool, goal_embedding=query_vec, require_verified=False, limit=args.k,
        )
        for m in matches:
            print(f"{m.get('name')!r:50} v{m.get('version')} "
                  f"state={m.get('verification_state')} similarity={m.get('similarity')}")
        if not matches:
            print("(no matches)")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_dir = sub.add_parser("skill-dir", help="Ingest every SKILL.md under a local directory tree.")
    p_dir.add_argument("path")
    p_dir.add_argument("--domain", default=None)
    p_dir.add_argument("--dry-run", action="store_true", help="List discovered sources without ingesting.")
    p_dir.set_defaults(func=_cmd_skill_dir)

    p_repo = sub.add_parser("skill-repo", help="Ingest every SKILL.md in a GitHub repository.")
    p_repo.add_argument("repo_url")
    p_repo.add_argument("--ref", default="HEAD")
    p_repo.add_argument("--domain", default=None)
    p_repo.set_defaults(func=_cmd_skill_repo)

    p_search = sub.add_parser("search", help="Search ingested procedures by goal.")
    p_search.add_argument("query")
    p_search.add_argument("--k", type=int, default=5)
    p_search.set_defaults(func=_cmd_search)

    args = parser.parse_args()
    if "DATABASE_URL" not in os.environ:
        print("REFUSED: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
