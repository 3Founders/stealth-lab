"""Final-V1 eval finding A: dangling sub-procedure pins.

Some LIVE procedures -- notably the `proc-test-planonly-root-*` fixtures
accumulated on the shared dev DB -- have a step with
``subprocedure_ref.procedure_id`` pointing at a procedure_id that has NO
live version row. The V-COMPOSE integrity guard correctly refuses to
resolve such a pin to a different version, so `find_best_way` /
`reproduce_procedure` fail for EVERY query whose tier-1 retrieval touches
one of these (the verified corpus is tiny, so they surface constantly).

A procedure that cannot compose is not a usable procedure. This script
tombstones the broken PARENT rows -- bi-temporal invalidate
(``t_invalid = t_expired = now()``), NEVER a DELETE -- and their live
`edges`, routed through one aggregate ChangeSet. Seeding fake child
procedures just to satisfy fixture pins would only add more detritus;
removing the unusable parents is the honest fix.

    python scripts/repair_dangling_subprocedure_pins.py            # dry run
    python scripts/repair_dangling_subprocedure_pins.py --apply    # tombstone
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(str(Path(__file__).resolve().parents[1] / ".env"))

from app.db.session import create_pool  # noqa: E402
from app.services.changeset_record import ChangeOperation, record_change_set  # noqa: E402

_AUTHOR = "procedure_graph_repair"
_REASON = ("finding A: procedure has a subprocedure_ref pin to a procedure_id "
           "with no live version -- cannot compose, tombstoned")


def _subproc_targets(steps) -> list[str]:
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except (ValueError, TypeError):
            return []
    out: list[str] = []
    for step in steps or []:
        ref = step.get("subprocedure_ref") if isinstance(step, dict) else None
        if isinstance(ref, dict) and ref.get("procedure_id"):
            out.append(str(ref["procedure_id"]))
    return out


async def _find_broken(pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT id::text, procedure_id::text, name, version, verification_state, steps "
        "FROM procedures WHERE t_invalid IS NULL AND steps::text ILIKE '%subprocedure_ref%'"
    )
    all_refs: set[str] = set()
    parent_refs: dict[str, list[str]] = {}
    for r in rows:
        refs = _subproc_targets(r["steps"])
        if refs:
            parent_refs[r["id"]] = refs
            all_refs.update(refs)
    if not all_refs:
        return []
    live = {
        x["procedure_id"] for x in await pool.fetch(
            "SELECT procedure_id::text FROM procedures "
            "WHERE procedure_id = ANY($1::uuid[]) AND t_invalid IS NULL",
            list(all_refs),
        )
    }
    missing = all_refs - live
    broken = []
    for r in rows:
        bad = [ref for ref in parent_refs.get(r["id"], []) if ref in missing]
        if bad:
            broken.append({
                "id": r["id"], "procedure_id": r["procedure_id"], "name": r["name"],
                "version": r["version"], "verification_state": r["verification_state"],
                "missing_refs": bad,
            })
    return broken


async def main(apply: bool) -> None:
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        broken = await _find_broken(pool)
        if not broken:
            print("no dangling sub-procedure pins among live procedures -- nothing to do")
            return
        print(f"{len(broken)} live procedure(s) with a dangling sub-procedure pin:")
        for b in broken:
            print(f"  {b['name']!r:52} v{b['version']} [{b['verification_state']}] "
                  f"id={b['id']}  ->  missing {b['missing_refs']}")
        if not apply:
            print("\n(dry run -- rerun with --apply to tombstone these + their live edges)")
            return

        row_ids = [b["id"] for b in broken]
        async with pool.acquire() as conn:
            async with conn.transaction():
                now = await conn.fetchval("SELECT now()")
                await conn.execute(
                    "UPDATE procedures SET t_invalid = $2, t_expired = $2, updated_at = $2 "
                    "WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL", row_ids, now)
                edges_tombstoned = await conn.fetchval(
                    "WITH t AS ( UPDATE edges SET t_invalid = $2, t_expired = $2 "
                    " WHERE t_invalid IS NULL AND ("
                    "   (source_id = ANY($1::uuid[]) AND source_table = 'procedures') OR "
                    "   (target_id = ANY($1::uuid[]) AND target_table = 'procedures')"
                    " ) RETURNING 1 ) SELECT count(*) FROM t", row_ids, now)
        cs_id = await record_change_set(
            pool, author=_AUTHOR, reason=_REASON,
            operations=[
                ChangeOperation(
                    operation="invalidate", target_table="procedures", target_id=b["id"],
                    detail={"name": b["name"], "version": b["version"],
                            "verification_state": b["verification_state"],
                            "missing_subprocedure_refs": b["missing_refs"],
                            "finding": "A"})
                for b in broken
            ],
        )
        print(f"\ntombstoned {len(broken)} procedure(s), {edges_tombstoned or 0} edge(s). "
              f"ChangeSet {cs_id}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
