"""Read-only: log in as stealth_app (URL from backend/.stealth_app.env, never printed) and show that it can read and
that row-level security applies to it. Prints counts and flags only."""
import asyncio
import re
import sys
from pathlib import Path

import asyncpg

ENV = Path(r"C:\Users\user\stealth-lab\backend\.stealth_app.env").read_text(encoding="utf-8")
names = sys.argv[1:] or ["DATABASE_URL"]


async def check(name: str) -> None:
    m = re.search(rf"(?m)^{name}=(.+)$", ENV)
    if not m:
        print(name, "missing")
        return
    c = await asyncpg.connect(m.group(1).strip(), timeout=30)
    try:
        r = await c.fetchrow("SELECT current_user AS u, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        out = {"user": r["u"], "superuser": r["rolsuper"], "bypassrls": r["rolbypassrls"]}
        has = {t: await c.fetchval("SELECT to_regclass($1) IS NOT NULL", t)
               for t in ("goals", "synced_projects", "routing_decisions")}
        if has["goals"]:
            out["goals_readable"] = (await c.fetchval("SELECT count(*) FROM (SELECT 1 FROM goals LIMIT 5) s")) >= 0
        if has["synced_projects"]:
            out["synced_projects_unscoped"] = await c.fetchval("SELECT count(*) FROM synced_projects")
            async with c.transaction():
                await c.execute("SELECT set_config('app.rls_system', 'on', TRUE)")
                out["synced_projects_system"] = await c.fetchval("SELECT count(*) FROM synced_projects")
        if has["routing_decisions"]:
            out["routing_private_unscoped"] = await c.fetchval(
                "SELECT count(*) FROM routing_decisions WHERE visibility <> 'public'")
        print(name[:12], out)
    finally:
        await c.close()


async def main() -> None:
    for n in names:
        try:
            await check(n)
        except Exception as exc:  # noqa: BLE001
            print(n[:12], "FAILED", type(exc).__name__, str(exc).splitlines()[0][:120])

asyncio.run(main())
