"""Ingest ast-grep-essentials rules as checkable Procedures (step 4).

Runs the corpus through the REAL sandboxed check runner -- every rule's own valid/invalid
cases -- and captures only the rules that pass. A rule that does not pass its own test
becomes no Procedure.

    # pilot, local shard only
    $env:DATABASE_URL = "postgresql://postgres@127.0.0.1:55441/sl_step4"
    $env:SL_AST_GREP_BIN = "<path to pinned ast-grep 0.45.3>"
    python scripts/ingest_ast_grep_rules.py --root <checkout> --limit 5

    # full corpus
    python scripts/ingest_ast_grep_rules.py --root <checkout>

Re-running is idempotent: `source_key` is `ast-grep:<rule_id>`, so a second run over the
same corpus creates nothing new and reports every row as `unchanged`.

NO PRODUCTION WRITES without the user's go-ahead (the step's rule). `--allow-production`
exists so the intent has to be typed, not inferred.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import close_pool, create_pool  # noqa: E402
from app.services.ast_grep_rules import (  # noqa: E402
    CORPUS_COMMIT,
    CORPUS_LICENSE_SPDX,
    CORPUS_REPO,
    classify_license,
    ingest_ast_grep_rules,
)

_EXPERIMENT_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def _refuse_experiment_db(dsn: str) -> None:
    """Never ingest into the experiment databases (`kel_*` on 127.0.0.1:55432).

    The step's isolation rule, enforced at the entrypoint rather than trusted."""
    lowered = dsn.lower()
    if not any(host in lowered for host in _EXPERIMENT_HOSTS):
        return
    tail = lowered.rsplit("/", 1)[-1].split("?")[0]
    if tail.startswith("kel_") or ":55432" in lowered:
        raise SystemExit(
            f"REFUSING to write to the experiment database {tail!r} on 55432. "
            "Experiments and ingestion must not share a database."
        )


def _resolve_binary(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    return os.environ.get("SL_AST_GREP_BIN")


async def _amain(a: argparse.Namespace) -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL is unset; point it at a LOCAL shard, never production.", file=sys.stderr)
        return 2
    if ":55432" not in dsn and not a.allow_production:
        pass  # local; fine
    elif not a.allow_production:
        print("ERROR: this DSN is not a local shard. Re-run with --allow-production once the "
              "user has approved a production write.", file=sys.stderr)
        return 2
    _refuse_experiment_db(dsn)

    root = Path(a.root).resolve()
    if not (root / "rules").is_dir():
        print(f"ERROR: {root} does not look like the corpus (no rules/ directory)", file=sys.stderr)
        return 2

    binary = _resolve_binary(a.ast_grep_bin)
    if not binary and not a.no_checks:
        print("ERROR: no ast-grep binary. Set SL_AST_GREP_BIN to a pinned 0.45.3 build, "
              "or pass --no-checks to DISCOVER ONLY (which captures nothing: without a "
              "check a rule has no verified outcome).", file=sys.stderr)
        return 2

    print(f"corpus   : {CORPUS_REPO}@{CORPUS_COMMIT}")
    lic = classify_license()
    print(f"license  : {lic['verdict'].decision} ({lic['verdict'].spdx_id}, "
          f"{lic['verdict'].allowlist_version}); package.json says {lic['package_json_spdx']}")
    print(f"root     : {root}")
    print(f"checker  : ast-grep via {binary or 'NOT RUN'}")
    print(f"limit    : {a.limit or 'all'}")

    pool = await create_pool(dsn)
    started = time.monotonic()
    try:
        counters = await ingest_ast_grep_rules(
            pool,
            root,
            limit=a.limit,
            owner_id=a.owner_id,
            tenant_id=a.tenant_id,
            timeout_seconds=a.timeout,
            ast_grep_binary=binary,
            run_checks=not a.no_checks,
        )
        elapsed = time.monotonic() - started
        print()
        print("=== counters ===")
        print(json.dumps(counters.to_json(), indent=2, sort_keys=True))
        print()
        accounted = counters.accounted()
        status = "OK" if accounted == counters.discovered else "MISMATCH"
        print(f"discovered {counters.discovered} | accounted {accounted} [{status}]")
        print(f"accepted Procedures : {counters.accepted}")
        print(f"unchanged (dedup)   : {counters.unchanged}")
        print(f"quarantined         : {sum(counters.quarantined_by_reason.values())} {counters.quarantined_by_reason}")
        print(f"rejected            : {sum(counters.rejected_by_reason.values())} {counters.rejected_by_reason}")
        print(f"check verdicts      : {counters.verdicts}")
        print(f"wall time           : {elapsed:.1f}s")
        if counters.accepted:
            print(f"seconds per accepted: {elapsed / counters.accepted:.2f}")

        counts = await pool.fetchrow(
            """
            SELECT count(*) AS procedures,
                   count(*) FILTER (WHERE verifier_check IS NOT NULL) AS with_check,
                   count(DISTINCT source_key) AS distinct_keys
            FROM procedures
            WHERE created_by = 'ast_grep_rules_ingestion' AND t_invalid IS NULL
            """
        )
        goals = await pool.fetchval(
            "SELECT count(*) FROM knowledge_nodes WHERE node_type = 'goal' AND t_invalid IS NULL"
        )
        print()
        print("=== stored on the shard ===")
        print(f"procedures (this corpus, live) : {counts['procedures']}")
        print(f"  of which carrying a check    : {counts['with_check']}")
        print(f"  distinct source keys         : {counts['distinct_keys']}")
        print(f"goals in the shard             : {goals}")
        if a.full_scale_cost and counters.accepted:
            per = elapsed / counters.accepted
            print()
            print("=== projection ===")
            print(f"measured {per:.2f}s per accepted Procedure (0 model dollars: no LLM call)")
            print(f"184 rules at this rate ~= {per * 184 / 60:.1f} min of wall time")
        return 0 if status == "OK" else 1
    finally:
        await close_pool()


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", required=True, help="path to a checkout of ast-grep-essentials")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--owner-id", default=None)
    p.add_argument("--tenant-id", default=None)
    p.add_argument("--timeout", type=float, default=60.0, help="per-check timeout, seconds")
    p.add_argument("--ast-grep-bin", default=None, help="pinned ast-grep 0.45.3 binary")
    p.add_argument("--no-checks", action="store_true",
                   help="discovery only; captures nothing (a rule without a check has no "
                        "verified outcome, so this is a dry run)")
    p.add_argument("--allow-production", action="store_true",
                   help="permit a non-local DSN; only after the user approves")
    p.add_argument("--full-scale-cost", action="store_true", help="print the projection")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(_parse(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
