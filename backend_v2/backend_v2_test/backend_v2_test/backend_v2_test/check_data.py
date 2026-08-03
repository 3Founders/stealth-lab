import asyncio
from app.db.session import create_pool


async def main():
    pool = await create_pool()
    n = await pool.fetchval("SELECT COUNT(*) FROM task_nodes WHERE t_invalid IS NULL")
    print("live task nodes:", n)
    rows = await pool.fetch("SELECT name FROM task_nodes WHERE t_invalid IS NULL LIMIT 10")
    for r in rows:
        print(" -", r["name"])
    await pool.close()


asyncio.run(main())