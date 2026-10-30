"""Building the registry cards' vectors (app/tool_routing/data/registry_vectors.npz), by script or by the server itself.

The file is 89 MB, so it is neither committed nor uploaded with a deploy (a 136 MB `railway up` timed out, 2026-10-30):
the hosted server builds it on its first start, in the background after the warm-up (TOOL_BUILD_VECTORS=off: never),
and serves keywords and the reranker until it is done. About 35 minutes and $0.20 of DeepInfra embeddings for 48k
cards; checkpointed by card id every 10 batches, so a restart picks up where it stopped.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable, Optional

import numpy as np

from app.tool_routing.catalog import registry_cards
from app.tool_routing.providers import ToolEmbedder
from app.tool_routing.retrieve import VECTORS_FILE, registry_vectors

log = logging.getLogger(__name__)


async def build_registry_vectors(batch: int = 256, say: Callable[[str], None] = log.info,
                                 embedder: Optional[ToolEmbedder] = None) -> bool:
    """Embed every registry card into VECTORS_FILE (resuming a checkpoint). False: no snapshot or no key."""
    cards = registry_cards()
    if not cards:
        say("tool vectors: no registry snapshot")
        return False
    e = embedder or ToolEmbedder()
    if not e.available():
        say("tool vectors: DEEPINFRA_KEY is not set")
        return False
    model = e.embedding_model_id()
    ckpt = VECTORS_FILE.with_name(VECTORS_FILE.stem + ".partial.npz")
    have: dict[str, np.ndarray] = {}
    if ckpt.exists():
        with np.load(ckpt, allow_pickle=False) as z:          # closed after: the checkpoint is deleted at the end
            if str(z["model"]) == model:
                have = {str(i): v for i, v in zip(z["ids"], z["vectors"])}
        if have:
            say(f"tool vectors: resuming, {len(have)} cards already embedded")
    todo = [c for c in cards if c.id not in have]
    t0 = time.monotonic()
    for n, i in enumerate(range(0, len(todo), batch), 1):
        part = todo[i:i + batch]
        for c, v in zip(part, await e.embed([c.text[:2000] for c in part], input_type="document")):
            have[c.id] = np.array(v, dtype=np.float16)
        if n % 10 == 0 or i + batch >= len(todo):
            np.savez(ckpt, ids=np.array(list(have)), vectors=np.array(list(have.values()), dtype=np.float16),
                     model=np.array(model))
            say(f"tool vectors: {len(have)}/{len(cards)} ({time.monotonic() - t0:.0f} s)")
    np.savez_compressed(VECTORS_FILE, ids=np.array([c.id for c in cards]),
                        vectors=np.array([have[c.id] for c in cards], dtype=np.float16), model=np.array(model))
    ckpt.unlink(missing_ok=True)
    registry_vectors.cache_clear()
    say(f"tool vectors: {len(cards)} ({model}) -> {VECTORS_FILE.name}")
    return True


async def build_if_missing() -> None:
    """The server's background build: only when the vectors are missing (or another model's) and the key is set."""
    if os.environ.get("TOOL_ROUTING", "on").lower() == "off" or os.environ.get("TOOL_BUILD_VECTORS", "on").lower() == "off":
        return
    e = ToolEmbedder()
    _rows, mat, model = registry_vectors()
    if not e.available() or (mat is not None and model == e.embedding_model_id()):
        return
    try:
        await build_registry_vectors(embedder=e)
    except Exception:  # noqa: BLE001 -- the router keeps working on keywords and the reranker
        log.warning("tool vectors: build failed; a restart resumes it", exc_info=True)
