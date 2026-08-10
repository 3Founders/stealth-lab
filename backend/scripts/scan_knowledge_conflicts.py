"""
Scans ALL live knowledge_nodes for pairs in the partial-match band
(genuine conflict candidates), with NO debate/LLM cost -- detection
only. Purpose: find out whether this corpus actually contains real
policy CONTRADICTIONS, or mostly just topically-related-but-compatible
documents (a real, open question for a reference KB corpus like this
one, unlike the synthetic refund-policy example which was built to
guarantee a real contradiction existed).

Run from backend/:
    python scripts/scan_knowledge_conflicts.py
    python scripts/scan_knowledge_conflicts.py --limit 50   # first 50 pairs only, faster look

Writes conflict_candidates.json (full list) and prints a sample to the
console for a quick read. Inspect a handful of these BEFORE running a
batch debate pipeline over all of them -- each debate costs real API
usage, and if most candidates turn out to be "related, not conflicting"
rather than real contradictions, that changes what the batch runner
should actually do (e.g. only debate the ones that look like genuine
version/scope conflicts, not everything the embedding band catches).
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.reuse_detection import FULL_MATCH_THRESHOLD, PARTIAL_MATCH_THRESHOLD


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    scope = AccessScope.unrestricted()

    # Same server-side self-join pattern as everywhere else in this
    # project (dedup.py, knowledge_conflict.py) -- one round trip, no
    # raw vector parsing in Python.
    # Excludes internal hierarchy nodes on BOTH sides -- same fix as
    # find_conflicting_knowledge, and for the same confirmed reason:
    # an unfiltered scan compares synthetic mean-embedding aggregator
    # nodes against real content and against each other, producing
    # nonsense "conflicts" at real scale (321,821 pairs found on the
    # first, unfiltered run against ~700 real documents -- mathematically
    # impossible for real pairs alone).
    rows = await pool.fetch(
        f"SELECT a.id AS id_a, a.name AS name_a, a.properties->>'category' AS cat_a, "
        f"b.id AS id_b, b.name AS name_b, b.properties->>'category' AS cat_b, "
        f"1 - (a.embedding <=> b.embedding) AS similarity "
        f"FROM knowledge_nodes a JOIN knowledge_nodes b ON a.id < b.id "
        f"WHERE a.t_invalid IS NULL AND b.t_invalid IS NULL "
        f"AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL "
        f"AND 1 - (a.embedding <=> b.embedding) >= $1 "
        f"AND 1 - (a.embedding <=> b.embedding) < $2 "
        f"AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.t_invalid IS NULL "
        f"  AND e.edge_type = 'OWNS' AND e.custom_edge_type = 'PARENT_OF' "
        f"  AND e.source_id = a.id AND e.source_table = 'knowledge_nodes') "
        f"AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.t_invalid IS NULL "
        f"  AND e.edge_type = 'OWNS' AND e.custom_edge_type = 'PARENT_OF' "
        f"  AND e.source_id = b.id AND e.source_table = 'knowledge_nodes') "
        f"ORDER BY similarity DESC",
        PARTIAL_MATCH_THRESHOLD, FULL_MATCH_THRESHOLD,
    )
    await pool.close()

    print(f"found {len(rows)} candidate pairs in the partial-match band "
          f"[{PARTIAL_MATCH_THRESHOLD}, {FULL_MATCH_THRESHOLD})")

    candidates = [
        {
            "id_a": str(r["id_a"]), "name_a": r["name_a"], "category_a": r["cat_a"],
            "id_b": str(r["id_b"]), "name_b": r["name_b"], "category_b": r["cat_b"],
            "similarity": float(r["similarity"]),
        }
        for r in rows
    ]
    if args.limit:
        candidates = candidates[: args.limit]

    with open("conflict_candidates.json", "w") as fh:
        json.dump(candidates, fh, indent=2)
    print(f"wrote conflict_candidates.json ({len(candidates)} pairs)\n")

    same_cat = sum(1 for c in candidates if c["category_a"] and c["category_a"] == c["category_b"])
    diff_cat = sum(1 for c in candidates if c["category_a"] and c["category_b"] and c["category_a"] != c["category_b"])
    print(f"same category: {same_cat}, different category: {diff_cat}, "
          f"uncategorized: {len(candidates) - same_cat - diff_cat}\n")

    print("=== sample (first 20, highest similarity first) ===")
    for c in candidates[:20]:
        print(f"  [{c['similarity']:.3f}] ({c['category_a']} / {c['category_b']})")
        print(f"      A: {c['name_a']}")
        print(f"      B: {c['name_b']}")


if __name__ == "__main__":
    asyncio.run(main())