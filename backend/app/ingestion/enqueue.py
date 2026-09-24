"""Enqueue ingestion work (idempotent; safe to re-run).

    # one skill package (immutable commit)
    python -m app.ingestion.enqueue skill-package --source-id anthropic-skills \
        --repo anthropics/skills --commit <sha> --path skills/pdf/SKILL.md

    # a batch from a manifest of packages (JSON lines: {"source_id","repo","commit","path", ...})
    python -m app.ingestion.enqueue skill-package --manifest packages.jsonl

    # GitHub-hosted documents of any adapter format (Markdown/HTML/PDF/DOCX/AGENTS.md/runbooks...)
    # manifest JSON lines: {"repository": "owner/repo", "path": "...", "commit": "<sha>", ...extra}
    python -m app.ingestion.enqueue document --manifest docs.jsonl

    # whole repos: reusable files (UI/CI/infra/scripts/...) -> Goal + 1-step Procedure each
    python -m app.ingestion.enqueue repo --repository shadcn-ui/ui [--commit <sha>] [--domains ui_design,ci_cd]

    # any registered job type with an explicit payload and scope
    python -m app.ingestion.enqueue raw --job-type normalize_trace_event --payload '{"trace_event_id": "..."}' \
        --idempotency-key trace-event:123 --scope-type session --scope-entity-id s1 --visibility private --owner-id u1

Re-running with the same identity is a no-op (unique (job_type, idempotency_key)).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from typing import Optional

from app.ingestion import queue as q
from app.services.auth_context import JobAuthority
from app.ingestion.config import control_database_url

SKILL_JOB = "ingest_skill_package"
DOC_JOB = "ingest_document"
REPO_JOB = "ingest_repo"


def skill_package_key(source_id: str, commit: str, path: str) -> str:
    """Content-addressed identity of one immutable package: same source + commit + path = same job."""
    return "skill:" + hashlib.sha256(f"{source_id}\x00{commit}\x00{path}".encode()).hexdigest()[:32]


async def enqueue_skill_packages(pool, packages: list[dict], *, config_version: Optional[str] = None,
                                 submitted_by: Optional[str] = None) -> dict:
    created = duplicate = 0
    for pkg in packages:
        payload = {k: v for k, v in pkg.items()}
        for req in ("source_id", "repo", "commit", "path"):
            if not payload.get(req):
                raise ValueError(f"package missing {req!r}: {pkg}")
        _, made = await q.enqueue(
            pool, SKILL_JOB, payload, idempotency_key=skill_package_key(payload["source_id"], payload["commit"], payload["path"]),
            source_id=payload["source_id"], scope_type="global", visibility="public", config_version=config_version,
            authority=JobAuthority(submitted_by_service_id=submitted_by, scope="global_public", visibility="public"))
        created += made
        duplicate += not made
    return {"created": created, "duplicate": duplicate}


def document_key(repository: str, commit: str, path: str) -> str:
    return "doc:" + hashlib.sha256(f"{repository}\x00{commit}\x00{path}".encode()).hexdigest()[:32]


async def enqueue_documents(pool, docs: list[dict], *, config_version: Optional[str] = None,
                            submitted_by: Optional[str] = None) -> dict:
    """One ingest_document job per commit-pinned GitHub file (handled by
    ingestion_jobs.handle_ingest_document). A pinned commit is required: it is the job's identity."""
    created = duplicate = 0
    for doc in docs:
        payload = dict(doc)
        if payload.get("uri") and not payload.get("repository"):
            # A web URL (UrlFetchAdapter): no commit exists, so the URL itself is the identity.
            key, source = "url:" + hashlib.sha256(payload["uri"].encode()).hexdigest()[:32], payload["uri"]
        else:
            for req in ("repository", "path", "commit"):
                if not payload.get(req):
                    raise ValueError(f"document missing {req!r}: {doc}")
            key = document_key(payload["repository"], payload["commit"], payload["path"])
            source = payload["repository"]
        _, made = await q.enqueue(
            pool, DOC_JOB, payload, idempotency_key=key,
            source_id=payload.get("source_id") or source, scope_type="global", visibility="public",
            config_version=config_version,
            authority=JobAuthority(submitted_by_service_id=submitted_by, scope="global_public", visibility="public"))
        created += made
        duplicate += not made
    return {"created": created, "duplicate": duplicate}


async def enqueue_repos(pool, repos: list[dict], *, submitted_by: Optional[str] = None) -> dict:
    """One ingest_repo job per (repository, commit); a missing commit is pinned to HEAD now."""
    from app.services.ingestion_sources.github_corpus import _default_http_get

    created = duplicate = 0
    for repo in repos:
        payload = {k: v for k, v in repo.items() if v is not None}
        if not payload.get("repository"):
            raise ValueError(f"repo missing 'repository': {repo}")
        if not payload.get("commit"):
            status, body = _default_http_get(f"https://api.github.com/repos/{payload['repository']}/commits/HEAD")
            if status != 200:
                raise ValueError(f"cannot resolve HEAD for {payload['repository']}: HTTP {status}")
            payload["commit"] = json.loads(body.decode("utf-8"))["sha"]
        key = "repo:" + hashlib.sha256(f"{payload['repository']}\x00{payload['commit']}".encode()).hexdigest()[:32]
        _, made = await q.enqueue(
            pool, REPO_JOB, payload, idempotency_key=key, source_id=payload["repository"], scope_type="global",
            visibility="public", authority=JobAuthority(submitted_by_service_id=submitted_by, scope="global_public",
                                                        visibility="public"))
        created += made
        duplicate += not made
    return {"created": created, "duplicate": duplicate}


def _parse(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m app.ingestion.enqueue", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("skill-package")
    s.add_argument("--manifest", help="JSON-lines file of packages")
    s.add_argument("--source-id")
    s.add_argument("--repo")
    s.add_argument("--commit")
    s.add_argument("--path")
    s.add_argument("--config-version")
    d = sub.add_parser("document")
    d.add_argument("--manifest", help="JSON-lines file of documents")
    d.add_argument("--repository")
    d.add_argument("--path")
    d.add_argument("--commit")
    d.add_argument("--config-version")
    rp = sub.add_parser("repo")
    rp.add_argument("--manifest", help='JSON lines {"repository", "commit"?, "domains"?, "per_domain"?}')
    rp.add_argument("--repository")
    rp.add_argument("--commit")
    rp.add_argument("--domains", help="comma-separated DOMAINS names (default: all)")
    rp.add_argument("--per-domain", type=int)
    r = sub.add_parser("raw")
    r.add_argument("--job-type", required=True)
    r.add_argument("--payload", required=True, help="JSON object")
    r.add_argument("--idempotency-key", required=True)
    r.add_argument("--source-id")
    r.add_argument("--scope-type", required=True)
    r.add_argument("--scope-entity-id")
    r.add_argument("--owner-id")
    r.add_argument("--visibility", default="public")
    r.add_argument("--config-version")
    return p.parse_args(argv)


async def _amain(a: argparse.Namespace) -> int:
    from app.db.session import create_pool

    pool = await create_pool(control_database_url(), max_size=2)
    try:
        if a.cmd == "skill-package":
            if a.manifest:
                with open(a.manifest, encoding="utf-8") as fh:
                    pkgs = [json.loads(line) for line in fh if line.strip()]
            else:
                pkgs = [{"source_id": a.source_id, "repo": a.repo, "commit": a.commit, "path": a.path}]
            print(json.dumps(await enqueue_skill_packages(pool, pkgs, config_version=a.config_version)))
        elif a.cmd == "document":
            if a.manifest:
                with open(a.manifest, encoding="utf-8") as fh:
                    docs = [json.loads(line) for line in fh if line.strip()]
            else:
                docs = [{"repository": a.repository, "path": a.path, "commit": a.commit}]
            print(json.dumps(await enqueue_documents(pool, docs, config_version=a.config_version)))
        elif a.cmd == "repo":
            if a.manifest:
                with open(a.manifest, encoding="utf-8") as fh:
                    repos = [json.loads(line) for line in fh if line.strip()]
            else:
                repos = [{"repository": a.repository, "commit": a.commit, "per_domain": a.per_domain,
                          "domains": a.domains.split(",") if a.domains else None}]
            print(json.dumps(await enqueue_repos(pool, repos)))
        else:
            jid, made = await q.enqueue(
                pool, a.job_type, json.loads(a.payload), idempotency_key=a.idempotency_key, source_id=a.source_id,
                scope_type=a.scope_type, scope_entity_id=a.scope_entity_id, owner_id=a.owner_id, visibility=a.visibility,
                config_version=a.config_version)
            print(json.dumps({"job_id": jid, "created": made}))
        return 0
    finally:
        await pool.close()


def main(argv=None) -> None:
    sys.exit(asyncio.run(_amain(_parse(argv))))


if __name__ == "__main__":
    main()
