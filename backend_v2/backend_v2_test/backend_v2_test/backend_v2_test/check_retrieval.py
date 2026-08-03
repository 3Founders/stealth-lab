import asyncio
from app.db.session import create_pool


async def main():
    pool = await create_pool()

    print("-- 1. does the visibility column have the value the filter expects? --")
    rows = await pool.fetch(
        "SELECT name, visibility::text AS vis, owner_id FROM task_nodes "
        "WHERE name = 'Extract structured fields' AND t_invalid IS NULL"
    )
    for r in rows:
        print(f"  name={r['name']!r} visibility={r['vis']!r} owner_id={r['owner_id']!r}")

    print()
    print("-- 2. does the raw full-text match work at all, with no visibility filter? --")
    raw = await pool.fetch(
        "SELECT name, ts_rank(to_tsvector('english', name || ' ' || COALESCE(description,'')), "
        "plainto_tsquery('english', $1)) AS rank "
        "FROM task_nodes "
        "WHERE to_tsvector('english', name || ' ' || COALESCE(description,'')) "
        "@@ plainto_tsquery('english', $1)",
        "What does the extraction step depend on?",
    )
    print(f"  {len(raw)} raw match(es) with no visibility filter at all")
    for r in raw[:5]:
        print(f"    {r['name']!r} rank={r['rank']}")

    print()
    print("-- 3. same match, WITH the visibility filter the real query applies --")
    filtered = await pool.fetch(
        "SELECT name FROM task_nodes "
        "WHERE t_invalid IS NULL AND visibility = 'public' "
        "AND to_tsvector('english', name || ' ' || COALESCE(description,'')) "
        "@@ plainto_tsquery('english', $1)",
        "What does the extraction step depend on?",
    )
    print(f"  {len(filtered)} match(es) with the visibility filter applied")
    for r in filtered[:5]:
        print(f"    {r['name']!r}")

    await pool.close()


asyncio.run(main())