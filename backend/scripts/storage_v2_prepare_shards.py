#!/usr/bin/env python3
"""Storage layout v2 cutover, step 1: bring every provisioned knowledge shard / search member to the current schema.

    python scripts/storage_v2_prepare_shards.py [--env-file backend/.neon_shards.env] [--only K001,K002] [--dry-run]

For each K###/S### connection string in the env file (never printed):

  1. `scripts/migrate.py --dsn` -- every pending migration (e.g. a shard whose first migrate failed);
  2. LEDGER CORRECTION for 132/133 (a deliberate one, as migrate.py asks for): shards provisioned while those two
     files were still being written recorded intermediate versions. The databases are new and empty and every
     version is idempotent DDL contained in the final text, so the final file is applied again and its checksum
     recorded -- after which migrate.py reports no mismatch;
  3. `scripts/migrate.py --dsn` again (anything after 133);
  4. the database's role (migration 133's sl_database_role): knowledge_shard for K###, search_member for S###.

Idempotent; re-run to resume. Exit status 1 when any database failed (each is reported by name only).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import asyncpg

BACKEND = Path(__file__).resolve().parents[1]
CORRECTED = ("132_search_group.sql", "133_route_aware_goal_refs.sql")
ROLE = {"K": "knowledge_shard", "S": "search_member"}


def _checksum(path: Path) -> str:        # the same normalisation as scripts/migrate.py
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def _migrate(dsn: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(BACKEND / "scripts" / "migrate.py"), "--dsn", dsn],
                       capture_output=True, text=True, cwd=BACKEND)
    out = re.sub(r"postgres(ql)?://\S+", "<dsn>", (r.stdout + r.stderr)[-1500:])
    return r.returncode, out


async def _correct(dsn: str, letter: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        for name in CORRECTED:
            path = BACKEND / "db" / name
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO schema_migrations (filename, checksum, kind) VALUES ($1, $2, 'schema') "
                "ON CONFLICT (filename) DO UPDATE SET checksum = EXCLUDED.checksum, applied_at = now()",
                name, _checksum(path))
        await conn.execute("INSERT INTO sl_database_role (singleton, role) VALUES (true, $1) "
                           "ON CONFLICT (singleton) DO UPDATE SET role = EXCLUDED.role", ROLE[letter])
    finally:
        await conn.close()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=str(BACKEND / ".neon_shards.env"))
    ap.add_argument("--only", default="", help="comma-separated shard ids")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    dsns: dict[str, str] = {}
    for line in Path(args.env_file).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([KS]\d{3})_DATABASE_URL=(.+)$", line.strip())
        if m and (not only or m.group(1) in only):
            dsns[m.group(1)] = m.group(2)
    failed = []
    for sid in sorted(dsns):
        if args.dry_run:
            print(f"{sid}: would prepare")
            continue
        try:
            first_rc, first_out = _migrate(dsns[sid])
            if first_rc != 0 and "MISMATCH" not in first_out:
                raise RuntimeError(first_out)
            asyncio.run(_correct(dsns[sid], sid[0]))
            rc, out = _migrate(dsns[sid])
            if rc != 0:
                raise RuntimeError(out)
            print(f"{sid}: ready ({ROLE[sid[0]]})", flush=True)
        except Exception as exc:  # noqa: BLE001 -- report and continue; re-run resumes
            failed.append(sid)
            print(f"{sid}: FAILED -- {re.sub(r'postgres(ql)?://[^ ]+', '<dsn>', str(exc))[:600]}", flush=True)
    print(f"done: {len(dsns) - len(failed)} ready, {len(failed)} failed {failed if failed else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
