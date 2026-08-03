"""
Schema migration runner.

Deliberately not Alembic. Alembic's value is autogenerating diffs from
SQLAlchemy models, and this project has no ORM -- adding SQLAlchemy purely
to get a migration tool would be a large dependency serving one feature.
What is actually missing is much smaller: a record of which SQL files have
been applied, and a guard against one changing after the fact.

Three properties:

  1. **Applied-once tracking.** `schema_migrations` records every file
     that has run. Re-running the runner applies only what is new, so it
     is safe to run on every deploy.

  2. **Checksum enforcement.** Editing a file that has already been applied
     is the classic way a schema drifts: the author's database has the
     change, production never will, and nothing reports it. The runner
     refuses to continue when a checksum no longer matches, which turns a
     silent divergence into a failed deploy.

  3. **One transaction per file.** A file that fails leaves nothing behind
     and is not recorded, so it can be fixed and re-run. Note the caveat
     in 06_index_tuning.sql: CREATE INDEX CONCURRENTLY cannot run inside a
     transaction, so at real volume those statements need applying by hand
     rather than through this runner.

Usage, from backend_v2/backend_v2 with DATABASE_URL set:

    python scripts/migrate.py            # apply anything pending
    python scripts/migrate.py --status   # report, change nothing
    python scripts/migrate.py --baseline # record existing files as applied
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.db.session import create_pool  # noqa: E402

DB_DIR = Path(__file__).resolve().parent.parent / "db"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _checksum(path: Path) -> str:
    # Newlines normalised so a Windows checkout and a Linux CI runner agree.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _discover() -> list[Path]:
    return sorted(DB_DIR.glob("*.sql"))


async def _applied(conn) -> dict[str, str]:
    rows = await conn.fetch("SELECT filename, checksum FROM schema_migrations")
    return {r["filename"]: r["checksum"] for r in rows}


async def main() -> int:
    parser = argparse.ArgumentParser(description="Apply pending SQL migrations.")
    parser.add_argument("--status", action="store_true", help="report without applying")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="record every current file as applied without running it "
        "(for a database already built by hand)",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set (put it in .env or export it)")
        return 1

    files = _discover()
    if not files:
        print(f"no .sql files found in {DB_DIR}")
        return 1

    pool = await create_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(_TRACKING_TABLE)
            applied = await _applied(conn)

            # Drift check first: report every mismatch, then refuse, rather
            # than failing on the first one and hiding the rest.
            drifted = [
                f.name for f in files
                if f.name in applied and applied[f.name] != _checksum(f)
            ]
            if drifted:
                print("These files changed after being applied:")
                for name in drifted:
                    print(f"  {name}")
                print(
                    "\nAn applied migration is history and cannot be edited. Add a new\n"
                    "file with the change. If the edit is genuinely cosmetic, update the\n"
                    "recorded checksum by hand -- deliberately, not by rerunning this."
                )
                return 1

            pending = [f for f in files if f.name not in applied]

            if args.status:
                print(f"applied: {len(applied)}    pending: {len(pending)}")
                for f in files:
                    print(f"  [{'x' if f.name in applied else ' '}] {f.name}")
                return 0

            if args.baseline:
                for f in pending:
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename, checksum) VALUES ($1,$2) "
                        "ON CONFLICT (filename) DO NOTHING",
                        f.name, _checksum(f),
                    )
                    print(f"  baselined {f.name}")
                print(f"\n{len(pending)} file(s) recorded as applied. Nothing was executed.")
                return 0

            if not pending:
                print(f"up to date ({len(applied)} applied)")
                return 0

            for f in pending:
                sql = f.read_text(encoding="utf-8")
                try:
                    async with conn.transaction():
                        await conn.execute(sql)
                        await conn.execute(
                            "INSERT INTO schema_migrations (filename, checksum) "
                            "VALUES ($1,$2)",
                            f.name, _checksum(f),
                        )
                except Exception as exc:  # noqa: BLE001
                    print(f"  [FAIL] {f.name}: {type(exc).__name__}: {exc}")
                    print("\nRolled back. Nothing from this file was applied.")
                    return 1
                print(f"  [ok]   {f.name}")

            print(f"\napplied {len(pending)} migration(s)")
            return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
