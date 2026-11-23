"""Repair way_runs rows whose jsonb columns hold a JSON string scalar instead of the array/object they mean.

These rows were written by `_save` before 2026-11-23, when it passed `json.dumps(...)` to a pool whose jsonb codec
already encodes. The result is `"[]"` where `[]` was meant. The Python read path hides it (it json.loads()es the
string), but any SQL that treats the column as JSON -- jsonb_array_length, `->`, containment -- fails with
"cannot get array length of a scalar".

Idempotent: only rows whose type is 'string' are touched, and the inner text is re-parsed once.
Run with:  python scripts/fix_jsonb_double_encoding.py [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib

import asyncpg

COLUMNS = ("outputs", "plan", "waiting", "step_inputs")


def dsn_from_env() -> str:
    d = {}
    for line in pathlib.Path(".env").read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d["DATABASE_URL"].split("?", 1)[0]


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(dsn_from_env(), timeout=60)
    try:
        for col in COLUMNS:
            broken = await conn.fetchval(
                f"SELECT count(1) FROM way_runs WHERE jsonb_typeof({col}) = 'string'")
            always = await conn.fetchval(f"SELECT count(1) FROM way_runs")
            print(f"{col:<12} string-typed rows: {broken}/{always}")
            if not broken or not apply:
                continue
            # `(#>> '{}')` is the dequoted text; casting it back to jsonb gives the array/object it stands for.
            done = await conn.fetchval(
                f"""WITH fixed AS (
                        UPDATE way_runs
                           SET {col} = (({col} #>> '{{}}'))::jsonb
                         WHERE jsonb_typeof({col}) = 'string'
                         RETURNING 1)
                    SELECT count(1) FROM fixed""")
            print(f"  repaired {done} row(s) in {col}")
        if not apply:
            print("\n(dry run: pass --apply to write)")
        else:
            print("\nafter:")
            for col in COLUMNS:
                print(f"  {col:<12}", [(r["t"], r["n"]) for r in await conn.fetch(
                    f"SELECT jsonb_typeof({col}) AS t, count(1) AS n FROM way_runs GROUP BY 1 ORDER BY 2 DESC")])
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write the repair")
    asyncio.run(main(ap.parse_args().apply))
