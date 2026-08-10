"""
Fetches the real 'pdf' skill task_node from the AFTER skills library
(ingested by ingest_after_skills.py) -- the real data Cell 2's
TaskNode trajectory hint will be built from.

Run from backend/:
    python scripts/fetch_pdf_skill_node.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool


async def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    row = await pool.fetchrow(
        "SELECT name, description, skill_ref, io_schema, success_criteria "
        "FROM task_nodes WHERE skill_ref = 'pdf' AND t_invalid IS NULL"
    )
    await pool.close()

    if not row:
        print("No task_node found with skill_ref = 'pdf' -- was ingest_after_skills.py run "
              "against this database?")
        return

    print("name:", row["name"])
    print("skill_ref:", row["skill_ref"])
    print("\ndescription:")
    print(row["description"])
    print("\nio_schema:", row["io_schema"])
    print("\nsuccess_criteria:")
    print(json.dumps(row["success_criteria"], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
