"""Which provisioning statement Neon refuses -- each inside a savepoint, the whole transaction ROLLED BACK (nothing kept)."""
import asyncio
import sys

import asyncpg
from dotenv import dotenv_values

sys.path.insert(0, r"C:\Users\user\stealth-lab\backend\scripts")
from provision_app_role import STATEMENTS  # noqa: E402

DSN = dotenv_values(r"C:\Users\user\stealth-lab\backend\.env")["DATABASE_URL"]
VARIANTS = [
    "CREATE ROLE stealth_app_probe LOGIN",
    "CREATE ROLE stealth_app_probe LOGIN NOSUPERUSER",
    "CREATE ROLE stealth_app_probe LOGIN NOCREATEDB NOCREATEROLE",
    "CREATE ROLE stealth_app_probe LOGIN NOREPLICATION",
    "CREATE ROLE stealth_app_probe LOGIN NOBYPASSRLS",
    "CREATE ROLE stealth_app_probe LOGIN NOINHERIT",
]


async def main():
    c = await asyncpg.connect(DSN)
    try:
        print("server:", await c.fetchval("SHOW server_version"))
        tx = c.transaction()
        await tx.start()
        await c.execute("CREATE ROLE stealth_app_probe LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                        "NOBYPASSRLS NOINHERIT")
        db = await c.fetchval("SELECT current_database()")
        for sql in ["ALTER ROLE stealth_app_probe PASSWORD 'probe-not-a-real-secret-123456'",
                    f'GRANT CONNECT ON DATABASE "{db}" TO stealth_app_probe']:
            sp = c.transaction()
            await sp.start()
            try:
                await c.execute(sql)
                print("OK     ", sql)
            except asyncpg.PostgresError as exc:
                print("REFUSED", sql, "->", exc.__class__.__name__, str(exc).splitlines()[0][:160])
            await sp.rollback()
        await tx.rollback()
    finally:
        await c.close()

asyncio.run(main())
