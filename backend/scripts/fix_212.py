"""Apply the committed db/212_referral_rewards.sql to production and re-baseline its ledger checksum (owner's call).

Why this and not a revert: the ledger recorded a checksum for a version that was never committed, and the live table is an
older shape than the file (it lacks reward_inr and the self-referral check). Production has 0 rows in referral_rewards, so
the NOT NULL column is safe to add. The file is idempotent, so running it again is the honest way to make the database
match the repository.

Order matters: apply first, and only re-baseline if it succeeded. If the apply throws, the transaction rolls back and the
ledger keeps its old checksum -- so a failure here leaves production exactly as it is now, not half-changed.

Prints what it did. Reads DATABASE_URL from the environment.
"""
import asyncio
import hashlib
import os
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve().parent
MIGRATION = HERE.parent / "db" / "212_referral_rewards.sql"


def checksum(path: Path) -> str:
    """The runner's own rule: hash the text, not the line endings (migrate.py::_checksum)."""
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


async def main() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    want = checksum(MIGRATION)
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        async with conn.transaction():
            await conn.execute(sql)                       # the whole file, in one transaction
            await conn.execute(
                "UPDATE schema_migrations SET checksum = $1 WHERE filename = $2",
                want, MIGRATION.name)
        print(f"applied {MIGRATION.name} and re-baselined its checksum to {want[:16]}")
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='referral_rewards' ORDER BY ordinal_position")
        print("columns now:", [c["column_name"] for c in cols])
        row = await conn.fetchrow(
            "SELECT checksum FROM schema_migrations WHERE filename = $1", MIGRATION.name)
        print("ledger now:", row["checksum"][:16], "(matches file:", row["checksum"] == want, ")")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
