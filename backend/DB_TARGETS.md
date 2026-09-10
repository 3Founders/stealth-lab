# Switching between the local and hosted Postgres

Two databases:

| target   | where it comes from                          | use for |
|----------|----------------------------------------------|---------|
| `local`  | `DATABASE_URL_LOCAL` in `backend/.env`        | migrations, `*_e2e.py`, schema-drift, backfills, heavy dev work |
| `hosted` | `DATABASE_URL` (or `DATABASE_URL_HOSTED`) in `backend/.env` | the deployed Supabase instance — egress-limited, touch sparingly |

Nothing in the codebase changed. `scripts/migrate.py`, `app.config`, and the
`*_e2e.py` suite all read `DATABASE_URL`; the e2e suite also honours
`TEST_DATABASE_URL` (via `tests/conftest.py`). The `dbtarget` helpers just
set those variables to the target you name.

## One-off command against a target

Env is set only for that one process — your shell is untouched:

```powershell
python scripts\dbtarget.py local  -- python scripts\migrate.py --status
python scripts\dbtarget.py local  -- python scripts\migrate.py
python scripts\dbtarget.py local  -- python -m pytest tests -q -k e2e
python scripts\dbtarget.py hosted -- python scripts\migrate.py --status
```

## Whole shell session against a target

**PowerShell** — dot-source (note the leading `. `):

```powershell
. .\scripts\dbtarget.ps1 local     # this session now uses the local DB
. .\scripts\dbtarget.ps1 hosted    # flip back to Supabase
. .\scripts\dbtarget.ps1 show      # what's active + what each resolves to
. .\scripts\dbtarget.ps1 off       # clear the overrides
```

**bash / zsh** — `source`:

```bash
source scripts/dbtarget.sh local
source scripts/dbtarget.sh hosted
source scripts/dbtarget.sh show
source scripts/dbtarget.sh off
```

After `... local`, a bare `python scripts/migrate.py` or `python -m pytest
tests -q` uses the local DB.

## Guardrails (kept, not weakened)

- `tests/conftest.py` still **refuses** to run the row-writing `*_e2e.py`
  suite against a Supabase URL unless `STEALTH_ALLOW_PROD_E2E=1`. `dbtarget
  hosted` does **not** set that flag. Run e2e against `local`.
- `dbtarget` resolves `hosted` from the `.env` **file**, not from a
  `DATABASE_URL` left in your environment — so switching to `local` and back
  to `hosted` is deterministic, never a stale value.

## First-time local setup

```powershell
# 1. a local pgvector Postgres (stock postgres:15 fails migration 01)
docker run -d --name stealth-local -e POSTGRES_PASSWORD=postgres -p 5432:5432 pgvector/pgvector:pg15

# 2. add to backend/.env
#    DATABASE_URL_LOCAL=postgresql://postgres:postgres@localhost:5432/postgres

# 3. apply the schema
python scripts\dbtarget.py local -- python scripts\migrate.py
python scripts\dbtarget.py local -- python scripts\migrate.py --status   # 55/55 applied

# 4. run the DB-backed suite
python scripts\dbtarget.py local -- python -m pytest tests -q
```
