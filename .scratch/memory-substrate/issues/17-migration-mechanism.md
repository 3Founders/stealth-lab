# Migration mechanism and data migration

Type: grilling
Status: resolved
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

## Answer

### Part 1 — mechanism: keep raw SQL, add the missing discipline. Do not adopt Alembic.

The ticket's own sharpest question decides this: what would actually have caught the two live
drifts? Working through both concretely:

- **`embedding_joint` ghost column** (`retrieval.py` accepts it, no DDL file creates it) — caught
  by a test that walks every `embedding_column` literal the code accepts and confirms each is a
  real column in the schema. A schema-vs-code consistency check, not a migration-ledger concern.
- **`ProvenanceSource` missing `public_generated`** (confirmed directly: `ontology.py:17` is
  `Literal["company_ingested", "company_debate", "prior_library"]` — `public_generated` is
  genuinely absent, even though the DB has it and `apply_generated()` writes it) — caught by a
  test that reads the real DB enum's values and diffs them against the Python `Literal`. Same
  category: schema-vs-model consistency.

Both real, already-happened drifts would have been caught by a test, not by adopting a
framework. Alembic's real value — autogenerating migrations from diffing ORM models against DB
state — requires SQLAlchemy models that do not exist here and that nothing else in this codebase
(asyncpg direct, throughout) points toward adopting. Without that, Alembic would just be its
revision/ledger machinery wrapped around the same hand-written SQL: a real but much smaller win
than it looks like, for a team at this stage that already made the equivalent call in ticket 09
(defer infrastructure that is not yet earning its cost).

**Four additions, each aimed at a specific, named gap, not a generic upgrade:**

1. A `schema_migrations` ledger table (filename, checksum, applied_at) — closes "no version
   table, no ledger, no checksum" directly.
2. **One documented runner script**, replacing the broken `for f in db/0*.sql` loop (which
   silently skips `10_code_sourced_agents.sql` — the table `code_review.py` inserts into) and the
   README's incomplete 01-05 listing. Applies only unapplied files, checked against the ledger.
3. **A CI test targeting exactly the two known drifts** above, not a generic "add more tests"
   gesture — the two diffs described, made real and running.
4. **Split seed data out of the DDL sequence.** `09_seed_internal_agents.sql` being an INSERT
   inside the numbered schema sequence — with its own comment admitting `agents` has no unique
   constraint to `ON CONFLICT` against, hence the `WHERE NOT EXISTS` guard — is a real smell
   worth naming, not just working around silently.

**Two things explicitly not counted as evidence either way:** the `08a`/`08b` same-transaction
trap is a real Postgres constraint (a newly-added enum value cannot be used in the transaction
that added it) — Alembic would hit the identical wall, so it is not an argument for or against
either mechanism. Baselining the existing 11 files into the new ledger (checksum + backfill
INSERT per file) is a one-time, cheap cost regardless of which mechanism wins, so it should not
be weighed as a reason to prefer the framework either.

**Rollback**, named as a real gap in the ticket: given this is an append-only,
invalidate-and-supersede system by design, full automated down-migrations are probably low value
for the cost of building them robustly. A documented "how to manually undo migration N" runbook
entry is the pragmatic middle ground over real reversibility for every future file.

### Part 2 — data migration, worked through per object as the ticket actually asks

- **`episodes`**: no migration needed. Inherited directly from ticket 06's decision — the schema
  is not changing (`content`/`content_ref` unchanged), so existing debate-transcript rows simply
  continue existing as valid historical episodes under the new usage.
- **`traces`**: no migration needed. Also inherited from ticket 06, which explicitly leaves
  `traces` completely untouched — stated here explicitly rather than left for a reader to infer
  from a different ticket.
- **`knowledge_nodes`, generic + `code_location`/`failure_mode` node types**: no migration
  needed. The `node_type`-discriminated design already supports new shapes arriving alongside old
  ones without touching existing rows — the same preserve-don't-destroy principle this whole
  ticket is built on.
- **`knowledge_nodes`, `node_type='claim'` specifically — genuinely not resolvable here.** How
  existing claim-type rows map to spec.md's real claim/TMS representation depends on
  **ticket 03 (Claim representation)**, still open. The migration *principle* for this ticket is
  the same as the method-library case below (preserve IDs where safe, explicit link where not);
  the concrete mapping is blocked on ticket 03's own answer and should not be invented
  prematurely here.
- **`edges`**: no migration needed — typed relationships between existing rows, unaffected by any
  of this.
- **Method-library `task_nodes` rows becoming procedures — do not reuse the UUID.** Real risk if
  reused: any code querying `task_nodes` by that id would collide with procedure-specific
  expectations, and vice versa — exactly the ticket's own stated concern, concretely realized.
  The existing, already-proven pattern for "this derives from that" in this codebase is an
  explicit edge (`CONFLICTS_WITH`, `VALIDATED_BY`, etc.), not shared identity. So: the new
  procedure gets a fresh UUID, linked back to the legacy `task_node` via an explicit edge —
  nothing destructive happens to the original row, and "preserve IDs" and "avoid a false identity
  collision" stop being in tension.
- **The SWE-bench corpus (731 rows across the tables above): stays exactly where it is,
  untouched.** Three reasons: explicitly out of scope per `map.md`'s own locked decision with the
  repo owner; ticket 09's answer only requires `owner_id` on *new* tables, so the corpus sitting
  in existing tables is not a blocker for anything this effort adds; and if/when the SWE-bench
  experiment's fate is decided later, that is the right point to decide whether it needs its own
  database or an `owner_id` backfill — deciding it now inside this ticket would be scope creep
  past what is already locked.
### Addendum (added on review) — the `embedding_joint` drift is worse than described

Verified independently: `retrieval.py:89` accepts `embedding_joint` as a valid
`embedding_column` value, and **no DDL file anywhere in `backend/db/` creates that column**
(confirmed by grep across all 11 files). The ticket describes the consequence as "constructing a
retriever with it passes validation and fails at query time," which is accurate but understates
the scope, because the column is not merely *accepted* — it is actively *depended on* by real,
running code outside `backend/`:

- `experiments/swebench_pro/graph_ingest.py:343-365` (`--joint-embeddings`) issues
  `UPDATE task_nodes SET embedding_joint = ...` and queries `WHERE ... embedding_joint IS NULL`.
- `experiments/swebench_pro/compare_embeddings.py:103-141` reads it as one of two compared
  columns, and Stage 2's headline result (joint beats split, sign test p=0.0066, n=400) was
  measured through it.

So a column that no version-controlled DDL creates is load-bearing for a produced experimental
result. It must have been added by hand, out of band, on whichever database those runs used —
which means the schema that produced a recorded result cannot be reconstructed from this
repository. That is a stronger argument for the ledger + runner + CI-check package proposed above
than the ticket's own framing implies: the failure is not only "a query errors later," it is
"a result exists whose schema provenance is unrecoverable."

It also sharpens what the CI check must actually compare. Checking `retrieval.py`'s accepted
literals against `backend/db/*.sql` alone would still pass on a database where someone had added
the column by hand. The check has to compare accepted literals against the **live database's**
`information_schema.columns`, and separately assert that every column the live database has is
creatable from a version-controlled DDL file — otherwise it validates the drift instead of
catching it.