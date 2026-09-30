"""
Step 6 pilot runner: CI workflow histories + bot dependency-bump PRs into a
LOCAL shard.

Refusals happen before any pool is opened, in this order:

* the DSN host must be local, and the database name must not look like an
  experiment database (`kel_*`, the SWE-bench rigs). SWE-bench-family material
  must never land in the measurement databases, and a mis-typed port is exactly
  how that happens by accident.
* no LLM client is ever constructed. `compile_artifact` is called with
  `judge=None`, so the goal identity judge -- the dominant cost in
  `ingestion_problems.md` P1, up to five sequential calls per goal -- is never
  built. Spend is therefore structurally zero rather than merely small, and the
  run report says so instead of estimating it.

Bounded and reproducible: the metadata CSV is streamed once and a prefix is
staged locally, so the pilot and the full run read the same bytes and a re-run
costs no network.

Usage
-----
    python scripts/step6_pilot.py --control-dsn "$STEP6_CONTROL" \\
        --shard-dsn "$STEP6_SHARD" --workflows 1000 --prs 500
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The GitHub client reads its token from the environment at first use, and the
# runner is a plain script, so nothing else has populated it. Without this the
# client silently runs UNAUTHENTICATED: 60 requests/hour instead of 5,000, and
# 10/minute of search instead of 30. It does not fail loudly -- it throttles, so
# a run that looked like it was working just stops making progress after ~60
# calls. Keys are read, never logged, never echoed.
_ENV_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)
if os.path.exists(_ENV_FILE):
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE, override=False)
    except Exception:  # noqa: BLE001 - fall back to a minimal parser
        with open(_ENV_FILE, "r", encoding="utf-8") as _handle:
            for _line in _handle:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _key, _, _value = _line.partition("=")
                os.environ.setdefault(_key.strip(), _value.strip().strip("'\""))

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
FORBIDDEN_DB_SUBSTRINGS = ("kel_swebench", "kel_")
RAW_TEMPLATE = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"
ZENODO_METADATA_URL = (
    "https://zenodo.org/api/records/20340547/files/workflows.csv.gz/content"
)


def _assert_local(dsn: str, label: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(dsn)
    host = (parsed.hostname or "").strip()
    if host not in LOCAL_HOSTS:
        raise SystemExit(
            f"REFUSING: --{label}-dsn host {host!r} is not local. Step 6 is "
            "local-shard only; production writes need explicit go-ahead."
        )
    dbname = (parsed.path or "").lstrip("/")
    for bad in FORBIDDEN_DB_SUBSTRINGS:
        if bad in dbname:
            raise SystemExit(
                f"REFUSING: --{label}-dsn database {dbname!r} matches the reserved "
                f"experiment prefix {bad!r}. The isolation rule forbids ingesting here."
            )


def stage_metadata_prefix(destination: str, *, lines: int) -> str:
    """Stream the pinned `workflows.csv.gz` and keep only the first `lines` rows.

    Two reasons not to just download the 311 MB file: a bounded pilot does not
    need it, and the prefix becomes a local, immutable artifact so the pilot and
    the full acceptance run provably read identical bytes. Handles the
    author-documented `.gz.gz` double compression defensively.

    Reads in chunks and stops as soon as it has enough lines. Reading the whole
    body first (the obvious implementation) costs ~5 minutes of wall clock and
    311 MB of RAM for a 500-row prefix, which is how the first smoke run spent
    322 s to compile 10 items.
    """
    import zlib

    if os.path.exists(destination):
        return destination
    request = urllib.request.Request(
        ZENODO_METADATA_URL, headers={"User-Agent": "stealthlab-ingestion/1.0"}
    )
    kept: list[str] = []
    decompressor = zlib.decompressobj(zlib.MAX_WBITS | 32)
    pending = b""
    with urllib.request.urlopen(request, timeout=180) as response:
        while len(kept) <= lines:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            pending += decompressor.decompress(chunk)
            text = pending.decode("utf-8", errors="replace")
            parts = text.split("\n")
            pending = parts.pop().encode("utf-8")
            kept.extend(parts)
    kept = kept[: lines + 1]
    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    with open(destination, "w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(kept))
    return destination


def build_license_resolver(client, *, allow: "tuple[str, ...]" = ()):
    """Resolve a repository's SPDX id from GitHub, one call per repository.

    Why the API at all, and why the blob: the Zenodo record licenses the
    *compilation*. Each contained workflow file belongs to its own repository
    under its own terms, and no published source addresses that -- so the item
    license has to be established per repository, which is hard rule 1 regardless
    of where the bytes came from.

    `GET /repos/{owner}/{repo}/license` returns the API's `spdx_id` summary *and*
    the LICENSE blob in one call, so there is no reason to prefer the summary.
    The project's own 2026-09-28 correction is explicit that the blob is the
    license and the API field is a hint, so the blob is decoded and identified
    first, with the field as fallback. `actions/starter-workflows` is the
    precedent: the API says NOASSERTION, the shipped file is MIT.
    """
    from app.services.repo_license_policy import identify_spdx_from_text

    cache: dict[str, str] = {}
    stats: Counter[str] = Counter()

    def resolve(repository: str) -> "tuple[str | None, str]":
        if repository in cache:
            return cache[repository], "cached"
        owner, _, name = repository.partition("/")
        if not owner or not name:
            stats["malformed"] += 1
            cache[repository] = ""
            return None, "malformed repository"
        try:
            payload = client.get(f"https://api.github.com/repos/{owner}/{name}/license")
        except Exception as exc:  # noqa: BLE001 - unresolved is a quarantine, not a crash
            stats[f"error:{type(exc).__name__}"] += 1
            cache[repository] = ""
            return None, f"api error {type(exc).__name__}"
        if not isinstance(payload, dict):
            stats["no_license_endpoint"] += 1
            cache[repository] = ""
            return None, "no license endpoint"
        blob = ((payload.get("content") or "").strip())
        if blob:
            try:
                import base64

                text = base64.b64decode(blob).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text = ""
            from_blob = identify_spdx_from_text(text)
            if from_blob:
                stats["from_blob"] += 1
                cache[repository] = from_blob
                return from_blob, "identified from LICENSE blob"
        summary = ((payload.get("license") or {}) or {}).get("spdx_id") or ""
        summary = str(summary).strip()
        if summary and summary.upper() != "NOASSERTION":
            stats["from_api_field"] += 1
            cache[repository] = summary
            return summary, "api spdx_id field"
        stats["noassertion"] += 1
        cache[repository] = ""
        return None, "NOASSERTION"

    resolve.cache = cache  # type: ignore[attr-defined]
    resolve.stats = stats  # type: ignore[attr-defined]
    return resolve


def build_body_lookup(metadata_path: str, *, limit: int, cache: dict[str, str]):
    """Return `body_lookup(file_hash) -> str | None`, plus the map it reads.

    `CiWorkflowHistorySource` hands `body_lookup` only a content hash, so the
    hash -> (repo, commit, path) map has to be built from the metadata first.
    Bodies come from raw.githubusercontent at the pinned commit rather than from
    the 1.4 GB tarball: it is smaller, it is attributable to an exact revision,
    and it keeps us out of the unresolved question of redistributing a
    compilation whose contained files carry their own licences.

    Failures return None instead of raising. A missing body still yields a
    document from the metadata alone, and the item is counted, not dropped
    silently.
    """
    from app.services.ingestion_sources.ci_workflow_history import (
        iter_metadata_file,
        parse_metadata_row,
    )
    import csv

    mapping: dict[str, str] = {}
    with open(metadata_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, raw in enumerate(reader):
            if index >= limit:
                break
            revision = parse_metadata_row(raw)
            if revision is None or not revision.file_hash:
                continue
            mapping[revision.file_hash] = RAW_TEMPLATE.format(
                repo=revision.repository, commit=revision.commit_hash, path=revision.file_path
            )

    misses: list[str] = []

    def lookup(file_hash: str) -> Optional[str]:
        url = mapping.get(file_hash)
        if not url:
            misses.append(file_hash)
            return None
        if file_hash in cache:
            return cache[file_hash]
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "stealthlab-ingestion/1.0"}
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - a 404/403 is data, not a crash
            misses.append(file_hash)
            return None
        cache[file_hash] = body
        return body

    lookup.misses = misses  # type: ignore[attr-defined]
    return lookup


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from app.db.session import create_pool
    from app.services.ingestion_sources.bot_dependency_prs import BotDependencyPrSource
    from app.services.ingestion_sources.ci_workflow_history import CiWorkflowHistorySource
    from app.services.ingestion_sources.workflow_knowledge import (
        CompileReport,
        compile_artifact,
    )

    started = time.time()
    report = CompileReport()
    outcomes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    body_cache: dict[str, str] = {}

    control = await create_pool(args.control_dsn, max_size=2)
    shard = await create_pool(args.shard_dsn, max_size=4)
    try:
        if not args.dry_run:
            from app.services import shards as sh

            os.environ.setdefault(f"{args.shard_id}_DATABASE_URL", args.shard_dsn)
            info = await sh.register_shard(
                control, args.shard_id, dsn_env=f"{args.shard_id}_DATABASE_URL", weight=100
            )
            print(f"shard registered: {info}", flush=True)
            # Send ALL placement to the local shard by zeroing the home shard's
            # weight, rather than splitting 50/50 with K000.
            #
            # This is not a performance tweak. A Procedure is homed with its Goal
            # (`choose_child_shard(goal.home_shard_id, ...)`), and the Goal is
            # homed by its own id, so a 50/50 split can land a Goal on K000 and
            # its Procedure on K001 -- and the procedure's foreign key to
            # `goals` then fails with "has no goal, neither local nor routed to a
            # shard". Cross-shard capture is three transactions across two
            # databases with no compensation; measuring it on a split placement
            # measures that hazard instead of the ingestion. One shard, one
            # database, atomic.
            await control.execute(
                "UPDATE knowledge_shards SET weight = 0 WHERE shard_id = $1", "K000"
            )
            sh.invalidate_shard_cache()
            print("K000 weight -> 0: all new public placement targets the local shard", flush=True)

        workflow_stats: dict[str, Any] = {}
        if args.workflows > 0:
            index_rows = args.workflows * 6 + 500
            metadata_path = stage_metadata_prefix(
                args.metadata_path, lines=index_rows
            )
            body_lookup = build_body_lookup(
                metadata_path, limit=index_rows, cache=body_cache
            )
            # One license call per repository, not per file: a repository with
            # forty workflow files costs one call, and the result is cached for
            # the rest of the run.
            from app.services.ingestion_sources.bot_dependency_prs import _GitHubApiClient

            license_client = _GitHubApiClient()
            resolve_license = build_license_resolver(license_client)
            source = CiWorkflowHistorySource(metadata_path, body_lookup=body_lookup)
            compiled = 0
            admitted = 0
            for ref in source.discover(limit=args.workflows):
                try:
                    artifact = source.fetch(ref)
                except Exception as exc:  # noqa: BLE001
                    reasons[f"fetch_error:{type(exc).__name__}"] += 1
                    continue
                item_spdx, license_note = resolve_license(artifact.repository or "")
                metadata = dict(artifact.license_metadata or {})
                metadata["item_spdx_id"] = item_spdx
                metadata["license_note"] = license_note
                if args.license_allow:
                    metadata["license_allow"] = list(args.license_allow)
                artifact = replace(artifact, license_metadata=metadata)
                result = await compile_artifact(control, artifact, report=report)
                outcomes[result["outcome"]] += 1
                if result.get("reason"):
                    reasons[str(result["reason"])[:120]] += 1
                if result["outcome"] == "candidate":
                    admitted += 1
                compiled += 1
                if compiled % 100 == 0:
                    print(
                        f"  workflows {compiled}/{args.workflows} "
                        f"candidates={report.candidates} outcomes={dict(outcomes)}",
                        flush=True,
                    )
            workflow_stats = {
                "compiled": compiled,
                "admitted": admitted,
                "bodies_fetched": len(body_cache),
                "body_lookup_misses": len(getattr(body_lookup, "misses", [])),
                "index_rows_staged": index_rows,
                "unique_repositories_licensed": len(
                    [v for v in resolve_license.cache.values() if v]
                ),
                "license_resolution": dict(resolve_license.stats),
                "api_requests": dict(license_client.stats),
            }

        pr_stats: dict[str, Any] = {}
        if args.prs > 0 and not args.dry_run:
            source_b = BotDependencyPrSource(
                repos=tuple(args.repos),
                since=args.since,
                per_page=min(args.per_page, 100),
                max_pages=args.max_pages,
            )
            compiled = 0
            for cursor in source_b.discover():
                try:
                    ref = source_b.ref_for(cursor)
                    artifact = source_b.fetch(ref)
                except Exception as exc:  # noqa: BLE001
                    reasons[f"pr_fetch_error:{type(exc).__name__}"] += 1
                    continue
                result = await compile_artifact(control, artifact, report=report)
                outcomes[result["outcome"]] += 1
                if result.get("reason"):
                    reasons[str(result["reason"])[:120]] += 1
                compiled += 1
                if compiled % 100 == 0:
                    print(f"  prs {compiled}/{args.prs}", flush=True)
                if compiled >= args.prs:
                    break
            pr_stats = {
                "compiled": compiled,
                "repos_scoped": list(args.repos) or "(global - truncated at 1,000)",
                "api_requests": dict(source_b._client.stats),
            }

        elapsed = time.time() - started
        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": args.dry_run,
            "compile_report": report.as_dict(),
            "outcomes": dict(outcomes),
            "reasons_top": dict(reasons.most_common(20)),
            "workflow_stats": workflow_stats,
            "pr_stats": pr_stats,
            "wall_seconds": round(elapsed, 1),
            "bytes_per_item": (
                round(report.bytes_content / report.considered, 1)
                if report.considered else 0
            ),
            "spend_usd": 0.0,
            "spend_note": (
                "structurally zero: no LLM client is constructed, so the goal identity "
                "judge (ingestion_problems.md P1) never runs"
            ),
        }
        if args.out:
            os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2, default=str)
        return summary
    finally:
        await control.close()
        await shard.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 6 local-shard pilot")
    parser.add_argument("--control-dsn", default=os.environ.get("STEP6_CONTROL_DSN", ""))
    parser.add_argument("--shard-dsn", default=os.environ.get("STEP6_SHARD_DSN", ""))
    parser.add_argument("--shard-id", default="K001")
    parser.add_argument("--workflows", type=int, default=0)
    parser.add_argument("--prs", type=int, default=0)
    parser.add_argument("--metadata-path", default=".scratch/step6/workflows_prefix.csv")
    parser.add_argument("--repos", nargs="*", default=[])
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--since", default="2025-01-01")
    parser.add_argument(
        "--license-allow", nargs="*", default=["MIT", "Apache-2.0", "BSD-2-Clause",
                                                "BSD-3-Clause", "ISC", "0BSD",
                                                "Unlicense", "CC0-1.0", "CC-BY-4.0"],
        help="caller-configured SPDX allowlist for the per-repository gate",
    )
    parser.add_argument("--out", default=".scratch/step6/run_summary.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.control_dsn or not args.shard_dsn:
        print("ERROR: --control-dsn and --shard-dsn are required", file=sys.stderr)
        return 2
    _assert_local(args.control_dsn, "control")
    _assert_local(args.shard_dsn, "shard")
    summary = asyncio.run(run(args))
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
