#!/usr/bin/env python
"""
ONE coherent user-facing bootstrap command (directive req #4, #41-42).

Before this script a user had to know and run several separate internal
entry points by hand to get full coverage: run_skill_ingestion() (via a
hand-built adapter) for repo SKILL.md/AGENTS.md/CLAUDE.md/CI-workflow/
runbook files, chat_history_import.import_chat_history() for Claude/
ChatGPT exports, and ingest_transcripts.py's process_transcript_session()
+ ingestion_jobs' job-queue functions for agent traces -- three different
modules, three different call shapes, no single place a user runs once.
This script is a thin orchestrator over exactly those existing functions
-- it reimplements none of their logic -- and prints ONE truthful,
non-fabricated summary in the shape the directive names: sources
scanned, episodes found, procedural items found, candidate procedures
created, duplicates merged, non-procedural material rejected,
insufficient-evidence material rejected.

Every number below is a real return value from a real already-committed
function; nothing is estimated or hardcoded. A source with no input path
given is reported as SKIPPED with a reason, never silently treated as
zero.

Git history: `--repo-root` also drives `app/local_agent/git_history_bootstrap.py`
(real `git log` via subprocess, conservative fix+test / migration+code+test
pattern formation -- never one candidate per bare commit). It is DB-free:
candidates land in the same local `LocalProcedureStore` under `--workspace`
that chat-export candidates use, never the shared Postgres `procedures`
table -- a personal repo's commit history is exactly the kind of private,
one-workspace material `local_store.py`'s module docstring describes.

Sources orchestrated (each optional; at least one input is required):
  --repo-root DIR        SKILL.md, AGENTS.md/CLAUDE.md, CI workflow jobs,
                          runbook-shaped docs, AND real git commit history
                          under DIR (repo skills/instructions/workflows via
                          run_skill_ingestion() into the shared `procedures`
                          table, needs DATABASE_URL; git history via
                          bootstrap_git_history() into the local
                          LocalProcedureStore under --workspace, DB-free).
  --claude-export FILE   A real claude.ai "conversations.json" export.
                          DB-free -- candidates land in the local
                          LocalProcedureStore under --workspace.
  --chatgpt-export FILE  A real chatgpt.com "conversations.json" export.
                          Same DB-free path as --claude-export.
  --traces-dir DIR       Claude Code session transcript files
                          (~/.claude/projects/<mangled>/*.jsonl shape).
                          Needs DATABASE_URL -- episodes are assembled and
                          persisted via process_transcript_session().
                          --promote-limit/--extract-limit optionally chain
                          the same claim-promotion / procedure-extraction
                          job queue run_ingestion.py drives (off by
                          default: each costs a real embedding/LLM call).

Usage (from backend/, with a populated .env or DATABASE_URL exported):
    python scripts/bootstrap.py --repo-root . --claude-export ~/claude_export/conversations.json
    python scripts/bootstrap.py --traces-dir ~/.claude/projects/-Users-me-repo
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

_REPO_MD_YAML_SUFFIXES = (".md", ".yml", ".yaml")


async def run_repo_procedural(pool, embedder, root: Path, *, created_by: str) -> dict:
    """Repo skills/instructions/workflows: SKILL.md, AGENTS.md/CLAUDE.md,
    CI workflow jobs, runbook-shaped docs. Drives each real adapter
    through the real run_skill_ingestion() compiler -- reimplements
    nothing. `non_procedural_skipped` counts .md/.yml/.yaml files this
    walk found that NO adapter's own discover() gate ever offered as a
    candidate (rejected before fetch/compile -- e.g. a plain prose
    README with no numbered steps and no code fence)."""
    from app.services.ingestion_sources.skill_md import LocalDirSkillSource
    from app.services.ingestion_sources.repo_procedural import (
        LocalDirAgentsMdSource,
        LocalDirCIWorkflowSource,
        LocalDirRunbookSource,
    )
    from app.services.skill_ingestion import run_skill_ingestion

    adapters = [
        LocalDirSkillSource(root),
        LocalDirAgentsMdSource(root),
        LocalDirCIWorkflowSource(root),
        LocalDirRunbookSource(root),
    ]

    per_adapter: dict[str, dict] = {}
    totals = {"artifacts_seen": 0, "candidates": 0, "accepted": 0,
              "duplicates": 0, "unchanged": 0, "stale": 0, "rejected": 0, "errors": 0}
    discovered_paths: set[str] = set()

    for adapter in adapters:
        result = await run_skill_ingestion(
            pool, adapter, embedder=embedder, created_by=created_by,
        )
        metrics = result["metrics"]
        per_adapter[adapter.source_type] = metrics
        for key in totals:
            totals[key] += metrics.get(key, 0)
        for ref in adapter.discover():
            discovered_paths.add(ref.path.split("#", 1)[0])

    all_files = [
        str(p.relative_to(root).as_posix())
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _REPO_MD_YAML_SUFFIXES
    ]
    non_procedural_skipped = sum(1 for f in all_files if f not in discovered_paths)

    return {
        "per_adapter": per_adapter,
        "totals": totals,
        "files_scanned": len(all_files),
        "non_procedural_skipped": non_procedural_skipped,
    }


def run_git_history(local_store, repo_root: Path) -> dict:
    """Real git commit history under `repo_root` -> conservative
    fix+test / migration+code+test candidates -> the same local
    LocalProcedureStore chat-export candidates use. DB-free, drives the
    real bootstrap_git_history() -- no reimplementation here."""
    from app.local_agent.git_history_bootstrap import bootstrap_git_history

    return bootstrap_git_history(local_store, str(repo_root))


def run_chat_export(local_store, path: Path, source_type: str) -> dict:
    """Claude or ChatGPT chat history export -> local procedure
    candidates, via the real import_chat_history() -- no reimplementation
    of parsing or evidence classification."""
    from app.local_agent.chat_history_import import import_chat_history

    return import_chat_history(str(path), source_type, local_store)


async def run_agent_traces(pool, trace_dir: Path, *, owner_id, project_id,
                            promote_limit: int, extract_limit: int) -> dict:
    """Agent traces: real Claude Code session transcripts -> real
    assembled episodes (process_transcript_session()), optionally chained
    into the same claim-promotion / procedure-extraction job queue
    run_ingestion.py drives (enqueue_pending_claim_promotions(),
    enqueue_pending_procedure_extractions(), process_pending_jobs()) --
    off by default since each queued job costs a real embedding or LLM
    call, same safety posture run_ingestion.py itself uses."""
    from app.services.ingestion_jobs import (
        enqueue_pending_claim_promotions,
        enqueue_pending_procedure_extractions,
        process_pending_jobs,
    )
    from app.services.trace_worker import process_transcript_session

    files = sorted(trace_dir.glob("*.jsonl")) if trace_dir.is_dir() else []
    sessions = []
    episode_totals = {"episodes": 0, "child_episodes": 0}
    for f in files:
        stats = await process_transcript_session(
            pool, f, owner_id=owner_id, visibility="private", project_id=project_id,
        )
        sessions.append({"session": f.stem, **stats})
        episode_totals["episodes"] += stats.get("episodes", 0)
        episode_totals["child_episodes"] += stats.get("child_episodes", 0)

    requeued = None
    if promote_limit > 0:
        requeued = await enqueue_pending_claim_promotions(pool, limit=promote_limit)
    extracted = None
    if extract_limit > 0:
        extracted = await enqueue_pending_procedure_extractions(pool, limit=extract_limit)
    job_totals = await process_pending_jobs(pool) if (requeued or extracted) else None

    return {
        "files_processed": len(files),
        "sessions": sessions,
        "episode_totals": episode_totals,
        "requeued_promotions": requeued,
        "queued_extractions": extracted,
        "jobs": job_totals,
    }


def _print_summary(sources_run: dict, sources_skipped: list[dict]) -> dict:
    episodes_found = 0
    procedural_items_found = 0
    candidate_procedures_created = 0
    duplicates_merged = 0
    non_procedural_rejected = 0
    insufficient_evidence_rejected = 0

    if "repo_procedural" in sources_run:
        r = sources_run["repo_procedural"]
        procedural_items_found += r["totals"]["candidates"]
        candidate_procedures_created += r["totals"]["accepted"]
        duplicates_merged += r["totals"]["duplicates"]
        non_procedural_rejected += r["non_procedural_skipped"]
        insufficient_evidence_rejected += r["totals"]["rejected"]

    for key in ("claude_history", "chatgpt_history"):
        if key in sources_run:
            c = sources_run[key]
            procedural_items_found += c["candidates_created"]
            candidate_procedures_created += c["candidates_created"]
            insufficient_evidence_rejected += c["discussion_only_skipped"]

    if "agent_traces" in sources_run:
        t = sources_run["agent_traces"]
        episodes_found += t["episode_totals"]["episodes"]
        if t["queued_extractions"]:
            procedural_items_found += t["queued_extractions"]["enqueued"]

    git_history_summary = "not run -- --repo-root not given"
    if "git_history" in sources_run:
        g = sources_run["git_history"]
        procedural_items_found += g["candidates_formed"]
        candidate_procedures_created += g["captured"]
        duplicates_merged += g["merged"]
        # bare commits with no cross-commit test/migration evidence are
        # the git-history analogue of "insufficient evidence" -- counted
        # honestly, never silently dropped.
        insufficient_evidence_rejected += g["skipped_bare_commits"]
        git_history_summary = (
            f"commits_scanned={g['commits_scanned']} "
            f"candidates_formed={g['candidates_formed']} "
            f"captured={g['captured']} merged={g['merged']} "
            f"skipped_bare_commits={g['skipped_bare_commits']}"
        )

    summary = {
        "sources_scanned": sorted(sources_run.keys()),
        "sources_skipped": sources_skipped,
        "episodes_found": episodes_found,
        "procedural_items_found": procedural_items_found,
        "candidate_procedures_created": candidate_procedures_created,
        "duplicates_merged": duplicates_merged,
        "non_procedural_rejected": non_procedural_rejected,
        "insufficient_evidence_rejected": insufficient_evidence_rejected,
        "git_history": git_history_summary,
        "detail": sources_run,
    }

    print("=== StealthLab bootstrap summary ===")
    print(f"sources scanned:                 {summary['sources_scanned']}")
    for skip in sources_skipped:
        print(f"  SKIPPED {skip['name']}: {skip['reason']}")
    print(f"episodes found:                  {episodes_found}")
    print(f"procedural items found:          {procedural_items_found}")
    print(f"candidate procedures created:    {candidate_procedures_created}")
    print(f"duplicates merged/skipped:       {duplicates_merged}")
    print(f"non-procedural material rejected:      {non_procedural_rejected}")
    print(f"insufficient-evidence material rejected: {insufficient_evidence_rejected}")
    print(f"git history: {summary['git_history']}")
    return summary


async def main_async(args: argparse.Namespace) -> dict:
    sources_run: dict = {}
    sources_skipped: list[dict] = []
    database_url = os.environ.get("DATABASE_URL")

    needs_db = bool(args.repo_root or args.traces_dir)
    pool = None
    embedder = None
    if needs_db:
        if not database_url:
            if args.repo_root:
                sources_skipped.append({"name": "repo_procedural", "reason": "DATABASE_URL not set"})
            if args.traces_dir:
                sources_skipped.append({"name": "agent_traces", "reason": "DATABASE_URL not set"})
        else:
            from app.db.session import create_pool
            from app.services.embeddings import Embedder
            pool = await create_pool(database_url)
            embedder = Embedder()

    try:
        if args.repo_root:
            if pool is not None:
                sources_run["repo_procedural"] = await run_repo_procedural(
                    pool, embedder, args.repo_root, created_by="bootstrap",
                )
        else:
            sources_skipped.append({"name": "repo_procedural", "reason": "--repo-root not given"})

        if args.claude_export or args.chatgpt_export or args.repo_root:
            from app.local_agent.local_store import LocalProcedureStore
            local_store = LocalProcedureStore(str(args.workspace))
            if args.claude_export:
                sources_run["claude_history"] = run_chat_export(
                    local_store, args.claude_export, "claude")
            else:
                sources_skipped.append({"name": "claude_history", "reason": "--claude-export not given"})
            if args.chatgpt_export:
                sources_run["chatgpt_history"] = run_chat_export(
                    local_store, args.chatgpt_export, "chatgpt")
            else:
                sources_skipped.append({"name": "chatgpt_history", "reason": "--chatgpt-export not given"})
            if args.repo_root:
                sources_run["git_history"] = run_git_history(local_store, args.repo_root)
        else:
            sources_skipped.append({"name": "claude_history", "reason": "--claude-export not given"})
            sources_skipped.append({"name": "chatgpt_history", "reason": "--chatgpt-export not given"})
            sources_skipped.append({"name": "git_history", "reason": "--repo-root not given"})

        if args.traces_dir:
            if pool is not None:
                sources_run["agent_traces"] = await run_agent_traces(
                    pool, args.traces_dir, owner_id=args.owner_id, project_id=args.project_id,
                    promote_limit=args.promote_limit, extract_limit=args.extract_limit,
                )
        else:
            sources_skipped.append({"name": "agent_traces", "reason": "--traces-dir not given"})
    finally:
        if pool is not None:
            await pool.close()

    return _print_summary(sources_run, sources_skipped)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--repo-root", type=Path, default=None,
                     help="repo directory to scan for SKILL.md / AGENTS.md / CLAUDE.md / "
                          "CI workflows / runbooks")
    ap.add_argument("--claude-export", type=Path, default=None,
                     help="path to a real claude.ai conversations.json export")
    ap.add_argument("--chatgpt-export", type=Path, default=None,
                     help="path to a real chatgpt.com conversations.json export")
    ap.add_argument("--traces-dir", type=Path, default=None,
                     help="directory of Claude Code session transcript .jsonl files")
    ap.add_argument("--workspace", type=Path, default=Path.cwd(),
                     help="workspace whose .stealthlab/local_procedures.db receives chat-history "
                          "candidates (default: cwd)")
    ap.add_argument("--owner-id", default=None)
    ap.add_argument("--project-id", default=None)
    ap.add_argument("--promote-limit", type=int, default=0, metavar="N",
                     help="agent-traces only: re-enqueue up to N claim promotions "
                          "(costs one real embedding call each; 0 disables)")
    ap.add_argument("--extract-limit", type=int, default=0, metavar="N",
                     help="agent-traces only: enqueue up to N procedure extractions "
                          "(costs one real LLM call each; 0 disables)")
    args = ap.parse_args()

    if not any((args.repo_root, args.claude_export, args.chatgpt_export, args.traces_dir)):
        ap.error("at least one of --repo-root / --claude-export / --chatgpt-export / "
                  "--traces-dir is required")

    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
