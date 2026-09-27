"""`python -m app.ingestion.admin benchmark-import ...`

    benchmark-import --source bigcodebench --file bigcodebench-v0.1.4.parquet --dry-run
    benchmark-import --source bigcodebench --download data/ --manifest bcb-manifest.json [--limit 20] [--embed]

--dry-run parses and reports (domains, split, exclusions) without writing anything.
The manifest (task -> goal, benchmark, split, visible tests) is what runners and
`routing-import` use.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

COMMANDS = ("benchmark-import",)


def add_parsers(sub: Any) -> None:
    p = sub.add_parser("benchmark-import")
    p.add_argument("--source", choices=["bigcodebench"], required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="local dataset file (.parquet / .jsonl / .jsonl.gz)")
    src.add_argument("--download", metavar="DIR", help="download the dataset into DIR first")
    p.add_argument("--version", default=None)
    p.add_argument("--limit", type=int)
    p.add_argument("--fit-fraction", type=float, default=0.6)
    p.add_argument("--seed", default="kel-v1")
    p.add_argument("--manifest", help="write the task -> goal/benchmark manifest here (JSON)")
    p.add_argument("--embed", action="store_true", help="embed the Goals (needs an embedding provider)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--identity", choices=["none", "model"], default="none",
                   help="model: production semantic identity + goal-abstraction placement (needs a judge)")
    p.add_argument("--no-domain-edges", action="store_true", help="leave the Goal hierarchy to production placement")


async def run(pool: Any, a: Any) -> int:
    from app.benchmarks import bigcodebench as bcb
    from app.benchmarks.importer import import_tasks
    from app.benchmarks.tasks import assign_splits

    version = a.version or bcb.LATEST_VERSION
    path = bcb.download(a.download, version=version) if a.download else Path(a.file)
    tasks = bcb.tasks_from_rows(bcb.load_rows(path), version=version)
    assign_splits(tasks, fit_fraction=a.fit_fraction, seed=a.seed)
    embedder = None
    if a.embed and not a.dry_run:
        from app.services.embeddings import Embedder

        embedder = Embedder(rate_limit_pool=pool)
    report = await import_tasks(pool, tasks, embedder=embedder, limit=a.limit, dry_run=a.dry_run,
                                judge_mode=a.identity, domain_edges=not a.no_domain_edges)
    manifest = report.pop("manifest", None)
    if manifest is not None and a.manifest:
        Path(a.manifest).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        report["manifest_file"] = a.manifest
    print(json.dumps(report, default=str, indent=2))
    return 0
