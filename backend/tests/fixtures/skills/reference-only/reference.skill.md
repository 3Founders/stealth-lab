---
name: canonical-chain-e2e-reference
description: Background on how the migration ledger and checksum drift interact.
---

## What the ledger is

The `schema_migrations` table records one row per applied migration file:
its name, a checksum of its normalized text, and when it was applied. It is
the authority for "has this file run", not a developer's memory.

## Why checksums drift on Windows

The checksum is taken over CRLF-normalized text. A ledger written before that
normalization existed will not match the same unchanged file on a CRLF
checkout. This is cosmetic: the schema is still correct, only the bookkeeping
disagrees.

## Why re-running additive migrations is safe

Every migration in this project is written `CREATE ... IF NOT EXISTS` /
`ADD COLUMN IF NOT EXISTS`. Re-applying one over an already-migrated schema
is a no-op. That property is what makes a ledger correction possible without
a database rebuild.

## Related material

See the migration runner's own module docstring, and the deployment runbook
for the hosted environment.
