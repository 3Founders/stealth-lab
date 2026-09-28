"""`python -m app.ingestion.admin skillmd-import ...`

    skillmd-import --limit 2000 --dry-run
    skillmd-import --limit 2000 --shard-dsn-env K002_DATABASE_URL --json out.json

Two modes, and the split is the safety property:

  --dry-run  executes every gate -- filename hygiene, mirror/origin recovery,
             content fetch, frontmatter, body size, coercion, screening,
             near-dup, and the per-origin-repository license allowlist -- and
             then stops before any write. It needs no database and costs only
             the network reads plus the license lookups, so the expensive
             questions (what fraction survives, what licenses do these
             repositories carry, how much is duplicated) can be answered
             before a single row is written anywhere.

  default    compiles the survivors through `run_skill_ingestion`, the same
             path `ingest skill-dir` uses. Requires the control pool, and
             refuses to run if the shard DSN names an experiment database.

`--offline-adapter` substitutes a fixture-backed adapter so the CLI's own
plumbing is provable without a network or a dataset.
"""
from __future__ import annotations

import json
from typing import Any

COMMANDS = ("skillmd-import",)


def add_parsers(sub: Any) -> None:
    p = sub.add_parser("skillmd-import")
    p.add_argument("--limit", type=int, default=2000,
                   help="admitted-skill target; rows are read until this many pass")
    p.add_argument("--dry-run", action="store_true",
                   help="run every gate, write nothing")
    p.add_argument("--no-license-gate", action="store_true",
                   help="skip the per-origin-repo allowlist (NOT for a real run)")
    p.add_argument("--star-prior", type=int, default=None,
                   help="soft ordering prior on repo stars (default 5; never rejects)")
    p.add_argument("--fetch-workers", type=int, default=6,
                   help="bounded pool for the raw-content fetch stage (default 6)")
    p.add_argument("--shard-dsn-env", default=None,
                   help="env var holding the local shard DSN; refused for experiment DBs")
    p.add_argument("--cache-path", default=None,
                   help="local parquet cache for the metadata columns")
    p.add_argument("--embed", action="store_true",
                   help="embed procedures (costs money); off for a gate-only pilot")
    p.add_argument("--max-usd", type=float, default=None,
                   help="spend cap for the model calls (rolling 24h, same ledger as the workers); "
                        "default: DAILY_LLM_BUDGET_USD")
    p.add_argument("--json", default=None, help="write the summary JSON here")
    p.add_argument("--markdown", default=None, help="write the summary table here")
    p.add_argument("--offline-adapter", action="store_true",
                   help="use the fixture adapter (no network, no dataset)")


async def run(pool: Any, a: Any) -> int:
    from app.services.ingestion_sources.skillmd_dataset import SkillMD138KReader
    from app.services.ingestion_sources.skillmd_pilot import (
        project_to_full_corpus,
        render_markdown,
        run_skillmd_pilot,
    )

    shard_dsn = None
    if a.shard_dsn_env:
        import os

        shard_dsn = os.environ.get(a.shard_dsn_env)
        if not shard_dsn:
            print(f"ERROR: {a.shard_dsn_env} is not set")
            return 1

    reader: Any = None
    if not a.offline_adapter:
        reader = SkillMD138KReader(cache_path=a.cache_path)
    else:
        from app.services.ingestion_sources.skillmd_offline import build_offline_reader

        reader = build_offline_reader()

    summary = await run_skillmd_pilot(
        pool,
        limit=a.limit,
        shard_dsn=shard_dsn,
        enforce_license=not a.no_license_gate,
        star_prior=a.star_prior,
        reader=reader,
        fetch_workers=a.fetch_workers,
        embed=a.embed,
        dry_run=a.dry_run,
        max_usd=a.max_usd,
    )
    projection = project_to_full_corpus(summary)
    summary["projection"] = projection

    print(json.dumps(summary, indent=2, default=str))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, default=str)
    if a.markdown:
        with open(a.markdown, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(summary, projection))
    return 0
