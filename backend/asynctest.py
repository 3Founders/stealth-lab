import asyncio
from app.services.embeddings import Embedder

async def main():
    e = Embedder()
    doc_vec = await e.embed_one("api", input_type="document")
    query_vec = await e.embed_one("api", input_type="query")
    import numpy as np
    a, b = np.array(doc_vec), np.array(query_vec)
    print("cosine:", float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))))

asyncio.run(main())