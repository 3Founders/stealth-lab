"""One-off: re-embed all live procedures via the provider chain (Gemini primary).

Why now: the column currently holds Voyage@1024 vectors while the chain now
serves Gemini@1024. Mixing two models' vectors in one VECTOR column makes
cosine similarity meaningless across old/new rows -- the migration-11 drift
failure at runtime scale. The corpus is small (~700 banking rows), so a full
re-embed is minutes today; after a cutover without it, it is a project.

Text reconstruction matches seed_banking_procedures.py's original formula
(title + blank line + document content), where document content lives in
steps[0].action. Rows whose shape differs fall back to goal-only text --
slightly different than their original vector, but consistently so across
every row, which is what ranking needs.

Run: python scripts/reembed_procedures_gemini.py   (from backend/)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.embeddings import Embedder

BATCH = 10          # ~10 x 305 tok = ~3K tokens/request
SLEEP_S = 7.0       # ~25K tokens/min sustained -- under the free tier's 30K TPM


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set")
        sys.exit(1)

    pool = await create_pool(min_size=1, max_size=2)
    rows = await pool.fetch(
        "SELECT id, name, goal, steps FROM procedures "
        "WHERE t_invalid IS NULL AND availability = 'active'"
    )
    print(f"{len(rows)} live procedures to re-embed")

    embedder = Embedder()
    import asyncio as _aio

    updated = 0
    failed: list[str] = []
    for start in range(0, len(rows), BATCH):
        chunk = rows[start : start + BATCH]
        texts = []
        for r in chunk:
            goal = r["goal"] or r["name"] or ""
            steps = json.loads(r["steps"]) if isinstance(r["steps"], str) else (r["steps"] or [])
            action = ""
            if steps and isinstance(steps[0], dict):
                action = steps[0].get("action") or ""
            texts.append(f"{goal}\n\n{action}" if action else goal)
        vectors = None
        for attempt in range(4):
            try:
                vectors = await embedder.embed(texts, input_type="document")
                break
            except Exception as exc:  # noqa: BLE001
                if "429" not in str(exc) or attempt == 3:
                    print(f"  batch {start//BATCH}: FAILED {str(exc)[:120]}")
                    failed.extend(str(r["id"]) for r in chunk)
                    break
                wait = 20.0 * (attempt + 1)
                print(f"  batch {start//BATCH}: 429, waiting {wait:.0f}s")
                await _aio.sleep(wait)
        if vectors is None:
            continue
        async with pool.acquire() as conn:
            await conn.executemany(
                "UPDATE procedures SET embedding = $2::vector WHERE id = $1::uuid",
                [(str(r["id"]), "[" + ",".join(repr(float(v)) for v in vec) + "]")
                 for r, vec in zip(chunk, vectors)],
            )
        updated += len(chunk)
        print(f"  {updated}/{len(rows)}", flush=True)
        if start + BATCH < len(rows):
            await _aio.sleep(SLEEP_S)

    n = await pool.fetchval("SELECT count(*) FROM procedures WHERE t_invalid IS NULL AND embedding IS NOT NULL")
    print(f"\nre-embedded {updated}, failed {len(failed)}; {n} rows now carry embeddings")
    await pool.close()
    sys.exit(1 if failed else 0)


asyncio.run(main())
