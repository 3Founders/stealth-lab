"""Preflight: refuse to start a writing ingestion pilot against a database with pending migrations.

docs/ingestion_review.md, cross-cutting lesson 2: migration 125 landed under a running step-3 pilot and 54 of
60 artifacts failed. Every pilot command that writes now checks first that every control-database migration in
db/ is recorded in the database's `schema_migrations` ledger (the ledger `scripts/migrate.py` keeps).

Only presence is checked here; `scripts/migrate.py --status` remains the tool for checksum mismatches.
STEALTH_SKIP_MIGRATION_CHECK=1 skips the check (for a deliberate, logged exception only).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).resolve().parents[2] / "db"


def _number(name: str) -> tuple[int, str]:
    m = re.match(r"^(\d+)", name)
    return (int(m.group(1)) if m else 0, name)


def _target_of(path: Path) -> str:
    with path.open(encoding="utf-8") as handle:
        first = handle.readline().strip().lower()
    return "search" if first.replace(" ", "") == "--target:search" else "control"


def expected_migrations(target: str = "control", db_dir: Path = DB_DIR) -> list[str]:
    """The migration filenames of `target`, in apply order -- the same selection scripts/migrate.py makes."""
    files = [p for p in db_dir.glob("*.sql") if p.is_file() and _target_of(p) == target]
    return [p.name for p in sorted(files, key=lambda p: _number(p.name))]


async def pending_migrations(pool: Any, target: str = "control", db_dir: Path = DB_DIR) -> list[str]:
    expected = expected_migrations(target, db_dir)
    exists = await pool.fetchval("SELECT to_regclass('schema_migrations') IS NOT NULL")
    if not exists:
        return expected
    applied = {r["filename"] for r in await pool.fetch("SELECT filename FROM schema_migrations")}
    return [name for name in expected if name not in applied]


class PendingMigrations(RuntimeError):
    pass


async def assert_schema_current(pool: Any, *, command: str, target: str = "control", env=os.environ) -> None:
    if env.get("STEALTH_SKIP_MIGRATION_CHECK") == "1":
        return
    pending = await pending_migrations(pool, target)
    if pending:
        shown = ", ".join(pending[:8]) + (f" (+{len(pending) - 8} more)" if len(pending) > 8 else "")
        raise PendingMigrations(
            f"{command}: refusing to run -- {len(pending)} migration(s) not applied to this database: {shown}. "
            "Run `python scripts/migrate.py --status`, apply them, then retry "
            "(docs/ingestion_review.md: a migration landing under a running pilot failed 54/60 artifacts)."
        )
