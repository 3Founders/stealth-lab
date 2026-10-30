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
    # resumable: vectors so far are checkpointed by card id every few batches; a rerun embeds only the rest
    ckpt = VECTORS_FILE.with_name(VECTORS_FILE.stem + ".partial.npz")
    have: dict[str, np.ndarray] = {}
    if ckpt.exists():
        z = np.load(ckpt, allow_pickle=False)
        if str(z["model"]) == model:
            have = {str(i): v for i, v in zip(z["ids"], z["vectors"])}
            print(f"resuming: {len(have)} cards already embedded")
    todo = [c for c in cards if c.id not in have]
    t0 = time.monotonic()
    for n, i in enumerate(range(0, len(todo), batch), 1):
        part = todo[i:i + batch]
        for c, v in zip(part, await e.embed([c.text[:2000] for c in part], input_type="document")):
            have[c.id] = np.array(v, dtype=np.float16)
        if n % 10 == 0 or i + batch >= len(todo):
            np.savez(ckpt, ids=np.array(list(have)), vectors=np.array(list(have.values()), dtype=np.float16), model=np.array(model))
            print(f"{len(have)}/{len(cards)} ({time.monotonic() - t0:.0f} s)", flush=True)
    vecs = [have[c.id] for c in cards]
    np.savez_compressed(VECTORS_FILE, ids=np.array([c.id for c in cards]), vectors=np.array(vecs, dtype=np.float16),
                        model=np.array(model))
    ckpt.unlink(missing_ok=True)
    print(f"{len(cards)} vectors ({model}) -> {VECTORS_FILE} ({VECTORS_FILE.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=256)
    raise SystemExit(asyncio.run(main(ap.parse_args().batch)))
