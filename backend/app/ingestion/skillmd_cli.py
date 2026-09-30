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

from app.utils.aio import run_blocking

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
    raw_fetcher: Any = None
    if not a.offline_adapter:
        reader = SkillMD138KReader(cache_path=a.cache_path)
    else:
        # BOTH the reader and the raw fetcher, not just the reader. The gate
        # lives inside `discover()` -- the content is fetched there because
        # `fetch()` has no rejection channel -- so leaving the fetcher live meant
        # `--offline-adapter` still made one raw.githubusercontent.com request
        # per candidate row while advertising "no network, no dataset". An
        # offline flag that reaches the network is worse than no flag.
        from app.services.ingestion_sources.skillmd_offline import (
            OfflineRawStore,
            build_offline_reader,
        )

        reader = build_offline_reader()
        raw_fetcher = OfflineRawStore()

    summary = await run_skillmd_pilot(
        pool,
        limit=a.limit,
        shard_dsn=shard_dsn,
        enforce_license=not a.no_license_gate,
        star_prior=a.star_prior,
        reader=reader,
        raw_fetcher=raw_fetcher,
        fetch_workers=a.fetch_workers,
        embed=a.embed,
        dry_run=a.dry_run,
        max_usd=a.max_usd,
    )
    projection = project_to_full_corpus(summary)
    summary["projection"] = projection

    print(json.dumps(summary, indent=2, default=str))
    await run_blocking(_write_reports, a, summary, projection)
    return 0


def _write_reports(a: Any, summary: dict[str, Any], projection: dict[str, Any]) -> None:
    """Write the summary JSON and the markdown table.

    OFF THE EVENT LOOP, and called only through `run_blocking`
        `open(...)/write(...)` is a blocking filesystem call, and `run()` is
        an `async def`, so the hard rule applies to the write exactly as it
        applies to the fetch. This is not a theoretical size: the report for a
        2,000-row pilot serialises every disposition reason plus the full
        projection, and `--json`/`--markdown` are how a pilot's numbers reach
        a human at all.

        Both writes go through one `run_blocking` so there is a single seam
        for a test to assert on rather than two.

    WHY A MODULE-LEVEL DEF AND NOT A CLOSURE
        A closure is untestable by identity -- a test can only reach it by
        monkeypatching `open` globally, which would also catch the reads it
        does not care about. A named module-level function is the thing a test
        can assert was dispatched off the calling thread.
    """
    if a.json:
        with open(a.json, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, default=str)
    if a.markdown:
        from app.services.ingestion_sources.skillmd_pilot import render_markdown

        with open(a.markdown, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(summary, projection))
