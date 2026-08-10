"""
Embed all 129 task instructions once, into the disk cache.

Separated from the experiment scripts so the rate-limited work happens
exactly once. Afterwards every retrieval arm reads the cache for free,
which also means the three arms of Hypothesis A are compared on
byte-identical query vectors rather than on separately-fetched ones.

Rate limit arithmetic, since guessing it wrong is what stalls the run:
Voyage's free tier caps BOTH 3 requests/min and 10K tokens/min. A 7000-
token batch every 21s satisfies the request cap but delivers ~20K
tokens/min, which trips the token cap -- observed as a burst of 429s
during the library seed. Batches are therefore sized so that
(batch_tokens / interval) stays under 10K/min.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.after.corpus import load_tasks
from experiments.after.embed_cache import CachedEmbedder


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-interval", type=float, default=21.0)
    ap.add_argument("--max-batch-tokens", type=int, default=3300)
    args = ap.parse_args()

    tasks = load_tasks()
    embedder = CachedEmbedder(min_interval=args.min_interval)
    embedder.MAX_BATCH_TOKENS = args.max_batch_tokens

    texts = [t.instruction for t in tasks]
    est = sum(len(x) // 4 for x in texts)
    print(f"{len(texts)} task instructions, ~{est} tokens estimated")
    print(f"interval={args.min_interval}s batch={args.max_batch_tokens} tok "
          f"-> ~{args.max_batch_tokens * 60 / args.min_interval:.0f} tokens/min")

    t0 = time.time()
    vectors = await embedder.embed(texts, input_type="query")
    print(f"done in {time.time()-t0:.0f}s  stats={embedder.stats()}")

    bad = [i for i, v in enumerate(vectors) if not v or len(v) != embedder.dimension]
    if bad:
        print(f"FAIL: {len(bad)} embeddings missing or wrong dimension")
        return 1
    print(f"all {len(vectors)} query vectors cached at dim {len(vectors[0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
