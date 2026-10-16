"""Read-only: the production control role's superuser / BYPASSRLS flags and whether it owns the routing tables.
Prints no connection string."""
import asyncio

import asyncpg
from dotenv import dotenv_values

DSN = dotenv_values(r"C:\Users\user\stealth-lab\backend\.env")["DATABASE_URL"]


async def main() -> None:
    c = await asyncpg.connect(DSN)
    try:
        r = await c.fetchrow("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        print("superuser:", r["rolsuper"], "| bypassrls:", r["rolbypassrls"])
        print("owns routing_decisions:", await c.fetchval(
            "SELECT tableowner = current_user FROM pg_tables WHERE tablename = 'routing_decisions'"))
        print("owns synced_projects:", await c.fetchval(
            "SELECT tableowner = current_user FROM pg_tables WHERE tablename = 'synced_projects'"))
    finally:
        await c.close()

asyncio.run(main())
