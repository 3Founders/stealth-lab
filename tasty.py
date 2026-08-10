import asyncio
from app.services.embeddings import Embedder
import numpy as np

async def main():
    e = Embedder()
    query_vec = np.array(await e.embed_one("api", input_type="query"))

    import asyncpg, os
    from dotenv import load_dotenv
    load_dotenv()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    rows = await conn.fetch(
        "SELECT name, embedding FROM task_nodes WHERE t_invalid IS NULL "
        "AND name IN ('api', 'rag', 'Group: transactions, Group: model_training, Group: debugging, Group: Group: factchecking, pdf, Group: rag, evaluation, Group: migrations, validation, pipelines (+1 more)')"
    )
    for r in rows:
        doc_vec = np.array([float(x) for x in r["embedding"].strip("[]").split(",")])
        sim = float(np.dot(query_vec, doc_vec) / (np.linalg.norm(query_vec) * np.linalg.norm(doc_vec)))
        print(f"{r['name'][:60]:60s} query-sim: {sim:.4f}")
    await conn.close()

asyncio.run(main())