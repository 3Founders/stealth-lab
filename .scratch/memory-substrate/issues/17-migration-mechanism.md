# Migration mechanism and data migration

Type: grilling
Status: claimed
Blocked by: 01

## Question

Two coupled questions: does the schema-change mechanism itself change, and what is the migration strategy for existing data?

This milestone adds a substantial number of tables and columns to a schema that is currently managed by hand. The mechanism decision has to be made before the first new table is written.

### Part 1 — the mechanism

The relevant existing facts, all of them concrete failures rather than aesthetic objections:

- 11 hand-numbered raw SQL files in `backend/db/`, idempotent by construction (`IF NOT EXISTS`, enum creation wrapped in exception-swallowing `DO` blocks), applied manually via `psql`.
- **No version table, no ledger, no checksum.** Nothing records which files have been applied to a given database.
- **No rollback path** for any migration.
- **A documented same-transaction trap**: `08a` and `08b` must be two genuinely separate executions, because Postgres forbids using a newly-added enum value in the transaction that added it. The file's own header explains at length that a SQL editor treating a pasted script as one transaction will fail.
- **The documented setup loop is wrong**: `for f in db/0*.sql` silently skips `10_code_sourced_agents.sql`, leaving a database without `agent_code_reviews` — a table `code_review.py` inserts into. The primary backend README lists only files 01–05.
- Data seeding is mixed into the DDL sequence (`09_seed_internal_agents.sql` is an INSERT, idempotent only via a `WHERE NOT EXISTS` guard, because `agents` has no unique constraint to conflict against).
- **Two live schema/model drifts caused by exactly this process**: `embedding_joint` is accepted and validated by `retrieval.py` but created by no DDL file (constructing a retriever with it passes validation and fails at query time); and `ProvenanceSource` in `models/ontology.py` omits `public_generated`, which the DB has and `apply_generated()` writes — so hydrating those rows through `from_row()` raises a `ValidationError`.

Decide: adopt a framework (Alembic is the default for this stack, though there is no SQLAlchemy here to hang it off; a lighter migration runner is also viable), or keep raw SQL and add the missing discipline (a version ledger table, a runner script that is the single documented entry point, and a CI check that models and schema agree).

### Part 2 — the data migration

spec.md: do not destroy existing data; preserve IDs where they map cleanly; create explicit compatibility links where they do not; existing reusable plans must become candidate procedures or legacy procedure records rather than silently disappearing.

The objects to map: existing `episodes` (only debate transcripts exist), `traces`, `knowledge_nodes` (including the virtual types `claim`, `failure_mode`, `code_location`), `task_nodes` (both runtime nodes and method-library rows), `edges`, and the method-library rows specifically.

Grill these:

- Adopting a framework mid-flight means the existing 11 files need a baseline. Is that a one-time cost worth paying now, or does it churn a system whose *real* failure was documentation and drift rather than the mechanism?
- **What would actually have caught the two live drifts?** If the answer is "a test comparing models to schema" rather than "a migration framework," then the framework is not the fix and should be judged on its own merits.
- The 731-row SWE-bench corpus currently lives in these tables. This map put the SWE-bench experiment out of scope — does its data migrate, stay untouched, or get separated into its own database? Answering "it stays" has schema consequences for every new owner/isolation column (ticket 09).
- Preserving IDs "where safe" needs a definition of safe. If a method-library `task_nodes` row becomes a procedure, does keeping the UUID create a row that two different concepts both claim?
