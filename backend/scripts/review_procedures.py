"""Review the procedure approval tiers (db/201, docs/goal_router.md).

    submitted < proposed < approved        (rejected is never shown or run)

    python scripts/review_procedures.py --list --status submitted --limit 20
    python scripts/review_procedures.py --promote <procedure-row-id> --to proposed --by dev-local
    python scripts/review_procedures.py --promote <procedure-row-id> --to approved --by dev-local
    python scripts/review_procedures.py --bulk-generated --to proposed --by dev-local

`--promote` moves one procedure and writes a `way_moderation_events` row. `--bulk-generated` promotes
every tool-card way still at `submitted` (the 56,741 materialized ones) and prints the count; use it
after you have reviewed the generated ways. The router shows every non-rejected tier with its label;
the comparison runner only executes `approved`.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # a procedure name can be any Unicode; the Windows console defaults to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

TIERS = ("submitted", "proposed", "approved", "rejected")


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""


async def run(args: argparse.Namespace) -> int:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=60)
    try:
        if args.list:
            rows = await conn.fetch(
                """
                SELECT id::text AS id, procedure_id::text AS procedure_id, name, approval_status,
                       verification_state::text AS verification_state, provenance
                FROM procedures
                WHERE t_invalid IS NULL
                  AND ($1::text IS NULL OR approval_status = $1)
                ORDER BY updated_at DESC, id
                LIMIT $2
                """,
                args.status, max(1, min(int(args.limit), 200)),
            )
            for r in rows:
                print(f"{r['id']}  {r['approval_status']:<9} {r['verification_state']:<9} "
                      f"{r['provenance'] or '-':<12} {r['name'][:60]}")
            print(f"{len(rows)} row(s)")
            return 0

        if args.promote:
            if args.to not in TIERS:
                raise SystemExit(f"--to must be one of {', '.join(TIERS)}")
            row = await conn.fetchrow(
                """
                UPDATE procedures
                SET approval_status = $2, updated_at = now()
                WHERE id = $1::uuid AND t_invalid IS NULL
                RETURNING id, procedure_id, name, approval_status
                """,
                args.promote, args.to,
            )
            if row is None:
                raise SystemExit(f"no procedure {args.promote!r}")
            await conn.execute(
                """
                INSERT INTO way_moderation_events (id, procedure_id, procedure_row_id, action, actor, reason)
                VALUES (gen_random_uuid(), $1::uuid, $2::uuid, 'promoted', $3, $4)
                """,
                str(row["procedure_id"]), str(row["id"]),
                args.by, f"to {args.to}: {args.reason}".strip(),
            )
            print(f"promoted {row['id']} ({row['name'][:60]}) -> {row['approval_status']}")
            return 0

        if args.bulk_generated:
            if args.to not in TIERS:
                raise SystemExit(f"--to must be one of {', '.join(TIERS)}")
            status = await conn.execute(
                """
                UPDATE procedures
                SET approval_status = $1, updated_at = now()
                WHERE provenance = 'tool_card' AND approval_status = 'submitted' AND t_invalid IS NULL
                """,
                args.to,
            )
            print(f"bulk generated -> {args.to}: {status}")
            return 0

        print("choose --list, --promote or --bulk-generated", file=sys.stderr)
        return 2
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Review procedure approval tiers.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", default=None, help="with --list: submitted | proposed | approved | rejected")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--promote", default=None, metavar="PROCEDURE_ROW_ID")
    ap.add_argument("--to", default="proposed")
    ap.add_argument("--by", default="dev-local")
    ap.add_argument("--reason", default="")
    ap.add_argument("--bulk-generated", action="store_true")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
