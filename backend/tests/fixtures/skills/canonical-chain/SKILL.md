---
name: canonical-chain-e2e-procedure
description: Roll a stuck database migration forward safely on a shared environment.
license: MIT
---

## When to use this

Use when a numbered SQL migration has partially applied on a shared database
and the migration ledger and the on-disk files disagree.

## Prerequisites

- Read access to the migration ledger table.
- A maintenance window, or confirmation that the tables involved are not being written.

## Steps

1. Snapshot the current ledger: `SELECT filename, checksum FROM schema_migrations ORDER BY filename`.
2. Diff each recorded checksum against the file on disk to find the drift set.
3. For every drifted file that is additive and idempotent, re-run it once.
4. Rewrite the ledger row for each re-run file with the fresh checksum.
5. Re-run the status check and confirm zero pending and zero mismatch.

## Expected outcome

The ledger and the on-disk migration files agree, and a normal `migrate` run
reports nothing pending.

## When not to use

Do not use this on a migration that is destructive or that carries a data
backfill -- those need a dedicated recovery runbook, not a blind re-run.

## Failure modes

- A non-idempotent migration errors on re-run; stop and hand off.
- The ledger table itself is corrupt; restore it from backup first.
