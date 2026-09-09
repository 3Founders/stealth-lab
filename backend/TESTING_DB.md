# Running the database (`*_e2e.py`) tests

The `*_e2e.py` suite **writes** rows — problems, procedures, benchmarks,
evaluations, claims. Point it at a **throwaway** Postgres, never at the
production/Supabase database in `backend/.env`. e2e fixtures seeded into the
live DB show up in the product (e.g. a problem titled
`[staleness-eval-gap-e2e <run>] ...` on the Problems page).

## How the wiring works (`tests/conftest.py`)

- Every `*_e2e.py` gates on `os.environ["DATABASE_URL"]`; unset → the test
  **skips**. So a plain `pytest tests` (offline) never touches a database.
- `.env`'s `DATABASE_URL` is stripped back out for tests — it cannot leak in
  through `dotenv.load_dotenv`.
- **`TEST_DATABASE_URL`** is the supported way in: if it is set and
  `DATABASE_URL` is *not* already exported, conftest promotes it to
  `DATABASE_URL` for the test process only. Nothing outside pytest sees it.
- Guardrail: if `DATABASE_URL` resolves to a hosted host (`supabase.co`,
  `neon.tech`, `rds.amazonaws.com`, …) the whole run aborts unless you set
  `STEALTH_ALLOW_PROD_E2E=1`. So `DATABASE_URL=<supabase> pytest` — the old
  footgun — now fails fast instead of seeding production.

## One-time throwaway DB

```bash
docker compose -f backend/docker-compose.test.yml up -d
# apply migrations to it (DATABASE_URL export is fine here — it's the throwaway)
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/stealth_test \
  python scripts/migrate.py
```

## Run the e2e suite against it

```bash
cd backend
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/stealth_test \
  python -m pytest tests -q
```

Wipe and recreate any time:

```bash
docker compose -f backend/docker-compose.test.yml down -v
docker compose -f backend/docker-compose.test.yml up -d
```

## Cleaning e2e junk already in the live DB

Rows a past `DATABASE_URL=<supabase> pytest` seeded. Review before deleting;
run against the Supabase DB (SQL editor or `psql`):

```sql
-- see what's there
SELECT id, title, created_at FROM problems
WHERE title LIKE '[%-e2e %]%' OR title LIKE '[%probe%';

-- delete (cascades to associated benchmarks/solutions/evaluations if FKs are ON DELETE CASCADE;
-- otherwise delete those first)
DELETE FROM problems WHERE title LIKE '[%-e2e %]%';
```

Other e2e-prefixed fixtures follow the same `[<slug>-e2e <hex>] ...` naming —
widen the `LIKE` as needed after reviewing.
