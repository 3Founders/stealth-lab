"""Embed the registry snapshot's tool cards for the tool router (app/tool_routing/retrieve.py).

    python scripts/build_tool_vectors.py [--batch 64]

Writes app/tool_routing/data/registry_vectors.npz: the card ids, their vectors (float16) and the embedding model id. The
router uses them only when the running embedder is the same model (a different model is a different space). Re-run
after scripts/fetch_mcp_registry.py, or after changing the embedding model.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tool_routing.providers import ToolEmbedder  # noqa: E402
from app.tool_routing.catalog import registry_cards  # noqa: E402
from app.tool_routing.retrieve import VECTORS_FILE  # noqa: E402


async def main(batch: int) -> int:
    cards = registry_cards()
    if not cards:
        print("no registry snapshot: run scripts/fetch_mcp_registry.py first")
        return 1
    e = ToolEmbedder()
    if not e.available():
        print("DEEPINFRA_KEY is not set")
        return 1
    model = e.embedding_model_id()
    vecs: list[list[float]] = []
    t0 = time.monotonic()
    for i in range(0, len(cards), batch):
        part = [c.text[:2000] for c in cards[i:i + batch]]
        vecs.extend(await e.embed(part, input_type="document"))
        print(f"{min(i + batch, len(cards))}/{len(cards)} ({time.monotonic() - t0:.0f} s)", flush=True)
    np.savez_compressed(VECTORS_FILE, ids=np.array([c.id for c in cards]), vectors=np.array(vecs, dtype=np.float16),
                        model=np.array(model))
    print(f"{len(cards)} vectors ({model}) -> {VECTORS_FILE} ({VECTORS_FILE.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=64)
    raise SystemExit(asyncio.run(main(ap.parse_args().batch)))
