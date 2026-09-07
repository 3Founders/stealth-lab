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
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.embeddings import Embedder
from app.services.ingestion_sources.skill_md import GitHubSkillSource, LocalDirSkillSource
from app.services.ingestion_sources import GitHubSkillCorpusSource, load_source_manifest
from app.services.skill_ingestion import persist_source_snapshot, run_skill_ingestion

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "config" / "skill_sources.yaml"


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


def _selected_specs(args: argparse.Namespace):
    manifest = load_source_manifest(args.manifest)
    if getattr(args, "source", None):
        return [manifest.by_id(args.source)]
    return [spec for spec in manifest.sources if spec.enabled]


async def _cmd_discover(args: argparse.Namespace) -> None:
    reports = []
    for spec in _selected_specs(args):
        adapter = GitHubSkillCorpusSource(spec)
        refs = list(adapter.discover())
        reports.append({
            **adapter.snapshot_metadata(),
            "skills_discovered": len(refs),
            "skill_paths": [ref.path for ref in refs],
        })
    print(json.dumps({"sources": reports}, indent=2, default=str))


async def _cmd_ingest_manifest(args: argparse.Namespace) -> None:
    from app.services.ingestion_jobs import (
        enqueue_skill_package_jobs, process_pending_jobs, resume_failed_skill_jobs,
    )
    pool = await create_pool(os.environ["DATABASE_URL"])
    results = []
    try:
        if args.resume:
            print(json.dumps({"resumed": await resume_failed_skill_jobs(pool)}))
        if args.queue_only or args.process_jobs:
            for spec in _selected_specs(args):
                adapter = GitHubSkillCorpusSource(spec)
                await persist_source_snapshot(pool, adapter)
                refs = list(adapter.discover())
                queued = await enqueue_skill_package_jobs(
                    pool,
                    source_spec={
                        "source_id": spec.id, "priority": spec.priority,
                        "source_type": spec.type, "repo": spec.repo,
                        "subtree": spec.path, "expected_format": spec.expected_format,
                        "ref": spec.ref,
                    },
                    refs=[{
                        "uri": ref.uri, "path": ref.path, "commit": ref.commit,
                    } for ref in refs if not args.skill_path or ref.path in args.skill_path],
                )
                results.append({"source": spec.id, "queued": queued,
                                "discovered": len(refs)})
            if args.process_jobs:
                results.append({"worker": await process_pending_jobs(
                    pool, limit=args.worker_limit,
                )})
            print(json.dumps({"results": results}, indent=2, default=str))
            return
        for spec in _selected_specs(args):
            adapter = GitHubSkillCorpusSource(
                spec, include_paths=set(args.skill_path or []),
            )
            snapshot = await persist_source_snapshot(pool, adapter)
            result = await run_skill_ingestion(
                pool, adapter, embedder=Embedder(),
                domain=(spec.repo if spec.type == "github_subtree" else None),
                created_by="structured_skill_ingestion_wave1", limit=args.limit,
            )
            results.append({
                "source": spec.id,
                "resolved_commit": str(snapshot["resolved_commit"]),
                "run_id": result["run_id"],
                "metrics": result["metrics"],
                "errors": [
                    {"status": outcome.status, "reason": outcome.reason}
                    for outcome in result["outcomes"] if outcome.status == "error"
                ],
            })
    finally:
        await pool.close()
    print(json.dumps({"results": results}, indent=2, default=str))


async def _cmd_retrieval_qa(args: argparse.Namespace) -> None:
    from app.services.applicability import find_applicable_procedures

    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    pool = await create_pool(os.environ["DATABASE_URL"])
    results = []
    try:
        embedder = Embedder()
        for case in suite:
            query_vec = await embedder.embed_one(case["query"], input_type="query")
            matches = await find_applicable_procedures(
                pool, goal_embedding=query_vec, require_verified=False, limit=5,
            )
            top = [{
                "procedure_id": str(m.get("procedure_id")), "name": m.get("name"),
                "source": (m.get("domain_payload") or {}).get("source"),
                "similarity": m.get("_similarity_score") or m.get("similarity"),
            } for m in matches]
            expected = case["expected_procedure_family"].lower()
            rank = next((i + 1 for i, m in enumerate(top)
                         if expected in (m.get("name") or "").lower()), None)
            results.append({**case, "top_5": top, "rank": rank})
    finally:
        await pool.close()
    payload = {"results": results}
    rendered = json.dumps(payload, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    if args.summary:
        print(json.dumps({
            "queries": len(results),
            "found_top_5": sum(result["rank"] is not None for result in results),
            "ranks": [{"query": result["query"], "rank": result["rank"]}
                      for result in results],
        }, indent=2))
    else:
        print(rendered)


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

    p_discover = sub.add_parser("discover", help="Inspect manifest sources without canonical writes.")
    p_discover.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p_discover.add_argument("--source")
    p_discover.set_defaults(func=_cmd_discover, needs_db=False)

    p_ingest = sub.add_parser("ingest", help="Ingest manifest sources into candidate staging.")
    p_ingest.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p_ingest.add_argument("--source")
    p_ingest.add_argument("--limit", type=int)
    p_ingest.add_argument("--skill-path", action="append", help="Exact repo-local SKILL.md path; repeatable.")
    p_ingest.add_argument("--resume", action="store_true")
    p_ingest.add_argument("--queue-only", action="store_true",
                          help="Queue one retryable job per package; do not ingest inline.")
    p_ingest.add_argument("--process-jobs", action="store_true",
                          help="Queue packages and process a bounded worker batch inline.")
    p_ingest.add_argument("--worker-limit", type=int, default=100)
    p_ingest.set_defaults(func=_cmd_ingest_manifest, needs_db=True)

    p_qa = sub.add_parser("retrieval-qa", help="Run the structured-skill retrieval suite.")
    p_qa.add_argument("--suite", required=True)
    p_qa.add_argument("--output")
    p_qa.add_argument("--summary", action="store_true")
    p_qa.set_defaults(func=_cmd_retrieval_qa, needs_db=True)

    args = parser.parse_args()
    if getattr(args, "needs_db", True) and "DATABASE_URL" not in os.environ:
        print("REFUSED: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
