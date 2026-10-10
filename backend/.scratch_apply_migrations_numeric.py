"""Apply db/*.sql in correct NUMERIC order (not migrate.py's lexical sort,
which mis-orders once migrations pass 3 digits). Test-infra only.
"""
import asyncio
import re
import sys
from pathlib import Path

import asyncpg

DB_DIR = Path(__file__).resolve().parent / "db"


def _prefix_num(path: Path) -> int:
    m = re.match(r"(\d+)", path.name)
    return int(m.group(1)) if m else 10**9


async def main(dsn: str) -> None:
    files = sorted((p for p in DB_DIR.glob("*.sql") if p.is_file()), key=lambda p: (_prefix_num(p), p.name))
    conn = await asyncpg.connect(dsn)
    try:
        for f in files:
            sql = f.read_text(encoding="utf-8")
            try:
                await conn.execute(sql)
            except Exception as e:
                print(f"FAILED at {f.name}: {e}", file=sys.stderr)
                raise
            print(f"applied {f.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
