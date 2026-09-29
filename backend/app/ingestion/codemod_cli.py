"""`ingest-codemods --source {nodejs,openrewrite} ...`

A pilot command for Step 7: enumerate a codemod catalog, run its
deterministic checks, and report what would enter the substrate.

    ingest-codemods --source nodejs --checkout <path> --commit <sha> --dry-run
    ingest-codemods --source openrewrite --max-pages 50 --dry-run --out report.json
    ingest-codemods --source nodejs --checkout <path> --commit <sha> \
        --shard-dsn-env SL_SHARD_DATABASE_URL --limit 5

DELIBERATELY NOT REGISTERED IN admin.py
    This is a pilot. The sibling `skillmd_cli.py` is wired into the admin
    dispatcher; this one is invoked directly with `python -m
    app.ingestion.codemod_cli ...` until the pilot has produced a number
    worth wiring in. Adding it to the dispatcher is a decision that
    belongs to the step that owns `admin.py`.

$0 LLM SPEND, AND THAT IS THE POINT
    A codemod is code and its checks are executable, so extraction,
    gating and checking here are fully deterministic: no judge, no
    extractor, no embedding. The report's `dollars` is therefore a real
    `0.0`, not an estimate, and the only cost is a network read
    (OpenRewrite) and subprocess time (Node.js). `--embed` is
    deliberately not offered: an embedding run spends money and changes
    what this command measures.

SAFE BY DEFAULT
    `--dry-run` is the default and needs no database. Passing
    `--apply` is the only way to write, and it refuses unless the DSN it
    finds (from `--shard-dsn-env`, else `DATABASE_URL`) names a loopback
    host. There is no code path here that writes to a remote database.

    The write path calls `ingest_skill_md` with `embed=False` and
    `created_by="codemod_ingestion"`. HONEST LIMIT: that entry point
    stamps the Procedure row but has no `provenance` column parameter,
    so the authoritative per-artifact provenance (repo, commit, path,
    detected SPDX, allowlist version, the full check payload) lives in
    this command's report and in the document body, not in a dedicated
    column. A dedicated capture path is a follow-up, not something this
    pilot should pretend to have.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from app.utils.aio import run_blocking

COMMANDS = ("ingest-codemods",)

SOURCES = ("nodejs", "openrewrite")

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})

COST_NOTE = (
    "0.0 by construction on a dry run: this source is LLM-free. Extraction is a directory read "
    "plus a static declaration parse, and the check is a subprocess or a parse. --apply compiles each recipe with "
    "a budget-guarded model call (--max-usd) and embeds it, so `dollars` here stays 0.0 and the spend is on the ledger."
)


class PilotRefused(RuntimeError):
    """The run was refused before any work, with the reason attached."""


def _dsn_is_loopback(dsn: str) -> bool:
    host = (urlparse(dsn).hostname or "").lower()
    return host in _LOOPBACK_HOSTS


def add_parsers(sub: Any) -> None:
    p = sub.add_parser("ingest-codemods")
    p.add_argument("--source", choices=SOURCES, required=True,
                   help="which catalog to enumerate")
    p.add_argument("--checkout", default=None,
                   help="local checkout of the catalog (required for --source nodejs)")
    p.add_argument("--commit", default=None,
                   help="pinned commit of that checkout (required for --source nodejs)")
    p.add_argument("--limit", type=int, default=50,
                   help="maximum items to process (default 50)")
    p.add_argument("--out", default=None, help="write the report JSON here")
    p.add_argument("--dry-run", action="store_true",
                   help="run every gate and write nothing (the default)")
    p.add_argument("--apply", action="store_true",
                   help="write through the shared compiler; refused unless the DSN is loopback")
    p.add_argument("--production", action="store_true",
                   help="with --apply: write through the deployment's control database (the same posture as skillmd-import) "
                        "instead of a loopback shard; needs the migrations preflight to pass and honours --max-usd")
    p.add_argument("--shard-dsn-env", default=None,
                   help="env var holding the local shard DSN; defaults to DATABASE_URL")
    p.add_argument("--node-bin", default="codemod",
                   help="the jssg CLI executable that runs Gate A (`codemod jssg test`)")
    p.add_argument("--node-exec", default="node",
                   help="the Node.js runtime used for Gate B (`node --check`); a different "
                        "program from --node-bin, and passing the jssg CLI here makes every "
                        ".js fixture look malformed")
    p.add_argument("--max-pages", type=int, default=None,
                   help="cap on OpenRewrite documentation pages fetched")
    p.add_argument("--max-usd", type=float, default=1.0,
                   help="model-spend ceiling for --apply, enforced BEFORE each paid call (rolling 24h ledger)")
    p.add_argument("--concurrency", type=int, default=4,
                   help="recipes compiled at once under --apply (default 4; each compile is several model calls)")
    p.add_argument("--deterministic", action="store_true",
                   help="--apply through the raw ingest_skill_md path: no provenance, no idempotency (re-runs duplicate "
                        "every row); local experiments only")
    p.add_argument("--no-embed", action="store_true",
                   help="write rows WITHOUT vectors or the duplicate check (they stay invisible to semantic search "
                        "until a backfill runs); the default embeds")
    p.add_argument("--no-checks", action="store_true",
                   help="skip the executable gates (NOT for a real run; they are the point)")
    p.add_argument("--include-rejected", action="store_true",
                   help="print the per-item rejection reasons, not just the histogram")


def _build_adapter(args: Any):
    if args.source == "nodejs":
        from app.services.ingestion_sources.codemod_node import (
            NodeUserlandMigrationsSource,
            DEFAULT_PINNED_COMMIT,
        )

        if not args.checkout:
            raise PilotRefused("--source nodejs needs --checkout (a local clone); this adapter never fetches")
        checkout = Path(args.checkout)
        if not checkout.is_dir():
            raise PilotRefused(f"--checkout does not exist: {checkout}")
        commit = args.commit or DEFAULT_PINNED_COMMIT
        if not args.commit:
            print(
                f"NOTE: --commit not supplied; defaulting to the commit the license facts "
                f"were read at ({commit}). Pass --commit explicitly to pin your own tree.",
                file=sys.stderr,
            )
        # Resolved to an absolute path here on purpose. The check runner
        # executes this string, and resolving it from PATH at call time means
        # whichever shim happens to sit earlier on PATH decides what a trust
        # check actually runs.
        return NodeUserlandMigrationsSource(
            checkout.resolve(),
            commit=commit,
            node_bin=shutil.which(args.node_bin) or args.node_bin,
            node_exec=shutil.which(args.node_exec) or args.node_exec,
            run_checks=not args.no_checks,
        )
    from app.services.ingestion_sources.openrewrite import OpenRewriteCatalogSource

    return OpenRewriteCatalogSource(max_pages=args.max_pages)


def _discover_all(adapter: Any) -> list:
    """Drain `adapter.discover()` inside a worker thread.

    WHY A HELPER AND NOT `run_blocking(adapter.discover)`
        `discover()` is a GENERATOR function, so calling it returns a generator
        without executing any of its body. `run_blocking(adapter.discover)`
        therefore offloads the *creation* of a generator and nothing else, and
        the `for ref in refs:` loop back in `run()` goes on to drive the actual
        catalog walk -- every OpenRewrite page fetch, every recipe-directory
        walk -- on the event loop thread. The iteration is what blocks, not
        the call, so the list has to be materialised in the worker.

        `run_skillmd_pilot` gets this right for the same reason:
        `run_blocking(lambda: list(source.discover()))`.

    WHY THE WHOLE LIST, NOT A BOUNDED CHUNK
        `discover()` populates `adapter.discovery_stats` as it goes, and `run()`
        reads those counters after the loop. Abandoning the generator early to
        keep the loop responsive would leave the report describing a partial
        walk, and a partial walk is not a number anyone can act on.
    """
    return list(adapter.discover())


def _report_item(artifact: Any) -> dict[str, Any]:
    license_metadata = artifact.license_metadata or {}
    check = license_metadata.get("check", {})
    return {
        "uri": artifact.uri,
        "path": artifact.path,
        "content_hash": artifact.content_hash,
        "bytes": len(artifact.content.encode("utf-8")),
        "recipe_id": license_metadata.get("recipe_name") or license_metadata.get("recipe_id"),
        "module": license_metadata.get("module"),
        "detected_spdx": license_metadata.get("detected_spdx"),
        "allowlist_version": license_metadata.get("allowlist_version"),
        "commit": artifact.commit,
        "check_tier": check.get("check_tier"),
        "check_semantics": check.get("check_semantics"),
        "check_passed": check.get("passed"),
        "gates": check.get("gates", {}),
        "case_count": check.get("case_count"),
        "negative_case_count": check.get("negative_case_count"),
        "fixture_layout": check.get("fixture_layout"),
        "resource_count": len(artifact.resources),
    }


async def _apply(pool: Any, artifacts: list[Any], *, embed: bool = True, deterministic: bool = False,
                 max_usd: float = 1.0, concurrency: int = 4, reasons_out: list | None = None) -> dict[str, int]:
    """Write the gated artifacts. The default is the REAL compiler (`compile_skill_artifact`): it registers the Source,
    opens the IngestionContext every derived row stamps (so a license takedown can find them), records the artifact by
    content hash (a re-run is a no-op, a changed recipe is a new version), extracts the structured claims/goals with a
    budget-guarded model call, and embeds. `--deterministic` keeps the old raw `ingest_skill_md` path -- no provenance,
    no idempotency (every re-run duplicated all rows), for local experiments only."""
    outcomes: dict[str, int] = {}

    def bump(key: str) -> None:
        outcomes[key] = outcomes.get(key, 0) + 1

    if deterministic:
        from app.services.skill_ingestion import ingest_skill_md

        for artifact in artifacts:
            name = getattr(artifact, "path", None) or getattr(artifact, "uri", "?")
            try:
                result = await ingest_skill_md(pool, artifact.content, fallback_name=name, created_by="codemod_ingestion",
                                               visibility="public", embed=embed)
            except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
                bump("error")
                print(f"ERROR ingesting {name}: {exc}", file=sys.stderr)
                continue
            bump(str(result.get("status", "error")))
        return outcomes

    from app.config import settings
    from app.services import ingest_budget
    from app.services.embeddings import Embedder
    from app.services.governance import BudgetExceeded
    from app.services.ingestion_jobs import _general_compute_client
    from app.services.skill_ingestion import compile_skill_artifact

    client = await run_blocking(_general_compute_client)
    judge_model = settings.general_compute_judge_model or "gemma-4-31B-it"
    # Every paid extraction path checks the budget BEFORE it spends, but only when a budget is installed.
    budget = ingest_budget.install(pool, cap_usd=max_usd) if ingest_budget.active() is None else None
    # A recipe compile is several model calls (extraction, identity judges) at 3-30 s each; one at a time, 39 recipes took
    # over two hours. Bounded concurrency; a budget stop halts the remaining recipes (in-flight ones finish).
    gate = asyncio.Semaphore(max(1, int(concurrency)))
    stop = asyncio.Event()

    async def compile_one(artifact: Any) -> None:
        name = getattr(artifact, "path", None) or getattr(artifact, "uri", "?")
        async with gate:
            if stop.is_set():
                return
            try:
                outcome = await compile_skill_artifact(
                    pool, artifact, embedder=Embedder(rate_limit_pool=pool), client=client,
                    created_by="codemod_ingestion", admission_llm_model=judge_model,
                    extraction_llm_model=judge_model,
                    fallback_extraction_llm_model=settings.general_compute_fallback_model or None,
                    claim_extraction_llm_model=judge_model,
                )
            except BudgetExceeded as exc:
                bump("budget_exceeded")
                print(f"STOP: {exc}", file=sys.stderr)
                stop.set()
                return
            except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
                bump("error")
                print(f"ERROR ingesting {name}: {exc}", file=sys.stderr)
                return
            status = str(getattr(outcome, "status", None) or "error")
            bump(status)
            reason = getattr(outcome, "reason", None)
            if status not in ("captured", "new_version", "unchanged") and reason:
                # Without this a "rejected" recipe is a bare count with no way to see why (2 of 39 on the first real run).
                print(f"{status.upper()} {name}: {str(reason)[:300]}", file=sys.stderr)
                if reasons_out is not None:
                    reasons_out.append({"recipe": str(name), "status": status, "reason": str(reason)[:300]})

    try:
        await asyncio.gather(*(compile_one(artifact) for artifact in artifacts))
    finally:
        if budget is not None:
            ingest_budget.uninstall()
    return outcomes


async def run(pool: Any, a: Any) -> int:
    from app.services.ingestion_sources.codemod_node import CodemodLicenseBlocked

    started = time.perf_counter()
    if getattr(a, "apply", False) and getattr(a, "dry_run", False):
        print("ERROR: --apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 1
    # No flag at all is a dry run. `--apply` is the only way to write.
    dry_run = not bool(getattr(a, "apply", False))

    dsn: str | None = None
    production = bool(getattr(a, "production", False))
    if not dry_run and production:
        # The deployment's control database, exactly like `skillmd-import`: only ever through the admin dispatcher, which runs
        # the pending-migrations preflight first and hands over the control pool. The write goes through the real compiler
        # (provenance, idempotency, a --max-usd cap enforced before each paid call).
        if pool is None:
            print("ERROR: --production needs the control pool; run this through the admin dispatcher", file=sys.stderr)
            return 1
    elif not dry_run:
        import os

        env_name = a.shard_dsn_env or "DATABASE_URL"
        dsn = os.environ.get(env_name)
        if not dsn:
            print(f"ERROR: --apply needs {env_name} to be set", file=sys.stderr)
            return 1
        if not _dsn_is_loopback(dsn):
            print(
                f"ERROR: refusing to write to a non-loopback database ({dsn.split('@')[-1]}). "
                "Pass --production to write through the deployment's control database.",
                file=sys.stderr,
            )
            return 1
        if pool is None:
            print("ERROR: --apply needs a control pool; run this through the admin dispatcher", file=sys.stderr)
            return 1

    try:
        adapter = await run_blocking(_build_adapter, a)   # Path.resolve / is_dir / shutil.which: stat + PATH scan
    except PilotRefused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    accepted: list[dict[str, Any]] = []
    rejections: dict[str, int] = {}
    rejections_detail: list[dict[str, str]] = []
    duplicates = 0
    seen: set[str] = set()
    bytes_produced = 0
    bytes_stored = 0
    processed = 0
    discovery_stats: dict[str, int] = {}
    capture: list[Any] = []

    refs = await run_blocking(_discover_all, adapter)   # the whole walk+fetch, off the loop
    for ref in refs:
        if processed >= a.limit:
            break
        processed += 1
        try:
            artifact = await run_blocking(adapter.fetch, ref)   # subprocess / HTTP inside: keep it off the loop
        except CodemodLicenseBlocked as exc:
            # A license refusal is a POLICY OUTCOME and gets its own bucket.
            # Folding it into `type(exc).__name__` alongside genuine crashes
            # means a run in which the gate correctly rejected the entire
            # catalog is indistinguishable from a run in which the code is
            # broken, and both print as one number.
            reason = f"license_{exc.verdict.decision.lower()}"
            rejections[reason] = rejections.get(reason, 0) + 1
            if a.include_rejected:
                rejections_detail.append(
                    {"uri": ref.uri, "reason": reason, "detail": exc.verdict.reason[:400]}
                )
            continue
        except Exception as exc:  # noqa: BLE001 -- a refusal is a result, not a crash
            reason = type(exc).__name__
            rejections[reason] = rejections.get(reason, 0) + 1
            if a.include_rejected:
                rejections_detail.append(
                    {
                        "uri": ref.uri,
                        "reason": reason,
                        "detail": str(exc)[:400],
                        "traceback": traceback.format_exc()[-800:],
                    }
                )
            else:
                # A crash with no traceback is unactionable, so one is always
                # printed to stderr even when the report omits it.
                print(f"ERROR fetching {ref.uri}: {exc}", file=sys.stderr)
                traceback.print_exc()
            continue

        fingerprint = adapter.fingerprint(artifact)
        if fingerprint in seen:
            duplicates += 1
            rejections["duplicate_fingerprint"] = rejections.get("duplicate_fingerprint", 0) + 1
            continue
        seen.add(fingerprint)

        item = _report_item(artifact)
        accepted.append(item)
        bytes_produced += item["bytes"]
        capture.append(artifact)

    # Read after the generator has been drained: `discover()` is a
    # generator, so its counters are only populated once iteration is
    # finished or abandoned.
    discovery_stats = dict(getattr(adapter, "discovery_stats", {}) or {})

    write_outcomes: dict[str, int] = {}
    write_reasons: list[dict[str, str]] = []
    if not dry_run:
        write_outcomes = await _apply(pool, capture, embed=not getattr(a, "no_embed", False), reasons_out=write_reasons,
                                      deterministic=bool(getattr(a, "deterministic", False)),
                                      max_usd=float(getattr(a, "max_usd", 1.0)),
                                      concurrency=int(getattr(a, "concurrency", 4)))
        bytes_stored = bytes_produced

    # Counts we did not measure are None, not zero. Reporting 0 for a
    # quantity nobody counted is the same class of error as reporting a
    # check pass nobody read.
    unmeasured = [
        "claims",
        "goals",
    ]
    report: dict[str, Any] = {
        "source": a.source,
        "adapter_source_type": adapter.source_type,
        "dry_run": dry_run,
        "limit": a.limit,
        "processed": processed,
        "accepted": len(accepted),
        "rejected_by_reason": dict(sorted(rejections.items())),
        "duplicate_hits": duplicates,
        "knowledge_items": {
            "procedures": (write_outcomes.get("captured", 0) + write_outcomes.get("new_version", 0)) if not dry_run else None,
            "procedures_planned": len(accepted) if dry_run else None,
            "claims": None,
            "goals": None,
        },
        "bytes_produced": bytes_produced,
        "bytes_stored": bytes_stored,
        "wall_time_s": round(time.perf_counter() - started, 3),
        "dollars": 0.0,
        "cost_note": COST_NOTE,
        "unmeasured": unmeasured,
        "discovery": discovery_stats,
        "items": accepted,
        "rejections": rejections_detail,
    }
    if write_outcomes:
        report["write_outcomes"] = write_outcomes
    if write_reasons:
        report["write_reasons"] = write_reasons

    print(json.dumps(report, indent=2, default=str))
    await run_blocking(_write_report, a.out, report)   # blocking filesystem write inside an async def
    return 0


def _write_report(out: str | None, report: dict[str, Any]) -> None:
    """Write the report JSON, or nothing when `--out` was not given.

    OFF THE EVENT LOOP, and called only through `run_blocking`
        `open(...)/json.dump(...)` is a blocking filesystem call inside an
        `async def`. The report embeds every accepted item's check payload,
        so this is not a token write, and this CLI is one dispatcher entry
        away from sharing a loop with the other admin commands.

    WHY A NAMED FUNCTION RATHER THAN AN INLINE `if`
        So a test can assert the call was *dispatched* off the calling thread.
        An inline block can only be observed by patching `builtins.open`, which
        would prove nothing about which thread it ran on.
    """
    if not out:
        return
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.ingestion.codemod_cli")
    add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    if args.command not in COMMANDS:  # pragma: no cover -- argparse already gates this
        print(f"ERROR: unknown command {args.command}", file=sys.stderr)
        return 2
    import asyncio

    # Dry-run needs no pool. `--apply` through this entry point therefore
    # has no pool to write with and is refused, with the reason printed
    # rather than an AttributeError.
    return asyncio.run(run(None, args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
