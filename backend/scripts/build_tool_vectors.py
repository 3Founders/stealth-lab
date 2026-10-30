"""Embed the registry snapshot's tool cards for the tool router (app/tool_routing/retrieve.py).

    python scripts/build_tool_vectors.py [--batch 64]

Writes app/tool_routing/data/registry_vectors.npz: the card ids, their vectors (float16) and the embedding model id. The
router uses them only when the running embedder is the same model (a different model is a different space). Re-run
after scripts/fetch_mcp_registry.py, or after changing the embedding model. The hosted server builds the file itself
when it is missing (app/tool_routing/vectors.py build_if_missing).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tool_routing.vectors import build_registry_vectors  # noqa: E402


async def main(batch: int) -> int:
    return 0 if await build_registry_vectors(batch, say=lambda m: print(m, flush=True)) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=256)
    raise SystemExit(asyncio.run(main(ap.parse_args().batch)))
