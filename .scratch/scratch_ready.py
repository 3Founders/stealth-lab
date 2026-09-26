"""Throwaway readiness harness -- delete after use.

Creates scratch DB `stealth_scratch_ready` in the SAME local Postgres
cluster as $DATABASE_URL_LOCAL (dev DB untouched), migrates it fresh,
runs ONLY the golden document-path E2E against it via TEST_DATABASE_URL,
prints the result, then drops the scratch DB. Never prints credentials.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env")

BACKEND = r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend"
SCRATCH_DB = "stealth_scratch_ready"


def _scratch_dsn() -> str:
    base = os.environ["DATABASE_URL_LOCAL"]
    # swap only the path component after host:port
    scheme_sep = "://"
    head, tail = base.split(scheme_sep, 1)
    creds_host, _, path_query = tail.partition("/")
    path, _, query = path_query.partition("?")
    return f"{head}{scheme_sep}{creds_host}/{SCRATCH_DB}" + (f"?{query}" if query else "")


async def _recreate_scratch(admin_dsn: str) -> None:
    import asyncpg

    base = os.environ["DATABASE_URL_LOCAL"]
    conn = await asyncpg.connect(base.rsplit("/", 1)[0] + "/postgres")
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {SCRATCH_DB}")
    finally:
        await conn.close()
    del admin_dsn


def _run(cmd: list[str], extra_env: dict[str, str]) -> int:
    env = dict(os.environ)
    env.update(extra_env)
    proc = subprocess.run(cmd, cwd=BACKEND, env=env, capture_output=True, text=True)
    out = (proc.stdout or "")[-4000:]
    err = (proc.stderr or "")[-2000:]
    print(f"$ {' '.join(cmd)}\nrc={proc.returncode}\n{out}\n{err}")
    return proc.returncode


def main() -> int:
    scratch = _scratch_dsn()
    asyncio.run(_recreate_scratch(scratch))
    print(f"[scratch] {SCRATCH_DB} created")
    rc = _run([sys.executable, "scripts/migrate.py"], {"DATABASE_URL": scratch})
    if rc != 0:
        print("MIGRATIONS FAILED -- aborting (scratch DB left for inspection)")
        return rc
    rc = _run(
        [sys.executable, "-m", "pytest", "tests/test_ingestion_canonical_chain_e2e.py",
         "-q", "-o", "asyncio_default_fixture_loop_scope=function"],
        {"TEST_DATABASE_URL": scratch},
    )
    print(f"PYTEST_RC={rc}")
    return rc


if __name__ == "__main__":
    code = main()
    # drop the scratch DB either way; ignore failures
    try:
        import asyncpg

        async def _drop() -> None:
            conn = await asyncpg.connect(
                os.environ["DATABASE_URL_LOCAL"].rsplit("/", 1)[0] + "/postgres"
            )
            try:
                await conn.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)")
            finally:
                await conn.close()

        asyncio.run(_drop())
        print(f"[cleanup] {SCRATCH_DB} dropped")
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] drop failed (manual cleanup may be needed): {exc!r}")
    sys.exit(code)
