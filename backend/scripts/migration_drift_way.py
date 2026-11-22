"""Record "reconcile a drifted migration" as a Way in kell's own catalog (owner's request, 2026-11-18).

Why this exists: a deploy once refused to start because schema_migrations recorded a checksum for a version of
db/212_referral_rewards.sql that was never committed. Reading the database -- rather than the error message -- showed the
live table was an OLDER shape than the file, so the applied version had genuinely differed. The fix was: check the live
schema, re-apply the idempotent file, re-baseline the ledger's checksum.

That sequence is right for any migration, not just 212, so it is recorded here as a reusable Way with the specifics as
parameters. Nothing in the Way names 212.

The procedure is written with capture_procedure -- the same service a submitted Way goes through -- so it lands in the
catalog the retriever and the router already read, with scope and provenance (hard rule 2), not as a bare SQL insert.

This file also stays useful as a tool: run it to (a) record the Way, or (b) inspect/remove it with --show/--delete.
It writes ONE Way and ONE goal; it never touches any other row.

    python scripts/migration_drift_way.py --owner <owner-id>            # record it
    python scripts/migration_drift_way.py --owner <owner-id> --show      # what is there now
    python scripts/migration_drift_way.py --owner <owner-id> --delete    # take this Way out (invalidates, never deletes)

Reads DATABASE_URL. Needs an owner id because the Way is private to kell until reviewed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg

NAME = "Reconcile a drifted database migration against the live schema"
GOAL = "A deployed service is blocked by a migration checksum mismatch"

# The Way, step by step. Every step says what to find out or decide; none of them names a particular migration, table or
# service, because the point is that it applies to the next one as well as to this one.
STEPS = [
    {
        "order": 1,
        "actor": "ai",
        "title": "Read the ledger and the live schema, not the error message",
        "prompt": (
            "Query schema_migrations for the filename, checksum and applied_at of the migration named in the failure. "
            "Then ask the database what it actually has: information_schema.columns for the tables the file creates, "
            "pg_constraint and pg_indexes for the constraints and indexes. Print both. Do not infer the cause from the "
            "error text alone -- a mismatch can mean the file changed, the applied version was never committed, or the "
            "live object is older than the file."
        ),
        "source_locator": {"source_id": "kell:scripts/migration_drift_way.py", "anchor": "step-1", "granularity": "section"},
    },
    {
        "order": 2,
        "actor": "ai",
        "title": "Decide which side is behind",
        "prompt": (
            "Compare the two. If the live objects are missing things the file declares, the FILE is ahead: re-apply it. "
            "If the live objects have things the file does not, the DATABASE is ahead and the file was reverted: do not "
            "re-apply, and do not 'fix' it by editing the file. If they match, only the checksum drifted: re-baseline. "
            "Write the verdict as one sentence before acting."
        ),
        "source_locator": {"source_id": "kell:scripts/migration_drift_way.py", "anchor": "step-2", "granularity": "section"},
    },
    {
        "order": 3,
        "actor": "ai",
        "title": "Prove the file is safe to re-run before running it",
        "prompt": (
            "Read the migration. Every statement must be IF NOT EXISTS, CREATE OR REPLACE, or DROP ... IF EXISTS followed "
            "by a re-add. Check what an ADD COLUMN ... NOT NULL would do to existing rows (it fails unless the table is "
            "empty or a default is given), and what a DROP/ADD CONSTRAINT would reject (list the values in use against the "
            "new allowed set first). If any statement would fail on live data, stop and write a follow-up migration "
            "instead of forcing this one."
        ),
        "source_locator": {"source_id": "kell:scripts/migration_drift_way.py", "anchor": "step-3", "granularity": "section"},
    },
    {
        "order": 4,
        "actor": "ai",
        "title": "Apply in one transaction, re-baseline only after it succeeds",
        "prompt": (
            "Run the whole file inside a single transaction, then update schema_migrations.checksum to the file's "
            "checksum computed the runner's way (sha256 of the text with CRLF and lone CR normalised to LF -- never of the "
            "raw bytes). If the apply raises, the transaction rolls back and the ledger keeps its old checksum, so a "
            "failure leaves production exactly as it was. Never re-baseline before the apply succeeds."
        ),
        "source_locator": {"source_id": "kell:scripts/migration_drift_way.py", "anchor": "step-4", "granularity": "section"},
    },
    {
        "order": 5,
        "actor": "ai",
        "title": "Prove the reconciliation, then deploy",
        "prompt": (
            "Re-read information_schema for every object the file declares and confirm each one is present. Confirm the "
            "ledger checksum now equals the file's. Then deploy and confirm the service answers (its health endpoint, and "
            "one real route returning its expected status rather than a 502). Report the deploy id."
        ),
        "source_locator": {"source_id": "kell:scripts/migration_drift_way.py", "anchor": "step-5", "granularity": "section"},
    },
    {
        "order": 6,
        "actor": "human",
        "title": "Decide whether to write the missing change as a new migration",
        "instruction": (
            "When step 2 found the database ahead, or step 3 found a statement that would fail on live data, the fix is a "
            "new migration file, not an edit to the old one. Say yes or no."
        ),
        "source_locator": {"source_id": "kell:scripts/migration_drift_way.py", "anchor": "step-6", "granularity": "section"},
    },
]


async def record(pool: asyncpg.Pool, owner_id: str) -> None:
    from app.services.procedures import capture_procedure

    existing = await pool.fetchval(
        "SELECT id::text FROM procedures WHERE name = $1 AND (owner_id = $2 OR owner_id IS NULL)", NAME, owner_id)
    if existing:
        print(f"already recorded: procedures.id = {existing}")
        return

    out = await capture_procedure(
        pool,
        name=NAME,
        goal=GOAL,
        steps=STEPS,
        # Scope: what makes this Way the right one. Without these the router would offer it for any database question.
        # scope_type must be one the V0 gate allows (v0_gate.SCOPE_TYPES); a reusable catalog Way is scoped to itself.
        scope_type="entity",
        scope={"kind": "reusable_way", "domain": "devops", "database": "postgres"},
        exclusions=["a migration that has never been applied anywhere", "deciding what a schema should be"],
        parameter_schema={
            "type": "object",
            "required": ["migration_file"],
            "properties": {
                "migration_file": {"type": "string", "description": "the db/NN_name.sql the failure names"},
                "database_url_env": {"type": "string", "default": "DATABASE_URL"},
            },
        },
        # Provenance is an enum the V0 gate owns (v0_gate.PROVENANCE_VALUES); this Way came out of our own incident, so
        # it is company_ingested. The story of that incident belongs in domain_payload, which is free-form.
        provenance="company_ingested",
        domain_payload={
            "incident": "2026-11-18: production refused to start because schema_migrations held a checksum for a version "
                        "of db/212_referral_rewards.sql that was never committed. The live table was an older shape than "
                        "the file. Reconciled by re-applying the idempotent file and re-baselining the checksum.",
            "tool": "backend/scripts/migration_drift_way.py",
        },
        domain="devops",
        owner_id=owner_id,
        visibility="private",        # kell's own catalog first; the reviewer can publish it
    )
    pid = (out or {}).get("procedure_id") or (out or {}).get("id")
    print(f"recorded: procedures.id = {pid}")


async def show(pool: asyncpg.Pool, owner_id: str) -> None:
    rows = await pool.fetch(
        "SELECT id::text AS id, name, approval_status, visibility, created_at FROM procedures "
        "WHERE name = $1 AND (owner_id = $2 OR owner_id IS NULL) ORDER BY created_at DESC", NAME, owner_id)
    if not rows:
        print("not recorded")
        return
    for r in rows:
        print(f"{r['id']}  {r['approval_status']:12} {r['visibility']:8} {r['created_at']}")


async def remove(pool: asyncpg.Pool, owner_id: str) -> None:
    # Invalidate, never delete: nothing in this system deletes a procedure (bitemporal, closed-append).
    n = await pool.execute(
        "UPDATE procedures SET t_invalid = now() WHERE name = $1 AND owner_id = $2 AND t_invalid IS NULL", NAME, owner_id)
    print(f"invalidated: {n}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True, help="user id the Way is private to until reviewed")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        if args.show:
            await show(pool, args.owner)
        elif args.delete:
            await remove(pool, args.owner)
        else:
            await record(pool, args.owner)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
