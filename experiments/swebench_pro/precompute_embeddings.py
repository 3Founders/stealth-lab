"""
Embed the whole memory corpus once, up front, into the shared cache.

Separate from the experiment run so that Voyage's free-tier throttle (3
RPM / 10K TPM) is paid once here rather than stalling the first instance of
a long run -- and so an interrupted embedding pass never leaves an
experiment half-run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from knowledge import VoyageEmbedder, KnowledgeNode  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True)
    args = ap.parse_args()

    with open(os.path.join(HERE, "subset.json"), encoding="utf-8") as f:
        subset = json.load(f)

    embedder = VoyageEmbedder(os.path.join(args.work_dir, "voyage_cache.json"))

    docs = [
        KnowledgeNode(r["instance_id"], r["title"], r["problem_statement"],
                      r["files_touched"], r["symbols_touched"],
                      r["commit_date"]).text_for_embedding()
        for r in subset["corpus"]
    ]
    queries = [
        f"{r['title']}\n\n{r['problem_statement'][:1500]}" for r in subset["eval"]
    ]

    t0 = time.time()
    print(f"embedding {len(docs)} corpus documents ...")
    embedder.embed(docs, input_type="document")
    print(f"embedding {len(queries)} eval queries ...")
    embedder.embed(queries, input_type="query")
    print(f"done in {time.time() - t0:.0f}s -> "
          f"{os.path.join(args.work_dir, 'voyage_cache.json')}")


if __name__ == "__main__":
    main()
