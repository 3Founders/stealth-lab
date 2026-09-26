import asyncio
from pathlib import Path

from app.db.session import create_pool
from app.execution.workspace_init import init_workspace


async def main() -> None:
    repo = str(Path(__file__).parent / "onboarding_trial")
    pool = await create_pool(statement_cache_size=0, min_size=1, max_size=2)
    try:
        first = await init_workspace(pool, repo, created_by="trial_workspace_init")
        second = await init_workspace(pool, repo, created_by="trial_workspace_init")
        print({"first": first, "second": second})
    finally:
        await pool.close()


asyncio.run(main())
