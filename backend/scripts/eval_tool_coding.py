"""Score the tool router on coding requests (app/tool_routing/eval_coding.json) over the registry snapshot.

    python scripts/eval_tool_coding.py [--arms bm25,hybrid,rerank] [--k 5]

Per arm: goal accuracy (the router's goal vs the label), hit@k (an acceptable tool -- one whose name, title or
description matches the item's pattern AND serves its goal -- in the top k) and MRR. A complement to ToolRet: ToolRet is
general APIs; this is what kell's users ask for.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tool_routing.catalog import registry_cards  # noqa: E402
from app.tool_routing.providers import ToolEmbedder  # noqa: E402
from app.tool_routing.rerank import rerank  # noqa: E402
from app.tool_routing.retrieve import rank  # noqa: E402

SET = Path(__file__).resolve().parents[1] / "app" / "tool_routing" / "eval_coding.json"


def acceptable(card, item) -> bool:
    return bool(re.search(item["match"], f"{card.name} {card.title} {card.description}", re.I))


async def main(a) -> int:
    items = json.loads(SET.read_text(encoding="utf-8"))["items"]
    cards = registry_cards()
    emb = ToolEmbedder()
    emb = emb if emb.available() else None
    arms = a.arms.split(",")
    print(f"{len(items)} requests, {len(cards)} registry cards, embedder {emb.embedding_model_id() if emb else 'none'}")
    report = {}
    for arm in arms:
        g_ok = hit = 0
        rr = 0.0
        misses = []
        for it in items:
            # "goal": the rerank arm with the labelled goal given, as the hook gives the triage's (the others guess it)
            hits, info = await rank(it["q"], cards, k=a.k, embedder=emb if arm != "bm25" else None,
                                    reranker=rerank if arm in ("rerank", "goal") else None,
                                    goal=it["goal"] if arm == "goal" else None)
            g_ok += info.get("goal") == it["goal"]
            pos = next((i for i, h in enumerate(hits) if acceptable(h.card, it)), None)
            if pos is not None:
                hit += 1
                rr += 1 / (pos + 1)
            else:
                misses.append((it["q"], info.get("goal"), [h.card.name for h in hits[:3]]))
        n = len(items)
        report[arm] = {"goal_acc": g_ok / n, f"hit@{a.k}": hit / n, "mrr": rr / n}
        print(f"{arm:<7} goal {100 * g_ok / n:5.1f}%  hit@{a.k} {100 * hit / n:5.1f}%  MRR {rr / n:.3f}")
        if a.show_misses:
            for q, g, top in misses:
                print(f"    miss: {q!r} (goal {g}) -> {top}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="bm25,hybrid,rerank,goal")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--out", default="../.scratch/tool_eval_coding.json")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
