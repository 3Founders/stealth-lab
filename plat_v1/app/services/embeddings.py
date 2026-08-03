"""
Embedding generation for task-node retrieval.

Two things matter and both fail quietly if you get them wrong:

  `input_type` is not decorative. Voyage embeds documents and queries into
  slightly different spaces on purpose. Stored task nodes are documents;
  a caller's prompt is a query. Swapping them degrades recall with no error.

  Dimension is checked here rather than at INSERT. A model returning 512
  where the column is VECTOR(1024) fails at the database with a message that
  points at the SQL instead of at the model choice.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional, Sequence

from app.config import settings

log = logging.getLogger(__name__)

InputType = Literal["document", "query"]


class EmbeddingError(Exception):
    pass


class Embedder:
    def __init__(self, model: Optional[str] = None, dimension: Optional[int] = None):
        self.model = model or settings.embedding_model
        self.dimension = dimension or settings.embedding_dimension

    async def embed(
        self, texts: Sequence[str], input_type: InputType = "document"
    ) -> list[list[float]]:
        if not texts:
            return []

        import voyageai

        client = voyageai.AsyncClient(api_key=settings.require("voyage_api_key"))
        try:
            result = await client.embed(list(texts), model=self.model, input_type=input_type)
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"Voyage embedding failed: {exc}") from exc

        vectors = result.embeddings
        if vectors and len(vectors[0]) != self.dimension:
            raise EmbeddingError(
                f"model {self.model} returned dimension {len(vectors[0])}, but the schema "
                f"expects {self.dimension}. Either pick a model with a matching dimension "
                f"or alter the VECTOR(n) column and re-embed everything -- mixed "
                f"dimensions in one column are not possible."
            )
        return vectors

    async def embed_one(self, text: str, input_type: InputType = "document") -> list[float]:
        vectors = await self.embed([text], input_type=input_type)
        if not vectors:
            raise EmbeddingError("no embedding returned")
        return vectors[0]


def to_pgvector(vector: Sequence[float]) -> str:
    """pgvector's text input format; asyncpg has no native codec for the type."""
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


def task_text(name: str, description: Optional[str] = None) -> str:
    """What actually gets embedded. A bare name carries much less signal."""
    return f"{name}\n{description}" if description else name
