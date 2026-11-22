"""Submit "reconcile a drifted migration" as a normal Way (owner's request, 2026-11-18).

Goes through app.submissions.api.submit -- exactly what POST /v1/goals/submissions does -- so the Way enters the same
queue, gets the same AI review, its own Goal, and lands in the catalog with real provenance if it passes. Nothing is
written directly into procedures; there is no shortcut here.

The steps are written to work for ANY drifted migration: none of them names 212, a table, a service or an environment.
The specifics travel as a parameter (migration_file).

    python scripts/submit_migration_drift_way.py --owner <user-id>      # submit
    python scripts/submit_migration_drift_way.py --owner <id> --show    # what state did it reach
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import asyncpg

GOAL = "Reconcile a drifted database migration against its live schema"
DESCRIPTION = (
    "A deploy is blocked because the migration ledger's checksum no longer matches the file on disk, or because the live "
    "schema differs from what the file declares. For any Postgres migration: find which side is behind, prove the file "
    "is safe to re-run, apply it in one transaction, re-baseline the ledger, and prove the result before deploying. "
    "Written from a real incident; nothing in it is specific to that migration."
)

STEPS = [
    {
        "kind": "automated",
        "title": "Read the ledger and the live schema, not the error message",
        "prompt": (
            "Query schema_migrations for the filename, checksum and applied_at of the migration named in the failure. Ask "
            "the database what it actually has: information_schema.columns for the tables the file creates, pg_constraint "
            "for its constraints, pg_indexes for its indexes. Print both. Do not infer the cause from the error text: a "
            "mismatch can mean the file changed, the applied text was never committed, or the live object is older than "
            "the file."
        ),
    },
    {
        "kind": "automated",
        "title": "Decide which side is behind",
        "prompt": (
            "Compare the two. Live objects missing things the file declares means the FILE is ahead: re-apply it. Live "
            "objects having things the file does not means the DATABASE is ahead and the file was reverted: do not "
            "re-apply, and do not edit the file to match. Identical objects with a different checksum means only the "
            "checksum drifted: re-baseline. State the verdict in one sentence before acting."
        ),
    },
    {
        "kind": "automated",
        "title": "Prove the file is safe to re-run before running it",
        "prompt": (
            "Read the migration. Every statement must be IF NOT EXISTS, CREATE OR REPLACE, or DROP ... IF EXISTS then "
            "re-add. Check what ADD COLUMN ... NOT NULL would do to existing rows (it fails unless the table is empty or "
            "a default is given) and what a DROP/ADD CONSTRAINT would reject (list the values in use against the new "
            "allowed set). If any statement would fail on live data, stop and write a follow-up migration instead."
        ),
    },
    {
        "kind": "automated",
        "title": "Apply in one transaction, re-baseline only after it succeeds",
        "prompt": (
            "Run the whole file inside one transaction, then set schema_migrations.checksum to the file's checksum "
            "computed the runner's way -- sha256 of the text with CRLF and lone CR normalised to LF, never of the raw "
            "bytes. If the apply raises, the transaction rolls back and the ledger keeps its old checksum, so a failure "
            "leaves production as it was. Never re-baseline before the apply succeeds."
        ),
    },
    {
        "kind": "automated",
        "title": "Prove the reconciliation, then deploy",
        "prompt": (
            "Re-read information_schema for every object the file declares and confirm each is present. Confirm the "
            "ledger checksum now equals the file's. Deploy, then confirm the service answers: its health endpoint and one "
            "real route returning its expected status rather than a 502. Report the deploy id."
        ),
    },
    {
        "kind": "human",
        "title": "Decide whether the missing change becomes a new migration",
        "description": (
            "When the database turned out to be ahead, or a statement would fail on live data, the fix is a NEW migration "
            "file, never an edit to the old one. Decide yes or no."
        ),
    },
]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True, help="the user id submitting this Way")
    ap.add_argument("--show", action="store_true", help="print the newest submission's state instead of submitting")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    try:
        from app.submissions import api

        if args.show:
            mine = await api.mine(pool, args.owner)
            for row in mine[:3]:
                print(json.dumps(row, default=str, indent=1))
            return
        out = await api.submit(pool, args.owner, {"goal": GOAL, "description": DESCRIPTION, "steps": STEPS})
        print("submission:", out.get("submission_id"))
        print("status:    ", out.get("status"))
        print("mode:      ", out.get("mode_label"))
        print("message:   ", out.get("message"))
        for s in out.get("steps") or []:
            print(f"  step {s.get('order')}: {s.get('status'):12} {s.get('title')[:60]}")
            if s.get("reason"):
                print(f"      why: {s['reason'][:160]}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
