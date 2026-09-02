"""Phase 10 deliverable 2 data-gathering probe (hand-run, not pytest).

Runs get_claim_graph_overview + traverse_claim_graph / get_claim_neighbors
against the 15 corpus_wave claims on live Supabase, plus one
query -> seed-claim -> neighbours worked example via search_global claim leg.
Prints JSON; the .md is hand-written from this output.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.session import create_pool  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.services.claim_graph_api import (  # noqa: E402
    get_claim_graph_overview,
    get_claim_neighbors,
    traverse_claim_graph,
)
from app.services.domain_search import search_global  # noqa: E402

S20_CLAIMS = {
    "f38d9dec-8c98-4d4c-b26e-fa06be9c666d": "LLM accuracy degrades non-uniformly as input length grows",
    "6f075acb-c56d-43ca-b449-0fc78f7bf8fb": "Degradation accelerates as needle-question semantic similarity decreases",
    "020d8d90-a850-40d6-ad52-88e89e475895": "A single distractor lowers performance vs needle-only; four distractors compound it",
    "7264f476-df45-4f2f-9a5c-5bde554757e5": "Models score higher on shuffled haystacks than logically-structured ones",
    "5d9413d7-7d2e-4518-b9a6-9e425f94f2b4": "Focused (~300 tok) prompts beat full (~113k tok) prompts across all models on LongMemEval",
}


async def main() -> None:
    pool = await create_pool(os.environ["DATABASE_URL"], statement_cache_size=0)
    scope = AccessScope.unrestricted()
    out: dict = {}

    ov = await get_claim_graph_overview(pool, scope=scope, link_mode="both", limit=200)
    ov["links"] = ov["edges"]
    out["overview"] = {
        "node_count": len(ov["nodes"]),
        "edge_count": len(ov["edges"]),
        "edges_by_kind": ov["counts"]["edges_by_kind"],
        "link_mode": ov.get("link_mode"),
        "by_status": ov["counts"]["by_status"],
        "counts_block": ov["counts"],
        "node_ids": [n["id"] for n in ov["nodes"]],
    }

    # similarity adjacency among S20 cluster
    sim_pairs = []
    for l in ov["links"]:
        if l.get("kind") == "similarity":
            s, t = str(l["source"]), str(l["target"])
            sim_pairs.append((s, t, round(float(l.get("weight", 0)), 4)))
    out["s20_cluster"] = {
        "claim_ids": list(S20_CLAIMS),
        "internal_similarity_edges": [
            p for p in sim_pairs
            if p[0] in S20_CLAIMS and p[1] in S20_CLAIMS
        ],
        "edges_from_s20_to_outside": [
            p for p in sim_pairs
            if (p[0] in S20_CLAIMS) != (p[1] in S20_CLAIMS)
        ],
        "connected": None,  # filled below
    }
    # connectivity of the S20 subgraph over similarity edges
    adj: dict[str, set[str]] = {c: set() for c in S20_CLAIMS}
    for s, t, _w in sim_pairs:
        if s in S20_CLAIMS and t in S20_CLAIMS:
            adj[s].add(t)
            adj[t].add(s)
    seen = set()
    stack = [next(iter(S20_CLAIMS))]
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        stack.extend(adj[c] - seen)
    out["s20_cluster"]["connected"] = (seen == set(S20_CLAIMS))
    out["s20_cluster"]["reachable_count"] = len(seen)

    # neighbours / traversal on 3 claims
    probes = [
        "f38d9dec-8c98-4d4c-b26e-fa06be9c666d",  # S20 anchor
        "36c151f0-412b-429c-981b-cab6bc30e28f",  # S24 trim-context experimental
        "be5d06b5-da3f-4aae-85dd-978f51a5267f",  # S36 fix-verified inferred
    ]
    out["probes"] = {}
    for cid in probes:
        nb = await get_claim_neighbors(pool, cid, scope=scope)
        tv = await traverse_claim_graph(pool, cid, max_hops=2, scope=scope)
        out["probes"][cid] = {
            "relation_neighbors": len(nb),
            "traverse_supporting": len(tv["supporting"]),
            "traverse_contradicting": len(tv["contradicting"]),
            "traverse_other": len(tv["other"]),
        }

    # worked example: query -> seed claim -> similarity neighbours -> source
    q = "does a shorter focused prompt beat a long one full of context"
    res = await search_global(
        pool, q, object_types=["claim"], limit=5, scope=scope,
    )
    claim_hits = res["results"]["claim"]
    seed = claim_hits[0] if claim_hits else None
    worked = {"query": q, "seed_claim": None, "similarity_neighbours": []}
    if seed:
        seed_id = seed["id"]
        worked["seed_claim"] = {"id": seed_id, "name": seed.get("name"), "score": seed.get("score")}
        neigh = [
            (str(l["source"]), str(l["target"]), round(float(l.get("weight", 0)), 4))
            for l in ov["links"]
            if l.get("kind") == "similarity" and seed_id in (str(l["source"]), str(l["target"]))
        ]
        for s, t, w in neigh:
            other = t if s == seed_id else s
            nm = next((n["statement"] for n in ov["nodes"] if n["id"] == other), None)
            worked["similarity_neighbours"].append({"id": other, "name": nm, "weight": w})
    out["worked_example"] = worked

    # node -> source_id mapping via knowledge_nodes.properties
    rows = await pool.fetch(
        "SELECT id, name, properties FROM knowledge_nodes "
        "WHERE node_type='claim' AND created_by='corpus_wave' AND t_invalid IS NULL"
    )
    out["claim_source_map"] = [
        {"id": str(r["id"]), "name": r["name"],
         "properties_keys": sorted((r["properties"] or {}).keys()),
         "source_hint": (r["properties"] or {}).get("source_id")
                        or (r["properties"] or {}).get("provenance")}
        for r in rows
    ]

    await pool.close()
    print(json.dumps(out, indent=2, default=str))


def _count_kinds(links: list[dict]) -> dict:
    d: dict[str, int] = {}
    for l in links:
        k = l.get("kind", "?")
        d[k] = d.get(k, 0) + 1
    return d


def _count_status(nodes: list[dict]) -> dict:
    d: dict[str, int] = {}
    for n in nodes:
        k = n.get("status") or n.get("lifecycle_state") or "?"
        d[k] = d.get(k, 0) + 1
    return d


if __name__ == "__main__":
    asyncio.run(main())
