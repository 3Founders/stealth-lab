"""Enqueue ingestion work (idempotent; safe to re-run).

    # one skill package (immutable commit)
    python -m app.ingestion.enqueue skill-package --source-id anthropic-skills \
        --repo anthropics/skills --commit <sha> --path skills/pdf/SKILL.md

    # a batch from a manifest of packages (JSON lines: {"source_id","repo","commit","path", ...})
    python -m app.ingestion.enqueue skill-package --manifest packages.jsonl

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
from app.ingestion.config import control_database_url

SKILL_JOB = "ingest_skill_package"


def skill_package_key(source_id: str, commit: str, path: str) -> str:
    """Content-addressed identity of one immutable package: same source + commit + path = same job."""
    return "skill:" + hashlib.sha256(f"{source_id}\x00{commit}\x00{path}".encode()).hexdigest()[:32]


async def enqueue_skill_packages(pool, packages: list[dict], *, config_version: Optional[str] = None) -> dict:
    created = duplicate = 0
    for pkg in packages:
        payload = {k: v for k, v in pkg.items()}
        for req in ("source_id", "repo", "commit", "path"):
            if not payload.get(req):
                raise ValueError(f"package missing {req!r}: {pkg}")
        _, made = await q.enqueue(
            pool, SKILL_JOB, payload, idempotency_key=skill_package_key(payload["source_id"], payload["commit"], payload["path"]),
            source_id=payload["source_id"], scope_type="global", visibility="public", config_version=config_version)
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
